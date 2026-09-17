"""Typed human actions from the control panel over the same append-only records used by MCP.

Every action records the actor workspace-user. Actions that change records cite a
user source that states what the user saved. Each request key is stored with a
signature, so a repeated request returns the original result and a reused key
with different content raises Conflict.

Agent run actions (delegate, merge, discard, review, cancel_run,
request_work_review, focus_check and focus_start) run outside the action transaction, because they start
processes or run git. Their own request keys make them idempotent. The hive actions (hive_post,
hive_close and hive_purge) also run outside it, because the hive is a second database beside the project.
"""
from . import codex_host
from .core import InvalidRecord, Conflict, USER_ACTOR as USER, dumps, _text, _digest
from .mcp import needs_done_check, with_done_check, write
from .planning import latest
from .shared import prior_result, run_summary, store_result

KEY_REUSED = 'This action key was already used for different changes.'
RECORD_OPERATIONS = ('plan', 'sprint', 'comment', 'requirements', 'lesson_review', 'allow_paths', 'link', 'component',
                     'answer_kickoff', 'instructions', 'phase', 'reassess', 'session_flag', 'session_proposal')
RUN_OPERATIONS = ('delegate', 'merge', 'discard', 'review', 'cancel_run', 'request_work_review', 'focus_check', 'focus_start',
                  'hive_post', 'hive_close', 'hive_purge', 'reconcile', 'reconcile_read_only')
# Promotion actions write to the machine memory, which is a second database, so they run outside the
# action transaction of this project, as the agent run actions do.
MACHINE_OPERATIONS = ('promotion', 'machine_rule')
OPERATIONS = RECORD_OPERATIONS + RUN_OPERATIONS + MACHINE_OPERATIONS
FIELDS = {
    'plan': (set(), {'episode_id', 'expected_version', 'title', 'objective', 'criterion', 'subject', 'payload'}),
    'sprint': (set(), {'episode_id', 'expected_version', 'title', 'objective', 'criterion', 'subject', 'payload'}),
    'comment': ({'episode_id', 'expected_version', 'text'}, set()),
    'requirements': ({'requirements', 'reason', 'expected_version'}, set()),
    'lesson_review': ({'lesson_id', 'expected_version', 'status', 'reason'}, {'paths', 'keywords', 'failure_type', 'roles'}),
    'allow_paths': ({'episode_id', 'expected_version', 'paths', 'reason'}, set()),
    'link': ({'from_id', 'to_id', 'type', 'reason'}, {'retire'}),
    'component': ({'title', 'kind', 'description', 'status'}, {'component_id', 'path'}),
    'answer_kickoff': ({'question_ids', 'text'}, {'episode_id'}),
    'instructions': ({'role', 'text'}, {'reason', 'actor'}),
    'phase': ({'phase', 'reason'}, set()),
    'reassess': ({'decision_id', 'assessment', 'reason'}, {'outcome_id'}),
    'session_flag': ({'flag_id', 'status', 'reason'}, set()),
    'session_proposal': ({'proposal_id', 'status', 'reason'}, {'episode_id', 'expected_version'}),
    'reconcile': ({'receipt_id', 'resolution', 'reason'}, set()),
    'reconcile_read_only': ({'reason'}, set()),
    'delegate': ({'episode_id'}, {'host', 'max_seconds'}),
    'merge': ({'run_id'}, {'override_reason'}),
    'discard': ({'run_id', 'reason'}, set()),
    'review': ({'episode_id', 'role'}, {'max_seconds', 'retry'}),
    'cancel_run': ({'run_id'}, set()),
    'request_work_review': ({'run_id'}, {'max_seconds'}),
    'focus_check': ({'episode_id', 'command', 'timeout_seconds'}, set()),
    'focus_start': ({'episode_id'}, set()),
    'hive_post': ({'swarm_id', 'move', 'claim'}, {'detail', 'target', 'addressee', 'bases', 'reply_to'}),
    'hive_close': ({'swarm_id', 'summary'}, set()),
    'hive_purge': ({'closed_before_days'}, set()),
    'promotion': ({'promotion_id', 'status', 'reason'}, {'rule', 'basis'}),
    'machine_rule': ({'rule_id', 'status', 'reason'}, set()),
}
MESSAGES = {
    'comment': 'Select a work item, its current version and the comment text.',
    'requirements': 'Provide the complete requirements, reason and current version.',
    'lesson_review': 'Select a lesson, status, reason and current work item version. Triggers and roles are optional.',
    'allow_paths': 'Select a work item, its current version, the paths to allow and the reason.',
    'link': 'Provide the two record ids, the link type and the reason.',
    'component': 'Provide the component title, kind, description and status. The id and path are optional.',
    'answer_kickoff': 'Select the kickoff questions and write the answer.',
    'instructions': 'Select the role and write the base instructions. The reason is optional.',
    'phase': 'Select the phase of the project and write the reason for the change.',
    'reassess': 'Select the decision, the new assessment and the reason. The outcome you reviewed is optional.',
    'session_flag': 'Select the flagged message, confirm or dismiss it, and give the reason.',
    'session_proposal': 'Select the proposal, accept or reject it, and give the reason. Acceptance needs the work item and its current version.',
    'reconcile': 'Select the tool call, its resolution and the reason.',
    'reconcile_read_only': 'Write the reason for resolving the read-only calls.',
    'delegate': 'Select the work item to delegate. The host and time limit are optional.',
    'merge': 'Select the delegated run to merge. An override reason is optional.',
    'discard': 'Select the delegated run to discard and give the reason.',
    'review': 'Select the work item and the check role.',
    'cancel_run': 'Select the agent run to cancel.',
    'request_work_review': 'Select the delegated work run to review again.',
    'focus_check': 'Select the work item and write the check command as separate arguments, with its timeout in seconds.',
    'focus_start': 'Select the work item whose focused problem should start.',
    'hive_post': 'Select the swarm and the move, and write the claim. A question needs its addressee, an answer its target '
                 'question and an observation at least one basis.',
    'hive_close': 'Select the swarm to close and write its summary.',
    'hive_purge': 'Give the number of days since a swarm closed. Older closed swarms are removed.',
    'promotion': 'Select the proposed promotion, accept or decline it, and give the reason.',
    'machine_rule': 'Select the machine rule, set the status to retired and give the reason.',
}
PLAN_SENTENCES = {'objective': 'The intended result is', 'criterion': 'Completion requires', 'scope': 'The scope is',
                  'next_action': 'The next action is', 'reason': 'The reason is', 'state': 'The selected work state is',
                  'owner': 'The responsible party is', 'priority': 'The priority is', 'autonomy': 'The recorded autonomy is',
                  'item_type': 'The work item type is', 'starts_on': 'The sprint starts on', 'ends_on': 'The sprint ends on',
                  'status': 'The sprint status is', 'text': 'The comment says'}
