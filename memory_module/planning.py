"""Work plans and sprint views over the existing append-only episode history."""
import json
from datetime import date

STATES = ('backlog', 'ready', 'in_progress', 'blocked', 'review', 'done', 'cancelled')
ITEM_TYPES = ('phase', 'epic', 'story', 'task', 'research', 'deliverable', 'workflow')
CARD_LIMIT = 5000
FIELDS = {
    'work_plan': ({'state', 'next_action', 'scope', 'autonomy', 'reason'},
                  {'sprint_id', 'depends_on', 'owner', 'priority', 'session_id', 'paths',
                   'item_type', 'acceptance', 'parent_id', 'focus', 'worktree', 'archived'}),
    'sprint': ({'starts_on', 'ends_on', 'status', 'reason'}, set()),
}
# A revision of these plan fields changes what the work covers, so an earlier assessment no longer applies.
# Allowed paths are an enforcement boundary rather than the task itself: widening them after a scope block lets
# work continue, and guards.scope_changes lists path additions by agents for the user to inspect.
SCOPE_FIELDS = ('scope', 'autonomy', 'depends_on')
SENTENCE_END = ('.', '!', '?')


def scope_value(plan, key):
    """Return a plan field for scope comparison, treating an absent list as empty."""
    if key in ('depends_on', 'paths'):
        return plan.get(key) or []
    return plan.get(key)


def scope_changed(before, after):
    """True when two plan payloads differ in a field that defines the scope of work."""
    return any(scope_value(before, key) != scope_value(after, key) for key in SCOPE_FIELDS)


def latest(memory, episode_id, kind):
    row = memory.db.execute('SELECT id,payload FROM events WHERE episode_id=? AND kind=? ORDER BY seq DESC LIMIT 1',
                            (episode_id, kind)).fetchone()
    return {'id': row['id'], **json.loads(row['payload'])} if row else None


def validate_payload(kind, payload):
    from .core import InvalidRecord, _text
    if kind not in FIELDS:
        return False
    required, optional = FIELDS[kind]
    if not isinstance(payload, dict) or required - payload.keys() or payload.keys() - required - optional:
        raise InvalidRecord(f'{kind} requires {sorted(required)}; optional: {sorted(optional)}.')
    for key, value in payload.items():
        if key == 'depends_on':
            if not isinstance(value, list) or len(value) > 30:
                raise InvalidRecord('depends_on must contain at most 30 explained episode references.')
            for ref in value:
                if not isinstance(ref, dict) or set(ref) != {'episode_id', 'reason'}:
                    raise InvalidRecord('Each dependency needs episode_id and reason.')
                _text(ref['episode_id'], 'episode_id', 200)
                _text(ref['reason'], 'dependency reason', 2000)
            if len({r['episode_id'] for r in value}) != len(value):
                raise InvalidRecord('Dependencies must be unique.')
        elif key == 'sprint_id' and value is None:
            continue
        elif kind == 'work_plan' and key == 'paths':
            from .guards import validate_patterns
            validate_patterns(value)
        elif kind == 'work_plan' and key == 'item_type':
            if value not in ITEM_TYPES:
                raise InvalidRecord(f'item_type must be one of {list(ITEM_TYPES)}.')
        elif kind == 'work_plan' and key == 'acceptance':
            validate_acceptance(value)
        elif kind == 'work_plan' and key == 'worktree':
            # The folder where the work is done, so agent checks read that worktree instead of the project folder.
            from pathlib import PurePosixPath, PureWindowsPath
            _text(value, 'worktree', 1000)
            if not (PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute()):
                raise InvalidRecord('worktree must be the absolute path of a git worktree of the project repository.')
        elif kind == 'work_plan' and key == 'parent_id':
            _text(value, 'parent_id', 200)
        elif kind == 'work_plan' and key == 'focus':
            from .focus import validate_block
            validate_block(value)
        elif kind == 'work_plan' and key == 'archived':
            if not isinstance(value, dict) or set(value) != {'idle_days', 'last_activity', 'restore_state'}:
                raise InvalidRecord('archived names idle_days, last_activity and restore_state.')
            if type(value['idle_days']) is not int or value['restore_state'] not in STATES:
                raise InvalidRecord('archived needs whole idle_days and a known restore_state.')
            _text(value['last_activity'], 'last_activity', 64)
        else:
            _text(value, key, 2000)
    if kind == 'sprint':
        try:
            start, end = date.fromisoformat(payload['starts_on']), date.fromisoformat(payload['ends_on'])
            if start > end or start.isoformat()!=payload['starts_on'] or end.isoformat()!=payload['ends_on']:
                raise ValueError()
        except ValueError as exc:
            raise InvalidRecord('Sprint dates must be YYYY-MM-DD and start no later than end.') from exc
        if payload['status'] not in {'planned', 'active', 'closed'}:
            raise InvalidRecord('Sprint status must be planned, active or closed.')
    else:
        for key, allowed, default in [('state', STATES, None), ('autonomy', ('suggest', 'act'), None),
                                      ('owner', ('agent', 'human'), 'agent'), ('priority', ('high', 'normal', 'low'), 'normal')]:
            if payload.get(key, default) not in allowed:
                raise InvalidRecord(f'{key} must be one of {list(allowed)}.')
        if payload['state'] == 'in_progress' and payload.get('owner', 'agent') == 'agent' and not payload.get('session_id'):
            raise InvalidRecord('Agent work in progress requires its host session_id.')
    return True


def validate_acceptance(value):
    """Acceptance criteria are 1 to 30 unique complete sentences."""
    from .core import InvalidRecord, _text
    if not isinstance(value, list) or not 1 <= len(value) <= 30:
        raise InvalidRecord('acceptance must be a list of 1 to 30 acceptance criteria.')
    for criterion in value:
        _text(criterion, 'acceptance criterion', 2000)
        if not criterion.rstrip().endswith(SENTENCE_END):
            raise InvalidRecord('Each acceptance criterion must be a complete sentence that ends with a full stop, '
                                'a question mark or an exclamation mark.')
    if len(set(value)) != len(value):
        raise InvalidRecord('Acceptance criteria must be unique.')
    return value


