"""The hive: a shared working record for agents that work together (section 12 of the build specification).

Agents log guided moves into a separate database beside the project database. Every
check is mechanical: no model is called, and every refusal names its rule and says how
to correct the entry. The hive links to the main memory through the episode of a swarm
and through distillation, which proposes lessons through the normal lesson path.
Reads open the hive read only and work when the file does not exist yet.
"""
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path, PurePosixPath
import re
import sqlite3
import uuid

from .core import Conflict, InvalidRecord, RESERVED_ACTORS, USER_ACTOR, _digest, _text, dumps

HIVE_FILE = 'hive.sqlite'
HIVE_ACTOR = 'hive-distiller'
MOVES = ('orient', 'hypothesis', 'observation', 'challenge', 'support', 'question', 'answer', 'conclusion', 'pattern',
         'checkpoint')
BASIS_KINDS = ('file', 'command', 'entry', 'source', 'url')
RELATIONS = ('replies_to', 'challenges', 'supports', 'cites', 'answers')
SWARM_KINDS = ('focus', 'workflow', 'manual')
HOSTS = ('codex', 'claude', 'workspace-user', 'session')
CONFIDENCE = ('low', 'medium', 'high')
CLAIM_LIMIT = 280
DETAIL_LIMIT = 1200
CODE_LINES = 20
CHECKPOINT_LIMIT = 400
MAX_ENTRIES = 40
CLOSING_RESERVE = 4
MAX_OBSERVATIONS = 10
MAX_BASES = 10
MAX_CITES = 10
DUPLICATE_OVERLAP = 0.8
QUERY_LIMIT = 30
RESUME_LIMIT = 1200
COMPOSE_BUDGET = 800
MIN_COMPOSE_BUDGET = 400
CHECKPOINT_FIELDS = ('done', 'belief', 'open_questions', 'next_step')
USER_MOVES = ('question', 'answer', 'observation')
BLIND_HIDDEN = ('hypothesis', 'conclusion', 'pattern')
BLIND_REFUSED = ('challenge', 'support', 'answer')
# The moves whose entries can carry the evidence of a conclusion, and the moves that need a basis Project Memory can check.
EVIDENCE_MOVES = ('observation', 'challenge', 'support', 'answer', 'conclusion')
CHECKED_BASIS_MOVES = ('observation', 'challenge', 'support')
CHECK_RECEIPT = 'FocusCheckRecorded'
CHECK_START = 'FocusStarted'
CLOSING_MOVES = ('conclusion', 'checkpoint')

# The fields each move requires and the fields it accepts besides them.
REQUIRED = {
    'orient': ('claim', 'bases'),
    'hypothesis': ('claim', 'detail'),
    'observation': ('claim', 'bases'),
    'challenge': ('claim', 'target', 'bases'),
    'support': ('claim', 'target', 'bases'),
    'question': ('claim', 'addressee'),
    'answer': ('claim', 'target'),
    'conclusion': ('claim', 'confidence', 'cites'),
    'pattern': ('claim', 'cites'),
    'checkpoint': CHECKPOINT_FIELDS,
}
OPTIONAL = {move: ('detail', 'bases', 'reply_to') for move in MOVES}
OPTIONAL['checkpoint'] = ()
TARGET_RELATION = {'challenge': 'challenges', 'support': 'supports', 'answer': 'answers'}

# The protocol section of a worker prompt (section 12.5), at most PROTOCOL_LIMIT characters.
PROTOCOL_LIMIT = 500
WORKER_PROTOCOL = ('Hive protocol: log your work with hive_log as you go. First orient: the goal as claim, with a source or file '
                   'basis. Then hypothesis: claim and detail on how you will test it. Then observations, each with a basis such '
                   'as path:line. Before the final answer, log a conclusion with confidence and cites, then a checkpoint with done, '
                   'belief, open_questions and next_step. Put both entry ids in hive_conclusion_id and hive_checkpoint_id. '
                   'A refusal names its rule and how to correct the entry.')

SWARM_NOT_FOUND = 'The swarm {swarm} was not found in the hive of this project.'
READ_ONLY = 'The hive was opened read only, so it cannot record this change.'
PURGE_USER_ONLY = 'Only the user can purge swarms, in the chat through user_action or with project-memory hive purge.'

SCHEMA = """
CREATE TABLE IF NOT EXISTS counters (name TEXT PRIMARY KEY, value INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS swarms (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, purpose TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('focus','workflow','manual')), episode_id TEXT,
 state TEXT NOT NULL CHECK(state IN ('open','closed','purging')), opened_at TEXT NOT NULL, closed_at TEXT, summary TEXT,
 blind INTEGER NOT NULL CHECK(blind IN (0,1)), project TEXT, base_commit TEXT, request_key TEXT NOT NULL UNIQUE,
 request_hash TEXT NOT NULL, close_key TEXT, distillation TEXT
);
CREATE TABLE IF NOT EXISTS agents (
 swarm_id TEXT NOT NULL REFERENCES swarms(id), agent_id TEXT NOT NULL,
 host TEXT NOT NULL CHECK(host IN ('codex','claude','workspace-user','session')), role TEXT NOT NULL,
 run_id TEXT, worktree TEXT, joined_at TEXT NOT NULL, revealed_at TEXT, session_id TEXT, PRIMARY KEY(swarm_id, agent_id)
);
CREATE TABLE IF NOT EXISTS entries (
 id TEXT PRIMARY KEY, swarm_id TEXT NOT NULL REFERENCES swarms(id), agent_id TEXT NOT NULL,
 move TEXT NOT NULL CHECK(move IN ('orient','hypothesis','observation','challenge','support','question','answer',
                                   'conclusion','pattern','checkpoint')),
 claim TEXT NOT NULL, detail TEXT NOT NULL, confidence TEXT, addressed_to TEXT, data TEXT NOT NULL,
 created_at TEXT NOT NULL, seq INTEGER NOT NULL UNIQUE, request_key TEXT NOT NULL, request_hash TEXT NOT NULL,
 UNIQUE(swarm_id, agent_id, request_key)
);
CREATE INDEX IF NOT EXISTS swarm_entries ON entries(swarm_id, seq);
CREATE TABLE IF NOT EXISTS bases (
 entry_id TEXT NOT NULL REFERENCES entries(id), kind TEXT NOT NULL CHECK(kind IN ('file','command','entry','source','url')),
 value TEXT NOT NULL, verified INTEGER NOT NULL CHECK(verified IN (0,1)), exit_code INTEGER
);
CREATE INDEX IF NOT EXISTS entry_bases ON bases(entry_id);
CREATE TABLE IF NOT EXISTS links (
 from_entry TEXT NOT NULL REFERENCES entries(id), to_entry TEXT NOT NULL REFERENCES entries(id),
 relation TEXT NOT NULL CHECK(relation IN ('replies_to','challenges','supports','cites','answers')),
 PRIMARY KEY(from_entry, to_entry, relation)
);
CREATE INDEX IF NOT EXISTS incoming_links ON links(to_entry, relation);
CREATE TABLE IF NOT EXISTS marks (
 entry_id TEXT NOT NULL REFERENCES entries(id), mark TEXT NOT NULL CHECK(mark IN ('confirmed','disputed')),
 reason TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(entry_id, mark)
);
CREATE TABLE IF NOT EXISTS proposals (
 entry_id TEXT PRIMARY KEY REFERENCES entries(id), lesson_id TEXT NOT NULL, source_id TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS refusals (swarm_id TEXT NOT NULL, agent_id TEXT NOT NULL, move TEXT, rule TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS compositions (
 swarm_id TEXT NOT NULL, agent_id TEXT NOT NULL, run_id TEXT, characters INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(entry_id UNINDEXED, claim, detail);
CREATE TRIGGER IF NOT EXISTS entries_fixed BEFORE UPDATE ON entries BEGIN
 SELECT RAISE(ABORT, 'Hive entries cannot be changed. Append a new entry instead.'); END;
CREATE TRIGGER IF NOT EXISTS entries_kept BEFORE DELETE ON entries
 WHEN (SELECT state FROM swarms WHERE id=OLD.swarm_id) IS NOT 'purging' BEGIN
 SELECT RAISE(ABORT, 'Hive entries cannot be deleted. Only a purge of a closed swarm removes them.'); END;
CREATE TRIGGER IF NOT EXISTS bases_fixed BEFORE UPDATE ON bases BEGIN
 SELECT RAISE(ABORT, 'Hive bases cannot be changed. Append a new entry instead.'); END;
CREATE TRIGGER IF NOT EXISTS bases_kept BEFORE DELETE ON bases
 WHEN (SELECT s.state FROM entries e JOIN swarms s ON s.id=e.swarm_id WHERE e.id=OLD.entry_id) IS NOT 'purging' BEGIN
 SELECT RAISE(ABORT, 'Hive bases cannot be deleted. Only a purge of a closed swarm removes them.'); END;
CREATE TRIGGER IF NOT EXISTS links_fixed BEFORE UPDATE ON links BEGIN
 SELECT RAISE(ABORT, 'Hive links cannot be changed. Append a new entry instead.'); END;
CREATE TRIGGER IF NOT EXISTS links_kept BEFORE DELETE ON links
 WHEN (SELECT s.state FROM entries e JOIN swarms s ON s.id=e.swarm_id WHERE e.id=OLD.from_entry) IS NOT 'purging' BEGIN
 SELECT RAISE(ABORT, 'Hive links cannot be deleted. Only a purge of a closed swarm removes them.'); END;
CREATE TRIGGER IF NOT EXISTS marks_fixed BEFORE UPDATE ON marks BEGIN
 SELECT RAISE(ABORT, 'Distillation marks cannot be changed.'); END;
CREATE TRIGGER IF NOT EXISTS marks_kept BEFORE DELETE ON marks
 WHEN (SELECT s.state FROM entries e JOIN swarms s ON s.id=e.swarm_id WHERE e.id=OLD.entry_id) IS NOT 'purging' BEGIN
 SELECT RAISE(ABORT, 'Distillation marks cannot be deleted. Only a purge of a closed swarm removes them.'); END;
CREATE TRIGGER IF NOT EXISTS proposals_fixed BEFORE UPDATE ON proposals BEGIN
 SELECT RAISE(ABORT, 'Lesson proposals of the hive cannot be changed.'); END;
CREATE TRIGGER IF NOT EXISTS proposals_kept BEFORE DELETE ON proposals
 WHEN (SELECT s.state FROM entries e JOIN swarms s ON s.id=e.swarm_id WHERE e.id=OLD.entry_id) IS NOT 'purging' BEGIN
 SELECT RAISE(ABORT, 'Lesson proposals of the hive cannot be deleted. Only a purge of a closed swarm removes them.'); END;
CREATE TRIGGER IF NOT EXISTS swarms_fixed BEFORE UPDATE OF id,title,purpose,kind,episode_id,opened_at,blind,project,
 base_commit,request_key,request_hash ON swarms BEGIN
 SELECT RAISE(ABORT, 'The identity of a swarm cannot be changed.'); END;
CREATE TRIGGER IF NOT EXISTS swarms_closed_once BEFORE UPDATE OF closed_at,summary,close_key,distillation ON swarms
 WHEN OLD.state IS NOT 'open' BEGIN
 SELECT RAISE(ABORT, 'A closed swarm keeps its summary and distillation.'); END;
CREATE TRIGGER IF NOT EXISTS swarms_state BEFORE UPDATE OF state ON swarms
 WHEN NOT ((OLD.state='open' AND NEW.state='closed') OR (OLD.state='closed' AND NEW.state='purging')) BEGIN
 SELECT RAISE(ABORT, 'A swarm moves only from open to closed, and only a closed swarm is purged.'); END;
CREATE TRIGGER IF NOT EXISTS swarms_kept BEFORE DELETE ON swarms WHEN OLD.state IS NOT 'purging' BEGIN
 SELECT RAISE(ABORT, 'Only a purge of a closed swarm removes it.'); END;
CREATE TRIGGER IF NOT EXISTS agents_fixed BEFORE UPDATE OF swarm_id,agent_id,host,role,run_id,worktree,joined_at,session_id ON agents BEGIN
 SELECT RAISE(ABORT, 'The membership of an agent cannot be changed.'); END;
CREATE TRIGGER IF NOT EXISTS agents_revealed_once BEFORE UPDATE OF revealed_at ON agents WHEN OLD.revealed_at IS NOT NULL BEGIN
 SELECT RAISE(ABORT, 'The view of an agent opens once.'); END;
CREATE TRIGGER IF NOT EXISTS agents_kept BEFORE DELETE ON agents
 WHEN (SELECT state FROM swarms WHERE id=OLD.swarm_id) IS NOT 'purging' BEGIN
 SELECT RAISE(ABORT, 'Only a purge of a closed swarm removes its agents.'); END;
"""