# Changing these fields changes what the user authorised, so the plan cites a new user source.
GOVERNED = {'plan': ('scope', 'autonomy', 'depends_on', 'paths', 'acceptance'), 'sprint': ('starts_on', 'ends_on')}
LIST_FIELDS = ('depends_on', 'paths', 'acceptance')


def action(memory, operation, data, request_key):
    """Apply one human action and return its result."""
    if operation not in OPERATIONS:
        raise InvalidRecord('Unknown workspace action. Use one of: ' + ', '.join(OPERATIONS) + '.')
    if not isinstance(data, dict):
        raise InvalidRecord('Action data must be an object.')
    _text(request_key, 'request_key', 180)
    required, optional = FIELDS[operation]
    if required - data.keys() or data.keys() - required - optional:
        raise InvalidRecord(MESSAGES.get(operation, 'The workspace action contains missing or unsupported fields.'),
                            required=sorted(required), optional=sorted(optional))
    if not codex_host.exists(memory):
        codex_host.initialize(memory)
    signature = _digest(dumps([operation, data]))
    if operation in RUN_OPERATIONS or operation in MACHINE_OPERATIONS:
        return _run_action(memory, operation, data, request_key, signature)
    try:
        with memory._write():
            prior = prior_result(memory, request_key, signature, KEY_REUSED)
            if prior is not None:
                return prior
            result = RECORD_HANDLERS[operation](memory, data, request_key)
            store_result(memory, request_key, signature, result)
        return result
    except InvalidRecord as exc:
        # The rejected Done transition is rolled back before the missing outcome check is requested.
        if operation == 'plan' and needs_done_check(exc) and data.get('episode_id'):
            raise with_done_check(memory, exc, data['episode_id']) from exc
        raise


