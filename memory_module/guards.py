"""Lesson triggers, accepted guards, decision acknowledgement, file scope enforcement and the rules composed into agent prompts.

An accepted lesson with at least one trigger (paths, keywords or a failure type)
becomes a guard. Decisions whose work matches a guard must acknowledge it, and
edit tools are checked against the path patterns of the session's current work.

An accepted lesson that names roles (assistant, worker or reviewer) is also a
rule. `compose` selects the rules a run triggers, orders them so that the same
run always produces the same text, and fills a character budget per role.
`instructions` puts the base text of the role before those rules, and
`effectiveness` reports counts per rule so that the user can retire a rule that
does not help. Nothing here writes a rule: an agent proposes a lesson and only
the user accepts it.

Boundary: edit targets come from the structured fields of edit tools (including
file writing tools of MCP servers, whose names carry an `mcp__<server>__`
prefix) and from patch markers found in any string argument. In arguments of
tools that are not edit tools, markers are also found after escaped line breaks
(`\\n`) and after an opening quote, so a patch piped into `apply_patch` from
`printf` or `echo` is checked. Shell redirection, `sed -i`, `mv`, scripts and
other indirect writes are not parsed, so a shell command can still change files
outside the recorded scope without being blocked.

Write tools of MCP servers that change something outside the project files,
such as a workflow in an n8n instance or a file in a document service, have no
file path. Such a tool counts as a write when a word of its name is a write verb
(create, update, delete, publish, execute and similar) and its first word is not
a read verb. Its target is `mcp:<server>/<tool>`, so the plan pattern
`mcp:<server>` allows every write tool of that server. The tools of Project
Memory itself are never targets. A write tool whose name uses no listed verb is
not detected.

Relative patterns never match paths outside the project root, and absolute
patterns inside the root are compared relative to it.
"""
import json
import os
from pathlib import Path
import posixpath
import re
import sqlite3
from functools import lru_cache

from .core import USER_ACTOR, InvalidRecord, MemoryError, _digest, _text

# A failure stays counted until the user reassesses it. Superseding alone is not enough, because the
# agent whose work failed may record the next outcome, and a count it can clear would reward hiding failures.
# The reassess action of the control panel records an outcome by the user that links to the one outcome it
# reassesses. That reassessment decides for the linked outcome only: good or unknown removes it from the count and
# bad keeps it counted. A reassessment is never counted itself, so a confirmed failure is counted once and keeps
# its place before or after the acceptance of a guard. An outcome by the user without such a link reassesses
# every earlier outcome of its decision.
REASSESSMENT_LINK = """SELECT 1 FROM event_links k JOIN events t ON t.id=k.prior_event_id
                       WHERE k.event_id={outcome}.id AND t.kind='outcome'"""
COUNTED_FAILURE = f"""o.kind='outcome' AND json_extract(o.payload,'$.assessment')='bad'
    AND NOT (o.actor='{USER_ACTOR}' AND EXISTS ({REASSESSMENT_LINK.format(outcome='o')}))
    AND COALESCE((SELECT json_extract(r.payload,'$.assessment') FROM event_links k JOIN events r ON r.id=k.event_id
                  WHERE k.prior_event_id=o.id AND r.kind='outcome' AND r.actor='{USER_ACTOR}'
                  ORDER BY r.rowid DESC LIMIT 1), 'bad')='bad'
    AND NOT EXISTS (SELECT 1 FROM events n WHERE n.kind='outcome' AND n.decision_id=o.decision_id
                    AND n.rowid > o.rowid AND n.actor='{USER_ACTOR}'
                    AND NOT EXISTS ({REASSESSMENT_LINK.format(outcome='n')}))"""

TRIGGERS = ('paths', 'keywords', 'failure_type')
# A lesson review may replace the triggers and the roles of the lesson it accepts.
REVIEW_FIELDS = TRIGGERS + ('roles',)
RULE_ROLES = ('assistant', 'worker', 'reviewer')
ROLE_BUDGETS = {'assistant': 600, 'worker': 1200, 'reviewer': 900}
# Rules promoted to the machine share the budget of the role and keep to a small part of it,
# so the rules of this project always take precedence.
MACHINE_BUDGETS = {'assistant': 200, 'worker': 400, 'reviewer': 300}
MAX_ACTIVE_RULES = 8
RULES_HEADING = 'Rules accepted in this project for this role:'
MACHINE_HEADING = 'Machine rules:'
# The heading of the machine rules and the blank line before it are part of the prompt, so the first
# composed machine rule pays for them within the machine budget.
MACHINE_OVERHEAD = len(MACHINE_HEADING) + 3
MACHINE_LABEL = 'Machine level. '
# A machine memory that is damaged, locked or not a database is reported and never stops a project run.
MACHINE_READ_ERRORS = (MemoryError, OSError, ValueError, sqlite3.Error)
BASE_SOURCE_PREFIX = 'instructions-base:'
RUN_SOURCE_PREFIX = 'instructions:'
SHIPPED_BASE_SOURCE = 'agents/{role}.md'
VERDICTS = ('pass', 'changes_required', 'uncertain')
EFFECTIVENESS_MINIMUM = 3
# One recurrence is not a trend, so the comparison before and after acceptance needs at least this many.
RECURRENCE_MINIMUM = 2
EDIT_TOOLS = frozenset({'Edit', 'Write', 'MultiEdit', 'NotebookEdit', 'apply_patch', 'ApplyPatch',
                        'edit_file', 'write_file', 'create_file'})
MCP_EDIT_TOOLS = EDIT_TOOLS | {'move_file'}
PATH_KEYS = ('file_path', 'notebook_path', 'path')
MOVE_KEYS = ('source', 'destination')
PATCH_MARKER = re.compile(r'^[ \t]*\*\*\* (?:Add File|Update File|Delete File|Move to): (.+?)[ \t]*$', re.MULTILINE)
# Used only for arguments of tools that are not edit tools: a marker may follow a quote on the same line.
QUOTED_PATCH_MARKER = re.compile(
    r'''(?:^[ \t]*|(?<=['"]))\*\*\* (?:Add File|Update File|Delete File|Move to): ([^\n'"]*[^\s'"])''', re.MULTILINE)
PATCH_BODY_PREFIXES = ('+', '-', ' ', '\t', '@')
MCP_TARGET_PREFIX = 'mcp:'
WRITE_VERBS = frozenset({
    'activate', 'add', 'append', 'apply', 'approve', 'archive', 'cancel', 'complete', 'copy', 'create', 'deactivate',
    'delete', 'deploy', 'disable', 'edit', 'enable', 'execute', 'grant', 'import', 'insert', 'invite', 'merge', 'move',
    'patch', 'pause', 'post', 'publish', 'put', 'rebase', 'remove', 'rename', 'replace', 'reset', 'restore', 'revoke',
    'run', 'schedule', 'send', 'set', 'share', 'submit', 'test', 'transfer', 'trash', 'unarchive', 'unpublish',
    'update', 'upload', 'upsert', 'write'})
