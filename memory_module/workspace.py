"""Typed human actions from the control panel over the same append-only records used by MCP.

Every action records the actor workspace-user. Actions that change records cite a
user source that states what the user saved. Each request key is stored with a
signature, so a repeated request returns the original result and a reused key
with different content raises Conflict.

Agent run actions (delegate, merge, discard, review, cancel_run and
request_work_review) run outside the action transaction, because they start
processes or run git. Their own request keys make them idempotent.
"""
from . import codex_host
from .core import InvalidRecord, Conflict, USER_ACTOR as USER, dumps, _text, _digest
from .mcp import needs_done_check, prior_result, with_done_check, write
from .planning import latest

KEY_REUSED = 'This action key was already used for different changes.'
RECORD_OPERATIONS = ('plan', 'sprint', 'comment', 'requirements', 'lesson_review', 'allow_paths', 'link', 'component',
                     'answer_kickoff')
RUN_OPERATIONS = ('delegate', 'merge', 'discard', 'review', 'cancel_run', 'request_work_review')
OPERATIONS = RECORD_OPERATIONS + RUN_OPERATIONS
FIELDS = {
    'plan': (set(), {'episode_id', 'expected_version', 'title', 'objective', 'criterion', 'subject', 'payload'}),
    'sprint': (set(), {'episode_id', 'expected_version', 'title', 'objective', 'criterion', 'subject', 'payload'}),
    'comment': ({'episode_id', 'expected_version', 'text'}, set()),
    'requirements': ({'requirements', 'reason', 'expected_version'}, set()),
    'lesson_review': ({'lesson_id', 'expected_version', 'status', 'reason'}, {'paths', 'keywords', 'failure_type'}),
    'allow_paths': ({'episode_id', 'expected_version', 'paths', 'reason'}, set()),
    'link': ({'from_id', 'to_id', 'type', 'reason'}, {'retire'}),
    'component': ({'title', 'kind', 'description', 'status'}, {'component_id', 'path'}),
    'answer_kickoff': ({'question_ids', 'text'}, {'episode_id'}),
    'delegate': ({'episode_id'}, {'host', 'max_seconds'}),
    'merge': ({'run_id'}, {'override_reason'}),
    'discard': ({'run_id', 'reason'}, set()),
    'review': ({'episode_id', 'role'}, {'max_seconds', 'retry'}),
    'cancel_run': ({'run_id'}, set()),
    'request_work_review': ({'run_id'}, {'max_seconds'}),
}
MESSAGES = {
    'comment': 'Select a work item, its current version and the comment text.',
    'requirements': 'Provide the complete requirements, reason and current version.',
    'lesson_review': 'Select a lesson, status, reason and current work item version. Triggers are optional.',
    'allow_paths': 'Select a work item, its current version, the paths to allow and the reason.',
    'link': 'Provide the two record ids, the link type and the reason.',
    'component': 'Provide the component title, kind, description and status. The id and path are optional.',
    'answer_kickoff': 'Select the kickoff questions and write the answer.',
    'delegate': 'Select the work item to delegate. The host and time limit are optional.',
    'merge': 'Select the delegated run to merge. An override reason is optional.',
    'discard': 'Select the delegated run to discard and give the reason.',
    'review': 'Select the work item and the check role.',
    'cancel_run': 'Select the agent run to cancel.',
    'request_work_review': 'Select the delegated work run to review again.',
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
    if operation in RUN_OPERATIONS:
        return _run_action(memory, operation, data, request_key, signature)
    try:
        with memory._write():
            prior = prior_result(memory, request_key, signature, KEY_REUSED)
            if prior is not None:
                return prior
            result = RECORD_HANDLERS[operation](memory, data, request_key)
            memory.db.execute('INSERT INTO adapter_requests VALUES (?,?,?)', (request_key, signature, dumps(result)))
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


def lesson_review(memory, data, request_key):
    lesson = memory.read(data['lesson_id'])
    if lesson['kind'] != 'lesson':
        raise InvalidRecord('Select a lesson to review.')
    triggers = {key: data[key] for key in ('paths', 'keywords', 'failure_type') if key in data}
    if triggers and data['status'] != 'accepted':
        raise InvalidRecord('Triggers apply only when you accept a lesson. Remove the paths, keywords and failure type, or accept the lesson.')
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
    return save_component(memory, component_id=data.get('component_id'), title=data['title'], kind=data['kind'],
                          description=data['description'], status=data['status'], actor=USER,
                          request_key=request_key + ':component', evidence=evidence, path=data.get('path'))


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


RECORD_HANDLERS = {'plan': plan, 'sprint': sprint, 'comment': comment, 'requirements': requirements,
                   'lesson_review': lesson_review, 'allow_paths': allow_paths, 'link': link, 'component': component,
                   'answer_kickoff': answer_kickoff}


# Agent run actions.

def _run_action(memory, operation, data, request_key, signature):
    prior = prior_result(memory, request_key, signature, KEY_REUSED)
    result = RUN_HANDLERS[operation](memory, data, request_key, first=prior is None)
    with memory._write():
        memory.db.execute('INSERT OR IGNORE INTO adapter_requests VALUES (?,?,?)', (request_key, signature, dumps(result)))
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
    return delegation.summary(memory, run)


def merge(memory, data, request_key, first):
    from . import delegation
    return delegation.merge(memory, data['run_id'], request_key=request_key + ':merge', actor=USER,
                            override_reason=data.get('override_reason'))


def discard(memory, data, request_key, first):
    from . import delegation
    return delegation.discard(memory, data['run_id'], request_key=request_key + ':discard', actor=USER, reason=data['reason'])


def review(memory, data, request_key, first):
    from . import delegation, reviews
    if data['role'] not in reviews.ROLES:
        raise InvalidRecord('The check role must be outcome, intent or recovery.')
    arguments = {'request_key': request_key + ':review', 'retry': data.get('retry', False)}
    if 'max_seconds' in data:
        arguments['max_seconds'] = data['max_seconds']
    run = reviews.request(memory, data['episode_id'], data['role'], **arguments)
    return delegation.summary(memory, _launch(memory, run, first))


def cancel_run(memory, data, request_key, first):
    from . import delegation, reviews
    return delegation.summary(memory, reviews.cancel(memory, data['run_id']))


def request_work_review(memory, data, request_key, first):
    """Request a new work review for a completed delegated run whose review failed, stopped or never started."""
    from . import delegation
    arguments = {'request_key': request_key + ':work-review'}
    if 'max_seconds' in data:
        arguments['max_seconds'] = data['max_seconds']
    follow = delegation.retry_review(memory, data['run_id'], **arguments)
    return delegation.summary(memory, _launch(memory, follow, first))


RUN_HANDLERS = {'delegate': delegate, 'merge': merge, 'discard': discard, 'review': review, 'cancel_run': cancel_run,
                'request_work_review': request_work_review}