class Refusal(InvalidRecord):
    """A guided refusal: it names the rule and says how to correct the entry, and nothing is recorded."""

    def __init__(self, rule, problem, correction, **extra):
        message = f'The entry breaks rule {rule}. {problem} {correction}'
        step = {'action': 'correct_entry', 'rule': rule, 'reason': correction, **extra}
        super().__init__(message, rule=rule, execution='not_started', next_step=step)
        self.rule = rule


class Hive:
    """One hive file beside the project database.

    A read only hive whose file does not exist has no connection, and every read
    function answers as for an empty hive.
    """

    def __init__(self, path, *, read_only=False, clock=None):
        self.path = Path(path).resolve()
        self.read_only = read_only
        self.clock = clock or (lambda: datetime.now(timezone.utc).isoformat(timespec='microseconds'))
        self.db = None
        if read_only:
            if self.path.exists():
                self.db = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True, isolation_level=None, timeout=5)
                self.db.row_factory = sqlite3.Row
                self.db.execute('PRAGMA query_only=ON')
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, isolation_level=None, timeout=5)
        self.db.row_factory = sqlite3.Row
        try:
            self.db.execute('PRAGMA journal_mode=WAL')
            self.db.execute('PRAGMA foreign_keys=ON')
            self.db.executescript(SCHEMA)
            _add_session_column(self.db)
        except Exception:
            self.db.close()
            raise

    def close(self):
        if self.db is not None:
            self.db.close()
            self.db = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def now(self):
        value = datetime.fromisoformat(self.clock().replace('Z', '+00:00'))
        return value.astimezone(timezone.utc).isoformat(timespec='microseconds')

    @contextmanager
    def write(self):
        """One transaction, nested through a savepoint when a transaction is already open."""
        if self.read_only or self.db is None:
            raise InvalidRecord(READ_ONLY)
        nested = self.db.in_transaction
        self.db.execute('SAVEPOINT hive_write' if nested else 'BEGIN IMMEDIATE')
        try:
            yield
            if nested:
                self.db.execute('RELEASE hive_write')
            else:
                self.db.commit()
        except BaseException:
            if nested:
                self.db.execute('ROLLBACK TO hive_write')
                self.db.execute('RELEASE hive_write')
            else:
                self.db.rollback()
            raise


def _add_session_column(db):
    """Add the session binding of agents to a hive file created before sessions were bound to their agents."""
    columns = {row[1] for row in db.execute('PRAGMA table_info(agents)')}
    if 'session_id' not in columns:
        db.execute('ALTER TABLE agents ADD COLUMN session_id TEXT')


def path_for(memory):
    """The hive file of a project: hive.sqlite beside the project database."""
    return Path(memory.path).parent / HIVE_FILE


def normalise(text):
    """The comparison form of a claim, shared with the hypotheses of focused problems."""
    from .focus import normalise as focus_normalise
    return focus_normalise(text)


# Reading the swarm.

def _require_swarm(hive, swarm_id):
    row = hive.db.execute('SELECT * FROM swarms WHERE id=?', (swarm_id,)).fetchone() if hive.db is not None else None
    if row is None:
        raise InvalidRecord(SWARM_NOT_FOUND.format(swarm=swarm_id), execution='not_started',
                            next_step={'action': 'check_swarm', 'reason': 'Use the identifier that hive_open returned.'})
    return dict(row)


def _agent(hive, swarm_id, agent_id):
    row = hive.db.execute('SELECT * FROM agents WHERE swarm_id=? AND agent_id=?', (swarm_id, agent_id)).fetchone()
    return dict(row) if row else None


def member(hive, swarm_id, agent_id):
    """The membership of an agent in a swarm. Raises when the swarm or the membership does not exist."""
    _require_swarm(hive, swarm_id)
    return _require_agent(hive, swarm_id, agent_id)


def _require_agent(hive, swarm_id, agent_id):
    agent = _agent(hive, swarm_id, agent_id)
    if agent is None:
        raise InvalidRecord(f'The agent {agent_id} has not joined the swarm {swarm_id}. Join the swarm before logging or '
                            'reading as this agent. A delegated worker is joined by its supervisor before the run starts.',
                            execution='not_started',
                            next_step={'action': 'join_swarm', 'reason': 'Join the swarm with hive_join first.'})
    return agent


def _bundle(hive, swarm_id):
    """The swarm with its agents and entries, each entry with its bases and its outgoing and incoming links."""
    swarm = _require_swarm(hive, swarm_id)
    agents = {row['agent_id']: dict(row) for row in hive.db.execute(
        'SELECT * FROM agents WHERE swarm_id=? ORDER BY joined_at, agent_id', (swarm_id,))}
    entries = []
    for row in hive.db.execute('SELECT * FROM entries WHERE swarm_id=? ORDER BY seq', (swarm_id,)):
        entry = dict(row)
        entry['data'] = json.loads(entry['data'])
        entry['bases'] = []
        entry['links_out'] = []
        entry['links_in'] = []
        entries.append(entry)
    by_id = {entry['id']: entry for entry in entries}
    for row in hive.db.execute('SELECT b.* FROM bases b JOIN entries e ON e.id=b.entry_id WHERE e.swarm_id=? '
                               'ORDER BY b.rowid', (swarm_id,)):
        by_id[row['entry_id']]['bases'].append({'kind': row['kind'], 'value': row['value'],
                                                'verified': bool(row['verified']), 'exit_code': row['exit_code']})
    for row in hive.db.execute('SELECT l.* FROM links l JOIN entries e ON e.id=l.from_entry WHERE e.swarm_id=? '
                               'ORDER BY l.rowid', (swarm_id,)):
        link = {'from': row['from_entry'], 'to': row['to_entry'], 'relation': row['relation']}
        by_id[row['from_entry']]['links_out'].append(link)
        if row['to_entry'] in by_id:
            by_id[row['to_entry']]['links_in'].append(link)
    marks = {}
    for row in hive.db.execute('SELECT m.* FROM marks m JOIN entries e ON e.id=m.entry_id WHERE e.swarm_id=?', (swarm_id,)):
        marks.setdefault(row['entry_id'], {})[row['mark']] = row['reason']
    return {'swarm': swarm, 'agents': agents, 'entries': entries, 'by_id': by_id, 'marks': marks}


def _revealed(bundle, agent):
    """True when the view of the agent is open: the swarm has no blind phase or the agent posted its hypothesis."""
    if agent is None or not bundle['swarm']['blind']:
        return True
    return agent['host'] == USER_ACTOR or agent['revealed_at'] is not None


def _visible(bundle, agent, entry):
    """True when the entry is visible to the agent. With no agent, the observer of the control panel sees everything."""
    if agent is None or entry['agent_id'] == agent['agent_id'] or _revealed(bundle, agent):
        return True
    author = bundle['agents'].get(entry['agent_id'])
    if author and author['host'] == USER_ACTOR:
        return True
    if entry['move'] in BLIND_HIDDEN:
        return False
    if entry['move'] == 'observation':
        return any(basis['verified'] for basis in entry['bases'])
    return True


def _check_passed(entry):
    """True when the entry has a verified command basis that exited with code 0."""
    return any(basis['kind'] == 'command' and basis['verified'] and basis['exit_code'] == 0 for basis in entry['bases'])


