"""Mechanical omissions and explicit intent assessments over immutable host receipts."""
from . import codex_host, shared
from .core import InvalidRecord, Conflict, _text, _digest

# A tool call that may change something. A call marked read only when it was captured needs no assessment.
# Activity that may change something, made by the main conversation rather than by a subagent.
MATERIAL = "coalesce(json_extract(t.payload,'$.read_only'),0)=0 AND " + shared.MAIN_THREAD.replace('p.', 't.')


def sessions(memory, limit=10, offset=0):
    if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
        raise InvalidRecord('Use limit 1–100 and a nonnegative offset.')
    rows = memory.db.execute("SELECT session_id,max(rowid) AS last FROM host_receipts WHERE event_name IN ('CaptureRecovered','CoverageBlockIssued') OR (event_name='UserPromptSubmit' AND json_extract(payload,'$.coverage_version')=1) GROUP BY session_id ORDER BY last DESC LIMIT ? OFFSET ?", (limit+1,offset)).fetchall()
    values = []
    for row in rows[:limit]:
        state = inspect(memory,row['session_id'],limit=1)
        values.append({k:state[k] for k in ('session_id','status','issues')})
    return {'sessions':values,'more':len(rows)>limit,'next_offset':offset+len(values)}


def work_issues(memory, episode_id):
    if not codex_host.exists(memory):
        return []
    from .planning import latest
    plan = latest(memory,episode_id,'work_plan')
    session = plan.get('session_id') if plan else None
    if not session:
        row = memory.db.execute("SELECT session_id FROM host_receipts WHERE episode_id=? AND event_name='DecisionBound' ORDER BY rowid DESC LIMIT 1",(episode_id,)).fetchone()
        session = row[0] if row else None
    if not session:
        return []
    return [item for item in inspect(memory,session,limit=1)['issues'] if item['type'] in {'intent_unassessed','capture_gap'}]


