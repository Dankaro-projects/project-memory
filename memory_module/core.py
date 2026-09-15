"""Evidence and decision records. No model, network or background processes."""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import uuid


from .schema import UPGRADE
from .workflow import Workflow, validate_payload, validate_event

SCHEMA_VERSION = 2
SUBJECTS = {"general", "code", "writing", "research"}
KINDS = {"decision", "action", "outcome", "research", "lesson", "note", "review", "correction", "action_result", "follow_up", "episode_status", "lesson_review", "work_plan", "sprint"}
ASSESSMENTS = {"pending", "good", "bad", "unknown"}


class MemoryError(Exception):
    """Expected error safe to report to a caller."""


class Conflict(MemoryError):
    pass


class InvalidRecord(MemoryError):
    def __init__(self, message, **details):
        super().__init__(message)
        self.details = details


class BudgetTooSmall(MemoryError):
    def __init__(self, message, **details):
        super().__init__(message)
        self.details = details


def dumps(value):
    """Canonical serialization, also used to measure the complete context reply."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _id(prefix):
    return prefix + "_" + uuid.uuid4().hex


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _text(value, name, limit=12000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise InvalidRecord(f"{name} must be nonempty text, at most {limit} characters.")
    return value


def _time(value):
    if not isinstance(value, str):
        raise InvalidRecord("Dates must be ISO 8601 strings with a time zone.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("Missing timezone")
        return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")
    except ValueError as exc:
        raise InvalidRecord("Dates must be ISO 8601 strings with a time zone.") from exc


SCHEMA = """
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE episodes (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, objective TEXT NOT NULL,
 task_type TEXT NOT NULL, criterion TEXT NOT NULL, created_at TEXT NOT NULL,
 version INTEGER NOT NULL DEFAULT 0 CHECK(version >= 0)
);
CREATE TABLE sources (
 id TEXT PRIMARY KEY, source_key TEXT NOT NULL, version INTEGER NOT NULL,
 title TEXT NOT NULL, summary TEXT NOT NULL, body TEXT NOT NULL,
 origin TEXT NOT NULL CHECK(origin IN ('user','tool','document')),
 content_hash TEXT NOT NULL, checked_at TEXT NOT NULL, review_after TEXT,
 UNIQUE(source_key, version)
);
CREATE INDEX source_versions ON sources(source_key, version DESC);
CREATE TABLE events (
 id TEXT PRIMARY KEY, episode_id TEXT NOT NULL REFERENCES episodes(id),
 seq INTEGER NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
 created_at TEXT NOT NULL, actor TEXT NOT NULL,
 decision_id TEXT REFERENCES events(id), supersedes TEXT UNIQUE REFERENCES events(id),
 request_key TEXT NOT NULL UNIQUE, request_hash TEXT NOT NULL,
 UNIQUE(episode_id,seq)
);
CREATE INDEX event_history ON events(episode_id, seq);
CREATE INDEX decision_events ON events(decision_id, kind, seq DESC);
CREATE TABLE dependencies (
 event_id TEXT NOT NULL REFERENCES events(id), source_id TEXT NOT NULL REFERENCES sources(id),
 reason TEXT NOT NULL, PRIMARY KEY(event_id,source_id)
);
CREATE INDEX dependent_events ON dependencies(source_id,event_id);
CREATE VIRTUAL TABLE search_index USING fts5(record_id UNINDEXED,kind UNINDEXED,title,body);
CREATE TRIGGER immutable_events_update BEFORE UPDATE ON events BEGIN
 SELECT RAISE(ABORT, 'Events cannot be changed; append a correction.'); END;
CREATE TRIGGER immutable_events_delete BEFORE DELETE ON events BEGIN
 SELECT RAISE(ABORT, 'Events cannot be deleted.'); END;
CREATE TRIGGER immutable_sources_update BEFORE UPDATE ON sources BEGIN
 SELECT RAISE(ABORT, 'Sources cannot be changed; add a version.'); END;
CREATE TRIGGER immutable_sources_delete BEFORE DELETE ON sources BEGIN
 SELECT RAISE(ABORT, 'Sources cannot be deleted.'); END;
CREATE TRIGGER immutable_dependencies_update BEFORE UPDATE ON dependencies BEGIN
 SELECT RAISE(ABORT, 'Evidence references cannot be changed.'); END;
CREATE TRIGGER immutable_dependencies_delete BEFORE DELETE ON dependencies BEGIN
 SELECT RAISE(ABORT, 'Evidence references cannot be deleted.'); END;
CREATE TRIGGER immutable_requirements_update BEFORE UPDATE ON settings BEGIN
 SELECT RAISE(ABORT, 'Project settings are fixed in this version.'); END;
