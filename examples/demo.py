"""A scripted failure, evidence update and recovery; not an LLM benchmark."""

import argparse
import json
from pathlib import Path

from memory_module import Memory, dumps


def run(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    with Memory.create(output / "demo.sqlite", "Invented refund-policy example", [
        "This project contains invented demonstration data.",
        "State current policy only after checking the stored source version.",
        "Keep explanations in professional plain English.",
    ]) as memory:
        episode = memory.start("Find the current refund period", "Return the refund period in force",
                               "policy lookup", "Returned days equal the current test fixture")
        actual_policy = {"refund_days": 30}  # Ground truth for this invented test.
        (output / "current-policy.json").write_text(json.dumps(actual_policy, indent=2) + "\n")
        old = memory.source("refund-policy", "Refund policy", "Earlier policy: 14 days.",
                            dumps({"refund_days": 14}), "document")["id"]
        counter = 0

        def record(kind, payload, **kwargs):
            nonlocal counter
            counter += 1
            return memory.record(episode["id"], kind, payload,
                expected_version=memory.episode(episode["id"])["version"],
                request_key=f"demo-{counter}", actor="scripted-example", **kwargs)

        first = record("decision", {"decision": "Use the previously stored 14-day policy.",
            "why": "It is the policy currently held in memory; its external currency is unverified.",
            "uncertainty": "The source may have changed since it was stored.",
            "expected": "The answer matches the current fixture.",
            "reconsider_when": "The fixture or refreshed source disagrees.", "model": "scripted-example"},
            evidence=[{"source_id": old, "reason": "Earlier policy copy; not checked against the current file."}])
        record("action", {"action": "Compare the stored answer with the current local fixture."}, decision_id=first["id"])
        selected = json.loads(memory.read(old, detail=True)["body"])["refund_days"]
        first_result = {"expected_days": actual_policy["refund_days"], "returned_days": selected,
                        "passed": selected == actual_policy["refund_days"]}
        result_source = memory.source("first-check", "First check", "The earlier answer failed the check.",
                                      dumps(first_result), "tool")["id"]
        record("outcome", {"observed": dumps(first_result), "assessment": "good" if first_result["passed"] else "bad",
            "assessment_reason": "Direct comparison with the local test fixture.", "severity": "minor",
            "attribution": "The answer used the earlier policy copy.", "failure_type": "outdated_source"},
            decision_id=first["id"], evidence=[{"source_id": result_source, "reason": "Actual comparison result."}])

        current = memory.source("refund-policy", "Refund policy", "Current fixture: 30 days.",
            (output / "current-policy.json").read_text(), "document")["id"]
        assert memory.read(first["id"])["status"] == "needs_review"
        record("research", {"question": "Which refund policy is current?", "queries": ["refund policy"],
            "findings": "The current local file specifies 30 days; the previous copy specified 14.",
            "gaps": "This checks an invented local fixture only.", "refresh_reason": "The first answer failed the fixture check."},
            evidence=[{"source_id": current, "reason": "The refreshed policy source."}])
        packet = memory.context("refund policy", episode_id=episode["id"], budget=7000)
        (output / "context.json").write_text(dumps(packet) + "\n")
        found = memory.search("refund policy")
        current_ids = [item["id"] for item in found["records"] if item["kind"] == "source" and item["id"] == current]
        assert current_ids == [current]
        second = record("decision", {"decision": "Read and use the current policy version.",
            "why": "The earlier answer failed; the refreshed source specifies 30 days.",
            "alternatives": ["Repeating the old answer would ignore the observed mismatch."],
            "expected": "The answer matches the current fixture.",
            "reconsider_when": "The policy source changes again.", "model": "scripted-example"},
            evidence=[{"source_id": current, "reason": "Current policy to read exactly."},
                      {"source_id": result_source, "reason": "Earlier failure that prompted the check."}],
            supersedes=first["id"])
        record("action", {"action": "Read the refreshed source and compare its answer with the fixture."}, decision_id=second["id"])
        selected = json.loads(memory.read(current_ids[0], detail=True)["body"])["refund_days"]
        second_result = {"expected_days": actual_policy["refund_days"], "returned_days": selected,
                         "passed": selected == actual_policy["refund_days"]}
        result_source2 = memory.source("second-check", "Recovery check", "The revised answer passed.", dumps(second_result), "tool")["id"]
        record("outcome", {"observed": dumps(second_result), "assessment": "good" if second_result["passed"] else "bad",
            "assessment_reason": "Direct comparison with the same local test fixture.",
            "severity": "none", "attribution": "The revised answer read the refreshed policy."},
            decision_id=second["id"], evidence=[{"source_id": result_source2, "reason": "Actual recovery check."}])
        record("lesson", {"when": "A question asks for the policy currently in force.",
            "do": "Check source currency and read the relevant current passage before answering.",
            "because": "The old-copy answer failed and the refreshed answer passed this example.",
            "exceptions": "This single scripted example does not establish a general improvement rate."},
            evidence=[{"source_id": result_source, "reason": "Failed earlier attempt."},
                      {"source_id": result_source2, "reason": "Successful correction."}])
        (output / "history.md").write_text(memory.history(episode["id"]))
        metrics = memory.metrics()
        (output / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")
        memory.backup(output / "portable-copy.sqlite")
        report = {"first_check": first_result, "recovery_check": second_result,
                  "context_characters": packet["used"], "context_budget": packet["budget"],
                  "context_omitted": packet["omitted"], "episode": episode["id"],
                  "interpretation": "Demonstrates recording, revision, evidence lookup and arithmetic. Does not measure AI learning."}
        (output / "demo-result.json").write_text(json.dumps(report, indent=2) + "\n")
        return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="demo-run", help="New output directory")
    print(json.dumps(run(parser.parse_args().output), indent=2))