READ_VERBS = frozenset({
    'analyze', 'check', 'count', 'describe', 'download', 'explore', 'fetch', 'find', 'get', 'inspect', 'list', 'lookup',
    'prepare', 'preview', 'query', 'read', 'resolve', 'search', 'show', 'status', 'validate', 'view'})
OWN_SERVER_MARKERS = ('project_memory', 'project-memory')
GLOB_CHARACTERS = ('*', '?')


class ScopeBlocked(MemoryError):
    """An edit tool call targets paths outside the recorded scope of work."""


# Validation used by core.py and planning.py.

def validate_patterns(value, name='paths', minimum=1, maximum=100):
    """Check a list of path patterns and return it unchanged."""
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise InvalidRecord(f'{name} must be a list of {minimum} to {maximum} path patterns.')
    for pattern in value:
        _text(pattern, name + ' pattern', 500)
        if '\x00' in pattern or '\\' in pattern:
            raise InvalidRecord(f'{name} patterns must be POSIX paths without backslashes.')
        if '..' in pattern.split('/'):
            raise InvalidRecord(f'{name} patterns cannot contain a parent directory segment.')
    if len(set(value)) != len(value):
        raise InvalidRecord(f'{name} patterns must be unique.')
    return value


def validate_keywords(value):
    if not isinstance(value, list) or len(value) > 30:
        raise InvalidRecord('keywords must be a list of at most 30 items.')
    for keyword in value:
        _text(keyword, 'keyword', 100)
    return value


def validate_triggers(payload):
    """Validate optional lesson trigger fields present in a payload."""
    if 'paths' in payload:
        validate_patterns(payload['paths'], minimum=0)
    if 'keywords' in payload:
        validate_keywords(payload['keywords'])
    if 'failure_type' in payload:
        _text(payload['failure_type'], 'failure_type', 200)
    if 'roles' in payload:
        validate_roles(payload['roles'])


def validate_considered(value):
    if not isinstance(value, list) or len(value) > 50:
        raise InvalidRecord('lessons_considered must be a list of at most 50 entries.')
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != {'lesson_id', 'applies', 'reason'}:
            raise InvalidRecord('Each lessons_considered entry needs lesson_id, applies and reason.')
        _text(entry['lesson_id'], 'lesson_id', 200)
        _text(entry['reason'], 'lessons_considered reason', 2000)
        if entry['applies'] not in ('yes', 'no'):
            raise InvalidRecord('lessons_considered applies must be yes or no.')
    if len({entry['lesson_id'] for entry in value}) != len(value):
        raise InvalidRecord('Each lesson appears at most once in lessons_considered.')
    return value


# Pattern semantics.

def is_absolute(path):
    """True for a POSIX path, a Windows drive path such as C:\\work, and a UNC path.

    Scope patterns and hook targets are compared as text, so a Windows path has
    to be recognised here. Otherwise it counts as relative, is never made
    relative to the project, and no pattern ever matches it.
    """
    text = str(path).replace('\\', '/')
    if text.startswith('/'):
        return True
    return len(text) > 1 and text[1] == ':' and text[0].isalpha()


def normalize(path):
    """Return a POSIX path without empty or current directory segments.

    Windows separators become forward slashes and a drive letter is kept in
    upper case, so that the same file has one spelling wherever it came from.
    """
    text = str(path).replace('\\', '/')
    if not text:
        return '.'
    absolute = is_absolute(text)
    drive = ''
    if absolute and not text.startswith('/'):
        drive, text = text[0].upper() + ':', text[2:]
    text = posixpath.normpath(text)
    parts = [part for part in text.split('/') if part not in ('', '.')]
    joined = '/'.join(parts)
    if absolute:
        return drive + '/' + joined
    return joined or '.'


def _has_glob(pattern):
    return any(character in pattern for character in GLOB_CHARACTERS)


@lru_cache(maxsize=2048)
def _regex(pattern):
    output = []
    index = 0
    length = len(pattern)
    while index < length:
        if pattern.startswith('**', index):
            end = index + 2
            while end < length and pattern[end] == '*':
                end += 1
            at_boundary = index == 0 or pattern[index - 1] == '/'
            if at_boundary and end < length and pattern[end] == '/':
                output.append('(?:[^/]*/)*')
                end += 1
            else:
                output.append('.*')
            index = end
        elif pattern[index] == '*':
            output.append('[^/]*')
            index += 1
        elif pattern[index] == '?':
            output.append('[^/]')
            index += 1
        else:
            output.append(re.escape(pattern[index]))
            index += 1
    return re.compile(''.join(output), re.DOTALL)


def _is_path_prefix(prefix, path):
    """True when prefix names path or a directory above it."""
    if prefix in ('', '.'):
        return not is_absolute(path)
    if prefix == '/':
        return is_absolute(path)
    prefix = prefix.rstrip('/')
    return path == prefix or path.startswith(prefix + '/')


def _relative(path, roots):
    for root in roots:
        if not root or root == '/':
            continue
        if path == root:
            return '.'
        if path.startswith(root + '/'):
            return path[len(root) + 1:]
    return path


def _roots(root):
    if root is None:
        return ()
    base = normalize(root)
    real = normalize(os.path.realpath(base))
    return (base,) if real == base else (base, real)


def match_path(path, patterns, *, root=None):
    """True when path matches any pattern.

    `*` matches within one segment, `**` across segments and `?` one character.
    A pattern without glob characters matches that path and everything beneath
    it. With root, absolute paths and patterns inside root are compared relative
    to it. A relative pattern never matches an absolute path, and an absolute
    pattern never matches a relative path, so `**` cannot reach outside the root.
    """
    roots = _roots(root)
    target = _relative(normalize(path), roots)
    for pattern in patterns:
        candidate = _relative(normalize(pattern), roots)
        if is_absolute(candidate) != is_absolute(target):
            continue
        if _has_glob(candidate):
            if _regex(candidate).fullmatch(target):
                return True
        elif _is_path_prefix(candidate, target):
            return True
    return False


def _literal_prefix(pattern):
    positions = [pattern.find(character) for character in GLOB_CHARACTERS if character in pattern]
    return pattern[:min(positions)] if positions else pattern


