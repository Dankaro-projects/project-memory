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


# Commands that read an existing project. setup and init create one; hook stays silent without one.
NEEDS_DATABASE={'serve','doctor','view','backup','sync','check','review','instructions','sessions','handoff'}


def database(args):
    if args.db:return Path(args.db).resolve()
    state=install_state(args)
    return Path(state['database']) if state else Path(args.project).resolve()/'.memory/project.sqlite'


def require_database(args):
    """Refuse a command that needs a project, with a plain sentence instead of an operating system error."""
    path=database(args)
    if not path.exists():
        raise ValueError('No project records were found in this folder. Run project-memory setup here to add Project '
                         'Memory to an existing project, run project-memory init to create a project from a template, '
                         'or pass --db with the path of an existing database.')
    return path


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
    # The machine memory holds the usage ledger and the registry of projects, so a mode wider than its owner is reported.
    from .machine import permissions
    try: health['machine_permissions']=permissions()
    except OSError as exc: health['machine_permissions']={'status':'unverified','wider':[],'error':str(exc)}
    return {**health,'database':str(path),'integrity':integrity,'mcp_process_verified':ok,'client':client or 'unknown','clients':clients or [],
            'expected_hook_events':sorted(expected),'observed_hooks':counts,
            'missing_hook_events':sorted(expected-counts.keys()),'unconfirmed_actions':pending,
            'note':'Receipt counts show past capture, not current configuration health. Missing lifecycle events may simply not have occurred.'}


def write_instructions(path,output):
    """Write the instruction text composed for each role now, one Markdown file per role."""
    from . import guards
    folder=Path(output).resolve()
    folder.mkdir(parents=True,exist_ok=True)
    roles=[]
    with Memory(path,read_only=True) as memory:
        for role in guards.RULE_ROLES:
            value=guards.instructions(memory,role)
            target=folder/(role+'.md')
            target.write_text(value['text']+'\n',encoding='utf-8')
            roles.append({'role':role,'file':str(target),'characters':value['characters'],'base_source':value['base_source'],
                          'base_version':value['base_version'],'rule_ids':value['rule_ids'],'omitted':value['omitted'],
                          'budget':value['budget'],'used':value['used'],'accepted_rules':value['accepted_total']})
    return {'output':str(folder),'roles':roles,
            'note':'These files are a copy for reading and are replaced on the next run. The records in the database stay the source of truth, '
                   'and a rule with a path, keyword or failure type trigger is composed only into the run that matches it.'}


def open_viewer(result,browser=True):
    """Ask the browser to open the control panel, and keep its address out of the output inside an assistant session.

    The address carries the access key of the panel. An assistant that reads it
    could send the actions of the user to the panel, so the address is printed
    only in a terminal of the user.
    """
    from .live import assistant_session,URL_WITHHELD
    if browser:result['browser_open_requested']=webbrowser.open(result['url'])
    if assistant_session():
        result={k:v for k,v in result.items() if k!='url'}
        result['url_withheld']=URL_WITHHELD
    return result


def machine_command(args):
    """Create the memory of this machine, or read its registry and its rules.

    The machine memory holds the rules the user promoted out of single projects
    and the registry of the projects on this computer. Reading never creates it,
    and the registry stays on this computer.
    """
    from . import machine
    if args.action=='init':
        result=machine.initialize()
        path=database(args)
        if path.exists():
            with Memory(path,read_only=True) as memory:
                result['registered']=machine.register_project(memory)
        return result
    value=machine.overview(limit=args.limit)
    shared=['machine','database','exists','note']
    if not value['exists']:
        return {**{key:value[key] for key in shared},'projects':[],'rules':[],'retired':[]}
    if args.action=='list':
        return {**{key:value[key] for key in shared},'projects':value['projects'],'projects_total':value['projects_total']}
    rules=value['rules'] if not args.role else [rule for rule in value['rules'] if args.role in rule['roles']]
    return {**{key:value[key] for key in shared},'role':args.role,'rules':rules,'rules_total':len(rules),
            'retired':value['retired']}


PROBE_FROM_ASSISTANT=('project-memory host probe runs the real host and spends tokens, so it is a user action and does not run '
                     'from inside an assistant session. Run it in your own terminal.')