def validate_parent(memory, episode, parent_id):
    """A parent is an existing work item that is not a sprint and is not a descendant of this item."""
    from .core import InvalidRecord
    if parent_id == episode['id']:
        raise InvalidRecord('A work item cannot be its own parent.')
    parent = memory.episode(parent_id)
    if parent['task_type'] == 'sprint':
        raise InvalidRecord('A sprint cannot be the parent of a work item. Assign the sprint with sprint_id instead.')
    # Parents follow the latest plan of each item, as dependencies do.
    found = memory.db.execute('''WITH RECURSIVE ancestors(id) AS (
      SELECT ? UNION SELECT json_extract(e.payload,'$.parent_id') FROM ancestors a
      JOIN events e ON e.episode_id=a.id AND e.kind='work_plan'
        AND e.seq=(SELECT max(n.seq) FROM events n WHERE n.episode_id=e.episode_id AND n.kind='work_plan')
      WHERE json_extract(e.payload,'$.parent_id') IS NOT NULL
    ) SELECT 1 FROM ancestors WHERE id=?''', (parent_id, episode['id'])).fetchone()
    if found:
        raise InvalidRecord('Work item parents cannot contain a cycle.')


def unresolved(memory, episode_id):
    """How many tool calls of this work item have no confirmed result."""
    from .shared import unconfirmed_total
    return unconfirmed_total(memory, episode_id=episode_id)


def completion(memory, episode_id):
    related = memory.db.execute('''WITH RECURSIVE work(id) AS (
      SELECT ? UNION SELECT json_extract(j.value,'$.episode_id') FROM work w
      JOIN events e ON e.episode_id=w.id AND e.kind='work_plan'
        AND e.seq=(SELECT max(n.seq) FROM events n WHERE n.episode_id=e.episode_id AND n.kind='work_plan')
      JOIN json_each(e.payload,'$.depends_on') j
    ) SELECT id FROM work''', (episode_id,)).fetchall()
    return all(completed_result(memory, r[0]) for r in related)


def completed_result(memory, episode_id):
    from .coverage import work_issues
    if work_issues(memory,episode_id):
        return False
    plan = latest(memory, episode_id, 'work_plan')
    if plan and plan['state'] == 'cancelled':
        return False
    decision = latest(memory, episode_id, 'decision')
    if not decision:
        return False
    row = memory.db.execute("SELECT id,payload FROM events WHERE kind='outcome' AND decision_id=? ORDER BY seq DESC LIMIT 1",
                            (decision['id'],)).fetchone()
    if not row:
        return False
    outcome = json.loads(row['payload'])
    from .reviews import required, current
    check = current(memory,episode_id) if required(memory,episode_id) else None
    return ((not check or check['state']=='pass') and outcome.get('assessment') == 'good' and outcome.get('completion') == 'complete'
            and not memory._needs_review(row['id']) and not memory.pending(episode_id, limit=1)['total']
            and not unresolved(memory, episode_id))


def validate_event(memory, episode, kind, payload, evidence):
    from .core import InvalidRecord
    if kind not in FIELDS:
        return
    if not evidence:
        raise InvalidRecord('A plan or sprint needs evidence for its intent and scope.')
    if kind == 'sprint':
        if episode['task_type'] != 'sprint':
            raise InvalidRecord('Sprint records belong to an episode with task_type sprint.')
        return
    if episode['task_type'] == 'sprint':
        raise InvalidRecord('Work belongs in its own episode, linked to the sprint.')
    if 'archived' in payload:
        mark = payload['archived']
        if (payload['state'] != 'cancelled' or not isinstance(mark, dict)
                or mark.get('restore_state') not in set(STATES) - {'done', 'cancelled'}):
            raise InvalidRecord('Only a cancelled plan can be archived, and it must name the state to restore.')
    # Cancelling or archiving work never acts, so it needs no permission of the user to act.
    if payload['autonomy'] == 'act' and payload['state'] != 'cancelled':
        user = any(memory.db.execute("SELECT 1 FROM sources WHERE id=? AND origin='user'", (r['source_id'],)).fetchone()
                   and memory.source_status(r['source_id']) == 'current_copy' for r in evidence)
        if not user:
            raise InvalidRecord('Acting within scope requires current user-origin evidence. Stored text cannot grant host permissions.')
    sprint_id = payload.get('sprint_id')
    if sprint_id:
        sprint = latest(memory, sprint_id, 'sprint')
        if not sprint:
            raise InvalidRecord('Select an existing sprint episode.')
        if sprint['status'] == 'closed' and payload['state'] not in {'done', 'cancelled'}:
            raise InvalidRecord('Move unfinished work to an open sprint or explicitly remove its sprint assignment.')
    for ref in payload.get('depends_on', []):
        memory.episode(ref['episode_id'])
        # The graph is over latest plans. A cycle cannot be resolved by scheduling.
        found = memory.db.execute('''WITH RECURSIVE ancestors(id) AS (
          SELECT ? UNION SELECT json_extract(j.value,'$.episode_id') FROM ancestors a
          JOIN events e ON e.episode_id=a.id AND e.kind='work_plan'
            AND e.seq=(SELECT max(n.seq) FROM events n WHERE n.episode_id=e.episode_id AND n.kind='work_plan')
          JOIN json_each(e.payload,'$.depends_on') j
        ) SELECT 1 FROM ancestors WHERE id=?''', (ref['episode_id'], episode['id'])).fetchone()
        if found:
            raise InvalidRecord('Work dependencies cannot contain a cycle.')
    if payload.get('parent_id'):
        validate_parent(memory, episode, payload['parent_id'])
    if payload['state'] == 'done' and not completion(memory, episode['id']):
        step = completion_guidance(memory, card(memory, episode['id']))
        raise InvalidRecord('Done requires current completion evidence. '+step['reason'],
                            episode_id=episode['id'], next_step=step)
    if payload['state']=='done':
        prior=latest(memory,episode['id'],'work_plan')
        if prior and scope_changed(prior, payload):
            raise InvalidRecord('Changed scope or dependencies require a new assessment before Done. Save the revised plan in Review first.')