def patterns_overlap(a, b, *, root=None):
    """True when two patterns can name the same file.

    Literal prefixes are compared as path prefixes, or one pattern matches the
    literal prefix of the other. The test is deliberately inclusive.
    """
    roots = _roots(root)
    first = _relative(normalize(a), roots)
    second = _relative(normalize(b), roots)
    first_prefix = _literal_prefix(first)
    second_prefix = _literal_prefix(second)
    if _is_path_prefix(first_prefix, second_prefix) or _is_path_prefix(second_prefix, first_prefix):
        return True
    return match_path(second_prefix, [first]) or match_path(first_prefix, [second])


def project_root(memory):
    """The project folder used to compare relative and absolute patterns."""
    from .codex_host import project_root as host_project_root
    return host_project_root(memory)


# Guards derived from lesson history.

def plan_paths(memory, episode_id):
    from .planning import latest
    plan = latest(memory, episode_id, 'work_plan')
    return list(plan.get('paths', [])) if plan else []


CANDIDATE_SQL = """SELECT l.id AS lesson_id, l.payload AS lesson, l.episode_id, l.subject,
        r.id AS review_id, r.payload AS review, r.created_at AS accepted_at
    FROM events l JOIN events r ON r.id = (SELECT x.id FROM events x WHERE x.kind='lesson_review'
        AND json_extract(x.payload,'$.lesson_id')=l.id ORDER BY x.rowid DESC LIMIT 1)
    WHERE l.kind='lesson' AND NOT EXISTS (SELECT 1 FROM events n WHERE n.supersedes=l.id)
      AND json_extract(r.payload,'$.status')='accepted'
    ORDER BY r.created_at, l.rowid"""


def _state_key(memory):
    """A cheap key that changes whenever a record or a source changes, so a cached read is never stale."""
    row = memory.db.execute('SELECT (SELECT coalesce(max(rowid),0) FROM events), '
                            '(SELECT coalesce(max(rowid),0) FROM sources)').fetchone()
    return (row[0], row[1])


def _cached(memory, name, key):
    """The cached value of this connection for the current state, or None."""
    store = getattr(memory, '_guard_cache', None)
    if store is None:
        return None
    entry = store.get(name)
    return entry[1] if entry and entry[0] == key else None


def _store(memory, name, key, value):
    """Keep a value for this connection until a record or a source changes."""
    store = getattr(memory, '_guard_cache', None)
    if store is None:
        store = {}
        try:
            memory._guard_cache = store
        except AttributeError:
            return value
    store[name] = (key, value)
    return value


def candidate_guards(memory):
    """Accepted lessons with at least one trigger or role, before the evidence freshness check.

    This is one query. `confirm_guards` then runs the freshness check of a
    review, which walks the evidence of a lesson, so a caller that first selects
    the few guards a run can use pays that walk only for those.
    """
    key = _state_key(memory)
    found = _cached(memory, 'candidates', key)
    if found is None:
        found = []
        for row in memory.db.execute(CANDIDATE_SQL).fetchall():
            lesson = json.loads(row['lesson'])
            review = json.loads(row['review'])
            source = review if any(name in review for name in TRIGGERS) else lesson
            guard = {
                'lesson_id': row['lesson_id'],
                'review_id': row['review_id'],
                'accepted_at': row['accepted_at'],
                'when': lesson['when'],
                'do': lesson['do'],
                'because': lesson['because'],
                'exceptions': lesson['exceptions'],
                'pattern_type': lesson.get('pattern_type', 'practice'),
                'paths': list(source.get('paths', [])),
                'keywords': list(source.get('keywords', [])),
                'failure_type': source.get('failure_type'),
                'roles': list(review['roles'] if 'roles' in review else lesson.get('roles', [])),
                'subject': row['subject'],
                'episode_id': row['episode_id'],
            }
            if guard['paths'] or guard['keywords'] or guard['failure_type'] or guard['roles']:
                found.append(guard)
        _store(memory, 'candidates', key, found)
    return [dict(guard) for guard in found]


def confirm_guards(memory, guards):
    """The guards whose acceptance still holds, which is the freshness check of their review."""
    key = _state_key(memory)
    statuses = _cached(memory, 'confirmed', key)
    if statuses is None:
        statuses = _store(memory, 'confirmed', key, {})
    result = []
    for guard in guards:
        held = statuses.get(guard['lesson_id'])
        if held is None:
            review = memory._lesson_review(guard['lesson_id'])
            held = bool(review and review['status'] == 'accepted')
            statuses[guard['lesson_id']] = held
        if held:
            result.append(guard)
    return result


def active_guards(memory):
    """Accepted lessons with at least one trigger, oldest acceptance first."""
    return confirm_guards(memory, candidate_guards(memory))


def _keyword_found(keyword, text):
    expression = r'(?<!\w)' + re.escape(keyword.strip()) + r'(?!\w)'
    return re.search(expression, text, flags=re.IGNORECASE) is not None


def matching_guards(memory, *, paths=(), text='', failure_type=None, guards=None, root=None):
    """Guards triggered by paths, keywords in text, or an equal failure type.

    Paths and guard patterns are compared relative to root, which defaults to
    the project root, so absolute and relative forms of the same path match.
    Each returned guard carries `matched_on`, naming the triggers that matched.
    """
    candidates = active_guards(memory) if guards is None else guards
    if root is None and paths and any(guard['paths'] for guard in candidates):
        root = project_root(memory)
    result = []
    for guard in candidates:
        matched = []
        if guard['paths'] and any(
                match_path(path, guard['paths'], root=root)
                or any(patterns_overlap(path, pattern, root=root) for pattern in guard['paths'])
                for path in paths):
            matched.append('paths')
        if text and any(_keyword_found(keyword, text) for keyword in guard['keywords']):
            matched.append('keywords')
        if failure_type and guard['failure_type'] == failure_type:
            matched.append('failure_type')
        if matched:
            result.append({**guard, 'matched_on': matched})
    return result


def require_acknowledgement(memory, episode, payload):
    """Reject a decision that does not acknowledge every matching guard."""
    from .planning import latest
    considered = payload.get('lessons_considered', [])
    for entry in considered:
        row = memory.db.execute('SELECT kind FROM events WHERE id=?', (entry['lesson_id'],)).fetchone()
        if row is None or row['kind'] != 'lesson':
            raise InvalidRecord(f'lessons_considered must reference lesson records. {entry["lesson_id"]} is not a lesson.')
    plan = latest(memory, episode['id'], 'work_plan')
    parts = [episode['objective'], episode['criterion'], payload.get('decision', ''), payload.get('why', '')]
    if plan:
        parts.append(plan.get('scope', ''))
    paths = plan.get('paths', []) if plan else []
    matched = matching_guards(memory, paths=paths, text='\n'.join(parts))
    acknowledged = {entry['lesson_id'] for entry in considered}
    missing = [guard for guard in matched if guard['lesson_id'] not in acknowledged]
    if not missing:
        return
    names = ', '.join(guard['lesson_id'] for guard in missing)
    raise InvalidRecord(
        f'This decision matches accepted lessons that it does not acknowledge: {names}. '
        'Read each lesson and add it to lessons_considered with applies yes or no and a reason.',
        matched_lessons=[{'lesson_id': guard['lesson_id'], 'when': guard['when'], 'do': guard['do'],
                          'exceptions': guard['exceptions']} for guard in missing],
        next_step={'action': 'acknowledge_lessons',
                   'read_with': {'view': 'record', 'id': missing[0]['lesson_id']}})


