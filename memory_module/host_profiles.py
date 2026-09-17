"""One declarative profile per agent host for the process runner: codex, claude, grok and opencode.

A profile states the program and its environment override, the roles the host may take before and after a
passing probe, the command line builders for the review and work roles, the environment a run receives, how
the structured answer is obtained, the extra run log events and their usage, and the wording of usage limits,
rate limits and missing sign in with reset times. hosts.py reads these profiles and keeps its public
functions, so adding a host means adding a profile and passing its probe.

Facts behind the Grok and OpenCode profiles, observed on 17 September 2026 from `grok --help`, `grok inspect`,
`opencode run --help` and the documentation that each program carries, without running a model:

- Grok 0.2.93 reads the Claude Code and Cursor configuration through ten harness compatibility cells (skills,
  rules, agents, mcps and hooks for each vendor). Each cell has a documented environment variable, such as
  GROK_CLAUDE_MCPS_ENABLED, that takes precedence over configuration. With the variables false, `grok inspect --json`
  reports every cell disabled with source env and marks the Claude MCP servers, Claude skills and the Claude
  instruction file disabled.
- Grok's own configuration has no switch: the user skills in ~/.grok/skills and ~/.agents/skills stay enabled, and so
  do the MCP servers and hooks of ~/.grok. `grok --help` offers no strict MCP configuration and no flag that skips
  skills or project rules. The review command therefore allows only three built-in tools and denies every MCP tool
  invocation, but a configured MCP server can still start.
- In a repository, Grok also loads .grok/config.toml (MCP servers, plugins, permissions), .grok/skills, .grok/hooks and
  .agents/skills from the working directory up to the git root, and a fresh repository was reported trusted. These
  cannot be switched off, so the Grok command lines refuse a folder that holds them. AGENTS.md project rules are also
  read and cannot be switched off.
- Every built-in Grok sandbox allows writes to ~/.grok, where later runs load hooks and configuration. The probe of
  the work role therefore also tries a write there, and the work role stays locked while that write succeeds.
- Grok offers no per run flag that adds an MCP server, so a Grok worker cannot receive the hive server.
- OpenCode 1.18.21 reads its global configuration from XDG_CONFIG_HOME, the extra configuration named by
  OPENCODE_CONFIG, and the project configuration unless OPENCODE_DISABLE_PROJECT_CONFIG is set. A permission
  request that the configuration does not allow is rejected in `opencode run` unless --auto is passed.
"""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re

from .core import InvalidRecord

REVIEW = 'review'
WORK = 'work'
WORK_TOOLS = 'Read,Glob,Grep,Edit,Write,Bash'
# The settings of a Claude worker: hooks disabled, and shell commands confined to the operating system sandbox, which
# limits writes to the worktree. A run fails at startup when the sandbox cannot start, and no command runs outside it,
# so a shell command cannot write to the hive or start a hive server that logs as another agent.
WORK_SETTINGS = json.dumps({'disableAllHooks': True, 'sandbox': {'enabled': True, 'failIfUnavailable': True,
                                                                 'autoAllowBashIfSandboxed': True, 'allowUnsandboxedCommands': False}},
                           separators=(',', ':'))
# The one MCP server a delegated worker of a swarm receives, and the tools Claude may call on it.
HIVE_SERVER = 'hive'
HIVE_TOOLS = ('hive_log', 'hive_query', 'hive_resume')
HIVE_NAME_TAKEN = ('The Codex configuration defines an MCP server named hive, which the hive server of a delegated worker '
                   'needs. Rename that server in the Codex configuration, then delegate the work again.')
HIVE_NOT_SUPPORTED = ('The {host} host cannot receive the hive server on its command line. Delegate work of a swarm to '
                      'Codex or Claude.')

GROK_REVIEW_TOOLS = 'read_file,grep,list_dir'
GROK_WORK_TOOLS = 'read_file,grep,list_dir,search_replace,write_file,run_terminal_cmd'
GROK_REVIEW_TURNS = '50'
GROK_WORK_TURNS = '200'
# The repository configuration that Grok loads from the working directory up to the git root and that no setting
# switches off, relative to each of those folders.
GROK_REPOSITORY_CONFIGURATION = ('.grok', '.agents/skills')
GROK_REPOSITORY_REFUSED = ('The {host} host would load the repository configuration in {names}, and no Grok setting '
                           'switches that off. Remove that configuration from the repository or use another host.')
