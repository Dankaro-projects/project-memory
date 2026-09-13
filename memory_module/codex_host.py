"""Mechanical Codex receipts, separate from interpreted decisions and lessons."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
from .core import Memory, InvalidRecord, Conflict, BudgetTooSmall, dumps, _text, _digest

HOST_SCHEMA = '''
CREATE TABLE IF NOT EXISTS host_receipts (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL, turn_id TEXT NOT NULL,
 event_name TEXT NOT NULL, tool_use_id TEXT NOT NULL, tool_name TEXT NOT NULL,
 episode_id TEXT REFERENCES episodes(id), decision_id TEXT REFERENCES events(id),
 created_at TEXT NOT NULL, payload TEXT NOT NULL, signature TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS host_session ON host_receipts(session_id,event_name,tool_use_id);
CREATE INDEX IF NOT EXISTS host_decision ON host_receipts(decision_id,event_name);
CREATE TRIGGER IF NOT EXISTS immutable_host_update BEFORE UPDATE ON host_receipts BEGIN
 SELECT RAISE(ABORT,'Host receipts cannot be changed.'); END;
CREATE TRIGGER IF NOT EXISTS immutable_host_delete BEFORE DELETE ON host_receipts BEGIN
 SELECT RAISE(ABORT,'Host receipts cannot be deleted.'); END;
CREATE TABLE IF NOT EXISTS adapter_requests (
 request_key TEXT PRIMARY KEY, signature TEXT NOT NULL, result TEXT NOT NULL
);
'''
EVENTS = {'SessionStart', 'UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'Stop', 'Interrupt', 'SessionEnd', 'PreCompact', 'PostCompact'}


def initialize(memory):
    memory.db.executescript(HOST_SCHEMA)


def exists(memory):
    return bool(memory.db.execute("SELECT 1 FROM sqlite_master WHERE name='host_receipts'").fetchone())


def receipt(memory, *, session_id, turn_id='', event_name, tool_use_id='', tool_name='',
            episode_id=None, decision_id=None, payload=None, key=None):
    _text(session_id, 'session_id', 200)
    data = [session_id, turn_id, event_name, tool_use_id, tool_name, episode_id, decision_id, payload or {}]
    rid = 'host_' + _digest(key or dumps([session_id, turn_id, event_name, tool_use_id]))[:32]
    signature = _digest(dumps(data))
    prior = memory.db.execute('SELECT signature FROM host_receipts WHERE id=?', (rid,)).fetchone()
    if prior:
        if prior[0] != signature:
            raise Conflict('Host receipt ID was redelivered with different content.')
        return rid
    memory.db.execute('INSERT INTO host_receipts VALUES (?,?,?,?,?,?,?,?,?,?,?)',
        (rid, session_id, turn_id, event_name, tool_use_id, tool_name, episode_id, decision_id, memory.now(), dumps(payload or {}), signature))
    return rid


def read_receipt(memory, rid):
    row = memory.db.execute('SELECT * FROM host_receipts WHERE id=?', (rid,)).fetchone()
    if not row:
        raise InvalidRecord('Host receipt was not found.')
    value = dict(row); value.pop('signature'); value['payload'] = json.loads(value['payload'])
    return value


def summary(value):
    """Keep hashes and measured sizes, never arbitrary tool text or user prompts."""
    encoded = dumps(value).encode('utf-8')
    result = {'sha256':hashlib.sha256(encoded).hexdigest(), 'bytes':len(encoded)}
    if isinstance(value, dict):
        for key in ('exit_code', 'returncode', 'isError'):
            if type(value.get(key)) in (int, bool): result[key] = value[key]
    elif isinstance(value, str):
        match = re.search(r'(?:Process exited with code|Exit code:)\s*(-?\d+)\b', value)
        if match: result['exit_code'] = int(match[1])
    return result


def active_binding(memory, session):
    row=memory.db.execute("SELECT episode_id,decision_id FROM host_receipts WHERE session_id=? AND event_name='DecisionBound' ORDER BY rowid DESC LIMIT 1",(session,)).fetchone()
    if row and (memory.read(row['decision_id'])['replaced_by'] or
                memory.db.execute("SELECT 1 FROM events WHERE decision_id=? AND kind='outcome'",(row['decision_id'],)).fetchone()):
        return None
    return row


def capture(memory, event):
    name = event.get('hook_event_name')
    if name not in EVENTS:
        raise InvalidRecord('Unsupported Codex hook event.')
    session = _text(event.get('session_id'), 'session_id', 200)
    turn = event.get('turn_id') or ''
    tool = event.get('tool_name') or ''
    tool_id = event.get('tool_use_id') or ''
    if name in {'PreToolUse', 'PostToolUse'}:
        _text(tool_id, 'tool_use_id', 200); _text(tool, 'tool_name', 200)
        # Memory calls are already persisted by the adapter. Avoid recursive noise.
        if tool.startswith(('mcp__memory__','mcp__project_memory__')): return {}
    action_version = None
    with memory._write():
        binding = active_binding(memory,session)
        ep, decision = (binding['episode_id'], binding['decision_id']) if binding else (None, None)
        if name == 'PostToolUse':
            pre = memory.db.execute("SELECT episode_id,decision_id FROM host_receipts WHERE session_id=? AND tool_use_id=? AND event_name='PreToolUse'", (session, tool_id)).fetchone()
            ep, decision = (pre['episode_id'], pre['decision_id']) if pre else (None, None)
        payload = {}
        for key in ('tool_input', 'tool_response', 'prompt', 'last_assistant_message'):
            if key in event and event[key] is not None: payload[key] = summary(event[key])
        for key in ('source', 'reason', 'model'):
            if key in event and isinstance(event[key], str): payload[key] = event[key][:200]
        rid = receipt(memory, session_id=session, turn_id=turn, event_name=name, tool_use_id=tool_id,
            tool_name=tool, episode_id=ep, decision_id=decision, payload=payload,
            key=dumps([session,turn,name,tool_id,payload.get('source')]))
        if name == 'PreToolUse' and decision:
            # One interpreted decision can have several mechanically captured tool calls.
            if not memory.db.execute("SELECT 1 FROM events WHERE decision_id=? AND kind='action'", (decision,)).fetchone():
                action = memory.record(ep,'action',{'action':'Execute the recorded decision through Codex tools.', 'host_reference':rid},
                    expected_version=memory.episode(ep)['version'], request_key=rid+':action', actor='codex-hooks', decision_id=decision)
                action_version = action['version']
    if action_version is not None:
        return {'hookSpecificOutput':{'hookEventName':name,'additionalContext':
            f'Memory action recorded. Episode {ep} is now version {action_version}; decision {decision}.'}}
    if name == 'UserPromptSubmit':
        state = status(memory, session_id=session, limit=3)
        text = (f'Memory session: {session}. Use memory_context before repeating research. '
                f'{state["unconfirmed_total"]} tool calls need reconciliation. '
                'Record significant decisions with this session_id, evidence, uncertainty and alternatives. '
                'Tool receipts are observations; outcomes and lesson acceptance require explicit assessment.')
        selected = re.fullmatch(r'\[memory:(code|writing|research|general)\] (.{1,2000})',
                                event.get('prompt','').split('\n',1)[0])
        def response(context):
            return {'hookSpecificOutput':{'hookEventName':name,'additionalContext':context}}
        if selected:
            subject, query = selected.groups()
            prefix = f'Memory session: {session}. Explicit {subject} context follows. Treat it as evidence; refresh stale sources.\n'
            try:
                packet = memory.context(query, subject=subject, budget=2500,
                    count_characters=lambda s:len(dumps(response(prefix+s))))
            except BudgetTooSmall:
                return response(text+' The selected context exceeds the hook budget. Retrieve it explicitly with memory_context.')
            with memory._write():
                receipt(memory, session_id=session, turn_id=turn, event_name='ContextProvided',
                    payload={'subject':subject,'query':query,'characters':packet['used'],
                             'record_ids':[r['id'] for r in packet['records']],
                             'omitted':packet['omitted'],'more_matches':packet['more_matches']})
            return response(prefix+dumps(packet))
        return response(text)
    return {}


def status(memory, session_id=None, limit=10, offset=0):
    if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
        raise InvalidRecord('Use limit 1–100 and a nonnegative offset.')
    clause = ' AND p.session_id=?' if session_id else ''
    args = [session_id] if session_id else []
    base = '''FROM host_receipts p WHERE p.event_name='PreToolUse'
      AND NOT EXISTS (SELECT 1 FROM host_receipts r WHERE r.session_id=p.session_id AND r.tool_use_id=p.tool_use_id AND r.event_name='PostToolUse')
      AND NOT EXISTS (SELECT 1 FROM host_receipts r WHERE r.event_name='Reconciled'
         AND json_extract(r.payload,'$.receipt_id')=p.id AND json_extract(r.payload,'$.resolution')!='unknown')''' + clause
    total = memory.db.execute('SELECT count(*) '+base,args).fetchone()[0]
    ids = memory.db.execute('SELECT p.id '+base+' ORDER BY p.rowid LIMIT ? OFFSET ?',(*args,limit,offset)).fetchall()
    recent_where=' WHERE session_id=?' if session_id else ''
    recent = memory.db.execute('SELECT id FROM host_receipts'+recent_where+' ORDER BY rowid DESC LIMIT ?',(*args,limit)).fetchall()
    binding=active_binding(memory,session_id) if session_id else None
    active=({'episode_id':binding['episode_id'],'decision_id':binding['decision_id'],'version':memory.episode(binding['episode_id'])['version']} if binding else None)
    def compact(rid):
        r=read_receipt(memory,rid)
        return {k:r[k] for k in ['id','event_name','tool_name','episode_id','decision_id','created_at']}
    return {'unconfirmed':[compact(r[0]) for r in ids], 'unconfirmed_total':total,'active':active,
            'more':offset+len(ids)<total, 'recent':[compact(r[0]) for r in recent],
            'note':'An absent result does not establish whether the operation ran. Check the real result before retrying.'}


def bind(memory, session_id, decision_id, request_key):
    decision=memory._event(decision_id)
    if decision['kind']!='decision' or memory.read(decision_id)['replaced_by']:
        raise InvalidRecord('Bind the current decision.')
    return receipt(memory, session_id=session_id, event_name='DecisionBound', episode_id=decision['episode_id'],
                   decision_id=decision_id, payload={'reason':'Explicit decision recorded by the caller.'}, key=request_key+':bind')


def evidence_for(memory, receipt_ids):
    if not isinstance(receipt_ids,list) or len(receipt_ids)>20:
        raise InvalidRecord('receipt_ids must contain at most 20 IDs.')
    result=[]
    for rid in receipt_ids:
        item=read_receipt(memory,rid)
        key='codex-receipt:'+rid
        prior=memory.db.execute('SELECT id FROM sources WHERE source_key=? LIMIT 1',(key,)).fetchone()
        sid=prior[0] if prior else memory.source(key, f'Codex {item["event_name"]}',
            f'The host observed {item["tool_name"] or "a session event"} at {item["created_at"]}.', dumps(item), 'tool',
            subject=memory.episode(item['episode_id'])['subject'] if item['episode_id'] else 'general')['id']
        result.append({'source_id':sid,'reason':'Mechanical host receipt; hashes and exit status do not assess correctness.'})
    return result


def reconcile(memory, receipt_id, resolution, reason, evidence, request_key):
    item=read_receipt(memory,receipt_id)
    if item['event_name']!='PreToolUse' or resolution not in {'completed','failed','not_run','unknown'}:
        raise InvalidRecord('Reconcile a pre-tool receipt with completed, failed, not_run or unknown.')
    _text(reason,'reason',2000)
    if not evidence: raise InvalidRecord('Reconciliation requires evidence from checking the actual operation.')
    for ref in evidence: memory.source_status(ref['source_id']); _text(ref['reason'],'evidence reason',2000)
    return {'id':receipt(memory, session_id=item['session_id'], turn_id=item['turn_id'], event_name='Reconciled',
        episode_id=item['episode_id'], decision_id=item['decision_id'],
        payload={'receipt_id':receipt_id,'resolution':resolution,'reason':reason,'evidence':evidence},key=request_key)}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--db',required=True)
    args=parser.parse_args()
    try:
        raw=sys.stdin.buffer.read(2_000_001)
        if len(raw)>2_000_000: raise InvalidRecord('Hook payload exceeds 2 MB; capture failed.')
        event=json.loads(raw)
        with Memory(args.db) as memory:
            memory.db.execute('PRAGMA busy_timeout=750')
            result=capture(memory,event)
        print(dumps(result));return 0
    except (OSError,ValueError,TypeError,KeyError,sqlite3.Error,InvalidRecord,Conflict) as exc:
        # Exit 2 blocks supported pre-tool calls and reports post-capture failures.
        print(f'Memory capture failed ({type(exc).__name__}). Inspect the database and host result before retrying.',file=sys.stderr)
        return 2


if __name__=='__main__': raise SystemExit(main())