def recurrences(memory, *, limit=50):
    """Bad outcomes with a guard's failure type recorded after its acceptance, until the user reassesses them."""
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise InvalidRecord('limit must be between 1 and 1000.')
    result = []
    for guard in active_guards(memory):
        if not guard['failure_type']:
            continue
        query = f"""FROM events o WHERE {COUNTED_FAILURE}
            AND json_extract(o.payload,'$.failure_type')=?
            AND o.rowid > (SELECT rowid FROM events WHERE id=?)"""
        arguments = (guard['failure_type'], guard['review_id'])
        total = memory.db.execute('SELECT count(*) ' + query, arguments).fetchone()[0]
        if not total:
            continue
        rows = memory.db.execute('SELECT o.id, o.episode_id, o.decision_id, o.created_at, o.payload ' + query +
                                 ' ORDER BY o.rowid DESC LIMIT ?', (*arguments, limit)).fetchall()
        outcomes = []
        for row in rows:
            outcome = json.loads(row['payload'])
            outcomes.append({'id': row['id'], 'episode_id': row['episode_id'], 'decision_id': row['decision_id'],
                             'created_at': row['created_at'], 'severity': outcome['severity'],
                             'observed': outcome['observed']})
        result.append({'lesson_id': guard['lesson_id'], 'review_id': guard['review_id'],
                       'failure_type': guard['failure_type'], 'accepted_at': guard['accepted_at'],
                       'total': total, 'outcomes': outcomes})
        if len(result) >= limit:
            break
    return result


def failures_without_lesson(memory, *, limit=50):
    """Latest bad outcomes per decision that no lesson or later success addresses.

    A lesson addresses a failure when it links to the outcome or its decision
    through event_links, or when it is recorded later in the same episode.
    """
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise InvalidRecord('limit must be between 1 and 1000.')
    rows = memory.db.execute("""SELECT o.id, o.episode_id, o.decision_id, o.created_at, o.payload,
            ep.title, ep.subject FROM events o JOIN episodes ep ON ep.id=o.episode_id
        WHERE o.kind='outcome'
          AND o.seq=(SELECT max(x.seq) FROM events x WHERE x.kind='outcome' AND x.decision_id=o.decision_id)
          AND json_extract(o.payload,'$.assessment')='bad'
          AND NOT EXISTS (SELECT 1 FROM event_links k JOIN events l ON l.id=k.event_id
                          WHERE l.kind='lesson' AND k.prior_event_id IN (o.id, o.decision_id))
          AND NOT EXISTS (SELECT 1 FROM events l WHERE l.kind='lesson' AND l.episode_id=o.episode_id
                          AND l.rowid > o.rowid)
          AND NOT EXISTS (SELECT 1 FROM events g WHERE g.kind='outcome' AND g.episode_id=o.episode_id
                          AND g.rowid > o.rowid AND json_extract(g.payload,'$.assessment')='good'
                          AND json_extract(g.payload,'$.completion')='complete')
        ORDER BY o.rowid DESC LIMIT ?""", (limit,)).fetchall()
    result = []
    for row in rows:
        outcome = json.loads(row['payload'])
        result.append({'outcome_id': row['id'], 'episode_id': row['episode_id'], 'decision_id': row['decision_id'],
                       'title': row['title'], 'subject': row['subject'], 'created_at': row['created_at'],
                       'severity': outcome['severity'], 'failure_type': outcome.get('failure_type'),
                       'observed': outcome['observed']})
    return result


def scope_changes(memory, *, limit=20):
    """Plan revisions by an actor other than workspace-user that add path patterns."""
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise InvalidRecord('limit must be between 1 and 1000.')
    rows = memory.db.execute("""SELECT n.id, n.episode_id, n.actor, n.created_at, n.payload, p.id AS previous_id,
            p.payload AS previous FROM events n JOIN events p ON p.id=n.supersedes
        WHERE n.kind='work_plan' AND n.actor != 'workspace-user'
          AND json_extract(n.payload,'$.paths') IS NOT NULL
        ORDER BY n.rowid DESC""")
    result = []
    for row in rows:
        current = json.loads(row['payload'])
        previous = set(json.loads(row['previous']).get('paths', []))
        added = [pattern for pattern in current['paths'] if pattern not in previous]
        if not added:
            continue
        result.append({'episode_id': row['episode_id'], 'plan_id': row['id'], 'previous_plan_id': row['previous_id'],
                       'actor': row['actor'], 'created_at': row['created_at'], 'added': added,
                       'reason': current['reason']})
        if len(result) >= limit:
            break
    return result


# Rules that target a role.

def validate_roles(value):
    """Check the optional roles field of a lesson or a lesson review."""
    if not isinstance(value, list) or not 1 <= len(value) <= len(RULE_ROLES):
        raise InvalidRecord(f'roles must be a list of 1 to {len(RULE_ROLES)} role names.')
    for role in value:
        if role not in RULE_ROLES:
            raise InvalidRecord('roles must name assistant, worker or reviewer.')
    if len(set(value)) != len(value):
        raise InvalidRecord('Each role appears at most once in roles.')
    return value


def _require_role(role):
    if role not in RULE_ROLES:
        raise InvalidRecord('role must be assistant, worker or reviewer.')


def _require_limit(limit):
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise InvalidRecord('limit must be between 1 and 1000.')


def _table_exists(memory, name):
    """True when the table is present, so read paths never create it."""
    return memory.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _columns(memory, name):
    return {row[1] for row in memory.db.execute(f'PRAGMA table_info({name})')} if _table_exists(memory, name) else set()


def recurrence_count(memory, failure_type, review_id, *, after=True):
    """Bad outcomes with this failure type before or after the acceptance, until the user reassesses them."""
    if not failure_type or not review_id:
        return 0
    comparison = '>' if after else '<'
    row = memory.db.execute(f"""SELECT count(*) FROM events o WHERE {COUNTED_FAILURE}
        AND json_extract(o.payload,'$.failure_type')=?
        AND o.rowid {comparison} (SELECT rowid FROM events WHERE id=?)""", (failure_type, review_id)).fetchone()
    return row[0] if row else 0


