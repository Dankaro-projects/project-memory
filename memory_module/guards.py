"""Lesson triggers, accepted guards, decision acknowledgement and file scope enforcement.

An accepted lesson with at least one trigger (paths, keywords or a failure type)
becomes a guard. Decisions whose work matches a guard must acknowledge it, and
edit tools are checked against the path patterns of the session's current work.

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
import posixpath
import re
from functools import lru_cache

from .core import InvalidRecord, MemoryError, _digest, _text

TRIGGERS = ('paths', 'keywords', 'failure_type')
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

def normalize(path):
    """Return a POSIX path without empty or current directory segments."""
    text = str(path)
    if not text:
        return '.'
    absolute = text.startswith('/')
    text = posixpath.normpath(text)
    parts = [part for part in text.split('/') if part not in ('', '.')]
    joined = '/'.join(parts)
    if absolute:
        return '/' + joined
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
        return not path.startswith('/')
    if prefix == '/':
        return path.startswith('/')
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
        if candidate.startswith('/') != target.startswith('/'):
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


def active_guards(memory):
    """Accepted lessons with at least one trigger, oldest acceptance first."""
    rows = memory.db.execute("""SELECT l.id, l.payload, l.episode_id, l.subject FROM events l
        WHERE l.kind='lesson' AND NOT EXISTS (SELECT 1 FROM events n WHERE n.supersedes=l.id)
          AND EXISTS (SELECT 1 FROM events r WHERE r.kind='lesson_review'
                      AND json_extract(r.payload,'$.lesson_id')=l.id)
        ORDER BY l.rowid""").fetchall()
    guards = []
    for row in rows:
        review = memory._lesson_review(row['id'])
        if not review or review['status'] != 'accepted':
            continue
        lesson = json.loads(row['payload'])
        source = review if any(key in review for key in TRIGGERS) else lesson
        guard = {
            'lesson_id': row['id'],
            'review_id': review['event_id'],
            'accepted_at': memory.db.execute('SELECT created_at FROM events WHERE id=?', (review['event_id'],)).fetchone()[0],
            'when': lesson['when'],
            'do': lesson['do'],
            'because': lesson['because'],
            'exceptions': lesson['exceptions'],
            'pattern_type': lesson.get('pattern_type', 'practice'),
            'paths': list(source.get('paths', [])),
            'keywords': list(source.get('keywords', [])),
            'failure_type': source.get('failure_type'),
            'subject': row['subject'],
            'episode_id': row['episode_id'],
        }
        if guard['paths'] or guard['keywords'] or guard['failure_type']:
            guards.append(guard)
    guards.sort(key=lambda guard: guard['accepted_at'])
    return guards


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
    """Current bad outcomes with a guard's failure type, recorded after its acceptance."""
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise InvalidRecord('limit must be between 1 and 1000.')
    result = []
    for guard in active_guards(memory):
        if not guard['failure_type']:
            continue
        query = """FROM events o WHERE o.kind='outcome'
            AND NOT EXISTS (SELECT 1 FROM events n WHERE n.supersedes=o.id)
            AND json_extract(o.payload,'$.assessment')='bad'
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
    base = cwd if isinstance(cwd, str) and cwd.startswith('/') else normalize(project_root)
    result = []
    for target in targets:
        if target.startswith(MCP_TARGET_PREFIX):
            result.append(target)
            continue
        absolute = normalize(target if target.startswith('/') else posixpath.join(base, target))
        relative = _relative(absolute, roots)
        if relative == absolute:
            relative = _relative(normalize(os.path.realpath(absolute)), roots)
            if relative.startswith('/'):
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
            'Ask the user to extend the scope in the control panel, or record a plan revision with the reason.')
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
