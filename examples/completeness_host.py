"""Bounded actual Codex/Claude omission case; keeps private host transcripts local."""
import argparse
import json
from pathlib import Path
import subprocess
import time
import uuid
from examples.codex_app_client import CodexClient
from memory_module import Memory
from memory_module.install import setup
from memory_module.coverage import inspect

PROMPT='Read ANSWER.txt once and tell me its exact sentence. This is an informational lookup, not an implementation task. Do not create an episode. After the lookup, assess any recording gap reported by the Project Memory hook using the available MCP tools. Keep the final answer to one sentence.'


def main():
    p=argparse.ArgumentParser();p.add_argument('--project',type=Path,required=True);p.add_argument('--host',choices=['codex','claude'],required=True);p.add_argument('--omit-initial',action='store_true');a=p.parse_args()
    root=a.project.resolve();root.mkdir(parents=True,exist_ok=False);(root/'ANSWER.txt').write_text('The tagged legacy exception remains required.\n')
    info=setup(root,client=a.host,trust=True,requirements=['Preserve the tagged legacy exception.'])
    started=time.monotonic();session=str(uuid.uuid4());error=None;usage=[]
    prompt=PROMPT
    if a.omit_initial:prompt+=' For this omission test, give the initial answer without calling memory tools. If the Stop hook then reports a recording gap, repair that gap using MCP and do not reread ANSWER.txt.'
    if a.host=='codex':
        client=CodexClient(root,root/'.memory/host.jsonl')
        try:
            session=client.start();turn=client.turn(session,prompt);result=client.complete(turn,timeout=120)
            usage=[v['params'] for v in client.events if v.get('method')=='thread/tokenUsage/updated']
            status=result['status']
        except (TimeoutError,RuntimeError) as exc:error=str(exc);status='incomplete'
        finally:client.close()
    else:
        cmd=['claude','-p','--session-id',session,'--output-format','json','--permission-mode','dontAsk','--setting-sources','project,local','--tools','Read','--allowedTools','Read,mcp__project_memory__memory_get,mcp__project_memory__memory_write,mcp__project_memory__memory_context','--strict-mcp-config','--mcp-config',str(root/'.mcp.json'),'--disable-slash-commands',prompt]
        try:
            run=subprocess.run(cmd,cwd=root,capture_output=True,text=True,timeout=120)
            (root/'.memory/host-output.json').write_text(run.stdout);(root/'.memory/host-stderr.log').write_text(run.stderr)
            result=json.loads(run.stdout);usage=result.get('usage');status=result.get('subtype',str(run.returncode))
        except (subprocess.TimeoutExpired,ValueError) as exc:error=type(exc).__name__;status='incomplete'
    with Memory(info['database']) as m:
        state=inspect(m,session)
        counts=dict(m.db.execute('SELECT event_name,count(*) FROM host_receipts GROUP BY event_name').fetchall())
        report={'host':a.host,'status':status,'error':error,'duration_ms':round((time.monotonic()-started)*1000),'session_id':session,'coverage':state,'hooks':counts,'episodes':m.db.execute('SELECT count(*) FROM episodes').fetchone()[0],'provider_usage':usage}
    (root/'.memory/result.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))


if __name__=='__main__':main()
