"""Agent host executables, command lines, run supervision, log parsing and availability.

Every host is described by its profile in host_profiles.py; this module reads the profiles instead of branching on
host names.

Callers build a command line here and run it through Supervisor, which owns the
one loop that agent checks and delegated work share. Run logs are read in
bounded chunks, and metrics never copy command output or host messages; a
detected unavailability is reported as a short category and an optional time.
"""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import sqlite3
import subprocess
import tempfile
import time
import uuid

from .core import InvalidRecord, Memory as ProjectMemory, MemoryError as ProjectMemoryError, _text, _time
from . import codex_host, host_profiles
# Names that callers and tests read from hosts; the profiles own them.
from .host_profiles import HIVE_NAME_TAKEN, HIVE_SERVER, HIVE_TOOLS, WORK_SETTINGS, WORK_TOOLS, hive_server, toml_value  # noqa: F401

# The hosts that may work without a probe. Availability and choice default to them; every profile is a known host.
HOSTS = host_profiles.work_hosts()
KNOWN_HOSTS = host_profiles.NAMES
ENVIRONMENT = {name: profile.environment_variable for name, profile in host_profiles.PROFILES.items()}
UNAVAILABLE_WITHOUT_UNTIL = timedelta(minutes=60)
STDERR_TAIL_BYTES = 16 * 1024

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
    return host_profiles.get(host).name


def profile(host):
    """The profile of a known host."""
    return host_profiles.get(host)


def roles(host, probed=False):
    """The roles a host may take, before or after a passing probe for its installed version."""
    return profile(host).roles(probed)


def allows(host, role, probed=False):
    """True when the profile of the host allows the role: review for every check, work for delegated work."""
    return (host_profiles.WORK if role == 'work' else host_profiles.REVIEW) in roles(host, probed)


def _override(host):
    return os.environ.get(ENVIRONMENT[host], '').strip()


def _installed_copy(found):
    """The first install location of the profile below the home folder, or None."""
    for relative in found.install_paths:
        path = Path.home() / relative
        if path.is_file():
            return str(path)
    return None


def executable(host):
    """Return the host program from the environment override, the search path or the install location of its profile."""
    found = profile(host)
    override = _override(found.name)
    if override:
        return override
    return shutil.which(found.program) or _installed_copy(found)


def review_command(host, project, folder, prompt):
    """Build the read-only review command line.

    An environment override replaces the program name, so a host that
    availability() reports as installed only through the override is also the
    program that runs. A program that is not on the search path but sits in the
    install location of its profile runs from there.
    """
    args = _review_arguments(host, project, folder, prompt)
    found = profile(host)
    program = _override(found.name)
    if not program and found.install_paths and not shutil.which(found.program):
        program = _installed_copy(found)
    if program:
        args[0] = program
    return args


def _review_arguments(host, project, folder, prompt):
    return profile(host).review(project, Path(folder), prompt)


def review_environment(host, project, folder, prompt):
    """The variables a review run adds to the inherited environment, or an empty mapping.

    A profile may write its generated configuration into the run folder here, so call it once per run.
    """
    found = profile(host)
    if found.environment is None:
        return {}
    return found.environment(project, Path(folder), prompt)


def work_command(host, worktree, folder, prompt, hive=None):
    """Build the command line for delegated work that may edit files inside the worktree.

    Hooks and every configured MCP server stay disabled. A run that belongs to a swarm passes
    hive, the binding of hive_server, and receives exactly one MCP server: the hive server.
    A host whose profile has no work builder is refused.
    """
    found = profile(host)
    if found.work is None:
        raise InvalidRecord(f'The {found.name} host does not take delegated work. Select a host whose profile allows work.')
    args = found.work(worktree, Path(folder), prompt, hive=hive)
    program = executable(found.name)
    if program:
        args[0] = program
    return args


def work_environment(host, worktree, folder, prompt):
    """The variables a work run adds to the inherited environment, or an empty mapping."""
    found = profile(host)
    if found.environment is None:
        return {}
    return found.environment(worktree, Path(folder), prompt)


def usage(host, provider_usage):
    """Token counts by kind and cost from the provider_usage of a run of the host; None marks a kind not reported."""
    return profile(host).usage(provider_usage)


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


def _profile_or_none(host):
    return host_profiles.PROFILES.get(host) if isinstance(host, str) else None


