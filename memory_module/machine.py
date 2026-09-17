"""The memory of this machine: the rules promoted out of its projects and the registry of those projects.

One machine runs many projects, each with its own memory. Above them sits one
memory for the machine itself. It is an ordinary Project Memory database,
created through `Memory.create`, so the same store, validation and retrieval
apply. Its project name is the machine name.

What it holds: rules the user promoted out of a single project, and a registry
of the projects on this machine with their path, template and phase. The
registry stays in this database and is never exported.

What a promoted rule must never carry: the name of a project, an absolute path,
a record identifier, a document file name of the project, an electronic mail
address or a host name. `inspect_text` checks the text mechanically and reports
which check matched and in which field, without repeating the value.

Who writes what. An agent proposes a promotion with `propose`, which records the
proposal in the project and writes nothing to the machine memory. The user
accepts it in the control panel with `accept`, which writes the rule into the
machine memory as workspace-user and records the acceptance in the project by
identifier only.

Every read works on a read only connection and on a machine database that does
not exist yet, so a project may read the machine rules before the user has
promoted anything.
"""
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import stat
import uuid

from .core import Conflict, InvalidRecord, Memory, MemoryError, USER_ACTOR, _digest, _text, dumps

DATABASE_VARIABLE = 'PROJECT_MEMORY_MACHINE_DB'
DEFAULT_DIRECTORY = '.project-memory'
DEFAULT_FILE = 'machine.sqlite'
FALLBACK_NAME = 'This machine'
RULES_EPISODE = 'Promoted rules'
RULES_OBJECTIVE = 'Hold the rules the user promoted out of single projects on this machine.'
RULES_CRITERION = 'Every rule is accepted by the user and carries the basis written at promotion.'
BASIS_PREFIX = 'promotion-basis:'
REQUIREMENTS = [
    'This memory holds the rules the user promoted out of the projects on this machine, and the registry of those projects.',
    'Only the user writes to this memory. A project proposes a promotion and reads the rules that were accepted.',
    'A promoted rule carries its own text and the short basis written at promotion, and nothing that identifies a project.',
    'The registry of projects stays in this database and is never exported.',
]
STATES = ('proposed', 'accepted', 'declined')
RULE_FIELDS = ('when', 'do', 'because', 'exceptions')
PROMOTION_STATES_NOTE = ('A proposal is recorded in this project only. Nothing is written to the machine memory until the '
                         'user accepts it in the control panel.')
ISOLATION_NOTE = ('The machine memory holds the rules the user promoted and the registry of the projects on this machine. '
                  'Effectiveness stays in each project: the adoption count reports how many projects promoted a rule, and no '
                  'outcome is combined across projects.')
NOT_CREATED_NOTE = ('No machine memory exists on this computer yet. It is created by project-memory machine init or by the '
                    'first promotion the user accepts.')
PROMOTION_SCHEMA = '''
CREATE TABLE IF NOT EXISTS rule_promotions (
 id TEXT PRIMARY KEY, lesson_id TEXT, state TEXT NOT NULL, actor TEXT NOT NULL, proposed_at TEXT NOT NULL,
 request_key TEXT UNIQUE NOT NULL, signature TEXT NOT NULL, rule TEXT NOT NULL, basis TEXT NOT NULL,
 decided_at TEXT, decided_by TEXT, decision_reason TEXT, machine_rule_id TEXT
);
CREATE INDEX IF NOT EXISTS promotion_state ON rule_promotions(state,proposed_at);
CREATE TRIGGER IF NOT EXISTS immutable_promotion_delete BEFORE DELETE ON rule_promotions BEGIN
 SELECT RAISE(ABORT,'A recorded promotion cannot be deleted.'); END;
CREATE TRIGGER IF NOT EXISTS immutable_promotion_proposal BEFORE UPDATE ON rule_promotions
 WHEN NEW.id!=OLD.id OR NEW.rule!=OLD.rule OR NEW.basis!=OLD.basis OR NEW.actor!=OLD.actor
 OR NEW.proposed_at!=OLD.proposed_at OR OLD.state!='proposed' BEGIN
 SELECT RAISE(ABORT,'A proposal and a decided promotion cannot be changed.'); END;
CREATE TRIGGER IF NOT EXISTS immutable_promotion_decision BEFORE UPDATE ON rule_promotions
 WHEN NEW.lesson_id IS NOT OLD.lesson_id OR NEW.request_key IS NOT OLD.request_key
 OR NEW.signature IS NOT OLD.signature OR NEW.state NOT IN ('accepted','declined')
 OR NEW.decided_by IS NOT 'workspace-user' OR NEW.decided_at IS NULL OR NEW.decision_reason IS NULL
 OR (NEW.state='accepted' AND (NEW.machine_rule_id IS NULL OR NEW.machine_rule_id NOT GLOB 'event_*'))
 OR (NEW.state='declined' AND NEW.machine_rule_id IS NOT NULL) BEGIN
 SELECT RAISE(ABORT,'A proposal changes only once, to the decision of the user, and keeps its text, key and lesson.'); END;
'''
PROMOTION_MARKER = 'immutable_promotion_decision'
REGISTRY_SCHEMA = '''
CREATE TABLE IF NOT EXISTS machine_projects (
 id TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL, template TEXT, phase TEXT NOT NULL, phase_at TEXT,
 first_seen TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS machine_adoptions (
 rule_id TEXT NOT NULL, project_id TEXT NOT NULL, adopted_at TEXT NOT NULL, basis TEXT NOT NULL,
 PRIMARY KEY (rule_id, project_id)
);
CREATE TRIGGER IF NOT EXISTS immutable_adoption_delete BEFORE DELETE ON machine_adoptions BEGIN
 SELECT RAISE(ABORT,'A recorded adoption cannot be deleted.'); END;
CREATE TRIGGER IF NOT EXISTS immutable_adoption_update BEFORE UPDATE ON machine_adoptions BEGIN
 SELECT RAISE(ABORT,'A recorded adoption cannot be changed.'); END;
CREATE TRIGGER IF NOT EXISTS immutable_registry_identity BEFORE UPDATE ON machine_projects
 WHEN NEW.id!=OLD.id OR NEW.path!=OLD.path OR NEW.first_seen!=OLD.first_seen BEGIN
 SELECT RAISE(ABORT,'A registered project keeps its identifier, path and first record.'); END;
CREATE TRIGGER IF NOT EXISTS immutable_registry_delete BEFORE DELETE ON machine_projects BEGIN
 SELECT RAISE(ABORT,'A registered project cannot be deleted.'); END;
'''
REGISTRY_MARKER = 'immutable_registry_delete'
# The usage ledger of section 16.2, written by usage.py. It records numbers, times and short fixed names only: no
# message content, prompt, file path, project name or session title. A log file is known by a digest of its file
# system identity, and an entry by a digest of what makes it unique, so a second collection changes nothing.
USAGE_SCHEMA = '''
CREATE TABLE IF NOT EXISTS usage_files (
 identity TEXT PRIMARY KEY, source TEXT NOT NULL, read_offset INTEGER NOT NULL, size INTEGER NOT NULL,
 modified_ns INTEGER NOT NULL, head_length INTEGER NOT NULL, head_digest TEXT NOT NULL, state TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS usage_records (
 entry TEXT PRIMARY KEY, host TEXT NOT NULL, kind TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT NOT NULL,
 input_tokens INTEGER, cached_input_tokens INTEGER, cache_write_tokens INTEGER, output_tokens INTEGER,
 reasoning_tokens INTEGER, total_tokens INTEGER, cost REAL, currency TEXT, duration_ms INTEGER, recorded_at TEXT NOT NULL,
 session TEXT, context_tokens INTEGER
);
CREATE INDEX IF NOT EXISTS usage_record_time ON usage_records(host, ended_at);
CREATE TABLE IF NOT EXISTS usage_limits (
 host TEXT NOT NULL, limit_id TEXT NOT NULL, window TEXT NOT NULL, used_percent REAL NOT NULL, window_minutes INTEGER,
 resets_at TEXT, observed_at TEXT NOT NULL, PRIMARY KEY (host, limit_id, window)
);
CREATE TABLE IF NOT EXISTS usage_limit_hits (
 entry TEXT PRIMARY KEY, host TEXT NOT NULL, reason TEXT NOT NULL, hit_at TEXT NOT NULL, until TEXT
);
CREATE INDEX IF NOT EXISTS usage_limit_hit_time ON usage_limit_hits(host, hit_at);
'''
USAGE_MARKER = 'usage_limit_hit_time'
USAGE_TABLES = ('usage_files', 'usage_records', 'usage_limits', 'usage_limit_hits')
# Section 17.1 added the session digest and the context size of each record. A ledger created before receives the two
# columns on its next write; its earlier records keep no session and are left out of the session statistics.
USAGE_COLUMNS = (('session', 'TEXT'), ('context_tokens', 'INTEGER'))
# The folder of the machine memory is readable by its owner only, and so are the database and its two SQLite files.
FOLDER_MODE = 0o700
FILE_MODE = 0o600
DATABASE_FILES = (('database', ''), ('write_ahead_log', '-wal'), ('shared_memory', '-shm'))


