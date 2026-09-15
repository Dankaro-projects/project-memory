"""Work plans and sprint views over the existing append-only episode history."""
import json
from datetime import date

STATES = ('backlog', 'ready', 'in_progress', 'blocked', 'review', 'done', 'cancelled')
FIELDS = {
    'work_plan': ({'state', 'next_action', 'scope', 'autonomy', 'reason'},
                  {'sprint_id', 'depends_on', 'owner', 'priority', 'session_id'}),
    'sprint': ({'starts_on', 'ends_on', 'status', 'reason'}, set()),
}


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


def unresolved(memory, episode_id):
    from . import codex_host
    if not codex_host.exists(memory):
        return 0
    return memory.db.execute('''SELECT count(*) FROM host_receipts p WHERE p.episode_id=? AND p.event_name='PreToolUse'
        AND NOT EXISTS (SELECT 1 FROM host_receipts r WHERE r.event_name='PostToolUse'
          AND r.session_id=p.session_id AND r.tool_use_id=p.tool_use_id
          AND coalesce(json_extract(r.payload,'$.host'),'codex')=coalesce(json_extract(p.payload,'$.host'),'codex'))
        AND NOT EXISTS (SELECT 1 FROM host_receipts r WHERE r.event_name='Reconciled'
          AND json_extract(r.payload,'$.receipt_id')=p.id AND json_extract(r.payload,'$.resolution')!='unknown')''', (episode_id,)).fetchone()[0]


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
    if payload['autonomy'] == 'act':
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
    if payload['state'] == 'done' and not completion(memory, episode['id']):
        step = completion_guidance(memory, card(memory, episode['id']))
        raise InvalidRecord('Done requires current completion evidence. '+step['reason'],
                            episode_id=episode['id'], next_step=step)
    if payload['state']=='done':
        prior=latest(memory,episode['id'],'work_plan')
        if prior and any(prior.get(k,[] if k=='depends_on' else None)!=payload.get(k,[] if k=='depends_on' else None) for k in ('scope','autonomy','depends_on')):
            raise InvalidRecord('Changed scope or dependencies require a new assessment before Done. Save the revised plan in Review first.')


def save(memory, kind, *, payload, actor, evidence, episode_id=None, expected_version=None,
         title=None, objective=None, criterion=None, subject=None, request_key, session_id=None, links=None):
    """Create work and its first plan atomically, or update at an explicit version."""
    from .core import InvalidRecord
    payload = dict(payload)
    if kind == 'work_plan' and payload.get('state') == 'in_progress' and payload.get('owner', 'agent') == 'agent':
        if not session_id:
            raise InvalidRecord('Use the host session_id when claiming work in progress.')
        payload['session_id'] = session_id
    if episode_id:
        if expected_version is None or any(x is not None for x in (title, objective, criterion)):
            raise InvalidRecord('An update needs expected_version. Episode intent is fixed; create linked work for a changed objective.')
        if subject is not None and subject != memory.episode(episode_id)['subject']:
            raise InvalidRecord('A work plan cannot change its episode subject.')
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
        problems.append({'type': 'plan_missing', 'reason': 'This existing episode has no recorded next action or autonomous scope.'})
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
        if any(p['type'] in {'execution_unconfirmed', 'dependency'} for p in problems):
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
    from .reviews import configured, exists, tree_signature
    from subprocess import SubprocessError
    config=configured(memory)
    cache=config and exists(memory) and memory.db.execute('SELECT 1 FROM review_runs LIMIT 1').fetchone()
    if cache:
        try:memory._review_tree=tree_signature(config['project'],cache=getattr(memory,'_review_tree_cache',None))
        except (OSError,SubprocessError):cache=False
    try:return _board(memory,limit=limit,offset=offset,sprint_id=sprint_id,subject=subject,query=query,state=state,episode_id=episode_id,grouped=grouped)
    finally:
        if cache:del memory._review_tree


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


def next_work(memory, *, episode_id=None, session_id=None, limit=5, offset=0, subject=None):
    from . import codex_host
    from .core import InvalidRecord
    state = codex_host.status(memory, session_id, limit=3) if session_id and codex_host.exists(memory) else None
    if not episode_id and state and state['active']:
        episode_id = state['active']['episode_id']
    if not episode_id:
        return {'selection_required': True, 'board': board(memory, limit=limit, offset=offset, subject=subject),
                'next_step': 'Select work that matches the current user request. A queued task is not permission to switch objectives.'}
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
    selected_skills = memory.db.execute("SELECT count(*) FROM sources s WHERE source_key LIKE ? AND version=(SELECT max(version) FROM sources WHERE source_key=s.source_key) AND json_extract(body,'$.state') != 'released'", ('workspace-skill-selection:' + episode_id + ':%',)).fetchone()[0]
    step = completion_guidance(memory,item) if action in {'reconcile','refresh_evidence','assess_coverage','inspect_dependency','review_plan','wait_review','refresh_review','request_review','review_findings','inspect_review','finalize'} else None
    return {'work': item, 'action': action, 'reason': reason, 'update':update,
            **({'next_step':step} if step and step['action']==action else {}),
            'skills': {'selected': selected_skills, 'read_with': {'view': 'skill_selections', 'id': episode_id},
                       'note': 'Read selected versions and conditions before using them. Selection is not execution evidence.'},
            'unconfirmed': state['unconfirmed'] if state and action == 'reconcile' else [],
            'recent_execution': state['recent'] if state and action == 'inspect_execution' else [],
            'requirements': {'version': memory.direction()['version'], 'read_with': 'memory_get requirements'},
            'note': 'This is advisory state, not host permission. Retrieve complete governing requirements and evidence before consequential work.'}
