"""Agent host executables, command lines, run log parsing and host availability.

This module never starts a host process. Callers build a command line here and
run it themselves. Run logs are read in bounded chunks, and metrics never copy
command output or host messages; a detected unavailability is reported as a
short category and an optional time.
"""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import shutil
import uuid

from .core import InvalidRecord, _text, _time
from . import codex_host

HOSTS = ('codex', 'claude')
ENVIRONMENT = {'codex': 'PROJECT_MEMORY_CODEX_BIN', 'claude': 'PROJECT_MEMORY_CLAUDE_BIN'}
UNAVAILABLE_WITHOUT_UNTIL = timedelta(minutes=60)
STDERR_TAIL_BYTES = 16 * 1024
WORK_TOOLS = 'Read,Glob,Grep,Edit,Write,Bash'

# Ordered from the most specific wording to the most general.
UNAVAILABLE_PATTERNS = (
    ('usage_limit', re.compile(r'usage[ _-]?limit', re.IGNORECASE)),
    ('quota', re.compile(r'insufficient[ _-]?quota|\bquota\b', re.IGNORECASE)),
    ('rate_limit', re.compile(r'rate[ _-]?limit|too many requests|\b(?:http|status|code|error)\D{0,12}429\b|\b429\b\s*(?:too|rate)', re.IGNORECASE)),
    ('authentication', re.compile(
        r'not logged in|please (?:run )?/?login|authentication[ _-]?(?:error|failed|failure|required)|failed to authenticate'
        r'|invalid (?:api|x-api) key|\b(?:http|status|code|error)\D{0,12}401\b|\b401\b\s*unauthori[sz]ed', re.IGNORECASE)),
)
RELATIVE_UNTIL = re.compile(r'try again in\s+(\d+(?:\.\d+)?)\s*(days?|d|hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\b', re.IGNORECASE)
ISO_UNTIL = re.compile(r'reset\w*\b\D{0,30}?(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)', re.IGNORECASE)
EPOCH_UNTIL = re.compile(r'\|\s*(\d{10})\b')
UNITS = {'d': 'days', 'h': 'hours', 'm': 'minutes', 's': 'seconds'}


def _host(host):
    if host not in HOSTS:
        raise InvalidRecord('The agent host must be codex or claude.', allowed=list(HOSTS))
    return host


def executable(host):
    """Return the host program from the environment override or the search path."""
    override = os.environ.get(ENVIRONMENT[_host(host)], '').strip()
    if override:
        return override
    return shutil.which(host)


def _codex_config_overrides(project):
    """Return the configured model and the arguments that disable every configured MCP server."""
    import tomllib
    config_path = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'config.toml'
    config = tomllib.loads(config_path.read_text()) if config_path.exists() else {}
    model = []
    if isinstance(config.get('model'), str):
        model = ['-m', config['model']]
    servers = []
    for path in [config_path, *(p / '.codex/config.toml' for p in reversed([Path(project), *Path(project).parents]))]:
        if path.exists():
            for name in tomllib.loads(path.read_text()).get('mcp_servers', {}):
                if not re.fullmatch(r'[A-Za-z0-9_-]+', name):
                    raise InvalidRecord('The review host cannot safely disable the MCP server named ' + name + '.')
                servers += ['-c', 'mcp_servers.' + name + '.enabled=false']
    return model, servers


def review_command(host, project, folder, prompt):
    """Build the read-only review command line.

    The arguments are the former reviews.command body. An environment override
    replaces the program name, so a host that availability() reports as
    installed only through the override is also the program that runs.
    """
    args = _review_arguments(host, project, folder, prompt)
    override = os.environ.get(ENVIRONMENT[host], '').strip() if host in ENVIRONMENT else ''
    if override:
        args[0] = override
    return args


