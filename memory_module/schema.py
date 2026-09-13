"""Additive schema changes. Version 1 files are migrated to a new file."""

UPGRADE = """
ALTER TABLE episodes ADD COLUMN subject TEXT NOT NULL DEFAULT 'general'
 CHECK(subject IN ('general','code','writing','research'));
ALTER TABLE sources ADD COLUMN subject TEXT NOT NULL DEFAULT 'general'
 CHECK(subject IN ('general','code','writing','research'));
ALTER TABLE events ADD COLUMN subject TEXT NOT NULL DEFAULT 'general'
 CHECK(subject IN ('general','code','writing','research'));
CREATE INDEX event_subject ON events(subject,kind,created_at);
CREATE INDEX source_subject ON sources(subject,checked_at);
CREATE TABLE event_links (
 event_id TEXT NOT NULL REFERENCES events(id),
 prior_event_id TEXT NOT NULL REFERENCES events(id),
 reason TEXT NOT NULL CHECK(length(trim(reason)) > 0),
 PRIMARY KEY(event_id,prior_event_id), CHECK(event_id != prior_event_id)
);
CREATE INDEX prior_links ON event_links(prior_event_id,event_id);
CREATE TRIGGER immutable_links_update BEFORE UPDATE ON event_links BEGIN
 SELECT RAISE(ABORT, 'Links cannot be changed.'); END;
CREATE TRIGGER immutable_links_delete BEFORE DELETE ON event_links BEGIN
 SELECT RAISE(ABORT, 'Links cannot be deleted.'); END;
CREATE TRIGGER checked_link BEFORE INSERT ON event_links WHEN
 (SELECT rowid FROM events WHERE id=NEW.prior_event_id) >=
 (SELECT rowid FROM events WHERE id=NEW.event_id) BEGIN
 SELECT RAISE(ABORT, 'A dependency must reference an earlier event.'); END;
CREATE TRIGGER checked_event BEFORE INSERT ON events BEGIN
 SELECT CASE WHEN NEW.id IS NULL OR length(trim(NEW.id))=0
  OR NOT json_valid(NEW.payload) THEN RAISE(ABORT, 'Invalid event ID or JSON.') END;
 SELECT CASE WHEN NEW.kind NOT IN
 ('decision','action','outcome','research','lesson','note','review','correction',
 'action_result','follow_up','episode_status','lesson_review')
 THEN RAISE(ABORT, 'Unknown event kind.') END;
 SELECT CASE WHEN NEW.subject != (SELECT subject FROM episodes WHERE id=NEW.episode_id)
 THEN RAISE(ABORT, 'Event subject must match its episode.') END;
END;
CREATE TRIGGER checked_source BEFORE INSERT ON sources BEGIN
 SELECT CASE WHEN NEW.id IS NULL OR length(trim(NEW.id))=0
 THEN RAISE(ABORT, 'Invalid source ID.') END;
END;
CREATE TRIGGER fixed_subject BEFORE UPDATE OF subject ON episodes BEGIN
 SELECT RAISE(ABORT, 'Episode subject is fixed.'); END;
CREATE TRIGGER checked_episode BEFORE INSERT ON episodes WHEN NEW.id IS NULL BEGIN
 SELECT RAISE(ABORT, 'Invalid episode ID.'); END;
"""


def migrate(source, destination):
    """Preserve the original, including committed WAL changes; upgrade a copy."""
    from pathlib import Path
    import sqlite3
    from .core import Conflict, InvalidRecord
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination:
        raise InvalidRecord('Migration needs a different destination.')
    old = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)
    try:
        version = old.execute('PRAGMA user_version').fetchone()[0]
        if version != 1:
            raise InvalidRecord('Migration expects a version 1 database.')
        try:
            with destination.open('xb'):
                pass
        except FileExistsError as exc:
            raise Conflict('Migration destination already exists.') from exc
        new = sqlite3.connect(destination)
        try:
            old.backup(new)
            new.executescript('BEGIN IMMEDIATE;\n' + UPGRADE + '\nPRAGMA user_version=2; COMMIT;')
            if new.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or new.execute('PRAGMA foreign_key_check').fetchone():
                raise InvalidRecord('Migrated database failed integrity checks.')
        except BaseException:
            new.close()
            destination.unlink(missing_ok=True)
            raise
        new.close()
    finally:
        old.close()
    return {'path': str(destination), 'schema_version': 2, 'original_unchanged': True}
