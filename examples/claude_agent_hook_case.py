"""Run a real Claude Stop hook and wait for its separate read-only reviewer."""
import argparse
import json
from pathlib import Path
import subprocess
import time
import uuid
from examples.agent_check_case import seed
from memory_module import Memory, codex_host, reviews
from memory_module.install import setup
from memory_module.planning import latest

PROMPT='Read SCOPE.md and parser.py. State whether the legacy exception exists. Do not modify files or records. A decision and claimed complete outcome are already bound to this session. Let the project Stop hook request the independent check; do not repeat implementation work.'


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--project',type=Path,required=True);args=parser.parse_args()
    project=args.project.resolve();info=seed(project);setup(project,client='claude',trust=True)
    case=json.loads((project/'.memory/case.json').read_text());session=str(uuid.uuid4())
    with Memory(info['database']) as memory:codex_host.bind(memory,session,latest(memory,case['episode_id'],'decision')['id'],'claude-host-bind')
    (project/'.memory/host-session.json').write_text(json.dumps({'session_id':session}))
    with (project/'.memory/host-output.json').open('w') as out,(project/'.memory/host-stderr.log').open('w') as err:
        result=subprocess.run(['claude','-p','--session-id',session,'--output-format','json','--permission-mode','dontAsk',
            '--setting-sources','project,local','--tools','Read,Glob,Grep','--allowedTools','Read,Glob,Grep',
            '--strict-mcp-config','--mcp-config','{"mcpServers":{}}','--disable-slash-commands',PROMPT],
            cwd=project,stdout=out,stderr=err,timeout=180)
    deadline=time.monotonic()+150
    with Memory(info['database']) as memory:
        while True:
            runs=reviews.listing(memory,case['episode_id'])['runs']
            if not runs or all(r['state'] not in reviews.ACTIVE for r in runs) or time.monotonic()>deadline:break
            time.sleep(.5)
        report={'host_exit_code':result.returncode,'checks':[{'role':r['role'],'state':r['state'],'metrics':r['metrics']} for r in runs],
                'hooks':[dict(r) for r in memory.db.execute('SELECT event_name,count(*) AS count FROM host_receipts GROUP BY event_name')]}
    (project/'.memory/host-result.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))


if __name__=='__main__':main()