def ensure_usage(machine):
    """Create the tables of the usage ledger in the machine memory once, and add the columns of section 17.1 once."""
    _ensure(machine, USAGE_SCHEMA, USAGE_MARKER)
    present = {row[1] for row in machine.db.execute('PRAGMA table_info(usage_records)')}
    missing = [(name, kind) for name, kind in USAGE_COLUMNS if name not in present]
    if missing or machine.db.execute("SELECT 1 FROM sqlite_master WHERE name='usage_record_session'").fetchone() is None:
        with machine._write():
            for name, kind in missing:
                machine.db.execute('ALTER TABLE usage_records ADD COLUMN ' + name + ' ' + kind)
            machine.db.execute('CREATE INDEX IF NOT EXISTS usage_record_session ON usage_records(host, session, ended_at)')


def has_usage_sessions(machine):
    """True when the ledger carries the session columns, so a read only connection never needs the migration."""
    return {name for name, _ in USAGE_COLUMNS} <= {row[1] for row in machine.db.execute('PRAGMA table_info(usage_records)')}


def has_usage(machine):
    """True when the usage ledger exists, so a read path never creates it."""
    return _table_exists(machine, 'usage_records')


# The machine database.

def machine_name():
    """The name of this machine, used as the project name of its memory."""
    try:
        name = socket.gethostname()
    except OSError:
        name = ''
    return name.strip() or FALLBACK_NAME


def database_path(path=None):
    """The machine database: the given path, the environment variable, or the file in the home folder."""
    if path:
        return Path(path).expanduser()
    configured = os.environ.get(DATABASE_VARIABLE, '').strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / DEFAULT_DIRECTORY / DEFAULT_FILE


def _mode_targets(target):
    """The folder and files whose modes are kept narrow. Only the folder of the default name is changed, because a
    database placed elsewhere by the environment variable may sit in a folder that other programs share."""
    found = []
    if target.parent.name == DEFAULT_DIRECTORY:
        found.append(('folder', target.parent, FOLDER_MODE))
    found += [(name, target.with_name(target.name + suffix), FILE_MODE) for name, suffix in DATABASE_FILES]
    return found


def secure(path=None):
    """Narrow the modes of the machine folder and database files to their owner. File modes are not used on Windows."""
    if os.name == 'nt':
        return
    for _, item, mode in _mode_targets(database_path(path)):
        try:
            current = stat.S_IMODE(item.stat().st_mode)
            if current & ~mode:
                item.chmod(mode)
        except OSError:
            continue


def permissions(path=None):
    """The modes of the machine folder and database files, and every one that is wider than its owner only.

    Doctor reports this. Nothing is changed here; the next write to the machine memory corrects a wider mode.
    """
    if os.name == 'nt':
        return {'status': 'not_checked', 'wider': [], 'note': 'File modes are not checked on Windows.'}
    target = database_path(path)
    wider = []
    for name, item, mode in _mode_targets(target):
        try:
            current = stat.S_IMODE(item.stat().st_mode)
        except OSError:
            continue
        if current & ~mode:
            wider.append({'item': name, 'mode': format(current, '04o'), 'expected': format(mode, '04o')})
    if not target.exists():
        return {'status': 'not_created', 'wider': wider, 'note': 'No machine memory exists on this computer yet.'}
    if wider:
        return {'status': 'too_wide', 'wider': wider,
                'note': 'Other local users may read the machine memory. The next write to it narrows the modes to its owner.'}
    return {'status': 'owner_only', 'wider': [], 'note': 'Only the owner can read the machine memory.'}


def writer(path=None, *, clock=None):
    """The machine memory opened for writing, with its folder and files narrowed to the owner before and after opening,
    because SQLite creates the two files beside the database when it opens it."""
    target = database_path(path)
    secure(target)
    store = Memory(target, clock=clock)
    secure(target)
    return store