def parse_until(text, now=None, host=None):
    """Return the ISO time when a host says it will accept work again, or None.

    A time that cannot be represented, for example a wait of millions of hours,
    is treated as unknown rather than raising. A named host adds the relative
    wordings of its profile after the shared ones.
    """
    now = now or datetime.now(timezone.utc)
    found = _profile_or_none(host)
    for relative in (RELATIVE_UNTIL,) + (found.until_patterns if found else ()):
        match = relative.search(text)
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


def unavailable(text, now=None, host=None):
    """Return {'reason','until'} when the text reports a limit or missing login, otherwise None.

    A named host adds the wordings of its profile after the shared patterns.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    found = _profile_or_none(host)
    for reason, pattern in UNAVAILABLE_PATTERNS + (found.unavailable_patterns if found else ()):
        if pattern.search(text):
            return {'reason': reason, 'until': parse_until(text, now, host)}
    return None


class RunLog:
    """Incremental reader for host JSON event logs and their standard error file.

    Without a host it reads the Codex and Claude events. A named host adds the events of its profile.
    Answer text is kept on the reader for the structured answer and never copied into the metrics.
    """

    def __init__(self, folder, host=None):
        self.folder = folder
        self.host = host
        self.profile = _profile_or_none(host)
        self.answer_text = ''
        self.offset = 0
        self.pending = b''
        self.discarding = False
        self.last_size = (0, 0)
        self.result = None
        # True after a successful turn.completed or result that no later error followed.
        self.completed = False
        self.metrics = {'phase':'starting', 'last_event':None, 'last_activity_at':None,
                        'completed_inspections':0, 'failed_inspections':0, 'active_inspections':0,
                        'host_error_events':0, 'unrecovered_error_events':0, 'reconnect_events':0, 'unparsed_events':0,
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
            self.metrics['unrecovered_error_events'] += 1
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
            self.finish()
        if self.profile and self.profile.event:
            self.profile.event(self, kind, value)

    def finish(self):
        """Record a successful completion of the host's turn."""
        self.metrics['phase'] = 'report_received'
        # Retry notices before a successful completion were transient; the run itself succeeded.
        self.completed = True
        # A later successful completion recovers every earlier error, such as a stream that reconnected.
        self.metrics['unrecovered_error_events'] = 0
        self.metrics.pop('host_unavailable', None)

    def append_text(self, chunk):
        """Add a streamed chunk of answer text, keeping at most the last TEXT_LIMIT characters."""
        self.answer_text = (self.answer_text + chunk)[-host_profiles.TEXT_LIMIT:]

    def replace_text(self, text):
        """Keep a complete text part of the answer as the latest one, at most TEXT_LIMIT characters."""
        self.answer_text = text[-host_profiles.TEXT_LIMIT:]

    def text(self):
        return self.answer_text

    def detect(self, text):
        """Record a host unavailability found in text; a finding with a time replaces one without."""
        if self.completed:
            return
        found = unavailable(text, host=self.host)
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


class Supervisor:
    """Run one host process with progress reporting, cancellation, deadline and termination handling.

    Agent checks and delegated work share this loop. `status` returns the state
    of the run, so a cancellation terminates the process, and `flush` records the
    metrics while the host works. The caller always calls stop() in a finally
    block, so a process is terminated even after an error.
    """

    def __init__(self, log, metrics, *, timeout, started, cancelled_message, timeout_message, status, flush):
        self.log = log
        self.metrics = metrics
        self.timeout = timeout
        self.started = started
        self.cancelled_message = cancelled_message
        self.timeout_message = timeout_message
        self.status = status
        self.flush = flush
        self.process = None

    @property
    def returncode(self):
        return self.process.returncode if self.process else None

    def run(self, args, *, cwd, folder, env=None):
        """Start the host with prompt.txt as input and wait. Return (state, error) when stopped early, otherwise (None, '').

        env holds variables added to the inherited environment; without them the host inherits it unchanged.
        """
        metrics = self.metrics
        state, error = None, ''
        environment = {**os.environ, **env} if env else None
        with (folder/'output.jsonl').open('w') as out, (folder/'stderr.log').open('w') as err, (folder/'prompt.txt').open() as incoming:
            self.process = subprocess.Popen(args, cwd=cwd, stdin=incoming, stdout=out, stderr=err, env=environment,
                                            text=True, start_new_session=os.name != 'nt')
            flushed = 0
            while self.process.poll() is None:
                metrics.update(self.log.read())
                elapsed = time.monotonic()-self.started
                metrics.update(duration_ms=round(elapsed*1000), remaining_seconds=round(max(0, self.timeout-elapsed), 1))
                if self.status() == 'cancelling':
                    state, error = 'cancelled', self.cancelled_message
                    metrics['termination_reason'] = 'cancelled'
                    break
                if elapsed >= self.timeout:
                    state, error = 'timed_out', self.timeout_message
                    metrics['termination_reason'] = 'execution_deadline'
                    break
                if elapsed >= flushed:
                    self.flush(metrics)
                    flushed = elapsed+2
                time.sleep(min(.25, max(0, self.timeout-elapsed)))
            if self.process.poll() is not None and metrics['termination_reason'] is None:
                metrics['termination_reason'] = 'completed' if self.process.returncode == 0 else 'host_exit'
        return state, error

    def stop(self):
        """Terminate the process group when the host is still running."""
        process = self.process
        if not process or process.poll() is not None:
            return
        try:
            if os.name == 'nt':
                process.terminate()
            else:
                os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                if os.name == 'nt':
                    process.kill()
                else:
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        except ProcessLookupError:
            process.wait()


