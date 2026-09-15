"""The installed Project Memory command."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import webbrowser
from . import Memory, MemoryError, __version__
from . import codex_host
from .install import setup, uninstall, python_args, project_state


def install_state(args):
    """Return the ownership record in the per client format, or an empty dictionary."""
    return project_state(args.project)


def database(args):
    if args.db:return Path(args.db).resolve()
    state=install_state(args)
    return Path(state['database']) if state else Path(args.project).resolve()/'.memory/project.sqlite'


def doctor(path, client=None, project=None, clients=None):
    """Verify the database and MCP process, and compare receipts with the configured clients."""
    from .health import expected_hook_events
    if clients is None and client:
        clients=[client]
    clients=sorted(clients) if clients is not None else None
    expected=expected_hook_events(clients,client)
    with Memory(path) as memory:
        integrity=memory.db.execute('PRAGMA integrity_check').fetchone()[0]
        if integrity!='ok':raise RuntimeError('SQLite integrity check failed: '+integrity)
        from .health import inspect
        health=inspect(memory)
        installed=codex_host.exists(memory)
        counts={r[0]:r[1] for r in memory.db.execute('SELECT event_name,count(*) FROM host_receipts GROUP BY event_name')} if installed else {}
        pending=codex_host.status(memory)['unconfirmed_total'] if installed else None
    requests=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'project-memory-doctor','version':__version__}}},
              {'jsonrpc':'2.0','id':2,'method':'tools/list','params':{}},
              {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'memory_get','arguments':{'view':'schema','id':'decision','max_chars':6000}}}]
    run=subprocess.run(python_args('memory_module.cli')+['serve','--db',str(path)],input=''.join(json.dumps(r)+'\n' for r in requests),text=True,encoding='utf-8',capture_output=True,timeout=20)
    replies=[json.loads(line) for line in run.stdout.splitlines()]
    ok=run.returncode==0 and len(replies)==3 and all('result' in r for r in replies) and not replies[-1]['result'].get('isError')
    if not ok:raise RuntimeError('The installed MCP process failed its handshake or read. '+run.stderr[:500])
    if 'codex' in (clients or []) and project:
        from .health import codex_hooks
        try: health['host_discovery']=codex_hooks(project)
        except (OSError,ValueError,RuntimeError,TimeoutError) as exc:
            health['host_discovery']={'status':'unverified','error':str(exc)}
    return {**health,'database':str(path),'integrity':integrity,'mcp_process_verified':ok,'client':client or 'unknown','clients':clients or [],
            'expected_hook_events':sorted(expected),'observed_hooks':counts,
            'missing_hook_events':sorted(expected-counts.keys()),'unconfirmed_actions':pending,
            'note':'Receipt counts show past capture, not current configuration health. Missing lifecycle events may simply not have occurred.'}


def main(argv=None):
    parser=argparse.ArgumentParser(description='Keep project decisions, evidence and outcomes locally.')
    parser.add_argument('--version',action='version',version=__version__)
    sub=parser.add_subparsers(dest='command',required=True)
    for name in ['setup','serve','doctor','view','backup','uninstall','sync','check','review']:
        p=sub.add_parser(name)
        p.add_argument('--project',default=os.environ.get('PROJECT_MEMORY_PROJECT',os.getcwd()))
        p.add_argument('--db')
        if name=='setup':
            p.add_argument('--client',choices=['mcp','codex','claude'],default='mcp');p.add_argument('--trust',action='store_true')
            p.add_argument('--no-view',action='store_true');p.add_argument('--requirement',action='append');p.add_argument('--document',action='append',default=[])
        elif name=='view':
            p.add_argument('--output');p.add_argument('--no-open',action='store_true');p.add_argument('--include-bodies',action='store_true')
            p.add_argument('--replace',action='store_true');p.add_argument('--episode');p.add_argument('--subject',choices=['code','writing','research','general'])
        elif name in {'sync','check'}:
            p.add_argument('--limit',type=int,default=100);p.add_argument('--offset',type=int,default=0)
        elif name=='uninstall':
            p.add_argument('--client',choices=['mcp','codex','claude'],help='Remove only this client. Without it, every client is removed.')
        elif name=='review':
            p.add_argument('--wait');p.add_argument('--episode');p.add_argument('--role',choices=['outcome','intent','recovery'],default='outcome')
            p.add_argument('--cancel');p.add_argument('--retry',action='store_true')
            p.add_argument('--max-seconds',type=int,help='Execution limit; with --wait, legacy alias for --wait-seconds.')
            p.add_argument('--wait-seconds',type=int,help='Stop waiting after this many seconds without cancelling the review (default: 60).')
        elif name=='backup':p.add_argument('destination')
    hook=sub.add_parser('hook');hook.add_argument('--db');hook.add_argument('--host',choices=sorted(codex_host.HOSTS),default='codex')
    hook.add_argument('--if-unmanaged',action='store_true')
    hook.add_argument('--project',default=os.environ.get('PROJECT_MEMORY_PROJECT',os.getcwd()))
    args=parser.parse_args(argv)
    try:
        if args.command=='setup':
            result=setup(args.project,client=args.client,database=args.db,requirements=args.requirement,documents=args.document,trust=args.trust)
            if not args.no_view:
                from .live import start
                result['viewer']=start(result['database'])
                result['viewer']['browser_open_requested']=webbrowser.open(result['viewer']['url'])
        elif args.command=='uninstall':result=uninstall(args.project,args.client)
        elif args.command=='hook':
            if args.if_unmanaged and install_state(args).get('clients',{}).get(args.host,{}).get('hook_command'):
                print('{}');return 0
            path=database(args)
            if not args.db and not path.exists():
                # A plugin hook runs in every project. Without a configured database there is nothing to capture.
                print('{}');return 0
            old=sys.argv;sys.argv=['project-memory hook','--db',str(path),'--host',args.host]
            try:return codex_host.main()
            finally:sys.argv=old
        elif args.command=='doctor':
            state=install_state(args)
            result=doctor(database(args),state.get('client'),args.project,sorted(state['clients']) if state else None)
        elif args.command=='review':
            from . import reviews
            import uuid
            if sum(bool(x) for x in (args.cancel,args.wait,args.episode))!=1:
                raise ValueError('Select exactly one of --episode, --wait or --cancel.')
            if args.wait_seconds is not None and not args.wait:
                raise ValueError('--wait-seconds requires --wait.')
            if args.wait and args.wait_seconds is not None and args.max_seconds is not None:
                raise ValueError('Use --wait-seconds alone when waiting.')
            if args.cancel and (args.max_seconds is not None or args.retry):
                raise ValueError('Cancellation does not accept execution limits or retry.')
            if args.wait and args.retry:
                raise ValueError('--retry starts a review with --episode; it cannot be used with --wait.')
            with Memory(database(args)) as memory:
                if args.cancel:result=reviews.cancel(memory,args.cancel)
                elif args.wait:
                    seconds=args.wait_seconds if args.wait_seconds is not None else args.max_seconds if args.max_seconds is not None else 60
                    result=reviews.wait(memory,args.wait,seconds)
                else:
                    if not args.episode:raise ValueError('Select --episode, --wait or --cancel.')
                    result=reviews.request(memory,args.episode,args.role,request_key='cli:'+uuid.uuid4().hex,retry=args.retry,max_seconds=args.max_seconds if args.max_seconds is not None else 300)
                    reviews.launch(memory,result)
                result.pop('snapshot',None)
        elif args.command=='view' and not args.output:
            if args.include_bodies or args.replace:raise ValueError('--include-bodies and --replace require --output for a snapshot.')
            from .live import start
            from urllib.parse import urlencode
            result=start(database(args))
            filters={k:v for k,v in {'episode':args.episode,'subject':args.subject}.items() if v}
            if filters:result['url']+='?'+urlencode(filters)
            if not args.no_open:result['browser_open_requested']=webbrowser.open(result['url'])
        else:
            with Memory(database(args)) as memory:
                if args.command=='serve':
                    if not codex_host.exists(memory):raise ValueError('Run project-memory setup in this project first.')
                    from .mcp import serve
                    serve(memory,sys.stdin,sys.stdout);return 0
                elif args.command=='backup':result=memory.backup(args.destination)
                elif args.command in {'sync','check'}:
                    from .documents import sync
                    result=sync(memory,limit=args.limit,offset=args.offset,check=args.command=='check')
                else:
                    output=Path(args.output).resolve()
                    result=memory.export_html(output,episode_id=args.episode,subject=args.subject,include_bodies=args.include_bodies,replace=args.replace)
                    if not args.no_open:result['browser_open_requested']=webbrowser.open(output.as_uri())
        print(json.dumps(result,indent=2));return 0
    except (MemoryError,OSError,ValueError,sqlite3.Error,RuntimeError,subprocess.SubprocessError) as exc:
        print(f'Project Memory: {exc}',file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
