"""The usage ledger of this machine: tokens, cost and limit state per host (section 16.2).

Usage limits belong to the account on this machine, not to one project, so the
ledger lives in the machine memory. It is filled from four sources:

- the delegated and review runs of every registered project, from the provider
  usage and duration in their run metrics, and the limit hits that
  `hosts.unavailable` recorded as receipts;
- interactive Codex session logs, from their `token_count` events, which also
  carry the reported `rate_limits`;
- interactive Claude Code transcripts, from the `usage` of each assistant message;
- the JSON events of OpenCode runs, from their `step_finish` events with tokens
  and cost. Grok usage is recorded only when its run metrics carry it.

What the ledger stores: the host, the source kind (`run` or `session`), start
and end time, token counts by kind, cost and currency when reported, and for
each reported limit the latest used percentage, window and reset time. What it
never stores: message content, prompts, file paths, project names or session
titles. A log file is known by a digest of its file system identity and an
entry by a digest of what makes it unique.

Collection is incremental and idempotent. Each log file keeps the offset of the
last complete line read, and a file that is unchanged since the last collection
is not opened. A partial last line waits for the next collection, and a
malformed line is counted and skipped. The same entry found twice, for example
in a resumed transcript that repeats earlier messages, is recorded once.

Collection runs when the user asks for the usage and at the end of a Project
Memory run. There is no background service. Every read works on a read only
connection and on a machine memory that has no ledger yet.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import statistics
import time

from . import machine
from .core import Memory, MemoryError, dumps

HOSTS = ('codex', 'claude', 'grok', 'opencode')
KINDS = ('run', 'session')
TOKEN_FIELDS = ('input_tokens', 'cached_input_tokens', 'cache_write_tokens', 'output_tokens', 'reasoning_tokens',
                'total_tokens')
SOURCES = {'codex': 'codex_session', 'claude': 'claude_transcript'}
# A host whose latest reported use in an unexpired window reaches this percentage is constrained (section 16.3).
CONSTRAINED_PERCENT = 85
# A limit hit without a reset time counts for this long, as `hosts.availability` treats it.
LIMIT_HIT_WITHOUT_UNTIL = timedelta(minutes=60)
FIVE_HOURS = timedelta(hours=5)
SEVEN_DAYS = timedelta(days=7)
# The 5 hour windows that fit inside the last 7 days, used for the median of a host's own usage.
MEDIAN_WINDOWS = int(SEVEN_DAYS / FIVE_HOURS)
UNFINISHED_RUN_STATES = ('queued', 'running', 'cancelling')
READ_CHUNK = 4 * 1024 * 1024
HEAD_BYTES = 256
DIGEST_CHARACTERS = 32
LIMIT_IDENTIFIER = re.compile(r'[a-z0-9][a-z0-9_]{0,59}')
REASON = re.compile(r'[a-z][a-z_]{0,39}')
RUN_IDENTIFIER = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,199}')
CURRENCY = 'USD'
LIMIT_WINDOWS = ('primary', 'secondary')
# The largest count or amount kept from one report. A larger value is malformed, and keeping it would overflow the
# integers of SQLite when it is stored or summed.
MAXIMUM_COUNT = 10 ** 13
MAXIMUM_AMOUNT = 10.0 ** 9
# A limit observation more than this far after the time of collection comes from a wrong clock and is ignored, so it
# cannot keep newer genuine reports from replacing it.
FUTURE_TOLERANCE = timedelta(hours=1)
# A limit window reported without a reset time and without its length expires after the longest window a host reports.
WINDOW_WITHOUT_LENGTH = SEVEN_DAYS
# A HostAvailable receipt is stored among the limit hits with this reason, and it ends every earlier hit of its host.
CLEARED = 'marked_available'

# What each host can report, so the output states plainly which data are measured and which are unavailable.
UNAVAILABLE = {
    'codex': ['Codex reports no cost for a subscription, so cost is unavailable.'],
    'claude': ['Claude Code records no remaining quota in its transcripts, so the limit state is unavailable.',
               'Claude Code transcripts carry no cost, so cost is unavailable.'],
    'grok': ['Grok output carries no usage in the observed formats, so tokens are recorded only when a run reports them.',
             'Grok reports no limit state and no cost, so both are unavailable.'],
    'opencode': ['OpenCode usage is read from the events of Project Memory runs only.',
                 'OpenCode reports no limit state, so it is unavailable.'],
}
NO_LEDGER_NOTE = ('No usage has been collected on this machine yet. Run project-memory usage to collect it from the local '
                  'logs of each host.')
WINDOWS_NOTE = ('Token totals follow the counting of each provider: Codex includes cached input in its input tokens, '
                'while Claude Code and OpenCode count cached input separately. Totals of different hosts are therefore '
                'not directly comparable.')


# Small value helpers.

def _digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:DIGEST_CHARACTERS]


def _digest_bytes(data):
    return hashlib.sha256(data).hexdigest()[:DIGEST_CHARACTERS]


def _count(value):
    """A non negative whole number, or None. A boolean is not a number here."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and 0 <= value <= MAXIMUM_COUNT:
        return value
    if isinstance(value, float) and math.isfinite(value) and 0 <= value <= MAXIMUM_COUNT and value.is_integer():
        return int(value)
    return None