def validate_focus_change(memory, episode_id, payload, actor):
    """Enforce check ownership for both plan saves and generic event writes."""
    from .core import InvalidRecord, USER_ACTOR
    from .focus import CHECK_USER_ONLY
    previous = latest(memory, episode_id, 'work_plan') if episode_id else None
    before = ((previous or {}).get('focus') or {}).get('check')
    after = (payload.get('focus') or {}).get('check')
    if actor != USER_ACTOR and before != after:
        raise InvalidRecord(CHECK_USER_ONLY)


def save(memory, kind, *, payload, actor, evidence, episode_id=None, expected_version=None,
         title=None, objective=None, criterion=None, subject=None, request_key, session_id=None, links=None):
    """Create work and its first plan atomically, or update at an explicit version."""
    from .core import InvalidRecord
    payload = dict(payload)
    if kind == 'work_plan':
        block = payload.get('focus')
        if block is not None:
            from .focus import validate_block
            validate_block(block)
        validate_focus_change(memory, episode_id, payload, actor)
    if kind == 'work_plan' and payload.get('state') == 'in_progress' and payload.get('owner', 'agent') == 'agent':
        if not session_id:
            raise InvalidRecord('Use the host session_id when claiming work in progress.')
        payload['session_id'] = session_id
    if episode_id:
        if expected_version is None or any(x is not None for x in (title, objective, criterion)):
            raise InvalidRecord('An update needs expected_version. The intended result of a work item is fixed; create a linked work item for a changed objective.')
        if subject is not None and subject != memory.episode(episode_id)['subject']:
            raise InvalidRecord('A plan cannot change the subject of its work item.')
    else:
        if expected_version is not None:
            raise InvalidRecord('A new work item does not take expected_version.')
        episode = memory.start(title, objective, 'sprint' if kind == 'sprint' else 'action', criterion, subject or 'general')
        episode_id, expected_version = episode['id'], 0
    previous = latest(memory, episode_id, kind)
    result = memory.record(episode_id, kind, payload, expected_version=expected_version, actor=actor,
                           evidence=evidence, links=links, request_key=request_key, supersedes=previous['id'] if previous else None)
    return {**result, 'episode_id': episode_id}


def progress(memory, *, episode_id, expected_version, payload, actor, request_key, session_id=None):
    """Append status changes without replacing scope or its supporting evidence."""
    from .core import InvalidRecord
    if not isinstance(payload,dict) or not {'reason'}<=payload.keys() or not payload.keys()<={'state','next_action','reason'} or not ({'state','next_action'}&payload.keys()):
        raise InvalidRecord('Progress requires reason and state or next_action. Use plan for an intentional scope revision.')
    previous = latest(memory,episode_id,'work_plan')
    if not previous:
        raise InvalidRecord('Record a plan before updating progress.')
    record = memory.read(previous['id'])
    evidence = [{'source_id':e['source_id'],'reason':e['reason']} for e in record['evidence']]
    return save(memory,'work_plan',episode_id=episode_id,expected_version=expected_version,
                payload={**record['payload'],**payload},actor=actor,evidence=evidence,
                request_key=request_key,session_id=session_id,links=record['links'])


def card(memory, episode_id):
    episode = memory.episode(episode_id)
    plan = latest(memory, episode_id, 'work_plan')
    decision = latest(memory, episode_id, 'decision')
    problems = []
    recorded = plan['state'] if plan else ('done' if episode['status'] == 'settled' else 'cancelled' if episode['status'] == 'abandoned' else 'backlog')
    if not plan:
        problems.append({'type': 'plan_missing', 'reason': 'This work item has no recorded plan, so it has no next action and no scope for agents.'})
    else:
        for reason in memory.review_reasons(plan['id']):
            problems.append({'type': 'intent_review', **reason})
        if plan.get('sprint_id'):
            sprint = latest(memory, plan['sprint_id'], 'sprint')
            if sprint and sprint['status'] == 'closed' and recorded not in {'done', 'cancelled'}:
                problems.append({'type': 'sprint_closed', 'reason': 'The sprint is closed. Reassign unfinished work before continuing.'})
        for ref in plan.get('depends_on', []):
            if not completion(memory, ref['episode_id']):
                problems.append({'type': 'dependency', **ref})
    outcome_row = memory.db.execute("SELECT id,payload FROM events WHERE kind='outcome' AND decision_id=? ORDER BY seq DESC LIMIT 1",
                                    (decision['id'],)).fetchone() if decision else None
    outcome = {'id': outcome_row['id'], **json.loads(outcome_row['payload'])} if outcome_row else None
    if decision:
        for reason in memory.review_reasons(outcome['id'] if outcome else decision['id']):
            problems.append({'type': 'evidence_review', **reason})
    from .coverage import work_issues
    problems.extend(work_issues(memory,episode_id))
    unconfirmed = unresolved(memory, episode_id)
    if unconfirmed:
        problems.append({'type': 'execution_unconfirmed', 'reason': f'{unconfirmed} tool calls need reconciliation before any retry.'})
    if recorded == 'done' and not completion(memory, episode_id):
        problems.append({'type': 'completion_review', 'reason': 'Current completion evidence does not establish that the intended result is achieved.'})
    state = recorded
    if state != 'cancelled':
        # Backlog that waits for an unfinished prerequisite is not blocked: nothing is scheduled yet.
        if any(p['type'] == 'execution_unconfirmed' or p['type'] == 'dependency' and recorded != 'backlog' for p in problems):
            state = 'blocked'
        elif problems and recorded not in {'backlog', 'blocked'}:
            state = 'review'
    from .reviews import required, current
    check = current(memory,episode_id) if required(memory,episode_id) else None
    if check and check['state']!='pass' and recorded!='cancelled' and (recorded=='done' or outcome and outcome.get('completion')=='complete'):
        problems.append({'type':'agent_check','reason':'The outcome agent check is '+check['state'].replace('_',' ')+'.','run_id':check.get('id')})
        if state not in {'blocked','backlog'}: state='review'
    if check:
        report=check.get('report')
        check={k:v for k,v in check.items() if k!='report'}
        if report:check['summary']=report['summary']
        if check.get('id'):check['read_full_with']={'view':'record','id':check['id'],'max_chars':20000}
    item = {'id': episode_id, 'title': episode['title'], 'subject': episode['subject'], 'date': episode['created_at'],
            'version': episode['version'], 'intent': episode['objective'], 'done_when': episode['criterion'],
            'state': state, 'recorded_state': recorded, 'plan': plan, 'issues': problems,
            'decision_id': decision['id'] if decision else None, **({'agent_check':check} if check else {}),
            'outcome': {'id': outcome['id'], 'assessment': outcome['assessment'], 'completion': outcome.get('completion')} if outcome else None}
    if outcome and outcome.get('completion')=='complete' or recorded=='done':
        item['completion_next'] = completion_guidance(memory, item)
    return item


