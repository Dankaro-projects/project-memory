"""Read functions for the control panel and the offline export.

Every endpoint is `name(memory, params) -> dict`. Parameters arrive as strings
from HTTP, so each function converts and validates them and raises
InvalidRecord for bad values. Endpoints only read: they never create tables and
they work on a read-only connection to a database without the optional tables.

Expensive values are computed once per request. When a caller attaches a
dictionary as `memory._api_cache`, card states and the architecture model are
kept there; the live server resets that dictionary whenever its revision changes.
"""
import json

from . import codex_host
from .core import InvalidRecord
from .graph import EVENT_TITLE_KEYS as TITLE_KEYS, _table_exists as _table
RECORD_VIEWS = ('episodes', 'pending', 'decisions', 'drift', 'documents', 'sources', 'direction', 'research',
                'corrections', 'lessons', 'patterns', 'events', 'captures')
SUMMARY_LIMIT = 10
ATTENTION_LIMIT = 20
GUARD_LIMIT = 50
RUN_REVIEW_LIMIT = 20
CARD_LIMIT = 5000
BODY_SLICE = 12000
FOLLOW_UP_RECEIPTS = ('LessonProposalsNotRecorded', 'HostAvailabilityNotRecorded', 'DelegationFollowUpNotStarted')
PRIORITY_RANK = {'high': 0, 'normal': 1, 'low': 2}


# Parameters.

def _int(params, name, default, low, high):
    value = params.get(name, default)
    if isinstance(value, str):
        if not value.strip().lstrip('-').isdigit():
            raise InvalidRecord(f'{name} must be a whole number from {low} to {high}.')
        value = int(value)
    if type(value) is not int or not low <= value <= high:
        raise InvalidRecord(f'{name} must be a whole number from {low} to {high}.')
    return value


def _text(params, name, required=False, limit=1100):
    value = params.get(name)
    if value in (None, ''):
        if required:
            raise InvalidRecord(f'Provide the {name} parameter.')
        return None
    if not isinstance(value, str) or len(value) > limit:
        raise InvalidRecord(f'{name} must be text of at most {limit} characters.')
    return value


def _flag(params, name, default=False):
    value = params.get(name, default)
    if isinstance(value, bool):
        return value
    if value in ('1', 'true', 'yes'):
        return True
    if value in ('0', 'false', 'no', ''):
        return False
    raise InvalidRecord(f'{name} must be true or false.')


def _cache(memory):
    return getattr(memory, '_api_cache', None)


def _cached(memory, key, compute):
    cache = _cache(memory)
    if cache is None:
        return compute()
    if key not in cache:
        cache[key] = compute()
    return cache[key]


# Records.

def event_title(payload, kind):
    return next((payload[key] for key in TITLE_KEYS if key in payload), kind)