def exists(path=None):
    """True when the machine database is already created."""
    return database_path(path).exists()


def create(path=None, *, name=None, clock=None):
    """Create the machine memory. A machine has one, so a second call reports the existing file."""
    target = database_path(path)
    if target.exists():
        raise Conflict('This machine already has a memory. Open it instead of creating a second one.')
    if target.parent.name == DEFAULT_DIRECTORY:
        target.parent.mkdir(mode=FOLDER_MODE, parents=True, exist_ok=True)
    store = Memory.create(target, name or machine_name(), list(REQUIREMENTS), clock=clock)
    secure(target)
    return store


def initialize(path=None, *, name=None, clock=None):
    """Create the machine memory when it is missing and report where it is."""
    target = database_path(path)
    created = not target.exists()
    if created:
        create(target, name=name, clock=clock).close()
    with writer(target, clock=clock) as store:
        return {'database': str(target), 'machine': store.project, 'created': created}


def open_machine(path=None, *, read_only=False, clock=None):
    """The machine memory, or None when it does not exist yet."""
    target = database_path(path)
    if not target.exists():
        return None
    return Memory(target, read_only=True, clock=clock) if read_only else writer(target, clock=clock)


def reader(path=None):
    """A read only connection to the machine memory, or None when it does not exist yet."""
    return open_machine(path, read_only=True)


def _statements(script):
    """Split a schema script into complete statements, including trigger bodies."""
    buffer = ''
    for line in script.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            if buffer.strip():
                yield buffer
            buffer = ''


def _table_exists(memory, name):
    """True when the table is present, so a read path never creates it."""
    return memory.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _ensure(memory, script, marker):
    """Create the tables and triggers of a script once, inside the current write.

    The marker is the last object of the script, so a database created before a
    trigger was added receives it on the next write.
    """
    if memory.db.execute('SELECT 1 FROM sqlite_master WHERE name=?', (marker,)).fetchone() is not None:
        return
    with memory._write():
        for statement in _statements(script):
            memory.db.execute(statement)


# The mechanical checks of a promotion.

# An address may use a local host without a top level domain and letters outside ASCII.
ELECTRONIC_MAIL = re.compile(r'(?<![\w.%+-])[\w.%+-]+@[^\W_](?:[\w.-]*[^\W_])?')
ABSOLUTE_PATH = re.compile(r'(?<![\w/])(?:~|[A-Za-z]:[\\/]|file://)?/?(?:/[A-Za-z0-9._@~+-]+){1,}/?')
WINDOWS_PATH = re.compile(r'(?<![\w])[A-Za-z]:\\[^\s]*')
# The home folder of a named user, a network share, a path that starts at an environment variable, and a
# path inside the folder of a user written without its leading separator.
HOME_PATH = re.compile(r'(?<![\w/~])~[A-Za-z0-9._-]*[\\/]')
NETWORK_PATH = re.compile(r'(?<![\w\\])\\\\[^\s\\/]+[\\/]')
VARIABLE_PATH = re.compile(r'(?:%[A-Za-z_][A-Za-z0-9_]*%|\$\{?[A-Za-z_][A-Za-z0-9_]*\}?)[\\/]')
USER_FOLDER = re.compile(r'(?<![\w.])(?:Users|home)[\\/][^\s\\/]+[\\/]', re.IGNORECASE)
# A record identifier in any letter case: a dashed UUID, a prefix with a hexadecimal part of eight or more
# characters, which also finds an identifier cut short, or a bare hexadecimal run of twelve or more.
RECORD_IDENTIFIER = re.compile(
    r'(?<![A-Za-z0-9])(?:'
    r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
    r'|[a-z][a-z0-9_]*?_(?=[0-9a-f]*[0-9])(?=[0-9a-f]*[a-f])[0-9a-f]{8,}'
    r'|(?=[0-9a-f]*[0-9])(?=[0-9a-f]*[a-f])[0-9a-f]{12,}'
    r')(?![A-Za-z0-9])', re.IGNORECASE)