def cards(memory, *, limit=CARD_LIMIT):
    """Every work item card, hashing the project once. Sprints are not work items."""
    from .reviews import shared_tree
    rows = memory.db.execute("SELECT id FROM episodes WHERE task_type!='sprint' ORDER BY created_at,id LIMIT ?",
                             (limit + 1,)).fetchall()
    with shared_tree(memory):
        values = {row[0]: card(memory, row[0]) for row in rows[:limit]}
    return {'cards': values, 'truncated': len(rows) > limit}


def card_states(found):
    """Map each work item id to its evidence checked state, from a cards() result."""
    return {episode_id: item['state'] for episode_id, item in found['cards'].items()}


def card_summary(item):
    """The bounded summary of one card, as lists and the Now view show it."""
    plan = item['plan'] or {}
    return {'id': item['id'], 'title': item['title'], 'subject': item['subject'], 'state': item['state'],
            'priority': plan.get('priority', 'normal'), 'next_action': plan.get('next_action'),
            'issues': len(item['issues']), 'owner': plan.get('owner', 'agent') if plan else None,
            'item_type': plan.get('item_type', 'task')}


def completion_guidance(memory, item):
    """Explain completion gates without granting permission or changing records."""
    from .reviews import ACTIVE, wait_command
    ep = item['id']
    issues = item['issues']
    if item['state']=='cancelled':
        return {'action':'stop', 'reason':'This work is cancelled.'}
    for kinds, action, reason in (
        ({'execution_unconfirmed'}, 'reconcile', 'Inspect actual tool effects and reconcile uncertain execution before any retry.'),
        ({'intent_unassessed','capture_gap'}, 'assess_coverage', 'Assess the unrecorded request or capture gap before completing this work.'),
        ({'intent_review','evidence_review'}, 'refresh_evidence', 'Inspect the changed evidence listed below and record its reassessment. Waiting for a review will not refresh evidence.'),
        ({'dependency'}, 'inspect_dependency', 'Inspect the unfinished prerequisite before completing this work.'),
        ({'sprint_closed'}, 'review_plan', 'Reassign unfinished work from the closed sprint.'),
    ):
        blockers = [i for i in issues if i['type'] in kinds]
        if blockers:
            first = blockers[0]
            read = {'view':'next', 'id':ep}
            if first.get('source_id') or first.get('record_id'):
                read = {'view':'record', 'id':first.get('source_id') or first['record_id']}
            elif first['type']=='dependency':
                read = {'view':'next', 'id':first['episode_id']}
            elif action in {'reconcile','assess_coverage'}:
                session = (item['plan'] or {}).get('session_id')
                if not session:
                    row = memory.db.execute("SELECT session_id FROM host_receipts WHERE episode_id=? AND event_name='DecisionBound' ORDER BY rowid DESC LIMIT 1", (ep,)).fetchone()
                    session = row[0] if row else None
                if session: read = {'view':'coverage', 'session_id':session}
            return {'action':action, 'reason':reason, 'blockers':blockers, 'read_with':read}
    outcome = item['outcome']
    if not item['decision_id'] or not outcome:
        return {'action':'assess_outcome', 'reason':'Inspect existing work and record the decision and evidenced outcome before completing it. Do not repeat completed actions.',
                'read_with':{'view':'episode','id':ep}}
    if outcome['assessment']!='good' or outcome['completion']!='complete':
        return {'action':'review_outcome', 'reason':'The recorded outcome does not establish a good, complete result. Inspect its findings and remaining work.',
                'read_with':{'view':'record','id':outcome['id']}}
    if memory.pending(ep,limit=1)['total']:
        return {'action':'assess_outcome', 'reason':'An earlier decision still has an unresolved outcome. Inspect its existing execution before recording an assessment.',
                'read_with':{'view':'episode','id':ep}}
    check = item.get('agent_check')
    if check and check['state']!='pass':
        state = check['state']
        step = {'read_with':{'view':'reviews','id':ep}, 'check_id':check.get('id'), 'review_state':state}
        if state in ACTIVE:
            step.update(action='wait_review', reason='The outcome review is '+state+'. Wait for this existing check; the recorded implementation is complete.',
                        wait_command=wait_command(memory,check['id']))
        elif state=='stale':
            step.update(action='refresh_review', reason='The previous review no longer covers current evidence or project files. Inspect the changes and reassess the outcome before requesting a new check.')
        elif state=='missing':
            step.update(action='request_review', reason='The complete outcome has no required agent check. Request an outcome review without repeating implementation.',
                        schema={'view':'schema','id':'agent_check'})
        elif state in {'changes_required','uncertain'}:
            step.update(action='review_findings', reason='Inspect the review findings and missing evidence before deciding what work or reassessment is needed.')
        else:
            step.update(action='inspect_review', reason='The review '+state.replace('_',' ')+'. Inspect its retained report and execution before explicitly retrying the check. Do not repeat implementation because a review stopped.')
        return step
    if completion(memory,ep):
        return {'action':'finalize', 'reason':'The recorded result and required review are current. Mark the work Done without repeating completed actions.'}
    return {'action':'inspect_completion', 'reason':'Inspect the recorded decisions and completion evidence before marking this work Done.',
            'read_with':{'view':'episode','id':ep}}