GROK_COMPATIBILITY = tuple(f'GROK_{vendor}_{surface}_ENABLED' for vendor in ('CLAUDE', 'CURSOR')
                           for surface in ('SKILLS', 'RULES', 'AGENTS', 'MCPS', 'HOOKS'))
OPENCODE_AGENT = 'project-memory-review'
OPENCODE_CONFIG_FILE = 'opencode.json'
OPENCODE_EMPTY_HOME = 'opencode-config-home'
OPENCODE_DENIED = ('edit', 'bash', 'webfetch', 'websearch', 'task', 'skill', 'todowrite', 'question', 'external_directory')
OPENCODE_ANSWER_PROTOCOL = ('\nReturn the report as the last part of your final message: one fenced block that starts with ```json '
                            'and holds only a JSON object that matches this schema. A final message without that block fails '
                            'the run.\nSchema:\n')
TEXT_LIMIT = 256 * 1024
USAGE_EVENTS_LIMIT = 100


def toml_value(value):
    """A TOML value for a Codex override. Characters outside ASCII stay literal, because TOML rejects the surrogate
    pairs that JSON uses for characters outside the Basic Multilingual Plane."""
    return json.dumps(value, ensure_ascii=False)


def codex_config_overrides(project):
    """Return the configured model and the arguments that disable every configured MCP server."""
    import tomllib
    config_path = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'config.toml'
    config = tomllib.loads(config_path.read_text()) if config_path.exists() else {}
    model = []
    if isinstance(config.get('model'), str):
        model = ['-m', config['model']]
    servers = []
    # Only a server that the loaded configuration defines can be disabled. Codex reads the user
    # configuration and the working directory's own .codex/config.toml, so naming a server from a
    # parent directory would create an entry that carries no command and no address, which Codex
    # rejects as an invalid transport before the run starts.
    seen = set()
    for path in [config_path, Path(project) / '.codex/config.toml']:
        if not path.exists():
            continue
        for name in tomllib.loads(path.read_text()).get('mcp_servers', {}):
            if not re.fullmatch(r'[A-Za-z0-9_-]+', name):
                raise InvalidRecord('The review host cannot safely disable the MCP server named ' + name + '.')
            if name in seen:
                continue
            seen.add(name)
            servers += ['-c', 'mcp_servers.' + name + '.enabled=false']
    return model, servers


def hive_server(binding):
    """The command and arguments of the restricted hive server for one worker.

    binding holds hive (the hive file), db (the project database), swarm_id, agent_id and role.
    The server offers only hive_log, hive_query and hive_resume, bound to that swarm and agent.
    """
    from .install import python_args
    launch = python_args('memory_module.cli')
    # Each value is joined to its option with =, so a value that begins with a dash stays the value of its option.
    arguments = ['hive-serve', '--hive=' + str(binding['hive']), '--swarm=' + binding['swarm_id'], '--agent=' + binding['agent_id'],
                 '--role=' + binding['role'], '--db=' + str(binding['db'])]
    return {'command': launch[0], 'args': launch[1:] + arguments}


# Codex.

def codex_review(project, folder, prompt):
    model, servers = codex_config_overrides(project)
    overrides = model + servers
    return ['codex', 'exec', '--ephemeral', '--skip-git-repo-check', '--sandbox', 'read-only',
            '-C', project, '-c', 'approval_policy="never"', '-c', 'features.hooks=false', '-c', 'features.plugins=false',
            '-c', 'features.apps=false', '-c', 'features.multi_agent=false', '-c', 'project_doc_max_bytes=0',
            '-c', 'memories.use_memories=false', '-c', 'memories.generate_memories=false',
            '-c', 'skills.include_instructions=false', '-c', 'web_search="disabled"', *overrides,
            '--output-schema', str(folder / 'schema.json'), '--output-last-message', str(folder / 'answer.json'), '--json', '-']