HOST_NAME = re.compile(r'(?<![\w.@-])(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+([A-Za-z]{2,24})(?![\w-])')
IPV4_ADDRESS = re.compile(r'(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w]|\.\d)')
IPV6_ADDRESS = re.compile(r'(?<![\w:])(?:[0-9a-f]{0,4}:){2,7}[0-9a-f]{0,4}(?![\w:])', re.IGNORECASE)
LOCAL_HOST = re.compile(r'(?<![\w.-])localhost(?![\w-])', re.IGNORECASE)
HOST_WITH_PORT = re.compile(r'(?<![\w.:/-])[A-Za-z][A-Za-z0-9-]*:\d{2,5}(?![\w:])')
SCHEME = re.compile(r'(?<![\w])(?:https?|ssh|ftp|git|file)://\S+')
NOT_HOST_NAMES = {
    'py', 'md', 'markdown', 'json', 'txt', 'text', 'csv', 'tsv', 'yaml', 'yml', 'toml', 'ini', 'cfg', 'sh', 'js', 'ts',
    'jsx', 'tsx', 'css', 'scss', 'html', 'htm', 'xml', 'sql', 'sqlite', 'db', 'log', 'lock', 'png', 'jpg', 'jpeg', 'gif',
    'svg', 'pdf', 'docx', 'pptx', 'xlsx', 'zip', 'tar', 'gz', 'env', 'rs', 'go', 'rb', 'java', 'kt', 'php', 'ipynb',
}
# These endings are file extensions and also country domains. A name with two or more dots before such an
# ending reads as a host name, such as portal.example.md, while a file name such as notes.md does not.
COUNTRY_DOMAINS_LIKE_EXTENSIONS = {'md', 'sh', 'rs', 'py'}
# Ordinary words of work in software, engagements and automations. A word of the project name that is in
# this list is not checked on its own, because a generic rule needs these words. The whole name is still
# checked, in any spelling.
COMMON_WORDS = {
    'project', 'projects', 'memory', 'client', 'clients', 'customer', 'customers', 'system', 'systems', 'service',
    'services', 'platform', 'module', 'modules', 'agent', 'agents', 'code', 'data', 'demo', 'main', 'test', 'tests',
    'tool', 'tools', 'work', 'core', 'app', 'apps', 'site', 'team', 'teams', 'pilot', 'phase', 'phases',
    'automation', 'automations', 'automated', 'workflow', 'workflows', 'invoice', 'invoices', 'intake', 'review',
    'reviews', 'model', 'models', 'operating', 'operations', 'operation', 'authority', 'port', 'ports', 'shipping',
    'report', 'reports', 'reporting', 'analysis', 'analytics', 'research', 'strategy', 'delivery', 'engagement',
    'engagements', 'product', 'products', 'management', 'program', 'programme', 'portfolio', 'design', 'build',
    'release', 'migration', 'integration', 'integrations', 'pipeline', 'pipelines', 'dashboard', 'website', 'mobile',
    'portal', 'backend', 'frontend', 'database', 'cloud', 'infrastructure', 'security', 'finance', 'financial',
    'sales', 'marketing', 'support', 'onboarding', 'assessment', 'audit', 'compliance', 'risk', 'governance',
    'transformation', 'digital', 'planning', 'plan', 'plans', 'roadmap', 'market', 'pricing', 'procurement',
    'supply', 'chain', 'supplier', 'suppliers', 'logistics', 'inventory', 'order', 'orders', 'payment', 'payments',
    'billing', 'accounting', 'payroll', 'hiring', 'recruitment', 'training', 'learning', 'knowledge', 'content',
    'document', 'documents', 'documentation', 'notes', 'board', 'office', 'group', 'company', 'business', 'global',
    'local', 'internal', 'external', 'final', 'draft', 'version', 'update', 'upgrade', 'rebuild', 'refresh',
    'redesign', 'prototype', 'proof', 'concept', 'study', 'survey', 'interview', 'interviews', 'stakeholder',
    'stakeholders', 'workstream', 'workstreams', 'deliverable', 'deliverables', 'handover', 'kickoff', 'scope',
    'discovery', 'alpha', 'beta', 'production', 'development', 'maintenance', 'legacy', 'monitoring', 'insight',
    'insights', 'metrics', 'email', 'emails', 'notification', 'notifications', 'scheduler', 'sync', 'import',
    'export', 'parser', 'engine', 'server', 'library', 'package', 'framework', 'interface', 'api',
    'process', 'processes', 'quality', 'performance', 'cost', 'costs', 'contract', 'contracts', 'policy',
    'policies', 'health', 'care', 'energy', 'public', 'private', 'sector', 'network', 'networks', 'account',
    'accounts', 'request', 'requests', 'ticket', 'tickets', 'task', 'tasks', 'agentic', 'assistant', 'assistants',
}
# Short words that ordinary text uses. A short word of the project name outside this list, such as an
# abbreviation, is checked as a whole word.
SHORT_COMMON_WORDS = {
    'a', 'an', 'as', 'at', 'be', 'by', 'do', 'go', 'if', 'in', 'is', 'it', 'me', 'my', 'no', 'of', 'on', 'or', 'so',
    'to', 'up', 'us', 'we', 'and', 'are', 'but', 'can', 'for', 'has', 'its', 'new', 'not', 'now', 'old', 'one', 'our',
    'out', 'the', 'two', 'use', 'was', 'way', 'web', 'who', 'you', 'app', 'api', 'dev', 'ops', 'run', 'set', 'get',
    'add', 'all', 'any', 'big', 'day', 'end', 'few', 'key', 'log', 'map', 'net', 'own', 'pay', 'per', 'see', 'top',
    'via', 'ai', 'ui', 'ux', 'id', 'ok', 'hr', 'pm', 'am', 'vs', 'etc', 'sql', 'ci', 'cd', 'qa',
}
CHECK_REASONS = {
    'electronic_mail': 'The text of the field {field} contains an electronic mail address.',
    'absolute_path': 'The text of the field {field} contains an absolute path.',
    'record_identifier': 'The text of the field {field} contains a record identifier.',
    'document_name': 'The text of the field {field} names a document captured in this project.',
    'host_name': 'The text of the field {field} contains a host name.',
    'project_name': 'The text of the field {field} names this project.',
}
REFUSED = ('This promotion carries text that belongs to this project alone. A promoted rule holds for every project on this '
           'machine, so rewrite the text without what the checks matched and propose it again.')


def _words(text):
    """The words of a text in lower case, split at every separator and where a small letter meets a capital."""
    spaced = []
    previous = ''
    for character in str(text):
        if previous and previous.islower() and character.isupper():
            spaced.append(' ')
        spaced.append(character)
        previous = character
    return [word.casefold() for word in re.findall(r'[^\W_]+', ''.join(spaced))]


def _project_names(memory):
    from .guards import project_root
    names = [memory.project]
    try:
        names.append(Path(project_root(memory)).name)
    except (OSError, ValueError):
        pass
    return [' '.join(str(name).split()) for name in names if str(name).strip()]


def name_checks(memory):
    """What names this project: the whole names, their distinctive words and their short abbreviations.

    A whole name is found in any spelling of its words, such as AcmeShipping,
    acme_shipping or acme-shipping. A distinctive word is found alone and with
    a plural or possessive ending. Ordinary words of work are not checked alone,
    so a generic rule may still speak of an automation or a model review.
    """
    phrases = set()
    words = set()
    short = set()
    for name in _project_names(memory):
        parts = _words(name)
        if len(parts) > 1 and len(''.join(parts)) >= 4:
            phrases.add(tuple(parts))
        for part in parts:
            if len(part) >= 4 and part not in COMMON_WORDS:
                words.add(part)
            elif 2 <= len(part) <= 3 and part not in SHORT_COMMON_WORDS:
                short.add(part)
    return {'phrases': sorted(phrases), 'words': sorted(words), 'short': sorted(short)}


def project_terms(memory):
    """The names and words of this project, which a promoted rule may not carry."""
    terms = {name.lower() for name in _project_names(memory) if len(name) >= 2}
    checks = name_checks(memory)
    terms.update(checks['words'])
    terms.update(checks['short'])
    return sorted(terms)


def _word_forms(word):
    return {word, word + 's', word + 'es'}


def _names_project(text, checks):
    """True when the text names the project in one of the forms of `name_checks`."""
    tokens = _words(text)
    if not tokens:
        return False
    forms = set()
    for word in checks['words']:
        forms.update(_word_forms(word))
    if any(token in forms or token in checks['short'] for token in tokens):
        return True
    for phrase in checks['phrases']:
        target = ''.join(phrase)
        for start in range(len(tokens)):
            joined = ''
            for token in tokens[start:]:
                joined += token
                if joined in _word_forms(target):
                    return True
                if len(joined) >= len(target):
                    break
    return False