def board(memory, *, limit=25, offset=0, sprint_id=None, subject=None, query='', state=None, episode_id=None, grouped=False):
    from .reviews import shared_tree
    with shared_tree(memory):
        return _board(memory, limit=limit, offset=offset, sprint_id=sprint_id, subject=subject, query=query, state=state,
                      episode_id=episode_id, grouped=grouped)


def _board(memory, *, limit=25, offset=0, sprint_id=None, subject=None, query='', state=None, episode_id=None, grouped=False):
    from .core import InvalidRecord
    if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
        raise InvalidRecord('Use limit 1–100 and a nonnegative offset.')
    if len(query) > 500:
        raise InvalidRecord('Search is limited to 500 characters.')
    if subject:
        memory._subject(subject)
    if state and state not in STATES:
        raise InvalidRecord('Unknown work state.')
    sql = '''SELECT ep.id FROM episodes ep LEFT JOIN events p ON p.episode_id=ep.id AND p.kind='work_plan'
        AND p.seq=(SELECT max(n.seq) FROM events n WHERE n.episode_id=ep.id AND n.kind='work_plan')
        WHERE ep.task_type!='sprint' '''
    args = []
    for condition, value in [('ep.id=?', episode_id), ('ep.subject=?', subject)]:
        if value:
            sql += ' AND '+condition; args.append(value)
    if sprint_id:
        sql += " AND coalesce(json_extract(p.payload,'$.sprint_id'),'')=?"; args.append('' if sprint_id == 'unassigned' else sprint_id)
    if query:
        sql += " AND instr(lower(ep.title||' '||ep.objective||' '||coalesce(p.payload,'')),lower(?))>0"; args.append(query)
    sql += " ORDER BY CASE json_extract(p.payload,'$.priority') WHEN 'high' THEN 0 WHEN 'low' THEN 2 ELSE 1 END,ep.created_at,ep.id"
    records, counts, total = [], dict.fromkeys(STATES, 0), 0
    groups = {state:[] for state in STATES} if grouped else None
    for row in memory.db.execute(sql, args):
        item = card(memory, row[0])
        counts[item['state']] += 1
        if grouped and len(groups[item['state']])<limit:
            groups[item['state']].append(item)
        if state and item['state'] != state:
            continue
        if offset <= total < offset+limit:
            records.append(item)
        total += 1
    if grouped: return {'groups':groups,'counts':counts,'total':total}
    return {'cards': records, 'counts': counts, 'total': total, 'offset': offset, 'more': offset+len(records)<total,
            'note': 'State is checked against recorded evidence. This view does not run work or grant permission.'}


def hierarchy(memory, *, root=None, limit=500, states=None):
    """Work items arranged by parent_id, with state counts rolled up over the descendants of each item.

    States come from the optional `states` map {episode_id: state}; missing states
    are computed once per included item from its card. Items whose parent is not a
    work item in this project are roots. The result is bounded by `limit` items.
    """
    from .core import InvalidRecord
    if type(limit) is not int or not 1 <= limit <= 5000:
        raise InvalidRecord('limit must be an integer from 1 to 5000.')
    if states is not None and not isinstance(states, dict):
        raise InvalidRecord('states must map episode ids to states.')
    if root is not None:
        if memory.episode(root)['task_type'] == 'sprint':
            raise InvalidRecord('A sprint is not part of the work item hierarchy.')
    rows = memory.db.execute('''SELECT ep.id, ep.title, ep.subject, ep.created_at, p.id AS plan_id, p.payload
        FROM episodes ep LEFT JOIN events p ON p.episode_id=ep.id AND p.kind='work_plan'
          AND p.seq=(SELECT max(n.seq) FROM events n WHERE n.episode_id=ep.id AND n.kind='work_plan')
        WHERE ep.task_type!='sprint' ORDER BY ep.created_at, ep.id''').fetchall()
    items = {}
    children = {}
    for row in rows:
        plan = json.loads(row['payload']) if row['payload'] else {}
        items[row['id']] = {'id': row['id'], 'title': row['title'], 'subject': row['subject'], 'created_at': row['created_at'],
                            'plan_id': row['plan_id'], 'item_type': plan.get('item_type', 'task'),
                            'parent_id': plan.get('parent_id'), 'owner': plan.get('owner', 'agent') if plan else None,
                            'priority': plan.get('priority', 'normal'), 'acceptance_total': len(plan.get('acceptance', []))}
    for item in items.values():
        if item['parent_id'] in items:
            children.setdefault(item['parent_id'], []).append(item['id'])
    if root is not None:
        roots = [root]
    else:
        roots = [item_id for item_id, item in items.items() if item['parent_id'] not in items]
    included = []
    seen = set()
    stack = list(reversed(roots))
    truncated = False
    while stack:
        item_id = stack.pop()
        if item_id in seen:
            continue
        if len(included) >= limit:
            truncated = True
            break
        seen.add(item_id)
        included.append(item_id)
        stack.extend(reversed(children.get(item_id, [])))
    states = states or {}
    root_ids = set(roots)
    nodes = {}
    for item_id in included:
        node = dict(items[item_id])
        node['state'] = states.get(item_id) or card(memory, item_id)['state']
        node['children'] = []
        node['rollup'] = dict.fromkeys(STATES, 0)
        node['descendants'] = 0
        nodes[item_id] = node
    # Included items are in depth-first order, so every child follows its parent.
    for item_id in reversed(included):
        node = nodes[item_id]
        parent = nodes.get(node['parent_id']) if item_id not in root_ids else None
        if parent is None:
            continue
        parent['children'].insert(0, node)
        parent['descendants'] += 1 + node['descendants']
        parent['rollup'][node['state']] += 1
        for state, count in node['rollup'].items():
            parent['rollup'][state] += count
    counts = dict.fromkeys(STATES, 0)
    for node in nodes.values():
        counts[node['state']] += 1
        active = node['descendants'] - node['rollup']['cancelled']
        node['progress'] = round(node['rollup']['done'] / active, 4) if active else None
    return {'roots': [nodes[item_id] for item_id in roots if item_id in nodes], 'total': len(nodes), 'counts': counts,
            'truncated': truncated,
            'note': 'Progress counts descendant work items that are done, excluding cancelled items. It does not measure effort.'}