def inspect(memory, session_id, limit=10, offset=0):
    _text(session_id, 'session_id', 200)
    if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
        raise InvalidRecord('Use limit 1–100 and a nonnegative offset.')
    pending = '''FROM host_receipts p WHERE p.session_id=? AND p.event_name='UserPromptSubmit'
      AND json_extract(p.payload,'$.coverage_version')=1
      AND NOT EXISTS (SELECT 1 FROM host_receipts a, json_each(a.payload,'$.prompt_ids') j
        WHERE a.session_id=p.session_id AND a.event_name='IntentAssessed' AND j.value=p.id)
      AND (p.decision_id IS NOT NULL OR EXISTS (SELECT 1 FROM host_receipts t
        WHERE t.session_id=p.session_id AND t.rowid>p.rowid AND (t.event_name='DecisionBound' OR t.event_name='PreToolUse' AND '''+MATERIAL+''')
        AND NOT EXISTS (SELECT 1 FROM host_receipts n WHERE n.session_id=p.session_id
          AND n.event_name='UserPromptSubmit' AND n.rowid>p.rowid AND n.rowid<t.rowid)))'''
    total = memory.db.execute('SELECT count(*) '+pending, (session_id,)).fetchone()[0]
    prompts = [dict(r) for r in memory.db.execute('SELECT p.id,p.created_at '+pending+' ORDER BY p.rowid LIMIT ? OFFSET ?',
                                                  (session_id, limit, offset))]
    assessment = memory.db.execute("SELECT rowid,id,episode_id,payload FROM host_receipts WHERE session_id=? AND event_name='IntentAssessed' ORDER BY rowid DESC LIMIT 1", (session_id,)).fetchone()
    since = assessment['rowid'] if assessment else 0
    unbound = memory.db.execute("SELECT count(*) FROM host_receipts t WHERE session_id=? AND event_name='PreToolUse' AND decision_id IS NULL AND json_extract(payload,'$.work_item') IS NULL AND rowid>? AND "+MATERIAL, (session_id, since)).fetchone()[0]
    state = codex_host.status(memory, session_id, limit=limit, offset=offset, main_only=True)
    active = state['active']
    open_sql = '''FROM host_receipts b WHERE b.session_id=? AND b.event_name='DecisionBound'
      AND NOT EXISTS (SELECT 1 FROM events e WHERE e.kind='outcome' AND e.decision_id=b.decision_id)
      AND NOT EXISTS (SELECT 1 FROM events e WHERE e.kind='decision' AND e.supersedes=b.decision_id)'''
    open_total = memory.db.execute('SELECT count(DISTINCT b.decision_id) '+open_sql,(session_id,)).fetchone()[0]
    open_decisions = [dict(r) for r in memory.db.execute('SELECT DISTINCT b.decision_id,b.episode_id '+open_sql+' LIMIT ? OFFSET ?', (session_id,limit,offset))]
    issues = []
    if total:
        issues.append({'type':'intent_unassessed', 'count':total, 'reason':'New requests have not been explicitly assessed against the recorded scope.'})
    if unbound:
        issues.append({'type':'activity_unassigned', 'count':unbound, 'reason':'Tool activity has no decision. Record its purpose; an informational lookup may need no work plan.'})
    if active:
        from .planning import latest
        if not latest(memory, active['episode_id'], 'work_plan'):
            issues.append({'type':'plan_missing', 'reason':'The active decision has no work plan.'})
    if open_total:
        issues.append({'type':'outcome_missing', 'count':open_total, 'reason':'Executed or selected decisions have no assessed outcome. Record partial or blocked work accurately.'})
    if state['unconfirmed_total']:
        unassessed=shared.unconfirmed_total(memory,session_id=session_id,clause=shared.UNASSESSED+' AND '+shared.MAIN_THREAD)
        issues.append({'type':'execution_unconfirmed', 'count':state['unconfirmed_total'], 'requires_assessment':bool(unassessed),
                       'reason':'Inspect actual effects before retrying; an absent receipt does not prove failure. Explicitly assessed unknown execution remains unresolved.'})
    gaps = [r[0] for r in memory.db.execute("SELECT id FROM host_receipts WHERE session_id=? AND event_name='CaptureRecovered' AND rowid>? ORDER BY rowid", (session_id, since))]
    if gaps:
        issues.append({'type':'capture_gap', 'count':len(gaps), 'reason':'Capture previously failed. Assess the missing interval against host results.'})
    return {'session_id':session_id, 'status':'needs_attention' if issues else 'recorded', 'issues':issues,
            'pending_prompts':prompts, 'pending_total':total, 'more':offset+len(prompts)<total or offset+len(open_decisions)<open_total or offset+limit<len(gaps) or state['more'],
            'next_offset':offset+max(len(prompts),len(open_decisions),len(state['unconfirmed']),len(gaps[offset:offset+limit])), 'active':active, 'capture_gaps':gaps[offset:offset+limit], 'capture_gaps_total':len(gaps),
            'unconfirmed':state['unconfirmed'], 'unconfirmed_total':state['unconfirmed_total'],
            'open_decisions':open_decisions, 'open_decisions_total':open_total,
            'last_assessment':assessment['id'] if assessment else None,
            'meaning':'This checks recording completeness, not the truth or completeness of the agent interpretation.'}