def codex_work(worktree, folder, prompt, hive=None):
    server = hive_server(hive) if hive else None
    _, servers = codex_config_overrides(worktree)
    hive_overrides = []
    if server:
        if 'mcp_servers.' + HIVE_SERVER + '.enabled=false' in servers:
            raise InvalidRecord(HIVE_NAME_TAKEN)
        hive_overrides = ['-c', 'mcp_servers.' + HIVE_SERVER + '.command=' + toml_value(server['command']),
                          '-c', 'mcp_servers.' + HIVE_SERVER + '.args=' + toml_value(server['args'])]
    return ['codex', 'exec', '--ephemeral', '--skip-git-repo-check', '--sandbox', 'workspace-write',
            '-C', str(worktree), '-c', 'approval_policy="never"', '-c', 'features.hooks=false', '-c', 'features.plugins=false',
            '-c', 'features.apps=false', '-c', 'features.multi_agent=false',
            '-c', 'memories.use_memories=false', '-c', 'memories.generate_memories=false',
            '-c', 'web_search="disabled"', *servers, *hive_overrides,
            '--output-schema', str(folder / 'schema.json'), '--output-last-message', str(folder / 'answer.json'), '--json', '-']


def result_answer(log):
    """The structured answer of a result event, as Claude reports it."""
    if log.result:
        candidate = log.result.get('structured_output')
        if candidate is None and log.result.get('result'):
            candidate = json.loads(log.result['result'])
        return candidate
    return None


def codex_answer(folder, log):
    if (folder / 'answer.json').exists():
        return json.loads((folder / 'answer.json').read_text())
    return result_answer(log)


# Claude.

def claude_review(project, folder, prompt):
    return ['claude', '-p', '--restricted', '--no-session-persistence', '--output-format', 'stream-json', '--verbose', '--permission-mode', 'dontAsk',
            '--setting-sources', '', '--settings', '{"disableAllHooks":true}', '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
            '--disable-slash-commands', '--tools', 'Read,Glob,Grep', '--allowedTools', 'Read,Glob,Grep',
            '--json-schema', (folder / 'schema.json').read_text(), '--system-prompt', prompt]


def claude_work(worktree, folder, prompt, hive=None):
    server = hive_server(hive) if hive else None
    configured = {'mcpServers': {}}
    allowed = WORK_TOOLS
    if server:
        configured['mcpServers'][HIVE_SERVER] = {'type': 'stdio', 'command': server['command'], 'args': server['args']}
        allowed += ',' + ','.join('mcp__' + HIVE_SERVER + '__' + name for name in HIVE_TOOLS)
    return ['claude', '-p', '--restricted', '--no-session-persistence', '--output-format', 'stream-json', '--verbose', '--permission-mode', 'dontAsk',
            '--setting-sources', '', '--settings', WORK_SETTINGS, '--strict-mcp-config',
            '--mcp-config', json.dumps(configured, separators=(',', ':')),
            '--disable-slash-commands', '--tools', WORK_TOOLS, '--allowedTools', allowed,
            '--json-schema', (folder / 'schema.json').read_text(), '--system-prompt', prompt]


def claude_answer(folder, log):
    return result_answer(log)


# Grok.

def grok_environment(project, folder, prompt):
    """Switch off every harness compatibility cell and cross session memory for one run."""
    values = {name: 'false' for name in GROK_COMPATIBILITY}
    values['GROK_MEMORY'] = '0'
    return values


def grok_home():
    """The folder of the Grok configuration, following GROK_HOME."""
    return Path(os.environ.get('GROK_HOME') or Path.home() / '.grok')


def grok_repository_configuration(folder):
    """The Grok configuration entries in a folder and its parents up to the git root, as names relative to each folder.

    Without a git root only the folder itself is read. The Grok home folder is the user configuration, not a repository
    configuration, and is skipped.
    """
    start = Path(folder).absolute()
    chain = [start, *start.parents]
    root = next((index for index, path in enumerate(chain) if (path / '.git').exists()), None)
    chain = chain[:root + 1] if root is not None else chain[:1]
    home = grok_home().absolute()
    found = []
    user = Path.home().absolute()
    for path in chain:
        if path == user:
            continue
        for name in GROK_REPOSITORY_CONFIGURATION:
            candidate = path / name
            if candidate.exists() and candidate.absolute() != home and name not in found:
                found.append(name)
    return found


def _refuse_grok_repository_configuration(folder):
    found = grok_repository_configuration(folder)
    if found:
        raise InvalidRecord(GROK_REPOSITORY_REFUSED.format(host='grok', names=' and '.join(found)), configuration=found)