def _review_passed(bundle, entry, memory):
    agent = bundle['agents'].get(entry['agent_id'])
    if memory is None or not agent or not agent['run_id']:
        return False
    from .shared import latest_review
    try:
        review = latest_review(memory, agent['run_id'])
    except (InvalidRecord, sqlite3.Error):
        return False
    return bool(review) and review['state'] == 'pass'


def _confirmation(bundle, entry, memory=None):
    """Why a conclusion is confirmed, or None. A closed swarm answers with its recorded marks."""
    if entry['move'] != 'conclusion':
        return None
    if bundle['swarm']['state'] != 'open':
        return bundle['marks'].get(entry['id'], {}).get('confirmed')
    cited = [bundle['by_id'][link['to']] for link in entry['links_out'] if link['relation'] == 'cites' and link['to'] in bundle['by_id']]
    if _check_passed(entry) or any(_check_passed(item) for item in cited):
        return 'A cited entry has a verified command basis that exited with code 0.'
    if _review_passed(bundle, entry, memory):
        return 'The run of its agent passed cross review.'
    return None


def _verified_basis(entry):
    return any(basis['verified'] for basis in entry['bases'])


def _unresolved(bundle, challenge):
    """True when the challenged agent has not answered the challenge.

    A challenge with a verified basis is answered only by a reply of the challenged agent that carries a
    verified basis of its own, so a reply that only disagrees keeps the challenge open. Any reply of the
    challenged agent answers a challenge without a verified basis.
    """
    target = next((bundle['by_id'].get(link['to']) for link in challenge['links_out'] if link['relation'] == 'challenges'), None)
    if target is None:
        return False
    needs_evidence = _verified_basis(challenge)
    for link in challenge['links_in']:
        reply = bundle['by_id'][link['from']]
        if link['relation'] == 'replies_to' and reply['agent_id'] == target['agent_id'] and (_verified_basis(reply) or not needs_evidence):
            return False
    return True


def _dispute(bundle, entry):
    """Why a conclusion is disputed, or None."""
    if entry['move'] != 'conclusion':
        return None
    if bundle['swarm']['state'] != 'open':
        return bundle['marks'].get(entry['id'], {}).get('disputed')
    for link in entry['links_in']:
        challenge = bundle['by_id'][link['from']]
        if link['relation'] == 'challenges' and _unresolved(bundle, challenge) and _verified_basis(challenge):
            return f'The challenge {challenge["id"]} with a verified basis has no reply from the agent.'
    return None


# Opening and joining.

def open_swarm(hive, *, title, purpose, kind, request_key, episode_id=None, blind=True, project=None, base_commit=None):
    """Open a swarm and return it. A repeated request key with the same content returns the same swarm."""
    _text(title, 'title', 200)
    _text(purpose, 'purpose', 2000)
    _text(request_key, 'request_key', 200)
    if kind not in SWARM_KINDS:
        raise InvalidRecord('The swarm kind must be focus, workflow or manual.')
    if type(blind) is not bool:
        raise InvalidRecord('blind must be true or false.')
    if not blind and kind != 'manual':
        raise InvalidRecord('Only a swarm of kind manual may open without the blind phase, because independent attempts '
                            'come first in focus and workflow swarms.')
    if project is not None and base_commit is None:
        base_commit = _head(project)
    values = [title, purpose, kind, episode_id, blind, str(project) if project else None, base_commit]
    signature = _digest(dumps(values))
    with hive.write():
        prior = hive.db.execute('SELECT * FROM swarms WHERE request_key=?', (request_key,)).fetchone()
        if prior:
            if prior['request_hash'] != signature:
                raise Conflict('The request key was already used to open a different swarm.')
            return {**_swarm_row(prior), 'duplicate': True}
        swarm_id = 'swarm_' + uuid.uuid4().hex[:16]
        hive.db.execute('INSERT INTO swarms (id,title,purpose,kind,episode_id,state,opened_at,blind,project,base_commit,'
                        'request_key,request_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                        (swarm_id, title, purpose, kind, episode_id, 'open', hive.now(), int(blind), values[5], base_commit,
                         request_key, signature))
        row = hive.db.execute('SELECT * FROM swarms WHERE id=?', (swarm_id,)).fetchone()
    return {**_swarm_row(row), 'duplicate': False}


def _swarm_row(row):
    value = dict(row)
    value['blind'] = bool(value['blind'])
    value['distillation'] = json.loads(value['distillation']) if value.get('distillation') else None
    for key in ('request_key', 'request_hash', 'close_key'):
        value.pop(key, None)
    return value


def _head(project):
    from .shared import git
    try:
        result = git(project, 'rev-parse', 'HEAD', check=False, timeout=10)
    except InvalidRecord:
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def join(hive, swarm_id, *, agent_id, role, host, run_id=None, worktree=None, session_id=None):
    """Add an agent to an open swarm. Joining again with the same host and role returns the membership.

    session_id binds the agent to the session that joined it. One session joins at most one agent in a
    swarm, so a session cannot end its blind phase, support its own entries or pass the entry limits
    through a second agent name.
    """
    _text(agent_id, 'agent_id', 100)
    _text(role, 'role', 100)
    if host not in HOSTS:
        raise InvalidRecord('The host of an agent must be codex, claude, workspace-user or session.')
    if (host == USER_ACTOR) != (agent_id == USER_ACTOR):
        raise InvalidRecord('The agent name workspace-user belongs to the user, and the user joins only under that name.')
    if session_id is not None:
        _text(session_id, 'session_id', 200)
    with hive.write():
        swarm = _require_swarm(hive, swarm_id)
        prior = _agent(hive, swarm_id, agent_id)
        if prior:
            if (prior['host'], prior['role'], prior['run_id'], prior.get('session_id')) != (host, role, run_id, session_id):
                raise Conflict(f'The agent {agent_id} already joined the swarm {swarm_id} as {prior["host"]} with role '
                               f'{prior["role"]}. Choose another agent name.')
            return {**prior, 'duplicate': True}
        if swarm['state'] != 'open':
            raise InvalidRecord(f'The swarm {swarm_id} is closed. Open a new swarm for new work.')
        if session_id is not None:
            joined = hive.db.execute('SELECT agent_id FROM agents WHERE swarm_id=? AND session_id=?', (swarm_id, session_id)).fetchone()
            if joined:
                raise Conflict(f'This session already joined the swarm {swarm_id} as the agent {joined[0]}. A session takes part '
                               'in a swarm as one agent, so log and read as that agent.')
        hive.db.execute('INSERT INTO agents (swarm_id,agent_id,host,role,run_id,worktree,joined_at,session_id) VALUES (?,?,?,?,?,?,?,?)',
                        (swarm_id, agent_id, host, role, run_id, str(worktree) if worktree else None, hive.now(), session_id))
        return {**_agent(hive, swarm_id, agent_id), 'duplicate': False}


# Logging a move.

def log(hive, swarm_id, agent_id, *, move, request_key, fields=None, memory=None):
    """Validate one move and append it. memory, when given, verifies command and source bases.

    Returns {'id', 'seq', 'move', 'duplicate', 'revealed'}. A refusal records its rule
    in the refusal counts and raises Refusal, and no entry is written.
    """
    _text(request_key, 'request_key', 200)
    fields = {} if fields is None else fields
    if not isinstance(fields, dict):
        raise InvalidRecord('fields must be an object.')
    signature = _digest(dumps([move, fields]))
    swarm = _require_swarm(hive, swarm_id)
    agent = _require_agent(hive, swarm_id, agent_id)
    prior = hive.db.execute('SELECT id,seq,move,request_hash FROM entries WHERE swarm_id=? AND agent_id=? AND request_key=?',
                            (swarm_id, agent_id, request_key)).fetchone()
    if prior:
        if prior['request_hash'] != signature:
            raise Conflict('The request key was already used for a different entry.')
        return {'id': prior['id'], 'seq': prior['seq'], 'move': prior['move'], 'duplicate': True, 'revealed': False}
    if swarm['state'] != 'open':
        raise InvalidRecord(f'The swarm {swarm_id} is closed, so it accepts no new entries.', execution='not_started',
                            next_step={'action': 'stop_logging', 'reason': 'Report the remaining findings in the final answer.'})
    try:
        with hive.write():
            bundle = _bundle(hive, swarm_id)
            entry = _checked(hive, bundle, bundle['agents'][agent_id], move, fields, memory)
            return _append(hive, bundle, bundle['agents'][agent_id], entry, request_key, signature)
    except Refusal as refusal:
        with hive.write():
            hive.db.execute('INSERT INTO refusals VALUES (?,?,?,?,?)',
                            (swarm_id, agent_id, move if move in MOVES else None, refusal.rule, hive.now()))
        raise


def _checked(hive, bundle, agent, move, fields, memory):
    """Apply every rule in order and return the entry to append."""
    _check_shape(move, fields)
    own = [entry for entry in bundle['entries'] if entry['agent_id'] == agent['agent_id']]
    user = agent['host'] == USER_ACTOR
    if user and move not in USER_MOVES:
        raise Refusal('user_moves', f'The user posts question, answer and observation entries, not {move}.',
                      'Post the entry as a question, an answer or an observation.')
    addressee = fields.get('addressee')
    oriented = next((entry for entry in own if entry['move'] == 'orient'), None)
    if not user and move == 'orient' and oriented:
        raise Refusal('orient_once', f'The agent already posted its orient entry {oriented["id"]}.',
                      'Record a checkpoint to restate the goal instead.', entry_id=oriented['id'])
    if not user and move != 'orient' and not oriented and not (move == 'question' and addressee == 'user'):
        raise Refusal('orient_first', 'The first move of an agent in a swarm is orient.',
                      'Post an orient entry with the goal as its claim and at least one basis of kind source or file. '
                      'Only a question to the user is accepted before it.')
    _check_limits(own, move)
    _check_text(move, fields)
    if not user and not _revealed(bundle, agent) and (move in BLIND_REFUSED or (move == 'question' and addressee != 'user')):
        raise Refusal('blind_phase', f'The agent is in the blind phase, which does not accept a {move} entry.',
                      'Post the hypothesis of the agent first. Before it, only orient, hypothesis, observation, conclusion, pattern, '
                      'checkpoint and questions to the user are accepted.')
    links = _check_links(bundle, agent, move, fields, memory)
    bases = _check_bases(hive, bundle, agent, move, fields, memory)
    if move == 'checkpoint':
        _check_repeated_checkpoint(own, fields)
    elif move != 'orient':
        # Agents of one swarm share a goal, so their orient entries may agree.
        _check_duplicate(bundle, agent, move, fields['claim'])
    return {'move': move, 'fields': fields, 'links': links, 'bases': bases}


