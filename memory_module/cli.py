"""The installed Project Memory command."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import uuid
import webbrowser
from . import Memory, MemoryError, __version__
from . import codex_host
from .install import setup, uninstall


def database(args):
    if args.db:return Path(args.db).resolve()
    project=Path(args.project).resolve()
    state=project/'.memory/install.json'
    return Path(json.loads(state.read_text())['database']) if state.exists() else project/'.memory/project.sqlite'


def doctor(path):
    with Memory(path) as memory:
        integrity=memory.db.execute('PRAGMA integrity_check').fetchone()[0]
        if integrity!='ok':raise RuntimeError('SQLite integrity check failed: '+integrity)
        installed=codex_host.exists(memory)
        counts={r[0]:r[1] for r in memory.db.execute('SELECT event_name,count(*) FROM host_receipts GROUP BY event_name')} if installed else {}
        pending=codex_host.status(memory)['unconfirmed_total'] if installed else None
    requests=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'project-memory-doctor','version':__version__}}},
              {'jsonrpc':'2.0','id':2,'method':'tools/list','params':{}},
              {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'memory_get','arguments':{'view':'schema','id':'decision','max_chars':6000}}}]
    run=subprocess.run([sys.executable,'-m','memory_module.cli','serve','--db',str(path)],input=''.join(json.dumps(r)+'\n' for r in requests),text=True,capture_output=True,timeout=20)
    replies=[json.loads(line) for line in run.stdout.splitlines()]
    ok=run.returncode==0 and len(replies)==3 and all('result' in r for r in replies) and not replies[-1]['result'].get('isError')
    if not ok:raise RuntimeError('The installed MCP process failed its handshake or read. '+run.stderr[:500])
    return {'database':str(path),'integrity':integrity,'mcp_process_verified':ok,'observed_hooks':counts,
            'missing_hook_events':sorted(codex_host.EVENTS-counts.keys()),'unconfirmed_actions':pending,
            'note':'Receipt counts show past capture, not current configuration health. Missing lifecycle events may simply not have occurred.'}


def main(argv=None):
    parser=argparse.ArgumentParser(description='Keep project decisions, evidence and outcomes locally.')
    parser.add_argument('--version',action='version',version=__version__)
    sub=parser.add_subparsers(dest='command',required=True)
    for name in ['setup','serve','doctor','view','backup','uninstall']:
        p=sub.add_parser(name)
        p.add_argument('--project',default=os.environ.get('PROJECT_MEMORY_PROJECT',os.getcwd()))
        p.add_argument('--db')
        if name=='setup':
            p.add_argument('--client',choices=['mcp','codex'],default='mcp');p.add_argument('--trust',action='store_true')
            p.add_argument('--requirement',action='append');p.add_argument('--document',action='append',default=[])
        elif name=='view':
            p.add_argument('--output');p.add_argument('--no-open',action='store_true');p.add_argument('--include-bodies',action='store_true')
            p.add_argument('--episode');p.add_argument('--subject',choices=['code','writing','research','general'])
        elif name=='backup':p.add_argument('destination')
    hook=sub.add_parser('hook');hook.add_argument('--db',required=True)
    args=parser.parse_args(argv)
    try:
        if args.command=='setup':result=setup(args.project,client=args.client,database=args.db,requirements=args.requirement,documents=args.document,trust=args.trust)
        elif args.command=='uninstall':result=uninstall(args.project)
        elif args.command=='hook':
            old=sys.argv;sys.argv=['project-memory hook','--db',args.db]
            try:return codex_host.main()
            finally:sys.argv=old
        elif args.command=='doctor':result=doctor(database(args))
        else:
            with Memory(database(args)) as memory:
                if args.command=='serve':
                    if not codex_host.exists(memory):raise ValueError('Run project-memory setup in this project first.')
                    from .mcp import serve
                    serve(memory,sys.stdin,sys.stdout);return 0
                elif args.command=='backup':result=memory.backup(args.destination)
                else:
                    output=Path(args.output).resolve() if args.output else memory.path.parent/('view-'+uuid.uuid4().hex[:12]+'.html')
                    result=memory.export_html(output,episode_id=args.episode,subject=args.subject,include_bodies=args.include_bodies)
                    if not args.no_open:result['browser_open_requested']=webbrowser.open(output.as_uri())
        print(json.dumps(result,indent=2));return 0
    except (MemoryError,OSError,ValueError,sqlite3.Error,RuntimeError,subprocess.SubprocessError) as exc:
        print(f'Project Memory: {exc}',file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