CREATE TRIGGER immutable_requirements_delete BEFORE DELETE ON settings BEGIN
 SELECT RAISE(ABORT, 'Project settings cannot be deleted.'); END;
CREATE TRIGGER fixed_criteria BEFORE UPDATE OF title,objective,task_type,criterion,created_at ON episodes BEGIN
 SELECT RAISE(ABORT, 'Episode objective and criteria are fixed; start a new episode.'); END;
"""


class Memory(Workflow):
    """One project per file. Use one connection per thread or process.

    Call create() for a new project. The constructor opens an existing database
    and does not silently create an empty replacement for a mistyped path.
    """

    @classmethod
    def create(cls, path, project, requirements, *, clock=None):
        path = Path(path)
        _text(project, "project", 200)
        if not isinstance(requirements, list) or not requirements:
            raise InvalidRecord("Provide at least one agreed requirement.")
        for item in requirements:
            _text(item, "requirement", 2000)
        # Exclusive creation avoids replacing an existing project.
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb"):
                pass
        except FileExistsError as exc:
            raise Conflict("Database already exists; open it instead.") from exc
        db = sqlite3.connect(path)
        try:
            db.executescript(SCHEMA + UPGRADE)
            db.executemany("INSERT INTO settings VALUES (?,?)", [
                ("project", project), ("requirements", dumps(requirements))])
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            db.commit()
        except Exception:
            db.close()
            path.unlink(missing_ok=True)
            raise
        db.close()
        return cls(path, clock=clock)

    def __init__(self, path, *, clock=None, read_only=False):
        self.path = Path(path).resolve()
        self.clock = clock or (lambda: datetime.now(timezone.utc).isoformat())
        self.db = sqlite3.connect(self.path.as_uri() + ("?mode=ro" if read_only else "?mode=rw"), uri=True,
                                  isolation_level=None, timeout=5)
        self.db.row_factory = sqlite3.Row
        try:
            if self.db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
                raise InvalidRecord("Unsupported database version. Use migrate to upgrade version 1 to a new file.")
            self.db.execute("PRAGMA foreign_keys=ON")
            if read_only:
                self.db.execute("PRAGMA query_only=ON")
            else:
                self.db.execute("PRAGMA journal_mode=WAL")
                self.db.execute("PRAGMA synchronous=FULL")
            self.project = self.db.execute("SELECT value FROM settings WHERE key='project'").fetchone()[0]
            self.initial_requirements = json.loads(self.db.execute(
                "SELECT value FROM settings WHERE key='requirements'").fetchone()[0])
        except Exception:
            self.db.close()
            raise

    def close(self):
        self.db.close()

    @property
    def requirements(self):
        return self.direction()['requirements']

    def direction(self):
        from .direction import current
        return current(self)

    def approve_requirements(self, **values):
        from .direction import approve
        return approve(self, **values)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def now(self):
        return _time(self.clock())

    @contextmanager
    def _write(self):
        nested = self.db.in_transaction
        self.db.execute("SAVEPOINT memory_write" if nested else "BEGIN IMMEDIATE")
        try:
            yield
            if nested:
                self.db.execute("RELEASE memory_write")
            else:
                self.db.commit()
        except BaseException:
            if nested:
                self.db.execute("ROLLBACK TO memory_write")
                self.db.execute("RELEASE memory_write")
            else:
                self.db.rollback()
            raise

    def episode(self, episode_id):
        row = self.db.execute("SELECT * FROM episodes WHERE id=?", (episode_id,)).fetchone()
        if row is None:
            raise InvalidRecord("Episode was not found in this project.")
        value = dict(row)
        status = self.db.execute("SELECT payload FROM events WHERE episode_id=? AND kind='episode_status' ORDER BY seq DESC LIMIT 1", (episode_id,)).fetchone()
        value["status"] = json.loads(status[0])["status"] if status else "active"
        return value

    def start(self, title, objective, task_type, criterion, subject="general"):
        for key, value in locals().copy().items():
            if key != "self":
                _text(value, key, 2000)
        self._subject(subject)
        eid = _id("episode")
        with self._write():
            self.db.execute("INSERT INTO episodes VALUES (?,?,?,?,?,?,0,?)",
                            (eid, title, objective, task_type, criterion, self.now(), subject))
        return self.episode(eid)

    def source(self, source_key, title, summary, body, origin, review_after=None, subject="general"):
        for key, value in (("source_key", source_key), ("title", title), ("summary", summary)):
            _text(value, key, 2000)
        _text(body, "body", 5_000_000)
        if origin not in {"user", "tool", "document"}:
            raise InvalidRecord("Source origin must be user, tool or document.")
        review_after = _time(review_after) if review_after is not None else None
        self._subject(subject)
        sid = _id("source")
        with self._write():
            previous = self.db.execute("SELECT subject FROM sources WHERE source_key=? LIMIT 1", (source_key,)).fetchone()
            if previous and previous[0] != subject:
                raise InvalidRecord("A source key cannot change subject.")
            version = self.db.execute("SELECT coalesce(max(version),0)+1 FROM sources WHERE source_key=?",
                                      (source_key,)).fetchone()[0]
            self.db.execute("INSERT INTO sources (id,source_key,version,title,summary,body,origin,content_hash,checked_at,review_after,subject) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
                sid, source_key, version, title, summary, body, origin,
                _digest(body), self.now(), review_after, subject))
            self.db.execute("INSERT INTO search_index VALUES (?,?,?,?)",
                            (sid, "source", title, summary + "\n" + body))
        return {"id": sid, "version": version, "status": self.source_status(sid)}

    def source_status(self, source_id):
        source = self.db.execute("SELECT source_key,content_hash FROM sources WHERE id=?", (source_id,)).fetchone()
        if source is None:
            raise InvalidRecord("Evidence source was not found in this project.")
        latest = self.db.execute("SELECT content_hash,origin,source_key,review_after FROM sources WHERE source_key=? ORDER BY version DESC LIMIT 1",
                                 (source["source_key"],)).fetchone()
        if latest["content_hash"] != source["content_hash"]:
            return "superseded"
        if latest['origin'] == 'document':
            from .documents import file_status
            status = file_status(latest['source_key'], latest['content_hash'])
            if status and status != 'current_copy':
                return status
        if latest["review_after"] and latest["review_after"] <= self.now():
            return "review_due"
        return "current_copy"  # Version/freshness status, not a truth judgement.

    def document(self, path, *, subject=None, review_after=None):
        from .documents import capture
        return capture(self, path, subject=subject, review_after=review_after)

    def _event(self, event_id):
        row = self.db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        if row is None:
            raise InvalidRecord("Event was not found in this project.")
        value = dict(row)
        value["payload"] = json.loads(value["payload"])
        return value

    def _validate_payload(self, kind, payload):
        from .planning import validate_payload as validate_plan
        from . import guards
        if validate_plan(kind, payload):
            return
        if kind == "lesson_review" and isinstance(payload, dict):
            # Optional trigger overrides are validated here; the remaining fields keep their existing checks.
            guards.validate_triggers(payload)
            payload = {key: value for key, value in payload.items() if key not in guards.TRIGGERS}
        if validate_payload(kind, payload):
            return
        required = {
            "decision": {"decision", "why", "expected", "reconsider_when"},
            "action": {"action"},
            "outcome": {"observed", "assessment", "assessment_reason", "severity", "attribution"},
            "research": {"question", "findings", "gaps"},
            "lesson": {"when", "do", "because", "exceptions"},
            "note": {"text"},
        }[kind]
        optional = {
            "decision": {"uncertainty", "assumptions", "alternatives", "review_after", "model", "follow_up_owner", "condition", "case_id", "project_revision", "work_plan_id", "lessons_considered"},
            "action": {"host_reference"},
            "outcome": {"tokens", "human_corrections", "duration_ms", "failure_type", "model",
                        "context_characters", "research_calls", "repeated_research", "maintenance_ms", "completion"},
            "research": {"queries", "refresh_reason"},
            "lesson": {"pattern_type", "paths", "keywords", "failure_type"}, "note": set(),
        }[kind]
        if not isinstance(payload, dict) or set(payload) - required - optional or required - set(payload):
            raise InvalidRecord(f"{kind} requires {sorted(required)}; optional: {sorted(optional)}.")
        for key, value in payload.items():
            if kind == "decision" and key == "lessons_considered":
                guards.validate_considered(value)
            elif kind == "lesson" and key == "paths":
                guards.validate_patterns(value, minimum=0)
            elif kind == "lesson" and key == "keywords":
                guards.validate_keywords(value)
            elif key in {"queries", "assumptions", "alternatives"}:
                if not isinstance(value, list) or len(value) > 30:
                    raise InvalidRecord(f"{key} must be a list of at most 30 strings.")
                for item in value:
                    _text(item, key, 2000)
            elif key in {"tokens", "human_corrections", "duration_ms", "context_characters", "research_calls", "repeated_research", "maintenance_ms", "project_revision"}:
                if type(value) is not int or value < 0:
                    raise InvalidRecord(f"{key} must be a nonnegative integer.")
            elif key == "review_after":
                _time(value)
            else:
                _text(value, key)
        if kind == "outcome":
            if "completion" in payload and payload["completion"] not in {"complete", "partial", "blocked", "abandoned"}:
                raise InvalidRecord("completion must be complete, partial, blocked or abandoned.")
            if payload["assessment"] not in ASSESSMENTS:
                raise InvalidRecord("assessment must be pending, good, bad or unknown.")
            if payload["severity"] not in {"none", "minor", "major", "unknown"}:
                raise InvalidRecord("severity must be none, minor, major or unknown.")
        if kind == "lesson" and payload.get("pattern_type", "practice") not in {"practice", "anti_pattern", "recovery"}:
            raise InvalidRecord("pattern_type must be practice, anti_pattern or recovery.")

    def record(self, episode_id, kind, payload, *, expected_version, request_key,
               actor, evidence=None, decision_id=None, supersedes=None, links=None):
        """Append a checked record. A duplicate key succeeds only for identical content.

        Evidence is [{"source_id": ..., "reason": ...}]. Returned data confirms
        persistence only, not execution of the recorded action or factual truth.
        """
        if kind not in KINDS:
            raise InvalidRecord("Unknown event kind.")
        if type(expected_version) is not int or expected_version < 0:
            raise InvalidRecord("expected_version must be a nonnegative integer.")
        _text(request_key, "request_key", 200)
        _text(actor, "actor", 200)
        self._validate_payload(kind, payload)
        payload = json.loads(dumps(payload))
        if "review_after" in payload:
            payload["review_after"] = _time(payload["review_after"])
        evidence = evidence if evidence is not None else []
        if not isinstance(evidence, list) or len(evidence) > 100:
            raise InvalidRecord("evidence must be a list of at most 100 references.")
        for ref in evidence:
            if not isinstance(ref, dict) or set(ref) != {"source_id", "reason"}:
                raise InvalidRecord("Evidence needs source_id and a short reason.")
            _text(ref["source_id"], "source_id", 200)
            _text(ref["reason"], "evidence reason", 2000)
        if len({r["source_id"] for r in evidence}) != len(evidence):
            raise InvalidRecord("Evidence references must be unique.")
        evidence = sorted(evidence, key=lambda r: r["source_id"])
        links = self._validate_links(links)
        signature_parts = [episode_id, kind, payload, actor, evidence, decision_id, supersedes]
        if links:
            signature_parts.append(links)
        signature = _digest(dumps(signature_parts))
        with self._write():
            prior = self.db.execute("SELECT * FROM events WHERE request_key=?", (request_key,)).fetchone()
            if prior:
                if prior["request_hash"] != signature:
                    raise Conflict("Request key was already used for different content.")
                return {"id": prior["id"], "version": prior["seq"], "duplicate": True}
            episode = self.episode(episode_id)
            if episode["version"] != expected_version:
                raise Conflict(f"Episode is version {episode['version']}; read it before updating.")
            for ref in evidence:
                self.source_status(ref["source_id"])
            validate_event(self, episode, kind, payload, evidence, decision_id, links)
            from .planning import validate_event as validate_plan
            validate_plan(self, episode, kind, payload, evidence)
            for link in links:
                self._event(link["event_id"])
            if kind in {"action", "outcome"}:
                target = self._event(decision_id)
                if target["kind"] != "decision" or target["episode_id"] != episode_id:
                    raise InvalidRecord("Action/outcome must reference a decision in this episode.")
                if kind == "action":
                    if self.db.execute("SELECT 1 FROM events WHERE supersedes=?", (decision_id,)).fetchone():
                        raise InvalidRecord("Cannot start an action for a replaced decision.")
                    if self.db.execute("SELECT 1 FROM events WHERE decision_id=? AND kind='action'",
                                       (decision_id,)).fetchone():
                        raise Conflict("Action is already recorded; a retry needs a new decision.")
                elif not self.db.execute("SELECT 1 FROM events WHERE decision_id=? AND kind='action'",
                                         (decision_id,)).fetchone():
                    raise InvalidRecord("Record the action before its outcome.")
            elif decision_id is not None and kind not in {"action_result", "follow_up"}:
                raise InvalidRecord("Only action and outcome records accept decision_id.")
            previous = None
            if kind == "decision":
                previous = self.db.execute("SELECT id FROM events WHERE episode_id=? AND kind='decision' ORDER BY seq DESC LIMIT 1",
                                           (episode_id,)).fetchone()
            elif kind == "outcome":
                previous = self.db.execute("SELECT id FROM events WHERE decision_id=? AND kind='outcome' ORDER BY seq DESC LIMIT 1",
                                           (decision_id,)).fetchone()
                if payload["assessment"] in {"good", "bad"} and not evidence:
                    raise InvalidRecord("An assessed outcome needs observable evidence references.")
            elif kind in {'work_plan', 'sprint'}:
                previous = self.db.execute('SELECT id FROM events WHERE episode_id=? AND kind=? ORDER BY seq DESC LIMIT 1',
                                           (episode_id, kind)).fetchone()
            if (previous["id"] if previous else None) != supersedes:
                raise Conflict("supersedes must identify the latest decision/outcome or plan being revised.")
            event_id, seq = _id("event"), expected_version + 1
            if kind == 'decision':
                current_revision = self.direction()['version']
                if 'project_revision' in payload and payload['project_revision'] != current_revision:
                    raise Conflict('The decision must use the current project direction.')
                payload['project_revision'] = current_revision
                from .planning import latest
                plan = latest(self, episode_id, 'work_plan')
                if 'work_plan_id' in payload and (not plan or payload['work_plan_id'] != plan['id']):
                    raise Conflict('The decision must use the current work plan.')
                if plan:
                    payload['work_plan_id'] = plan['id']
                from .guards import require_acknowledgement
                require_acknowledgement(self, episode, payload)
            if kind in {'work_plan', 'sprint'}:
                from .schema import enable_plans
                enable_plans(self)
            self.db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
                event_id, episode_id, seq, kind, dumps(payload), self.now(), actor,
                decision_id, supersedes, request_key, signature, episode["subject"]))
            self.db.executemany("INSERT INTO dependencies VALUES (?,?,?)", [
                (event_id, ref["source_id"], ref["reason"]) for ref in evidence])
            self.db.executemany("INSERT INTO event_links VALUES (?,?,?)", [
                (event_id, ref["event_id"], ref["reason"]) for ref in links])
            self.db.execute("UPDATE episodes SET version=? WHERE id=?", (seq, episode_id))
            title = episode["title"] + " / " + kind
            self.db.execute("INSERT INTO search_index VALUES (?,?,?,?)", (
                event_id, kind, title, episode["objective"] + "\n" + dumps(payload)))
        return {"id": event_id, "version": seq, "duplicate": False}

    def read(self, record_id, *, detail=False):
        """Read exact stored text. Budgeted context should be used for discovery."""
        columns="*" if detail else "id,source_key,version,title,summary,origin,content_hash,checked_at,review_after,subject"
        source = self.db.execute("SELECT "+columns+" FROM sources WHERE id=?", (record_id,)).fetchone()
        if source:
            value = dict(source)
            value.update(kind="source", status=self.source_status(record_id))
            if value['origin'] == 'document':
                from .documents import document_path
                path = document_path(value['source_key'])
                if path is not None:
                    value['document'] = {'path': str(path), 'format': 'Markdown',
                        'authority': 'This is captured evidence. Decisions and lesson acceptance require separate explicit records.'}
            return value
        value = self._event(record_id)
        value.pop("request_hash")
        value.pop("request_key")
        value["evidence"] = [dict(row) for row in self.db.execute(
            "SELECT d.source_id,d.reason,s.title,s.origin FROM dependencies d JOIN sources s ON s.id=d.source_id WHERE d.event_id=? ORDER BY d.source_id",
            (record_id,))]
        for ref in value["evidence"]:
            ref["status"] = self.source_status(ref["source_id"])
        replacement = self.db.execute("SELECT id FROM events WHERE supersedes=?", (record_id,)).fetchone()
        value["replaced_by"] = replacement[0] if replacement else None
        value["status"] = "proposed" if value["kind"] == "lesson" else "recorded"
        if replacement:
            value["status"] = "replaced"
        elif any(ref["status"] != "current_copy" for ref in value["evidence"]):
            value["status"] = "needs_review"
        value["links"] = [dict(row) for row in self.db.execute(
            "SELECT prior_event_id AS event_id,reason FROM event_links WHERE event_id=? ORDER BY prior_event_id", (record_id,))]
        reasons=self.review_reasons(record_id) if not replacement else []
        if reasons:
            value['review_reasons']=reasons
            value["status"] = "needs_review"
        if value["kind"] == "lesson":
            review = self._lesson_review(record_id)
            value["lesson_status"] = review["status"] if review else "proposed"
            if value["status"] != "needs_review":
                value["status"] = value["lesson_status"]
        if detail:
            value["source_text"] = {ref["source_id"]: self.read(ref["source_id"], detail=True)["body"]
                                    for ref in value["evidence"]}
        return value

    def search(self, query, *, limit=10, include_history=False, subject=None, include_general=False, compact=False, offset=0):
        _text(query, "query", 2000)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise InvalidRecord("limit must be between 1 and 100.")
        if type(offset) is not int or offset < 0:
            raise InvalidRecord('offset must be a nonnegative integer.')
        terms = list(dict.fromkeys(re.findall(r"\w+", query, flags=re.UNICODE)))[:32]
        if not terms:
            return {"records": [], "more": False}
        expression = " OR ".join('"' + term + '"' for term in terms)
        filters = "" if include_history else """
          AND (s.id IS NULL OR s.version=(SELECT max(s2.version) FROM sources s2 WHERE s2.source_key=s.source_key))
          AND NOT EXISTS (SELECT 1 FROM events newer WHERE newer.supersedes=e.id)
        """
        params = [expression]
        if subject is not None:
            self._subject(subject)
            filters += " AND coalesce(e.subject,s.subject) IN (?,?)"
            params.extend([subject, "general" if include_general else subject])
        params.extend([limit+1, offset])
        rows = self.db.execute("""
          SELECT search_index.record_id FROM search_index
          LEFT JOIN sources s ON s.id=search_index.record_id
          LEFT JOIN events e ON e.id=search_index.record_id
          WHERE search_index MATCH ? AND search_index.kind IN ('source','decision','outcome','research','lesson','note','review','correction','work_plan','sprint')
        """ + filters + " ORDER BY bm25(search_index),search_index.record_id LIMIT ? OFFSET ?", params).fetchall()
        describe = self._index_entry if compact else self._descriptor
        return {"records": [describe(row[0]) for row in rows[:limit]], "more": len(rows) > limit}

    def _index_entry(self, record_id):
        value = self.read(record_id)
        title = value['title'] if value['kind'] == 'source' else self.episode(value['episode_id'])['title']
        entry = {'id': record_id, 'kind': value['kind'], 'subject': value['subject'], 'status': value['status'],
                 'title': title[:160], 'title_truncated': len(title) > 160,
                 'date': value.get('checked_at', value.get('created_at'))}
        if 'lesson_status' in value:
            entry['lesson_status'] = value['lesson_status']
        return entry

    def _descriptor(self, record_id):
        value = self.read(record_id)
        if value["kind"] == "source":
            text = f"{value['title']}: {value['summary']}\nSource: {value['origin']}; status: {value['status']}. Read the original for precise claims."
        else:
            # Full fields preserve exceptions and negations; budget packing drops
            # whole records rather than silently cutting the end of a lesson.
            text = self._event_text(value)
        return {"id": record_id, "kind": value["kind"], "subject": value["subject"], "status": value["status"],
                "text": text, "signature": _digest(dumps(value))}

    def context(self, query, *, episode_id=None, budget=4000, count_tokens=None, seen=None,
                subject=None, include_general=False, wrapper_prefix="", wrapper_suffix="", count_characters=None):
        """Pack whole records. Default budget is CHARACTERS, not estimated tokens.

        count_tokens can supply the host's tokenizer for an exact count of this
        serialized reply. Pass exact wrapper_prefix and wrapper_suffix to include tool wrapper text.
        """
        if type(budget) is not int or budget <= 0:
            raise InvalidRecord("budget must be a positive integer.")
        if count_tokens and count_characters:
            raise InvalidRecord('Choose a token counter or a character counter, not both.')
        count = count_tokens or count_characters or len
        unit = "tokens" if count_tokens else "characters"
        seen = seen or {}
        direction=self.direction()
        required = [{"id": "requirements", "kind": "requirements", "text": "\n".join(direction["requirements"])}]
        if direction["version"]:
            required[0].update(version=direction["version"],status=direction["status"])
        signature = _digest(dumps([direction['version'], direction['requirements'], direction.get('status', 'current')]))
        required[0]['signature'] = signature
        if seen.get('requirements') == signature:
            required[0].pop('text')
            required[0]['text'] = 'The previously retrieved requirements are unchanged and still apply.'
        if episode_id:
            episode = self.episode(episode_id)
            if subject is not None and subject != episode["subject"]:
                raise InvalidRecord("Context subject must match the selected episode.")
            subject = episode["subject"]
            required.append({"id": episode_id, "kind": "objective", "text":
                             f"Objective: {episode['objective']}\nSuccess criterion: {episode['criterion']}"})
            plan = self.db.execute("SELECT id FROM events WHERE episode_id=? AND kind='work_plan' ORDER BY seq DESC LIMIT 1", (episode_id,)).fetchone()
            if plan:
                required.append(self._descriptor(plan[0]))
            current = self.db.execute("SELECT id FROM events WHERE episode_id=? AND kind='decision' ORDER BY seq DESC LIMIT 1",
                                      (episode_id,)).fetchone()
            if current:
                required.append(self._descriptor(current[0]))
        subject = subject or "general"
        matches = self.search(query, limit=100, subject=subject, include_general=include_general)
        required_ids = {record["id"] for record in required}
        candidates = [record for record in matches["records"] if record["id"] not in required_ids
                      and seen.get(record["id"]) != record["signature"]]
        packet = {"project": self.project, "scope":{"subject":subject,"include_general":include_general}, "unit": unit, "budget": budget,
                  "used": 0, "omitted": len(candidates), "more_matches": matches["more"], "records": required}

        def size():
            for _ in range(10):
                measured = count(wrapper_prefix + dumps(packet) + wrapper_suffix)
                if type(measured) is not int or measured < 0:
                    raise InvalidRecord("Token counter must return a nonnegative integer.")
                if measured == packet["used"]:
                    return measured
                packet["used"] = measured
            raise InvalidRecord("Counter did not produce a stable count.")

        if size() > budget:
            raise BudgetTooSmall(f"Required task context needs {packet['used']} {unit}; budget is {budget}.",
                minimum_required=packet['used'], unit=unit, max_chars_limit=20000,
                required_ids=[r['id'] for r in required],
                next_call={'tool':'memory_get','arguments':{'view':'requirements','offset':0,'limit':10}},
                note='Read every requirements page before reusing its signature in seen.requirements. Do not omit constraints you have not read.')
        # Reserve bounded discovery information before packing full evidence.
        omitted = []
        packet['omitted_records'] = omitted
        packet['expand'] = {'tool':'memory_get','view':'search','query':query,'subject':subject,
                            'include_general':include_general,'offset':0}
        if size() > budget:
            packet.pop('expand'); packet.pop('omitted_records')
        for record in candidates:
            packet["records"].append(record)
            packet["omitted"] -= 1
            if size() > budget:
                packet["records"].pop()
                packet["omitted"] += 1
                if 'omitted_records' in packet and len(omitted) < 5:
                    omitted.append({'id':record['id'],'kind':record['kind'],
                                    'reason':'The complete record does not fit the remaining budget.',
                                    'read':{'view':'record','id':record['id']}})
                    if size() > budget: omitted.pop()
                size()
        assert size() <= budget
        return packet

    def _event_text(self, event):
        labels = {"decision": "Decision", "why": "Why", "expected": "Expected consequence",
                  "reconsider_when": "Reconsider when", "observed": "Observed consequence",
                  "assessment_reason": "Assessment reason", "review_after": "Check after",
                  "when": "Applies when", "do": "Suggested action", "because": "Reason",
                  "attribution": "What caused it"}
        lines = [f"{event['kind'].capitalize()} · {event['created_at']} · {event['status']}"]
        order = {
            "decision": ["decision", "why", "uncertainty", "assumptions", "alternatives", "expected", "reconsider_when", "review_after", "model"],
            "outcome": ["observed", "assessment", "assessment_reason", "severity", "attribution"],
            "research": ["question", "queries", "findings", "gaps", "refresh_reason"],
            "lesson": ["when", "do", "because", "exceptions"],
        }.get(event["kind"], [])
        keys = [key for key in order if key in event["payload"]]
        keys.extend(key for key in event["payload"] if key not in keys)
        for key in keys:
            value = event["payload"][key]
            lines.append(f"{labels.get(key, key.replace('_', ' ').capitalize())}: " +
                         ("; ".join(item if isinstance(item, str) else
                          f"{item['episode_id']}: {item['reason']}" if key == 'depends_on' else
                          f"{item['lesson_id']} (applies: {item['applies']}): {item['reason']}" if key == 'lessons_considered' else
                          f"{item['location']} ({item['severity']}): {item['issue']}" for item in value) if isinstance(value, list) else str(value)))
        if event["supersedes"]:
            lines.append("Replaces: " + event["supersedes"])
        if event["replaced_by"]:
            lines.append("Replaced by: " + event["replaced_by"])
        if event["decision_id"]:
            lines.append("Decision reference: " + event["decision_id"])
        if event["kind"] == "lesson":
            if event.get("lesson_status", "proposed") == "proposed":
                lines.append("This lesson is proposed; it has not been activated or independently validated.")
            elif event.get('lesson_status') in {'rejected', 'retired'}:
                lines.append('This lesson is ' + event['lesson_status'] + '; do not apply it. It is retained as history.')
            elif event.get('status') == 'needs_review':
                lines.append('Review the supporting evidence before using this lesson again.')
            else:
                lines.append("Lesson review: " + event["lesson_status"] + ". Apply only within its stated conditions and exceptions.")
        for ref in event.get("links", []):
            lines.append(f"Earlier event: {ref['event_id']}: {ref['reason']}")
        lines.append("Evidence:")
        if not event["evidence"]:
            lines.append("- No evidence attached.")
        for ref in event["evidence"]:
            lines.append(f"- {ref['source_id']} ({ref['status']}, {ref['origin']}): {ref['reason']}")
        return "\n".join(lines)

    def history(self, episode_id=None, *, day=None):
        if episode_id:
            episode = self.episode(episode_id)
            header = f"# {episode['title']}\n\nObjective: {episode['objective']}\n\nSuccess criterion: {episode['criterion']}"
        else:
            header = f"# {self.project} — history"
        conditions, args = [], []
        if episode_id:
            conditions.append("episode_id=?")
            args.append(episode_id)
        if day:
            try:
                datetime.strptime(day, "%Y-%m-%d")
            except ValueError as exc:
                raise InvalidRecord("day must be YYYY-MM-DD, in UTC.") from exc
            conditions.append("substr(created_at,1,10)=?")
            args.append(day)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        rows = self.db.execute("SELECT id FROM events" + where + " ORDER BY created_at,rowid", args).fetchall()
        return header + "\n\n" + "\n\n".join(self._event_text(self.read(row[0])) for row in rows) + "\n"

    def metrics(self):
        """Latest outcome per decision, grouped by exact task type and criterion.

        A recorded action is not proof that the host actually executed it.
        Outcome labels are supplied by the caller; counts are computed here.
        """
        rows = self.db.execute("""
          SELECT d.id,d.payload AS decision_payload,ep.task_type,ep.criterion,ep.subject,
            (SELECT payload FROM events o WHERE o.decision_id=d.id AND o.kind='outcome' ORDER BY seq DESC LIMIT 1) AS outcome,
            EXISTS(SELECT 1 FROM events a WHERE a.decision_id=d.id AND a.kind='action') AS acted
          FROM events d JOIN episodes ep ON ep.id=d.episode_id WHERE d.kind='decision'
        """).fetchall()
        groups = {}
        for row in rows:
            model = json.loads(row["decision_payload"]).get("model", "unspecified")
            decision_payload = json.loads(row["decision_payload"])
            key = (row["task_type"], row["criterion"], model, row["subject"], decision_payload.get("condition", "unspecified"))
            group = groups.setdefault(key, {"task_type": key[0], "criterion": key[1], "model": key[2], "subject": key[3], "condition": key[4], "decisions": 0,
                "assessed": 0, "good": 0, "bad": 0, "pending": 0, "unknown": 0, "not_acted": 0,
                "major_bad": 0, "bad_outcome_rate": None,
                "reported_costs": {k: {"total": 0, "reported_decisions": 0}
                    for k in ("tokens", "human_corrections", "duration_ms", "context_characters", "research_calls", "repeated_research", "maintenance_ms")},
                "completion": {k: 0 for k in ("complete", "partial", "blocked", "abandoned", "unmeasured")}, "failure_types": {}})
            group["decisions"] += 1
            if not row["acted"]:
                group["not_acted"] += 1
                continue
            outcome = json.loads(row["outcome"]) if row["outcome"] else {"assessment": "pending"}
            assessment = outcome["assessment"]
            group[assessment] += 1
            group["completion"][outcome.get("completion", "unmeasured")] += 1
            for metric, totals in group["reported_costs"].items():
                if metric in outcome:
                    totals["total"] += outcome[metric]
                    totals["reported_decisions"] += 1
            if assessment in {"good", "bad"}:
                group["assessed"] += 1
                if assessment == "bad" and outcome["severity"] == "major":
                    group["major_bad"] += 1
                if assessment == "bad" and outcome.get("failure_type"):
                    failure = outcome["failure_type"]
                    group["failure_types"][failure] = group["failure_types"].get(failure, 0) + 1
        for group in groups.values():
            if group["assessed"]:
                group["bad_outcome_rate"] = group["bad"] / group["assessed"]
        episodes = [self.episode(r[0]) for r in self.db.execute('SELECT id FROM episodes')]
        episode_counts = {state: sum(e['status'] == state for e in episodes) for state in ('active', 'reopened', 'settled', 'abandoned')}
        episode_counts['without_decisions'] = self.db.execute("SELECT count(*) FROM episodes ep WHERE NOT EXISTS (SELECT 1 FROM events e WHERE e.episode_id=ep.id AND e.kind='decision')").fetchone()[0]
        return {"project": self.project, "groups": list(groups.values()), "episodes": episode_counts,
                "meaning": "Descriptive rates from recorded assessments, not proof of causation or improvement."}

    def backup(self, destination):
        """Portable snapshot using SQLite backup, including committed WAL content."""
        destination = Path(destination)
        if destination.resolve() == self.path:
            raise InvalidRecord("Backup destination must differ from the open database.")
        try:
            with destination.open("xb"):
                pass
        except FileExistsError as exc:
            raise Conflict("Backup destination already exists.") from exc
        target = sqlite3.connect(destination)
        try:
            self.db.backup(target)
        except BaseException:
            target.close()
            destination.unlink(missing_ok=True)
            raise
        target.close()
        return {"path": str(destination), "project": self.project}