def host_probe(args):
    """Run the conformance probe of one host. Only the user starts it, because it runs the real host and spends tokens."""
    from .live import assistant_session
    from . import hosts
    if assistant_session():
        raise ValueError(PROBE_FROM_ASSISTANT)
    if not 30<=args.timeout<=1800:
        raise ValueError('The probe time limit must be between 30 and 1,800 seconds.')
    print(f'Project Memory: the probe runs the {args.name} host and spends tokens.',file=sys.stderr)
    return hosts.probe(args.name,timeout=args.timeout)


def main(argv=None):
    parser=argparse.ArgumentParser(description='Keep project decisions, evidence and outcomes locally.')
    parser.add_argument('--version',action='version',version=__version__)
    sub=parser.add_subparsers(dest='command',required=True)
    for name in ['setup','serve','doctor','view','backup','uninstall','sync','check','review','instructions']:
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
            p.add_argument('--session',default='',help='The session that requests the check. Its tool calls reach the reviewer as evidence about the requesting turn.')
            p.add_argument('--max-seconds',type=int,help='Execution limit; with --wait, legacy alias for --wait-seconds.')
            p.add_argument('--wait-seconds',type=int,help='Stop waiting after this many seconds without cancelling the review (default: 60).')
        elif name=='instructions':
            p.add_argument('--output',required=True,help='Folder that receives one Markdown file per role. The database stays the source of truth.')
        elif name=='backup':p.add_argument('destination')
    init=sub.add_parser('init',help='Create a project from a template: product, engagement or automation.')
    init.add_argument('path')
    init.add_argument('--template',required=True,choices=['product','engagement','automation'])
    init.add_argument('--client',action='append',choices=['mcp','codex','claude'],default=[])
    init.add_argument('--name')
    init.add_argument('--no-git',action='store_true')
    init.add_argument('--no-view',action='store_true')
    mach=sub.add_parser('machine',help='The memory of this machine: the rules you promoted and the registry of projects.')
    mach.add_argument('action',choices=['init','list','rules'],help='init creates the machine memory, list reads the registry, rules reads the promoted rules.')
    mach.add_argument('--project',default=os.environ.get('PROJECT_MEMORY_PROJECT',os.getcwd()))
    mach.add_argument('--db',help='The project database that init records in the registry.')
    mach.add_argument('--role',choices=['assistant','worker','reviewer'],help='Read the rules composed into the prompt of this role.')
    mach.add_argument('--limit',type=int,default=50)
    usage_parser=sub.add_parser('usage',help='Collect and show the usage of each agent host on this machine: tokens, cost and limit state.')
    usage_parser.add_argument('--json',action='store_true',help='Print the report as JSON instead of a table.')
    host_parser=sub.add_parser('host',help='Agent hosts on this machine. probe runs the real host, spends tokens and records what it may do.')
    host_parser.add_argument('action',choices=['probe'],help='probe runs the conformance checks of the process runner for one host.')
    host_parser.add_argument('name',choices=['codex','claude','grok','opencode'],metavar='HOST',help='codex, claude, grok or opencode.')
    host_parser.add_argument('--timeout',type=int,default=300,help='The time limit of each probe run in seconds, from 30 to 1,800 (default: 300).')
    hive_server=sub.add_parser('hive-serve',help='The restricted MCP server of a delegated worker: hive_log, hive_query and hive_resume for one swarm and agent.')
    hive_server.add_argument('--hive',required=True,help='The hive file, .memory/hive.sqlite beside the project database.')
    hive_server.add_argument('--swarm',required=True)
    hive_server.add_argument('--agent',required=True)
    hive_server.add_argument('--role',required=True)
    hive_server.add_argument('--db',help='The project database, used read only to verify command and source bases. Defaults to project.sqlite beside the hive.')
    hive_parser=sub.add_parser('hive',help='The shared record of agents that work together.')
    hive_parser.add_argument('action',choices=['purge'],help='purge removes whole swarms that closed before the given number of days.')
    hive_parser.add_argument('--closed-before',type=int,required=True,metavar='DAYS')
    hive_parser.add_argument('--project',default=os.environ.get('PROJECT_MEMORY_PROJECT',os.getcwd()))
    hive_parser.add_argument('--db')
    sessions_parser=sub.add_parser('sessions',help='Read the finished sessions of this project into digests, flags and proposals.')
    sessions_parser.add_argument('action',choices=['collect','list','flags','distill','on','off'],
        help='collect reads new transcript lines, list shows the digests, flags shows possible unrecorded directions, '
             'distill asks a host for proposals that you accept or reject in the control panel, on and off switch session reading.')
    sessions_parser.add_argument('--project',default=os.environ.get('PROJECT_MEMORY_PROJECT',os.getcwd()))
    sessions_parser.add_argument('--db')
    sessions_parser.add_argument('--session',help='The session key, such as claude:<session id>. The latest session by default.')
    sessions_parser.add_argument('--host',choices=['claude','codex'],help='The host that distills. The host with the most headroom by default.')
    sessions_parser.add_argument('--timeout',type=int,default=600,help='The time limit of a distillation in seconds (default: 600).')
    sessions_parser.add_argument('--hint-tokens',type=int,help='With on: the context size above which the prompt hook suggests a fresh session.')
    handoff_parser=sub.add_parser('handoff',help='Report whether this project is ready for a fresh session, or list what is not yet recorded.')
    handoff_parser.add_argument('--project',default=os.environ.get('PROJECT_MEMORY_PROJECT',os.getcwd()))
    handoff_parser.add_argument('--db')
    handoff_parser.add_argument('--session',help='The session key to check. The latest session by default.')
    hook=sub.add_parser('hook');hook.add_argument('--db');hook.add_argument('--host',choices=sorted(codex_host.HOSTS),default='codex')
    hook.add_argument('--if-unmanaged',action='store_true')
    hook.add_argument('--project',default=os.environ.get('PROJECT_MEMORY_PROJECT',os.getcwd()))
    args=parser.parse_args(argv)
    try:
        if args.command in NEEDS_DATABASE:
            require_database(args)
        if args.command=='setup':
            result=setup(args.project,client=args.client,database=args.db,requirements=args.requirement,documents=args.document,trust=args.trust)
            if not args.no_view:
                from .live import start
                result['viewer']=open_viewer(start(result['database']))
        elif args.command=='init':
            from .templates import scaffold
            result=scaffold(args.path,args.template,name=args.name,clients=args.client,git=not args.no_git)
            if not args.no_view:
                from .live import start
                result['viewer']=open_viewer(start(result['database']))
        elif args.command=='uninstall':result=uninstall(args.project,args.client)
        elif args.command=='machine':result=machine_command(args)
        elif args.command=='usage':
            from . import usage
            result=usage.report()
            if not args.json:
                print(usage.render(result),end='')
                return 0
        elif args.command=='host':
            result=host_probe(args)
        elif args.command=='hive-serve':
            from .mcp import serve_hive
            path=Path(args.hive).resolve()
            db=Path(args.db).resolve() if args.db else path.parent/'project.sqlite'
            serve_hive(path,args.swarm,args.agent,args.role,sys.stdin,sys.stdout,db=db if db.exists() else None)
            return 0
        elif args.command=='hive':
            from .hive import Hive, path_for, purge
            from .core import USER_ACTOR
            with Memory(require_database(args)) as memory:
                path=path_for(memory)
                with Hive(path,read_only=not path.exists()) as store:
                    # A purge from the terminal is an action of the user.
                    result=purge(store,memory,closed_before_days=args.closed_before,actor=USER_ACTOR)
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
                    result=reviews.request(memory,args.episode,args.role,request_key='cli:'+uuid.uuid4().hex,session_id=args.session,retry=args.retry,max_seconds=args.max_seconds if args.max_seconds is not None else 300)
                    reviews.launch(memory,result)
                result.pop('snapshot',None)
        elif args.command in {'sessions','handoff'}:
            from . import sessions
            with Memory(database(args)) as memory:
                if args.command=='handoff':result=sessions.handoff(memory,session_key=args.session)
                elif args.action=='collect':result=sessions.collect(memory)
                elif args.action=='list':sessions.collect(memory);result={'sessions':sessions.digests(memory),'flags':sessions.precision(memory)}
                elif args.action=='flags':result={'flags':sessions.flags(memory,session_key=args.session,status='open'),'counts':sessions.precision(memory)}
                elif args.action=='distill':result=sessions.distill(memory,session_key=args.session,host=args.host,timeout=args.timeout)
                else:result=sessions.configure(memory,reading=args.action=='on',hint_tokens=args.hint_tokens if args.action=='on' else None)
        elif args.command=='instructions':
            result=write_instructions(database(args),args.output)
        elif args.command=='view' and not args.output:
            if args.include_bodies or args.replace:raise ValueError('--include-bodies and --replace require --output for a snapshot.')
            from .live import start
            from urllib.parse import urlencode
            result=start(database(args))
            filters={k:v for k,v in {'episode':args.episode,'subject':args.subject}.items() if v}
            if filters:result['url']+='?'+urlencode(filters)
            result=open_viewer(result,browser=not args.no_open)
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