def assess(memory, *, session_id, request_key, prompt_ids, effect, reason, episode_id=None,
           requirements=None, gap_ids=None, plan_id=None):
    _text(session_id, 'session_id', 200); _text(reason, 'reason', 2000)
    if effect not in {'new_work','changed','unchanged','informational','deferred'}:
        raise InvalidRecord('Use new_work, changed, unchanged, informational or deferred.')
    if not isinstance(prompt_ids, list) or not 1 <= len(prompt_ids) <= 20 or len(set(prompt_ids)) != len(prompt_ids):
        raise InvalidRecord('Assess 1–20 distinct prompt receipt IDs from this session.')
    prompts = [codex_host.read_receipt(memory, rid) for rid in prompt_ids]
    if any(p['session_id'] != session_id or p['event_name'] != 'UserPromptSubmit' for p in prompts):
        raise InvalidRecord('Intent assessments must reference this session’s user prompt receipts.')
    newest = memory.db.execute("SELECT id FROM host_receipts WHERE session_id=? AND event_name='UserPromptSubmit' AND json_extract(payload,'$.notification') IS NULL ORDER BY rowid DESC LIMIT 1", (session_id,)).fetchone()
    if newest and newest[0] not in prompt_ids:
        raise Conflict('A newer request arrived. Read session coverage and assess it before continuing.')
    requirements = [] if requirements is None else requirements
    if not isinstance(requirements, list) or len(requirements)>50:
        raise InvalidRecord('requirements must contain at most 50 complete conditions and exceptions.')
    for text in requirements: _text(text, 'requirement', 2000)
    if gap_ids is not None and (not isinstance(gap_ids,list) or any(not isinstance(rid,str) for rid in gap_ids) or len(set(gap_ids))!=len(gap_ids)):
        raise InvalidRecord('gap_ids must be a list of distinct capture gap IDs.')
    if effect in {'new_work','changed'} and not requirements:
        raise InvalidRecord('New or changed work needs explicit conditions and exceptions in requirements.')
    plan = None
    if episode_id:
        from .planning import latest
        memory.episode(episode_id); plan = latest(memory, episode_id, 'work_plan')
    if effect in {'new_work','changed','unchanged'} and not plan:
        raise InvalidRecord('Work assessments need an existing work plan. Informational turns need no episode.')
    if effect in {'new_work','changed','unchanged'} and plan_id != plan['id']:
        raise Conflict('Read the current work plan and pass its plan_id; a checkpoint cannot acknowledge an unseen revision. Bundled writes supply it automatically.')
    if plan and plan.get('state')=='in_progress' and plan.get('owner','agent')=='agent' and plan.get('session_id') != session_id:
        raise Conflict('Another session owns this work. Explicitly claim its current plan before assessing it.')
    if effect == 'changed' and any(p['payload'].get('plan_id') == plan['id'] for p in prompts):
        raise InvalidRecord('Revise the plan before declaring that changed intent has been recorded.')
    gaps = [r[0] for r in memory.db.execute("SELECT id FROM host_receipts WHERE session_id=? AND event_name='CaptureRecovered' AND rowid>coalesce((SELECT max(rowid) FROM host_receipts WHERE session_id=? AND event_name='IntentAssessed'),0) ORDER BY rowid",(session_id,session_id))]
    if set(gap_ids or []) != set(gaps):
        raise InvalidRecord('Explicitly include every outstanding capture_gaps ID after inspecting the lost interval.')
    payload = {'prompt_ids':prompt_ids, 'effect':effect, 'reason':reason, 'requirements':requirements,
               'plan_id':plan['id'] if plan else None, 'gap_ids':gaps}
    rid=codex_host.receipt(memory, session_id=session_id, event_name='IntentAssessed',
                         episode_id=episode_id, payload=payload, key=request_key+':intent')
    state=inspect(memory,session_id,limit=1)
    return {'id':rid,'status':state['status'],'remaining_issues':[item['type'] for item in state['issues']]}


def hook(memory, event):
    name = event['hook_event_name']; session = event['session_id']
    if name not in {'Stop','SessionStart','PostCompact'}:
        return {}
    state = inspect(memory, session, limit=3)
    if not any(i.get('requires_assessment',True) for i in state['issues']):
        return {}
    reason = 'Project Memory needs assessment: '+', '.join(x['type'].replace('_',' ') for x in state['issues'])+'. '
    reason += f'Use memory_get coverage with session_id {session}. '
    if not state['open_decisions_total']:
        reason += 'For an informational lookup, use memory_write checkpoint with session_id and data {prompt_ids: [the observed prompt IDs], effect: "informational", reason: your explicit assessment}. This needs no plan, decision or outcome. For material work, record its plan and assess new_work with all conditions and exceptions. '
    else:
        reason += 'Assess intent with memory_write checkpoint (or data.checkpoint on a plan/record write). Record actual outcomes, including partial or blocked work. '
    reason += 'Do not repeat effects to repair records. The checkpoint result reports remaining recording issues.'
    if name == 'Stop':
        # One intervention per user prompt, shared with agent reviews. Later stops leave gaps visible.
        prompt = memory.db.execute("SELECT id FROM host_receipts WHERE session_id=? AND event_name='UserPromptSubmit' ORDER BY rowid DESC LIMIT 1", (session,)).fetchone()
        key = 'coverage-block:'+session+':'+(prompt[0] if prompt else str(event.get('turn_id','')))
        rid = 'host_'+_digest(key)[:32]
        if event.get('stop_hook_active') or memory.db.execute('SELECT 1 FROM host_receipts WHERE id=?', (rid,)).fetchone():
            return {}
        with memory._write():
            codex_host.receipt(memory, session_id=session, event_name='CoverageBlockIssued',
                              payload={'prompt_id':prompt[0] if prompt else None,'issues':[i['type'] for i in state['issues']]}, key=key)
        return {'decision':'block', 'reason':reason}
    return {'hookSpecificOutput':{'hookEventName':name, 'additionalContext':reason}}