def role_rules(memory, role, *, rules=None):
    """Accepted rules that target one role, oldest acceptance first.

    Without a given list the role is selected first and the freshness check of a
    review then runs for the rules of that role only, so a run of a role without
    rules pays one query. A given list is used as it is, because its caller
    already selected it.
    """
    _require_role(role)
    if rules is not None:
        return [rule for rule in rules if role in rule['roles']]
    return confirm_guards(memory, [rule for rule in candidate_guards(memory) if role in rule['roles']])


def _has_trigger(rule):
    return bool(rule['paths'] or rule['keywords'] or rule['failure_type'])


def matching_rules(memory, role, *, paths=(), text='', failure_types=(), rules=None, root=None):
    """Rules of a role that the run triggers, plus rules whose only trigger is the role.

    Triggered rules are selected by `matching_guards`, so paths, keywords and
    failure types behave exactly as they do for decisions.
    """
    candidates = role_rules(memory, role, rules=rules)
    triggered = [rule for rule in candidates if _has_trigger(rule)]
    matched = {}
    for failure_type in tuple(failure_types) or (None,):
        for rule in matching_guards(memory, paths=paths, text=text, failure_type=failure_type,
                                    guards=triggered, root=root):
            found = matched.get(rule['lesson_id'])
            if found is None:
                matched[rule['lesson_id']] = rule
                continue
            found['matched_on'] = found['matched_on'] + [name for name in rule['matched_on']
                                                         if name not in found['matched_on']]
    result = []
    for rule in candidates:
        if not _has_trigger(rule):
            result.append({**rule, 'matched_on': ['roles']})
        elif rule['lesson_id'] in matched:
            result.append(matched[rule['lesson_id']])
    return result


def _sentence(value):
    """A field of a lesson without its trailing full stop, so the rendered block reads as one sentence."""
    text = ' '.join(str(value).split())
    return text[:-1] if text.endswith('.') else text


def render_rule(rule):
    """The one block a rule contributes to a composed prompt."""
    return (f'When {_sentence(rule["when"])}. Do {_sentence(rule["do"])}. '
            f'Exceptions: {_sentence(rule["exceptions"])}.')


def render_machine_rule(rule):
    """The one block a rule of the machine memory contributes, marked as machine level."""
    return MACHINE_LABEL + render_rule(rule)


def machine_rules(machine, role, *, paths=(), text='', failure_types=(), root=None):
    """The rules of the machine memory that this run triggers, most recent acceptance first.

    Effectiveness stays in each project, so the machine memory records no
    recurrence and the order is the acceptance and then the identifier.
    """
    candidates = matching_rules(machine, role, paths=paths, text=text, failure_types=failure_types, root=root)
    ordered = sorted(candidates, key=lambda rule: rule['lesson_id'])
    ordered.sort(key=lambda rule: rule['accepted_at'], reverse=True)
    return ordered


def _machine_memory(machine):
    """The machine memory to compose from, whether this call opened it, and why it is missing.

    `machine` is a memory to use, False to leave the machine rules out, or None
    to read the machine memory of this computer when it exists.
    """
    if machine is False:
        return None, False, None
    if machine is not None:
        return machine, False, None
    from . import machine as machine_module
    try:
        found = machine_module.reader()
        return found, found is not None, None
    except MACHINE_READ_ERRORS as error:
        # A machine memory that cannot be read must not stop the run of a project.
        return None, False, MACHINE_UNREADABLE + ' ' + str(error)


MACHINE_UNREADABLE = 'The machine memory could not be read, so no machine rule was composed.'


def _characters(count):
    return f'the {count} character that remains' if count == 1 else f'the {count} characters that remain'


def _compose_machine(result, machine, role, *, paths, text, failure_types, root, places, exclude=()):
    """Append the machine rules of a role after the rules of the project, within the budget of the role."""
    memory, opened, error = _machine_memory(machine)
    budget = min(MACHINE_BUDGETS[role], max(0, result['budget'] - result['used']))
    result.update({'machine_rule_ids': [], 'machine_budget': budget, 'machine_used': 0, 'machine_total': 0,
                   'machine_text': ''})
    if error:
        result['machine_error'] = error
    if memory is None:
        return result
    try:
        try:
            candidates = machine_rules(memory, role, paths=paths, text=text, failure_types=failure_types, root=root)
        except MACHINE_READ_ERRORS as failure:
            result['machine_error'] = MACHINE_UNREADABLE + ' ' + str(failure)
            return result
        excluded = set(exclude or ())
        candidates = [rule for rule in candidates if rule['lesson_id'] not in excluded]
        result['machine_total'] = len(candidates)
        blocks = []
        used = 0
        for position, rule in enumerate(candidates):
            block = render_machine_rule(rule)
            # The first machine rule also carries the heading of the machine rules.
            length = len(block) + (1 if blocks else MACHINE_OVERHEAD)
            if position + places >= MAX_ACTIVE_RULES:
                result['omitted'].append({'lesson_id': rule['lesson_id'], 'level': 'machine',
                                          'reason': f'A prompt carries at most {MAX_ACTIVE_RULES} rules for one role, '
                                                    'so this machine rule was not composed.'})
                continue
            if used + length > budget:
                result['omitted'].append({'lesson_id': rule['lesson_id'], 'level': 'machine',
                                          'reason': f'This machine rule did not fit within {_characters(budget)} '
                                                    'for machine rules in this role.'})
                continue
            blocks.append(block)
            result['machine_rule_ids'].append(rule['lesson_id'])
            used += length
        if blocks:
            result['machine_text'] = '\n'.join(blocks)
            result['text'] = '\n'.join([result['text']] + blocks) if result['text'] else result['machine_text']
            result['machine_overhead'] = MACHINE_OVERHEAD
            result['used'] += used
            result['machine_used'] = used
    finally:
        if opened:
            memory.close()
    return result