def grok_review(project, folder, prompt):
    _refuse_grok_repository_configuration(project)
    return ['grok', '--prompt-file', str(folder / 'prompt.txt'), '--output-format', 'streaming-json',
            '--json-schema', (folder / 'schema.json').read_text(), '--cwd', str(project), '--sandbox', 'read-only',
            '--permission-mode', 'dontAsk', '--tools', GROK_REVIEW_TOOLS, '--deny', 'MCPTool', '--deny', 'Bash',
            '--deny', 'Edit', '--deny', 'Write', '--deny', 'WebFetch', '--no-subagents', '--no-memory',
            '--disable-web-search', '--max-turns', GROK_REVIEW_TURNS, '--verbatim', '--rules=' + prompt]


def grok_work(worktree, folder, prompt, hive=None):
    if hive:
        raise InvalidRecord(HIVE_NOT_SUPPORTED.format(host='grok'))
    _refuse_grok_repository_configuration(worktree)
    return ['grok', '--prompt-file', str(folder / 'prompt.txt'), '--output-format', 'streaming-json',
            '--json-schema', (folder / 'schema.json').read_text(), '--cwd', str(worktree), '--sandbox', 'workspace',
            '--permission-mode', 'dontAsk', '--tools', GROK_WORK_TOOLS, '--allow', 'Read', '--allow', 'Grep',
            '--allow', 'Edit', '--allow', 'Write', '--allow', 'Bash', '--deny', 'MCPTool', '--deny', 'WebFetch',
            '--no-subagents', '--no-memory', '--disable-web-search', '--max-turns', GROK_WORK_TURNS, '--verbatim',
            '--rules=' + prompt]


def grok_event(log, kind, value):
    """Grok streaming-json events: text and thought chunks, end with metadata, and max_turns_reached."""
    if kind == 'text' and isinstance(value.get('data'), str):
        log.append_text(value['data'])
        log.metrics['phase'] = 'awaiting_host'
    elif kind == 'thought':
        log.metrics['phase'] = 'awaiting_host'
    elif kind == 'max_turns_reached':
        log.metrics['host_error_events'] += 1
        log.metrics['unrecovered_error_events'] += 1
        log.metrics['phase'] = 'host_error'
        log.completed = False
    elif kind == 'end':
        log.result = value
        usage = grok_usage_fields(value)
        if usage:
            log.metrics['provider_usage'] = usage
        log.finish()


def grok_usage_fields(value):
    """Token counts that an end event carries, from a usage object or top level token fields, else None."""
    source = value.get('usage') if isinstance(value.get('usage'), dict) else value
    found = {key: number for key, number in source.items()
             if isinstance(key, str) and key.lower().endswith('tokens') and isinstance(number, (int, float))
             and not isinstance(number, bool)}
    return found or None


def grok_answer(folder, log):
    """The structuredOutput of the end event, or the streamed text read as JSON."""
    if log.result:
        candidate = log.result.get('structuredOutput')
        if isinstance(candidate, str):
            return json.loads(candidate)
        if candidate is not None:
            return candidate
        if log.result.get('structuredOutputError'):
            raise InvalidRecord('The grok host did not produce the structured answer that the schema requires.')
    text = log.text().strip()
    if text:
        return json.loads(text)
    return None


# OpenCode.

def strip_jsonc(text):
    """Remove comments and trailing commas outside strings, so a JSONC configuration parses as JSON."""
    output = []
    index = 0
    in_string = False
    while index < len(text):
        char = text[index]
        pair = text[index:index + 2]
        if in_string:
            output.append(char)
            if char == '\\':
                output.append(text[index + 1:index + 2])
                index += 2
                continue
            if char == '"':
                in_string = False
            index += 1
        elif char == '"':
            in_string = True
            output.append(char)
            index += 1
        elif pair == '//':
            end = text.find('\n', index)
            index = len(text) if end < 0 else end
        elif pair == '/*':
            end = text.find('*/', index + 2)
            index = len(text) if end < 0 else end + 2
        else:
            output.append(char)
            index += 1
    return re.sub(r',(\s*[}\]])', r'\1', ''.join(output))