def document_names(memory):
    """The file names of the documents captured in this project."""
    from .documents import PREFIX, FILE_PREFIX, document_path
    rows = memory.db.execute('SELECT DISTINCT source_key FROM sources WHERE source_key LIKE ? OR source_key LIKE ?',
                             (PREFIX + '%', FILE_PREFIX + '%')).fetchall()
    names = set()
    for row in rows:
        path = document_path(row[0])
        if path and len(path.name) >= 4:
            names.add(path.name.lower())
    return sorted(names)


def host_terms():
    """The name of this machine, which a promoted rule may not carry."""
    name = machine_name().lower()
    terms = {name} if len(name) >= 3 else set()
    label = name.split('.')[0]
    if len(label) >= 3:
        terms.add(label)
    return sorted(terms)


def _contains_term(text, term):
    return re.search(r'(?<![\w-])' + re.escape(term) + r'(?![\w-])', text, flags=re.IGNORECASE) is not None


def _host_name(match):
    ending = match.group(1).lower()
    if ending not in NOT_HOST_NAMES:
        return True
    return ending in COUNTRY_DOMAINS_LIKE_EXTENSIONS and match.group(0).count('.') >= 2


def _network_address(text):
    if IPV4_ADDRESS.search(text) or LOCAL_HOST.search(text) or HOST_WITH_PORT.search(text):
        return True
    return any('::' in match.group(0) or match.group(0).count(':') >= 5 for match in IPV6_ADDRESS.finditer(text))


def _absolute_path(text):
    return any(pattern.search(text) for pattern in (SCHEME, WINDOWS_PATH, ABSOLUTE_PATH, HOME_PATH, NETWORK_PATH,
                                                     VARIABLE_PATH, USER_FOLDER))


def _field_matches(text, field, terms, documents, hosts):
    """The checks that matched in one field. The value itself is never part of the result."""
    found = []
    working = text
    if ELECTRONIC_MAIL.search(working):
        found.append('electronic_mail')
        # The address also reads as a host name, so it is reported once, as an address.
        working = ELECTRONIC_MAIL.sub(' ', working)
    if _absolute_path(working):
        found.append('absolute_path')
    if RECORD_IDENTIFIER.search(working):
        found.append('record_identifier')
    if any(_contains_term(working, name) for name in documents):
        found.append('document_name')
    if any(_host_name(match) for match in HOST_NAME.finditer(working)) or _network_address(working) \
            or any(_contains_term(working, name) for name in hosts):
        found.append('host_name')
    if _names_project(working, terms):
        found.append('project_name')
    return [{'check': name, 'field': field, 'reason': CHECK_REASONS[name].format(field=field)} for name in found]