def row(memory, rid, *, body_offset=None):
    """One record of any kind as the panel shows it, with an optional slice of a source body."""
    if not isinstance(rid, str) or not rid:
        raise InvalidRecord('Provide a record id.')
    if rid.startswith('direction_'):
        try:
            version = int(rid.split('_')[1])
        except (IndexError, ValueError) as exc:
            raise InvalidRecord('Unknown project revision.') from exc
        detail = None
        if version == 0:
            detail = {'version': 0, 'requirements': memory.initial_requirements, 'reason': 'Initial project settings.', 'evidence': []}
        elif _table(memory, 'project_revisions'):
            found = memory.db.execute('SELECT version,requirements,reason,actor,evidence,created_at FROM project_revisions WHERE version=?',
                                      (version,)).fetchone()
            if found:
                detail = dict(found)
                for key in ('requirements', 'evidence'):
                    detail[key] = json.loads(detail[key])
        if detail is None:
            raise InvalidRecord('Unknown project revision.')
        current = memory.direction()
        return {'id': rid, 'kind': 'project_revision', 'subject': 'general',
                'status': current.get('status', 'current') if version == current['version'] else 'historical',
                'title': f'Project requirements, version {version}', 'date': detail.get('created_at', ''),
                'episode_id': '', 'detail': detail}
    if rid.startswith('episode_'):
        detail = memory.episode(rid)
        return {'id': rid, 'kind': 'episode', 'subject': detail['subject'], 'status': detail['status'],
                'title': detail['title'], 'date': detail['created_at'], 'episode_id': rid, 'detail': detail}
    if rid.startswith('host_'):
        if not codex_host.exists(memory):
            raise InvalidRecord('Host receipt was not found.')
        detail = codex_host.read_receipt(memory, rid)
        reported = memory.db.execute("""SELECT 1 FROM host_receipts WHERE session_id=? AND tool_use_id=? AND event_name='PostToolUse'
            AND coalesce(json_extract(payload,'$.host'),'codex')=?""",
                                     (detail['session_id'], detail['tool_use_id'], detail['payload'].get('host', 'codex'))).fetchone()
        reconciled = memory.db.execute("""SELECT 1 FROM host_receipts WHERE event_name='Reconciled'
            AND json_extract(payload,'$.receipt_id')=? AND json_extract(payload,'$.resolution')!='unknown'""", (rid,)).fetchone()
        unconfirmed = detail['event_name'] == 'PreToolUse' and not reported and not reconciled
        episode = detail['episode_id']
        return {'id': rid, 'kind': 'host_receipt', 'subject': memory.episode(episode)['subject'] if episode else 'general',
                'status': 'execution_unconfirmed' if unconfirmed else 'observed',
                'title': detail['event_name'] + ': ' + (detail['tool_name'] or 'Host session'),
                'date': detail['created_at'], 'episode_id': episode or '', 'detail': detail}
    detail = memory.read(rid)
    source = detail['kind'] == 'source'
    if source and body_offset is not None:
        body = memory.read(rid, detail=True)['body']
        if body_offset > len(body):
            raise InvalidRecord('body_offset exceeds the source length.')
        detail.update(body=body[body_offset:body_offset + BODY_SLICE], body_offset=body_offset,
                      body_more=body_offset + BODY_SLICE < len(body), next_offset=min(body_offset + BODY_SLICE, len(body)))
    payload = detail.get('payload', {})
    title = detail['title'] if source else event_title(payload, detail['kind'])
    result = {'id': rid, 'kind': detail['kind'], 'subject': detail['subject'], 'status': detail['status'],
              'title': title, 'date': detail.get('checked_at', detail.get('created_at', '')),
              'episode_id': detail.get('episode_id', ''), 'detail': detail}
    if detail['kind'] == 'decision':
        result['episode_title'] = memory.episode(detail['episode_id'])['title']
        outcome = memory.db.execute("SELECT id FROM events WHERE kind='outcome' AND decision_id=? ORDER BY seq DESC LIMIT 1",
                                    (rid,)).fetchone()
        result['outcome'] = row(memory, outcome[0]) if outcome else None
    return result


def _title_sql():
    return 'coalesce(' + ','.join(f"json_extract(payload,'$.{key}')" for key in TITLE_KEYS) + ',kind)'


