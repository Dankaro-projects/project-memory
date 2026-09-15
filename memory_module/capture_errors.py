"""Durable failure records when the project database cannot accept a hook."""
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

from .core import dumps, InvalidRecord


def paths(database):
    database = Path(database)
    legacy = database.with_suffix('.capture-error.json')
    return ([legacy] if legacy.exists() else []) + sorted(database.with_suffix('.capture-errors').glob('*.json'))


def record(database, event, error):
    from .install import atomic
    folder = Path(database).with_suffix('.capture-errors')
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    event = event if isinstance(event,dict) else {}
    session = event.get('session_id')
    name = event.get('hook_event_name')
    value = {'id':uuid.uuid4().hex, 'session_id':session if isinstance(session,str) and session else 'unknown',
             'event_name':name if isinstance(name,str) and name else 'unknown', 'error':type(error).__name__,
             'created_at':datetime.now(timezone.utc).isoformat()}
    atomic(folder/(value['id']+'.json'), dumps(value))


def recover(memory):
    from .codex_host import receipt
    pending = []
    for path in paths(memory.path):
        try:
            gap = json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            continue  # Another worker committed and removed this same record.
        if not isinstance(gap,dict) or not isinstance(gap.get('session_id'),str):
            raise InvalidRecord('A capture failure record is invalid. Inspect the private capture diagnostics.')
        pending.append((path,gap))
    if not pending: return
    with memory._write():
        for _,gap in pending:
            receipt(memory,session_id=gap['session_id'],event_name='CaptureRecovered',payload=gap,
                    key='capture-gap:'+dumps(gap))
    # Commit first. A crash or concurrent recovery can safely replay these IDs.
    for path,_ in pending: path.unlink(missing_ok=True)


def summary(database):
    files = paths(database)
    if not files: return None
    sessions = set()
    unreadable = 0
    count = 0
    for path in files:
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(value,dict) or not isinstance(value.get('session_id'),str):
                raise ValueError('Invalid capture session.')
            sessions.add(value['session_id'])
        except FileNotFoundError:
            continue
        except (OSError,ValueError,KeyError,TypeError):
            unreadable += 1
        count += 1
    if not count: return None
    return {'count':count,'session_count':len(sessions),'sessions':sorted(sessions)[:5],
            'more_sessions':len(sessions)>5,'unreadable_records':unreadable}


def signature(database):
    values = []
    for path in paths(database):
        try:
            stat = path.stat()
            values.append((path.name,stat.st_mtime_ns,stat.st_size))
        except FileNotFoundError:
            continue
    return values