def _user_source(memory, request_key, title, sentences, subject='general'):
    source = memory.source('workspace:' + request_key, title, sentences[0], '\n'.join(sentences), 'user', subject=subject)
    return [{'source_id': source['id'], 'reason': 'The user submits this change through the local control panel.'}]


def _version(memory, episode_id, expected):
    episode = memory.episode(episode_id)
    if expected != episode['version']:
        raise Conflict('This work item changed while you were editing. Your draft is retained; reload its current version before saving.')
    return episode


# Record actions.

def _plan(memory, data, request_key, operation):
    if 'payload' not in data or not isinstance(data['payload'], dict):
        raise InvalidRecord('Provide the plan fields in payload.')
    data = dict(data)
    episode_id = data.get('episode_id')
    episode = _version(memory, episode_id, data.get('expected_version')) if episode_id else None
    title = episode['title'] if episode else data.get('title', 'Work')
    kind = 'work_plan' if operation == 'plan' else 'sprint'
    old = latest(memory, episode_id, kind) if episode_id else None
    payload = data['payload']
    if operation == 'plan' and old and old.get('focus') and 'focus' not in payload:
        # The plan form does not edit the focused problem, so a saved plan keeps the focus block and its check.
        payload = data['payload'] = {**payload, 'focus': old['focus']}
    if (operation == 'plan' and payload.get('owner', 'agent') == 'agent' and payload.get('state') == 'in_progress'
            and (not old or old.get('state') != 'in_progress' or payload.get('session_id') != old.get('session_id'))):
        raise InvalidRecord('Only an active agent session can claim work in progress. Select Ready to queue agent work.')
    evidence = None
    if old and all(_governed(old, key) == _governed(payload, key) for key in GOVERNED[operation]):
        # Keep the original intent evidence when the user changes only scheduling or progress.
        evidence = [dict(item) for item in memory.db.execute('SELECT source_id,reason FROM dependencies WHERE event_id=?', (old['id'],))]
    if not evidence:
        sentences = _plan_sentences(memory, operation, title, data)
        subject = data.get('subject') or (episode['subject'] if episode else 'general')
        evidence = _user_source(memory, request_key, operation.capitalize() + ' for ' + title, sentences, subject)
    return write(memory, operation, request_key + ':record', {**data, 'actor': USER, 'evidence': evidence},
                 session_id=payload.get('session_id'))


def _governed(plan, key):
    return plan.get(key) or [] if key in LIST_FIELDS else plan.get(key)


def _plan_sentences(memory, operation, title, data):
    sentences = ['The user saves this ' + ('work item' if operation == 'plan' else operation) + ' for ' + title + '.']
    values = {**data, **data['payload']}
    for key, label in PLAN_SENTENCES.items():
        if isinstance(values.get(key), str):
            sentences.append(label + ': ' + values[key])
    for ref in values.get('depends_on') or []:
        if isinstance(ref, dict) and isinstance(ref.get('episode_id'), str):
            sentences.append('This work depends on ' + memory.episode(ref['episode_id'])['title'] + ' because ' + str(ref.get('reason')))
    if isinstance(values.get('parent_id'), str):
        sentences.append('This work item belongs to ' + memory.episode(values['parent_id'])['title'] + '.')
    if isinstance(values.get('sprint_id'), str):
        sentences.append('The assigned sprint is ' + memory.episode(values['sprint_id'])['title'] + '.')
    for criterion in values.get('acceptance') or []:
        sentences.append('Acceptance criterion: ' + str(criterion))
    if values.get('paths'):
        sentences.append('The allowed paths are: ' + ', '.join(str(path) for path in values['paths']) + '.')
    return sentences


def plan(memory, data, request_key):
    return _plan(memory, data, request_key, 'plan')


def sprint(memory, data, request_key):
    return _plan(memory, data, request_key, 'sprint')