def _amount(value):
    """A finite non negative number, or None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or not 0 <= value <= MAXIMUM_AMOUNT:
        return None
    return float(value)


def moment(value):
    """A time in UTC from an ISO 8601 text, Unix seconds or Unix milliseconds, or None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if not math.isfinite(value) or value <= 0:
            return None
        seconds = value / 1000 if value > 100_000_000_000 else value
        try:
            return datetime.fromtimestamp(seconds, timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str) or not 10 <= len(value) <= 40:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            return None
        # A time at the edge of the date range with an offset cannot be moved to UTC.
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def stamp(value):
    """The stored form of a time, which also sorts correctly as text."""
    return value.astimezone(timezone.utc).isoformat(timespec='microseconds')


def tokens(usage):
    """Token counts by kind from any usage shape a host reports, or None when it carries no count.

    Accepted shapes: a Codex `token_count` total or turn usage, a Claude message or
    result usage, an OpenCode `tokens` object, or a list of any of them, which is
    summed. The total is the reported total when present. Otherwise it follows the
    provider: Codex counts cached input inside its input, Claude counts it apart.
    """
    if isinstance(usage, list):
        found = [part for part in (tokens(item) for item in usage[:1000]) if part]
        if not found:
            return None
        return {field: sum(part[field] or 0 for part in found) for field in TOKEN_FIELDS}
    if not isinstance(usage, dict):
        return None
    if isinstance(usage.get('tokens'), dict):
        usage = usage['tokens']
    cache = usage.get('cache') if isinstance(usage.get('cache'), dict) else {}
    details = usage.get('output_tokens_details') if isinstance(usage.get('output_tokens_details'), dict) else {}
    separate_cache = 'cache_read_input_tokens' in usage or 'cache_creation_input_tokens' in usage or bool(cache)
    values = {
        'input_tokens': _first(usage, 'input_tokens', 'input', 'inputTokens'),
        'cached_input_tokens': _first(usage, 'cached_input_tokens', 'cache_read_input_tokens', 'cachedInputTokens') if not cache
        else _count(cache.get('read')),
        'cache_write_tokens': _first(usage, 'cache_write_input_tokens', 'cache_creation_input_tokens', 'cache_write_tokens') if not cache
        else _count(cache.get('write')),
        'output_tokens': _first(usage, 'output_tokens', 'output', 'outputTokens'),
        'reasoning_tokens': _first(usage, 'reasoning_output_tokens', 'reasoning', 'reasoning_tokens', 'reasoningTokens') if not details
        else _count(details.get('thinking_tokens')),
        'total_tokens': _first(usage, 'total_tokens', 'total', 'totalTokens'),
    }
    if all(value is None for value in values.values()):
        return None
    if values['total_tokens'] is None:
        parts = ['input_tokens', 'output_tokens']
        if separate_cache:
            parts += ['cached_input_tokens', 'cache_write_tokens']
        values['total_tokens'] = sum(values[name] or 0 for name in parts)
    return values


def usage_cost(usage):
    """The summed cost that usage reports carry, such as the OpenCode steps in run metrics, or None."""
    parts = usage if isinstance(usage, list) else [usage]
    found = [_amount(part.get('cost')) for part in parts[:1000] if isinstance(part, dict)]
    found = [amount for amount in found if amount is not None]
    return round(sum(found), 8) if found else None


def _first(usage, *names):
    for name in names:
        value = _count(usage.get(name))
        if value is not None:
            return value
    return None


def _json(raw):
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeDecodeError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


# Where the logs of each host are on this machine.

def default_folders():
    """The session log folders of Codex and Claude Code, following their own home variables."""
    home = Path.home()
    codex = Path(os.environ.get('CODEX_HOME') or home / '.codex')
    claude = Path(os.environ.get('CLAUDE_CONFIG_DIR') or home / '.claude')
    return {'codex': codex / 'sessions', 'claude': claude / 'projects'}