def host_answer(host, folder, log):
    """Return the structured answer a host produced, or None. A profile may refuse a missing or invalid answer."""
    found = _profile_or_none(host) or profile('claude')
    return found.read_answer(Path(folder), log)


def unavailable_error(host, found):
    text = f'The {host} host did not accept the run ({found["reason"].replace("_", " ")}).'
    if found.get('until'):
        text += f' It reports that it accepts work again at {found["until"]}.'
    return text + ' Project Memory records the host as unavailable.'


def unrecovered_errors(metrics):
    """Host errors that no later successful completion followed.

    A run log written before this count existed reports every host error, as it did then.
    """
    return metrics.get('unrecovered_error_events', metrics.get('host_error_events', 0))


def run_state(host, metrics, report, *, missing_report, exit_message):
    """The state, error text and termination reason after a host process, or None when the caller decides.

    A host that refused the work becomes host_unavailable; a host error or a
    missing report becomes failed; a run that finished with a valid report is
    completed, which the caller turns into its own final state.
    """
    reason = metrics['termination_reason']
    found = metrics.get('host_unavailable')
    if reason in {'completed', 'host_exit'} and found:
        return 'host_unavailable', unavailable_error(host, found), 'host_unavailable'
    if reason == 'completed':
        if unrecovered_errors(metrics):
            return 'failed', 'The host reported an error. Inspect its private event log.', 'host_error'
        if not report:
            return 'failed', metrics.get('report_error', missing_report), 'invalid_report'
        return 'completed', '', None
    if reason == 'host_exit':
        return 'failed', exit_message, None
    return None


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


# Routing by headroom (section 16.3).
#
# A host is constrained when a receipt of this project marks it unavailable, when its latest reported use reaches
# 85 percent in a window that has not reset, or when it hit a limit whose reset time has not passed. The last two
# facts come from the usage ledger of this machine, because limits belong to the account and not to one project.

ROUTE_REASONS = ('preferred', 'headroom', 'all_constrained', 'same_host', 'not_installed')


def ledger_headroom(names, now):
    """The headroom of each named host from the usage ledger of this machine, or None when the ledger cannot be read."""
    from . import usage
    try:
        return usage.headroom(now=now, hosts=tuple(names))
    except (ProjectMemoryError, OSError, sqlite3.Error, ValueError):
        return None


def _clock(value):
    """A stored time as a short reading of the time in UTC, or the text unchanged."""
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    except (AttributeError, ValueError):
        return str(value)


def _why(option):
    """The reasons that constrain one host, as one clause."""
    parts = []
    for reason in option['reasons']:
        if reason == 'not_installed':
            parts.append('it is not installed')
        elif reason == 'marked_unavailable':
            until = option.get('unavailable_until')
            parts.append('it is marked unavailable' + (f' until {_clock(until)}' if until else ''))
        elif reason == 'used_percent':
            resets = option.get('resets_at')
            parts.append(f'its reported use is {option["used_percent"]:g} percent of a window'
                         + (f' that resets at {_clock(resets)}' if resets else ' that has not reset'))
        elif reason == 'limit_hit':
            until = option.get('limit_hit_until')
            parts.append('it hit a usage limit' + (f' that resets at {_clock(until)}' if until else ' less than 60 minutes ago'))
    return ' and '.join(parts)