def opencode_global_model():
    """The model of the user's global OpenCode configuration, which the isolated run no longer reads."""
    base = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'opencode'
    for name in ('opencode.jsonc', 'opencode.json', 'config.json'):
        path = base / name
        if not path.is_file():
            continue
        try:
            value = json.loads(strip_jsonc(path.read_text(encoding='utf-8')))
        except (OSError, ValueError):
            continue
        if isinstance(value, dict) and isinstance(value.get('model'), str) and value['model'].strip():
            return value['model']
    return None


def opencode_configuration(folder, prompt):
    """The generated review configuration: every edit, shell, network and delegation tool denied, and no MCP server."""
    denied = {name: 'deny' for name in OPENCODE_DENIED}
    agent_prompt = prompt + OPENCODE_ANSWER_PROTOCOL + (folder / 'schema.json').read_text()
    return {'$schema': 'https://opencode.ai/config.json', 'autoupdate': False, 'share': 'disabled', 'mcp': {},
            'permission': denied,
            'agent': {OPENCODE_AGENT: {'description': 'Project Memory read only review.', 'mode': 'primary',
                                       'prompt': agent_prompt, 'permission': denied}}}


def opencode_environment(project, folder, prompt):
    """Write the review configuration into the run folder and return the variables that load only that configuration."""
    home = folder / OPENCODE_EMPTY_HOME
    home.mkdir(parents=True, exist_ok=True)
    config = folder / OPENCODE_CONFIG_FILE
    config.write_text(json.dumps(opencode_configuration(folder, prompt), ensure_ascii=False, indent=1), encoding='utf-8')
    return {'OPENCODE_CONFIG': str(config), 'XDG_CONFIG_HOME': str(home), 'OPENCODE_DISABLE_PROJECT_CONFIG': '1',
            'OPENCODE_DISABLE_CLAUDE_CODE': '1', 'OPENCODE_DISABLE_EXTERNAL_SKILLS': '1',
            'OPENCODE_DISABLE_DEFAULT_PLUGINS': '1', 'OPENCODE_DISABLE_AUTOUPDATE': '1', 'OPENCODE_DISABLE_SHARE': '1'}


def opencode_review(project, folder, prompt):
    model = opencode_global_model()
    return ['opencode', 'run', '--format', 'json', '--pure', '--dir', str(project), '--agent', OPENCODE_AGENT,
            *(['--model', model] if model else [])]


def opencode_event(log, kind, value):
    """OpenCode json events: step_start, tool_use, text, reasoning and step_finish, each with a part."""
    part = value.get('part') if isinstance(value.get('part'), dict) else {}
    if kind == 'step_start':
        log.metrics['phase'] = 'awaiting_host'
    elif kind == 'tool_use':
        log.metrics['completed_inspections'] += 1
        state = part.get('state') if isinstance(part.get('state'), dict) else {}
        if state.get('status') == 'error':
            log.metrics['failed_inspections'] += 1
        log.metrics['phase'] = 'awaiting_host'
    elif kind == 'text' and isinstance(part.get('text'), str):
        log.replace_text(part['text'])
    elif kind == 'step_finish':
        step = {'tokens': part.get('tokens') if isinstance(part.get('tokens'), dict) else None}
        if isinstance(part.get('cost'), (int, float)) and not isinstance(part.get('cost'), bool):
            step['cost'] = part['cost']
        usage = log.metrics['provider_usage'] or []
        log.metrics['provider_usage'] = (usage + [step])[-USAGE_EVENTS_LIMIT:]
        if part.get('reason') != 'tool-calls':
            log.finish()


FENCED_JSON = re.compile(r'```json[ \t]*\r?\n(.*?)\r?\n?```', re.DOTALL | re.IGNORECASE)


def opencode_answer(folder, log):
    """The JSON object of the final fenced json block of the last text part. A missing or invalid block fails the run."""
    text = log.text()
    blocks = FENCED_JSON.findall(text)
    if not blocks:
        raise InvalidRecord('The opencode host did not end its answer with a JSON block, so the run has no report.')
    try:
        return json.loads(blocks[-1])
    except ValueError:
        raise InvalidRecord('The final JSON block of the opencode answer is not valid JSON, so the run has no report.') from None


# Usage extraction. Each function reads the provider_usage that the run log recorded and returns token counts by
# kind with None for a kind the host did not report. Cost is in US dollars when the host reports it.

USAGE_KINDS = ('input', 'output', 'reasoning', 'cache_read', 'cache_write', 'total')


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _add(totals, kind, value):
    number = _number(value)
    if number is not None:
        totals[kind] = (totals[kind] or 0) + number


