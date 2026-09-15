"""Project-scoped installation with recoverable, ownership-checked configuration.

One project can connect several clients at the same time. The ownership file
`.memory/install.json` records, per client, the exact configuration that setup
wrote, so setup and uninstall change only the entries they own.
"""
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
HOOK_CLIENTS = ('codex', 'claude')
SERVER = 'project_memory'
SOURCE_ROOT = Path(__file__).resolve().parent.parent
PREVIOUS = ('previous_block', 'previous_hook_command', 'previous_server_entry')


def python_args(module):
    """Return the interpreter arguments that run a module of this package."""
    # Project files must not shadow the runtime in installed or source-launched children.
    if (SOURCE_ROOT / 'pyproject.toml').is_file():
        bootstrap = 'import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); runpy.run_module(sys.argv.pop(1),run_name="__main__")'
        return [sys.executable, '-X', 'utf8', '-c', bootstrap, str(SOURCE_ROOT), module]
    return [sys.executable, '-I', '-X', 'utf8', '-m', module]


def atomic(path, content):
    """Replace a file atomically after flushing its content to disk."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as out:
            out.write(content)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def launcher():
    """Return a stable command that starts the installed Project Memory CLI."""
    uv = shutil.which('uv')
    if uv and (SOURCE_ROOT / 'pyproject.toml').exists() and (SOURCE_ROOT / '.venv').exists():
        # A source checkout runs its own environment, so unreleased changes can be exercised in a real host.
        return [uv, 'run', '--project', str(SOURCE_ROOT), 'project-memory']
    if not any(part.startswith('archive-v') for part in Path(sys.executable).parts):
        # Persistent installations must run their installed code, including unreleased wheels.
        return python_args('memory_module.cli')
    if uv:
        # The host retains a stable launcher, never an interpreter in uvx's cache.
        return [uv, 'tool', 'run', '--from', RELEASE_SOURCE, 'project-memory']
    raise Conflict('Install uv or install project-memory-mcp in a persistent environment before setting up Codex.')


def paths(project):
    """Return the project, ownership file, Codex configuration and Codex hooks paths."""
    project = Path(project).resolve()
    if not project.is_dir():
        raise ValueError('Project directory does not exist.')
    return project, project / '.memory/install.json', project / '.codex/config.toml', project / '.codex/hooks.json'


def claude_paths(project):
    """Return the Claude Code MCP file and local settings file of a project."""
    # Claude Code reads project MCP servers from .mcp.json and hooks from project settings.
    # The local settings file stays out of version control, which suits machine-specific launch paths.
    return project / '.mcp.json', project / '.claude/settings.local.json'


def load_json(path, default):
    """Read a JSON object from a file, or return the default when it is missing."""
    if not path.exists():
        return default
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError(f'{path.name} must contain a JSON object.')
    return value


def load_settings(path):
    """Claude Code settings hold hooks beside other keys. Validate only the hooks section."""
    value = load_json(path, {})
    hooks = value.setdefault('hooks', {})
    if not isinstance(hooks, dict):
        raise ValueError('Existing hooks setting is invalid.')
    load_hooks_value({'hooks': hooks})
    return value


def hook_groups(command, events):
    """Build one command hook group per lifecycle event."""
    groups = {}
    for event in sorted(events):
        timeout = 3 if event in {'Interrupt', 'SessionEnd'} else 10
        group = {'hooks': [{'type': 'command', 'command': command, 'timeout': timeout}]}
        if event in {'PreToolUse', 'PostToolUse', 'PostToolUseFailure'}:
            group['matcher'] = '.*'
        groups[event] = group
    return groups


def remove_block(text, block):
    """Remove the recorded managed block, refusing a block that was edited."""
    if BEGIN not in text and END not in text:
        return text
    if text.count(BEGIN) != 1 or text.count(END) != 1 or block not in text:
        raise Conflict('The managed MCP configuration changed. Restore its recorded block before running setup or uninstall.')
    return text.replace(block, '', 1)


def load_hooks(path):
    """Read and validate a Codex hooks file."""
    return load_hooks_value(load_json(path, {'hooks': {}}))


def load_hooks_value(value):
    """Validate the shape of a hooks object and return it."""
    if not isinstance(value, dict) or not isinstance(value.get('hooks'), dict):
        raise ValueError('Existing hooks file is invalid.')
    for groups in value['hooks'].values():
        if not isinstance(groups, list):
            raise ValueError('Existing hook groups must be lists.')
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get('hooks'), list):
                raise ValueError('Existing hook group is invalid.')
            if any(not isinstance(h, dict) for h in group['hooks']):
                raise ValueError('Existing hook command is invalid.')
    return value


def remove_hooks(value, command):
    """Remove every hook entry that runs exactly this command."""
    for event, groups in list(value['hooks'].items()):
        kept = []
        for group in groups:
            items = [h for h in group['hooks'] if not (h.get('type') == 'command' and h.get('command') == command)]
            if items:
                kept.append({**group, 'hooks': items})
        if kept:
            value['hooks'][event] = kept
        else:
            value['hooks'].pop(event)
    return value


def upgrade_state(state):
    """Convert a single client ownership record into the per client format.

    A legacy record that was interrupted while switching clients keeps the
    previous client as installed. Removing an absent block or hook is harmless,
    so a later setup or uninstall of that client still completes safely.
    """
    if isinstance(state.get('clients'), dict):
        return state
    client = state.get('client') or 'mcp'
    hook = state.get('hook_command') or ''
    previous_hook = state.get('previous_hook_command') or ''
    clients = {}
    if state.get('block'):
        codex = {'block': state['block'], 'hook_command': hook}
        if state.get('previous_block'):
            codex['previous_block'] = state['previous_block']
            codex['previous_hook_command'] = previous_hook
        clients['codex'] = codex
    elif state.get('previous_block'):
        clients['codex'] = {'block': state['previous_block'], 'hook_command': previous_hook}
    if state.get('server_entry'):
        claude = {'server_entry': state['server_entry'], 'hook_command': hook}
        if state.get('previous_server_entry'):
            claude['previous_server_entry'] = state['previous_server_entry']
            claude['previous_hook_command'] = previous_hook
        clients['claude'] = claude
    elif state.get('previous_server_entry'):
        clients['claude'] = {'server_entry': state['previous_server_entry'], 'hook_command': previous_hook}
    if client == 'mcp' or not clients:
        clients['mcp'] = {}
    if client not in clients:
        client = sorted(clients)[-1]
    return {'version': state.get('version', ''), 'phase': state.get('phase', 'installed'), 'database': state['database'],
            'clients': clients, 'client': client, 'backups': list(state.get('backups', []))}


def read_state(state_path):
    """Read the ownership file in the per client format, or return None."""
    state_path = Path(state_path)
    if not state_path.exists():
        return None
    value = json.loads(state_path.read_text(encoding='utf-8'))
    if not isinstance(value, dict) or 'database' not in value:
        raise ValueError('The installation file .memory/install.json is invalid.')
    return upgrade_state(value)


def project_state(project):
    """Return the ownership record of a project, or an empty dictionary."""
    return read_state(Path(project).resolve() / '.memory/install.json') or {}


def write_state(state_path, state):
    atomic(state_path, json.dumps(state, indent=2) + '\n')


def join_command(parts):
    return (subprocess.list2cmdline if os.name == 'nt' else shlex.join)(parts)


def candidates(entry, key):
    """Return the current and previous owned values of one key, without empty values."""
    values = []
    for name in (key, 'previous_' + key):
        value = (entry or {}).get(name)
        if value and value not in values:
            values.append(value)
    return values


def strip_codex(text, entry, strict):
    """Remove the owned Codex block from configuration text."""
    for block in candidates(entry, 'block'):
        if block in text:
            return remove_block(text, block)
    if strict and (BEGIN in text or END in text):
        raise Conflict('The managed MCP block has changed.')
    return text


def strip_claude(servers, settings, entry):
    """Remove the owned Claude Code server entry and hooks."""
    for owned in candidates(entry, 'server_entry'):
        if servers.get(SERVER) == owned:
            servers.pop(SERVER)
    for command in candidates(entry, 'hook_command'):
        remove_hooks(settings, command)


def setup(project, *, client='mcp', database=None, requirements=None, documents=(), trust=False, _launcher=None):
    """Add or refresh one client in a project and keep the other configured clients."""
    project, state_path, config, hooks_path = paths(project)
    if client not in CLIENTS:
        raise ValueError('Client must be mcp, codex or claude.')
    if trust and client == 'mcp':
        raise ValueError('--trust requires --client codex or --client claude.')
    state = read_state(state_path)
    database = Path(database).resolve() if database else project / '.memory/project.sqlite'
    if state and state['database'] != str(database):
        raise Conflict('This project is connected to another database. Uninstall the connection before changing it.')
    clients = {name: dict(entry) for name, entry in (state or {}).get('clients', {}).items()}
    owned = clients.get(client)
    mcp_path, settings_path = claude_paths(project)
    entry = {}
    command = ''
    config_text = None
    hooks = None
    mcp_config = None
    settings = None
    if client == 'codex':
        original = config.read_text(encoding='utf-8') if config.exists() else ''
        plain = strip_codex(original, owned, strict=bool(state))
        parsed = tomllib.loads(plain)
        hooks = load_hooks(hooks_path)
        for previous in candidates(owned, 'hook_command'):
            remove_hooks(hooks, previous)
        legacy = parsed.get('mcp_servers', {}).get('memory', {})
        if 'memory_module.mcp' in legacy.get('args', []):
            raise Conflict('This project still uses the legacy memory-module adapter. Disable its MCP server and hooks before connecting Project Memory; its database can be reused.')
        if 'hooks' in parsed:
            raise Conflict('Inline Codex hooks take precedence. Move them to hooks.json before installing project hooks.')
        if SERVER in parsed.get('mcp_servers', {}):
            raise Conflict('An unmanaged project_memory MCP server already exists.')
        launch = _launcher or launcher()
        command = join_command(launch + ['hook', '--db', str(database)])
        block = (BEGIN + '[mcp_servers.project_memory]\n'
                 + 'command = ' + json.dumps(launch[0]) + '\n'
                 + 'args = ' + json.dumps(launch[1:] + ['serve', '--db', str(database)]) + '\n'
                 + 'cwd = ' + json.dumps(str(project)) + '\n'
                 + 'default_tools_approval_mode = "approve"\n' + END)
        for event, group in hook_groups(command, EVENTS).items():
            hooks['hooks'].setdefault(event, []).append(group)
        config_text = plain + ('\n' if plain and not plain.endswith('\n') else '') + block
        tomllib.loads(config_text)
        entry = {'block': block, 'hook_command': command}
        if owned:
            entry['previous_block'] = owned.get('block', '')
            entry['previous_hook_command'] = owned.get('hook_command', '')
    elif client == 'claude':
        mcp_config = load_json(mcp_path, {})
        settings = load_settings(settings_path)
        servers = mcp_config.setdefault('mcpServers', {})
        if not isinstance(servers, dict):
            raise ValueError('Existing .mcp.json mcpServers must be an object.')
        strip_claude(servers, settings, owned)
        if SERVER in servers:
            raise Conflict('An unmanaged project_memory MCP server already exists in .mcp.json.')
        launch = _launcher or launcher()
        command = join_command(launch + ['hook', '--db', str(database), '--host', 'claude'])
        server_entry = {'command': launch[0], 'args': launch[1:] + ['serve', '--db', str(database)]}
        servers[SERVER] = server_entry
        # Claude Code raises PostToolUseFailure instead of a second PostToolUse; both close the same tool receipt.
        for event, group in hook_groups(command, HOST_EVENTS['claude'] | {'PostToolUseFailure'}).items():
            settings['hooks'].setdefault(event, []).append(group)
        if trust:
            enabled = settings.setdefault('enabledMcpjsonServers', [])
            if not isinstance(enabled, list):
                raise ValueError('Existing enabledMcpjsonServers setting is invalid.')
            if SERVER not in enabled:
                enabled.append(SERVER)
        entry = {'server_entry': server_entry, 'hook_command': command}
        if owned:
            entry['previous_server_entry'] = owned.get('server_entry')
            entry['previous_hook_command'] = owned.get('hook_command', '')
    private = project / '.memory'
    private.mkdir(exist_ok=True)
    ignore = private / '.gitignore'
    if not ignore.exists():
        atomic(ignore, '*\n')
    database.parent.mkdir(parents=True, exist_ok=True)
    if not database.exists():
        default = ['No project-specific requirements have been approved. Obtain agreement before making material project decisions.']
        with Memory.create(database, project.name, requirements or default):
            pass
    elif requirements:
        with Memory(database) as memory:
            if memory.requirements != requirements:
                raise Conflict('Existing requirements differ. Approve an explicit direction revision instead of changing setup defaults.')
    backups = []
    with Memory(database) as memory:
        if owned is None:
            backup_dir = project / '.memory/backups' / uuid.uuid4().hex
            backup_dir.mkdir(parents=True)
            memory.backup(backup_dir / 'project.sqlite')
            for path in (config, hooks_path, mcp_path, settings_path):
                if path.exists():
                    shutil.copy2(path, backup_dir / path.name)
            backups = [str(backup_dir)]
        with memory._write():
            memory.db.execute("INSERT OR REPLACE INTO settings VALUES ('workspace_project',?)", (json.dumps(str(project)),))
        initialize(memory)
        if client in HOOK_CLIENTS:
            from .reviews import configure
            configure(memory, project, client)
        initialize_graph(memory)
        from .health import inspect
        health = inspect(memory)
        captured = [memory.document(str(Path(doc).resolve()))['id'] for doc in documents]
    # Persist ownership first: a retry can finish after either configuration write.
    clients[client] = entry
    new_state = {'version': __version__, 'phase': 'pending', 'database': str(database), 'clients': clients,
                 'client': client, 'backups': (state or {}).get('backups', []) + backups}
    write_state(state_path, new_state)
    if client == 'codex':
        atomic(hooks_path, json.dumps(hooks, indent=2) + '\n')
        atomic(config, config_text)
    if client == 'claude':
        atomic(mcp_path, json.dumps(mcp_config, indent=2) + '\n')
        atomic(settings_path, json.dumps(settings, indent=2) + '\n')
    new_state['phase'] = 'installed'
    for key in PREVIOUS:
        entry.pop(key, None)
    write_state(state_path, new_state)
    capture = {'codex': 'Configured only. Run a new Codex task and inspect doctor for actual receipts.',
               'claude': 'Configured only. Start a new Claude Code session in this project, approve the project MCP server if asked, and inspect doctor for actual receipts.',
               'mcp': 'Explicit MCP capture only; this client has no automatic hooks.'}[client]
    result = {'project': str(project), 'database': str(database), 'client': client, 'clients': sorted(clients),
              'documents': captured, 'phase': 'installed', 'capture': capture, 'baseline': health['baseline'],
              'viewer': {'available': True, 'command': 'project-memory view', 'mode': 'live; interactive; local'}}
    if trust and client == 'codex':
        from .setup_codex import trust_project_hooks
        result['trust'] = trust_project_hooks(project, database, command)
    elif trust:
        result['trust'] = {'enabled_mcpjson_servers': [SERVER], 'settings': str(settings_path),
                           'note': 'Claude Code runs project hooks from its settings files without a separate trust step. The MCP server is pre-approved in the local settings.'}
    return result


def initialize_graph(memory):
    """Create the links table when the graph module is available."""
    try:
        from . import graph
    except ModuleNotFoundError as exc:
        if exc.name != __package__ + '.graph':
            raise
        return False
    graph.initialize(memory)
    return True


def uninstall(project, client=None):
    """Remove one client, or every client when none is named. Records are preserved."""
    project, state_path, config, hooks_path = paths(project)
    if client is not None and client not in CLIENTS:
        raise ValueError('Client must be mcp, codex or claude.')
    state = read_state(state_path)
    if not state:
        return {'removed': False, 'reason': 'No managed installation exists.'}
    clients = state['clients']
    if client is not None and client not in clients:
        return {'removed': False, 'reason': f'The {client} client is not installed in this project.',
                'clients': sorted(clients)}
    selected = sorted(clients) if client is None else [client]
    removed_hosts = remove_review_hosts(state['database'], [name for name in selected if name in HOOK_CLIENTS])
    if 'codex' in selected:
        entry = clients['codex']
        original = config.read_text(encoding='utf-8') if config.exists() else ''
        plain = strip_codex(original, entry, strict=True)
        hooks = load_hooks(hooks_path)
        for command in candidates(entry, 'hook_command'):
            remove_hooks(hooks, command)
        atomic(config, plain)
        atomic(hooks_path, json.dumps(hooks, indent=2) + '\n')
    if 'claude' in selected:
        entry = clients['claude']
        mcp_path, settings_path = claude_paths(project)
        mcp_config = load_json(mcp_path, {})
        servers = mcp_config.get('mcpServers', {})
        settings = load_settings(settings_path)
        strip_claude(servers if isinstance(servers, dict) else {}, settings, entry)
        enabled = settings.get('enabledMcpjsonServers')
        if isinstance(enabled, list) and SERVER in enabled:
            enabled.remove(SERVER)
        if not settings['hooks']:
            settings.pop('hooks')
        atomic(mcp_path, json.dumps(mcp_config, indent=2) + '\n')
        atomic(settings_path, json.dumps(settings, indent=2) + '\n')
    remaining = {name: entry for name, entry in clients.items() if name not in selected}
    if remaining:
        last = state['client'] if state['client'] in remaining else sorted(remaining)[-1]
        write_state(state_path, {**state, 'clients': remaining, 'client': last})
    else:
        state_path.unlink()
    return {'removed': True, 'clients_removed': selected, 'clients': sorted(remaining),
            'installation_file_kept': bool(remaining), 'database_preserved': state['database'],
            'agent_hosts_removed': removed_hosts,
            'note': 'Project records and backups remain. Codex may retain inactive trust entries for removed commands. Removed clients no longer run agent checks or delegated work.'}


def remove_review_hosts(database, names):
    """Remove uninstalled hook clients from the agent host setting before their configuration is removed."""
    if not names or not Path(database).exists():
        return []
    from .reviews import remove_host
    with Memory(database) as memory:
        for name in names:
            remove_host(memory, name)
    return sorted(names)