def page(memory, params):
    """A filtered, ordered page of records for one records view."""
    view = params.get('view', 'episodes')
    limit = _int(params, 'limit', 25, 1, 100)
    offset = _int(params, 'offset', 0, 0, 10**9)
    query = params.get('query', '') or ''
    if not isinstance(query, str) or len(query) > 500:
        raise InvalidRecord('Search is limited to 500 characters.')
    related = _text(params, 'related')
    if related:
        sql = '''SELECT id FROM events WHERE decision_id=? OR supersedes=?
               UNION SELECT event_id FROM dependencies WHERE source_id=?
               UNION SELECT event_id FROM event_links WHERE prior_event_id=?'''
        arguments = (related,) * 4
        total = memory.db.execute('SELECT count(*) FROM (' + sql + ')', arguments).fetchone()[0]
        ids = memory.db.execute('SELECT e.id FROM events e JOIN (' + sql + ') r ON e.id=r.id ORDER BY e.created_at,e.rowid LIMIT ? OFFSET ?',
                                (*arguments, limit, offset)).fetchall()
        return {'records': [row(memory, item[0]) for item in ids], 'total': total, 'offset': offset, 'limit': limit,
                'more': offset + len(ids) < total}
    selections = [
        "SELECT id,'episode' AS kind,subject,id AS episode_id,created_at AS date,title,title||' '||objective AS text FROM episodes",
        'SELECT id,kind,subject,episode_id,created_at AS date,' + _title_sql() + ' AS title,payload AS text FROM events',
        "SELECT id,'source' AS kind,subject,'' AS episode_id,checked_at AS date,title,title||' '||summary||' '||body AS text FROM sources"]
    if codex_host.exists(memory):
        selections.append("""SELECT id,'host_receipt' AS kind,coalesce((SELECT subject FROM episodes WHERE episodes.id=host_receipts.episode_id),'general') AS subject,
            coalesce(episode_id,'') AS episode_id,created_at AS date,event_name||': '||tool_name AS title,payload AS text FROM host_receipts""")
    if view == 'direction':
        selections = ["SELECT 'direction_0' AS id,'project_revision' AS kind,'general' AS subject,'' AS episode_id,'' AS date,"
                      "'Project requirements, version 0' AS title,(SELECT value FROM settings WHERE key='requirements') AS text"]
        if memory.direction()['version']:
            selections.append("SELECT 'direction_'||version,'project_revision','general','',created_at,"
                              "'Project requirements, version '||version,requirements||' '||reason FROM project_revisions")
    clauses = []
    arguments = []
    kinds = {'episodes': ['episode'], 'decisions': ['decision'], 'sources': ['source'], 'documents': ['source'],
             'lessons': ['lesson'], 'research': ['research'], 'corrections': ['correction'], 'patterns': ['lesson'],
             'captures': ['host_receipt'], 'pending': ['decision']}
    if view in kinds:
        clauses.append('kind IN (' + ','.join('?' for _ in kinds[view]) + ')')
        arguments.extend(kinds[view])
    elif view == 'events':
        clauses.append("kind NOT IN ('episode','source','host_receipt')")
    elif view not in {'direction', 'drift'}:
        raise InvalidRecord('Unknown records view. Use one of: ' + ', '.join(RECORD_VIEWS) + '.')
    filters = [('subject', params.get('subject'), '='), ('episode_id', params.get('episode'), '='),
               ('date', params.get('from'), '>='), ('substr(date,1,10)', params.get('to'), '<='),
               ('date', params.get('since'), '>='), ('date', params.get('until'), '<=')]
    for column, value, operator in filters:
        if value:
            if not isinstance(value, str) or len(value) > 200:
                raise InvalidRecord('Record filters must be short text values.')
            clauses.append(column + operator + '?')
            arguments.append(value)
    if query:
        clauses.append('(instr(lower(text),lower(?))>0 OR instr(lower(title),lower(?))>0 OR id=?)')
        arguments.extend([query, query, query])
    order = {'newest': 'date DESC,id', 'oldest': 'date,id', 'title': 'title COLLATE NOCASE,id'}.get(params.get('order', 'newest'))
    if not order:
        raise InvalidRecord('Unknown sort order. Use newest, oldest or title.')
    where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
    union = ' UNION ALL '.join(selections)
    sql = 'SELECT id FROM (' + union + ')' + where + ' ORDER BY ' + order
    pending = {}
    status = params.get('status')
    derived = view in {'pending', 'drift', 'documents', 'patterns'} or bool(status)
    if not derived:
        total = memory.db.execute('SELECT count(*) FROM (' + union + ')' + where, arguments).fetchone()[0]
        records = [row(memory, item[0]) for item in memory.db.execute(sql + ' LIMIT ? OFFSET ?', (*arguments, limit, offset)).fetchall()]
    else:
        # Derived states use the same domain rules as MCP. List pages never carry source bodies.
        total = 0
        records = []
        for found in memory.db.execute(sql, arguments).fetchall():
            record = row(memory, found[0])
            detail = record['detail']
            if view == 'pending':
                values = memory.pending(decision_id=record['id'])['decisions']
                if not values:
                    continue
                pending[record['id']] = values[0]
            if view == 'drift' and record['status'] not in {'needs_review', 'review_due', 'superseded', 'file_changed',
                                                             'file_missing', 'file_unreadable'}:
                continue
            if view == 'documents' and detail.get('origin') != 'document':
                continue
            if view == 'patterns' and detail.get('payload', {}).get('pattern_type') not in {'anti_pattern', 'practice', 'recovery'}:
                continue
            state = pending[record['id']]['state'] if view == 'pending' else record['status']
            if status and state != status:
                continue
            if offset <= total < offset + limit:
                records.append(record)
            elif view == 'pending':
                pending.pop(record['id'], None)
            total += 1
    return {'records': records, 'pending': [pending[r['id']] for r in records if r['id'] in pending], 'total': total,
            'offset': offset, 'limit': limit, 'more': offset + len(records) < total, 'source_bodies_included': False}


def latest_decisions(memory, limit=3):
    """One current decision per work item, newest first; earlier decisions stay in the records."""
    selection = """FROM events e WHERE e.kind='decision' AND NOT EXISTS
        (SELECT 1 FROM events n WHERE n.kind='decision' AND n.episode_id=e.episode_id AND n.seq>e.seq)"""
    total = memory.db.execute('SELECT count(*) ' + selection).fetchone()[0]
    ids = memory.db.execute('SELECT e.id ' + selection + ' ORDER BY e.created_at DESC,e.rowid DESC LIMIT ?', (limit,)).fetchall()
    return {'records': [row(memory, item[0]) for item in ids], 'total': total}