def _texts(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [item for item in value if isinstance(item, str)]
    return []


def inspect_text(memory, fields):
    """Check the text of a promotion against this project and report what matched.

    `fields` maps a field name to its text or to a list of text. The result names
    the check and the field, never the value, so a refusal can be shown and
    recorded without repeating what it found.
    """
    terms = name_checks(memory)
    documents = document_names(memory)
    hosts = host_terms()
    found = []
    seen = set()
    for field, value in fields.items():
        for text in _texts(value):
            for match in _field_matches(text, field, terms, documents, hosts):
                key = (match['check'], match['field'])
                if key not in seen:
                    seen.add(key)
                    found.append(match)
    return found


def refuse_matches(matches):
    """Refuse a promotion whose text matched a check. The message never repeats the value."""
    if not matches:
        return
    checks = sorted({match['check'] for match in matches})
    named = ', '.join(check.replace('_', ' ') for check in checks)
    raise InvalidRecord(REFUSED + ' The checks that matched are: ' + named + '.',
                        matches=matches, checks=checks,
                        next_step={'action': 'rewrite_promotion',
                                   'reason': 'Write the rule so that it names no project, path, identifier, document, '
                                             'address or host, and propose it again.'})


# Proposing a promotion inside a project.

def _rule_fields(values):
    """The checked text of a rule, in the shape of a lesson."""
    rule = {}
    for key in RULE_FIELDS:
        _text(values.get(key), key, 2000)
        rule[key] = ' '.join(values[key].split())
    return rule


def _validate_rule(values, *, roles, keywords, failure_type, pattern_type):
    from . import guards
    rule = _rule_fields(values)
    rule['roles'] = guards.validate_roles(list(roles) if isinstance(roles, (list, tuple)) else roles)
    if keywords:
        rule['keywords'] = guards.validate_keywords(list(keywords))
    if failure_type is not None:
        _text(failure_type, 'failure_type', 200)
        rule['failure_type'] = ' '.join(failure_type.split())
    if pattern_type is not None:
        if pattern_type not in {'practice', 'anti_pattern', 'recovery'}:
            raise InvalidRecord('pattern_type must be practice, anti_pattern or recovery.')
        rule['pattern_type'] = pattern_type
    return rule


def _checked_fields(rule, basis):
    """Every text of a promotion that reaches the machine memory, by field."""
    return {**{key: rule[key] for key in RULE_FIELDS}, 'basis': basis, 'keywords': rule.get('keywords', []),
            'failure_type': rule.get('failure_type')}


def propose(memory, *, when, do, because, exceptions, basis, roles, actor, request_key,
            lesson_id=None, keywords=None, failure_type=None, pattern_type=None, paths=None):
    """Record a proposal to promote a rule to the machine memory. Nothing is written to that memory.

    The mechanical checks run here, so the proposer learns at once that the text
    carries something that belongs to this project alone.
    """
    _text(request_key, 'request_key', 180)
    _text(actor, 'actor', 200)
    _text(basis, 'basis', 2000)
    if paths:
        raise InvalidRecord('A promoted rule carries no path pattern, because a path belongs to one project. Use keywords, '
                            'a failure type or the roles the rule applies to.')
    rule = _validate_rule({'when': when, 'do': do, 'because': because, 'exceptions': exceptions},
                          roles=roles, keywords=keywords, failure_type=failure_type, pattern_type=pattern_type)
    basis = ' '.join(basis.split())
    if lesson_id is not None:
        _text(lesson_id, 'lesson_id', 200)
        lesson = memory._event(lesson_id)
        if lesson['kind'] != 'lesson':
            raise InvalidRecord('Name the accepted lesson this rule generalises, or omit lesson_id.')
    refuse_matches(inspect_text(memory, _checked_fields(rule, basis)))
    _ensure(memory, PROMOTION_SCHEMA, PROMOTION_MARKER)
    signature = _digest(dumps([rule, basis, lesson_id, actor]))
    promotion_id = 'promotion_' + uuid.uuid4().hex
    with memory._write():
        prior = memory.db.execute('SELECT id,signature FROM rule_promotions WHERE request_key=?', (request_key,)).fetchone()
        if prior:
            if prior['signature'] != signature:
                raise Conflict('This request key was already used for a different promotion.')
            return promotion(memory, prior['id'])
        memory.db.execute('INSERT INTO rule_promotions (id,lesson_id,state,actor,proposed_at,request_key,signature,rule,basis)'
                          ' VALUES (?,?,?,?,?,?,?,?,?)',
                          (promotion_id, lesson_id, 'proposed', actor, memory.now(), request_key, signature,
                           dumps(rule), basis))
    return promotion(memory, promotion_id)


def _promotion_row(row):
    value = {'id': row['id'], 'lesson_id': row['lesson_id'], 'state': row['state'], 'actor': row['actor'],
             'proposed_at': row['proposed_at'], 'rule': json.loads(row['rule']), 'basis': row['basis'],
             'decided_at': row['decided_at'], 'decided_by': row['decided_by'], 'reason': row['decision_reason'],
             'machine_rule_id': row['machine_rule_id']}
    value['note'] = PROMOTION_STATES_NOTE if value['state'] == 'proposed' else ISOLATION_NOTE
    return value


def promotion(memory, promotion_id):
    """One recorded promotion of this project."""
    if not _table_exists(memory, 'rule_promotions'):
        raise InvalidRecord('This project has no proposed promotion.')
    row = memory.db.execute('SELECT * FROM rule_promotions WHERE id=?', (promotion_id,)).fetchone()
    if row is None:
        raise InvalidRecord('This promotion was not found in this project.')
    return _promotion_row(row)


def promotions(memory, *, state=None, limit=50):
    """The promotions recorded in this project, newest first. Safe on a read only connection."""
    if type(limit) is not int or not 1 <= limit <= 200:
        raise InvalidRecord('Use limit 1 to 200 for the recorded promotions.')
    if state is not None and state not in STATES:
        raise InvalidRecord('Select a promotion state. Use one of: ' + ', '.join(STATES) + '.', states=list(STATES))
    if not _table_exists(memory, 'rule_promotions'):
        return []
    sql = 'SELECT * FROM rule_promotions'
    parameters = []
    if state is not None:
        sql += ' WHERE state=?'
        parameters.append(state)
    sql += ' ORDER BY proposed_at DESC, rowid DESC LIMIT ?'
    parameters.append(limit)
    return [_promotion_row(row) for row in memory.db.execute(sql, parameters).fetchall()]


# The user accepts or declines a proposal.

def _require_user(actor, action):
    if actor != USER_ACTOR:
        raise InvalidRecord(action + ' is a user action in the control panel. The recorded actor is ' + USER_ACTOR +
                            ', so another actor name is not accepted.', actor=USER_ACTOR)


def _decided(memory, promotion_id, *, state, actor, reason, machine_rule_id=None):
    with memory._write():
        memory.db.execute('UPDATE rule_promotions SET state=?,decided_at=?,decided_by=?,decision_reason=?,machine_rule_id=?'
                          ' WHERE id=?', (state, memory.now(), actor, reason, machine_rule_id, promotion_id))
    return promotion(memory, promotion_id)


def accept(memory, promotion_id, *, actor, reason, changes=None, basis=None, path=None):
    """Write a proposed rule into the machine memory and record the acceptance in this project.

    The machine memory is created when it does not exist yet. The project keeps
    the identifier of the machine rule and nothing of the machine memory itself,
    and the machine memory keeps no identifier, name or path of this project
    outside its registry.
    """
    _require_user(actor, 'Promoting a rule to the machine memory')
    _text(reason, 'reason', 2000)
    current = promotion(memory, promotion_id)
    if current['state'] == 'accepted':
        return current
    if current['state'] != 'proposed':
        raise InvalidRecord('This promotion was already declined. Propose the rule again to promote it.',
                            state=current['state'])
    rule = dict(current['rule'])
    if changes:
        if not isinstance(changes, dict) or set(changes) - set(RULE_FIELDS) - {'roles', 'keywords', 'failure_type', 'pattern_type'}:
            raise InvalidRecord('Change only the text, roles, keywords, failure type or pattern type of the proposed rule.')
        merged = {**rule, **changes}
        rule = _validate_rule(merged, roles=merged['roles'], keywords=merged.get('keywords'),
                              failure_type=merged.get('failure_type'), pattern_type=merged.get('pattern_type'))
    basis = ' '.join((basis or current['basis']).split())
    _text(basis, 'basis', 2000)
    refuse_matches(inspect_text(memory, _checked_fields(rule, basis)))
    target = database_path(path)
    if not target.exists():
        create(target).close()
    # The machine key is a digest of the promotion identifier, so the write is repeatable
    # without the machine memory holding a record identifier of this project.
    key = _digest(promotion_id)[:16]
    with writer(target) as store:
        rule_id, adopted = _store_rule(store, rule, basis, key)
        project_id = register(store, path=str(_project_path(memory)), template=_project_template(memory),
                              phase=_project_phase(memory))
        adoptions = _record_adoption(store, rule_id, project_id, basis)
        name = store.project
    result = _decided(memory, promotion_id, state='accepted', actor=USER_ACTOR, reason=reason, machine_rule_id=rule_id)
    return {**result, 'machine': name, 'machine_rule_id': rule_id, 'rule_is_new': adopted, 'adopted_by': adoptions}


def decline(memory, promotion_id, *, actor, reason):
    """Record that the user declined a proposed promotion. Nothing is written to the machine memory."""
    _require_user(actor, 'Deciding a proposed promotion')
    _text(reason, 'reason', 2000)
    current = promotion(memory, promotion_id)
    if current['state'] != 'proposed':
        raise InvalidRecord('This promotion was already decided.', state=current['state'])
    return _decided(memory, promotion_id, state='declined', actor=USER_ACTOR, reason=reason)


def _project_path(memory):
    from .guards import project_root
    try:
        return Path(project_root(memory)).resolve()
    except (OSError, ValueError):
        return memory.path.parent


def _project_template(memory):
    from .templates import setting
    value = setting(memory)
    return value['template'] if value else None


def _project_phase(memory):
    from .planning import phase
    return phase(memory)


# Writing and reading the rules of the machine memory.

def rule_digest(rule):
    """The identity of a rule text, so the same rule promoted from two projects is one machine rule."""
    return _digest(dumps({key: rule[key].strip().lower() for key in RULE_FIELDS}))


def _rules_episode(machine):
    row = machine.db.execute('SELECT id FROM episodes WHERE title=? ORDER BY rowid LIMIT 1', (RULES_EPISODE,)).fetchone()
    if row:
        return machine.episode(row[0])
    return machine.start(RULES_EPISODE, RULES_OBJECTIVE, 'learning', RULES_CRITERION)


def _existing_rule(machine, rule):
    """The identifier of an accepted machine rule with this text, or None."""
    digest = rule_digest(rule)
    for row in machine.db.execute("SELECT id,payload FROM events WHERE kind='lesson'"
                                  ' AND NOT EXISTS (SELECT 1 FROM events n WHERE n.supersedes=events.id)').fetchall():
        payload = json.loads(row['payload'])
        if all(key in payload for key in RULE_FIELDS) and rule_digest(payload) == digest:
            review = machine._lesson_review(row['id'])
            if review and review['status'] == 'accepted':
                return row['id']
    return None


def _triggers(rule):
    return {'roles': set(rule.get('roles') or []), 'keywords': {str(item).lower() for item in rule.get('keywords') or []},
            'failure_type': rule.get('failure_type') or None, 'pattern_type': rule.get('pattern_type') or None}


def _same_reach(existing, rule):
    """True when the machine rule already reaches every run that the promoted rule would reach."""
    held = _triggers(existing)
    wanted = _triggers(rule)
    return (wanted['roles'] <= held['roles'] and wanted['keywords'] == held['keywords']
            and wanted['failure_type'] == held['failure_type'] and wanted['pattern_type'] == held['pattern_type'])


def _store_rule(machine, rule, basis, key):
    """Record the rule in the machine memory as an accepted lesson, or reuse the rule that already holds this text.

    A rule with the same text but other roles or triggers is refused instead of
    being merged, so no role, keyword or failure type is dropped without notice.
    """
    found = _existing_rule(machine, rule)
    if found:
        existing = machine._event(found)['payload']
        if not _same_reach(existing, rule):
            raise InvalidRecord('The machine memory already holds this rule text with other roles or triggers, so accepting it '
                           'here would drop the roles or triggers of this proposal. Accept it with the roles and triggers '
                           'of the machine rule, or retire that rule and accept this proposal with the combined roles and '
                           'triggers.', machine_rule_id=found,
                           machine_rule={'roles': list(existing.get('roles') or []),
                                         'keywords': list(existing.get('keywords') or []),
                                         'failure_type': existing.get('failure_type'),
                                         'pattern_type': existing.get('pattern_type')})
        return found, False
    episode = _rules_episode(machine)
    source = machine.source(BASIS_PREFIX + key, 'Basis of a promoted rule', 'The user promoted this rule to the machine memory.',
                            basis, 'user')
    evidence = [{'source_id': source['id'], 'reason': 'The user wrote this basis when promoting the rule.'}]
    payload = {key_name: rule[key_name] for key_name in RULE_FIELDS}
    for optional in ('roles', 'keywords', 'failure_type', 'pattern_type'):
        if rule.get(optional):
            payload[optional] = rule[optional]
    lesson = machine.record(episode['id'], 'lesson', payload, expected_version=episode['version'],
                            request_key='promotion:' + key + ':rule', actor=USER_ACTOR, evidence=evidence)
    machine.record(episode['id'], 'lesson_review',
                   {'lesson_id': lesson['id'], 'status': 'accepted',
                    'reason': 'The user promoted this rule to the machine memory.'},
                   expected_version=lesson['version'], request_key='promotion:' + key + ':acceptance', actor=USER_ACTOR,
                   evidence=evidence, links=[{'event_id': lesson['id'], 'reason': 'This acceptance promotes the rule.'}])
    return lesson['id'], True


def retire(memory_or_machine, rule_id, *, actor, reason, path=None):
    """Retire a machine rule. The rule and its history stay readable, and no run composes it again."""
    _require_user(actor, 'Retiring a machine rule')
    _text(reason, 'reason', 2000)
    store = memory_or_machine if _is_machine(memory_or_machine, path) else None
    close = False
    if store is None:
        store = open_machine(path)
        close = True
        if store is None:
            raise InvalidRecord(NOT_CREATED_NOTE)
    try:
        lesson = store._event(rule_id)
        if lesson['kind'] != 'lesson':
            raise InvalidRecord('Select a machine rule to retire.')
        review = store._lesson_review(rule_id)
        if review and review['status'] == 'retired':
            # Retiring a rule twice reports the recorded retirement instead of writing a second one.
            return {'rule_id': rule_id, 'status': 'retired', 'actor': USER_ACTOR, 'reason': review.get('reason'),
                    'duplicate': True, 'note': 'This rule was already retired. Its history stays readable.'}
        episode = store.episode(lesson['episode_id'])
        key = _digest(rule_id + reason)[:16]
        source = store.source(BASIS_PREFIX + 'retired:' + key, 'Retirement of a promoted rule',
                              'The user retired this rule.', reason, 'user')
        store.record(episode['id'], 'lesson_review',
                     {'lesson_id': rule_id, 'status': 'retired', 'reason': reason},
                     expected_version=episode['version'], request_key='retire:' + key, actor=USER_ACTOR,
                     evidence=[{'source_id': source['id'], 'reason': 'The user recorded this reason.'}],
                     links=[{'event_id': rule_id, 'reason': 'This review retires the rule.'}])
        return {'rule_id': rule_id, 'status': 'retired', 'actor': USER_ACTOR, 'reason': reason,
                'note': 'The rule and its history stay readable, and no run composes it again.'}
    finally:
        if close:
            store.close()


def _is_machine(value, path):
    return isinstance(value, Memory) and value.path == database_path(path).resolve()


# The registry of the projects on this machine.

def register(machine, *, path, template, phase):
    """Record or refresh one project in the registry and return its registry identifier."""
    _ensure(machine, REGISTRY_SCHEMA, REGISTRY_MARKER)
    name = str(path)
    now = machine.now()
    with machine._write():
        row = machine.db.execute('SELECT id FROM machine_projects WHERE path=?', (name,)).fetchone()
        if row:
            project_id = row['id']
            machine.db.execute('UPDATE machine_projects SET template=?,phase=?,phase_at=?,updated_at=? WHERE id=?',
                               (template, phase['phase'], phase['at'], now, project_id))
        else:
            project_id = 'registered_' + uuid.uuid4().hex
            machine.db.execute('INSERT INTO machine_projects (id,path,template,phase,phase_at,first_seen,updated_at)'
                               ' VALUES (?,?,?,?,?,?,?)',
                               (project_id, name, template, phase['phase'], phase['at'], now, now))
    return project_id


def register_project(memory, *, path=None):
    """Record this project in the registry of the machine memory. Only a local caller does this."""
    store = open_machine(path)
    if store is None:
        raise InvalidRecord(NOT_CREATED_NOTE)
    with store:
        return register(store, path=str(_project_path(memory)), template=_project_template(memory),
                        phase=_project_phase(memory))


def refresh_phase(memory, *, path=None):
    """Bring the phase of this project up to date in the registry, when the project is registered.

    The panel calls this after the user records a phase. A machine memory that
    does not exist, or that does not register this project, is left unchanged,
    and a machine memory that cannot be read does not stop the phase change.
    """
    target = database_path(path)
    if not target.exists():
        return 'no_machine_memory'
    try:
        with writer(target) as store:
            if not _table_exists(store, 'machine_projects'):
                return 'not_registered'
            row = store.db.execute('SELECT id FROM machine_projects WHERE path=?', (str(_project_path(memory)),)).fetchone()
            if row is None:
                return 'not_registered'
            register(store, path=str(_project_path(memory)), template=_project_template(memory),
                     phase=_project_phase(memory))
            return 'updated'
    except (MemoryError, OSError, ValueError, sqlite3.Error):
        return 'unavailable'


def _record_adoption(machine, rule_id, project_id, basis):
    """Record that one project promoted this rule, and return how many projects have."""
    _ensure(machine, REGISTRY_SCHEMA, REGISTRY_MARKER)
    with machine._write():
        machine.db.execute('INSERT OR IGNORE INTO machine_adoptions (rule_id,project_id,adopted_at,basis) VALUES (?,?,?,?)',
                           (rule_id, project_id, machine.now(), basis))
    return machine.db.execute('SELECT count(*) FROM machine_adoptions WHERE rule_id=?', (rule_id,)).fetchone()[0]


def registry(machine, *, limit=200):
    """The projects recorded on this machine, newest first. Safe on a read only connection."""
    if not _table_exists(machine, 'machine_projects'):
        return []
    rows = machine.db.execute('SELECT * FROM machine_projects ORDER BY updated_at DESC, rowid DESC LIMIT ?',
                              (limit,)).fetchall()
    return [{'id': row['id'], 'path': row['path'], 'name': Path(row['path']).name, 'template': row['template'],
             'phase': row['phase'], 'phase_at': row['phase_at'], 'first_seen': row['first_seen'],
             'updated_at': row['updated_at']} for row in rows]


def _adoption_counts(machine):
    if not _table_exists(machine, 'machine_adoptions'):
        return {}
    return {row[0]: row[1] for row in machine.db.execute('SELECT rule_id,count(*) FROM machine_adoptions GROUP BY rule_id')}


def _basis(machine, rule_id):
    row = machine.db.execute('SELECT s.body FROM dependencies d JOIN sources s ON s.id=d.source_id'
                             ' WHERE d.event_id=? ORDER BY s.rowid LIMIT 1', (rule_id,)).fetchone()
    return row[0] if row else ''


def rules(machine, *, role=None, limit=50, include_retired=False):
    """The rules of the machine memory, with their basis and adoption count. Safe on a read only connection."""
    from . import guards
    if role is not None and role not in guards.RULE_ROLES:
        raise InvalidRecord('role must be assistant, worker or reviewer.')
    if type(limit) is not int or not 1 <= limit <= 200:
        raise InvalidRecord('Use limit 1 to 200 for the machine rules.')
    counts = _adoption_counts(machine)
    found = []
    rows = machine.db.execute("SELECT id,payload,created_at FROM events WHERE kind='lesson'"
                              ' AND NOT EXISTS (SELECT 1 FROM events n WHERE n.supersedes=events.id)'
                              ' ORDER BY rowid DESC').fetchall()
    for row in rows:
        payload = json.loads(row['payload'])
        if not all(key in payload for key in RULE_FIELDS):
            continue
        review = machine._lesson_review(row['id'])
        status = review['status'] if review else 'proposed'
        # Only a rule the user accepted or later retired is a machine rule. A lesson written into this
        # database in any other way is not one, so it is neither composed nor listed as retired.
        if status not in ('accepted', 'retired'):
            continue
        if status == 'retired' and not include_retired:
            continue
        if role is not None and role not in payload.get('roles', []):
            continue
        found.append({'rule_id': row['id'], 'status': status, 'recorded_at': row['created_at'],
                      **{key: payload[key] for key in RULE_FIELDS},
                      'roles': list(payload.get('roles', [])), 'keywords': list(payload.get('keywords', [])),
                      'failure_type': payload.get('failure_type'), 'basis': _basis(machine, row['id']),
                      'adopted_by': counts.get(row['id'], 0)})
        if len(found) >= limit:
            break
    return found


def overview(path=None, *, limit=50, include_registry=True):
    """The read only view of the machine memory, which also answers before that memory exists."""
    target = database_path(path)
    if not target.exists():
        result = {'machine': machine_name(), 'database': str(target), 'exists': False, 'rules': [], 'retired': [],
                  'rules_total': 0, 'projects_total': 0, 'note': NOT_CREATED_NOTE + ' ' + ISOLATION_NOTE}
        if include_registry:
            result['projects'] = []
        return result
    with Memory(target, read_only=True) as store:
        found = rules(store, limit=limit, include_retired=True)
        projects = registry(store, limit=limit)
        result = {'machine': store.project, 'database': str(target), 'exists': True,
                  'rules': [rule for rule in found if rule['status'] == 'accepted'],
                  'retired': [rule for rule in found if rule['status'] == 'retired'],
                  'rules_total': len([rule for rule in found if rule['status'] == 'accepted']),
                  'projects_total': len(projects), 'note': ISOLATION_NOTE}
        if include_registry:
            result['projects'] = projects
        else:
            result['registry_note'] = ('The registry of the projects on this machine stays local, so it is not part of this '
                                       'view. Only the count is reported.')
        return result