def _check_shape(move, fields):
    if move not in MOVES:
        raise Refusal('move', f'The move {move} is not a hive move.', 'Use one of ' + ', '.join(MOVES) + '.')
    allowed = REQUIRED[move] + OPTIONAL[move]
    unexpected = sorted(set(fields) - set(allowed))
    if unexpected:
        raise Refusal('fields', f'The move {move} does not accept the fields ' + ', '.join(unexpected) + '.',
                      'Send only ' + ', '.join(allowed) + '.', allowed=list(allowed))
    missing = [name for name in REQUIRED[move] if fields.get(name) in (None, '', [])]
    if missing:
        raise Refusal('required_fields', f'The move {move} needs the fields ' + ', '.join(missing) + '.',
                      'Add them. The move needs ' + ', '.join(REQUIRED[move]) + '.', missing=missing)
    for name, value in fields.items():
        expected = list if name in ('bases', 'cites') else str
        if not isinstance(value, expected):
            kind = 'a list' if expected is list else 'text'
            raise Refusal('field_type', f'The field {name} must be {kind}.', f'Send {name} as {kind}.')


def _check_limits(own, move):
    if len(own) >= MAX_ENTRIES:
        raise Refusal('entry_limit', f'The agent has recorded {MAX_ENTRIES} entries, the limit for one agent in a swarm.',
                      'Put the remaining findings in the final answer of the run.')
    if len(own) >= MAX_ENTRIES - CLOSING_RESERVE and move not in CLOSING_MOVES:
        raise Refusal('entry_limit', f'The agent has recorded {len(own)} of {MAX_ENTRIES} entries, and the last '
                      f'{CLOSING_RESERVE} are kept for conclusions and checkpoints.',
                      'Record a checkpoint or a conclusion instead.', suggested_moves=list(CLOSING_MOVES))
    observations = sum(1 for entry in own if entry['move'] == 'observation')
    if move == 'observation' and observations >= MAX_OBSERVATIONS:
        raise Refusal('observation_limit', f'The agent has recorded {MAX_OBSERVATIONS} observations, the limit for one '
                      'agent in a swarm.', 'Record a conclusion that draws the observations together, or a checkpoint.',
                      suggested_moves=list(CLOSING_MOVES))


def _check_text(move, fields):
    if move == 'checkpoint':
        for name in CHECKPOINT_FIELDS:
            if len(fields[name]) > CHECKPOINT_LIMIT:
                raise Refusal('checkpoint_length', f'The checkpoint field {name} has {len(fields[name])} characters, '
                              f'and the limit is {CHECKPOINT_LIMIT}.', f'Shorten {name} to at most {CHECKPOINT_LIMIT} characters.')
        return
    claim = fields['claim'].strip()
    if len(fields['claim']) > CLAIM_LIMIT:
        raise Refusal('claim_length', f'The claim has {len(fields["claim"])} characters, and the limit is {CLAIM_LIMIT}.',
                      f'Shorten the claim to one sentence of at most {CLAIM_LIMIT} characters and move the rest to detail.')
    if ('\n' in claim or '`' in claim or len(claim.split()) < 3 or not claim[0].isalnum()
            or claim[-1] not in '.?!'):
        raise Refusal('claim_sentence', 'The claim is not one complete sentence of plain text.',
                      'Write the claim as one sentence of at least three words that ends with a full stop, a question mark '
                      'or an exclamation mark, without line breaks or code.')
    detail = fields.get('detail', '')
    if len(detail) > DETAIL_LIMIT:
        raise Refusal('detail_length', f'The detail has {len(detail)} characters, and the limit is {DETAIL_LIMIT}.',
                      f'Shorten the detail to at most {DETAIL_LIMIT} characters and cite a file basis for longer material.')
    longest = _longest_code_block(detail)
    if longest > CODE_LINES:
        raise Refusal('detail_code', f'The detail holds a code block of {longest} lines, and the limit is {CODE_LINES}.',
                      f'Keep code blocks to at most {CODE_LINES} lines and point to the rest with a file basis such as '
                      'path:start-end.')
    if 'confidence' in fields and fields['confidence'] not in CONFIDENCE:
        raise Refusal('confidence', f'The confidence {fields["confidence"]} is not low, medium or high.',
                      'Set confidence to low, medium or high.')
    if 'addressee' in fields and not re.fullmatch(r'(agent|role):\S+|user|all', fields['addressee']):
        raise Refusal('addressee', f'The addressee {fields["addressee"]} is not a recognised form.',
                      'Address the question to agent:<agent id>, role:<role>, user or all.')


def _longest_code_block(text):
    longest, count, inside = 0, 0, False
    for line in text.splitlines():
        if line.strip().startswith('```'):
            if inside:
                longest = max(longest, count)
            inside, count = not inside, 0
        elif inside:
            count += 1
    return max(longest, count) if inside else longest


def _target(bundle, agent, identifier, name):
    entry = bundle['by_id'].get(identifier)
    if entry is None or not _visible(bundle, agent, entry):
        raise Refusal('target', f'The {name} {identifier} is not an entry of this swarm that the agent can see.',
                      'Use the identifier of a visible entry of this swarm, as hive_query lists it.')
    return entry


def _check_links(bundle, agent, move, fields, memory):
    links = []
    if move in TARGET_RELATION:
        target = _target(bundle, agent, fields['target'], 'target')
        if move in ('challenge', 'support') and target['agent_id'] == agent['agent_id']:
            raise Refusal('own_entry', f'The target {target["id"]} is an entry of the same agent, and an agent does not '
                          f'{move} its own entry.', 'Record an observation or a new conclusion that revises the entry instead.')
        if move == 'answer' and target['move'] != 'question':
            raise Refusal('target_move', f'The target {target["id"]} is a {target["move"]}, and an answer targets a question.',
                          'Target a question, or reply to the entry with reply_to on another move.')
        links.append((target['id'], TARGET_RELATION[move]))
    if fields.get('reply_to'):
        links.append((_target(bundle, agent, fields['reply_to'], 'reply_to')['id'], 'replies_to'))
    if move in ('conclusion', 'pattern'):
        cites = fields['cites']
        needed = 1 if move == 'conclusion' else 2
        if len(set(cites)) < needed or len(set(cites)) != len(cites) or len(cites) > MAX_CITES:
            raise Refusal('cites', f'A {move} cites at least {needed} distinct entries and at most {MAX_CITES}.',
                          f'List {needed} or more distinct entry identifiers of this swarm in cites.')
        cited = [_target(bundle, agent, identifier, 'cited entry') for identifier in cites]
        if move == 'conclusion' and not any(item['move'] in EVIDENCE_MOVES for item in cited):
            raise Refusal('cites_evidence', 'A conclusion cites at least one entry that holds evidence, and a goal, a hypothesis, '
                          'a question or a checkpoint does not.',
                          'Cite an observation, a challenge, a support, an answer or an earlier conclusion that the claim rests on.')
        if move == 'pattern' and not any(item['agent_id'] != agent['agent_id'] or _confirmation(bundle, item, memory) for item in cited):
            raise Refusal('pattern_basis', 'A pattern cites at least one entry of another agent or a confirmed conclusion.',
                          'Cite an entry of another agent or a conclusion confirmed by a check, or record a conclusion instead.')
        links.extend((item['id'], 'cites') for item in cited)
    return links


FILE_BASIS = re.compile(r'(?P<path>[^:]+):(?P<start>[1-9][0-9]*)(?:-(?P<end>[1-9][0-9]*))?')


def _check_bases(hive, bundle, agent, move, fields, memory):
    bases = fields.get('bases', [])
    if len(bases) > MAX_BASES:
        raise Refusal('basis_count', f'The entry has {len(bases)} bases, and the limit is {MAX_BASES}.',
                      f'Keep the {MAX_BASES} strongest bases.')
    checked = []
    for basis in bases:
        if not isinstance(basis, dict) or set(basis) != {'kind', 'value'} or not isinstance(basis['value'], str) \
                or not basis['value'].strip() or len(basis['value']) > 500:
            raise Refusal('basis_shape', 'Each basis is an object with kind and a value of 1 to 500 characters.',
                          'Send bases as [{"kind": "file", "value": "path:line"}].')
        if basis['kind'] not in BASIS_KINDS:
            raise Refusal('basis_kind', f'The basis kind {basis["kind"]} is not known.',
                          'Use file, command, entry, source or url.')
        checked.append(_verified(hive, bundle, agent, basis, memory))
    if move == 'orient' and not any(item['kind'] in ('source', 'file') for item in checked):
        raise Refusal('basis_kind', 'An orient entry needs at least one basis of kind source or file.',
                      'Add the source or file that states the goal, for example the plan source or a file:line basis.')
    if move in CHECKED_BASIS_MOVES and checked and all(item['kind'] == 'url' for item in checked):
        article = 'An' if move == 'observation' else 'A'
        raise Refusal('basis_checked', f'{article} {move} needs at least one basis that Project Memory can check, and a web address '
                      'cannot be checked.', 'Add a file, command, entry or source basis that the claim rests on.')
    if move == 'support':
        target = bundle['by_id'][fields['target']]
        repeated = {(item['kind'], item['value']) for item in target['bases']}
        repeated.add(('entry', target['id']))
        shared = [item['value'] for item in checked if (item['kind'], item['value']) in repeated]
        if shared:
            raise Refusal('basis_independent', f'The support repeats the basis {shared[0]} of its target.',
                          'Support the entry with evidence that its author did not use, such as another file or command.')
    return checked