# Work state.

def cards(memory):
    """Every work item card, computed once per request. Sprints are not work items."""
    def compute():
        from .planning import card
        from .reviews import shared_tree
        rows = memory.db.execute("SELECT id FROM episodes WHERE task_type!='sprint' ORDER BY created_at,id LIMIT ?",
                                 (CARD_LIMIT + 1,)).fetchall()
        with shared_tree(memory):
            values = {item[0]: card(memory, item[0]) for item in rows[:CARD_LIMIT]}
        return {'cards': values, 'truncated': len(rows) > CARD_LIMIT}
    return _cached(memory, 'cards', compute)


def card_states(memory):
    """Map each work item id to its evidence checked state."""
    return {episode_id: item['state'] for episode_id, item in cards(memory)['cards'].items()}


def card_summary(item):
    plan = item['plan'] or {}
    return {'id': item['id'], 'title': item['title'], 'subject': item['subject'], 'state': item['state'],
            'priority': plan.get('priority', 'normal'), 'next_action': plan.get('next_action'),
            'issues': len(item['issues']), 'owner': plan.get('owner', 'agent') if plan else None,
            'item_type': plan.get('item_type', 'task')}


def _receipts(memory, names, limit, episode_id=None):
    if not codex_host.exists(memory):
        return []
    marks = ','.join('?' for _ in names)
    sql = 'SELECT id FROM host_receipts WHERE event_name IN (' + marks + ')'
    arguments = list(names)
    if episode_id:
        sql += ' AND episode_id=?'
        arguments.append(episode_id)
    rows = memory.db.execute(sql + ' ORDER BY rowid DESC LIMIT ?', (*arguments, limit)).fetchall()
    return [codex_host.read_receipt(memory, item[0]) for item in rows]


def active_runs(memory):
    from . import delegation, reviews
    return [delegation.summary(memory, run) for run in reviews.active_runs(memory)]


def awaiting_merge(memory, limit=50):
    """Completed delegated work with changed files that is neither merged nor discarded."""
    from . import delegation, reviews
    if not reviews.exists(memory):
        return []
    rows = memory.db.execute("SELECT id FROM review_runs WHERE role='work' AND state='completed' ORDER BY rowid DESC LIMIT ?",
                             (limit,)).fetchall()
    summaries = [delegation.summary(memory, reviews.read(memory, item[0])) for item in rows]
    return [summary for summary in summaries if summary['changed_files'] and not summary['merge']]


def scope_blocks(memory, limit=5):
    """Recent edits that hooks blocked because they were outside the recorded paths of the work."""
    from . import guards
    from .planning import latest
    result = []
    found = _receipts(memory, ('ScopeBlocked',), limit)
    root = guards.project_root(memory) if found else None
    for receipt in found:
        payload = receipt['payload']
        episode_id = payload.get('episode_id') or receipt['episode_id']
        plan = latest(memory, episode_id, 'work_plan') if episode_id else None
        allowed = (plan or {}).get('paths') or []
        blocked = list(payload.get('blocked', []))
        still = [path for path in blocked if not guards.match_path(path, allowed, root=root)] if allowed else blocked
        result.append({'id': receipt['id'], 'created_at': receipt['created_at'], 'episode_id': episode_id,
                       'plan_id': payload.get('plan_id'), 'tool_name': payload.get('tool_name') or receipt['tool_name'],
                       'host': payload.get('host'), 'blocked': payload.get('blocked', []),
                       'allowed_patterns': payload.get('allowed_patterns', []), 'still_outside': still})
    return result


def proposed_lesson_ids(memory):
    rows = memory.db.execute("""SELECT l.id FROM events l WHERE l.kind='lesson'
        AND NOT EXISTS (SELECT 1 FROM events n WHERE n.supersedes=l.id)
        AND NOT EXISTS (SELECT 1 FROM events r WHERE r.kind='lesson_review' AND json_extract(r.payload,'$.lesson_id')=l.id)
        ORDER BY l.rowid DESC""").fetchall()
    return [item[0] for item in rows]