def comment(memory, data, request_key):
    episode = _version(memory, data['episode_id'], data['expected_version'])
    evidence = _user_source(memory, request_key, 'Comment for ' + episode['title'],
                            ['The user comments on ' + episode['title'] + '.', 'The comment says: ' + str(data['text'])],
                            episode['subject'])
    return memory.record(episode['id'], 'note', {'text': data['text']}, expected_version=data['expected_version'],
                         actor=USER, evidence=evidence, request_key=request_key + ':record')


def requirements(memory, data, request_key):
    source = memory.source('workspace:' + request_key, 'The user approves project requirements', data['reason'], dumps(data), 'user')
    return memory.approve_requirements(**data, actor=USER, request_key=request_key + ':approval',
                                       evidence=[{'source_id': source['id'], 'reason': 'The user explicitly approves this complete requirement revision.'}])


def phase(memory, data, request_key):
    """Record the phase of the project. The phase decides who merges delegated work."""
    from .planning import set_phase
    from . import machine
    result = set_phase(memory, phase=data['phase'], reason=data['reason'], actor=USER)
    # The registry of the machine memory records the phase of each project, so it follows the change.
    result['machine_registry'] = machine.refresh_phase(memory)
    return result


def lesson_review(memory, data, request_key):
    lesson = memory.read(data['lesson_id'])
    if lesson['kind'] != 'lesson':
        raise InvalidRecord('Select a lesson to review.')
    triggers = {key: data[key] for key in ('paths', 'keywords', 'failure_type', 'roles') if key in data}
    if triggers and data['status'] != 'accepted':
        raise InvalidRecord('Triggers and roles apply only when you accept a lesson. Remove the paths, keywords, failure type and roles, or accept the lesson.')
    source = memory.source('workspace:' + request_key, 'The user reviews a proposed lesson', data['reason'], dumps(data), 'user',
                           subject=lesson['subject'])
    payload = {key: data[key] for key in ('lesson_id', 'status', 'reason')}
    payload.update(triggers)
    return memory.record(lesson['episode_id'], 'lesson_review', payload, expected_version=data['expected_version'],
                         actor=USER, request_key=request_key + ':review',
                         evidence=[{'source_id': source['id'], 'reason': 'The user explicitly reviews this lesson.'}],
                         links=[{'event_id': lesson['id'], 'reason': data['reason']}])


def _too_broad(pattern):
    """True when a pattern allows the whole machine or the whole project, such as /, /**, **, **/* or a single dot."""
    from .guards import normalize
    text = normalize(pattern)
    segments = [segment for segment in text.split('/') if segment]
    return text in ('.', '/') or bool(segments) and set(segments) <= {'*', '**'} and '**' in segments


def allow_paths(memory, data, request_key):
    """Append a plan revision that adds path patterns to the allowed paths of a work item."""
    from .guards import validate_patterns
    episode = _version(memory, data['episode_id'], data['expected_version'])
    old = latest(memory, episode['id'], 'work_plan')
    if not old:
        raise InvalidRecord('Record a plan for this work item before allowing paths.')
    requested = data['paths']
    if isinstance(requested, list) and all(isinstance(path, str) for path in requested):
        requested = list(dict.fromkeys(requested))
    validate_patterns(requested)
    broad = [path for path in requested if _too_broad(path)]
    if broad:
        raise InvalidRecord('These paths would allow changes anywhere in the project or on the computer: ' + ', '.join(broad) +
                            '. Allow the folders or files that the work needs instead.', paths=broad)
    _text(data['reason'], 'reason', 2000)
    current = list(old.get('paths') or [])
    added = [path for path in requested if path not in current]
    if not added:
        raise InvalidRecord('These paths are already allowed for this work item.', paths=current)
    record = memory.read(old['id'])
    sentences = ['The user allows more paths for ' + episode['title'] + '.',
                 'The added paths are: ' + ', '.join(added) + '.', 'The reason is: ' + data['reason']]
    evidence = _user_source(memory, request_key, 'Allowed paths for ' + episode['title'], sentences, episode['subject'])
    for ref in record['evidence']:
        if len(evidence) < 20 and ref['source_id'] not in {item['source_id'] for item in evidence}:
            evidence.append({'source_id': ref['source_id'], 'reason': ref['reason']})
    payload = {**record['payload'], 'paths': current + added, 'reason': data['reason']}
    result = write(memory, 'plan', request_key + ':record',
                   {'episode_id': episode['id'], 'expected_version': episode['version'], 'payload': payload, 'actor': USER,
                    'evidence': evidence, 'links': record['links']},
                   session_id=payload.get('session_id'))
    return {**result, 'added': added, 'paths': current + added}