def _empty_usage():
    return {**{kind: None for kind in USAGE_KINDS}, 'cost': None}


def _records(provider_usage):
    if isinstance(provider_usage, dict):
        return [provider_usage]
    if isinstance(provider_usage, list):
        return [item for item in provider_usage if isinstance(item, dict)]
    return []


def codex_usage(provider_usage):
    """Codex turn.completed usage: input_tokens includes cached_input_tokens, output_tokens includes reasoning."""
    totals = _empty_usage()
    for record in _records(provider_usage):
        _add(totals, 'input', record.get('input_tokens'))
        _add(totals, 'cache_read', record.get('cached_input_tokens'))
        _add(totals, 'output', record.get('output_tokens'))
        _add(totals, 'reasoning', record.get('reasoning_output_tokens'))
        _add(totals, 'total', record.get('total_tokens'))
    return totals


def claude_usage(provider_usage):
    """Claude result usage: input, output, cache creation and cache read tokens."""
    totals = _empty_usage()
    for record in _records(provider_usage):
        _add(totals, 'input', record.get('input_tokens'))
        _add(totals, 'output', record.get('output_tokens'))
        _add(totals, 'cache_read', record.get('cache_read_input_tokens'))
        _add(totals, 'cache_write', record.get('cache_creation_input_tokens'))
    return totals


GROK_USAGE_KEYS = {'inputtokens': 'input', 'prompttokens': 'input', 'outputtokens': 'output', 'completiontokens': 'output',
                   'reasoningtokens': 'reasoning', 'cachedtokens': 'cache_read', 'cachereadtokens': 'cache_read',
                   'totaltokens': 'total'}


def grok_usage(provider_usage):
    """Grok token fields of the end event, when it reports them, in camel case or snake case."""
    totals = _empty_usage()
    for record in _records(provider_usage):
        for key, value in record.items():
            kind = GROK_USAGE_KEYS.get(key.replace('_', '').lower()) if isinstance(key, str) else None
            if kind:
                _add(totals, kind, value)
    return totals


def opencode_usage(provider_usage):
    """OpenCode step_finish tokens and cost, summed over the steps of the run."""
    totals = _empty_usage()
    for record in _records(provider_usage):
        tokens = record.get('tokens') if isinstance(record.get('tokens'), dict) else {}
        for kind in ('input', 'output', 'reasoning', 'total'):
            _add(totals, kind, tokens.get(kind))
        cache = tokens.get('cache') if isinstance(tokens.get('cache'), dict) else {}
        _add(totals, 'cache_read', cache.get('read'))
        _add(totals, 'cache_write', cache.get('write'))
        _add(totals, 'cost', record.get('cost'))
    return totals


@dataclass(frozen=True)
class Profile:
    """A host program for the process runner. Command builders return the arguments with the bare program name."""
    name: str
    program: str
    environment_variable: str
    roles_before_probe: tuple
    roles_after_probe: tuple
    answer: str
    packet_in_input: bool
    isolation: str
    review: object
    work: object = None
    environment: object = None
    read_answer: object = None
    event: object = None
    usage: object = None
    install_paths: tuple = ()
    # The folder whose files later runs of the host load as configuration. The probe of the work role checks that a
    # worker cannot write there.
    configuration_home: object = None
    unavailable_patterns: tuple = ()
    until_patterns: tuple = ()

    def roles(self, probed=False):
        return self.roles_after_probe if probed else self.roles_before_probe