def follow_up_attention(memory, limit=ATTENTION_LIMIT):
    """Agent follow ups that could not be recorded and are not yet resolved by a later record."""
    result = []
    for receipt in _receipts(memory, FOLLOW_UP_RECEIPTS, 200):
        payload = receipt['payload']
        name = receipt['event_name']
        run_id = payload.get('run_id')
        if name == 'LessonProposalsNotRecorded':
            if memory.db.execute('SELECT 1 FROM events WHERE request_key LIKE ?', ('lesson-proposal:' + str(run_id) + ':%',)).fetchone():
                continue
            reason = 'The lesson proposals of an agent run were not recorded: ' + payload.get('error', 'no reason was given.')
        elif name == 'HostAvailabilityNotRecorded':
            later = memory.db.execute("""SELECT 1 FROM host_receipts WHERE session_id=? AND event_name IN ('HostAvailable','HostUnavailable')
                AND rowid>(SELECT rowid FROM host_receipts WHERE id=?)""", ('host:' + str(payload.get('host')), receipt['id'])).fetchone()
            if later:
                continue
            reason = 'The availability of the ' + str(payload.get('host')) + ' host was not recorded: ' + payload.get('error', 'no reason was given.')
        else:
            # This receipt is written only for delegated work, whose runs table has the parent_run column.
            if memory.db.execute('SELECT 1 FROM review_runs WHERE parent_run=?', (run_id,)).fetchone():
                continue
            reason = 'The follow up of a delegated run did not start: ' + payload.get('error', 'no reason was given.')
        result.append({'type': 'agent_follow_up', 'receipt': name, 'reason': reason, 'id': receipt['id'], 'run_id': run_id,
                       'episode_id': receipt['episode_id'], 'created_at': receipt['created_at']})
        if len(result) >= limit:
            break
    return result


def _decision_summary(record):
    outcome = record.get('outcome')
    return {'id': record['id'], 'title': record['title'], 'episode_id': record['episode_id'],
            'episode_title': record.get('episode_title'), 'date': record['date'], 'status': record['status'],
            'outcome': {'id': outcome['id'], 'assessment': outcome['detail']['payload'].get('assessment'),
                        'completion': outcome['detail']['payload'].get('completion'), 'status': outcome['status']} if outcome else None}


def _sorted_summaries(items):
    ordered = sorted(items, key=lambda item: (PRIORITY_RANK.get((item['plan'] or {}).get('priority', 'normal'), 1), item['date']))
    return [card_summary(item) for item in ordered[:SUMMARY_LIMIT]]


# Endpoints.

def health(memory, params):
    """Database, capture and baseline health with configured clients and host availability."""
    from . import reviews
    from .health import inspect
    from .install import read_state
    value = inspect(memory)
    clients = None
    try:
        state = read_state(memory.path.parent / 'install.json')
        if state and state.get('database') == str(memory.path):
            clients = sorted(state['clients'])
    except (OSError, ValueError, KeyError, TypeError):
        clients = None
    episodes = [dict(item) for item in memory.db.execute('SELECT id,title FROM episodes ORDER BY created_at DESC LIMIT 1000')]
    return {**value, 'requirements': memory.requirements, 'clients': clients, 'review_host': reviews.configured(memory),
            'hosts': host_overview(memory)['hosts'],
            'episodes': episodes, 'episodes_more': memory.db.execute('SELECT count(*) FROM episodes').fetchone()[0] > 1000}