def _measure(option):
    """The headroom that decided a choice, as one clause, or an empty text when nothing was reported."""
    parts = []
    if option['used_percent'] is not None:
        parts.append(f'a reported use of {option["used_percent"]:g} percent')
    # A load of zero means no recorded use in the last 5 hours, which every host shares on a new ledger, so it adds nothing.
    if option['relative_load']:
        parts.append(f'a load in the last 5 hours of {option["relative_load"]:g} times its own median')
    return ' and '.join(parts)


# Claude and Grok never report a percentage. For ranking, such a host counts as half used: it follows a host that
# reports less than this and precedes a host that reports more, so a host that reports a high use does not win over a
# host that cannot report one.
UNREPORTED_PERCENT = 50


def _rank(option, order):
    """Constrained hosts last and a limit hit after them, then the lowest reported percentage, then the lowest load
    relative to the host's own 7 day median, then the configured order. A host without a reported percentage ranks as
    UNREPORTED_PERCENT. A host without a measured load follows the hosts whose load is measured, and a host with no
    recorded use in the last 5 hours has a load of zero."""
    percent = option['used_percent'] if option['used_percent'] is not None else UNREPORTED_PERCENT
    return (option['constrained'], 'limit_hit' in option['reasons'], percent,
            option['relative_load'] is None, option['relative_load'] or 0, order[option['host']])


def route(memory, preferred, *, allowed=None, exclude=(), headroom=None):
    """Choose a host by availability and headroom, and return the decision with its reason.

    The preferred host is kept unless it is constrained. Otherwise the installed and available host with the most
    headroom is chosen: the lowest reported percentage first, then the lowest load relative to its own median, then
    the configured order. When every such host is constrained by the ledger, the one with the most headroom still
    runs. A host that is not installed or that this project marked unavailable is never chosen. headroom maps a host
    to the value of usage.host_headroom; without it the usage ledger of this machine is read.
    """
    _host(preferred)
    allowed = list(HOSTS) if allowed is None else [_host(host) for host in allowed]
    candidates = [preferred] + [host for host in allowed if host != preferred]
    candidates = [host for host in candidates if host in allowed and host not in exclude]
    now = datetime.fromisoformat(memory.now())
    ledger = headroom if headroom is not None else ledger_headroom(candidates, now)
    reports = []
    options = []
    for host in candidates:
        report = availability(memory, host)
        reports.append(report)
        room = (ledger or {}).get(host) or {}
        reasons = [] if report['installed'] else ['not_installed']
        if not report['available']:
            reasons.append('marked_unavailable')
        reasons.extend(reason for reason in room.get('reasons') or [] if reason in ('used_percent', 'limit_hit'))
        options.append({'host': host, 'installed': report['installed'], 'available': report['available'],
                        'constrained': bool(reasons), 'reasons': reasons, 'unavailable_until': report['until'],
                        'used_percent': room.get('used_percent'), 'resets_at': room.get('resets_at'),
                        'limit_hit_until': (room.get('limit_hit') or {}).get('until'),
                        'relative_load': room.get('relative_load')})
    runnable = [option for option in options if option['installed'] and option['available']]
    if not runnable:
        raise InvalidRecord(
            'No configured agent host is available. Install a host, wait until its limit resets, or mark it available again.',
            availability=reports or [availability(memory, host) for host in HOSTS])
    order = {host: allowed.index(host) for host in candidates}
    first = options[0] if candidates and candidates[0] == preferred else None
    if first and not first['constrained']:
        chosen, reason = first, 'preferred'
        sentence = f'The preferred host {preferred} was chosen because it is not constrained.'
    else:
        chosen = min(runnable, key=lambda option: _rank(option, order))
        reason = 'all_constrained' if chosen['constrained'] else 'headroom'
        if first is None:
            sentence = f'The preferred host {preferred} is excluded from this choice.'
        else:
            sentence = f'The preferred host {preferred} was not chosen because {_why(first)}.'
        measure = _measure(chosen)
        if reason == 'headroom':
            sentence += f' The {chosen["host"]} host was chosen because it has the most headroom among the hosts that are not constrained'
        else:
            sentence += (f' Every other allowed host is constrained too, so the {chosen["host"]} host was chosen because '
                         f'it has the most headroom, although {_why(chosen)}')
        sentence += (f', with {measure}.' if measure else '.')
    return {'host': chosen['host'], 'preferred': preferred, 'reason': reason, 'sentence': sentence,
            'decided_at': memory.now(), 'ledger_read': ledger is not None, 'hosts': options}


def choose(memory, preferred, *, allowed=None, exclude=(), headroom=None):
    """Return the preferred host when it can run and is not constrained, otherwise the allowed host with the most headroom."""
    return route(memory, preferred, allowed=allowed, exclude=exclude, headroom=headroom)['host']