def _verified(hive, bundle, agent, basis, memory):
    """Check one basis. A basis that names something missing is refused; only a url is stored without a check."""
    kind, value = basis['kind'], basis['value'].strip()
    result = {'kind': kind, 'value': value, 'verified': False, 'exit_code': None}
    if kind == 'file':
        _check_file(bundle['swarm'], agent, value)
        result['verified'] = True
    elif kind == 'command':
        if memory is None:
            raise Refusal('basis_command', f'The command basis {value} cannot be checked, because the project memory is not '
                          'available to this hive.', 'Cite a file basis instead.')
        receipt = _receipt(memory, value)
        if receipt is None or not _swarm_check(memory, bundle['swarm'], receipt):
            raise Refusal('basis_command', f'The command basis {value} is not a check result that Project Memory recorded for '
                          'this swarm.', 'Cite the receipt of a check of this swarm, as its check entries list it, or a file basis.')
        if type(receipt['payload'].get('exit_code')) is int:
            result['verified'] = True
            result['exit_code'] = receipt['payload']['exit_code']
    elif kind == 'entry':
        entry = bundle['by_id'].get(value)
        if entry is None or not _visible(bundle, agent, entry):
            # The same refusal for a missing entry and a hidden one, so the check does not reveal hidden entries.
            raise Refusal('basis_entry', f'The entry basis {value} names no entry of this swarm that the agent can see.',
                          'Use the identifier of a visible entry of this swarm, as hive_query lists it.')
        result['verified'] = True
    elif kind == 'source':
        if memory is None:
            raise Refusal('basis_source', f'The source basis {value} cannot be checked, because the project memory is not '
                          'available to this hive.', 'Cite a file basis instead.')
        if not memory.db.execute('SELECT 1 FROM sources WHERE id=?', (value,)).fetchone():
            raise Refusal('basis_source', f'The source basis {value} names no source of the main memory.',
                          'Use the identifier of a recorded source, as memory_get lists it.')
        result['verified'] = True
    elif not re.fullmatch(r'https?://\S+', value):
        raise Refusal('basis_url', f'The url basis {value} is not a web address.', 'Write a full address that starts with https://.')
    return result


def _check_file(swarm, agent, value):
    """Refuse a file basis unless it names a line range of a regular file inside the project."""
    match = FILE_BASIS.fullmatch(value)
    if not match:
        raise Refusal('basis_file', f'The file basis {value} is not in the form path:line or path:start-end.',
                      'Write a relative path inside the project followed by a line number, such as src/app.py:12.')
    start = int(match['start'])
    end = int(match['end'] or start)
    if end < start:
        raise Refusal('basis_file', f'The line range of the file basis {value} ends before it starts.',
                      'Write the first line before the last line, such as src/app.py:12-20.')
    path = PurePosixPath(match['path'])
    if path.is_absolute() or '..' in path.parts or '\\' in match['path']:
        raise Refusal('basis_file', f'The file basis {value} names a path outside the project.',
                      'Use a path relative to the project root, without .. parts.')
    lines = _file_lines(swarm, agent, str(path))
    if lines is None:
        raise Refusal('basis_file', f'The file {path} is not a file at the base commit of the swarm or in the worktree of the '
                      'agent.', 'Check the path with a file listing and cite a file that exists.')
    if end > lines:
        raise Refusal('basis_file', f'The file basis {value} names line {end}, and the file {path} has {lines} lines.',
                      'Cite lines that exist in the file.')


def _file_lines(swarm, agent, path):
    """The line count of a regular file in the worktree of the agent or at the base commit of the swarm, or None.

    A path in the worktree that resolves outside the worktree, for example through a symbolic link, does not count.
    """
    worktree = agent.get('worktree')
    if worktree:
        root = Path(worktree).resolve()
        candidate = Path(worktree) / path
        if candidate.is_file() and candidate.resolve().is_relative_to(root):
            with candidate.open('rb') as handle:
                return _count_lines(handle.read())
    if swarm['project'] and swarm['base_commit']:
        from .shared import git
        try:
            listed = git(swarm['project'], 'ls-tree', '-z', swarm['base_commit'], '--', path, check=False, timeout=10)
            fields = listed.stdout.split('\0')[0].split(None, 3) if listed.returncode == 0 else []
            if len(fields) < 4 or fields[1] != 'blob' or fields[0] not in ('100644', '100755') or fields[3] != path:
                return None
            content = git(swarm['project'], 'cat-file', 'blob', fields[2], check=False, timeout=10)
        except InvalidRecord:
            return None
        return _count_lines(content.stdout.encode()) if content.returncode == 0 else None
    return None


def _count_lines(content):
    if not content:
        return 0
    return content.count(b'\n') + (0 if content.endswith(b'\n') else 1)


def _receipt(memory, receipt_id):
    """A host receipt of the main memory as a dictionary, or None."""
    from . import codex_host
    if not codex_host.exists(memory):
        return None
    row = memory.db.execute('SELECT episode_id,event_name,payload FROM host_receipts WHERE id=?', (receipt_id,)).fetchone()
    return {'episode_id': row['episode_id'], 'event_name': row['event_name'], 'payload': json.loads(row['payload'])} if row else None


def _swarm_check(memory, swarm, receipt):
    """True when the receipt is a check result that Project Memory recorded for a start of this swarm (section 12.8).

    Other receipts, such as a command the user ran in a session, do not count, even in the episode of the swarm.
    """
    if receipt['event_name'] != CHECK_RECEIPT or swarm['episode_id'] is None or receipt['episode_id'] != swarm['episode_id']:
        return False
    start_key = receipt['payload'].get('start_key')
    for row in memory.db.execute('SELECT payload FROM host_receipts WHERE episode_id=? AND event_name=?', (swarm['episode_id'], CHECK_START)):
        started = json.loads(row['payload'])
        if started.get('swarm_id') == swarm['id'] and start_key is not None and started.get('start_key') == start_key:
            return True
    return False


def _similar(first, second):
    """True when two texts are near duplicates (section 12.3): equal normalised texts, or word sets that overlap by
    DUPLICATE_OVERLAP or more of all their words.

    A synonym for one word is not caught, on purpose: a word measure cannot tell a synonym from a changed fact, such
    as negative input and positive input, and refusing a changed fact would lose a finding.
    """
    one, two = normalise(first), normalise(second)
    words, other = set(one.split()), set(two.split())
    union = words | other
    return one == two or (bool(union) and len(words & other) / len(union) >= DUPLICATE_OVERLAP)


def _check_duplicate(bundle, agent, move, claim):
    """Refuse a near duplicate of the same move, or the same claim under another move.

    A claim may repeat a hypothesis or a question, because a conclusion that confirms a hypothesis and an answer
    that restates a question are progress, not repetition.
    """
    for entry in bundle['entries']:
        if entry['move'] in ('orient', 'checkpoint') or not _visible(bundle, agent, entry):
            continue
        same_move = entry['move'] == move and _similar(claim, entry['claim'])
        restated = entry['move'] not in ('hypothesis', 'question') and normalise(claim) == normalise(entry['claim'])
        if same_move or restated:
            raise Refusal('near_duplicate', f'The claim repeats the {entry["move"]} {entry["id"]} of agent {entry["agent_id"]}.',
                          f'Support or challenge the entry {entry["id"]} instead of repeating it. For an entry of the same agent, '
                          'record a conclusion that revises it.', entry_id=entry['id'], suggested_moves=['support', 'challenge'])


def _check_repeated_checkpoint(own, fields):
    """Refuse a checkpoint whose four fields equal those of an earlier checkpoint of the agent, after normalising.

    Checkpoints of real progress often differ in one number only, such as the step that is done, so only an equal
    checkpoint is refused.
    """
    text = [normalise(fields[name]) for name in CHECKPOINT_FIELDS]
    for entry in own:
        if entry['move'] == 'checkpoint' and [normalise(entry['data'][name]) for name in CHECKPOINT_FIELDS] == text:
            raise Refusal('near_duplicate', f'The checkpoint repeats the checkpoint {entry["id"]} of the agent.',
                          'Record a checkpoint only when the work has moved on, and state what changed.', entry_id=entry['id'])


def _append(hive, bundle, agent, entry, request_key, signature):
    fields, move = entry['fields'], entry['move']
    hive.db.execute("INSERT INTO counters VALUES ('entry', 1) ON CONFLICT(name) DO UPDATE SET value=value+1")
    seq = hive.db.execute("SELECT value FROM counters WHERE name='entry'").fetchone()[0]
    entry_id = 'e' + str(seq)
    if move == 'checkpoint':
        claim, detail = fields['belief'], ''
        data = {name: fields[name] for name in CHECKPOINT_FIELDS}
    else:
        claim, detail = fields['claim'].strip(), fields.get('detail', '')
        data = {name: fields[name] for name in ('target', 'cites', 'reply_to') if name in fields}
    hive.db.execute('INSERT INTO entries VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (entry_id, bundle['swarm']['id'], agent['agent_id'], move, claim, detail, fields.get('confidence'),
                     fields.get('addressee'), dumps(data), hive.now(), seq, request_key, signature))
    hive.db.executemany('INSERT INTO bases VALUES (?,?,?,?,?)', [
        (entry_id, basis['kind'], basis['value'], int(basis['verified']), basis['exit_code']) for basis in entry['bases']])
    hive.db.executemany('INSERT OR IGNORE INTO links VALUES (?,?,?)', [(entry_id, target, relation) for target, relation in entry['links']])
    hive.db.execute('INSERT INTO entries_fts VALUES (?,?,?)', (entry_id, claim, detail))
    revealed = move == 'hypothesis' and agent['revealed_at'] is None
    if revealed:
        hive.db.execute('UPDATE agents SET revealed_at=? WHERE swarm_id=? AND agent_id=?',
                        (hive.now(), bundle['swarm']['id'], agent['agent_id']))
    return {'id': entry_id, 'seq': seq, 'move': move, 'duplicate': False, 'revealed': revealed}