def now(memory, params):
    """The current state of the project: work, attention items, agents, decisions and learning counts."""
    from . import guards
    from .capture_errors import summary as capture_summary
    from .coverage import sessions
    from .planning import STATES
    found = cards(memory)
    items = list(found['cards'].values())
    counts = dict.fromkeys(STATES, 0)
    for item in items:
        counts[item['state']] += 1
    by_state = {state: [item for item in items if item['state'] == state] for state in ('in_progress', 'blocked', 'review', 'ready')}
    attention = []
    for block in scope_blocks(memory, limit=5):
        outside = block['still_outside']
        if outside:
            attention.append({'type': 'scope_block', 'id': block['id'], 'episode_id': block['episode_id'],
                              'reason': 'An edit was blocked because ' + ', '.join(outside[:5]) + (' is' if len(outside) == 1 else ' are') +
                                        ' outside the recorded paths of the work item. Allow the paths or keep the block.'})
    for item in by_state['blocked']:
        first = item['issues'][0]['reason'] if item['issues'] else (item['plan'] or {}).get('reason', 'The work item is blocked.')
        attention.append({'type': 'blocked_work', 'id': item['id'], 'reason': item['title'] + ' is blocked. ' + first})
    for run in awaiting_merge(memory, limit=20):
        review = (run.get('review') or {}).get('state', 'missing').replace('_', ' ')
        attention.append({'type': 'awaiting_merge', 'id': run['id'], 'episode_id': run['episode_id'],
                          'reason': f'Delegated work changed {run["changed_files"]} files and awaits a merge decision. Its review is {review}.'})
    attention.extend(follow_up_attention(memory, limit=10))
    recurring = guards.recurrences(memory, limit=50)
    for entry in recurring:
        attention.append({'type': 'guard_recurrence', 'id': entry['lesson_id'],
                          'reason': f'The failure type {entry["failure_type"]} occurred {entry["total"]} times after the lesson was accepted.'})
    failures = guards.failures_without_lesson(memory, limit=1000)
    for entry in failures[:10]:
        attention.append({'type': 'failure_without_lesson', 'id': entry['outcome_id'], 'episode_id': entry['episode_id'],
                          'reason': 'A failed outcome in ' + entry['title'] + ' has no lesson and no later complete result.'})
    failure = capture_summary(memory.path)
    if failure:
        attention.append({'type': 'capture_failure', 'id': None, 'sessions': failure['sessions'],
                          'reason': f'{failure["count"]} host events could not be recorded. The next session start recovers them as capture gaps.'})
    if codex_host.exists(memory):
        for session in sessions(memory, limit=5)['sessions']:
            if session['status'] != 'recorded':
                attention.append({'type': 'recording_gap', 'id': session['session_id'],
                                  'reason': 'A host session has recording issues: ' + ', '.join(issue['type'].replace('_', ' ') for issue in session['issues']) + '.'})
    for item in by_state['review']:
        attention.append({'type': 'work_to_review', 'id': item['id'], 'reason': item['title'] + ' needs review before it can continue or finish.'})
    lessons = proposed_lesson_ids(memory)
    if lessons:
        attention.append({'type': 'lessons_to_accept', 'id': lessons[0], 'count': len(lessons),
                          'reason': f'{len(lessons)} proposed lessons await your acceptance or rejection.'})
    runs = active_runs(memory)
    from . import delegation
    recent = delegation.runs(memory, limit=5)['runs']
    result = {'project': memory.project, 'counts': counts, 'total': len(items), 'truncated': found['truncated'],
              'in_progress': _sorted_summaries(by_state['in_progress']), 'blocked': _sorted_summaries(by_state['blocked']),
              'review': _sorted_summaries(by_state['review']), 'ready': _sorted_summaries(by_state['ready']),
              'attention': attention[:ATTENTION_LIMIT], 'attention_total': len(attention),
              'agents': {'active': runs, 'recent': recent},
              'latest_decisions': [_decision_summary(record) for record in latest_decisions(memory, limit=5)['records']],
              'scope_blocks': scope_blocks(memory, limit=5), 'scope_changes': guards.scope_changes(memory, limit=5),
              'lessons_to_accept': len(lessons), 'failures_without_lesson': len(failures),
              'recurrences': sum(entry['total'] for entry in recurring)}
    from .templates import setting
    template = setting(memory)
    if template:
        from .templates import _baseline
        result['kickoff'] = {'template': template['template'], 'baseline': _baseline(memory)['status'],
                             'read_with': 'api/kickoff'}
    return result


def board(memory, params):
    """Work item cards filtered by subject, sprint, state, search text or one work item."""
    from .planning import board as planning_board
    return planning_board(memory, limit=_int(params, 'limit', 25, 1, 100), offset=_int(params, 'offset', 0, 0, 10**9),
                          subject=_text(params, 'subject', limit=50), query=_text(params, 'query', limit=500) or '',
                          sprint_id=_text(params, 'sprint_id', limit=200), state=_text(params, 'state', limit=50),
                          episode_id=_text(params, 'episode', limit=200) or _text(params, 'episode_id', limit=200),
                          grouped=_flag(params, 'grouped'))


def sprints(memory, params):
    """A page of sprints with their schedules, used by the sprint filter of the work view."""
    from .planning import sprints as planning_sprints
    return planning_sprints(memory, _int(params, 'limit', 25, 1, 100), _int(params, 'offset', 0, 0, 10**9),
                            _text(params, 'episode', limit=200))