# Probe for the process runner (section 16.4).
#
# `project-memory host probe HOST` runs the real host and spends tokens, so only the user starts it. Each probe is
# recorded as a receipt in the machine memory, keyed by the host and its installed version, because the program and
# its sign in belong to this machine. The receipt holds check names, states and plain sentences, never host output.

PROBE_EVENT = 'HostProbeFinished'
PROBE_ANSWER = 'probe-ready'
PROBE_TIMEOUT = 300
PROBE_AGENT = 'probe'
PROBE_INSIDE = 'probe-inside.txt'
# A fixed time for the offline check of each canned limit message, and the reason and wait it must yield.
PROBE_NOW = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)
PROBE_LIMIT_MESSAGES = {
    'codex': ('You have hit your usage limit. Try again in 3 hours.', 'usage_limit', timedelta(hours=3)),
    'claude': ('Claude usage limit reached. Your limit resets at 2026-09-17T18:00:00Z.', 'usage_limit', timedelta(hours=6)),
    'grok': ('Rate limit exceeded for this account. Try again in 45 minutes.', 'rate_limit', timedelta(minutes=45)),
    'opencode': ('GoUsageLimitError: the monthly usage limit is reached. Reset in 2 hours.', 'usage_limit', timedelta(hours=2)),
}
PROBE_REVIEW_PROMPT = ('This is a conformance probe of Project Memory. Do not read, run or change anything. Return only JSON '
                       'that matches the schema: set answer to ' + PROBE_ANSWER + ', list in mcp_servers the name of every '
                       'MCP server whose tools you can call, and list in skills the name of every skill that is available '
                       'to you. Use an empty list when there is none.')
PROBE_REVIEW_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['answer', 'mcp_servers', 'skills'],
                       'properties': {'answer': {'type': 'string'}, 'mcp_servers': {'type': 'array', 'items': {'type': 'string'}},
                                      'skills': {'type': 'array', 'items': {'type': 'string'}}}}
PROBE_WORK_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['outside_write', 'inside_write', 'hive_logged'],
                     'properties': {'outside_write': {'type': 'string', 'enum': ['refused', 'succeeded', 'not_attempted']},
                                    'inside_write': {'type': 'boolean'}, 'hive_logged': {'type': 'boolean'}}}
PROBE_EVIDENCE = 'Return the probe answer now.'
_VERSIONS = {}
# A version that could not be read is read again after this many seconds, so identical calls in between agree.
VERSION_RETRY_SECONDS = 600


