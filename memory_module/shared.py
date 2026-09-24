"""One implementation of the concepts that several modules share.

Request key replay, budget fitting for bounded tool results, the execution
status of host receipts, git command execution and agent run summaries live
here, so that the panel API, the MCP adapter, delegated work and the reviews
worker all report the same values. Read functions work on a read-only
connection and on a database without the optional tables.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess

from .core import BudgetTooSmall, Conflict, InvalidRecord, dumps

GIT_TIMEOUT = 120


# Request key replay over the adapter request table.

def prior_result(memory, request_key, signature, message='Request key was already used for different content.'):
    """The stored result of an earlier write with this request key, or None. A different signature raises Conflict."""
    prior = memory.db.execute('SELECT signature,result FROM adapter_requests WHERE request_key=?', (request_key,)).fetchone()
    if not prior:
        return None
    if prior['signature'] != signature:
        raise Conflict(message)
    return json.loads(prior['result'])


def latest_source(memory, source_id):
    """The newest version of the source this id belongs to, or the id itself when it has no source row."""
    row = memory.db.execute('SELECT id FROM sources WHERE source_key=(SELECT source_key FROM sources WHERE id=?) '
                            'ORDER BY version DESC LIMIT 1', (source_id,)).fetchone()
    return row[0] if row else source_id


def store_result(memory, request_key, signature, result, *, keep_existing=False):
    """Store the result of a write, so that a repeated request key returns it instead of writing again."""
    statement = 'INSERT OR IGNORE INTO adapter_requests VALUES (?,?,?)' if keep_existing else 'INSERT INTO adapter_requests VALUES (?,?,?)'
    memory.db.execute(statement, (request_key, signature, dumps(result)))


# Budget fitting for bounded results.

def tool_result(value, error=False):
    return {'content': [{'type': 'text', 'text': dumps(value)}], 'isError': error}


def size(value):
    """Characters of the complete tool result for a value."""
    return len(dumps(tool_result(value)))


def bounded(value, budget):
    needed = size(value)
    if needed > budget:
        raise BudgetTooSmall('The complete record exceeds max_chars. Narrow the request, page lineage, or deliberately increase max_chars; records are not silently cut.',
                             minimum_required=needed, unit='characters', max_chars_limit=20000)
    return value


def trim(result, page, key, budget):
    """Drop trailing entries of page[key] until the result fits, keeping at least one entry."""
    entries = page[key]
    while len(entries) > 1 and size(result) > budget:
        entries.pop()
        if 'next_offset' in page:
            page['next_offset'] -= 1
        page['more'] = True
    return result


def shrink(build, limit, budget):
    """Call build(limit) with a halving limit until the result fits or the limit is 1."""
    result = build(limit)
    reduced = False
    while limit > 1 and size(result) > budget:
        limit = max(1, limit // 2)
        result = build(limit)
        reduced = True
    if reduced:
        result['truncated'] = True
        result['limit_used'] = limit
    return result


def largest(build, low, high, budget):
    """The largest count from low to high whose result fits the budget. The result for low must fit."""
    while low < high:
        middle = (low + high + 1) // 2
        if size(build(middle)) <= budget:
            low = middle
        else:
            high = middle - 1
    return low


# Execution status of host receipts. Both clauses describe the pre-tool receipt under the alias p.

_REPORTED = ("EXISTS (SELECT 1 FROM host_receipts r WHERE r.session_id=p.session_id AND r.tool_use_id=p.tool_use_id"
             " AND r.event_name='PostToolUse'"
             " AND coalesce(json_extract(r.payload,'$.host'),'codex')=coalesce(json_extract(p.payload,'$.host'),'codex'))")
_RECONCILED = ("EXISTS (SELECT 1 FROM host_receipts r WHERE r.event_name='Reconciled'"
               " AND json_extract(r.payload,'$.receipt_id')=p.id{resolution})")
_PENDING = "p.event_name='PreToolUse' AND NOT " + _REPORTED + " AND NOT " + _RECONCILED
# Execution is not established: no result and no reconciliation that resolves it. Unknown preserves the uncertainty.
UNCONFIRMED = _PENDING.format(resolution=" AND json_extract(r.payload,'$.resolution')!='unknown'")
# Not assessed at all: no result and no reconciliation, not even an explicit unknown.
UNASSESSED = _PENDING.format(resolution='')
# A call of the main conversation. Claude Code names the subagent that made a call; a background agent may still be
# running when the main conversation stops, so its calls are not activity the main conversation must account for.
MAIN_THREAD = "json_extract(p.payload,'$.agent_id') IS NULL"


def unconfirmed_total(memory, *, episode_id=None, session_id=None, clause=UNCONFIRMED):
    """How many tool calls of a work item or a session have no confirmed result."""
    from . import codex_host
    if not codex_host.exists(memory):
        return 0
    sql = 'SELECT count(*) FROM host_receipts p WHERE ' + clause
    arguments = []
    for column, value in (('p.episode_id', episode_id), ('p.session_id', session_id)):
        if value is not None:
            sql += ' AND ' + column + '=?'
            arguments.append(value)
    return memory.db.execute(sql, arguments).fetchone()[0]


def is_unconfirmed(memory, receipt_id):
    """True when this pre-tool receipt has no reported result and no reconciliation that establishes one."""
    return bool(memory.db.execute('SELECT 1 FROM host_receipts p WHERE p.id=? AND ' + UNCONFIRMED, (receipt_id,)).fetchone())


# Git command execution.

def git(root, *args, check=True, timeout=GIT_TIMEOUT):
    """Run git in a directory and return the completed process."""
    try:
        result = subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True,
                                encoding='utf-8', errors='replace', timeout=timeout, stdin=subprocess.DEVNULL)
    except FileNotFoundError as exc:
        raise InvalidRecord('Git is not installed. Delegated work requires git.') from exc
    except subprocess.TimeoutExpired as exc:
        raise InvalidRecord('Git did not finish in time. Inspect the repository before retrying.') from exc
    if check and result.returncode != 0:
        raise InvalidRecord('Git reported an error: ' + git_message(result))
    return result


def git_message(result):
    return (result.stderr or result.stdout).strip()[:2000] or 'no message'


# The project files that agent checks, the architecture model and the panel read, and their content signature.

def project_paths(project):
    """The project root and its tracked and untracked files, without the database folder and capture artefacts."""
    root = Path(project).resolve()
    try:
        result = git(root, 'ls-files', '-z', '--cached', '--others', '--exclude-standard', check=False, timeout=10)
    except InvalidRecord:
        result = None
    if result and result.returncode == 0 and result.stdout.strip('\0'):
        paths = sorted(set(result.stdout.split('\0')) - {''})
    else:
        paths = []
        for folder, dirs, files in os.walk(root):
            dirs[:] = sorted(d for d in dirs if d not in {'.memory', '.git', '.venv', 'node_modules', '__pycache__'} and not d.endswith('.capture-errors'))
            paths.extend(str((Path(folder)/f).relative_to(root)) for f in sorted(files))
    return root, [name for name in paths if Path(name).parts[0] != '.memory'
                  and not any(part.endswith('.capture-errors') for part in Path(name).parts)
                  and not name.endswith(('.sqlite', '.sqlite-wal', '.sqlite-shm', '.pyc', '.capture-error.json'))]


def tree_signature(project, *, cache=None):
    """Viewer reads reuse unchanged files; review requests hash fresh content."""
    # Windows ctime can be creation time, so it cannot detect writes that restore mtime.
    if os.name != 'posix':
        cache = None
    root, paths = project_paths(project)
    fingerprints = []
    for name in paths:
        try:
            value = (root/name).lstat()
            fingerprints.append((name, value.st_ino, value.st_mode, value.st_size, value.st_mtime_ns, value.st_ctime_ns))
        except FileNotFoundError:
            fingerprints.append((name, None))
    if cache is not None and cache.get('root') == str(root) and cache.get('files') == fingerprints:
        return cache['signature']
    digest = hashlib.sha256()
    for name in paths:
        path = root/name
        digest.update(name.encode('utf-8'))
        if path.is_symlink():
            digest.update(b'symlink:' + os.readlink(path).encode())
        elif path.is_file():
            with path.open('rb') as source:
                for block in iter(lambda: source.read(65536), b''):
                    digest.update(block)
        elif path.exists():
            digest.update(b'directory')
        else:
            digest.update(b'deleted')
    signature = digest.hexdigest()
    if cache is not None:
        # Do not retain a hash if a write raced this read.
        for fingerprint in fingerprints:
            name = fingerprint[0]
            try:
                value = (root/name).lstat()
                after = (name, value.st_ino, value.st_mode, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
            except FileNotFoundError:
                after = (name, None)
            if after != fingerprint:
                cache.clear()
                raise InvalidRecord('Project files changed during the freshness check. Retry after the save completes.')
        cache.update(root=str(root), files=fingerprints, signature=signature)
    return signature


# Agent run summaries, including the review and merge state of delegated work.

def settlement(memory, run_id):
    """The latest merge or discard receipt of a delegated work run, or None."""
    from . import codex_host
    if not codex_host.exists(memory):
        return None
    row = memory.db.execute("SELECT id FROM host_receipts WHERE event_name IN ('DelegationMerged','DelegationDiscarded') "
                            "AND json_extract(payload,'$.run_id')=? ORDER BY rowid DESC LIMIT 1", (run_id,)).fetchone()
    return codex_host.read_receipt(memory, row[0]) if row else None


def latest_review(memory, run_id):
    """The latest work review of a delegated work run, or None. Work runs exist only with the delegation columns."""
    from . import reviews
    if not reviews.exists(memory):
        return None
    row = memory.db.execute("SELECT id FROM review_runs WHERE parent_run=? AND role='work_review' ORDER BY rowid DESC LIMIT 1",
                            (run_id,)).fetchone()
    return reviews.read(memory, row[0]) if row else None


def run_summary(memory, run):
    """One agent run without its snapshot, with the review and merge state of delegated work."""
    metrics = run.get('metrics') or {}
    result = {key: run.get(key) for key in ('id', 'episode_id', 'role', 'host', 'state', 'error', 'created_at',
                                            'updated_at', 'parent_run', 'branch', 'workspace')}
    result['summary'] = (run.get('report') or {}).get('summary')
    result['changed_files'] = len(metrics.get('changed_files') or [])
    result['independence'] = (run.get('snapshot') or {}).get('independence')
    if run['role'] == 'work':
        review = latest_review(memory, run['id'])
        result['review'] = {'id': review['id'], 'state': review['state'], 'host': review['host']} if review else None
        settled = settlement(memory, run['id'])
        result['merge'] = None
        if settled:
            result['merge'] = {'state': 'merged' if settled['event_name'] == 'DelegationMerged' else 'discarded',
                               'commit': settled['payload'].get('commit'), 'at': settled['created_at']}
    return result