def work(memory, params):
    """One work item: its card, next step, lineage, agent runs, checks and a page of history."""
    from . import delegation, graph, reviews
    from .planning import next_work
    episode_id = _text(params, 'id', required=True, limit=200)
    offset = _int(params, 'offset', 0, 0, 10**9)
    limit = _int(params, 'limit', 50, 1, 100)
    check_offset = _int(params, 'check_offset', 0, 0, 10**9)
    episode = memory.episode(episode_id)
    if episode['task_type'] == 'sprint':
        raise InvalidRecord('A sprint is not a work item. Read it with the board sprint filter.')
    # Reuse the cards of this request when they are cached; a single read computes only this card.
    item = cards(memory)['cards'].get(episode_id) if _cache(memory) is not None else None
    with reviews.shared_tree(memory):
        if item is None:
            from .planning import card
            item = card(memory, episode_id)
        step = next_work(memory, episode_id=episode_id)
        checks = reviews.listing(memory, episode_id, limit=10, offset=check_offset)
    for run in checks['runs']:
        snapshot = json.loads(memory.db.execute('SELECT snapshot FROM review_runs WHERE id=?', (run['id'],)).fetchone()[0])
        run['conditions'] = {c['id']: c['condition'] for c in snapshot.get('checklist', []) + snapshot.get('constraints', [])}
    total = memory.db.execute('SELECT count(*) FROM events WHERE episode_id=?', (episode_id,)).fetchone()[0]
    ids = memory.db.execute('SELECT id FROM events WHERE episode_id=? ORDER BY seq LIMIT ? OFFSET ?', (episode_id, limit, offset)).fetchall()
    history = {'records': [row(memory, rid[0]) for rid in ids], 'total': total, 'offset': offset, 'limit': limit,
               'more': offset + len(ids) < total}
    return {'card': item, 'next': {key: step.get(key) for key in ('action', 'reason', 'next_step')},
            'lineage': graph.lineage(memory, episode_id, depth=2, limit=60),
            'runs': delegation.runs(memory, episode_id=episode_id, limit=20), 'reviews': checks, 'history': history}


def record(memory, params):
    """One record; a source carries the body slice that starts at body_offset."""
    rid = _text(params, 'id', required=True, limit=300)
    return {'record': row(memory, rid, body_offset=_int(params, 'body_offset', 0, 0, 10**12))}


def lineage(memory, params):
    """The recorded and derived neighbourhood of one record, bounded by depth and node count."""
    from . import graph
    focus = _text(params, 'id', required=True, limit=1100)
    return graph.lineage(memory, focus, depth=_int(params, 'depth', 3, 1, 10), limit=_int(params, 'limit', 80, 1, 500))


def work_graph(memory, params):
    """Work items and the dependencies between them, with evidence checked states."""
    from . import graph
    return graph.work_graph(memory, states=card_states(memory), limit=_int(params, 'limit', 300, 1, 5000))


def _layers(params):
    value = params.get('layers')
    if value in (None, ''):
        return None
    if isinstance(value, str):
        return [part.strip() for part in value.split(',') if part.strip()]
    if isinstance(value, (list, tuple)):
        return list(value)
    raise InvalidRecord('layers must be a comma separated list.')


def architecture(memory, params):
    """The structure of the project in layers: extracted code, exported workflows and authored components."""
    from . import architecture as structure
    level = _text(params, 'level', limit=20) or 'component'
    focus = _text(params, 'focus', limit=1100)
    layers = _layers(params)
    key = ('architecture', level, focus, tuple(layers) if layers else None)

    def compute():
        return structure.model(memory, level=level, focus=focus, work_states=card_states(memory),
                               cache=getattr(memory, '_architecture_cache', None), layers=layers)
    return _cached(memory, key, compute)


def _lesson_entry(memory, lesson_id):
    lesson = memory.read(lesson_id)
    episode = memory.episode(lesson['episode_id'])
    evidence = []
    for ref in lesson['evidence']:
        source = memory.db.execute('SELECT title,origin FROM sources WHERE id=?', (ref['source_id'],)).fetchone()
        evidence.append({'source_id': ref['source_id'], 'reason': ref['reason'],
                         'title': source['title'] if source else None, 'origin': source['origin'] if source else None})
    payload = lesson['payload']
    return {'id': lesson['id'], 'episode_id': lesson['episode_id'], 'episode_title': episode['title'],
            'episode_version': episode['version'], 'subject': lesson['subject'], 'created_at': lesson['created_at'],
            'actor': lesson.get('actor'), 'status': lesson['status'],
            **{key: payload.get(key) for key in ('when', 'do', 'because', 'exceptions', 'pattern_type', 'paths', 'keywords', 'failure_type')},
            'evidence': evidence, 'links': lesson.get('links', [])}


def learning(memory, params):
    """Accepted guards with recurrence counts, proposed lessons, failures without lessons, scope changes and signals."""
    from . import guards
    limit = _int(params, 'limit', 25, 1, 100)
    offset = _int(params, 'offset', 0, 0, 10**9)
    recurring = guards.recurrences(memory, limit=50)
    counts = {entry['lesson_id']: entry['total'] for entry in recurring}
    accepted = guards.active_guards(memory)
    active = [{**guard, 'recurrences': counts.get(guard['lesson_id'], 0)} for guard in accepted[:GUARD_LIMIT]]
    proposed = proposed_lesson_ids(memory)
    return {'guards': active, 'guards_total': len(accepted), 'guards_more': len(accepted) > GUARD_LIMIT, 'recurrences': recurring,
            'proposed_lessons': {'lessons': [_lesson_entry(memory, lesson_id) for lesson_id in proposed[offset:offset + limit]],
                                 'total': len(proposed), 'offset': offset, 'more': offset + limit < len(proposed)},
            'failures_without_lesson': guards.failures_without_lesson(memory, limit=50),
            'scope_changes': guards.scope_changes(memory, limit=20), 'signals': memory.signals(limit=20)}


