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
from .codex_host import initialize, EVENTS

BEGIN = '# BEGIN project-memory managed configuration\n'
END = '# END project-memory managed configuration\n'
RELEASE_SOURCE = f'https://github.com/Dankaro-projects/project-memory/releases/download/v{__version__}/project_memory_mcp-{__version__}-py3-none-any.whl'


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


def remove_block(text, block):
    if BEGIN not in text and END not in text:return text
    if text.count(BEGIN)!=1 or text.count(END)!=1 or block not in text:
        raise Conflict('The managed MCP configuration changed. Restore its recorded block before running setup or uninstall.')
    return text.replace(block,'',1)


def load_hooks(path):
    value=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'hooks':{}}
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
    if client not in {'mcp','codex'}:raise ValueError('Client must be mcp or codex.')
    if trust and client!='codex':raise ValueError('--trust requires --client codex.')
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
    if state:
        for owned in [state.get('hook_command'),state.get('previous_hook_command')]:
            if owned:remove_hooks(hooks,owned)
    block='';command=''
    if client=='codex':
        legacy=parsed.get('mcp_servers',{}).get('memory',{})
        if 'memory_module.mcp' in legacy.get('args',[]):
            raise Conflict('This project still uses the legacy memory-module adapter. Disable its MCP server and hooks before connecting Project Memory; its database can be reused.')
        if 'hooks' in parsed:raise Conflict('Inline Codex hooks take precedence. Move them to hooks.json before installing project hooks.')
        if 'project_memory' in parsed.get('mcp_servers',{}):raise Conflict('An unmanaged project_memory MCP server already exists.')
        launch=_launcher or launcher()
        command=(subprocess.list2cmdline if os.name=='nt' else shlex.join)(launch+['hook','--db',str(database)])
        block=BEGIN+'[mcp_servers.project_memory]\ncommand = '+json.dumps(launch[0])+'\nargs = '+json.dumps(launch[1:]+['serve','--db',str(database)])+'\ncwd = '+json.dumps(str(project))+'\ndefault_tools_approval_mode = "approve"\n'+END
        for event in sorted(EVENTS):
            group={'hooks':[{'type':'command','command':command,'timeout':3 if event in {'Interrupt','SessionEnd'} else 10}]}
            if event in {'PreToolUse','PostToolUse'}:group['matcher']='.*'
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
            for path in (config,hooks_path):
                if path.exists():shutil.copy2(path,backup_dir/path.name)
            backups=[str(backup_dir)]
        initialize(memory)
        captured=[memory.document(str(Path(doc).resolve()))['id'] for doc in documents]
    # Persist ownership first: a retry can finish after either configuration write.
    new_state={'version':__version__,'phase':'pending','database':str(database),'client':client,'block':block,
               'hook_command':command,'previous_block':(state or {}).get('block',''),
               'previous_hook_command':(state or {}).get('hook_command',''),'backups':(state or {}).get('backups',[])+backups}
    atomic(state_path,json.dumps(new_state,indent=2)+'\n')
    if client=='codex' or state and state.get('block'):
        atomic(hooks_path,json.dumps(hooks,indent=2)+'\n');atomic(config,config_text)
    new_state['phase']='installed';new_state.pop('previous_block');new_state.pop('previous_hook_command');atomic(state_path,json.dumps(new_state,indent=2)+'\n')
    result={'project':str(project),'database':str(database),'client':client,'documents':captured,'phase':'installed',
            'capture':'Configured only. Run a new Codex task and inspect doctor for actual receipts.' if client=='codex' else 'Explicit MCP capture only; this client has no automatic hooks.'}
    if trust:
        from .setup_codex import trust_project_hooks
        result['trust']=trust_project_hooks(project,database,command)
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
    state_path.unlink()
    return {'removed':True,'database_preserved':state['database'],
            'note':'Project records and backups remain. Codex may retain inactive trust hashes for the removed commands.'}