# Reading for agents.

def _compact(entry):
    return {'id': entry['id'], 'seq': entry['seq'], 'agent': entry['agent_id'], 'move': entry['move'], 'claim': entry['claim'],
            'links_in': len(entry['links_in']), 'links_out': len(entry['links_out'])}


def _addressed(bundle, agent, entry):
    """True when the question is addressed to the agent, its role or all agents."""
    return entry['addressed_to'] in ('all', 'agent:' + agent['agent_id'], 'role:' + agent['role'])


def query(hive, swarm_id, agent_id=None, *, moves=None, addressed_to=None, text=None, since_seq=0, limit=QUERY_LIMIT):
    """Compact rows of the entries the agent can see, oldest first after since_seq.

    Without agent_id the reader is the observer of the control panel and sees every entry.
    """
    if type(limit) is not int or not 1 <= limit <= QUERY_LIMIT:
        raise InvalidRecord(f'limit must be between 1 and {QUERY_LIMIT}.')
    if type(since_seq) is not int or since_seq < 0:
        raise InvalidRecord('since_seq must be a nonnegative integer.')
    if moves is not None and (not isinstance(moves, list) or set(moves) - set(MOVES)):
        raise InvalidRecord('moves must be a list of hive moves: ' + ', '.join(MOVES) + '.')
    bundle = _bundle(hive, swarm_id)
    agent = _require_agent(hive, swarm_id, agent_id) if agent_id is not None else None
    matching = None
    if text:
        words = normalise(text).split()
        if words:
            expression = ' '.join('"' + word + '"' for word in words)
            matching = {row[0] for row in hive.db.execute('SELECT entry_id FROM entries_fts WHERE entries_fts MATCH ?', (expression,))}
    rows, hidden = [], 0
    for entry in bundle['entries']:
        if entry['seq'] <= since_seq:
            continue
        if not _visible(bundle, agent, entry):
            hidden += 1
            continue
        if moves and entry['move'] not in moves:
            continue
        if addressed_to and entry['addressed_to'] != addressed_to:
            continue
        if matching is not None and entry['id'] not in matching:
            continue
        rows.append(_compact(entry))
    kept = rows[:limit]
    return {'swarm_id': swarm_id, 'phase': 'open' if _revealed(bundle, agent) else 'blind', 'entries': kept,
            'more': len(rows) > limit, 'next_since_seq': kept[-1]['seq'] if kept else since_seq, 'hidden_by_blind_phase': hidden}


def resume(hive, swarm_id, agent_id):
    """The latest checkpoint of the agent, the entries addressed to it and the unresolved challenges of its entries.

    The result fits RESUME_LIMIT characters as serialised; the rows that do not fit are counted in omitted.
    """
    bundle = _bundle(hive, swarm_id)
    agent = _require_agent(hive, swarm_id, agent_id)
    sections = _open_items(bundle, agent)
    latest = next((entry for entry in reversed(bundle['entries'])
                   if entry['agent_id'] == agent_id and entry['move'] == 'checkpoint'), None)
    checkpoint = {'id': latest['id'], **latest['data']} if latest else None
    rows = {'addressed': [_brief(entry) for entry in sections['answers'] + sections['questions']],
            'challenges': [_brief(entry) for entry in sections['challenges']]}
    result = {'swarm_id': swarm_id, 'agent_id': agent_id, 'phase': 'open' if _revealed(bundle, agent) else 'blind',
              'checkpoint': checkpoint, 'addressed': rows['addressed'], 'challenges': rows['challenges'],
              'omitted': {'addressed': 0, 'challenges': 0}, 'characters': RESUME_LIMIT}
    for key in ('challenges', 'addressed'):
        while result[key] and len(dumps(result)) > RESUME_LIMIT:
            result[key].pop()
            result['omitted'][key] += 1
    while checkpoint and len(dumps(result)) > RESUME_LIMIT:
        # The checkpoint is cut last, evenly across its four fields, and each cut field ends with an ellipsis.
        share = (len(dumps(result)) - RESUME_LIMIT) // len(CHECKPOINT_FIELDS) + 4
        longest = max(CHECKPOINT_FIELDS, key=lambda name: len(checkpoint[name]))
        for name in CHECKPOINT_FIELDS:
            text = checkpoint[name].removesuffix('...')
            if len(text) > share or name == longest:
                checkpoint[name] = text[:max(0, len(text) - share)] + '...'
    result['characters'] = len(dumps(result))
    return result


def _brief(entry):
    return {'id': entry['id'], 'agent': entry['agent_id'], 'move': entry['move'], 'claim': entry['claim']}


def _open_items(bundle, agent):
    """Answers to the questions of the agent, open questions addressed to it and unresolved challenges of its entries."""
    answers, questions, challenges = [], [], []
    for entry in bundle['entries']:
        if not _visible(bundle, agent, entry) or entry['agent_id'] == agent['agent_id']:
            continue
        target = bundle['by_id'].get(entry['data'].get('target', ''))
        if entry['move'] == 'answer' and target and target['agent_id'] == agent['agent_id']:
            answers.append(entry)
        elif entry['move'] == 'question' and _addressed(bundle, agent, entry) \
                and not any(link['relation'] == 'answers' for link in entry['links_in']):
            questions.append(entry)
        elif entry['move'] == 'challenge' and target and target['agent_id'] == agent['agent_id'] and _unresolved(bundle, entry):
            challenges.append(entry)
    return {'answers': answers, 'questions': questions, 'challenges': challenges}


# Composed hive context.

SECTIONS = (
    ('answers', 'Answers to questions of this agent:', 'answers'),
    ('questions', 'Open questions addressed to this agent or its role:', 'questions'),
    ('challenges', 'Unresolved challenges of entries of this agent:', 'challenges'),
    ('confirmed', 'Conclusions confirmed by a check or review:', 'confirmed conclusions'),
    ('patterns', 'Accepted patterns from earlier swarms:', 'accepted patterns'),
    ('latest', 'Latest conclusions of other agents:', 'conclusions of other agents'),
)


def composition(hive, swarm_id, agent_id, budget=COMPOSE_BUDGET, memory=None):
    """The composed hive context with its measurements: {'text', 'characters', 'included', 'omitted'}.

    memory, when given, lets confirmation use cross review and lets accepted patterns of earlier swarms appear.
    """
    if type(budget) is not int or budget < MIN_COMPOSE_BUDGET:
        raise InvalidRecord(f'The composition budget must be an integer of at least {MIN_COMPOSE_BUDGET} characters.')
    bundle = _bundle(hive, swarm_id)
    agent = _require_agent(hive, swarm_id, agent_id)
    items = _open_items(bundle, agent)
    visible = [entry for entry in bundle['entries'] if _visible(bundle, agent, entry)]
    confirmed = [entry for entry in visible if _confirmation(bundle, entry, memory)]
    items['confirmed'] = confirmed
    items['patterns'] = _accepted_patterns(hive, bundle, memory)
    latest = {}
    if _revealed(bundle, agent):
        for entry in visible:
            if entry['move'] == 'conclusion' and entry['agent_id'] != agent_id and entry not in confirmed:
                latest[entry['agent_id']] = entry
    items['latest'] = list(latest.values())
    phase = 'open' if _revealed(bundle, agent) else 'blind'
    header = f'Hive context of agent {agent_id} in swarm {swarm_id}, {phase} phase.'
    candidates = [(key, title, label, _line(entry, key)) for key, title, label in SECTIONS for entry in items[key]]
    kept, omitted = [], {}
    for candidate in candidates:
        if len(_render(header, kept + [candidate], {})) <= budget:
            kept.append(candidate)
        else:
            omitted[candidate[2]] = omitted.get(candidate[2], 0) + 1
    # The omission line needs room too, so the last kept lines give way until it fits.
    while omitted and len(_render(header, kept, omitted, budget)) > budget and kept:
        dropped = kept.pop()
        omitted[dropped[2]] = omitted.get(dropped[2], 0) + 1
    order = [label for _, _, label in SECTIONS]
    omitted = {label: omitted[label] for label in order if label in omitted}
    text = _render(header, kept, omitted, budget)
    included = {}
    for key, _, _, _ in kept:
        included[key] = included.get(key, 0) + 1
    return {'text': text, 'characters': len(text), 'included': included, 'omitted': omitted}


def _render(header, kept, omitted, budget=None):
    lines = [header]
    current = None
    for key, title, _, line in kept:
        if key != current:
            lines.append(title)
            current = key
        lines.append(line)
    if omitted:
        lines.append(_omission_line(omitted, budget))
    return '\n'.join(lines)


def compose(hive, swarm_id, agent_id, budget=COMPOSE_BUDGET, memory=None):
    """Deterministic hive context for a worker prompt or a session, within budget characters."""
    return composition(hive, swarm_id, agent_id, budget, memory)['text']


def _line(entry, key):
    target = entry['data'].get('target')
    relation = f' of {target}' if key == 'challenges' and target else ''
    confidence = f' ({entry["confidence"]} confidence)' if entry['confidence'] else ''
    return f'{entry["id"]} {entry["agent_id"]} {entry["move"]}{relation}{confidence}: {entry["claim"]}'


def _omission_line(omitted, budget):
    parts = [f'{count} {label}' for label, count in omitted.items()]
    return f'Omitted to fit {budget} characters: ' + ', '.join(parts) + '.'


