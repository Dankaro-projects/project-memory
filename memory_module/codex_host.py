"""Mechanical host receipts from Codex or Claude Code, separate from interpreted decisions and lessons."""
import argparse
import hashlib
import json
import re
import shlex
import sqlite3
import sys
from pathlib import Path
from .core import Memory, InvalidRecord, Conflict, BudgetTooSmall, dumps, _text, _digest
from . import guards, shared

HOST_SCHEMA = '''
CREATE TABLE IF NOT EXISTS host_receipts (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL, turn_id TEXT NOT NULL,
 event_name TEXT NOT NULL, tool_use_id TEXT NOT NULL, tool_name TEXT NOT NULL,
 episode_id TEXT REFERENCES episodes(id), decision_id TEXT REFERENCES events(id),
 created_at TEXT NOT NULL, payload TEXT NOT NULL, signature TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS host_session ON host_receipts(session_id,event_name,tool_use_id);
CREATE INDEX IF NOT EXISTS host_decision ON host_receipts(decision_id,event_name);
CREATE TRIGGER IF NOT EXISTS immutable_host_update BEFORE UPDATE ON host_receipts BEGIN
 SELECT RAISE(ABORT,'Host receipts cannot be changed.'); END;
CREATE TRIGGER IF NOT EXISTS immutable_host_delete BEFORE DELETE ON host_receipts BEGIN
 SELECT RAISE(ABORT,'Host receipts cannot be deleted.'); END;
CREATE TABLE IF NOT EXISTS adapter_requests (
 request_key TEXT PRIMARY KEY, signature TEXT NOT NULL, result TEXT NOT NULL
);
'''
EVENTS = {'SessionStart', 'UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'Stop', 'Interrupt', 'SessionEnd', 'PreCompact', 'PostCompact'}
HOSTS = {'codex', 'claude'}
# Claude Code has no Interrupt hook. An interrupted tool call leaves its PreToolUse receipt unconfirmed instead.
HOST_EVENTS = {'codex': EVENTS, 'claude': EVENTS - {'Interrupt'}}
# Claude Code events that carry the same meaning as a canonical event. The original name stays in the payload.
CLAUDE_ALIASES = {'PostToolUseFailure': 'PostToolUse'}
MEMORY_TOOL = re.compile(r'^mcp__.+__memory_(context|get|write)$')
# The hook context of a host stays within this many characters, as it did before rules were composed.
HOOK_CHARACTERS = 2500
# The rules keep room for the rest of the hook context, which is one short sentence.
RULE_CHARACTERS = 2000
# The prompt text read for the keywords of a rule.
PROMPT_CHARACTERS = 2000


# Tools that only read. A turn whose tool calls are all read only needs no intent assessment.
READ_TOOLS = {'Read', 'Grep', 'Glob', 'LS', 'NotebookRead', 'ToolSearch', 'WebSearch', 'WebFetch', 'TaskOutput',
              'ListMcpResourcesTool', 'ReadMcpResourceTool', 'ReadMcpResourceDirTool', 'view_image'}
SHELL_TOOLS = {'Bash', 'shell', 'exec_command', 'local_shell'}
# Shell commands that only read. Any other command, an output redirection, a substitution or a here document makes
# the call material, so an error in this list can only ask for an assessment that was not needed.
READ_COMMANDS = {'cd', 'ls', 'cat', 'head', 'tail', 'wc', 'grep', 'rg', 'find', 'sed', 'sort', 'uniq', 'cut', 'tr',
                 'echo', 'pwd', 'which', 'file', 'stat', 'du', 'diff', 'jq', 'tree', 'date', 'nl', 'basename',
                 'dirname', 'realpath', 'true'}
READ_GIT = {'log', 'status', 'diff', 'show', 'rev-parse', 'ls-files', 'blame', 'grep', 'describe', 'shortlog'}
LISTING_GIT = {'tag', 'branch'}
LISTING_CHANGES = {'-a', '-s', '-u', '-f', '-d', '-D', '-m', '-M', '-c', '-C', '--delete', '--force', '--move', '--copy',
                   '--annotate', '--sign', '--message', '--set-upstream-to', '--unset-upstream', '--edit-description'}
WRITE_OPTIONS = {'--in-place', '-delete', '-exec', '-execdir', '-ok', '-okdir', '-fprint', '-fprintf', '-fls', '--output'}
HARMLESS_REDIRECTS = re.compile(r'(?<!\S)(?:[12&]?>\s*/dev/null|2>&1)(?=\s|$|[;&|])')


def shell_command(tool_input):
    """The command text of a shell tool call, or None."""
    if not isinstance(tool_input, dict):
        return None
    command = tool_input.get('command', tool_input.get('cmd'))
    if isinstance(command, list) and command and all(isinstance(part, str) for part in command):
        if len(command) >= 3 and command[0].rsplit('/', 1)[-1] in {'bash', 'zsh', 'sh'} and command[-2] in {'-c', '-lc'}:
            return command[-1]
        return shlex.join(command)
    return command if isinstance(command, str) else None


def read_only(tool, tool_input):
    """True when a tool call cannot change files, records or anything outside the session."""
    if tool in READ_TOOLS:
        return True
    if tool not in SHELL_TOOLS:
        return False
    command = shell_command(tool_input)
    if not command or '$(' in command or '`' in command or '<<' in command:
        return False
    try:
        lexer = shlex.shlex(HARMLESS_REDIRECTS.sub(' ', command).replace('\n', ' ; '), posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return False
    segments, words = [], []
    for token in tokens:
        if token in {';', '&&', '||', '|'}:
            segments.append(words)
            words = []
        elif token and set(token) <= set('();<>|&'):
            return False
        else:
            words.append(token)
    segments.append(words)
    for words in filter(None, segments):
        name = words[0].rsplit('/', 1)[-1]
        options = words[1:]
        if any(word.split('=')[0] in WRITE_OPTIONS for word in options):
            return False
        if name == 'git':
            if options[:1] == ['-C']:
                options = options[2:]
            if not options:
                return False
            if options[0] in LISTING_GIT:
                # git tag and git branch only list when they name nothing and change nothing.
                if any(not word.startswith('-') or word.split('=')[0] in LISTING_CHANGES for word in options[1:]):
                    return False
            elif options[0] not in READ_GIT:
                return False
        elif name not in READ_COMMANDS or name == 'sed' and any(word.startswith('-i') for word in options):
            return False
    return True


def initialize(memory):
    memory.db.executescript(HOST_SCHEMA)


def exists(memory):
    return bool(memory.db.execute("SELECT 1 FROM sqlite_master WHERE name='host_receipts'").fetchone())


def project_root(memory):
    """The project folder used to compare edit targets with recorded work paths."""
    try:
        from .architecture import project_root as architecture_root
    except ImportError:
        architecture_root = None
    if architecture_root is not None:
        return architecture_root(memory)
    for key in ('workspace_project', 'review_host'):
        row = memory.db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        if not row:
            continue
        try:
            value = json.loads(row[0])
        except ValueError:
            continue
        if key == 'review_host':
            value = value.get('project') if isinstance(value, dict) else None
        if isinstance(value, str) and value:
            return Path(value)
    return memory.path.parent.parent


def rule_shown(memory, session_id, lesson_id):
    """The receipt key of a rule in a session, and whether the session has already seen it."""
    key = f'guard-shown:{session_id}:{lesson_id}'
    rid = 'host_' + _digest(key)[:32]
    return key, bool(memory.db.execute('SELECT 1 FROM host_receipts WHERE id=?', (rid,)).fetchone())


def assistant_base(memory, *, available):
    """The base instruction text of the assistant role, when the room left in the hook context carries it.

    The base text is the text in force for the role: the version the user saved
    in this project, or the shipped file. A session receives it at its start and
    again after a compaction, because the earlier context is then gone.
    """
    base = guards.base_text(memory, 'assistant')['text']
    return base if base and len(base) <= available else ''


def assistant_rules(memory, *, session_id, targets, text='', root=None, limit=RULE_CHARACTERS):
    """The accepted rules for the files the assistant is about to edit, or for the text of its prompt.

    The rules are composed at the assistant budget, which also carries the
    identifier of each rule, and each rule is shown once in a session. A rule
    that does not fit keeps no receipt, so a later edit in the same session can
    still carry it, and the rules left out are always named, including when no
    rule at all fitted.
    """
    if not targets and not text:
        return {'text': '', 'rule_ids': [], 'omitted': []}
    if root is None:
        root = project_root(memory)
    recording = exists(memory)
    pending = []
    # The freshness check of a review is the expensive part, so the rules of this role that the
    # session has not seen yet are selected first and only those are confirmed.
    for guard in guards.candidate_guards(memory):
        roles = guard.get('roles') or []
        # A rule of the assistant always applies here. A lesson without roles keeps the path reminder it had before.
        if not (('assistant' in roles) or (not roles and guard['paths'])):
            continue
        if recording and rule_shown(memory, session_id, guard['lesson_id'])[1]:
            continue
        pending.append({**guard, 'roles': ['assistant']})
    # The triggers come from the same query, so this edit or this prompt selects the rules it can
    # carry before any freshness check runs. An edit that no rule covers then pays nothing for the
    # rules of the project.
    triggered = []
    selected = []
    for rule in pending:
        if rule['paths'] or rule['keywords'] or rule['failure_type']:
            triggered.append(rule)
        else:
            selected.append(rule)
    matched = {rule['lesson_id'] for rule in
               guards.matching_guards(memory, paths=list(targets), text=text, guards=triggered, root=root)}
    selected.extend(rule for rule in triggered if rule['lesson_id'] in matched)
    pending = guards.confirm_guards(memory, selected)
    machine_memory, opened, _ = guards._machine_memory(None)
    try:
        machine_available, machine_seen = _machine_candidates(memory, machine_memory, session_id=session_id,
                                                              targets=targets, text=text, root=root, recording=recording)
        if not pending and not machine_available:
            return {'text': '', 'rule_ids': [], 'omitted': []}
        composed = guards.compose(memory, 'assistant', paths=list(targets), text=text, rules=pending, root=root,
                                  machine=machine_memory if machine_available else False, machine_exclude=machine_seen)
    finally:
        if opened:
            machine_memory.close()
    available = {rule['lesson_id']: rule for rule in pending}
    blocks, shown, machine_shown = [], [], []
    omitted = list(composed['omitted'])
    # The rule blocks stay within the budget of the role, including the identifier each one carries.
    # The sentence that names the rules left out is a report, not rule text, and follows that budget.
    rule_limit = min(limit, guards.ROLE_BUDGETS['assistant'])
    used = 0
    for lesson_id in composed['rule_ids']:
        rule = available[lesson_id]
        matched = [target for target in targets
                   if not rule['paths'] or guards.match_path(target, rule['paths'], root=root)]
        block = (f'Project Memory lesson {lesson_id}'
                 + (' applies to ' + ', '.join(matched[:3]) if rule['paths'] and matched else '')
                 + '. ' + guards.render_rule(rule))
        if used + len(block) + 1 > rule_limit:
            omitted.append({'lesson_id': lesson_id,
                            'reason': f'This rule did not fit within the budget of {rule_limit} characters.'})
            continue
        blocks.append(block)
        shown.append((lesson_id, matched[:20]))
        used += len(block) + 1
    # The rules promoted to this machine follow the rules of the project, each marked as machine level.
    for lesson_id in composed['machine_rule_ids']:
        block = f'Project Memory machine rule {lesson_id}. ' + guards.render_machine_rule(machine_available[lesson_id])
        if used + len(block) + 1 > rule_limit:
            omitted.append({'lesson_id': lesson_id, 'level': 'machine',
                            'reason': f'This machine rule did not fit within the budget of {rule_limit} characters.'})
            continue
        blocks.append(block)
        machine_shown.append(lesson_id)
        used += len(block) + 1
    project_omitted = [entry['lesson_id'] for entry in omitted if entry.get('level') != 'machine']
    machine_omitted = [entry['lesson_id'] for entry in omitted if entry.get('level') == 'machine']
    notes = []
    if project_omitted:
        notes.append('These accepted rules also apply and were not carried here: ' + ', '.join(project_omitted)
                     + '. Read them with memory_get record.')
    if machine_omitted:
        notes.append('These machine rules also apply and were not carried here: ' + ', '.join(machine_omitted)
                     + '. Read them with memory_get machine.')
    for note in notes:
        if used + len(note) + 1 <= limit:
            blocks.append(note)
            used += len(note) + 1
    if (shown or machine_shown) and recording:
        with memory._write():
            for lesson_id, matched in shown:
                key, already = rule_shown(memory, session_id, lesson_id)
                if already:
                    continue
                receipt(memory, session_id=session_id, event_name='GuardShown', episode_id=None,
                        payload={'lesson_id': lesson_id, 'review_id': available[lesson_id]['review_id'],
                                 'targets': matched}, key=key)
            for lesson_id in machine_shown:
                key, already = rule_shown(memory, session_id, lesson_id)
                if already:
                    continue
                receipt(memory, session_id=session_id, event_name='GuardShown', episode_id=None,
                        payload={'machine_rule_id': lesson_id, 'level': 'machine'}, key=key)
    return {'text': ' '.join(blocks), 'rule_ids': [lesson_id for lesson_id, _ in shown],
            'machine_rule_ids': machine_shown, 'omitted': omitted}


def _machine_candidates(memory, machine_memory, *, session_id, targets, text, root, recording):
    """The machine rules of the assistant that this edit or prompt triggers and the session has not seen.

    The second value names the triggered machine rules the session has already
    seen, so the composition leaves them out as it leaves out the project rules
    already shown. A machine memory that cannot be read yields no rule.
    """
    if machine_memory is None:
        return {}, []
    try:
        found = guards.machine_rules(machine_memory, 'assistant', paths=list(targets), text=text, root=root)
    except guards.MACHINE_READ_ERRORS:
        return {}, []
    available = {}
    seen = []
    for rule in found:
        if recording and rule_shown(memory, session_id, rule['lesson_id'])[1]:
            seen.append(rule['lesson_id'])
        else:
            available[rule['lesson_id']] = rule
    return available, seen


def record_block(memory, *, session, turn, tool, tool_id, host, result):
    """Commit a ScopeBlocked receipt in its own write. The blocked tool call gets no PreToolUse receipt."""
    payload = {'episode_id': result['episode_id'], 'plan_id': result['plan_id'], 'tool_name': tool,
               'blocked': result['blocked'], 'allowed_patterns': result['allowed_patterns'], 'host': host}
    # A redelivered call under the same plan can name other targets; each distinct block gets its own receipt.
    key = dumps([session, turn, 'ScopeBlocked', tool_id, result['plan_id'], host, tool, result['blocked']])
    with memory._write():
        return receipt(memory, session_id=session, turn_id=turn, event_name='ScopeBlocked', tool_use_id=tool_id,
                       tool_name=tool, episode_id=result['episode_id'], payload=payload, key=key)


def receipt(memory, *, session_id, turn_id='', event_name, tool_use_id='', tool_name='',
            episode_id=None, decision_id=None, payload=None, key=None):
    _text(session_id, 'session_id', 200)
    data = [session_id, turn_id, event_name, tool_use_id, tool_name, episode_id, decision_id, payload or {}]
    rid = 'host_' + _digest(key or dumps([session_id, turn_id, event_name, tool_use_id]))[:32]
    signature = _digest(dumps(data))
    prior = memory.db.execute('SELECT signature FROM host_receipts WHERE id=?', (rid,)).fetchone()
    if prior:
        if prior[0] != signature:
            raise Conflict('Host receipt ID was redelivered with different content.')
        return rid
    memory.db.execute('INSERT INTO host_receipts VALUES (?,?,?,?,?,?,?,?,?,?,?)',
        (rid, session_id, turn_id, event_name, tool_use_id, tool_name, episode_id, decision_id, memory.now(), dumps(payload or {}), signature))
    return rid


def read_receipt(memory, rid):
    row = memory.db.execute('SELECT * FROM host_receipts WHERE id=?', (rid,)).fetchone()
    if not row:
        raise InvalidRecord('Host receipt was not found.')
    value = dict(row); value.pop('signature'); value['payload'] = json.loads(value['payload'])
    return value


def summary(value):
    """Keep hashes and measured sizes, never arbitrary tool text or user prompts."""
    encoded = dumps(value).encode('utf-8')
    result = {'sha256':hashlib.sha256(encoded).hexdigest(), 'bytes':len(encoded)}
    if isinstance(value, dict):
        for key in ('exit_code', 'returncode', 'isError'):
            if type(value.get(key)) in (int, bool): result[key] = value[key]
    elif isinstance(value, str):
        match = re.search(r'(?:Process exited with code|Exit code:)\s*(-?\d+)\b', value)
        if match: result['exit_code'] = int(match[1])
    return result


def claimed_item(memory, session):
    """The in progress work item whose current plan this session claimed, the most recent claim first, or None."""
    rows = memory.db.execute("SELECT episode_id FROM events WHERE kind='work_plan' AND json_extract(payload,'$.session_id')=? "
                             "GROUP BY episode_id ORDER BY max(seq) DESC LIMIT 20", (session,)).fetchall()
    for row in rows:
        plan = memory.db.execute("SELECT payload FROM events WHERE episode_id=? AND kind='work_plan' ORDER BY seq DESC LIMIT 1",
                                 (row[0],)).fetchone()
        current = json.loads(plan[0]) if plan else {}
        if current.get('state') == 'in_progress' and current.get('session_id') == session:
            return row[0]
    return None


def active_binding(memory, session):
    row=memory.db.execute("SELECT episode_id,decision_id FROM host_receipts WHERE session_id=? AND event_name='DecisionBound' ORDER BY rowid DESC LIMIT 1",(session,)).fetchone()
    if row and (memory.read(row['decision_id'])['replaced_by'] or
                memory.db.execute("SELECT 1 FROM events WHERE decision_id=? AND kind='outcome'",(row['decision_id'],)).fetchone()):
        return None
    return row


NOTIFICATION = re.compile(r'\s*(<task-notification>|Another Claude session sent a message:\s*<agent-message\b)')


def notification(prompt):
    """Whether Claude Code injected the prompt for a background task or a subagent rather than the user typing it."""
    return isinstance(prompt, str) and bool(NOTIFICATION.match(prompt))


def expiry_sentence(archived, shown=5):
    """One sentence that names the idle work archived at this session start."""
    if not archived:
        return ''
    titles = '; '.join(item['title'] for item in archived[:shown]) + ('; and ' + str(len(archived) - shown) + ' more' if len(archived) > shown else '')
    return (f'{len(archived)} idle work item{"s were" if len(archived) != 1 else " was"} archived: {titles}. '
            'If the user asks to restore one, revise its plan to the state named in its archived field and leave that field out.')


def session_context(memory, session, compacted=False):
    """Short factual state for a host that starts or restores a session. It reports counts and IDs, never instructions from records."""
    state = status(memory, session_id=session, limit=3, main_only=True)
    parts = [f'Memory session: {session}.']
    if compacted:
        parts.append('The conversation was compacted; retrieve earlier evidence with memory_context or memory_get before relying on it.')
    active = state['active']
    if active:
        parts.append(f'Active decision {active["decision_id"]} in episode {active["episode_id"]} (version {active["version"]}) still awaits an outcome.')
        from .planning import latest
        if latest(memory, active['episode_id'], 'work_plan'):
            parts.append(f'Use memory_get next with id {active["episode_id"]} and this session_id to recover its intent, scope and next action.')
    parts.append(f'{state["unconfirmed_total"]} tool calls need reconciliation.' if state['unconfirmed_total'] else 'No tool calls need reconciliation.')
    from .planning import phase as project_phase
    if project_phase(memory)['phase'] == 'production':
        parts.append('This project is in production: delegated work and its review still run, and only the user brings '
                     'the result into the project, in the control panel.')
    parts.append('Use memory_context before repeating research. Record a decision when choosing or revising an approach with consequences, using this session_id, evidence, uncertainty and alternatives. Routine acknowledgement needs no decision record. '
                 'Tool receipts are observations; outcomes and lesson acceptance require explicit assessment.')
    return ' '.join(parts)


def setup_context(memory, refreshed):
    from .health import inspect
    health = inspect(memory)
    text = ' Project baseline: '+health['baseline']['status']+'. Coverage remains unassessed. The live HTML viewer is available with project-memory view.'
    if refreshed and (refreshed['created'] or refreshed['more'] or any(r['status'] not in {'current_copy','review_due'} for r in refreshed['documents'])):
        text += f" Document sync captured {refreshed['created']} new versions. Use memory_get documents to inspect outstanding changes."
        if refreshed['more']: text += ' More selected files remain; continue with memory_write sync at offset '+str(refreshed['next_offset'])+'.'
    return text


def capture(memory, event, host='codex'):
    if host not in HOSTS:
        raise InvalidRecord('Unsupported host.')
    host_event = event.get('hook_event_name')
    name = CLAUDE_ALIASES.get(host_event, host_event) if host == 'claude' else host_event
    if name not in HOST_EVENTS[host]:
        raise InvalidRecord(f'Unsupported {host} hook event.')
    session = _text(event.get('session_id'), 'session_id', 200)
    # Codex numbers turns; Claude Code identifies prompts. Either keeps repeated lifecycle events distinct.
    turn = event.get('turn_id') or event.get('prompt_id') or ''
    tool = event.get('tool_name') or ''
    tool_id = event.get('tool_use_id') or ''
    if name in {'PreToolUse', 'PostToolUse'}:
        _text(tool_id, 'tool_use_id', 200); _text(tool, 'tool_name', 200)
        # Memory calls are already persisted by the adapter. Avoid recursive noise.
        if tool.startswith(('mcp__memory__','mcp__project_memory__')) or MEMORY_TOOL.match(tool): return {}
    root = project_root(memory) if name in {'PreToolUse', 'UserPromptSubmit'} else None
    if name == 'PreToolUse':
        # Checked before any receipt: a blocked tool never runs, so it must not look like an unconfirmed call.
        blocked = guards.scope_check(memory, session_id=session, event=event, project_root=root)
        if blocked:
            record_block(memory, session=session, turn=turn, tool=tool, tool_id=tool_id, host=host, result=blocked)
            raise guards.ScopeBlocked(guards.blocked_message(blocked))
    rules = None
    compacted = name == 'SessionStart' and event.get('source') == 'compact'
    action_version = None
    refreshed = None
    if name in {'SessionStart', 'Stop'}:
        from .documents import sync
        refreshed = sync(memory)
    if name == 'Stop':
        # The vault of the user is regenerated after a session that wrote records. A full run does not fit the hook
        # budget, so the refresh is detached and the hook returns without waiting for it. A failure never blocks a turn.
        from . import vault
        try:
            if vault.needs_refresh(memory):
                vault.refresh_detached(memory)
        except (OSError, ValueError, RuntimeError, sqlite3.Error):
            pass
    with memory._write():
        binding = active_binding(memory,session)
        ep, decision = (binding['episode_id'], binding['decision_id']) if binding else (None, None)
        if name == 'PostToolUse':
            pre = memory.db.execute("SELECT episode_id,decision_id FROM host_receipts WHERE session_id=? AND tool_use_id=? AND event_name='PreToolUse' AND coalesce(json_extract(payload,'$.host'),'codex')=?", (session, tool_id, host)).fetchone()
            ep, decision = (pre['episode_id'], pre['decision_id']) if pre else (None, None)
        payload = {}
        if name == 'UserPromptSubmit' and notification(event.get('prompt')):
            # The host reports a finished background task or a subagent's report. Nobody asked for anything,
            # so the prompt is recorded without entering the intent assessment.
            payload['notification'] = True
        elif name == 'UserPromptSubmit':
            from .planning import latest
            plan = latest(memory, ep, 'work_plan') if ep else None
            payload.update(coverage_version=1, plan_id=plan['id'] if plan else None)
        if refreshed is not None and (refreshed['created'] or refreshed['more'] or any(r['status'] not in {'current_copy','review_due'} for r in refreshed['documents'])):
            payload['documents'] = {'created': refreshed['created'], 'more': refreshed['more'],
                'issues': [{'id': r['id'], 'status': r['status']} for r in refreshed['documents']
                           if r['status'] not in {'current_copy', 'review_due'}]}
        for key in ('tool_input', 'tool_response', 'prompt', 'last_assistant_message', 'error'):
            if key in event and event[key] is not None: payload[key] = summary(event[key])
        for key in ('source', 'reason', 'model', 'trigger', 'agent_id', 'agent_type'):
            if key in event and isinstance(event[key], str): payload[key] = event[key][:200]
        if host != 'codex': payload['host'] = host
        if host_event != name: payload['host_event'] = host_event
        if name=='Stop':payload['stop_hook_active']=bool(event.get('stop_hook_active'))
        if name == 'PostToolUse' and host_event == 'PostToolUseFailure': payload['failed'] = True
        if name == 'PreToolUse' and read_only(tool, event.get('tool_input')): payload['read_only'] = True
        if name == 'PreToolUse' and not decision:
            # Without a bound decision, for example after a partial outcome, the call belongs to the work the session
            # claimed. It is named in the payload only: the episode_id column selects the receipts of a check snapshot.
            owned = claimed_item(memory, session)
            if owned: payload['work_item'] = owned
        key = [session,turn,name,tool_id,payload.get('source')]
        if name=='Stop':
            key.extend([payload.get('last_assistant_message',{}).get('sha256'),payload['stop_hook_active']])
        if host != 'codex': key.append(host)
        if 'documents' in payload: key.append(payload['documents'])
        # Without a turn or prompt identifier, repeated lifecycle events in one session must not collide.
        if host == 'claude' and not turn and name not in {'PreToolUse', 'PostToolUse'}: key.append(memory.now())
        prior_id='host_'+_digest(dumps(key))[:32]
        prior=memory.db.execute('SELECT id FROM host_receipts WHERE id=?',(prior_id,)).fetchone()
        if prior:
            original=read_receipt(memory,prior[0])
            observed={k:v for k,v in payload.items() if k not in {'plan_id','coverage_version'}}
            previous={k:v for k,v in original['payload'].items() if k not in {'plan_id','coverage_version'}}
            if observed!=previous:
                raise Conflict('Host event was redelivered with different observations.')
            ep,decision=original['episode_id'],original['decision_id']
            payload=original['payload']
        rid = receipt(memory, session_id=session, turn_id=turn, event_name=name, tool_use_id=tool_id,
            tool_name=tool, episode_id=ep, decision_id=decision, payload=payload, key=dumps(key))
        if name == 'PreToolUse' and decision:
            # One interpreted decision can have several mechanically captured tool calls.
            if not memory.db.execute("SELECT 1 FROM events WHERE decision_id=? AND kind='action'", (decision,)).fetchone():
                action = memory.record(ep,'action',{'action':f'Execute the recorded decision through {"Claude Code" if host == "claude" else "Codex"} tools.', 'host_reference':rid},
                    expected_version=memory.episode(ep)['version'], request_key=rid+':action', actor=host+'-hooks', decision_id=decision)
                action_version = action['version']
        if name == 'PreToolUse':
            targets = guards.relative_targets(guards.edit_targets(tool, event.get('tool_input')), root, event.get('cwd'))
            rules = assistant_rules(memory, session_id=session, targets=targets, root=root)
        if name == 'UserPromptSubmit':
            # A rule of the assistant role and a rule whose keyword the prompt uses reach the assistant here,
            # so the rules of the role are not carried by edit tool calls alone.
            rules = assistant_rules(memory, session_id=session, targets=[],
                                    text=str(event.get('prompt') or '')[:PROMPT_CHARACTERS], root=root)
    reminder = rules['text'] if rules else ''
    if action_version is not None:
        text = f'Memory action recorded. Episode {ep} is now version {action_version}; decision {decision}.'
        return {'hookSpecificOutput':{'hookEventName':host_event,'additionalContext':text+(' '+reminder if reminder else '')}}
    if reminder and name == 'PreToolUse':
        return {'hookSpecificOutput':{'hookEventName':host_event,'additionalContext':reminder}}
    scope = guards.scope_sentence(memory, session) if name in {'SessionStart', 'UserPromptSubmit'} else ''
    if name in {'SessionStart', 'UserPromptSubmit'}:
        from .templates import kickoff_hint
        hint = kickoff_hint(memory)
        if hint:
            scope = (scope + ' ' + hint).strip()
    if name == 'SessionStart':
        # Claude Code reads this at startup, resume and after automatic compaction. Codex ignores unknown output.
        started = session_context(memory, session, compacted) + setup_context(memory, refreshed) + (' ' + scope if scope else '')
        from . import planning
        try:
            archived = expiry_sentence(planning.expire(memory))
        except (OSError, ValueError, InvalidRecord, Conflict, sqlite3.Error):
            # Idle work is archived at the next session start instead.
            archived = ''
        if archived:
            started += ' ' + archived
        from . import sessions
        try:
            work = sessions.work_to_continue(memory, room=HOOK_CHARACTERS - len(started) - 1)
        except (OSError, ValueError, InvalidRecord, Conflict, sqlite3.Error):
            work = ''
        if work:
            started += ' ' + work
        try:
            earlier = sessions.start_summary(memory, session, room=HOOK_CHARACTERS - len(started) - 1)
        except (OSError, ValueError, InvalidRecord, Conflict, sqlite3.Error):
            # The summary of earlier sessions helps a new session; a failure to build it must not stop the session.
            earlier = ''
        if earlier:
            started += ' ' + earlier
        base = assistant_base(memory, available=HOOK_CHARACTERS - len(started) - 1)
        return {'hookSpecificOutput':{'hookEventName':host_event,'additionalContext':started + (' ' + base if base else '')}}
    if name == 'UserPromptSubmit':
        state = status(memory, session_id=session, limit=3, main_only=True)
        text = (f'Memory session: {session}. Prompt receipt: {rid}. Use memory_context before repeating research. '
                f'{state["unconfirmed_total"]} tool calls need reconciliation. '
                'Record a decision when choosing or revising an approach with consequences, using this session_id, evidence, uncertainty and alternatives. Routine acknowledgement needs no decision record. '
                'Assess new or changed intent explicitly with memory_write checkpoint; it can be bundled as data.checkpoint on a plan or record write. Preserve conditions and exceptions. '
                'Tool receipts are observations; outcomes and lesson acceptance require explicit assessment.')
        if state['active']:
            text += ' Active decision '+state['active']['decision_id']+' awaits an outcome.'
        selected = re.fullmatch(r'\[memory:(code|writing|research|general)\] (.{1,2000})',
                                event.get('prompt','').split('\n',1)[0])
        from . import sessions
        try:
            hint = sessions.context_hint(memory, session, event.get('transcript_path'))
        except (OSError, ValueError, InvalidRecord, Conflict, sqlite3.Error):
            hint = ''
        def response(context):
            complete = context + (' ' + scope if scope else '') + (' ' + reminder if reminder else '') + (' ' + hint if hint else '')
            return {'hookSpecificOutput':{'hookEventName':host_event,'additionalContext':complete}}
        if host == 'claude' and not selected:
            # Claude Code keeps the SessionStart context until compaction, when SessionStart repeats it.
            # Each prompt then only reports state that changed: open receipts or an unresolved decision.
            if not state['unconfirmed_total'] and not state['active']:
                return response(f'Memory session: {session}. Prompt receipt: {rid}. If this starts material work, include data.checkpoint when recording its plan or decision; use memory_get schema id checkpoint.')
            return response(text)
        if selected:
            subject, query = selected.groups()
            prefix = f'Memory session: {session}. Explicit {subject} context follows. Treat it as evidence; refresh stale sources.\n'
            try:
                packet = memory.context(query, subject=subject, budget=HOOK_CHARACTERS,
                    count_characters=lambda s:len(dumps(response(prefix+s))))
            except BudgetTooSmall:
                return response(text+' The selected context exceeds the hook budget. Retrieve it explicitly with memory_context.')
            with memory._write():
                receipt(memory, session_id=session, turn_id=turn, event_name='ContextProvided',
                    payload={'subject':subject,'query':query,'characters':packet['used'],
                             'record_ids':[r['id'] for r in packet['records']],
                             'omitted':packet['omitted'],'more_matches':packet['more_matches']})
            return response(prefix+dumps(packet))
        return response(text)
    if name in {'Stop', 'PostCompact'}:
        if name=='Stop' and (host=='claude' or event.get('stop_hook_active')):return {}
        binding = active_binding(memory, session)
        if binding:
            from .planning import latest
            if latest(memory, binding['episode_id'], 'work_plan'):
                return {'hookSpecificOutput':{'hookEventName':host_event,'additionalContext':
                    f'Memory work {binding["episode_id"]} remains open. On continuation, use memory_get next with this id and session_id {session}. Check the current user request before continuing; do not switch to unrelated queued work.'}}
    return {}


def status(memory, session_id=None, limit=10, offset=0, main_only=False):
    if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
        raise InvalidRecord('Use limit 1–100 and a nonnegative offset.')
    clause = (' AND p.session_id=?' if session_id else '') + (' AND ' + shared.MAIN_THREAD if main_only else '')
    args = [session_id] if session_id else []
    base = '''FROM host_receipts p WHERE p.event_name='PreToolUse'
      AND NOT EXISTS (SELECT 1 FROM host_receipts r WHERE r.session_id=p.session_id AND r.tool_use_id=p.tool_use_id AND r.event_name='PostToolUse' AND coalesce(json_extract(r.payload,'$.host'),'codex')=coalesce(json_extract(p.payload,'$.host'),'codex'))
      AND NOT EXISTS (SELECT 1 FROM host_receipts r WHERE r.event_name='Reconciled'
         AND json_extract(r.payload,'$.receipt_id')=p.id AND json_extract(r.payload,'$.resolution')!='unknown')''' + clause
    total = memory.db.execute('SELECT count(*) '+base,args).fetchone()[0]
    ids = memory.db.execute('SELECT p.id '+base+' ORDER BY p.rowid LIMIT ? OFFSET ?',(*args,limit,offset)).fetchall()
    recent_where=' WHERE session_id=?' if session_id else ''
    recent = memory.db.execute('SELECT id FROM host_receipts'+recent_where+' ORDER BY rowid DESC LIMIT ?',(*args,limit)).fetchall()
    binding=active_binding(memory,session_id) if session_id else None
    active=({'episode_id':binding['episode_id'],'decision_id':binding['decision_id'],'version':memory.episode(binding['episode_id'])['version']} if binding else None)
    def compact(rid):
        r=read_receipt(memory,rid)
        return {k:r[k] for k in ['id','event_name','tool_name','episode_id','decision_id','created_at']}
    return {'unconfirmed':[compact(r[0]) for r in ids], 'unconfirmed_total':total,'active':active,
            'more':offset+len(ids)<total, 'recent':[compact(r[0]) for r in recent],
            'note':'An absent result does not establish whether the operation ran. Check the real result before retrying. Every reconciliation, including unknown, requires evidence [{source_id, reason}] from inspecting the actual effect. Record that source before reconciling; an empty evidence list is rejected.'}


def bind(memory, session_id, decision_id, request_key):
    decision=memory._event(decision_id)
    if decision['kind']!='decision' or memory.read(decision_id)['replaced_by']:
        raise InvalidRecord('Bind the current decision.')
    from .planning import latest
    plan=latest(memory,decision['episode_id'],'work_plan')
    if plan and plan['state']=='in_progress' and plan.get('owner','agent')=='agent' and plan.get('session_id')!=session_id:
        raise Conflict('Another session owns this work. Claim its current plan explicitly before binding its decision.')
    return receipt(memory, session_id=session_id, event_name='DecisionBound', episode_id=decision['episode_id'],
                   decision_id=decision_id, payload={'reason':'Explicit decision recorded by the caller.'}, key=request_key+':bind')


def evidence_for(memory, receipt_ids):
    if not isinstance(receipt_ids,list) or len(receipt_ids)>20:
        raise InvalidRecord('receipt_ids must contain at most 20 IDs.')
    result=[]
    for rid in receipt_ids:
        item=read_receipt(memory,rid)
        key='codex-receipt:'+rid
        prior=memory.db.execute('SELECT id FROM sources WHERE source_key=? LIMIT 1',(key,)).fetchone()
        sid=prior[0] if prior else memory.source(key, f'Codex {item["event_name"]}',
            f'The host observed {item["tool_name"] or "a session event"} at {item["created_at"]}.', dumps(item), 'tool',
            subject=memory.episode(item['episode_id'])['subject'] if item['episode_id'] else 'general')['id']
        result.append({'source_id':sid,'reason':'Mechanical host receipt; hashes and exit status do not assess correctness.'})
    return result


def reconcile(memory, receipt_id, resolution, reason, evidence, request_key):
    item=read_receipt(memory,receipt_id)
    if item['event_name']!='PreToolUse' or resolution not in {'completed','failed','not_run','unknown'}:
        raise InvalidRecord('Reconcile a pre-tool receipt with completed, failed, not_run or unknown.')
    _text(reason,'reason',2000)
    if not evidence: raise InvalidRecord('Reconciliation requires evidence from checking the actual operation.')
    for ref in evidence: memory.source_status(ref['source_id']); _text(ref['reason'],'evidence reason',2000)
    return {'id':receipt(memory, session_id=item['session_id'], turn_id=item['turn_id'], event_name='Reconciled',
        episode_id=item['episode_id'], decision_id=item['decision_id'],
        payload={'receipt_id':receipt_id,'resolution':resolution,'reason':reason,'evidence':evidence},key=request_key)}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--db',required=True)
    parser.add_argument('--host',choices=sorted(HOSTS),default='codex')
    args=parser.parse_args()
    event = {}
    try:
        raw=sys.stdin.buffer.read(2_000_001)
        if len(raw)>2_000_000: raise InvalidRecord('Hook payload exceeds 2 MB; capture failed.')
        event=json.loads(raw)
        if not isinstance(event,dict): raise InvalidRecord('Hook payload must be an object.')
        with Memory(args.db) as memory:
            memory.db.execute('PRAGMA busy_timeout=750')
            from .capture_errors import recover
            recover(memory)
            result=capture(memory,event,host=args.host)
            from .coverage import hook as coverage_hook, inspect as coverage_state
            check=coverage_hook(memory,event)
            from .reviews import hook
            if not check and (event['hook_event_name']=='Interrupt' or event['hook_event_name'] in {'Stop','SessionStart','UserPromptSubmit'} and not coverage_state(memory,event['session_id'])['issues']):
                check=hook(memory,event,args.host)
            if check:
                if 'decision' in check: result=check
                else:
                    extra=check.get('hookSpecificOutput',{}).get('additionalContext','')
                    output=result.setdefault('hookSpecificOutput',{'hookEventName':event['hook_event_name']})
                    output['additionalContext']=output.get('additionalContext','')+' '+extra
        print(dumps(result));return 0
    except guards.ScopeBlocked as exc:
        # The block is already committed as a ScopeBlocked receipt. It is not a capture failure.
        print(str(exc), file=sys.stderr)
        return 2
    except (OSError,ValueError,TypeError,KeyError,sqlite3.Error,InvalidRecord,Conflict) as exc:
        # A sidecar remains observable when SQLite itself cannot accept a receipt.
        try:
            from .capture_errors import record
            record(args.db,event,exc)
        except OSError:
            pass  # stderr remains the host-visible failure channel if the directory is unwritable.
        # Exit 2 blocks supported pre-tool calls and reports post-capture failures.
        print(f'Memory capture failed ({type(exc).__name__}). Inspect the database and host result before retrying.',file=sys.stderr)
        return 2


if __name__=='__main__': raise SystemExit(main())