def sprints(memory, limit=25, offset=0, episode_id=None):
    from .core import InvalidRecord
    if type(limit) is not int or not 1<=limit<=100 or type(offset) is not int or offset<0:
        raise InvalidRecord('Use limit 1–100 and a nonnegative offset.')
    rows = memory.db.execute("SELECT id FROM episodes WHERE task_type='sprint'"+(' AND id=?' if episode_id else '')+
                            ' ORDER BY created_at DESC,id LIMIT ? OFFSET ?', ((episode_id,) if episode_id else ())+(limit+1, offset)).fetchall()
    result = []
    for row in rows[:limit]:
        ep = memory.episode(row[0]); value = latest(memory, ep['id'], 'sprint')
        result.append({'id': ep['id'], 'title': ep['title'], 'intent': ep['objective'], 'version': ep['version'], 'schedule': value})
    return {'sprints': result, 'offset': offset, 'more': len(rows)>limit}


SELECTABLE = ('in_progress', 'ready')


def selection(memory, *, limit=5, offset=0, subject=None, state=None):
    """The work a caller may choose from: the requested page, and the items that can start now.

    The board is ordered by priority and age, so template phases that wait for an
    earlier phase can fill the first page. `ready` names the work that can start
    now, so asking what to do next does not hide it behind a page boundary or a
    character budget.
    """
    from .reviews import shared_tree
    with shared_tree(memory):
        page = _board(memory, limit=limit, offset=offset, subject=subject, state=state)
        result = {'selection_required': True, 'board': page,
                  'next_step': 'Select work that matches the current user request. A queued task is not permission to switch objectives.'}
        if state is None:
            grouped = _board(memory, limit=limit, subject=subject, grouped=True)
            startable = [item for name in SELECTABLE for item in grouped['groups'][name]]
            result['ready'] = [card_summary(item) for item in startable[:limit]]
            result['ready_total'] = sum(grouped['counts'][name] for name in SELECTABLE)
            if result['ready_total']:
                result['next_step'] = ('Select work that matches the current user request. The items under ready can start now; '
                                       'the board also lists work that waits for an earlier item. '
                                       'A queued task is not permission to switch objectives.')
    return result


def next_work(memory, *, episode_id=None, session_id=None, limit=5, offset=0, subject=None, state=None):
    from . import codex_host
    from .core import InvalidRecord
    status = codex_host.status(memory, session_id, limit=3) if session_id and codex_host.exists(memory) else None
    if not episode_id and status and status['active']:
        episode_id = status['active']['episode_id']
    if not episode_id:
        return selection(memory, limit=limit, offset=offset, subject=subject, state=state)
    state = status
    item = card(memory, episode_id)
    if subject and item['subject'] != subject:
        raise InvalidRecord('The selected work does not match the requested subject.')
    plan = item['plan']
    reason, action = 'Review the recorded intent before planning work.', 'plan'
    if item['state'] == 'cancelled':
        action, reason = 'stop', 'This work is cancelled.'
    elif state and state['unconfirmed_total']:
        action, reason = 'reconcile', 'Inspect unconfirmed host calls and their real effects before continuing.'
    elif any(p['type'] == 'execution_unconfirmed' for p in item['issues']):
        action, reason = 'reconcile', 'This work contains unconfirmed execution from a host session.'
    elif item['issues'] and plan:
        step = completion_guidance(memory,item)
        action, reason = step['action'], step['reason']
    elif plan:
        if plan['state'] in {'done', 'cancelled'}:
            action, reason = 'stop', 'This work has no pending continuation.'
        elif completion(memory, episode_id):
            action, reason = 'finalize', 'The intended result has current completion evidence. Update the plan to Done; do not repeat the completed action.'
        elif plan.get('session_id') and plan['state'] == 'in_progress' and plan['session_id'] != session_id:
            action, reason = 'inspect_owner', 'Another session recorded work in progress. Inspect its result before taking over.'
        elif plan.get('owner', 'agent') == 'human' or plan['autonomy'] == 'suggest':
            action, reason = 'propose', 'The recorded scope calls for a human step or a proposal.'
        elif plan['state'] in {'backlog', 'blocked', 'review'}:
            action, reason = 'review', 'This work is not ready to continue; inspect its recorded reason and next action.'
        elif item['decision_id'] and memory.db.execute("SELECT 1 FROM events WHERE kind='action' AND decision_id=?", (item['decision_id'],)).fetchone() and not item['outcome']:
            action, reason = 'inspect_execution', 'The decision has recorded execution but no assessed outcome. Inspect existing tool results and side effects before following a potentially outdated next action; do not repeat completed work.'
        elif item['outcome'] and item['outcome']['assessment'] in {'bad', 'unknown', 'pending'}:
            action, reason = 'review_outcome', 'Review the recorded outcome and its uncertainty. Revise the decision when another attempt is needed; a failed or unknown outcome does not authorise a retry.'
        else:
            action, reason = 'continue', 'Continue only within the recorded scope and the current user authorisation.'
    update = None
    if plan:
        update = {'tool':'memory_write','operation':'progress','episode_id':episode_id,'expected_version':item['version'],
                  'schema':{'view':'schema','id':'progress'},
                  'note':'Supply actor and payload with reason and state or next_action. Pass this host session_id for in_progress. Progress preserves scope, dependencies, links and evidence. Use plan only for an intentional scope revision.'}
    step = completion_guidance(memory,item) if action in {'reconcile','refresh_evidence','assess_coverage','inspect_dependency','review_plan','wait_review','refresh_review','request_review','review_findings','inspect_review','finalize'} else None
    return {'work': item, 'action': action, 'reason': reason, 'update':update,
            **({'next_step':step} if step and step['action']==action else {}),
            'unconfirmed': state['unconfirmed'] if state and action == 'reconcile' else [],
            'recent_execution': state['recent'] if state and action == 'inspect_execution' else [],
            'requirements': {'version': memory.direction()['version'], 'read_with': 'memory_get requirements'},
            'note': 'This is advisory state, not host permission. Retrieve complete governing requirements and evidence before consequential work.'}