def host_overview(memory):
    """Whether agent hosts are configured, and the availability of each configured host."""
    from . import hosts, reviews
    config = reviews.configured(memory)
    return {'configured': bool(config), 'hosts': [hosts.availability(memory, host) for host in (config or {}).get('hosts', [])]}


def agents(memory, params):
    """Host availability, active runs, a page of runs and agent follow ups that need attention."""
    from . import delegation
    episode_id = _text(params, 'episode', limit=200)
    return {**host_overview(memory),
            'active': active_runs(memory),
            'runs': delegation.runs(memory, episode_id=episode_id, limit=_int(params, 'limit', 20, 1, 100),
                                    offset=_int(params, 'offset', 0, 0, 10**9)),
            'attention': follow_up_attention(memory)}


def run(memory, params):
    """One agent run with its report, metrics, diff source, plan paths, reviews and merge state, without the snapshot."""
    from . import delegation, reviews
    run_id = _text(params, 'id', required=True, limit=200)
    value = reviews.read(memory, run_id)
    snapshot = value.pop('snapshot') or {}
    result = {**value, **delegation.summary(memory, value), 'paths': snapshot.get('paths'),
              'diff_source': (value['metrics'] or {}).get('diff_source')}
    if value['role'] == 'work':
        rows = memory.db.execute("SELECT id FROM review_runs WHERE parent_run=? AND role='work_review' ORDER BY rowid DESC LIMIT ?",
                                 (run_id, RUN_REVIEW_LIMIT)).fetchall()
        result['reviews'] = [delegation.summary(memory, reviews.read(memory, item[0])) for item in rows]
    return {'run': result}


def requirements(memory, params):
    """Approval metadata of the requirement revisions and a page of the requirement text."""
    from .direction import items, overview
    version = params.get('version')
    version = None if version in (None, '') else _int(params, 'version', 0, 0, 10**6)
    value = overview(memory, limit=_int(params, 'revision_limit', 10, 1, 100), offset=_int(params, 'revision_offset', 0, 0, 10**6))
    value['items'] = items(memory, limit=_int(params, 'limit', 25, 1, 100), offset=_int(params, 'offset', 0, 0, 10**6), version=version)
    return value


def coverage(memory, params):
    """Recording completeness per host session, or the detail of one session."""
    from .coverage import inspect, sessions
    limit = _int(params, 'limit', 10, 1, 100)
    offset = _int(params, 'offset', 0, 0, 10**9)
    session_id = _text(params, 'session_id', limit=200)
    if not codex_host.exists(memory):
        if session_id:
            raise InvalidRecord('No host activity is recorded in this project.')
        return {'sessions': [], 'more': False, 'next_offset': offset}
    return inspect(memory, session_id, limit, offset) if session_id else sessions(memory, limit, offset)


def kickoff(memory, params):
    """The kickoff checklist of a project created from a template."""
    from .templates import kickoff as template_kickoff
    return template_kickoff(memory)


def plan(memory, params):
    """The hierarchy of phases, epics, stories, research, deliverables and workflows with progress roll ups."""
    from .planning import hierarchy
    return hierarchy(memory, root=_text(params, 'root', limit=200), limit=_int(params, 'limit', 500, 1, 5000),
                     states=card_states(memory))


def components(memory, params):
    """Authored components with their status, links and attached work."""
    from .architecture import components as authored
    return authored(memory, status=_text(params, 'status', limit=20), kind=_text(params, 'kind', limit=40),
                    limit=_int(params, 'limit', 100, 1, 500), offset=_int(params, 'offset', 0, 0, 10**9))


ENDPOINTS = {
    'health': health, 'now': now, 'board': board, 'sprints': sprints, 'work': work, 'records': page, 'record': record, 'run': run,
    'lineage': lineage, 'work_graph': work_graph, 'architecture': architecture, 'learning': learning,
    'agents': agents, 'requirements': requirements, 'coverage': coverage, 'kickoff': kickoff, 'plan': plan,
    'components': components,
}