PROFILES = {
    'codex': Profile(
        name='codex', program='codex', environment_variable='PROJECT_MEMORY_CODEX_BIN',
        roles_before_probe=(REVIEW, WORK), roles_after_probe=(REVIEW, WORK),
        answer='output_file', packet_in_input=True,
        isolation=('Ephemeral session with hooks, plugins, apps, multi agent, memories, project instructions, skill '
                   'instructions and web search switched off by -c overrides, and every MCP server that the loaded '
                   'configuration defines disabled by name. Reviews run in the read-only sandbox, work in workspace-write.'),
        review=codex_review, work=codex_work, read_answer=codex_answer, usage=codex_usage),
    'claude': Profile(
        name='claude', program='claude', environment_variable='PROJECT_MEMORY_CLAUDE_BIN',
        roles_before_probe=(REVIEW, WORK), roles_after_probe=(REVIEW, WORK),
        answer='schema_flag', packet_in_input=False,
        isolation=('No setting sources, hooks disabled, a strict empty MCP configuration, slash commands disabled and no '
                   'session persistence. Reviews may use Read, Glob and Grep only; workers run shell commands inside the '
                   'operating system sandbox.'),
        review=claude_review, work=claude_work, read_answer=claude_answer, usage=claude_usage),
    'grok': Profile(
        name='grok', program='grok', environment_variable='PROJECT_MEMORY_GROK_BIN',
        roles_before_probe=(REVIEW,), roles_after_probe=(REVIEW, WORK),
        answer='schema_flag', packet_in_input=False,
        isolation=('The ten harness compatibility variables switch off Claude Code and Cursor skills, rules, agents, MCP '
                   'servers and hooks, GROK_MEMORY=0 and --no-memory switch off cross session memory, --no-subagents and '
                   '--disable-web-search remove delegation and the network tools, --tools allows only the listed built-in '
                   'tools and --deny MCPTool refuses every MCP tool. The user skills in ~/.grok/skills and ~/.agents/skills, '
                   'the MCP servers and hooks of ~/.grok and AGENTS.md project rules have no switch. A folder whose '
                   'repository holds .grok or .agents/skills is refused. Every Grok sandbox allows writes to ~/.grok, so '
                   'the probe of the work role must show that a worker cannot write there.'),
        review=grok_review, work=grok_work, environment=grok_environment, read_answer=grok_answer, event=grok_event,
        usage=grok_usage,
        install_paths=('.grok/bin/grok',),
        configuration_home=grok_home,
        unavailable_patterns=(
            ('authentication', re.compile(r'not signed in|run `?grok login', re.IGNORECASE)),
        )),
    'opencode': Profile(
        name='opencode', program='opencode', environment_variable='PROJECT_MEMORY_OPENCODE_BIN',
        roles_before_probe=(REVIEW,), roles_after_probe=(REVIEW,),
        answer='final_json_block', packet_in_input=False,
        isolation=('XDG_CONFIG_HOME points at an empty folder, so the global configuration, its MCP servers, agents, '
                   'plugins and instructions are not read; OPENCODE_DISABLE_PROJECT_CONFIG skips the project configuration '
                   'and instructions; OPENCODE_DISABLE_CLAUDE_CODE and OPENCODE_DISABLE_EXTERNAL_SKILLS skip Claude Code '
                   'prompts and skills; --pure skips external plugins; the generated configuration in OPENCODE_CONFIG '
                   'defines no MCP server and denies edit, shell, web, task, skill and question tools. OpenCode lists no '
                   'sandbox, so it takes no work role in this wave.'),
        review=opencode_review, environment=opencode_environment, read_answer=opencode_answer, event=opencode_event,
        usage=opencode_usage,
        install_paths=('.opencode/bin/opencode',),
        unavailable_patterns=(
            ('usage_limit', re.compile(r'GoUsageLimitError|FreeUsageLimitError')),
            ('authentication', re.compile(r'ProviderAuthError')),
        ),
        until_patterns=(
            re.compile(r'(?:reset|retry) in\s+(\d+(?:\.\d+)?)\s*(days?|d|hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\b',
                       re.IGNORECASE),
        )),
}
NAMES = tuple(PROFILES)


def get(name):
    """The profile of a host, or InvalidRecord naming the known hosts."""
    profile = PROFILES.get(name) if isinstance(name, str) else None
    if profile is None:
        raise InvalidRecord('The agent host must be ' + ', '.join(NAMES[:-1]) + ' or ' + NAMES[-1] + '.', allowed=list(NAMES))
    return profile


def work_hosts():
    """The hosts whose profiles allow work without a probe, in profile order."""
    return tuple(name for name, profile in PROFILES.items() if WORK in profile.roles_before_probe)


def summary():
    """Every profile as plain data: program, override variable, roles before and after a probe, answer and isolation."""
    return [{'host': profile.name, 'program': profile.program, 'environment_variable': profile.environment_variable,
             'roles_before_probe': list(profile.roles_before_probe), 'roles_after_probe': list(profile.roles_after_probe),
             'answer': profile.answer, 'isolation': profile.isolation} for profile in PROFILES.values()]
