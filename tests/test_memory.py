import concurrent.futures
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest

from memory_module import Memory, Conflict, InvalidRecord, BudgetTooSmall, dumps


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.path = Path(self.tmp.name) / "project.sqlite"
        self.now = "2026-09-12T10:00:00+00:00"
        self.m = Memory.create(self.path, "test-project", ["Use professional plain English.",
                              "Do not change the agreed objective."], clock=lambda: self.now)
        self.ep = self.m.start("Check a service", "Restore access", "repair", "Authenticated request succeeds")
        self.source = self.m.source("service-log", "Authentication result", "Authentication failed.",
                                    "HTTP 401: credential expired", "tool")["id"]
        self.refs = [{"source_id": self.source, "reason": "Observed authentication result."}]
        self.key = 0

    def tearDown(self):
        self.m.close()
        self.tmp.cleanup()

    def record(self, kind, payload, **kw):
        self.key += 1
        return self.m.record(self.ep["id"], kind, payload,
                             expected_version=self.m.episode(self.ep["id"])["version"],
                             request_key=str(self.key), actor="test", **kw)

    def decision(self, **kw):
        return self.record("decision", {"decision": "Refresh authentication credentials", "why": "The request returned an authentication error.",
                           "expected": "Access is restored.", "reconsider_when": "Valid credentials fail."}, evidence=self.refs, **kw)

    def action(self, decision_id):
        return self.record("action", {"action": "Attempted authentication refresh"}, decision_id=decision_id)

    def outcome(self, decision_id, assessment="bad", **kw):
        return self.record("outcome", {"observed": "The request result was checked.", "assessment": assessment,
                "assessment_reason": "Compared the tool result with the recorded success criterion.",
                "severity": "minor", "attribution": "Cause remains uncertain."}, decision_id=decision_id,
                evidence=self.refs, **kw)

    def test_records_survive_reopening(self):
        d = self.decision()
        with Memory(self.path) as other:
            self.assertEqual(other.read(d["id"])["payload"]["decision"], "Refresh authentication credentials")
            self.assertEqual(other.project, "test-project")

    def test_missing_database_not_silently_created(self):
        path = Path(self.tmp.name) / "typo.sqlite"
        with self.assertRaises(sqlite3.OperationalError):
            Memory(path)
        self.assertFalse(path.exists())

    def test_create_does_not_overwrite(self):
        with self.assertRaises(Conflict):
            Memory.create(self.path, "other", ["different"])
        self.assertEqual(self.m.project, "test-project")

    def test_retry_is_idempotent_and_conflicting_payload_rejected(self):
        args = dict(episode_id=self.ep["id"], kind="note", payload={"text": "Investigate the error."},
                    expected_version=0, request_key="retry", actor="test")
        first = self.m.record(**args)
        second = self.m.record(**args)
        self.assertEqual(first["id"], second["id"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(self.m.episode(self.ep["id"])["version"], 1)
        with self.assertRaises(Conflict):
            self.m.record(**{**args, "payload": {"text": "Different content"}})

    def test_stale_update_rejected_without_partial_record(self):
        self.decision()
        with self.assertRaises(Conflict):
            self.m.record(self.ep["id"], "note", {"text": "stale writer"}, expected_version=0,
                          request_key="stale", actor="test")
        self.assertEqual(self.m.episode(self.ep["id"])["version"], 1)
        self.assertEqual(self.m.search("stale writer")["records"], [])

    def test_simultaneous_writers_cannot_both_update_same_version(self):
        barrier = threading.Barrier(2)

        def write(key):
            with Memory(self.path) as other:
                barrier.wait(timeout=5)
                try:
                    other.record(self.ep["id"], "note", {"text": key}, expected_version=0,
                                 request_key=key, actor=key)
                    return "saved"
                except Conflict:
                    return "conflict"

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(write, ["agent-a", "agent-b"]))
        self.assertCountEqual(results, ["saved", "conflict"])

    def test_invalid_evidence_rolls_back(self):
        with self.assertRaises(InvalidRecord):
            self.record("note", {"text": "Never saved"}, evidence=[{"source_id": "missing", "reason": "test"}])
        self.assertEqual(self.m.episode(self.ep["id"])["version"], 0)
        self.assertEqual(self.m.search("Never saved")["records"], [])

    def test_evidence_cannot_cross_projects(self):
        with Memory.create(Path(self.tmp.name)/"other.sqlite", "other", ["Other rules"]) as other:
            ep = other.start("t", "o", "type", "criterion")
            with self.assertRaises(InvalidRecord):
                other.record(ep["id"], "note", {"text": "bad reference"}, expected_version=0,
                             request_key="x", actor="worker", evidence=self.refs)
            self.assertEqual(other.search("Authentication")["records"], [])

    def test_unknown_fields_cannot_change_criteria(self):
        with self.assertRaises(InvalidRecord):
            self.record("note", {"text": "change", "criterion": "easier"})
        with self.assertRaises(sqlite3.IntegrityError):
            self.m.db.execute("UPDATE episodes SET criterion='easier'")
        with self.assertRaises(sqlite3.IntegrityError):
            self.m.db.execute("UPDATE settings SET value='other'")

    def test_events_and_sources_are_not_rewritten(self):
        self.decision()
        with self.assertRaises(sqlite3.IntegrityError):
            self.m.db.execute("DELETE FROM events")
        with self.assertRaises(sqlite3.IntegrityError):
            self.m.db.execute("UPDATE sources SET body='rewritten'")

    def test_source_change_flags_dependents_preserves_old_evidence(self):
        d = self.decision()
        self.m.source("service-log", "New result", "Request succeeded", "HTTP 200", "tool")
        self.assertEqual(self.m.read(d["id"])["status"], "needs_review")
        self.assertEqual(self.m.read(self.source, detail=True)["body"], "HTTP 401: credential expired")
        self.assertEqual(self.m.read(self.source)["status"], "superseded")

    def test_same_content_recheck_does_not_invalidate_evidence(self):
        d = self.decision()
        self.m.source("service-log", "Authentication result", "Still failed",
                      "HTTP 401: credential expired", "tool")
        self.assertEqual(self.m.read(d["id"])["status"], "recorded")

    def test_review_due_without_claiming_external_refresh(self):
        source = self.m.source("policy", "Policy", "Policy guidance", "Policy text", "document",
                               review_after="2026-09-13T10:00:00Z")["id"]
        self.assertEqual(self.m.source_status(source), "current_copy")
        self.now = "2026-09-14T10:00:00+00:00"
        self.assertEqual(self.m.source_status(source), "review_due")
        self.assertIn(source, self.m.due()["sources"])

    def test_naive_dates_and_invalid_costs_rejected(self):
        with self.assertRaises(InvalidRecord):
            self.m.source("p", "p", "p", "p", "tool", "2026-09-13")
        d = self.decision()
        self.action(d["id"])
        with self.assertRaises(InvalidRecord):
            self.record("outcome", {"observed": "x", "assessment": "good", "assessment_reason": "x",
                "severity": "none", "attribution": "unknown", "tokens": -2}, decision_id=d["id"], evidence=self.refs)

    def test_outcome_requires_recorded_action_and_evidence(self):
        d = self.decision()
        with self.assertRaises(InvalidRecord):
            self.outcome(d["id"])
        self.action(d["id"])
        with self.assertRaises(InvalidRecord):
            self.record("outcome", {"observed": "x", "assessment": "good", "assessment_reason": "x",
                "severity": "none", "attribution": "unknown"}, decision_id=d["id"])

    def test_decision_change_is_explicit_and_not_retroactive(self):
        first = self.decision()
        with self.assertRaises(Conflict):
            self.decision()
        second = self.decision(supersedes=first["id"])
        self.assertEqual(self.m.read(first["id"])["replaced_by"], second["id"])
        self.assertEqual(self.m.read(first["id"])["payload"]["expected"], "Access is restored.")
        with self.assertRaises(InvalidRecord):
            self.action(first["id"])

    def test_metrics_do_not_count_pending_or_duplicate_outcomes_as_success(self):
        first = self.decision()
        self.action(first["id"])
        bad = self.outcome(first["id"], "bad")
        self.outcome(first["id"], "unknown", supersedes=bad["id"])
        second = self.decision(supersedes=first["id"])
        self.action(second["id"])
        group = self.m.metrics()["groups"][0]
        self.assertEqual((group["assessed"], group["unknown"], group["pending"]), (0, 1, 1))
        self.assertIsNone(group["bad_outcome_rate"])

    def test_failure_rate_counts_each_decision_once(self):
        first = self.decision()
        self.action(first["id"])
        self.outcome(first["id"], "bad")
        second = self.decision(supersedes=first["id"])
        self.action(second["id"])
        self.outcome(second["id"], "good")
        group = self.m.metrics()["groups"][0]
        self.assertEqual((group["bad"], group["assessed"], group["bad_outcome_rate"]), (1, 2, 0.5))

    def test_different_success_criteria_are_separate_groups(self):
        self.decision()
        self.ep = self.m.start("other", "other", "repair", "Different criterion")
        self.decision()
        self.assertEqual(len(self.m.metrics()["groups"]), 2)

    def test_costs_have_coverage_and_are_not_inferred(self):
        d = self.decision()
        self.action(d["id"])
        self.record("outcome", {"observed": "checked", "assessment": "bad", "assessment_reason": "test",
            "severity": "major", "attribution": "unknown", "tokens": 123, "failure_type": "expired_source"},
            decision_id=d["id"], evidence=self.refs)
        group = self.m.metrics()["groups"][0]
        self.assertEqual(group["reported_costs"]["tokens"], {"total": 123, "reported_decisions": 1})
        self.assertEqual(group["reported_costs"]["human_corrections"]["reported_decisions"], 0)
        self.assertEqual(group["failure_types"], {"expired_source": 1})
        self.assertEqual(group["major_bad"], 1)

    def test_database_failure_rolls_back_event_and_version(self):
        self.m.db.execute("CREATE TRIGGER reject_version BEFORE UPDATE ON episodes BEGIN SELECT RAISE(ABORT,'simulated storage failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.record("note", {"text": "should never be stored"})
        self.assertEqual(self.m.db.execute("SELECT count(*) FROM events").fetchone()[0], 0)
        self.assertEqual(self.m.episode(self.ep["id"])["version"], 0)
        self.assertEqual(self.m.search("never stored")["records"], [])

    def test_scripted_demo_records_failure_and_recovery(self):
        from examples.demo import run
        result = run(Path(self.tmp.name) / "example output")
        self.assertFalse(result["first_check"]["passed"])
        self.assertTrue(result["recovery_check"]["passed"])
        self.assertLessEqual(result["context_characters"], result["context_budget"])

    def test_search_handles_literal_punctuation_and_excludes_old_versions(self):
        old = self.m.source("refunds", "Refund policy", "Old refund policy", "refund 10 days", "document")["id"]
        new = self.m.source("refunds", "Refund policy", "Current refund policy", "refund 20 days", "document")["id"]
        ids = [r["id"] for r in self.m.search('refund: " OR * NEAR()')["records"]]
        self.assertIn(new, ids)
        self.assertNotIn(old, ids)
        history = self.m.search("refund", include_history=True)
        self.assertIn(old, [r["id"] for r in history["records"]])

    def test_small_budget_reports_incomplete_without_cutting_lesson(self):
        self.record("lesson", {"when": "authentication fails", "do": "refresh credentials " * 200,
                    "because": "observed error", "exceptions": "Do not retry if credentials are valid."}, evidence=self.refs)
        packet = self.m.context("authentication", budget=700)
        self.assertLessEqual(len(dumps(packet)), 700)
        self.assertEqual(packet["used"], len(dumps(packet)))
        self.assertGreater(packet["omitted"], 0)
        self.assertFalse(any(r["kind"] == "lesson" for r in packet["records"]))
        with self.assertRaises(BudgetTooSmall):
            self.m.context("authentication", episode_id=self.ep["id"], budget=10)

    def test_required_current_decision_never_silently_omitted(self):
        d = self.decision()
        packet = self.m.context("unrelated", episode_id=self.ep["id"], budget=6000)
        self.assertIn(d["id"], [r["id"] for r in packet["records"]])
        with self.assertRaises(BudgetTooSmall):
            self.m.context("unrelated", episode_id=self.ep["id"], budget=300)

    def test_host_counter_and_seen_versions(self):
        packet = self.m.context("authentication", budget=4000, count_tokens=lambda text: len(text.encode("utf-8")))
        self.assertEqual(packet["unit"], "tokens")  # An injected test counter, not a real tokenizer.
        self.assertEqual(packet["used"], len(dumps(packet).encode("utf-8")))
        seen = {r["id"]: r["signature"] for r in packet["records"] if "signature" in r}
        again = self.m.context("authentication", budget=4000, seen=seen)
        self.assertEqual([r["kind"] for r in again["records"]], ["requirements"])

    def test_expired_evidence_changes_delivered_signature(self):
        self.m.source("expiring", "Policy", "Authentication policy", "Policy", "document", "2026-09-13T00:00:00Z")
        packet = self.m.context("policy", budget=4000)
        seen = {r["id"]: r["signature"] for r in packet["records"] if "signature" in r}
        self.now = "2026-09-14T00:00:00Z"
        again = self.m.context("policy", budget=4000, seen=seen)
        self.assertTrue(any(r.get("status") == "review_due" for r in again["records"]))

    def test_lesson_is_not_automatically_active(self):
        lesson = self.record("lesson", {"when": "authentication fails", "do": "check credentials",
                   "because": "error", "exceptions": "valid credentials"}, evidence=self.refs)
        self.assertEqual(self.m.read(lesson["id"])["status"], "proposed")
        self.assertIn("not been activated", self.m.search("credentials")["records"][0]["text"])

    def test_due_consequences_and_daily_history(self):
        d = self.record("decision", {"decision": "Check later", "why": "Delayed effect", "expected": "A result",
                "reconsider_when": "No result", "review_after": "2026-09-13T00:00:00Z"})
        self.action(d["id"])
        self.now = "2026-09-14T00:00:00Z"
        self.assertIn(d["id"], self.m.due()["decisions"])
        self.outcome(d["id"], "good")
        self.assertNotIn(d["id"], self.m.due()["decisions"])
        self.assertIn("Observed consequence", self.m.history(day="2026-09-14"))
        self.assertNotIn("Observed consequence", self.m.history(day="2026-09-12"))

    def test_backup_reopens_with_history_search_and_metrics(self):
        d = self.decision()
        self.action(d["id"])
        self.outcome(d["id"])
        target = Path(self.tmp.name) / "backup.sqlite"
        self.m.backup(target)
        with Memory(target, clock=lambda: self.now) as copy:
            self.assertEqual(copy.history(), self.m.history())
            self.assertEqual(copy.metrics(), self.m.metrics())
            self.assertEqual(copy.search("authentication"), self.m.search("authentication"))
        with self.assertRaises(Conflict):
            self.m.backup(target)

    def test_cli_reports_actual_success_and_failure(self):
        cmd = [sys.executable, "-m", "memory_module", "--db", str(self.path)]
        result = subprocess.run(cmd + ["metrics"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["project"], "test-project")
        invalid = subprocess.run(cmd + ["context", "authentication", "--max-chars", "1"], capture_output=True, text=True)
        self.assertEqual(invalid.returncode, 2)
        self.assertEqual(json.loads(invalid.stderr)["error"], "BudgetTooSmall")


if __name__ == "__main__":
    unittest.main()