# The phase of the project. The phase decides who may merge delegated work. It is stored as the
# versioned source project-phase, so the current phase is the latest version and every earlier
# phase stays readable. Only the user writes it, from the control panel, through set_phase.

PHASES = ('development', 'production')
DEFAULT_PHASE = 'development'
DEFAULT_PHASE_REASON = 'A new project starts in development. No phase change is recorded yet.'
PHASE_TITLES = {'development': 'Project phase: development', 'production': 'Project phase: production'}
# The meaning of each phase speaks of work items and results, so it reads the same for a software product,
# an engagement and an automation.
PHASE_MEANING = {
    'development': 'The project is in development, so the orchestrator may bring delegated work into the project after '
                   'a passing work review.',
    'production': 'The project is in production and maintenance, so only the user brings delegated work into the '
                  'project, in the control panel.',
}
PHASE_UNVERIFIED_REASON = ('The latest recorded phase was not written through the control panel, so Project Memory treats '
                           'the project as in production until the user records the phase again.')


def _phase_seal(version, name, reason, at, previous):
    """The seal of one recorded phase, which chains it to the seal of the version before it."""
    from .core import PHASE_SOURCE_KEY, _digest, dumps
    return _digest(dumps([PHASE_SOURCE_KEY, version, name, reason, at, previous or '']))


def _phase_body(row):
    try:
        body = json.loads(row['body'])
    except ValueError:
        body = {}
    return body if isinstance(body, dict) else {}


def _phase_version(row, previous=None):
    """One recorded phase, tolerant of a body that cannot be read as JSON.

    `verified` is true when the version was written by set_phase: its body
    carries the actor of the user and a seal that chains to the version before
    it. A row written into the database in another way fails that check.
    """
    from .core import USER_ACTOR
    body = _phase_body(row)
    name = body.get('phase') if body.get('phase') in PHASES else DEFAULT_PHASE
    earlier = _phase_body(previous).get('seal') if previous is not None else None
    verified = (row['origin'] == 'user' and body.get('actor') == USER_ACTOR and body.get('phase') in PHASES
                and isinstance(body.get('reason'), str)
                and body.get('seal') == _phase_seal(row['version'], body.get('phase'), body.get('reason'),
                                                    body.get('at'), earlier))
    return {'phase': name, 'reason': body.get('reason') or row['summary'],
            'at': body.get('at') or row['checked_at'], 'version': row['version'], 'verified': verified}


def _phase_rows(memory, limit):
    from .core import PHASE_SOURCE_KEY
    return memory.db.execute('SELECT version,summary,body,checked_at,origin FROM sources WHERE source_key=? '
                             'ORDER BY version DESC LIMIT ?', (PHASE_SOURCE_KEY, limit)).fetchall()


def _latest_phase(memory):
    """The newest recorded phase, checked against the version before it, or None."""
    rows = _phase_rows(memory, 2)
    if not rows:
        return None
    return _phase_version(rows[0], rows[1] if len(rows) > 1 else None)


def phase(memory):
    """The current phase of the project, with the reason and the time it was recorded.

    A project without a recorded phase is in development at version 0. When the
    newest version was not written through the control panel, the project is
    treated as in production, so a changed record can only tighten the gate.
    """
    current = _latest_phase(memory)
    if current is None:
        return {'phase': DEFAULT_PHASE, 'reason': DEFAULT_PHASE_REASON, 'at': None, 'version': 0, 'verified': True}
    if not current['verified']:
        return {**current, 'phase': 'production', 'reason': PHASE_UNVERIFIED_REASON}
    return current


def phase_history(memory, *, limit=50):
    """Every recorded phase, newest first, so an earlier phase stays readable."""
    from .core import InvalidRecord
    if type(limit) is not int or not 1 <= limit <= 200:
        raise InvalidRecord('Use limit 1 to 200 for the recorded phases.')
    rows = _phase_rows(memory, limit + 1)
    return [_phase_version(row, rows[index + 1] if index + 1 < len(rows) else None)
            for index, row in enumerate(rows[:limit])]