def link(memory, data, request_key):
    from . import graph
    return graph.link(memory, from_id=data['from_id'], to_id=data['to_id'], type=data['type'], reason=data['reason'],
                      actor=USER, request_key=request_key + ':link', retire=data.get('retire'))


def component(memory, data, request_key):
    from .architecture import save_component
    title = data['title'] if isinstance(data['title'], str) else 'Component'
    sentences = ['The user describes the component ' + title + '.',
                 'The kind is: ' + str(data['kind']) + '.', 'The status is: ' + str(data['status']) + '.',
                 'The description is: ' + str(data['description'])]
    if data.get('path'):
        sentences.append('The component describes the project path ' + str(data['path']) + '.')
    evidence = _user_source(memory, request_key, 'Component ' + title, sentences)
    # An omitted path keeps the stored one, so a form that sends only the changed fields does not clear it.
    optional = {'path': data['path']} if 'path' in data else {}
    return save_component(memory, component_id=data.get('component_id'), title=data['title'], kind=data['kind'],
                          description=data['description'], status=data['status'], actor=USER,
                          request_key=request_key + ':component', evidence=evidence, **optional)


def answer_kickoff(memory, data, request_key):
    from .templates import answer_kickoff as record_answer
    _text(data['text'], 'text', 12000)
    ids = data['question_ids']
    sentences = ['The user answers kickoff questions.',
                 'The answered questions are: ' + ', '.join(str(item) for item in ids) + '.' if isinstance(ids, list) else 'The answered questions are listed.',
                 'The answer says: ' + data['text']]
    evidence = _user_source(memory, request_key, 'Kickoff answers', sentences)
    return record_answer(memory, question_ids=ids, text=data['text'], actor=USER, request_key=request_key + ':answer',
                         evidence=evidence, episode_id=data.get('episode_id'))


def instructions(memory, data, request_key):
    """Save a new version of the base instruction text of one role.

    The base text is a source, so every earlier version stays and activating an
    earlier text is a new version with that text. Only the user writes it.
    """
    from . import guards
    actor = data.get('actor', USER)
    if actor != USER:
        raise InvalidRecord('Saving the base instructions of a role is a user action. The recorded actor is ' + USER +
                            ', so another actor name is not accepted.', actor=USER)
    role = data['role']
    if role not in guards.RULE_ROLES:
        raise InvalidRecord('Select the role to save. Use one of: ' + ', '.join(guards.RULE_ROLES) + '.',
                            roles=list(guards.RULE_ROLES))
    body = data['text']
    # Rejects a value that is not text, is empty or is only spaces.
    _text(body, 'text', 100000)
    reason = data.get('reason') or 'The user saves the base instructions of the ' + role + ' role.'
    _text(reason, 'reason', 2000)
    current = guards.base_text(memory, role)
    if current['source_id'] and current['text'] == body.strip():
        raise InvalidRecord('The saved base instructions of this role already have this text.',
                            version=current['version'])
    source = memory.source(guards.base_source_key(role), 'Base instructions for the ' + role + ' role', reason,
                           body, 'user', internal=True)
    composed = guards.instructions(memory, role)
    return {'role': role, 'source': guards.base_source_key(role), 'source_id': source['id'], 'version': source['version'],
            'actor': USER, 'characters': composed['characters'], 'base_characters': len(composed['base']),
            'rule_ids': composed['rule_ids'], 'omitted': composed['omitted'], 'budget': composed['budget'],
            'used': composed['used'], 'text': composed['text']}


# The user may judge an outcome good, bad or unknown. Pending is the state of an outcome that nobody has judged yet.
REASSESSMENTS = ('good', 'bad', 'unknown')