def _review_arguments(host, project, folder, prompt):
    if host == 'codex':
        model, servers = _codex_config_overrides(project)
        overrides = model + servers
        return ['codex', 'exec', '--ephemeral', '--skip-git-repo-check', '--sandbox', 'read-only',
                '-C', project, '-c', 'approval_policy="never"', '-c', 'features.hooks=false', '-c', 'features.plugins=false',
                '-c', 'features.apps=false', '-c', 'features.multi_agent=false', '-c', 'project_doc_max_bytes=0',
                '-c', 'memories.use_memories=false', '-c', 'memories.generate_memories=false',
                '-c', 'skills.include_instructions=false', '-c', 'web_search="disabled"', *overrides,
                '--output-schema', str(folder / 'schema.json'), '--output-last-message', str(folder / 'answer.json'), '--json', '-']
    return ['claude', '-p', '--restricted', '--no-session-persistence', '--output-format', 'stream-json', '--verbose', '--permission-mode', 'dontAsk',
            '--setting-sources', '', '--settings', '{"disableAllHooks":true}', '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
            '--disable-slash-commands', '--tools', 'Read,Glob,Grep', '--allowedTools', 'Read,Glob,Grep',
            '--json-schema', (folder / 'schema.json').read_text(), '--system-prompt', prompt]


def work_command(host, worktree, folder, prompt):
    """Build the command line for delegated work that may edit files inside the worktree."""
    _host(host)
    folder = Path(folder)
    if host == 'codex':
        _, servers = _codex_config_overrides(worktree)
        args = ['codex', 'exec', '--ephemeral', '--skip-git-repo-check', '--sandbox', 'workspace-write',
                '-C', str(worktree), '-c', 'approval_policy="never"', '-c', 'features.hooks=false', '-c', 'features.plugins=false',
                '-c', 'features.apps=false', '-c', 'features.multi_agent=false',
                '-c', 'memories.use_memories=false', '-c', 'memories.generate_memories=false',
                '-c', 'web_search="disabled"', *servers,
                '--output-schema', str(folder / 'schema.json'), '--output-last-message', str(folder / 'answer.json'), '--json', '-']
    else:
        args = ['claude', '-p', '--restricted', '--no-session-persistence', '--output-format', 'stream-json', '--verbose', '--permission-mode', 'dontAsk',
                '--setting-sources', '', '--settings', '{"disableAllHooks":true}', '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
                '--disable-slash-commands', '--tools', WORK_TOOLS, '--allowedTools', WORK_TOOLS,
                '--json-schema', (folder / 'schema.json').read_text(), '--system-prompt', prompt]
    program = executable(host)
    if program:
        args[0] = program
    return args


def _strings(value, limit=64):
    """Collect bounded string values from an event, depth first."""
    found = []
    stack = [value]
    while stack and len(found) < limit:
        item = stack.pop()
        if isinstance(item, str):
            found.append(item[:4000])
        elif isinstance(item, dict):
            stack.extend(reversed(list(item.values())))
        elif isinstance(item, list):
            stack.extend(reversed(item[:100]))
    return found


def parse_until(text, now=None):
    """Return the ISO time when a host says it will accept work again, or None.

    A time that cannot be represented, for example a wait of millions of hours,
    is treated as unknown rather than raising.
    """
    now = now or datetime.now(timezone.utc)
    match = RELATIVE_UNTIL.search(text)
    if match:
        unit = UNITS[match[2][0].lower()]
        try:
            return (now + timedelta(**{unit: float(match[1])})).isoformat()
        except (OverflowError, ValueError):
            return None
    match = ISO_UNTIL.search(text)
    if match:
        value = match[1].replace(' ', 'T')
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat()
        except (OverflowError, ValueError):
            pass
    match = EPOCH_UNTIL.search(text)
    if match:
        try:
            return datetime.fromtimestamp(int(match[1]), timezone.utc).isoformat()
        except (OverflowError, ValueError, OSError):
            return None
    return None


def unavailable(text, now=None):
    """Return {'reason','until'} when the text reports a limit or missing login, otherwise None."""
    if not isinstance(text, str) or not text.strip():
        return None
    for reason, pattern in UNAVAILABLE_PATTERNS:
        if pattern.search(text):
            return {'reason': reason, 'until': parse_until(text, now)}
    return None