def compose(memory, role, *, paths=(), text='', failure_types=(), budget=None, rules=None, root=None, exclude=(),
            machine=None, machine_exclude=()):
    """The rules composed into a prompt for one role, with every omission reported.

    Order: fewest recurrences after acceptance first, then the most recent
    acceptance, then the lesson identifier, so the same run composes the same
    text. Rules beyond `MAX_ACTIVE_RULES` or beyond the character budget are
    returned under `omitted` with the reason. `exclude` names rules the prompt
    already carries elsewhere, for example as the constraints of a review; they
    are reported as left out instead of being written twice.

    The rules promoted to this machine follow the rules of this project, each
    marked as machine level. They keep to the sub budget of the role and to what
    the project rules leave of the role budget, so a project rule is never
    displaced by a machine rule, and a machine rule left out is reported like any
    other omission.
    """
    _require_role(role)
    if budget is None:
        budget = ROLE_BUDGETS[role]
    if type(budget) is not int or not 0 <= budget <= 20000:
        raise InvalidRecord('budget must be between 0 and 20000 characters.')
    rules = role_rules(memory, role, rules=rules)
    excluded = set(exclude or ())
    candidates = matching_rules(memory, role, paths=paths, text=text, failure_types=failure_types,
                                rules=rules, root=root)
    carried = [rule for rule in candidates if rule['lesson_id'] in excluded]
    counted = [{**rule, 'recurrences_after': recurrence_count(memory, rule['failure_type'], rule['review_id'])}
               for rule in candidates if rule['lesson_id'] not in excluded]
    ordered = sorted(counted, key=lambda rule: rule['lesson_id'])
    ordered.sort(key=lambda rule: rule['accepted_at'], reverse=True)
    ordered.sort(key=lambda rule: rule['recurrences_after'])
    blocks = []
    rule_ids = []
    omitted = []
    matched_ids = {rule['lesson_id'] for rule in ordered}
    # A rule whose block is longer than the whole budget is reported here even when this run does not
    # match it, because no run of this role can carry it and the user would otherwise wait for a run
    # that can never exist.
    oversize = [rule for rule in rules if rule['lesson_id'] not in matched_ids
                and rule['lesson_id'] not in excluded and len(render_rule(rule)) > budget]
    used = 0
    for position, rule in enumerate(ordered):
        if position >= MAX_ACTIVE_RULES:
            omitted.append({'lesson_id': rule['lesson_id'],
                            'reason': f'A prompt carries at most {MAX_ACTIVE_RULES} rules for one role, '
                                      'so this rule was not composed.'})
            continue
        block = render_rule(rule)
        length = len(block) + (1 if blocks else 0)
        if used + length > budget:
            omitted.append({'lesson_id': rule['lesson_id'],
                            'reason': f'This rule did not fit within the budget of {budget} characters.'})
            continue
        blocks.append(block)
        rule_ids.append(rule['lesson_id'])
        used += length
    for rule in carried:
        omitted.append({'lesson_id': rule['lesson_id'],
                        'reason': 'This prompt already carries this rule among its constraints, '
                                  'so it is not repeated in the rules.'})
    for rule in oversize:
        omitted.append({'lesson_id': rule['lesson_id'],
                        'reason': f'This rule alone is longer than the budget of {budget} characters, '
                                  'so no run of this role can carry it.'})
    result = {'role': role, 'text': '\n'.join(blocks), 'project_text': '\n'.join(blocks), 'rule_ids': rule_ids,
              'omitted': omitted, 'budget': budget, 'used': used, 'matched_total': len(ordered),
              'accepted_total': len(rules)}
    return _compose_machine(result, machine, role, paths=paths, text=text, failure_types=failure_types, root=root,
                            places=len(rule_ids), exclude=machine_exclude)


# Base text and the composed instructions of a run.

def base_source_key(role):
    """The source key of the project base text for a role."""
    _require_role(role)
    return BASE_SOURCE_PREFIX + role


def run_source_key(role):
    """The source key under which a run stores the exact text it received."""
    _require_role(role)
    return RUN_SOURCE_PREFIX + role


def shipped_base(role):
    """The base text shipped with the package, or an empty string when the role has no file."""
    _require_role(role)
    path = Path(__file__).with_name('agents').joinpath(role + '.md')
    return path.read_text(encoding='utf-8').strip() if path.exists() else ''


def base_text(memory, role):
    """The base text in force for a role: the project version when the user saved one, otherwise the shipped file."""
    _require_role(role)
    # Only a version whose origin is the user is in force, so a source written by a tool never
    # becomes the base text, even in a project recorded before the key was reserved.
    row = memory.db.execute("SELECT id,version,body FROM sources WHERE source_key=? AND origin='user' "
                            'ORDER BY version DESC LIMIT 1', (base_source_key(role),)).fetchone()
    if row:
        return {'text': row['body'].strip(), 'source': base_source_key(role), 'source_id': row['id'],
                'version': row['version']}
    shipped = shipped_base(role)
    return {'text': shipped, 'source': SHIPPED_BASE_SOURCE.format(role=role) if shipped else 'none',
            'source_id': None, 'version': None}


def instructions(memory, role, *, paths=(), text='', failure_types=(), budget=None, rules=None, root=None, exclude=(),
                 machine=None):
    """The complete text for a role: the base text first, then the composed rules.

    The omissions of `compose` are reported unchanged, so a caller can show
    which accepted rules the run did not carry.
    """
    base = base_text(memory, role)
    composed = compose(memory, role, paths=paths, text=text, failure_types=failure_types, budget=budget,
                       rules=rules, root=root, exclude=exclude, machine=machine)
    parts = [base['text']] if base['text'] else []
    if composed['project_text']:
        parts.append(RULES_HEADING + '\n' + composed['project_text'])
    if composed['machine_text']:
        parts.append(MACHINE_HEADING + '\n' + composed['machine_text'])
    complete = '\n\n'.join(parts)
    return {'role': role, 'text': complete, 'base': base['text'], 'rules': composed['text'],
            'base_source': base['source'], 'base_source_id': base['source_id'], 'base_version': base['version'],
            'rule_ids': composed['rule_ids'], 'omitted': composed['omitted'], 'budget': composed['budget'],
            'used': composed['used'], 'matched_total': composed['matched_total'],
            'accepted_total': composed['accepted_total'], 'machine_rule_ids': composed['machine_rule_ids'],
            'machine_rules': composed['machine_text'], 'machine_budget': composed['machine_budget'],
            'machine_used': composed['machine_used'], 'machine_total': composed['machine_total'],
            'machine_error': composed.get('machine_error'), 'characters': len(complete)}


# Effectiveness of the rules in force.

def _review_verdicts(memory, columns):
    """The verdict of the cross review of each delegated work run, by the identifier of that work run."""
    found = {}
    if 'parent_run' not in columns:
        return found
    rows = memory.db.execute("SELECT parent_run,report FROM review_runs WHERE role='work_review' "
                             'AND parent_run IS NOT NULL AND report IS NOT NULL ORDER BY rowid').fetchall()
    for row in rows:
        try:
            report = json.loads(row['report'])
        except ValueError:
            continue
        verdict = report.get('verdict') if isinstance(report, dict) else None
        if verdict in VERDICTS:
            found[row['parent_run']] = verdict
    return found


