"""Project-scoped installation with recoverable, ownership-checked configuration."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import tomllib
import uuid
from . import Memory, __version__
from .core import Conflict
from .codex_host import initialize, EVENTS, HOST_EVENTS

BEGIN = '# BEGIN project-memory managed configuration\n'
END = '# END project-memory managed configuration\n'
RELEASE_SOURCE = f'https://github.com/Dankaro-projects/project-memory/releases/download/v{__version__}/project_memory_mcp-{__version__}-py3-none-any.whl'
CLIENTS = {'mcp', 'codex', 'claude'}
SERVER = 'project_memory'
SOURCE_ROOT = Path(__file__).resolve().parent.parent


def atomic(path, content):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(prefix='.'+path.name+'.',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8',newline='') as out:
            out.write(content);out.flush();os.fsync(out.fileno())
        os.replace(temp,path)
    finally:
        if os.path.exists(temp):os.unlink(temp)


def launcher():
    uv=shutil.which('uv')
    if uv and (SOURCE_ROOT/'pyproject.toml').exists() and (SOURCE_ROOT/'.venv').exists():
        # A source checkout runs its own environment, so unreleased changes can be exercised in a real host.
        return [uv,'run','--project',str(SOURCE_ROOT),'project-memory']
    if uv:
        # The host retains a stable launcher, never an interpreter in uvx's cache.
        return [uv,'tool','run','--from',RELEASE_SOURCE,'project-memory']
    if any(part.startswith('archive-v') for part in Path(sys.executable).parts):
        raise Conflict('Install uv or install project-memory-mcp in a persistent environment before setting up Codex.')
    return [sys.executable,'-m','memory_module.cli']


def paths(project):
    project=Path(project).resolve()
    if not project.is_dir():raise ValueError('Project directory does not exist.')
    return project,project/'.memory/install.json',project/'.codex/config.toml',project/'.codex/hooks.json'


def claude_paths(project):
    # Claude Code reads project MCP servers from .mcp.json and hooks from project settings.
    # The local settings file stays out of version control, which suits machine-specific launch paths.
    return project/'.mcp.json',project/'.claude/settings.local.json'


def load_json(path, default):
    if not path.exists():return default
    value=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value,dict):raise ValueError(f'{path.name} must contain a JSON object.')
    return value


def load_settings(path):
    """Claude Code settings hold hooks beside other keys. Validate only the hooks section."""
    value=load_json(path,{})
    hooks=value.setdefault('hooks',{})
    if not isinstance(hooks,dict):raise ValueError('Existing hooks setting is invalid.')
    load_hooks_value({'hooks':hooks})
    return value


def hook_groups(command, events):
    groups={}
    for event in sorted(events):
        group={'hooks':[{'type':'command','command':command,'timeout':3 if event in {'Interrupt','SessionEnd'} else 10}]}
        if event in {'PreToolUse','PostToolUse','PostToolUseFailure'}:group['matcher']='.*'
        groups[event]=group
    return groups


def remove_block(text, block):
    if BEGIN not in text and END not in text:return text
    if text.count(BEGIN)!=1 or text.count(END)!=1 or block not in text:
        raise Conflict('The managed MCP configuration changed. Restore its recorded block before running setup or uninstall.')
    return text.replace(block,'',1)


def load_hooks(path):
    return load_hooks_value(load_json(path,{'hooks':{}}))


def load_hooks_value(value):
    if not isinstance(value,dict) or not isinstance(value.get('hooks'),dict):raise ValueError('Existing hooks file is invalid.')
    for groups in value['hooks'].values():
        if not isinstance(groups,list):raise ValueError('Existing hook groups must be lists.')
        for group in groups:
            if not isinstance(group,dict) or not isinstance(group.get('hooks'),list):raise ValueError('Existing hook group is invalid.')
            if any(not isinstance(h,dict) for h in group['hooks']):raise ValueError('Existing hook command is invalid.')
    return value


def remove_hooks(value, command):
    for event,groups in list(value['hooks'].items()):
        kept=[]
        for group in groups:
            items=[h for h in group['hooks'] if not (h.get('type')=='command' and h.get('command')==command)]
            if items:kept.append({**group,'hooks':items})
        if kept:value['hooks'][event]=kept
        else:value['hooks'].pop(event)
    return value


def setup(project, *, client='mcp', database=None, requirements=None, documents=(), trust=False, _launcher=None):
    project,state_path,config,hooks_path=paths(project)
    if client not in CLIENTS:raise ValueError('Client must be mcp, codex or claude.')
    if trust and client=='mcp':raise ValueError('--trust requires --client codex or --client claude.')
    state=json.loads(state_path.read_text()) if state_path.exists() else None
    database=Path(database).resolve() if database else project/'.memory/project.sqlite'
    if state and state['database']!=str(database):raise Conflict('This project is connected to another database. Uninstall the connection before changing it.')
    original=config.read_text(encoding='utf-8') if config.exists() else ''
    plain=original
    if state:
        candidates=[state.get('block',''),state.get('previous_block','')]
        matched=next((b for b in candidates if b and b in original),None)
        if matched:plain=remove_block(original,matched)
        elif BEGIN in original or END in original:raise Conflict('The managed MCP block has changed.')
    parsed=tomllib.loads(plain)
    hooks=load_hooks(hooks_path)
    mcp_path,settings_path=claude_paths(project)
    mcp_config=load_json(mcp_path,{});settings=load_settings(settings_path)
    servers=mcp_config.setdefault('mcpServers',{})
    if not isinstance(servers,dict):raise ValueError('Existing .mcp.json mcpServers must be an object.')
    if state:
        for owned in [state.get('hook_command'),state.get('previous_hook_command')]:
            if owned:remove_hooks(hooks,owned);remove_hooks(settings,owned)
        for owned in [state.get('server_entry'),state.get('previous_server_entry')]:
            if owned and servers.get(SERVER)==owned:servers.pop(SERVER)
    block='';command='';server_entry=None
    if client=='claude':
        if SERVER in servers:raise Conflict('An unmanaged project_memory MCP server already exists in .mcp.json.')
        launch=_launcher or launcher()
        command=(subprocess.list2cmdline if os.name=='nt' else shlex.join)(launch+['hook','--db',str(database),'--host','claude'])
        server_entry={'command':launch[0],'args':launch[1:]+['serve','--db',str(database)]}
        servers[SERVER]=server_entry
        # Claude Code raises PostToolUseFailure instead of a second PostToolUse; both close the same tool receipt.
        for event,group in hook_groups(command,HOST_EVENTS['claude']|{'PostToolUseFailure'}).items():
            settings['hooks'].setdefault(event,[]).append(group)
        if trust:
            enabled=settings.setdefault('enabledMcpjsonServers',[])
            if not isinstance(enabled,list):raise ValueError('Existing enabledMcpjsonServers setting is invalid.')
            if SERVER not in enabled:enabled.append(SERVER)
    if client=='codex':
        legacy=parsed.get('mcp_servers',{}).get('memory',{})
        if 'memory_module.mcp' in legacy.get('args',[]):
            raise Conflict('This project still uses the legacy memory-module adapter. Disable its MCP server and hooks before connecting Project Memory; its database can be reused.')
        if 'hooks' in parsed:raise Conflict('Inline Codex hooks take precedence. Move them to hooks.json before installing project hooks.')
        if 'project_memory' in parsed.get('mcp_servers',{}):raise Conflict('An unmanaged project_memory MCP server already exists.')
        launch=_launcher or launcher()
        command=(subprocess.list2cmdline if os.name=='nt' else shlex.join)(launch+['hook','--db',str(database)])
        block=BEGIN+'[mcp_servers.project_memory]\ncommand = '+json.dumps(launch[0])+'\nargs = '+json.dumps(launch[1:]+['serve','--db',str(database)])+'\ncwd = '+json.dumps(str(project))+'\ndefault_tools_approval_mode = "approve"\n'+END
        for event,group in hook_groups(command,EVENTS).items():
            hooks['hooks'].setdefault(event,[]).append(group)
    config_text=plain+('\n' if plain and not plain.endswith('\n') else '')+block
    tomllib.loads(config_text)
    private=project/'.memory';private.mkdir(exist_ok=True)
    ignore=private/'.gitignore'
    if not ignore.exists():atomic(ignore,'*\n')
    database.parent.mkdir(parents=True,exist_ok=True)
    if not database.exists():
        with Memory.create(database,project.name,requirements or ['No project-specific requirements have been approved. Obtain agreement before making material project decisions.']):pass
    elif requirements:
        with Memory(database) as memory:
            if memory.requirements!=requirements:raise Conflict('Existing requirements differ. Approve an explicit direction revision instead of changing setup defaults.')
    backups=[]
    with Memory(database) as memory:
        if not state:
            backup_dir=project/'.memory/backups'/uuid.uuid4().hex
            backup_dir.mkdir(parents=True)
            memory.backup(backup_dir/'project.sqlite')
            for path in (config,hooks_path,mcp_path,settings_path):
                if path.exists():shutil.copy2(path,backup_dir/path.name)
            backups=[str(backup_dir)]
        initialize(memory)
        from .health import inspect
        health=inspect(memory)
        captured=[memory.document(str(Path(doc).resolve()))['id'] for doc in documents]
    # Persist ownership first: a retry can finish after either configuration write.
    new_state={'version':__version__,'phase':'pending','database':str(database),'client':client,'block':block,
               'hook_command':command,'server_entry':server_entry,'previous_block':(state or {}).get('block',''),
               'previous_hook_command':(state or {}).get('hook_command',''),'previous_server_entry':(state or {}).get('server_entry'),
               'backups':(state or {}).get('backups',[])+backups}
    atomic(state_path,json.dumps(new_state,indent=2)+'\n')
    if client=='codex' or state and state.get('block'):
        atomic(hooks_path,json.dumps(hooks,indent=2)+'\n');atomic(config,config_text)
    if client=='claude' or state and state.get('server_entry'):
        atomic(mcp_path,json.dumps(mcp_config,indent=2)+'\n');atomic(settings_path,json.dumps(settings,indent=2)+'\n')
    new_state['phase']='installed'
    for key in ('previous_block','previous_hook_command','previous_server_entry'):new_state.pop(key)
    atomic(state_path,json.dumps(new_state,indent=2)+'\n')
    capture={'codex':'Configured only. Run a new Codex task and inspect doctor for actual receipts.',
             'claude':'Configured only. Start a new Claude Code session in this project, approve the project MCP server if asked, and inspect doctor for actual receipts.',
             'mcp':'Explicit MCP capture only; this client has no automatic hooks.'}[client]
    result={'project':str(project),'database':str(database),'client':client,'documents':captured,'phase':'installed','capture':capture,'baseline':health['baseline'],
            'viewer':{'available':True,'command':'project-memory view','mode':'live; read-only; local'}}
    if trust and client=='codex':
        from .setup_codex import trust_project_hooks
        result['trust']=trust_project_hooks(project,database,command)
    elif trust:
        result['trust']={'enabled_mcpjson_servers':[SERVER],'settings':str(settings_path),
                         'note':'Claude Code runs project hooks from its settings files without a separate trust step. The MCP server is pre-approved in the local settings.'}
    return result


def uninstall(project):
    project,state_path,config,hooks_path=paths(project)
    if not state_path.exists():return {'removed':False,'reason':'No managed installation exists.'}
    state=json.loads(state_path.read_text())
    original=config.read_text(encoding='utf-8') if config.exists() else ''
    plain=remove_block(original,state['block']) if state.get('block') else original
    hooks=remove_hooks(load_hooks(hooks_path),state['hook_command']) if state.get('hook_command') else None
    if state.get('block'):
        atomic(config,plain)
        atomic(hooks_path,json.dumps(hooks,indent=2)+'\n')
    if state.get('server_entry'):
        mcp_path,settings_path=claude_paths(project)
        mcp_config=load_json(mcp_path,{});servers=mcp_config.get('mcpServers',{})
        if isinstance(servers,dict) and servers.get(SERVER)==state['server_entry']:servers.pop(SERVER)
        settings=remove_hooks(load_settings(settings_path),state['hook_command'])
        enabled=settings.get('enabledMcpjsonServers')
        if isinstance(enabled,list) and SERVER in enabled:enabled.remove(SERVER)
        if not settings['hooks']:settings.pop('hooks')
        atomic(mcp_path,json.dumps(mcp_config,indent=2)+'\n');atomic(settings_path,json.dumps(settings,indent=2)+'\n')
    state_path.unlink()
    return {'removed':True,'database_preserved':state['database'],
            'note':'Project records and backups remain. Codex may retain inactive trust hashes for the removed commands.'}