class RunLog:
    """Incremental reader for host JSON event logs and their standard error file."""

    def __init__(self, folder):
        self.folder = folder
        self.offset = 0
        self.pending = b''
        self.discarding = False
        self.last_size = (0, 0)
        self.result = None
        # True after a successful turn.completed or result that no later error followed.
        self.completed = False
        self.metrics = {'phase':'starting', 'last_event':None, 'last_activity_at':None,
                        'completed_inspections':0, 'failed_inspections':0, 'active_inspections':0,
                        'host_error_events':0, 'reconnect_events':0, 'unparsed_events':0,
                        'output_bytes':0, 'stderr_bytes':0, 'provider_usage':None, 'model':None}

    def event(self, value):
        if not isinstance(value,dict):
            self.metrics['unparsed_events'] += 1
            return
        kind = value.get('type','unknown')
        if not isinstance(kind,str): kind = 'unknown'
        self.metrics['last_event'] = kind[:100]
        if kind in {'error','turn.failed'} or value.get('is_error'):
            self.metrics['host_error_events'] += 1
            self.metrics['phase'] = 'host_error'
            self.completed = False
        if kind in {'error','turn.failed'} or (kind=='result' and value.get('is_error')):
            self.detect(' '.join(_strings(value)))
        if kind in {'reconnect','reconnecting','connection.reconnecting'}:
            self.metrics['reconnect_events'] += 1
        if isinstance(value.get('model'),str): self.metrics['model'] = value['model'][:180]
        # Codex emits aggregate usage at turn completion; Claude emits it in result.
        if isinstance(value.get('usage'),dict) and kind in {'turn.completed','result'}:
            if kind=='result':
                self.metrics['provider_usage'] = value['usage']
            else:
                usage = self.metrics['provider_usage'] or []
                self.metrics['provider_usage'] = (usage+[value['usage']])[-100:]
        item = value.get('item')
        if isinstance(item,dict) and item.get('type')=='command_execution':
            if kind=='item.started':
                self.metrics['active_inspections'] += 1
            elif kind=='item.completed':
                self.metrics['active_inspections'] = max(0,self.metrics['active_inspections']-1)
                self.metrics['completed_inspections'] += 1
                if item.get('exit_code') not in (None,0): self.metrics['failed_inspections'] += 1
            self.metrics['phase'] = 'inspecting' if self.metrics['active_inspections'] else 'awaiting_host'
        if kind=='assistant':
            message = value.get('message',{})
            if isinstance(message.get('model'),str): self.metrics['model'] = message['model'][:180]
            for block in message.get('content',[]):
                if isinstance(block,dict) and block.get('type')=='tool_use':
                    self.metrics['active_inspections'] += 1
                    self.metrics['phase'] = 'inspecting'
        if kind=='user':
            for block in value.get('message',{}).get('content',[]):
                if isinstance(block,dict) and block.get('type')=='tool_result':
                    self.metrics['active_inspections'] = max(0,self.metrics['active_inspections']-1)
                    self.metrics['completed_inspections'] += 1
                    if block.get('is_error'): self.metrics['failed_inspections'] += 1
                    self.metrics['phase'] = 'inspecting' if self.metrics['active_inspections'] else 'awaiting_host'
        if kind in {'turn.started','thread.started','system'} and not self.metrics['active_inspections']:
            self.metrics['phase'] = 'awaiting_host'
        if kind=='result': self.result = value
        if kind in {'turn.completed','result'} and not value.get('is_error'):
            self.metrics['phase'] = 'report_received'
            # Retry notices before a successful completion were transient; the run itself succeeded.
            self.completed = True
            self.metrics.pop('host_unavailable', None)

    def detect(self, text):
        """Record a host unavailability found in text; a finding with a time replaces one without."""
        if self.completed:
            return
        found = unavailable(text)
        if not found:
            return
        prior = self.metrics.get('host_unavailable')
        if prior is None or (found['until'] and not prior.get('until')):
            self.metrics['host_unavailable'] = found

    def read_stderr_tail(self):
        path = self.folder/'stderr.log'
        if not path.exists():
            return ''
        with path.open('rb') as stream:
            size = stream.seek(0, os.SEEK_END)
            stream.seek(max(0, size-STDERR_TAIL_BYTES))
            return stream.read().decode('utf-8', errors='replace')

    def read(self, final=False):
        output, stderr = self.folder/'output.jsonl', self.folder/'stderr.log'
        stats = [p.stat() if p.exists() else None for p in (output,stderr)]
        sizes = tuple(s.st_size if s else 0 for s in stats)
        changed = [s.st_mtime for old,new,s in zip(self.last_size,sizes,stats) if s and new!=old]
        if changed:
            self.metrics['last_activity_at'] = datetime.fromtimestamp(max(changed),timezone.utc).isoformat()
        self.last_size = sizes
        self.metrics.update(output_bytes=sizes[0], stderr_bytes=sizes[1])
        if output.exists():
            with output.open('rb') as stream:
                stream.seek(self.offset)
                while True:
                    chunk = stream.read(1024*1024)
                    self.offset += len(chunk)
                    self.pending += chunk
                    while b'\n' in self.pending:
                        line, self.pending = self.pending.split(b'\n',1)
                        if self.discarding:
                            self.discarding = False
                        else:
                            self.parse(line)
                    if len(self.pending)>4*1024*1024:
                        self.pending = b''
                        if not self.discarding: self.metrics['unparsed_events'] += 1
                        self.discarding = True
                    if not final or not chunk: break
        if final and self.pending and not self.discarding:
            self.parse(self.pending)
            self.pending = b''
        if final:
            try:
                self.detect(self.read_stderr_tail())
            except OSError:
                pass
        last = self.metrics['last_activity_at']
        self.metrics['seconds_since_activity'] = round(max(0,(datetime.now(timezone.utc)-datetime.fromisoformat(last)).total_seconds()),1) if last else None
        return dict(self.metrics)

    def parse(self, line):
        if not line.strip(): return
        if len(line)>4*1024*1024:
            self.metrics['unparsed_events'] += 1
            return
        try:
            value = json.loads(line)
            self.event(value)
        except (ValueError,TypeError,AttributeError):
            self.metrics['unparsed_events'] += 1