def _run_verdict(row, report, reviews_by_run):
    """The verdict of a run.

    A check reports its own verdict. A delegated work run reports a result
    instead, so the verdict of the cross review that judged that work is the
    verdict of the run, and the rules the worker received are counted against it.
    """
    verdict = report.get('verdict') if isinstance(report, dict) else None
    if verdict in VERDICTS:
        return verdict
    reviewed = reviews_by_run.get(row['id'])
    if reviewed in VERDICTS:
        return reviewed
    return 'pending'


def _composed_runs(memory):
    """Runs that recorded composed rule identifiers in their metrics, grouped by rule."""
    grouped = {}
    columns = _columns(memory, 'review_runs')
    if not {'metrics', 'report', 'state', 'role'} <= columns:
        return grouped
    reviews_by_run = _review_verdicts(memory, columns)
    rows = memory.db.execute("SELECT id,role,state,report,metrics FROM review_runs "
                             "WHERE metrics IS NOT NULL AND metrics LIKE '%rule_ids%' ORDER BY rowid").fetchall()
    for row in rows:
        try:
            metrics = json.loads(row['metrics'])
            report = json.loads(row['report']) if row['report'] else {}
        except ValueError:
            continue
        identifiers = metrics.get('rule_ids') if isinstance(metrics, dict) else None
        if not isinstance(identifiers, list):
            continue
        entry = {'run_id': row['id'], 'role': row['role'], 'state': row['state'],
                 'verdict': _run_verdict(row, report, reviews_by_run),
                 'instruction_source': metrics.get('instruction_source')}
        for lesson_id in identifiers:
            if isinstance(lesson_id, str):
                grouped.setdefault(lesson_id, []).append(entry)
    return grouped


def _count(value, noun):
    """A count with its noun, so a sentence reads correctly with the number one."""
    return f'{value} {noun}' if value == 1 else f'{value} {noun}s'


def _times(value):
    return _count(value, 'time')


def _state(verdicts, assessed, before, after):
    """The state of a rule from counts alone.

    A single recurrence decides nothing, so the comparison of the recurrences
    before and after acceptance is used only once at least RECURRENCE_MINIMUM of
    them were recorded. Below that the verdicts of the runs decide, and a rule
    with too few of those stays unproven.
    """
    if before + after >= RECURRENCE_MINIMUM:
        if after and after >= before:
            return 'ineffective'
        if before and not after:
            return 'effective'
    if assessed >= EFFECTIVENESS_MINIMUM:
        if not verdicts['changes_required']:
            return 'effective'
        if verdicts['changes_required'] > verdicts['pass']:
            return 'ineffective'
    return 'unproven'


def _effectiveness_note(state, assessed, before, after):
    """One plain sentence for the state, which says when the counts are too small to claim a change."""
    small = assessed < EFFECTIVENESS_MINIMUM and before + after < EFFECTIVENESS_MINIMUM
    if state == 'unproven':
        return ('The counts are too small to separate this rule from the rest of the run, with '
                + _count(assessed, 'assessed run') + ' and ' + _count(after, 'recurrence') + ' after acceptance.')
    caveat = ' These counts are small, so they do not establish the change on their own.' if small else ''
    if state == 'effective':
        return ('The failure type recurred ' + _times(before) + ' before acceptance and ' + _times(after)
                + ' after it, over ' + _count(assessed, 'assessed run') + '.' + caveat)
    return ('The failure type recurred ' + _times(after) + ' after acceptance against ' + _times(before)
            + ' before it, over ' + _count(assessed, 'assessed run') + '.' + caveat
            + ' Return this rule to the user.')


def effectiveness(memory, *, limit=50):
    """Counts per rule: the runs that composed it, their verdicts, its recurrences and its state.

    Pending and uncertain verdicts stay out of the ratio and the denominators are
    always reported. No model is called and nothing is written.
    """
    _require_limit(limit)
    grouped = _composed_runs(memory)
    result = []
    for rule in active_guards(memory):
        if not rule['roles']:
            continue
        runs = grouped.get(rule['lesson_id'], [])
        verdicts = {name: 0 for name in VERDICTS + ('pending',)}
        for run in runs:
            verdicts[run['verdict']] += 1
        assessed = verdicts['pass'] + verdicts['changes_required']
        before = recurrence_count(memory, rule['failure_type'], rule['review_id'], after=False)
        after = recurrence_count(memory, rule['failure_type'], rule['review_id'])
        state = _state(verdicts, assessed, before, after)
        result.append({
            'lesson_id': rule['lesson_id'], 'review_id': rule['review_id'], 'accepted_at': rule['accepted_at'],
            'roles': list(rule['roles']), 'when': rule['when'], 'do': rule['do'],
            'failure_type': rule['failure_type'], 'runs': len(runs),
            'run_ids': [run['run_id'] for run in runs[:20]], 'verdicts': verdicts, 'assessed': assessed,
            'recurrences_before': before, 'recurrences_after': after, 'state': state,
            'note': _effectiveness_note(state, assessed, before, after)})
        if len(result) >= limit:
            break
    return result


def rule_counts(memory):
    """Accepted rules per role, so a surface can report the load without composing a prompt."""
    rules = active_guards(memory)
    return {role: len([rule for rule in rules if role in rule['roles']]) for role in RULE_ROLES}


# Host enforcement.

def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def edit_tool_name(tool_name):
    """Return the edit tool name without an MCP server prefix, or None for other tools."""
    if not isinstance(tool_name, str):
        return None
    if tool_name in EDIT_TOOLS:
        return tool_name
    if tool_name.startswith('mcp__'):
        name = tool_name.rsplit('__', 1)[-1]
        if name in MCP_EDIT_TOOLS:
            return name
    return None


