"""Exercise a real Codex Stop hook, its review worker and optional interruption."""
import argparse
import json
from pathlib import Path
import queue
import time

from memory_module import Memory
from memory_module import codex_host, reviews
from memory_module.install import setup
from memory_module.planning import latest
from .agent_check_case import seed
from .codex_app_client import CodexClient


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--project',required=True);parser.add_argument('--interrupt',action='store_true')
    args=parser.parse_args();project=Path(args.project).resolve();seed(project)
    setup(project,client='codex',trust=True)
    case=json.loads((project/'.memory/case.json').read_text())
    client=CodexClient(project,project/'.memory/host.jsonl');interrupted=False;completed=None
    try:
        thread=client.request('thread/start',{'cwd':str(project),'model':client.model,'approvalPolicy':'never','sandbox':'read-only',
            'developerInstructions':'Inspect only this invented local fixture. Do not delegate, browse, edit files or change memory records. Host hooks may independently check the work. Use the native memory tools to inspect results when needed.'})['thread']['id']
        with Memory(case['database']) as memory:codex_host.bind(memory,thread,latest(memory,case['episode_id'],'decision')['id'],'live-bind')
        turn=client.turn(thread,'The recorded parser work is claimed complete. Briefly report its recorded assessment and whether an independent check supports it. Do not repair the fixture or change its records. Work episode: '+case['episode_id'])
        deadline=time.monotonic()+300
        while time.monotonic()<deadline:
            try:
                event=client.receive(timeout=1)
                if event.get('method')=='turn/completed' and event['params']['turn']['id']==turn:
                    completed=event['params']['turn'];break
            except queue.Empty:pass
            if args.interrupt and not interrupted:
                with Memory(case['database']) as memory:
                    rows=memory.db.execute("SELECT id FROM review_runs WHERE session_id=? AND state='running'",(thread,)).fetchall()
                if rows:
                    client.request('turn/interrupt',{'threadId':thread,'turnId':turn});interrupted=True
        result={'thread_id':thread,'turn_id':turn,'interruption_requested':interrupted,'turn':completed,'timed_out':completed is None}
        with Memory(case['database']) as memory:
            result['checks']=reviews.listing(memory,case['episode_id'])
            result['hooks']=[dict(r) for r in memory.db.execute('SELECT event_name,count(*) AS count FROM host_receipts GROUP BY event_name')]
        (project/'.memory/host-result.json').write_text(json.dumps(result,indent=2))
        print(json.dumps(result,indent=2))
    finally:client.close()


if __name__=='__main__':main()