def reassess(memory, data, request_key):
    """Record a new outcome of a decision as the user for one outcome of that decision.

    The new outcome supersedes the latest outcome of the decision, as every outcome does, and links to
    the outcome the user reassesses: `outcome_id`, or without it the latest counted failure of the decision,
    or when none is counted the latest outcome that is not itself a reassessment. guards.COUNTED_FAILURE reads that link, so a good or unknown assessment removes exactly
    that outcome from the recurrence count, and a bad assessment keeps it counted where it was, with its
    failure type and severity. This is the only way a failure leaves the count.
    """
    from . import guards
    if data.get('actor', USER) != USER:
        raise InvalidRecord('Reassessing an outcome is a user action. The recorded actor is ' + USER +
                            ', so another actor name is not accepted.', actor=USER)
    decision_id = _text(data['decision_id'], 'decision_id', 200)
    decision = memory._event(decision_id)
    if decision['kind'] != 'decision':
        raise InvalidRecord('Select a decision to reassess. The selected record is not a decision.')
    assessment = data['assessment']
    if assessment not in REASSESSMENTS:
        raise InvalidRecord('Select the new assessment. Use one of: ' + ', '.join(REASSESSMENTS) + '.',
                            assessments=list(REASSESSMENTS))
    reason = _text(data['reason'], 'reason', 2000)
    outcomes = [row['id'] for row in memory.db.execute(
        "SELECT id FROM events WHERE decision_id=? AND kind='outcome' ORDER BY seq", (decision_id,))]
    if not outcomes:
        raise InvalidRecord('This decision has no recorded outcome, so there is nothing to reassess.')
    earlier = {row['id'] for row in memory.db.execute(
        "SELECT o.id FROM events o WHERE o.decision_id=? AND o.kind='outcome' AND o.actor=? "
        f"AND EXISTS ({guards.REASSESSMENT_LINK.format(outcome='o')})", (decision_id, USER))}
    if 'outcome_id' in data:
        target = _text(data['outcome_id'], 'outcome_id', 200)
        if target not in outcomes:
            raise InvalidRecord('Select an outcome of this decision to reassess.')
        if target in earlier:
            raise InvalidRecord('This outcome is an earlier reassessment. Reassess the outcome that it assessed instead.')
    else:
        counted = [row['id'] for row in memory.db.execute(
            f"SELECT o.id FROM events o WHERE o.decision_id=? AND {guards.COUNTED_FAILURE} ORDER BY o.seq", (decision_id,))]
        target = (counted or [identifier for identifier in outcomes if identifier not in earlier])[-1]
    previous = memory._event(target)['payload']
    episode = memory.episode(decision['episode_id'])
    # A bad assessment keeps the recorded severity of a failure. An earlier outcome without a failure has no severity to keep.
    if assessment == 'good':
        severity = 'none'
    elif assessment == 'bad' and previous['severity'] in ('minor', 'major'):
        severity = previous['severity']
    else:
        severity = 'unknown'
    payload = {'observed': 'The user reassesses an outcome of this decision in the control panel.',
               'assessment': assessment, 'assessment_reason': reason, 'severity': severity,
               'attribution': previous['attribution']}
    if assessment == 'bad' and previous.get('failure_type'):
        payload['failure_type'] = previous['failure_type']
    sentences = ['The user reassesses the outcome of this decision: ' + str(decision['payload'].get('decision', decision_id)),
                 'The new assessment is: ' + assessment + '.', 'The reason is: ' + reason]
    evidence = _user_source(memory, request_key, 'Reassessment for ' + episode['title'], sentences, episode['subject'])
    result = memory.record(episode['id'], 'outcome', payload, expected_version=episode['version'], actor=USER,
                           evidence=evidence, decision_id=decision_id, supersedes=outcomes[-1],
                           links=[{'event_id': target, 'reason': 'The user reassesses this outcome.'}],
                           request_key=request_key + ':outcome')
    return {**result, 'decision_id': decision_id, 'episode_id': episode['id'], 'supersedes': outcomes[-1],
            'reassessed': target, 'assessment': assessment}


def session_flag(memory, data, request_key):
    """Confirm or dismiss a possible unrecorded direction. Both are kept, so the precision of the flags can be measured."""
    from . import sessions
    return sessions.decide_flag(memory, data['flag_id'], data['status'], data['reason'], actor=USER)