def _accepted_patterns(hive, bundle, memory, limit=5):
    """Patterns of other swarms whose proposed lesson the user accepted, ranked by full text relevance to the purpose."""
    if memory is None:
        return []
    words = sorted({word for word in normalise(bundle['swarm']['purpose']).split() if len(word) >= 4})[:12]
    if not words:
        return []
    expression = ' OR '.join('"' + word + '"' for word in words)
    rows = hive.db.execute(
        'SELECT e.*, p.lesson_id FROM entries_fts f JOIN entries e ON e.id=f.entry_id JOIN proposals p ON p.entry_id=e.id '
        "WHERE entries_fts MATCH ? AND e.move='pattern' AND e.swarm_id!=? ORDER BY bm25(entries_fts), e.seq",
        (expression, bundle['swarm']['id'])).fetchall()
    found = []
    for row in rows:
        review = memory._lesson_review(row['lesson_id'])
        if review and review['status'] == 'accepted':
            entry = dict(row)
            entry['data'] = json.loads(entry['data'])
            found.append(entry)
        if len(found) == limit:
            break
    return found


def record_composition(hive, swarm_id, agent_id, characters, run_id=None):
    """Record the characters composed into one prompt, so the swarm summary can report them."""
    if type(characters) is not int or characters < 0:
        raise InvalidRecord('characters must be a nonnegative integer.')
    with hive.write():
        _require_swarm(hive, swarm_id)
        hive.db.execute('INSERT INTO compositions VALUES (?,?,?,?,?)', (swarm_id, agent_id, run_id, characters, hive.now()))


# Completion of a delegated run.

def check_completion(hive, swarm_id, agent_id, *, conclusion_id, checkpoint_id):
    """Whether a run names its own conclusion and checkpoint. Returns {'complete', 'problems'}."""
    problems = []
    for name, identifier, move in (('hive_conclusion_id', conclusion_id, 'conclusion'), ('hive_checkpoint_id', checkpoint_id, 'checkpoint')):
        row = None
        if hive.db is not None and isinstance(identifier, str):
            row = hive.db.execute('SELECT swarm_id,agent_id,move FROM entries WHERE id=?', (identifier,)).fetchone()
        if row is None:
            problems.append(f'The {name} {identifier} names no hive entry.')
        elif (row['swarm_id'], row['agent_id']) != (swarm_id, agent_id):
            problems.append(f'The {name} {identifier} belongs to another agent or swarm.')
        elif row['move'] != move:
            problems.append(f'The {name} {identifier} is a {row["move"]}, not a {move}.')
    return {'complete': not problems, 'problems': problems}


# Listing for sessions and the control panel.

def swarms(hive, *, limit=20, offset=0):
    """Swarms with state, kind, agents and counts, newest first."""
    if hive.db is None:
        return {'swarms': [], 'total': 0, 'max_seq': 0}
    total = hive.db.execute('SELECT count(*) FROM swarms').fetchone()[0]
    listed = []
    for row in hive.db.execute('SELECT * FROM swarms ORDER BY opened_at DESC, id LIMIT ? OFFSET ?', (limit, offset)).fetchall():
        swarm = _swarm_row(row)
        swarm.pop('distillation')
        swarm['agents'] = [{'agent_id': agent['agent_id'], 'host': agent['host'], 'role': agent['role'],
                            'revealed': agent['revealed_at'] is not None}
                           for agent in hive.db.execute('SELECT * FROM agents WHERE swarm_id=? ORDER BY joined_at', (row['id'],))]
        swarm['moves'] = dict(hive.db.execute('SELECT move,count(*) FROM entries WHERE swarm_id=? GROUP BY move ORDER BY move',
                                              (row['id'],)).fetchall())
        swarm['entries'] = sum(swarm['moves'].values())
        listed.append(swarm)
    return {'swarms': listed, 'total': total, 'max_seq': max_seq(hive)}


def max_seq(hive):
    """The highest entry sequence ever given, for the change polling of the control panel."""
    if hive.db is None:
        return 0
    row = hive.db.execute("SELECT value FROM counters WHERE name='entry'").fetchone()
    return row[0] if row else 0


# Distillation and closing.

def distill(hive, swarm_id, memory=None):
    """The mechanical distillation of a swarm, without writing anything."""
    bundle = _bundle(hive, swarm_id)
    conclusions = [entry for entry in bundle['entries'] if entry['move'] == 'conclusion']
    confirmed = {entry['id']: reason for entry in conclusions if (reason := _confirmation(bundle, entry, memory))}
    disputed = {entry['id']: reason for entry in conclusions if (reason := _dispute(bundle, entry))}
    citations = Counter()
    for entry in bundle['entries']:
        for link in entry['links_out']:
            if link['relation'] == 'cites':
                citations[link['to']] += 1
        for basis in entry['bases']:
            if basis['kind'] == 'entry' and basis['value'] in bundle['by_id']:
                citations[basis['value']] += 1
    # A conclusion that is confirmed and also disputed does not carry a pattern into a lesson.
    settled = set(confirmed) - set(disputed)
    patterns = [entry['id'] for entry in bundle['entries'] if entry['move'] == 'pattern'
                and any(link['relation'] == 'cites' and link['to'] in settled for link in entry['links_out'])]
    refusals = dict(hive.db.execute('SELECT rule,count(*) FROM refusals WHERE swarm_id=? GROUP BY rule ORDER BY rule', (swarm_id,)).fetchall())
    composed = hive.db.execute('SELECT count(*),coalesce(sum(characters),0) FROM compositions WHERE swarm_id=?', (swarm_id,)).fetchone()
    return {'swarm_id': swarm_id, 'entries': len(bundle['entries']),
            'moves': dict(sorted(Counter(entry['move'] for entry in bundle['entries']).items())),
            'entries_per_agent': dict(sorted(Counter(entry['agent_id'] for entry in bundle['entries']).items())),
            'conclusions': len(conclusions), 'confirmed': confirmed, 'disputed': disputed,
            'citations': {key: citations[key] for key in bundle['by_id'] if citations[key]},
            'pattern_candidates': patterns, 'refusals': refusals,
            'composed': {'prompts': composed[0], 'characters': composed[1]}}


def close(hive, swarm_id, *, summary, request_key, memory=None):
    """Close a swarm and distill it.

    Marks confirmed and disputed conclusions, proposes one lesson in the main memory for each
    pattern that cites a confirmed conclusion, and writes a summary note in the episode of the
    swarm. Without memory or episode nothing is written to the main memory, and the result says so.
    """
    _text(summary, 'summary', 2000)
    _text(request_key, 'request_key', 200)
    swarm = _require_swarm(hive, swarm_id)
    if swarm['state'] != 'open':
        if swarm['close_key'] == request_key:
            return {**json.loads(swarm['distillation']), 'duplicate': True}
        raise Conflict(f'The swarm {swarm_id} is already closed.')
    result = distill(hive, swarm_id, memory)
    bundle = _bundle(hive, swarm_id)
    result['proposals'] = []
    result['main_memory'] = 'not_available'
    if memory is not None and swarm['episode_id']:
        result['main_memory'] = 'recorded'
        settled = {key: reason for key, reason in result['confirmed'].items() if key not in result['disputed']}
        for entry_id in result['pattern_candidates']:
            result['proposals'].append(_propose(memory, bundle, bundle['by_id'][entry_id], settled))
        result['summary_record'] = _summary_note(memory, bundle, result, summary)
    result['duplicate'] = False
    with hive.write():
        now = hive.now()
        for mark in ('confirmed', 'disputed'):
            hive.db.executemany('INSERT OR IGNORE INTO marks VALUES (?,?,?,?)',
                                [(entry_id, mark, reason, now) for entry_id, reason in result[mark].items()])
        hive.db.executemany('INSERT OR IGNORE INTO proposals VALUES (?,?,?,?)',
                            [(item['entry_id'], item['lesson_id'], item['source_id'], now) for item in result['proposals']])
        hive.db.execute("UPDATE swarms SET state='closed', closed_at=?, summary=?, close_key=?, distillation=? WHERE id=?",
                        (now, summary, request_key, dumps(result), swarm_id))
    return result


def _propose(memory, bundle, pattern, confirmed):
    """Propose one lesson for a pattern through the normal lesson path. The user accepts or rejects it."""
    swarm = bundle['swarm']
    episode = memory.episode(swarm['episode_id'])
    cited = [link['to'] for link in pattern['links_out'] if link['relation'] == 'cites']
    key = 'hive-lesson:' + swarm['id'] + ':' + pattern['id']
    body = dumps({'swarm_id': swarm['id'], 'pattern_id': pattern['id'], 'agent_id': pattern['agent_id'], 'claim': pattern['claim'],
                  'detail': pattern['detail'], 'cited_entries': cited, 'confirmed_conclusions': [i for i in cited if i in confirmed]})
    with memory._write():
        prior = memory.db.execute('SELECT id FROM events WHERE request_key=?', (key,)).fetchone()
        source = memory.db.execute("SELECT id FROM sources WHERE source_key=? ORDER BY version DESC LIMIT 1", (key,)).fetchone()
        source_id = source[0] if source else memory.source(key, 'Hive pattern ' + pattern['id'], pattern['claim'], body, 'tool',
                                                           subject=episode['subject'])['id']
        if prior:
            return {'entry_id': pattern['id'], 'lesson_id': prior[0], 'source_id': source_id, 'duplicate': True}
        confirmed_cited = ', '.join(i for i in cited if i in confirmed)
        because = (f'The pattern {pattern["id"]} of the hive swarm {swarm["id"]} cites the confirmed conclusions {confirmed_cited}. '
                   + pattern['detail']).strip()
        lesson = memory.record(swarm['episode_id'], 'lesson', {
            'when': ('When the work resembles this purpose: ' + swarm['purpose'])[:2000],
            'do': pattern['claim'], 'because': because[:12000],
            'exceptions': 'None were recorded in the hive swarm.'},
            expected_version=episode['version'], actor=HIVE_ACTOR, request_key=key,
            evidence=[{'source_id': source_id, 'reason': 'The source quotes the pattern and lists the hive entries it cites.'}])
    return {'entry_id': pattern['id'], 'lesson_id': lesson['id'], 'source_id': source_id, 'duplicate': lesson['duplicate']}


