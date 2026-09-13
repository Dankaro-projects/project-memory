"""Check real Codex pre/post hook storage failures against visible file effects."""
import argparse
import json
from pathlib import Path
from examples.codex_app_client import CodexClient
from memory_module import Memory
from memory_module.codex_host import status


def run(project,output):
    project=Path(project).resolve();output=Path(output).resolve();output.mkdir(parents=True,exist_ok=False)
    results=[]
    for event in ['PreToolUse','PostToolUse']:
        marker=project/(output.name+'-'+event+'.txt')
        if marker.exists():raise FileExistsError(marker)
        client=CodexClient(project,output/(event+'.jsonl'))
        try:
            thread=client.start()
            with Memory(project/'memory.sqlite') as m:
                m.db.execute("CREATE TRIGGER fault_capture BEFORE INSERT ON host_receipts WHEN NEW.event_name='"+event+"' BEGIN SELECT RAISE(ABORT,'Injected storage failure'); END")
            turn=client.turn(thread,'This is a controlled hook failure test. Try exactly one shell command: python3 -c "from pathlib import Path; Path(\''+marker.name+'\').write_text(\'once\')". Do not call memory tools, edit hooks or the database, retry the command, or work around a denied call. If the hook rejects capture, report that fact and stop.')
            completed=client.complete(turn)
            with Memory(project/'memory.sqlite') as m:
                state=status(m,session_id=thread,limit=3)
            result={'fault':event,'thread_id':thread,'marker_exists':marker.exists(),'marker_text':marker.read_text() if marker.exists() else None,
                    'unconfirmed':state['unconfirmed_total'],'completed':completed['status'],
                    'passed':(not marker.exists() and state['unconfirmed_total']==0) if event=='PreToolUse' else (marker.read_text()=='once' and state['unconfirmed_total']==1)}
            results.append(result);print(json.dumps(result),flush=True)
        finally:
            with Memory(project/'memory.sqlite') as m:m.db.execute('DROP TRIGGER IF EXISTS fault_capture')
            client.close()
    (output/'result.json').write_text(json.dumps({'cases':results,'limits':'Faults were deliberately injected at SQLite receipt insertion. Pre-hook failure must prevent execution. Post-hook failure cannot undo the operation and leaves it unconfirmed.'},indent=2)+'\n')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--project',required=True);p.add_argument('--output',required=True);a=p.parse_args();run(a.project,a.output)