def mcp_write_target(tool_name):
    """Return `mcp:<server>/<tool>` for an MCP tool that writes outside the project files, otherwise None.

    File tools of MCP servers are handled as edit tools with their paths, and the
    tools of Project Memory itself are never targets.
    """
    if not isinstance(tool_name, str) or not tool_name.startswith('mcp__') or edit_tool_name(tool_name):
        return None
    parts = tool_name[len('mcp__'):].rsplit('__', 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    server, tool = parts
    if any(marker in server.lower() for marker in OWN_SERVER_MARKERS):
        return None
    words = [word for word in re.split(r'[_\-.\s]+', re.sub(r'(?<=[a-z0-9])(?=[A-Z])', '_', tool).lower()) if word]
    if not words or words[0] in READ_VERBS or not any(word in WRITE_VERBS for word in words):
        return None
    return MCP_TARGET_PREFIX + server + '/' + tool


def _escaped_markers(text):
    """Patch marker targets written with escaped line breaks or after a quote.

    Lines that look like patch body lines are skipped, so a patch or heredoc
    that adds text containing marker examples is not misread.
    """
    found = []
    for line in text.split('\n'):
        if not line or line.startswith(PATCH_BODY_PREFIXES) or '*** ' not in line:
            continue
        expanded = line.replace('\\r\\n', '\n').replace('\\n', '\n')
        found.extend(match.group(1) for match in QUOTED_PATCH_MARKER.finditer(expanded))
    return found


def edit_targets(tool_name, tool_input):
    """Paths an edit tool call would change, in order and without duplicates.

    An MCP write tool without file paths yields the target `mcp:<server>/<tool>`.
    """
    targets = []
    external = mcp_write_target(tool_name)
    if external:
        targets.append(external)
    name = edit_tool_name(tool_name)
    if name and isinstance(tool_input, dict):
        keys = PATH_KEYS + MOVE_KEYS if name == 'move_file' else PATH_KEYS
        for key in keys:
            value = tool_input.get(key)
            if isinstance(value, str) and value.strip():
                targets.append(value.strip())
    for text in _strings(tool_input):
        if '*** ' in text:
            targets.extend(match.group(1) for match in PATCH_MARKER.finditer(text))
            if not name:
                targets.extend(_escaped_markers(text))
    return list(dict.fromkeys(targets))


def session_work(memory, session_id):
    """The session's current work: its in-progress plan, else the plan of its bound decision."""
    from . import codex_host
    from .planning import latest
    row = memory.db.execute("""SELECT e.id, e.episode_id, e.payload FROM events e WHERE e.kind='work_plan'
          AND e.seq=(SELECT max(n.seq) FROM events n WHERE n.episode_id=e.episode_id AND n.kind='work_plan')
          AND json_extract(e.payload,'$.state')='in_progress' AND json_extract(e.payload,'$.session_id')=?
        ORDER BY e.rowid DESC LIMIT 1""", (session_id,)).fetchone()
    if row:
        return {'id': row['id'], 'episode_id': row['episode_id'], **json.loads(row['payload'])}
    if not codex_host.exists(memory):
        return None
    binding = codex_host.active_binding(memory, session_id)
    if not binding:
        return None
    plan = latest(memory, binding['episode_id'], 'work_plan')
    return {**plan, 'episode_id': binding['episode_id']} if plan else None


def relative_targets(targets, project_root, cwd=None):
    """Targets relative to the project root when inside it, otherwise absolute."""
    roots = _roots(project_root)
    base = normalize(cwd) if isinstance(cwd, str) and is_absolute(cwd) else normalize(project_root)
    result = []
    for target in targets:
        if target.startswith(MCP_TARGET_PREFIX):
            result.append(target)
            continue
        absolute = normalize(target) if is_absolute(target) else normalize(posixpath.join(base, normalize(target)))
        relative = _relative(absolute, roots)
        if relative == absolute:
            relative = _relative(normalize(os.path.realpath(absolute)), roots)
            if is_absolute(relative):
                relative = absolute
        result.append(relative)
    return list(dict.fromkeys(result))


def scope_check(memory, *, session_id, event, project_root):
    """Return the blocked targets of a PreToolUse edit outside the session's work paths."""
    if event.get('hook_event_name') != 'PreToolUse':
        return None
    targets = edit_targets(event.get('tool_name') or '', event.get('tool_input'))
    if not targets:
        return None
    plan = session_work(memory, session_id)
    if not plan or not plan.get('paths'):
        return None
    targets = relative_targets(targets, project_root, event.get('cwd'))
    blocked = [target for target in targets if not match_path(target, plan['paths'], root=project_root)]
    if not blocked:
        return None
    return {'episode_id': plan['episode_id'], 'plan_id': plan['id'], 'blocked': blocked,
            'allowed_patterns': list(plan['paths'])}


def blocked_message(result):
    text = (f'Project Memory blocked this edit because {", ".join(result["blocked"])} '
            f'{"is" if len(result["blocked"]) == 1 else "are"} outside the recorded scope of work {result["episode_id"]}. '
            f'Allowed paths: {", ".join(result["allowed_patterns"])}. '
            'Ask the user to extend the scope in the chat and record it with user_action, or record a plan revision with the reason.')
    if any(target.startswith(MCP_TARGET_PREFIX) for target in result['blocked']):
        text += (' A target that starts with mcp: is a write tool of an MCP server that changes something outside the project files. '
                 'The plan pattern mcp:<server> allows the write tools of that server.')
    return text


def guard_reminders(memory, *, session_id, targets, root=None):
    """Guards whose paths match a target and that this session has not been shown yet.

    Targets and guard patterns are compared relative to root, which defaults to
    the project root.
    """
    from . import codex_host
    if not targets:
        return []
    if root is None:
        root = project_root(memory)
    recording = codex_host.exists(memory)
    shown = []
    with memory._write():
        for guard in active_guards(memory):
            if not guard['paths']:
                continue
            matched = [target for target in targets if match_path(target, guard['paths'], root=root)]
            if not matched:
                continue
            key = f'guard-shown:{session_id}:{guard["lesson_id"]}'
            if recording:
                receipt_id = 'host_' + _digest(key)[:32]
                if memory.db.execute('SELECT 1 FROM host_receipts WHERE id=?', (receipt_id,)).fetchone():
                    continue
                codex_host.receipt(memory, session_id=session_id, event_name='GuardShown', episode_id=None,
                                   payload={'lesson_id': guard['lesson_id'], 'review_id': guard['review_id'],
                                            'targets': matched}, key=key)
            shown.append({**guard, 'targets': matched})
    return shown


def reminder_text(guards):
    lines = []
    for guard in guards:
        lines.append(f'Project Memory lesson {guard["lesson_id"]} applies to {", ".join(guard.get("targets", guard["paths"]))}. '
                     f'When: {guard["when"]} Do: {guard["do"]} Exceptions: {guard["exceptions"]}')
    return ' '.join(lines)


def scope_sentence(memory, session_id):
    """One sentence naming the session's allowed patterns and up to three matching guards, or an empty string."""
    plan = session_work(memory, session_id)
    if not plan or not plan.get('paths'):
        return ''
    guards = matching_guards(memory, paths=plan['paths'])[:3]
    text = f'Work {plan["episode_id"]} allows edits only in {", ".join(plan["paths"])}'
    if guards:
        text += ', and lessons ' + ', '.join(guard['lesson_id'] for guard in guards) + ' apply to these paths'
    return text + '.'