def set_phase(memory, *, phase, reason, actor):
    """Record a new phase of the project. Only the user changes the phase, in the control panel."""
    from .core import InvalidRecord, PHASE_SOURCE_KEY, USER_ACTOR, dumps, _text
    if actor != USER_ACTOR:
        raise InvalidRecord('Changing the phase of the project is a user action in the control panel. The recorded actor '
                            'is ' + USER_ACTOR + ', so another actor name is not accepted.', actor=USER_ACTOR)
    if phase not in PHASES:
        raise InvalidRecord('Select the phase of the project. Use one of: ' + ', '.join(PHASES) + '.',
                            phases=list(PHASES))
    _text(reason, 'reason', 2000)
    with memory._write():
        rows = _phase_rows(memory, 2)
        current = _phase_version(rows[0], rows[1] if len(rows) > 1 else None) if rows else None
        if current and current['verified'] and current['phase'] == phase:
            raise InvalidRecord('This project is already in ' + phase + '. Record a phase only when it changes.',
                                phase=phase, version=current['version'])
        at = memory.now()
        version = rows[0]['version'] + 1 if rows else 1
        previous = _phase_body(rows[0]).get('seal') if rows else None
        text = reason.strip()
        body = dumps({'phase': phase, 'reason': text, 'at': at, 'actor': USER_ACTOR,
                      'seal': _phase_seal(version, phase, text, at, previous)})
        source = memory.source(PHASE_SOURCE_KEY, PHASE_TITLES[phase], text, body, 'user', internal=True)
    return {'phase': phase, 'reason': text, 'at': at, 'version': source['version'],
            'source_id': source['id'], 'actor': USER_ACTOR, 'meaning': PHASE_MEANING[phase]}


# Idle work expires. On 24 September 2026 the project created 6.5 work items per active day and closed 2.0, and nothing left
# the list unless someone closed it. The user chose 14 days without activity, and 7 days for an item waiting on another item.
EXPIRY_DAYS = {'idle': 14, 'waiting': 7}
OPEN_STATES = ('backlog', 'ready', 'in_progress', 'blocked', 'review')
EXPIRY_ACTOR = 'project-memory'


def expiry_days(memory):
    """The idle periods after which open work is archived."""
    days = dict(EXPIRY_DAYS)
    for key in days:
        row = memory.db.execute('SELECT value FROM settings WHERE key=?', ('expire_' + key + '_days',)).fetchone()
        if row:
            days[key] = int(row[0])
    return days


def set_expiry_days(memory, *, idle, waiting):
    from .core import InvalidRecord
    if any(type(value) is not int or not 1 <= value <= 365 for value in (idle, waiting)):
        raise InvalidRecord('Expiry periods are whole numbers of days from 1 to 365.')
    with memory._write():
        for key, value in (('idle', idle), ('waiting', waiting)):
            memory.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', ('expire_' + key + '_days', str(value)))


def expire(memory, now=None):
    """Archive open work without activity for the idle period, or the shorter period when it waits on another item.

    An archive is a plan revision to cancelled that names the state to restore. It adds records and removes none.
    A parent with open children waits until they are closed. Return the archived items.
    """
    from datetime import datetime, timezone
    from .core import InvalidRecord, Conflict
    now = now or datetime.now(timezone.utc)
    days = expiry_days(memory)
    plans = {}
    for row in memory.db.execute("""SELECT e.episode_id,e.id,e.payload FROM events e WHERE e.kind='work_plan'
                                    AND e.seq=(SELECT max(n.seq) FROM events n WHERE n.episode_id=e.episode_id AND n.kind='work_plan')"""):
        plans[row['episode_id']] = {'id': row['id'], **json.loads(row['payload'])}
    open_ids = {ep for ep, plan in plans.items() if plan['state'] in OPEN_STATES}
    parents = {plan.get('parent_id') for ep, plan in plans.items() if ep in open_ids}
    archived, evidence = [], None
    for ep in sorted(open_ids - parents):
        plan = plans[ep]
        waiting = any((plans.get(ref['episode_id']) or {}).get('state') in OPEN_STATES for ref in plan.get('depends_on', []))
        limit = days['waiting' if waiting else 'idle']
        last = memory.db.execute('SELECT max(created_at) FROM events WHERE episode_id=?', (ep,)).fetchone()[0]
        idle = (now - datetime.fromisoformat(last)).total_seconds() / 86400
        if idle < limit:
            continue
        if evidence is None:
            source = memory.source('expiry:' + now.isoformat(), 'Idle work archived', 'Open work without activity is archived.',
                                   f'Rule of the user of 24 September 2026: archive open work after {days["idle"]} days without '
                                   f'activity, or {days["waiting"]} days when it waits on another item. Run at {now.isoformat()}.',
                                   'tool', subject='general')
            evidence = [{'source_id': source['id'], 'reason': 'The expiry rule archived this work.'}]
        payload = {k: v for k, v in plan.items() if k not in {'id', 'session_id'}}
        payload.update(state='cancelled', reason=f'Archived after {limit} days without activity. Ask in the chat to restore it.',
                       archived={'idle_days': limit, 'last_activity': last, 'restore_state': plan['state']})
        episode = memory.episode(ep)
        if episode['status'] in {'settled', 'abandoned'}:
            # A closed episode takes no new plan; its recorded state is outdated rather than open.
            continue
        try:
            save(memory, 'work_plan', payload=payload, actor=EXPIRY_ACTOR, evidence=evidence, episode_id=ep,
                 expected_version=episode['version'], request_key='expire:' + plan['id'])
        except (InvalidRecord, Conflict):
            # One refused item, for example one changed at the same moment, does not stop the others.
            continue
        archived.append({'episode_id': ep, 'title': episode['title'], 'idle_days': limit, 'restore_state': plan['state']})
    return archived


def restore(memory, episode_id, *, evidence, request_key, actor='agent'):
    """Return archived work to the state it had before it was archived."""
    from .core import InvalidRecord
    plan = latest(memory, episode_id, 'work_plan')
    if not plan or 'archived' not in plan:
        raise InvalidRecord('This work item is not archived.')
    payload = {k: v for k, v in plan.items() if k not in {'id', 'archived'}}
    payload.update(state=plan['archived']['restore_state'], reason='Restored at the request of the user.')
    return save(memory, 'work_plan', payload=payload, actor=actor, evidence=evidence, episode_id=episode_id,
                expected_version=memory.episode(episode_id)['version'], request_key=request_key)
