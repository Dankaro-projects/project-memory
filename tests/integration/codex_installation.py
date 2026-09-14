"""Verify an installed project through a fresh native Codex host; keep logs private.

Usage: python -m tests.integration.codex_installation PROJECT PRIVATE_OUTPUT
Requires the actual Codex CLI login and completed, trusted project setup.
The versioned marker prevents an accidental repeat of this verification.
"""
import json,sys,time,uuid
from pathlib import Path
from tests.integration.codex_app_client import CodexClient
from memory_module import Memory, __version__
from memory_module.codex_host import status
project=Path(sys.argv[1]).resolve()
output=Path(sys.argv[2]).resolve();output.mkdir(parents=True,exist_ok=True)
marker=project/f'.memory/{__version__}-installation-check.txt'
if marker.exists():raise FileExistsError(marker)
text=f'Project Memory {__version__} preserves this project: '+uuid.uuid4().hex
marker.write_text(text+'\n')
source_key=f'verification:{__version__}:'+uuid.uuid4().hex
client=CodexClient(project,output/'events.jsonl')
start=time.monotonic()
try:
    session=client.start()
    prompt=f'''Verify the upgraded Project Memory connection in this project only. This is an informational installation check; do not create an episode, plan, decision or lesson, modify product files or launch reviewers. Use the native memory_get health tool once with max_chars 12000. Read {marker} once using one shell command. Store its exact text as a tool-origin source through native memory_write source with source_key {source_key!r}, title "The installed host reads and captures a verification marker.", summary "This is a bounded installed integration check.", and subject code. Use this memory session ID: {session}. Assess this informational request with memory_write checkpoint, effect informational and a concrete reason, using the observed prompt ID from memory_get coverage. Finish in one sentence; do not repeat a successful read or source write.'''
    turn=client.turn(session,prompt)
    completed=client.complete(turn,timeout=180)
finally:
    client.close()
state=json.loads((project/'.memory/install.json').read_text())
with Memory(state['database'],read_only=True) as m:
    source=m.db.execute('SELECT id,body FROM sources WHERE source_key=?',(source_key,)).fetchone()
    counts=dict(m.db.execute('SELECT event_name,count(*) FROM host_receipts WHERE session_id=? GROUP BY event_name',(session,)))
    calls=[v['params']['item'] for v in client.events if v.get('method')=='item/completed' and v['params']['item'].get('type')=='mcpToolCall']
    result={'session_id':session,'status':completed['status'],'source_matches':bool(source and source['body']==text+'\n'),'source_id':source['id'] if source else None,'hook_counts':counts,'mcp_calls':[{'tool':c.get('tool'),'server':c.get('server'),'status':c.get('status'),'error':c.get('error')} for c in calls],'unconfirmed':status(m,session_id=session)['unconfirmed_total'],'duration_ms':round((time.monotonic()-start)*1000),'usage':[v['params'] for v in client.events if v.get('method')=='thread/tokenUsage/updated'][-1:]}
(output/'result.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result))
assert result['status']=='completed' and result['source_matches'] and result['unconfirmed']==0, result
assert all(counts.get(event,0)>0 for event in ('SessionStart','UserPromptSubmit','PreToolUse','PostToolUse','Stop')), result