def installed_version(host):
    """The first line that `PROGRAM --version` prints, at most 120 characters, or None. It starts no model."""
    program = executable(host)
    if not program:
        return None
    try:
        stat = Path(program).stat()
        key = (program, stat.st_size, stat.st_mtime_ns)
    except OSError:
        key = (program, None, None)
    if key in _VERSIONS:
        version, checked = _VERSIONS[key]
        if version is not None or time.monotonic() - checked < VERSION_RETRY_SECONDS:
            return version
    try:
        result = subprocess.run([program, '--version'], capture_output=True, text=True, timeout=15, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        result = None
    lines = (result.stdout or result.stderr or '').strip().splitlines() if result is not None else []
    version = lines[0].strip()[:120] if result is not None and result.returncode == 0 and lines else None
    _VERSIONS[key] = (version, time.monotonic())
    return version


def probe_results(path=None):
    """The latest probe receipt of each host in the machine memory, read only. A missing memory holds none."""
    from . import machine
    target = machine.database_path(path)
    found = {}
    if not target.exists():
        return found
    with ProjectMemory(target, read_only=True) as store:
        if not codex_host.exists(store):
            return found
        rows = store.db.execute('SELECT created_at,payload FROM host_receipts WHERE event_name=? ORDER BY rowid DESC',
                                (PROBE_EVENT,)).fetchall()
    for row in rows:
        payload = json.loads(row['payload'])
        host = payload.get('host')
        if host in KNOWN_HOSTS and host not in found:
            found[host] = {**payload, 'probed_at': row['created_at']}
    return found


def probed(host, path=None):
    """True when the latest probe of the installed version of the host passed. Only a profile whose roles grow after a
    probe needs one, so any other host answers False without reading anything."""
    found = profile(host)
    if found.roles_before_probe == found.roles_after_probe:
        return False
    from . import machine
    target = machine.database_path(path)
    if not target.exists():
        return False
    try:
        with ProjectMemory(target, read_only=True) as store:
            if not codex_host.exists(store):
                return False
            rows = store.db.execute('SELECT payload FROM host_receipts WHERE event_name=? AND session_id=? ORDER BY rowid DESC',
                                    (PROBE_EVENT, 'probe:' + found.name)).fetchall()
    except (ProjectMemoryError, OSError, sqlite3.Error):
        return False
    if not rows:
        return False
    version = installed_version(found.name)
    for row in rows:
        payload = json.loads(row['payload'])
        if payload.get('version') == version:
            return bool(payload.get('passed')) and version is not None
    return False


def may_work(host, path=None):
    """True when the profile of the host allows work, before a probe or after a passing probe of its installed version."""
    found = profile(host)
    if host_profiles.WORK in found.roles_before_probe:
        return True
    return host_profiles.WORK in found.roles_after_probe and probed(found.name, path)


def _check(name, status, detail):
    return {'check': name, 'status': status, 'detail': detail}


def probe_limit_message(host):
    """Classify the canned limit message of the host offline, with no model call."""
    text, reason, wait = PROBE_LIMIT_MESSAGES[host]
    found = unavailable(text, now=PROBE_NOW, host=host)
    expected = (PROBE_NOW + wait).isoformat()
    if found and found['reason'] == reason and found['until'] == expected:
        return _check('limit_message', 'passed', f'A canned limit message was classified as {reason.replace("_", " ")} '
                                                 f'with its reset time.')
    return _check('limit_message', 'failed', f'A canned limit message was not classified as {reason.replace("_", " ")} '
                                             'with its reset time.')


def _probe_run(host, args, *, cwd, folder, env, timeout):
    """Run one probe process and return its metrics, its structured answer or None, and a sentence on any failure."""
    metrics = {'termination_reason': None}
    log = RunLog(folder, host)
    supervisor = Supervisor(log, metrics, timeout=timeout, started=time.monotonic(), cancelled_message='',
                            timeout_message='The probe run reached its time limit.', status=lambda: 'running',
                            flush=lambda values: None)
    error = ''
    try:
        stopped, message = supervisor.run(args, cwd=cwd, folder=folder, env=env)
        if stopped:
            error = message
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        error = 'The host could not start: ' + str(exc)
        metrics['termination_reason'] = 'worker_error'
    finally:
        supervisor.stop()
    metrics.update(log.read(final=True))
    answer = None
    try:
        answer = host_answer(host, folder, log)
    except (OSError, ValueError, InvalidRecord) as exc:
        metrics['report_error'] = str(exc)[:300]
    found = run_state(host, metrics, answer, missing_report='The host returned no structured answer.',
                      exit_message='The host process exited unsuccessfully.')
    if found and found[0] != 'completed':
        error = error or found[1]
    return metrics, (answer if isinstance(answer, dict) else None), error


def _prompt_file(found, folder, prompt):
    (folder / 'prompt.txt').write_text(prompt + '\n\n' + PROBE_EVIDENCE if found.packet_in_input else PROBE_EVIDENCE,
                                       encoding='utf-8')


def probe_review(found, root, timeout):
    """A small task that must return a structured answer, with usage extraction and, for a host that needs a probe
    before it works, the host's own report that no MCP server or skill is visible to it."""
    project = root / 'project'
    folder = root / 'review-run'
    project.mkdir()
    folder.mkdir()
    (project / 'README.md').write_text('# Probe\n\nThis folder holds nothing to review.\n', encoding='utf-8')
    (folder / 'schema.json').write_text(json.dumps(PROBE_REVIEW_SCHEMA), encoding='utf-8')
    _prompt_file(found, folder, PROBE_REVIEW_PROMPT)
    args = review_command(found.name, str(project), folder, PROBE_REVIEW_PROMPT)
    environment = review_environment(found.name, str(project), folder, PROBE_REVIEW_PROMPT)
    metrics, answer, error = _probe_run(found.name, args, cwd=str(project), folder=folder, env=environment, timeout=timeout)
    checks = []
    if answer and answer.get('answer') == PROBE_ANSWER:
        checks.append(_check('structured_answer', 'passed', 'The host returned a structured answer that matches the probe schema.'))
    else:
        checks.append(_check('structured_answer', 'failed', 'The host returned no valid structured answer. '
                             + (error or 'The answer did not match the probe schema.')))
    counts = usage(found.name, metrics.get('provider_usage'))
    kinds = [kind.replace('_', ' ') for kind in host_profiles.USAGE_KINDS if counts.get(kind) is not None]
    if kinds:
        checks.append(_check('usage', 'passed', 'Token usage was extracted from the event log: ' + ', '.join(kinds) + '.'))
    else:
        checks.append(_check('usage', 'failed', 'The event log of the run carried no token usage that the profile can read.'))
    if found.roles_before_probe != found.roles_after_probe:
        servers = answer.get('mcp_servers') if answer else None
        skills = answer.get('skills') if answer else None
        if isinstance(servers, list) and isinstance(skills, list) and not servers and not skills:
            checks.append(_check('no_user_configuration', 'passed', 'The host reported no MCP server and no skill in the probe '
                                 'run. This rests on the report of the host itself.'))
        elif answer is None:
            checks.append(_check('no_user_configuration', 'failed', 'The host returned no answer, so the probe cannot show '
                                 'that no MCP server or skill is visible to it.'))
        else:
            checks.append(_check('no_user_configuration', 'failed',
                                 f'The host reported {len(servers or [])} MCP servers and {len(skills or [])} skills in the probe '
                                 'run, so user configuration is visible to it.'))
    return checks, metrics


def _probe_hive(found, root):
    """A project memory and an open swarm that the worker of the probe joins, or None for a host that cannot receive the hive."""
    from . import hive
    if found.name not in hive.HOSTS:
        return None
    database = root / 'hive-project' / '.memory' / 'project.sqlite'
    database.parent.mkdir(parents=True)
    with ProjectMemory.create(database, 'Probe', ['The probe checks the hive server of a worker.']) as memory:
        path = hive.path_for(memory)
    with hive.Hive(path) as store:
        swarm = hive.open_swarm(store, title='Probe', purpose='The probe checks that a worker writes through the hive server.',
                                kind='manual', request_key='probe', blind=False)
        hive.join(store, swarm['id'], agent_id=PROBE_AGENT, role=PROBE_AGENT, host=found.name)
    return {'hive': str(path), 'db': str(database), 'swarm_id': swarm['id'], 'agent_id': PROBE_AGENT, 'role': PROBE_AGENT}


def _hive_entries(binding):
    from . import hive
    with hive.Hive(binding['hive'], read_only=True) as store:
        return store.db.execute('SELECT count(*) FROM entries WHERE swarm_id=? AND agent_id=?',
                                (binding['swarm_id'], binding['agent_id'])).fetchone()[0]


def probe_work(found, root, timeout, outside_folder):
    """A worker run that tries a shell write outside its worktree, writes one file inside it and, when the host can
    receive the hive server, logs one question through it. For a host whose profile names a configuration folder, the
    worker also tries a write there, and the probe file is removed afterwards whatever the outcome."""
    worktree = root / 'worktree'
    folder = root / 'work-run'
    worktree.mkdir()
    folder.mkdir()
    outside = outside_folder / 'outside.txt'
    binding = _probe_hive(found, root)
    steps = [f'Run this shell command once: printf probe > {shlex.quote(str(outside))} . It is expected to fail. Do not try '
             'another way to write that file.']
    home_file = None
    if found.configuration_home is not None:
        home = Path(found.configuration_home())
        home.mkdir(parents=True, exist_ok=True)
        home_file = home / ('project-memory-probe-' + uuid.uuid4().hex + '.txt')
        steps.append(f'Run this shell command once: printf probe > {shlex.quote(str(home_file))} . It is expected to fail. Do '
                     'not try another way to write that file.')
    steps.append(f'Create the file {PROBE_INSIDE} in the working directory with the text probe.')
    try:
        return _probe_work_run(found, worktree, folder, outside, binding, steps, home_file, timeout)
    finally:
        if home_file is not None:
            home_file.unlink(missing_ok=True)


def _probe_work_run(found, worktree, folder, outside, binding, steps, home_file, timeout):
    """Run the worker of the probe and turn what it wrote into checks."""
    if binding:
        steps.append('Call the hive_log tool once with move question, request_key probe, and fields with the claim '
                     '"Does the hive server accept the entry of this probe?" and the addressee user.')
    prompt = ('This is a conformance probe of Project Memory. Do these steps in order. '
              + ' '.join(f'{number}. {step}' for number, step in enumerate(steps, 1))
              + ' Then return only JSON that matches the schema.')
    (folder / 'schema.json').write_text(json.dumps(PROBE_WORK_SCHEMA), encoding='utf-8')
    _prompt_file(found, folder, prompt)
    args = work_command(found.name, str(worktree), folder, prompt, hive=binding)
    environment = work_environment(found.name, str(worktree), folder, prompt)
    metrics, answer, error = _probe_run(found.name, args, cwd=str(worktree), folder=folder, env=environment, timeout=timeout)
    checks = []
    if outside.exists():
        checks.append(_check('shell_write_outside_refused', 'failed',
                             'A shell write outside the worktree succeeded, so the sandbox does not confine writes.'))
    elif not (worktree / PROBE_INSIDE).exists():
        checks.append(_check('shell_write_outside_refused', 'failed',
                             'The worker did not write its file inside the worktree, so the probe cannot show that the sandbox '
                             'refused the write outside it. ' + (error or '')))
    else:
        checks.append(_check('shell_write_outside_refused', 'passed',
                             'A shell write outside the worktree was refused, and a write inside the worktree succeeded.'))
    if home_file is not None:
        if home_file.exists():
            checks.append(_check('configuration_home_write_refused', 'failed',
                                 'A shell write into the configuration folder of the host succeeded, so a worker could '
                                 'change the configuration that later runs load.'))
        elif not (worktree / PROBE_INSIDE).exists():
            checks.append(_check('configuration_home_write_refused', 'failed',
                                 'The worker did not write its file inside the worktree, so the probe cannot show that the '
                                 'sandbox refused the write into the configuration folder of the host.'))
        else:
            checks.append(_check('configuration_home_write_refused', 'passed',
                                 'A shell write into the configuration folder of the host was refused.'))
    if binding is None:
        checks.append(_check('hive_write_accepted', 'not_applicable',
                             f'The {found.name} host cannot receive the hive server on its command line, so it takes no work '
                             'of a swarm.'))
    elif _hive_entries(binding):
        checks.append(_check('hive_write_accepted', 'passed', 'A hive write through the hive server was accepted.'))
    else:
        checks.append(_check('hive_write_accepted', 'failed', 'No hive write through the hive server was recorded.'))
    return checks, metrics


def _record_probe(result, path):
    from . import machine
    target = machine.database_path(path)
    machine.initialize(target)
    with machine.writer(target) as store:
        if not codex_host.exists(store):
            codex_host.initialize(store)
        with store._write():
            return codex_host.receipt(store, session_id='probe:' + result['host'], event_name=PROBE_EVENT, payload=result,
                                      key='probe:' + result['host'] + ':' + uuid.uuid4().hex)


def probe(host, *, path=None, timeout=PROBE_TIMEOUT):
    """Run the conformance probe of one host with the process runner and record it in the machine memory.

    This starts the real host and spends tokens. The checks are: the canned limit message (offline), a structured
    answer from a small task, usage extraction, for a profile that needs a probe before it works no MCP server or skill
    visible to it, and for a profile that may work a shell write outside the worktree refused and a hive write through
    the hive server accepted. The work role that a probe unlocks requires every applicable check to pass.
    """
    from . import machine
    found = profile(host)
    if not executable(found.name):
        raise InvalidRecord(f'The {found.name} host is not installed, so it cannot be probed. Install it or set '
                            f'{found.environment_variable} to its program.')
    version = installed_version(found.name)
    started = time.monotonic()
    checks = [probe_limit_message(found.name)]
    measured = {}
    outside_folder = machine.database_path(path).parent / ('probe-' + uuid.uuid4().hex)
    outside_folder.mkdir(parents=True)
    try:
        with tempfile.TemporaryDirectory(prefix='project-memory-probe-') as folder:
            root = Path(folder).resolve()
            review_checks, metrics = probe_review(found, root, timeout)
            checks.extend(review_checks)
            measured['review_duration_ms'] = metrics.get('duration_ms')
            if host_profiles.WORK in found.roles_after_probe:
                work_checks, metrics = probe_work(found, root, timeout, outside_folder)
                checks.extend(work_checks)
                measured['work_duration_ms'] = metrics.get('duration_ms')
    finally:
        shutil.rmtree(outside_folder, ignore_errors=True)
    passed = version is not None and all(check['status'] != 'failed' for check in checks)
    result = {'host': found.name, 'version': version, 'runner': 'process', 'passed': passed,
              'roles': list(found.roles(passed)), 'checks': checks, 'measured': measured,
              'seconds': round(time.monotonic() - started, 1)}
    if version is None:
        result['note'] = 'The installed version could not be read, so the probe cannot unlock a role.'
    result['receipt'] = _record_probe(result, path)
    return result