def session_proposal(memory, data, request_key):
    """Accept a distilled proposal into a work item, or reject it. Only this action writes a proposal as a record."""
    from . import sessions
    return sessions.decide_proposal(memory, data['proposal_id'], data['status'], data['reason'], actor=USER,
                                    episode_id=data.get('episode_id'), expected_version=data.get('expected_version'))


RECORD_HANDLERS = {'plan': plan, 'sprint': sprint, 'comment': comment, 'requirements': requirements,
                   'lesson_review': lesson_review, 'allow_paths': allow_paths, 'link': link, 'component': component,
                   'answer_kickoff': answer_kickoff, 'instructions': instructions, 'phase': phase, 'reassess': reassess,
                   'session_flag': session_flag, 'session_proposal': session_proposal}


# Agent run actions.

def _run_action(memory, operation, data, request_key, signature):
    prior = prior_result(memory, request_key, signature, KEY_REUSED)
    result = RUN_HANDLERS[operation](memory, data, request_key, first=prior is None)
    with memory._write():
        store_result(memory, request_key, signature, result, keep_existing=True)
    return result


def _launch(memory, run, first):
    """Start a queued run once. A repeated request returns the existing run without a second worker."""
    from . import reviews
    if first and run['state'] == 'queued':
        reviews.launch(memory, run)
    return run


def delegate(memory, data, request_key, first):
    from . import delegation
    arguments = {'request_key': request_key + ':delegate', 'host': data.get('host')}
    if 'max_seconds' in data:
        arguments['max_seconds'] = data['max_seconds']
    run = delegation.request_work(memory, data['episode_id'], **arguments)
    if first and run['state'] == 'queued':
        delegation.launch(memory, run)
    return run_summary(memory, run)


def merge(memory, data, request_key, first):
    from . import delegation
    return delegation.merge(memory, data['run_id'], request_key=request_key + ':merge', actor=USER,
                            override_reason=data.get('override_reason'))


def discard(memory, data, request_key, first):
    from . import delegation
    return delegation.discard(memory, data['run_id'], request_key=request_key + ':discard', actor=USER, reason=data['reason'])


def review(memory, data, request_key, first):
    from . import reviews
    if data['role'] not in reviews.ROLES:
        raise InvalidRecord('The check role must be outcome, intent or recovery.')
    arguments = {'request_key': request_key + ':review', 'retry': data.get('retry', False)}
    if 'max_seconds' in data:
        arguments['max_seconds'] = data['max_seconds']
    run = reviews.request(memory, data['episode_id'], data['role'], **arguments)
    return run_summary(memory, _launch(memory, run, first))


def cancel_run(memory, data, request_key, first):
    from . import focus, reviews
    run = reviews.cancel(memory, data['run_id'])
    if run['role'] == 'work' and (run['snapshot'] or {}).get('focus') and run['state'] == 'cancelled':
        # A focused attempt cancelled while queued never runs, so its start is settled here.
        focus.settle(memory, run['episode_id'])
    return run_summary(memory, run)


def focus_check(memory, data, request_key, first):
    """Set the check of a focused problem. Only the user sets it, because the check runs a command on this computer."""
    from . import focus
    return focus.set_check(memory, data['episode_id'], command=data['command'], timeout_seconds=data['timeout_seconds'],
                           request_key=request_key + ':focus-check', actor=USER)


def focus_start(memory, data, request_key, first):
    """Start the attempts of a focused problem."""
    from . import focus
    return focus.start(memory, data['episode_id'], request_key=request_key + ':focus-start', actor=USER)


# Hive actions. The user takes part in a swarm as workspace-user and decides when a swarm closes or is removed.

HIVE_MISSING = 'This project has no hive yet, so there is no swarm to change. A swarm opens when agents start to work together.'


def _hive(memory):
    from . import hive
    path = hive.path_for(memory)
    if not path.exists():
        raise InvalidRecord(HIVE_MISSING)
    return hive.Hive(path)