def _summary_note(memory, bundle, result, summary):
    swarm = bundle['swarm']
    key = 'hive-summary:' + swarm['id']
    episode = memory.episode(swarm['episode_id'])
    counts = {name: result[name] for name in ('entries', 'moves', 'entries_per_agent', 'conclusions', 'refusals', 'composed')}
    body = dumps({**counts, 'swarm_id': swarm['id'], 'confirmed': sorted(result['confirmed']), 'disputed': sorted(result['disputed']),
                  'proposals': [item['lesson_id'] for item in result['proposals']], 'summary': summary})
    moves = ', '.join(f'{count} {move}' for move, count in result['moves'].items()) or 'no entries'
    text = (f'The hive swarm {swarm["title"]} ({swarm["id"]}) closed with {result["entries"]} entries: {moves}. '
            f'{len(result["confirmed"])} of {result["conclusions"]} conclusions were confirmed and {len(result["disputed"])} were '
            f'disputed. {len(result["proposals"])} lessons were proposed. {result["composed"]["characters"]} characters of hive '
            f'context were composed into prompts. Refusals by rule: '
            + (', '.join(f'{rule} {count}' for rule, count in result['refusals'].items()) or 'none') + '. ' + summary)
    with memory._write():
        prior = memory.db.execute('SELECT id FROM events WHERE request_key=?', (key,)).fetchone()
        if prior:
            return prior[0]
        source = memory.source(key, 'Hive swarm summary', ('Summary of the hive swarm ' + swarm['title'])[:2000], body, 'tool',
                               subject=episode['subject'])
        note = memory.record(swarm['episode_id'], 'note', {'text': text[:12000]}, expected_version=episode['version'],
                             actor=HIVE_ACTOR, request_key=key,
                             evidence=[{'source_id': source['id'], 'reason': 'The source holds the counts of the swarm distillation.'}])
    return note['id']


# Retention.

def purge(hive, memory, *, closed_before_days, actor):
    """Remove whole swarms closed more than closed_before_days ago. Only the user purges.

    The main memory records one HivePurged receipt with counts only.
    """
    if actor != USER_ACTOR:
        raise InvalidRecord(PURGE_USER_ONLY, execution='not_started',
                            next_step={'action': 'ask_user', 'reason': 'Ask the user to purge closed swarms.'})
    if type(closed_before_days) is not int or closed_before_days < 0:
        raise InvalidRecord('closed_before_days must be a nonnegative integer.')
    counts = {'swarms': 0, 'agents': 0, 'entries': 0, 'closed_before_days': closed_before_days}
    if hive.db is not None:
        cutoff = (datetime.fromisoformat(hive.now()) - timedelta(days=closed_before_days)).isoformat(timespec='microseconds')
        with hive.write():
            ids = [row[0] for row in hive.db.execute("SELECT id FROM swarms WHERE state='closed' AND closed_at<=? ORDER BY id", (cutoff,))]
            for swarm_id in ids:
                counts['agents'] += hive.db.execute('SELECT count(*) FROM agents WHERE swarm_id=?', (swarm_id,)).fetchone()[0]
                counts['entries'] += hive.db.execute('SELECT count(*) FROM entries WHERE swarm_id=?', (swarm_id,)).fetchone()[0]
                hive.db.execute("UPDATE swarms SET state='purging' WHERE id=?", (swarm_id,))
                inside = '(SELECT id FROM entries WHERE swarm_id=?)'
                for statement in (f'DELETE FROM links WHERE from_entry IN {inside} OR to_entry IN {inside}',
                                  f'DELETE FROM bases WHERE entry_id IN {inside}', f'DELETE FROM marks WHERE entry_id IN {inside}',
                                  f'DELETE FROM proposals WHERE entry_id IN {inside}', f'DELETE FROM entries_fts WHERE entry_id IN {inside}'):
                    hive.db.execute(statement, (swarm_id,) * statement.count('?'))
                for table in ('entries', 'agents', 'refusals', 'compositions'):
                    hive.db.execute(f'DELETE FROM {table} WHERE swarm_id=?', (swarm_id,))
                hive.db.execute('DELETE FROM swarms WHERE id=?', (swarm_id,))
                counts['swarms'] += 1
    counts['receipt_id'] = _purge_receipt(memory, counts)
    return counts


def _purge_receipt(memory, counts):
    from . import codex_host
    with memory._write():
        if not codex_host.exists(memory):
            from .reviews import _statements
            for statement in _statements(codex_host.HOST_SCHEMA):
                memory.db.execute(statement)
        now = memory.now()
        return codex_host.receipt(memory, session_id='hive-purge', event_name='HivePurged', payload={**counts, 'purged_at': now},
                                  key='hive-purge:' + now)


# The session adapter of the main MCP server.

RESERVED_AGENT_NAMES = {name.replace('_', ' ').replace('-', ' ') for name in RESERVED_ACTORS} | {'focus orchestrator', 'hive distiller'}


SESSION_NEEDED = ('A session takes part in a swarm through its own session_id. Pass session_id with the hive action, the same value '
                  'for every call of the session.')


def _session_agent(hive, swarm_id, agent_id, session_id):
    """The agent of the session in a swarm. A session logs and reads only as the one agent that it joined itself."""
    agent = _require_agent(hive, swarm_id, agent_id)
    if agent['host'] != 'session' or agent.get('session_id') is None or agent.get('session_id') != session_id:
        raise InvalidRecord(f'The agent {agent_id} is not the agent that this session joined in the swarm {swarm_id}. A session '
                            'logs and reads only as the agent that it joined itself.', execution='not_started',
                            next_step={'action': 'join_swarm', 'reason': 'Join the swarm under an agent name of the session, or '
                                       'use the agent name that this session joined with.'})
    return agent


def _require_session(session_id):
    if not isinstance(session_id, str) or not session_id.strip():
        raise InvalidRecord(SESSION_NEEDED, execution='not_started',
                            next_step={'action': 'correct_arguments', 'reason': 'Send the request again with session_id.'})


def session_write(memory, action, *, request_key, session_id=None, swarm_id=None, title=None, purpose=None, kind=None, episode_id=None,
                  blind=None, agent_id=None, role=None, move=None, fields=None, summary=None):
    """The hive actions of a session over memory_write: open, join, log and close.

    A session joins as host session in the project folder, at most once in each swarm, and logs only
    as the agent it joined itself, identified by its session_id. It never uses the names reserved for
    the user or for Project Memory.
    """
    if agent_id is not None and agent_id.strip().lower().replace('_', ' ').replace('-', ' ') in RESERVED_AGENT_NAMES:
        raise InvalidRecord(f'The agent name {agent_id} is reserved for the user or for Project Memory. Choose an agent name of its own for the session.',
                            execution='not_started', next_step={'action': 'correct_arguments', 'reason': 'Choose another agent_id.'})
    needed = {'open': ('title', 'purpose', 'kind'), 'join': ('swarm_id', 'agent_id', 'role'),
              'log': ('swarm_id', 'agent_id', 'move'), 'close': ('swarm_id', 'summary')}[action]
    values = {'swarm_id': swarm_id, 'title': title, 'purpose': purpose, 'kind': kind, 'agent_id': agent_id, 'role': role,
              'move': move, 'summary': summary}
    missing = [name for name in needed if values[name] is None]
    if missing:
        raise InvalidRecord(f'The hive action {action} needs ' + ', '.join(missing) + '.', execution='not_started',
                            next_step={'action': 'correct_arguments', 'read_with': {'view': 'schema', 'id': 'hive'},
                                       'reason': 'Add the listed fields and send the request again.'})
    if action in ('join', 'log'):
        _require_session(session_id)
    from .guards import project_root
    project = project_root(memory)
    with Hive(path_for(memory)) as hive:
        if action == 'open':
            if episode_id is not None:
                memory.episode(episode_id)
            return open_swarm(hive, title=title, purpose=purpose, kind=kind, request_key=request_key, episode_id=episode_id,
                              blind=True if blind is None else blind, project=project)
        if action == 'join':
            return join(hive, swarm_id, agent_id=agent_id, role=role, host='session', worktree=project, session_id=session_id)
        if action == 'log':
            _session_agent(hive, swarm_id, agent_id, session_id)
            return log(hive, swarm_id, agent_id, move=move, request_key=request_key, fields=fields, memory=memory)
        return close(hive, swarm_id, summary=summary, request_key=request_key, memory=memory)


def session_read(memory, swarm_id, options, session_id=None):
    """The hive reads of a session over memory_get: swarms without an id, otherwise query or resume of options['agent_id'].

    A read of one swarm is made as the agent that this session joined, so the blind phase holds for sessions too.
    """
    with Hive(path_for(memory), read_only=True) as hive:
        if swarm_id is None:
            return swarms(hive, limit=options.get('limit', 20), offset=options.get('offset', 0))
        options = dict(options)
        action = options.pop('action', 'query')
        agent_id = options.pop('agent_id')
        _require_swarm(hive, swarm_id)
        _require_session(session_id)
        _session_agent(hive, swarm_id, agent_id, session_id)
        if action == 'resume':
            return resume(hive, swarm_id, agent_id)
        return query(hive, swarm_id, agent_id, **options)


def session_context(memory, session_id, budget=COMPOSE_BUDGET, limit=3):
    """The composed hive context of each open swarm that the session joined, newest swarm first (section 12.7).

    Returns a list of {swarm_id, agent_id, text}, empty when the session joined no open swarm or the hive
    file does not exist.
    """
    if not isinstance(session_id, str) or not session_id.strip():
        return []
    with Hive(path_for(memory), read_only=True) as hive:
        if hive.db is None:
            return []
        columns = {row[1] for row in hive.db.execute('PRAGMA table_info(agents)')}
        if 'session_id' not in columns:
            return []
        rows = hive.db.execute("SELECT a.swarm_id, a.agent_id FROM agents a JOIN swarms s ON s.id=a.swarm_id WHERE a.session_id=? "
                               "AND a.host='session' AND s.state='open' ORDER BY s.opened_at DESC, s.id LIMIT ?",
                               (session_id, limit)).fetchall()
        return [{'swarm_id': row['swarm_id'], 'agent_id': row['agent_id'],
                 'text': compose(hive, row['swarm_id'], row['agent_id'], budget, memory)} for row in rows]