def project_database(root):
    """The database of a registered project, or None when none is found."""
    root = Path(root)
    try:
        state = json.loads((root / '.memory' / 'install.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        state = None
    if isinstance(state, dict) and isinstance(state.get('database'), str):
        configured = Path(state['database'])
        if configured.is_file():
            return configured
    folder = root / '.memory'
    for name in ('project.sqlite', 'memory.sqlite'):
        if (folder / name).is_file():
            return folder / name
    return None


# Writing the ledger.

class Batch:
    """The records and limit reports of one collection step, written together."""

    def __init__(self, store, stats):
        self.store = store
        self.stats = stats
        collected = moment(store.now())
        self.latest_observation = stamp((collected or datetime.now(timezone.utc)) + FUTURE_TOLERANCE)
        self.first = {}
        self.largest = {}
        self.limits = {}

    def record(self, entry, host, kind, started, ended, counts, *, cost=None, duration_ms=None, keep='first'):
        """Add one usage record. With keep='largest' a later report of the same entry replaces a smaller one."""
        row = {'entry': entry, 'host': host, 'kind': kind, 'started_at': stamp(started), 'ended_at': stamp(ended),
               **{field: (counts or {}).get(field) for field in TOKEN_FIELDS},
               'cost': cost, 'currency': CURRENCY if cost is not None else None, 'duration_ms': duration_ms}
        if keep == 'largest':
            prior = self.largest.get(entry)
            if prior is None or (row['total_tokens'] or 0) > (prior['total_tokens'] or 0):
                self.largest[entry] = row
        else:
            self.first.setdefault(entry, row)

    def limit(self, host, report, observed):
        """Keep the latest used percentage of each reported limit window.

        An observation later than the time of collection allows is ignored, because it comes from a wrong clock.
        """
        if not isinstance(report, dict) or stamp(observed) > self.latest_observation:
            return
        limit_id = report.get('limit_id')
        if not isinstance(limit_id, str) or not LIMIT_IDENTIFIER.fullmatch(limit_id):
            limit_id = 'default'
        for window in LIMIT_WINDOWS:
            part = report.get(window)
            if not isinstance(part, dict):
                continue
            percent = _amount(part.get('used_percent'))
            if percent is None:
                continue
            resets = moment(part.get('resets_at'))
            key = (host, limit_id, window)
            row = {'host': host, 'limit_id': limit_id, 'window': window, 'used_percent': min(percent, 1000.0),
                   'window_minutes': _count(part.get('window_minutes')),
                   'resets_at': stamp(resets) if resets else None, 'observed_at': stamp(observed)}
            prior = self.limits.get(key)
            if prior is None or row['observed_at'] >= prior['observed_at']:
                self.limits[key] = row

    def write(self):
        """Write everything gathered inside the current transaction of the store."""
        now = self.store.now()
        columns = ['entry', 'host', 'kind', 'started_at', 'ended_at', *TOKEN_FIELDS, 'cost', 'currency', 'duration_ms']
        placeholders = ','.join('?' * (len(columns) + 1))
        insert = 'INSERT INTO usage_records (' + ','.join(columns) + ',recorded_at) VALUES (' + placeholders + ')'
        for row in self.first.values():
            cursor = self.store.db.execute(insert + ' ON CONFLICT(entry) DO NOTHING', [row[name] for name in columns] + [now])
            self.stats['records_written'] += cursor.rowcount
        updated = ','.join(name + '=excluded.' + name for name in TOKEN_FIELDS)
        for row in self.largest.values():
            cursor = self.store.db.execute(
                insert + ' ON CONFLICT(entry) DO UPDATE SET ' + updated + ' WHERE coalesce(excluded.total_tokens,0)'
                ' > coalesce(usage_records.total_tokens,0)', [row[name] for name in columns] + [now])
            self.stats['records_written'] += cursor.rowcount
        for row in self.limits.values():
            cursor = self.store.db.execute(
                'INSERT INTO usage_limits (host,limit_id,window,used_percent,window_minutes,resets_at,observed_at)'
                ' VALUES (?,?,?,?,?,?,?) ON CONFLICT(host,limit_id,window) DO UPDATE SET used_percent=excluded.used_percent,'
                'window_minutes=excluded.window_minutes,resets_at=excluded.resets_at,observed_at=excluded.observed_at'
                ' WHERE excluded.observed_at>usage_limits.observed_at OR usage_limits.observed_at>?',
                (row['host'], row['limit_id'], row['window'], row['used_percent'], row['window_minutes'], row['resets_at'],
                 row['observed_at'], self.latest_observation))
            self.stats['limits_written'] += cursor.rowcount
        self.first = {}
        self.largest = {}
        self.limits = {}


# The readers of each log format. Each reads one complete line and keeps only numbers and times.

class CodexSession:
    """A Codex session log: `token_count` events with cumulative totals and the reported rate limits.

    The cumulative total of a session makes repeated reports harmless: a report
    whose total did not grow adds nothing. A total that fell, as after a
    compaction, adds the last turn only.
    """

    host = 'codex'

    def __init__(self, batch, state):
        self.batch = batch
        self.state = state

    def line(self, raw):
        if b'"token_count"' not in raw:
            return
        value = _json(raw)
        if value is None:
            self.batch.stats['malformed_lines'] += 1
            return
        payload = value.get('payload')
        if value.get('type') != 'event_msg' or not isinstance(payload, dict) or payload.get('type') != 'token_count':
            return
        at = moment(value.get('timestamp'))
        if at is None:
            self.batch.stats['lines_without_time'] += 1
            return
        info = payload.get('info')
        if isinstance(info, dict):
            self.count(info, at)
        self.batch.limit(self.host, payload.get('rate_limits'), at)

    def count(self, info, at):
        total = tokens(info.get('total_token_usage'))
        last = tokens(info.get('last_token_usage'))
        previous = self.state.get('total')
        if total:
            current = [total[field] or 0 for field in TOKEN_FIELDS]
            if previous is None or len(previous) != len(current) or current[-1] < previous[-1]:
                delta = last or total
            elif current[-1] == previous[-1]:
                delta = None
            else:
                delta = {field: max(0, after - before) for field, after, before in zip(TOKEN_FIELDS, current, previous)}
            self.state['total'] = current
        else:
            delta = last
        if not delta or not delta['total_tokens']:
            return
        # A cumulative total identifies its report, because a forked session replays the reports of its parent with new
        # times. The same total copied into another session file is therefore the same entry. A report without a total
        # is known by its time as well.
        entry = _digest('codex:' + dumps(total)) if total else _digest('codex:' + stamp(at) + ':' + dumps(last))
        self.batch.record(entry, self.host, 'session', at, at, delta)


class ClaudeTranscript:
    """A Claude Code transcript: the usage of each assistant message.

    A message written in several lines repeats its identifier with growing usage,
    so the largest report of an identifier is kept. A resumed transcript repeats
    earlier messages, which share their identifier and are recorded once.
    """

    host = 'claude'

    def __init__(self, batch, state):
        self.batch = batch
        self.state = state

    def line(self, raw):
        if b'"usage"' not in raw or b'"assistant"' not in raw:
            return
        value = _json(raw)
        if value is None:
            self.batch.stats['malformed_lines'] += 1
            return
        message = value.get('message')
        if value.get('type') != 'assistant' or not isinstance(message, dict) or message.get('model') == '<synthetic>':
            return
        counts = tokens(message.get('usage'))
        if not counts or not counts['total_tokens']:
            return
        at = moment(value.get('timestamp'))
        if at is None:
            self.batch.stats['lines_without_time'] += 1
            return
        identifier = message.get('id') if isinstance(message.get('id'), str) and message.get('id') else value.get('uuid')
        if not isinstance(identifier, str) or not identifier:
            return
        request = value.get('requestId') if isinstance(value.get('requestId'), str) else ''
        entry = _digest('claude:' + identifier + ':' + request)
        self.batch.record(entry, self.host, 'session', at, at, counts, keep='largest')


READERS = {'codex': CodexSession, 'claude': ClaudeTranscript}


def opencode_usage(lines):
    """Tokens and cost from the JSON events of one OpenCode run, summed over its `step_finish` events.

    Returns None when no step reported usage. Malformed lines are counted and skipped.
    """
    found = []
    cost = None
    malformed = 0
    times = []
    for raw in lines:
        if isinstance(raw, str):
            raw = raw.encode('utf-8', errors='replace')
        if not raw.strip():
            continue
        if b'step' not in raw:
            continue
        value = _json(raw)
        if value is None:
            malformed += 1
            continue
        part = value.get('part') if isinstance(value.get('part'), dict) else {}
        if value.get('type') not in ('step_finish', 'step-finish') and part.get('type') != 'step-finish':
            continue
        source = value if isinstance(value.get('tokens'), dict) else part
        counts = tokens({'tokens': source.get('tokens')}) if isinstance(source.get('tokens'), dict) else None
        if counts:
            found.append(counts)
        amount = _amount(source.get('cost'))
        if amount is not None:
            cost = (cost or 0.0) + amount
        at = moment(value.get('timestamp'))
        if at is not None:
            times.append(at)
    if not found and cost is None:
        return {'tokens': None, 'cost': None, 'steps': 0, 'malformed_lines': malformed}
    summed = {field: sum(part[field] or 0 for part in found) for field in TOKEN_FIELDS} if found else None
    return {'tokens': summed, 'cost': round(cost, 8) if cost is not None else None, 'steps': len(found),
            'malformed_lines': malformed, 'started': min(times) if times else None, 'ended': max(times) if times else None}


# Collection.

def new_stats():
    return {'files_seen': 0, 'files_read': 0, 'files_unchanged': 0, 'files_restarted': 0, 'files_unreadable': 0,
            'bytes_read': 0, 'malformed_lines': 0, 'lines_without_time': 0, 'records_written': 0, 'limits_written': 0,
            'projects_seen': 0, 'projects_without_database': 0, 'projects_unreadable': 0, 'runs_recorded': 0,
            'runs_skipped': 0, 'limit_hits_recorded': 0, 'records_added': 0, 'folders': {}, 'seconds': 0.0}


def collect(store, *, folders=None, projects=True):
    """Collect usage into the ledger of an open machine memory and return counts only.

    `folders` maps `codex` and `claude` to their session log folders; by default
    the folders of this machine are read. A source that cannot be read is counted
    and does not stop the others.
    """
    started = time.monotonic()
    machine.ensure_usage(store)
    stats = new_stats()
    before = store.db.execute('SELECT count(*) FROM usage_records').fetchone()[0]
    found = default_folders() if folders is None else folders
    for host in ('codex', 'claude'):
        folder = found.get(host)
        if not folder:
            stats['folders'][host] = 'not_configured'
            continue
        folder = Path(folder)
        if not folder.is_dir():
            stats['folders'][host] = 'missing'
            continue
        stats['folders'][host] = 'read'
        try:
            paths = sorted(folder.rglob('*.jsonl'))
        except OSError:
            stats['folders'][host] = 'unreadable'
            continue
        for path in paths:
            try:
                scan_file(store, path, host, stats)
            except (OSError, ValueError, OverflowError):
                stats['files_unreadable'] += 1
    if projects:
        collect_runs(store, stats)
    stats['records_added'] = store.db.execute('SELECT count(*) FROM usage_records').fetchone()[0] - before
    stats['seconds'] = round(time.monotonic() - started, 3)
    return stats


def scan_file(store, path, host, stats):
    """Read the complete lines a log file gained since the last collection."""
    source = SOURCES[host]
    status = path.stat()
    stats['files_seen'] += 1
    identity = _digest(source + ':' + str(status.st_dev) + ':' + str(status.st_ino))
    row = store.db.execute('SELECT * FROM usage_files WHERE identity=?', (identity,)).fetchone()
    if row is not None and row['size'] == status.st_size and row['modified_ns'] == status.st_mtime_ns:
        stats['files_unchanged'] += 1
        return
    with path.open('rb') as stream, store._write():
        offset = 0
        state = {}
        head_length = min(status.st_size, HEAD_BYTES)
        head_digest = None
        if row is not None:
            head = stream.read(row['head_length'])
            same = len(head) == row['head_length'] and _digest_bytes(head) == row['head_digest']
            if same and status.st_size >= row['read_offset']:
                offset = row['read_offset']
                state = _state(row['state'])
                head_length = row['head_length']
                head_digest = row['head_digest']
            else:
                # Another file took this identity, or the file was rewritten. Its entries are recorded once anyway.
                stats['files_restarted'] += 1
        if head_digest is None:
            stream.seek(0)
            head_digest = _digest_bytes(stream.read(head_length))
        batch = Batch(store, stats)
        reader = READERS[host](batch, state)
        stream.seek(offset)
        rest = b''
        position = offset
        while True:
            chunk = stream.read(READ_CHUNK)
            if not chunk:
                break
            data = rest + chunk
            end = data.rfind(b'\n')
            if end < 0:
                rest = data
                continue
            for line in data[:end].split(b'\n'):
                if line.strip():
                    reader.line(line)
            position += end + 1
            rest = data[end + 1:]
            batch.write()
        batch.write()
        stats['bytes_read'] += position - offset
        stats['files_read'] += 1
        store.db.execute(
            'INSERT INTO usage_files (identity,source,read_offset,size,modified_ns,head_length,head_digest,state,updated_at)'
            ' VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(identity) DO UPDATE SET source=excluded.source,'
            'read_offset=excluded.read_offset,size=excluded.size,modified_ns=excluded.modified_ns,'
            'head_length=excluded.head_length,head_digest=excluded.head_digest,state=excluded.state,'
            'updated_at=excluded.updated_at',
            (identity, source, position, status.st_size, status.st_mtime_ns, head_length, head_digest, _stored_state(state),
             store.now()))


def _state(text):
    try:
        value = json.loads(text)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _stored_state(state):
    """Only the running token total of a Codex session is kept between collections."""
    total = state.get('total')
    if isinstance(total, list) and all(_count(item) is not None for item in total):
        return dumps({'total': total})
    return dumps({})


def collect_runs(store, stats):
    """Record the finished runs and limit hits of every registered project."""
    machine.ensure_usage(store)
    known_runs = {row[0] for row in store.db.execute("SELECT entry FROM usage_records WHERE kind='run'")}
    known_hits = {row[0] for row in store.db.execute('SELECT entry FROM usage_limit_hits')}
    for project in machine.registry(store, limit=1_000_000):
        stats['projects_seen'] += 1
        database = project_database(project['path'])
        if database is None:
            stats['projects_without_database'] += 1
            continue
        try:
            with Memory(database, read_only=True) as memory:
                runs = _finished_runs(memory, known_runs, stats)
                hits = _limit_hits(memory, known_hits)
        except (MemoryError, OSError, sqlite3.Error, ValueError, TypeError, KeyError):
            stats['projects_unreadable'] += 1
            continue
        batch = Batch(store, stats)
        with store._write():
            for run in runs:
                batch.record(run['entry'], run['host'], 'run', run['started'], run['ended'], run['tokens'],
                             cost=run['cost'], duration_ms=run['duration_ms'])
                known_runs.add(run['entry'])
            batch.write()
            for hit in hits:
                cursor = store.db.execute('INSERT INTO usage_limit_hits (entry,host,reason,hit_at,until) VALUES (?,?,?,?,?)'
                                          ' ON CONFLICT(entry) DO NOTHING',
                                          (hit['entry'], hit['host'], hit['reason'], hit['hit_at'], hit['until']))
                stats['limit_hits_recorded'] += cursor.rowcount
                known_hits.add(hit['entry'])
        stats['runs_recorded'] += len(runs)


def _table(memory, name):
    return memory.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _finished_runs(memory, known, stats):
    if not _table(memory, 'review_runs'):
        return []
    found = []
    placeholders = ','.join('?' * len(UNFINISHED_RUN_STATES))
    rows = memory.db.execute('SELECT id,host,created_at,updated_at,metrics FROM review_runs WHERE state NOT IN ('
                             + placeholders + ') ORDER BY rowid', UNFINISHED_RUN_STATES).fetchall()
    for row in rows:
        entry = _digest('run:' + str(row['id']))
        if entry in known:
            continue
        started = moment(row['created_at'])
        ended = moment(row['updated_at'])
        if row['host'] not in HOSTS or started is None or ended is None:
            stats['runs_skipped'] += 1
            continue
        metrics = _state(row['metrics'] or '{}')
        counts = tokens(metrics.get('provider_usage'))
        cost = usage_cost(metrics.get('provider_usage'))
        if row['host'] == 'opencode' or counts is None:
            events = _run_events(memory, row['id'])
            if events is not None:
                counts = events['tokens'] or counts
                cost = events['cost'] if events['cost'] is not None else cost
        found.append({'entry': entry, 'host': row['host'], 'started': started, 'ended': max(started, ended),
                      'tokens': counts, 'cost': cost, 'duration_ms': _count(metrics.get('duration_ms'))})
    return found


def _run_events(memory, run_id):
    """The usage in the event log of one run, or None when the log is absent."""
    if not isinstance(run_id, str) or not RUN_IDENTIFIER.fullmatch(run_id) or set(run_id) == {'.'}:
        return None
    path = memory.path.parent / 'agent-runs' / run_id / 'output.jsonl'
    if not path.is_file():
        return None
    with path.open('rb') as stream:
        return opencode_usage(stream)


def _limit_hits(memory, known):
    if not _table(memory, 'host_receipts'):
        return []
    found = []
    rows = memory.db.execute("SELECT id,event_name,created_at,payload FROM host_receipts"
                             " WHERE event_name IN ('HostUnavailable','HostAvailable') ORDER BY rowid").fetchall()
    for row in rows:
        entry = _digest('hit:' + str(row['id']))
        if entry in known:
            continue
        payload = _state(row['payload'] or '{}')
        at = moment(row['created_at'])
        if payload.get('host') not in HOSTS or at is None:
            continue
        if row['event_name'] == 'HostAvailable':
            # A host marked available again ends its earlier limit hits.
            found.append({'entry': entry, 'host': payload['host'], 'reason': CLEARED, 'hit_at': stamp(at), 'until': None})
            continue
        reason = payload.get('reason')
        reason = reason if isinstance(reason, str) and REASON.fullmatch(reason) and reason != CLEARED else 'other'
        until = moment(payload.get('until'))
        found.append({'entry': entry, 'host': payload['host'], 'reason': reason, 'hit_at': stamp(at),
                      'until': stamp(until) if until else None})
    return found


def collect_machine(path=None, *, folders=None, projects=True):
    """Create the machine memory when needed, collect into it and return the counts."""
    target = machine.database_path(path)
    created = not target.exists()
    machine.initialize(target)
    with Memory(target) as store:
        stats = collect(store, folders=folders, projects=projects)
    stats['machine_memory_created'] = created
    return stats


def collect_after_run(path=None):
    """Collect at the end of a Project Memory run. A failure here never changes the result of the run."""
    target = machine.database_path(path)
    if not target.exists():
        return None
    try:
        with Memory(target) as store:
            return collect(store)
    except Exception:  # noqa: BLE001 because the run has finished and its result must not depend on the ledger.
        return None


# Reading the ledger.

def _now(now):
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('The time of a usage report needs a time zone.')
    return now.astimezone(timezone.utc)


def _day_start(now, zone):
    local = now.astimezone(zone) if zone is not None else now.astimezone()
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def window_bounds(now, zone=None):
    """The start of each window. A record counts when its end time is after the start and not after now.

    The current day starts at local midnight and includes that instant.
    """
    return {'last_5_hours': now - FIVE_HOURS, 'today': _day_start(now, zone), 'last_7_days': now - SEVEN_DAYS}


def _window(store, host, start, now, *, inclusive):
    comparison = '>=' if inclusive else '>'
    # total() adds as real numbers, so a large ledger cannot overflow the integers of SQLite while it is read.
    sums = ','.join('total(' + field + ')' for field in TOKEN_FIELDS)
    rows = store.db.execute('SELECT kind,count(*),count(total_tokens),' + sums + ',sum(cost),currency FROM usage_records'
                            ' WHERE host=? AND ended_at' + comparison + '? AND ended_at<=? GROUP BY kind,currency',
                            (host, stamp(start), stamp(now))).fetchall()
    value = {'from': stamp(start), 'records': 0, 'run_records': 0, 'session_records': 0, 'records_with_tokens': 0,
             **{field: 0 for field in TOKEN_FIELDS}, 'cost': {}}
    for row in rows:
        value['records'] += row[1]
        value[row[0] + '_records'] = value.get(row[0] + '_records', 0) + row[1]
        value['records_with_tokens'] += row[2]
        for index, field in enumerate(TOKEN_FIELDS):
            value[field] += int(row[3 + index] or 0)
        if row[-1] and row[-2] is not None:
            value['cost'][row[-1]] = round(value['cost'].get(row[-1], 0.0) + row[-2], 6)
    return value


def limit_state(store, host, now):
    """The latest reported limit windows of a host, each marked expired when its reset time has passed.

    Each report of a limit carries all of its current windows with one observation time, so a window observed before
    the latest report of its limit is no longer reported and is left out. An observation later than now allows comes
    from a wrong clock and is left out as well. A window without a reset time expires when its length has passed since
    it was observed.
    """
    if not machine.has_usage(store):
        return []
    rows = store.db.execute('SELECT limit_id,window,used_percent,window_minutes,resets_at,observed_at FROM usage_limits'
                            ' WHERE host=? AND observed_at<=? ORDER BY limit_id,window',
                            (host, stamp(now + FUTURE_TOLERANCE))).fetchall()
    latest = {}
    for row in rows:
        latest[row['limit_id']] = max(latest.get(row['limit_id'], ''), row['observed_at'])
    found = []
    for row in rows:
        if row['observed_at'] < latest[row['limit_id']]:
            continue
        resets = moment(row['resets_at'])
        if resets is None:
            observed = moment(row['observed_at'])
            length = timedelta(minutes=row['window_minutes']) if row['window_minutes'] else WINDOW_WITHOUT_LENGTH
            expired = observed is None or observed + length <= now
        else:
            expired = resets <= now
        found.append({'limit_id': row['limit_id'], 'window': row['window'], 'used_percent': row['used_percent'],
                      'window_minutes': row['window_minutes'], 'resets_at': row['resets_at'],
                      'observed_at': row['observed_at'], 'expired': expired})
    return found


def latest_limit_hit(store, host, now):
    """The limit hit that constrains a host, or else its latest limit hit, marked active while its reset time has not passed.

    Every hit since the host was last marked available counts, so a later hit that has already expired does not hide an
    earlier one whose reset time is still ahead. When several are active, the one that resets last is returned.
    """
    if not machine.has_usage(store):
        return None
    cleared = store.db.execute('SELECT max(hit_at) FROM usage_limit_hits WHERE host=? AND reason=? AND hit_at<=?',
                               (host, CLEARED, stamp(now))).fetchone()[0]
    rows = store.db.execute('SELECT reason,hit_at,until FROM usage_limit_hits WHERE host=? AND reason<>? AND hit_at>?'
                            ' ORDER BY hit_at DESC', (host, CLEARED, cleared or '')).fetchall()
    if not rows and cleared is None:
        return None
    found = []
    for row in rows:
        until = moment(row['until'])
        hit = moment(row['hit_at'])
        if until is not None:
            active = until > now
            ends = until
        else:
            active = hit is not None and now - hit < LIMIT_HIT_WITHOUT_UNTIL
            ends = hit + LIMIT_HIT_WITHOUT_UNTIL if hit is not None else now
        found.append(({'reason': row['reason'], 'hit_at': row['hit_at'], 'until': row['until'], 'active': active}, ends))
    active = [item for item in found if item[0]['active']]
    if active:
        return max(active, key=lambda item: item[1])[0]
    if found:
        return found[0][0]
    # Every hit ended when the host was marked available; the latest of them is still reported, inactive.
    row = store.db.execute('SELECT reason,hit_at,until FROM usage_limit_hits WHERE host=? AND reason<>?'
                           ' ORDER BY hit_at DESC LIMIT 1', (host, CLEARED)).fetchone()
    if row is None:
        return None
    return {'reason': row['reason'], 'hit_at': row['hit_at'], 'until': row['until'], 'active': False}


def _median_of_windows(store, host, now):
    """The median total of the 5 hour windows in the last 7 days that recorded any tokens, or None."""
    rows = store.db.execute('SELECT ended_at,total_tokens FROM usage_records WHERE host=? AND ended_at>? AND ended_at<=?'
                            ' AND total_tokens IS NOT NULL', (host, stamp(now - SEVEN_DAYS), stamp(now))).fetchall()
    buckets = [0] * MEDIAN_WINDOWS
    for row in rows:
        at = moment(row[0])
        if at is None:
            continue
        index = int((now - at) / FIVE_HOURS)
        if 0 <= index < MEDIAN_WINDOWS:
            buckets[index] += row[1]
    used = [value for value in buckets if value > 0]
    return statistics.median(used) if used else None


def host_headroom(store, host, now=None):
    """The headroom of one host for routing (section 16.3), from the ledger alone.

    A host is constrained when its latest reported use reaches 85 percent in a window
    whose reset time has not passed, or when it hit a limit that has not reset.
    Whether a project marked the host unavailable is read by `hosts.availability`.
    """
    now = _now(now)
    value = {'host': host, 'constrained': False, 'reasons': [], 'used_percent': None, 'resets_at': None,
             'limit_hit': None, 'tokens_5_hours': None, 'median_5_hours': None, 'relative_load': None}
    if store is None or not machine.has_usage(store):
        return value
    current = [item for item in limit_state(store, host, now) if not item['expired']]
    # A limit of a single model, such as a second Codex limit beside the general one, does not decide for the whole
    # host while the general limit of the host is reported.
    general = [item for item in current if item['limit_id'] in (host, 'default')]
    if general:
        current = general
    if current:
        highest = max(current, key=lambda item: item['used_percent'])
        value['used_percent'] = highest['used_percent']
        value['resets_at'] = highest['resets_at']
        if highest['used_percent'] >= CONSTRAINED_PERCENT:
            value['reasons'].append('used_percent')
    hit = latest_limit_hit(store, host, now)
    if hit and hit['active']:
        value['limit_hit'] = hit
        value['reasons'].append('limit_hit')
    recent = _window(store, host, now - FIVE_HOURS, now, inclusive=False)
    value['tokens_5_hours'] = recent['total_tokens'] if recent['records_with_tokens'] else None
    median = _median_of_windows(store, host, now)
    value['median_5_hours'] = median
    if median and value['tokens_5_hours'] is not None:
        value['relative_load'] = round(value['tokens_5_hours'] / median, 4)
    elif not recent['records']:
        # A host with no recorded use in the last 5 hours has the lowest possible load, also when it was never used.
        value['relative_load'] = 0.0
    value['constrained'] = bool(value['reasons'])
    return value


def headroom(path=None, *, now=None, hosts=HOSTS):
    """The headroom of each host, read only from the machine memory, also before any usage was collected."""
    target = machine.database_path(path)
    if not target.exists():
        return {host: host_headroom(None, host, now) for host in hosts}
    with Memory(target, read_only=True) as store:
        return {host: host_headroom(store, host, now) for host in hosts}


def summary(store, *, now=None, zone=None):
    """Tokens per window, cost, limit state and limit hits for every host. Safe on a read only connection."""
    now = _now(now)
    bounds = window_bounds(now, zone)
    ledger = store is not None and machine.has_usage(store)
    hosts = []
    for host in HOSTS:
        item = {'host': host, 'windows': {}, 'limits': [], 'limit_hit': None,
                'measured': {'tokens': False, 'cost': False, 'limit_state': False, 'limit_hits': False},
                'unavailable': list(UNAVAILABLE[host])}
        if ledger:
            for name, start in bounds.items():
                item['windows'][name] = _window(store, host, start, now, inclusive=name == 'today')
            flags = store.db.execute('SELECT count(total_tokens),count(cost) FROM usage_records WHERE host=?',
                                     (host,)).fetchone()
            item['limits'] = limit_state(store, host, now)
            item['limit_hit'] = latest_limit_hit(store, host, now)
            item['measured'] = {'tokens': flags[0] > 0, 'cost': flags[1] > 0, 'limit_state': bool(item['limits']),
                                'limit_hits': item['limit_hit'] is not None}
            item['headroom'] = host_headroom(store, host, now)
        else:
            item['headroom'] = host_headroom(None, host, now)
        if ledger and not item['measured']['tokens']:
            item['unavailable'].insert(0, 'No tokens were recorded for this host on this machine.')
        hosts.append(item)
    return {'measured_at': stamp(now), 'ledger': ledger, 'hosts': hosts,
            'note': WINDOWS_NOTE if ledger else NO_LEDGER_NOTE}


def report(path=None, *, collect_first=True, folders=None, now=None, zone=None):
    """What `project-memory usage` prints: the collection counts and the summary per host."""
    target = machine.database_path(path)
    result = {'database': str(target)}
    if collect_first:
        result['collection'] = collect_machine(target, folders=folders)
    if not target.exists():
        return {**result, **summary(None, now=now, zone=zone)}
    with Memory(target, read_only=True) as store:
        return {**result, **summary(store, now=now, zone=zone)}


# The plain text table.

def _number(value):
    return f'{value:,}'


def _limit_text(item):
    current = [limit for limit in item['limits'] if not limit['expired']]
    parts = []
    for limit in current:
        window = f'{limit["window_minutes"]} minute window' if limit['window_minutes'] else 'window'
        text = f'{limit["used_percent"]:g} percent of the {window} of {limit["limit_id"]}'
        if limit['resets_at']:
            text += f', which resets at {limit["resets_at"][:16].replace("T", " ")} UTC'
        parts.append(text)
    if item['limit_hit'] and item['limit_hit']['active']:
        hit = item['limit_hit']
        text = 'A limit was hit (' + hit['reason'].replace('_', ' ') + ')'
        if hit['until']:
            text += f' and resets at {hit["until"][:16].replace("T", " ")} UTC'
        parts.append(text)
    return parts


def render(value):
    """The usage report as plain text for a terminal."""
    lines = []
    collection = value.get('collection')
    if collection:
        lines.append(f'Usage was collected in {collection["seconds"]:g} seconds: {_number(collection["files_read"])} log '
                     f'files read, {_number(collection["records_added"])} new records and '
                     f'{_number(collection["runs_recorded"])} runs of registered projects.')
    if not value['ledger']:
        lines.append(value['note'])
        return '\n'.join(lines) + '\n'
    lines.append('')
    header = f'{"Host":<10}{"5 hours":>16}{"Today":>16}{"7 days":>16}{"Cost in 7 days":>18}'
    lines.append(header)
    for item in value['hosts']:
        windows = item['windows']
        cells = []
        for name in ('last_5_hours', 'today', 'last_7_days'):
            window = windows[name]
            # A host that reported tokens before shows zero for a quiet window. Only a host never measured is unavailable.
            cells.append(_number(window['total_tokens']) if item['measured']['tokens'] else 'unavailable')
        cost = windows['last_7_days']['cost']
        cost_text = ', '.join(f'{amount:.2f} {currency}' for currency, amount in sorted(cost.items())) if cost else (
            f'0.00 {CURRENCY}' if item['measured']['cost'] else 'unavailable')
        lines.append(f'{item["host"]:<10}{cells[0]:>16}{cells[1]:>16}{cells[2]:>16}{cost_text:>18}')
    lines.append('')
    for item in value['hosts']:
        limits = _limit_text(item)
        if limits:
            lines.append(item['host'] + ': ' + '; '.join(limits) + '.')
        elif item['measured']['limit_state']:
            lines.append(item['host'] + ': every reported limit window has reset.')
        for sentence in item['unavailable']:
            lines.append(item['host'] + ': ' + sentence)
    lines.append('')
    lines.append(value['note'])
    return '\n'.join(lines) + '\n'