def hive_post(memory, data, request_key, first):
    """Post a question, an answer or an observation to a swarm as the user. Agents receive it in their hive context."""
    from . import hive
    from .guards import project_root
    if data['move'] not in hive.USER_MOVES:
        raise InvalidRecord('The user posts a question, an answer or an observation in a swarm.', moves=list(hive.USER_MOVES))
    fields = {name: data[name] for name in ('claim', 'detail', 'target', 'addressee', 'bases', 'reply_to')
              if data.get(name) not in (None, '', [])}
    with _hive(memory) as store:
        # The user joins once per swarm. A file basis is checked against the files of this project.
        hive.join(store, data['swarm_id'], agent_id=USER, role='user', host=USER, worktree=project_root(memory))
        result = hive.log(store, data['swarm_id'], USER, move=data['move'], request_key=request_key + ':hive-post',
                          fields=fields, memory=memory)
    return {**result, 'swarm_id': data['swarm_id'], 'agent_id': USER}


def hive_close(memory, data, request_key, first):
    """Close a swarm and distill it: conclusions are marked, lessons are proposed and a summary note is recorded."""
    from . import hive
    with _hive(memory) as store:
        return hive.close(store, data['swarm_id'], summary=data['summary'], request_key=request_key + ':hive-close', memory=memory)


def hive_purge(memory, data, request_key, first):
    """Remove whole swarms that closed at least the given number of days ago. The main memory keeps a receipt with counts."""
    from . import hive
    path = hive.path_for(memory)
    with hive.Hive(path, read_only=not path.exists()) as store:
        return hive.purge(store, memory, closed_before_days=data['closed_before_days'], actor=USER)


def request_work_review(memory, data, request_key, first):
    """Request a new work review for a completed delegated run whose review failed, stopped or never started."""
    from . import delegation
    arguments = {'request_key': request_key + ':work-review'}
    if 'max_seconds' in data:
        arguments['max_seconds'] = data['max_seconds']
    follow = delegation.retry_review(memory, data['run_id'], **arguments)
    return run_summary(memory, _launch(memory, follow, first))


# Promotion actions. The user decides a proposal in the panel; only this path writes to the machine memory.

def promotion(memory, data, request_key, first):
    """Accept or decline a proposed promotion of a rule to the machine memory.

    Acceptance writes the rule into the machine memory as workspace-user and
    records the acceptance in this project by the identifier of that rule.
    """
    from . import machine
    status = data['status']
    if status == 'accepted':
        return machine.accept(memory, data['promotion_id'], actor=USER, reason=data['reason'],
                              changes=data.get('rule'), basis=data.get('basis'))
    if status == 'declined':
        if data.get('rule') or data.get('basis'):
            raise InvalidRecord('Change the text of a proposed rule only when you accept it.')
        return machine.decline(memory, data['promotion_id'], actor=USER, reason=data['reason'])
    raise InvalidRecord('Accept or decline the proposed promotion.', statuses=['accepted', 'declined'])


def machine_rule(memory, data, request_key, first):
    """Retire a rule of the machine memory. The rule and its history stay readable."""
    from . import machine
    if data['status'] != 'retired':
        raise InvalidRecord('The user retires a machine rule. Set the status to retired.', statuses=['retired'])
    return machine.retire(memory, data['rule_id'], actor=USER, reason=data['reason'])


# Reconciliation actions read transcripts, so they run outside the action transaction.

def reconcile(memory, data, request_key, first):
    """Resolve one unconfirmed tool call as the user, with its transcript entry as evidence when one exists."""
    from . import sessions
    return sessions.reconcile(memory, data['receipt_id'], data['resolution'], data['reason'], request_key, actor=USER)


def reconcile_read_only(memory, data, request_key, first):
    """Resolve every unconfirmed read-only call whose transcript shows its result."""
    from . import sessions
    return sessions.reconcile_read_only(memory, data['reason'], request_key, actor=USER)


RUN_HANDLERS = {'delegate': delegate, 'merge': merge, 'discard': discard, 'review': review, 'cancel_run': cancel_run,
                'request_work_review': request_work_review, 'promotion': promotion, 'machine_rule': machine_rule,
                'focus_check': focus_check, 'focus_start': focus_start, 'hive_post': hive_post, 'hive_close': hive_close,
                'hive_purge': hive_purge, 'reconcile': reconcile, 'reconcile_read_only': reconcile_read_only}