ReviewLog = RunLog


def _record(memory, host, event_name, payload):
    if not codex_host.exists(memory):
        codex_host.initialize(memory)
    key = 'host-availability:' + host + ':' + uuid.uuid4().hex
    with memory._write():
        return codex_host.receipt(memory, session_id='host:' + host, event_name=event_name, payload=payload, key=key)


def mark_unavailable(memory, host, reason, until=None):
    """Record that a host refused work, for example because of a usage limit."""
    _host(host)
    _text(reason, 'reason', 500)
    payload = {'host': host, 'reason': reason, 'until': _time(until) if until is not None else None}
    return _record(memory, host, 'HostUnavailable', payload)


def mark_available(memory, host):
    """Record that a host accepts work again."""
    _host(host)
    return _record(memory, host, 'HostAvailable', {'host': host})


def availability(memory, host):
    """Report whether a host is installed and whether a recent receipt marks it unavailable."""
    _host(host)
    result = {'host': host, 'installed': executable(host) is not None, 'available': True, 'until': None, 'reason': None}
    if not codex_host.exists(memory):
        return result
    rows = memory.db.execute(
        "SELECT event_name,created_at,payload FROM host_receipts WHERE session_id=? AND event_name IN ('HostUnavailable','HostAvailable') ORDER BY rowid DESC LIMIT 1",
        ('host:' + host,)).fetchall()
    if not rows or rows[0]['event_name'] != 'HostUnavailable':
        return result
    payload = json.loads(rows[0]['payload'])
    now = datetime.fromisoformat(memory.now())
    until = payload.get('until')
    if until:
        active = datetime.fromisoformat(until) > now
    else:
        active = now - datetime.fromisoformat(rows[0]['created_at']) < UNAVAILABLE_WITHOUT_UNTIL
    if active:
        result.update(available=False, until=until, reason=payload.get('reason'))
    return result


def choose(memory, preferred, *, allowed=None, exclude=()):
    """Return the preferred host when it can run, otherwise another allowed host."""
    _host(preferred)
    allowed = list(HOSTS) if allowed is None else [_host(host) for host in allowed]
    candidates = [preferred] + [host for host in allowed if host != preferred]
    candidates = [host for host in candidates if host in allowed and host not in exclude]
    reports = []
    for host in candidates:
        report = availability(memory, host)
        reports.append(report)
        if report['installed'] and report['available']:
            return host
    raise InvalidRecord(
        'No configured agent host is available. Install a host, wait until its limit resets, or mark it available again.',
        availability=reports or [availability(memory, host) for host in HOSTS])
