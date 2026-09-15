"""Typed links between records and one edge query over the recorded history.

Explicit links are append only. A link is retired by adding a new row that names
it; both rows remain. Derived edges are computed from existing tables and are
never stored. Every read function works on a read-only connection and on a
database that has no links table.
"""
import json
import posixpath
import re
import uuid

from .core import Conflict, InvalidRecord, _digest, _text, dumps

LINK_TYPES = ('depends_on', 'blocks', 'implements', 'affects_component', 'relates_to', 'caused_by', 'learned_from', 'annotates',
              'uses', 'produces', 'part_of', 'owns', 'informs')
AUTHORED_PREFIX = 'component:'
SERVICE_NAME = re.compile(r'[a-z0-9][a-z0-9._-]{0,99}')
EVENT_TITLE_KEYS = ('decision', 'summary', 'observed', 'question', 'do', 'text', 'next_action', 'reason', 'action')
DECISION_KINDS = ('action', 'action_result', 'outcome', 'follow_up')
CHECK_ROLES = ('outcome', 'intent', 'recovery', 'work_review')
TITLE_LIMIT = 300
EDGE_LIMIT_PER_LEVEL = 2000
EDGE_LIMIT_WORK_GRAPH = 5000

SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS links (
 id TEXT PRIMARY KEY, from_id TEXT NOT NULL, to_id TEXT NOT NULL, type TEXT NOT NULL,
 reason TEXT NOT NULL CHECK(length(trim(reason))>0), actor TEXT NOT NULL, created_at TEXT NOT NULL,
 request_key TEXT NOT NULL UNIQUE, signature TEXT NOT NULL, retires TEXT REFERENCES links(id),
 CHECK(from_id != to_id)
)''',
    'CREATE INDEX IF NOT EXISTS links_from ON links(from_id,type)',
    'CREATE INDEX IF NOT EXISTS links_to ON links(to_id,type)',
    'CREATE UNIQUE INDEX IF NOT EXISTS links_retired ON links(retires) WHERE retires IS NOT NULL',
    '''CREATE TRIGGER IF NOT EXISTS immutable_graph_links_update BEFORE UPDATE ON links BEGIN
 SELECT RAISE(ABORT, 'Links cannot be changed; add a retiring link.'); END''',
    '''CREATE TRIGGER IF NOT EXISTS immutable_graph_links_delete BEFORE DELETE ON links BEGIN
 SELECT RAISE(ABORT, 'Links cannot be deleted; add a retiring link.'); END''',
)

PACKAGE_ECOSYSTEM = re.compile(r'[A-Za-z0-9_.+-]{1,50}')
PACKAGE_NAME = re.compile(r'[^\s\x00-\x1f]{1,300}')


def initialize(memory):
    """Create the links table. Idempotent and safe inside an open write transaction."""
    with memory._write():
        for statement in SCHEMA:
            memory.db.execute(statement)


def _table_exists(memory, name):
    row = memory.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    return bool(row)


def _columns(memory, table):
    return {row[1] for row in memory.db.execute('PRAGMA table_info(' + table + ')')}


def _component_path(path):
    """Return the path when it is a canonical relative POSIX path, otherwise raise."""
    if not isinstance(path, str) or not path or len(path) > 1000:
        raise InvalidRecord('A component id needs a relative path of at most 1000 characters.')
    invalid = ('\\' in path or path.startswith('/') or re.match(r'^[A-Za-z]:', path)
               or any(ord(ch) < 32 for ch in path) or '..' in path.split('/')
               or posixpath.normpath(path) != path)
    if invalid:
        raise InvalidRecord('A component id must be component: followed by a normalized relative POSIX path without a leading slash or "..". Use "." for the project root.')
    return path


def _package_parts(rest):
    ecosystem, separator, name = rest.partition(':')
    if not separator or not PACKAGE_ECOSYSTEM.fullmatch(ecosystem) or not PACKAGE_NAME.fullmatch(name):
        raise InvalidRecord('A package id must be package:<ecosystem>:<name> without spaces.')
    return ecosystem, name


def _title(text):
    text = str(text)
    if len(text) > TITLE_LIMIT:
        return text[:TITLE_LIMIT - 3] + '...'
    return text


def _episode_subject(memory, episode_id):
    if not episode_id:
        return 'general'
    row = memory.db.execute('SELECT subject FROM episodes WHERE id=?', (episode_id,)).fetchone()
    return row[0] if row else 'general'


def node(memory, id):
    """Describe one graph endpoint. Unknown ids raise InvalidRecord."""
    if not isinstance(id, str) or not id or len(id) > 1100:
        raise InvalidRecord('A graph node id must be nonempty text.')
    if id.startswith('episode_'):
        episode = memory.episode(id)
        return {'id': id, 'kind': 'episode', 'title': _title(episode['title']), 'status': episode['status'],
                'subject': episode['subject'], 'date': episode['created_at'], 'episode_id': id}
    if id.startswith('event_') or id.startswith('source_'):
        value = memory.read(id)
        if value['kind'] == 'source':
            return {'id': id, 'kind': 'source', 'title': _title(value['title']), 'status': value['status'],
                    'subject': value['subject'], 'date': value['checked_at'], 'episode_id': None}
        payload = value['payload']
        title = next((payload[key] for key in EVENT_TITLE_KEYS if key in payload), value['kind'].replace('_', ' '))
        return {'id': id, 'kind': value['kind'], 'title': _title(title), 'status': value['status'],
                'subject': value['subject'], 'date': value['created_at'], 'episode_id': value['episode_id']}
    if id.startswith('host_'):
        if not _table_exists(memory, 'host_receipts'):
            raise InvalidRecord('Host receipt was not found.')
        from .codex_host import read_receipt
        receipt = read_receipt(memory, id)
        title = receipt['event_name'] + ' receipt'
        if receipt['tool_name']:
            title += ' for ' + receipt['tool_name']
        episode_id = receipt['episode_id'] or receipt['payload'].get('episode_id')
        return {'id': id, 'kind': 'receipt', 'title': _title(title), 'status': 'recorded',
                'subject': _episode_subject(memory, episode_id), 'date': receipt['created_at'], 'episode_id': episode_id}
    if id.startswith('check_'):
        row = None
        if _table_exists(memory, 'review_runs'):
            row = memory.db.execute('SELECT id,episode_id,role,state,created_at FROM review_runs WHERE id=?', (id,)).fetchone()
        if row is None:
            raise InvalidRecord('The agent check was not found.')
        title = 'Delegated work run' if row['role'] == 'work' else row['role'].replace('_', ' ').capitalize() + ' check'
        return {'id': id, 'kind': 'check', 'title': title, 'status': row['state'],
                'subject': _episode_subject(memory, row['episode_id']), 'date': row['created_at'], 'episode_id': row['episode_id']}
    if id.startswith('direction_'):
        number = id[len('direction_'):]
        if not re.fullmatch(r'0|[1-9][0-9]{0,8}', number):
            raise InvalidRecord('A direction id must be direction_ followed by a version number.')
        from .direction import current, revision_at
        revision = revision_at(memory, int(number))
        status = revision.get('status', 'current') if revision['version'] == current(memory)['version'] else 'superseded'
        return {'id': id, 'kind': 'direction', 'title': 'Project direction version ' + number, 'status': status,
                'subject': 'general', 'date': revision.get('created_at'), 'episode_id': None}
    if id.startswith('component:'):
        path = _component_path(id[len('component:'):])
        authored = authored_component(memory, id)
        if authored:
            return {'id': id, 'kind': 'component', 'title': _title(authored['title']), 'status': authored['status'],
                    'subject': 'general', 'date': authored['updated_at'], 'episode_id': None}
        if path.startswith('n8n:'):
            title = _workflow_title(memory, path[len('n8n:'):]) or path[len('n8n:'):]
            return {'id': id, 'kind': 'component', 'title': _title(title), 'status': None,
                    'subject': 'general', 'date': None, 'episode_id': None}
        title = memory.project if path == '.' else path
        return {'id': id, 'kind': 'component', 'title': _title(title), 'status': None,
                'subject': _component_subject(memory, path), 'date': None, 'episode_id': None}
    if id.startswith('service:'):
        name = id[len('service:'):]
        if not SERVICE_NAME.fullmatch(name):
            raise InvalidRecord('A service id must be service: followed by a lowercase name of letters, digits, dots, hyphens or underscores.')
        return {'id': id, 'kind': 'service', 'title': _title(name), 'status': None,
                'subject': 'general', 'date': None, 'episode_id': None}
    if id.startswith('package:'):
        _, name = _package_parts(id[len('package:'):])
        return {'id': id, 'kind': 'package', 'title': _title(name), 'status': None,
                'subject': 'code', 'date': None, 'episode_id': None}
    raise InvalidRecord('The graph node id is not a known record, component or package.')


def _workflow_title(memory, key):
    """The name of an exported n8n workflow, `<file>` or `<file>#<position>` in a bulk export, or None."""
    from .architecture import MAX_FILE_BYTES, _read_workflow, project_root
    name, _, position = key.rpartition('#')
    if not name or not position.isdigit():
        name, position = key, ''
    try:
        path = project_root(memory) / name
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            return None
        summary = _read_workflow(path)
    except (OSError, InvalidRecord):
        return None
    if isinstance(summary, dict) and not position:
        return summary['name']
    if isinstance(summary, list) and position and 1 <= int(position) <= len(summary):
        return summary[int(position) - 1]['name']
    return None


def _component_subject(memory, path):
    """code when the folder or file holds source files that the code layer reads, otherwise general."""
    from .architecture import LANGUAGES, project_root
    try:
        root = project_root(memory)
        target = root if path == '.' else root / path
        if target.is_file():
            return 'code' if target.suffix in LANGUAGES else 'general'
        if target.is_dir():
            for count, child in enumerate(target.iterdir()):
                if count >= 5000:
                    break
                if child.suffix in LANGUAGES and child.is_file():
                    return 'code'
    except (OSError, InvalidRecord):
        return 'general'
    return 'general'


def authored_component(memory, component_id):
    """Return the latest authored version of `component:<slug>`, or None when no authored source exists."""
    row = memory.db.execute('SELECT id, version, body, checked_at FROM sources WHERE source_key=? ORDER BY version DESC LIMIT 1',
                            (component_id,)).fetchone()
    if row is None:
        return None
    try:
        body = json.loads(row['body'])
    except ValueError:
        return None
    if not isinstance(body, dict) or not isinstance(body.get('title'), str) or not isinstance(body.get('status'), str):
        return None
    return {'id': component_id, 'source_id': row['id'], 'version': row['version'], 'updated_at': row['checked_at'],
            'title': body['title'], 'kind': body.get('kind'), 'description': body.get('description'),
            'status': body['status'], 'path': body.get('path')}


def link(memory, *, from_id, to_id, type, reason, actor, request_key, retire=None):
    """Append an explicit typed link, or retire an active one. Returns {'id', 'duplicate'}."""
    if type not in LINK_TYPES:
        raise InvalidRecord('Link type must be one of ' + ', '.join(LINK_TYPES) + '.')
    _text(from_id, 'from_id', 1100)
    _text(to_id, 'to_id', 1100)
    _text(reason, 'reason', 2000)
    _text(actor, 'actor', 200)
    _text(request_key, 'request_key', 200)
    if retire is not None:
        _text(retire, 'retire', 200)
    if from_id == to_id:
        raise InvalidRecord('A link needs two different endpoints.')
    signature = _digest(dumps([from_id, to_id, type, reason, actor, retire]))
    with memory._write():
        initialize(memory)
        prior = memory.db.execute('SELECT id,signature FROM links WHERE request_key=?', (request_key,)).fetchone()
        if prior:
            if prior['signature'] != signature:
                raise Conflict('Request key was already used for a different link.')
            return {'id': prior['id'], 'duplicate': True}
        node(memory, from_id)
        node(memory, to_id)
        if retire is not None:
            target = memory.db.execute('SELECT * FROM links WHERE id=?', (retire,)).fetchone()
            if target is None or target['retires'] is not None:
                raise InvalidRecord('retire must name an existing link, not a retirement record.')
            if (target['from_id'], target['to_id'], target['type']) != (from_id, to_id, type):
                raise InvalidRecord('A retirement must use the same endpoints and type as the link it retires.')
            if memory.db.execute('SELECT 1 FROM links WHERE retires=?', (retire,)).fetchone():
                raise Conflict('This link is already retired.')
        else:
            active = memory.db.execute('''SELECT id FROM links l WHERE from_id=? AND to_id=? AND type=? AND retires IS NULL
                AND NOT EXISTS (SELECT 1 FROM links r WHERE r.retires=l.id)''', (from_id, to_id, type)).fetchone()
            if active:
                raise Conflict('An active link with the same endpoints and type already exists: ' + active[0] + '.')
        link_id = 'link_' + uuid.uuid4().hex
        memory.db.execute('INSERT INTO links (id,from_id,to_id,type,reason,actor,created_at,request_key,signature,retires) VALUES (?,?,?,?,?,?,?,?,?,?)',
                          (link_id, from_id, to_id, type, reason, actor, memory.now(), request_key, signature, retire))
    return {'id': link_id, 'duplicate': False}


def _focus(a, b):
    return '(' + a + ' IN (SELECT id FROM focus) OR ' + b + ' IN (SELECT id FROM focus))'


def _branches(memory):
    """Return {name: sql} for every edge source available in this database.

    Each query selects a, b, type, reason, origin and optionally link_id.
    """
    latest_plan = "e.kind='work_plan' AND e.seq=(SELECT max(n.seq) FROM events n WHERE n.episode_id=e.episode_id AND n.kind='work_plan')"
    target_episode = "json_extract(j.value,'$.episode_id')"
    direction = "'direction_' || coalesce(json_extract(e.payload,'$.project_revision'),0)"
    plan_id = "json_extract(e.payload,'$.work_plan_id')"
    lesson_id = "json_extract(e.payload,'$.lesson_id')"
    kinds = ','.join("'" + kind + "'" for kind in DECISION_KINDS)
    branches = {
        'evidence': "SELECT d.source_id AS a, d.event_id AS b, 'supports' AS type, d.reason AS reason, 'evidence' AS origin, NULL AS link_id "
                    'FROM dependencies d WHERE ' + _focus('d.source_id', 'd.event_id'),
        'event_link': "SELECT l.prior_event_id, l.event_id, 'informs', l.reason, 'event_link', NULL "
                      'FROM event_links l WHERE ' + _focus('l.prior_event_id', 'l.event_id'),
        'decision': "SELECT e.decision_id, e.id, e.kind, 'This ' || replace(e.kind,'_',' ') || ' is recorded against the decision.', 'decision', NULL "
                    'FROM events e WHERE e.decision_id IS NOT NULL AND e.kind IN (' + kinds + ') AND ' + _focus('e.decision_id', 'e.id'),
        'revision': "SELECT e.supersedes, e.id, 'revised_by', 'The later record revises the earlier record. Both remain in the history.', 'revision', NULL "
                    'FROM events e WHERE e.supersedes IS NOT NULL AND ' + _focus('e.supersedes', 'e.id'),
        'plan_dependency': "SELECT e.episode_id, " + target_episode + ", 'depends_on', json_extract(j.value,'$.reason'), 'plan', NULL "
                           "FROM events e JOIN json_each(e.payload,'$.depends_on') j WHERE " + latest_plan + ' AND ' + _focus('e.episode_id', target_episode),
        'plan_parent': "SELECT e.episode_id, json_extract(e.payload,'$.parent_id'), 'part_of', 'The latest work plan places this work item under the parent work item.', 'plan', NULL "
                       "FROM events e WHERE " + latest_plan + " AND json_extract(e.payload,'$.parent_id') IS NOT NULL AND "
                       + _focus('e.episode_id', "json_extract(e.payload,'$.parent_id')"),
        'episode': "SELECT e.episode_id, e.id, 'contains', 'The record belongs to this work item.', 'episode', NULL "
                   'FROM events e WHERE e.kind NOT IN (' + kinds + ') AND ' + _focus('e.episode_id', 'e.id'),
        'direction': 'SELECT ' + direction + ", e.id, 'governs', 'The decision was recorded under this project direction version.', 'direction', NULL "
                     "FROM events e WHERE e.kind='decision' AND " + _focus(direction, 'e.id'),
        'plan_decision': 'SELECT ' + plan_id + ", e.id, 'plans', 'The decision was recorded under this work plan.', 'plan', NULL "
                         "FROM events e WHERE e.kind='decision' AND " + plan_id + ' IS NOT NULL AND ' + _focus(plan_id, 'e.id'),
        'review': "SELECT e.id, " + lesson_id + ", 'reviews', 'The lesson review records the status ' || coalesce(json_extract(e.payload,'$.status'),'unknown') || '. ' || coalesce(json_extract(e.payload,'$.reason'),''), 'review', NULL "
                  "FROM events e WHERE e.kind='lesson_review' AND " + lesson_id + ' IS NOT NULL AND ' + _focus('e.id', lesson_id),
    }
    if _table_exists(memory, 'review_runs'):
        columns = _columns(memory, 'review_runs')
        if {'id', 'episode_id', 'role'} <= columns:
            roles = ','.join("'" + role + "'" for role in CHECK_ROLES)
            branches['run_check'] = ("SELECT r.episode_id, r.id, 'checked_by', 'The ' || replace(r.role,'_',' ') || ' check examines this work item.', 'run', NULL "
                                     'FROM review_runs r WHERE r.role IN (' + roles + ') AND ' + _focus('r.episode_id', 'r.id'))
            branches['run_work'] = ("SELECT r.episode_id, r.id, 'delegated_to', 'This work item was delegated to an agent run.', 'run', NULL "
                                    "FROM review_runs r WHERE r.role='work' AND " + _focus('r.episode_id', 'r.id'))
            if 'parent_run' in columns:
                branches['run_parent'] = (
                    "SELECT r.parent_run, r.id, CASE WHEN r.role='work_review' AND p.role='work' THEN 'reviewed_by' ELSE 'rerouted_to' END, "
                    "CASE WHEN r.role='work_review' AND p.role='work' THEN 'The work review examines the delegated work run.' "
                    "ELSE 'The run was rerouted to another agent host.' END, 'run', NULL "
                    'FROM review_runs r LEFT JOIN review_runs p ON p.id=r.parent_run '
                    'WHERE r.parent_run IS NOT NULL AND ' + _focus('r.parent_run', 'r.id'))
    if _table_exists(memory, 'host_receipts'):
        receipt_episode = "coalesce(h.episode_id,json_extract(h.payload,'$.episode_id'))"
        branches['scope'] = ('SELECT ' + receipt_episode + ", h.id, 'blocked_edit', 'Project Memory blocked an edit outside the recorded scope of this work.', 'scope', NULL "
                             "FROM host_receipts h WHERE h.event_name='ScopeBlocked' AND " + receipt_episode + ' IS NOT NULL AND ' + _focus(receipt_episode, 'h.id'))
    if _table_exists(memory, 'links'):
        branches['link'] = ("SELECT l.from_id, l.to_id, l.type, l.reason, 'link', l.id FROM links l "
                            'WHERE l.retires IS NULL AND NOT EXISTS (SELECT 1 FROM links r WHERE r.retires=l.id) AND ' + _focus('l.from_id', 'l.to_id'))
    return branches


def _query(memory, ids, names, limit):
    branches = _branches(memory)
    selected = [branches[name] for name in names if name in branches]
    if not ids or not selected:
        return []
    # The column list names every branch positionally, whichever branch comes first.
    sql = ('WITH focus(id) AS (SELECT value FROM json_each(:ids)), found(a, b, type, reason, origin, link_id) AS ('
           + ' UNION ALL '.join(selected)
           + ') SELECT a, b, type, reason, origin, link_id FROM found'
           + ' WHERE a IS NOT NULL AND b IS NOT NULL ORDER BY origin, a, type, b LIMIT :limit')
    rows = memory.db.execute(sql, {'ids': json.dumps(sorted(set(ids))), 'limit': limit}).fetchall()
    result = []
    used = set()
    for row in rows:
        edge_id = row['a'] + '|' + row['type'] + '|' + row['b']
        if edge_id in used:
            edge_id += '|' + row['origin']
        used.add(edge_id)
        edge = {'id': edge_id, 'from': row['a'], 'to': row['b'], 'type': row['type'], 'reason': row['reason'] or '', 'origin': row['origin']}
        if row['link_id']:
            edge['link_id'] = row['link_id']
        result.append(edge)
    return result


def _check_ids(ids):
    if isinstance(ids, str) or not isinstance(ids, (list, tuple, set, frozenset)):
        raise InvalidRecord('ids must be a list of record ids.')
    if len(ids) > 10000:
        raise InvalidRecord('Request edges for at most 10000 ids at once.')
    for value in ids:
        if not isinstance(value, str) or not value:
            raise InvalidRecord('Each id must be nonempty text.')
    return list(ids)


def _check_int(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise InvalidRecord(f'{name} must be an integer from {low} to {high}.')


def edges(memory, ids, *, limit=500):
    """Every recorded and derived edge touching any id, ordered deterministically.

    Explicit link edges also carry link_id so that a caller can retire them.
    """
    ids = _check_ids(ids)
    _check_int(limit, 'limit', 1, 20000)
    return _query(memory, ids, list(_branches(memory)), limit)


def _safe_node(memory, id):
    try:
        return node(memory, id)
    except InvalidRecord:
        return {'id': id, 'kind': 'missing', 'title': id, 'status': 'missing', 'subject': 'general', 'date': None, 'episode_id': None}


def lineage(memory, focus, *, depth=3, limit=80):
    """Breadth-first neighbourhood in both directions, bounded by depth and node count."""
    _check_int(depth, 'depth', 1, 10)
    _check_int(limit, 'limit', 1, 500)
    nodes = {focus: node(memory, focus)}
    found = {}
    truncated = False
    frontier = [focus]
    for _ in range(depth):
        if not frontier:
            break
        level = edges(memory, frontier, limit=EDGE_LIMIT_PER_LEVEL + 1)
        if len(level) > EDGE_LIMIT_PER_LEVEL:
            truncated = True
            level = level[:EDGE_LIMIT_PER_LEVEL]
        following = []
        for edge in level:
            key = (edge['from'], edge['type'], edge['to'], edge['origin'])
            for endpoint in (edge['from'], edge['to']):
                if endpoint not in nodes:
                    if len(nodes) >= limit:
                        truncated = True
                        continue
                    nodes[endpoint] = _safe_node(memory, endpoint)
                    following.append(endpoint)
            if edge['from'] in nodes and edge['to'] in nodes and key not in found:
                found[key] = edge
        frontier = following
    result_edges = []
    used = set()
    for edge in found.values():
        edge = dict(edge)
        base = edge['from'] + '|' + edge['type'] + '|' + edge['to']
        edge['id'] = base if base not in used else base + '|' + edge['origin']
        used.add(edge['id'])
        result_edges.append(edge)
    return {'focus': focus, 'nodes': list(nodes.values()), 'edges': result_edges, 'truncated': truncated}


def work_graph(memory, *, states=None, limit=300):
    """Work items and their dependencies: plan depends_on plus explicit depends_on and blocks links."""
    if states is not None and not isinstance(states, dict):
        raise InvalidRecord('states must map episode ids to states.')
    _check_int(limit, 'limit', 1, 5000)
    states = states or {}
    rows = memory.db.execute('''SELECT ep.id, ep.title, ep.subject, p.payload FROM episodes ep
        LEFT JOIN events p ON p.episode_id=ep.id AND p.kind='work_plan'
          AND p.seq=(SELECT max(n.seq) FROM events n WHERE n.episode_id=ep.id AND n.kind='work_plan')
        WHERE ep.task_type!='sprint' ORDER BY ep.created_at, ep.id LIMIT ?''', (limit + 1,)).fetchall()
    truncated = len(rows) > limit
    nodes = []
    for row in rows[:limit]:
        plan = json.loads(row['payload']) if row['payload'] else {}
        nodes.append({'id': row['id'], 'kind': 'episode', 'title': _title(row['title']), 'subject': row['subject'],
                      'priority': plan.get('priority', 'normal'),
                      'item_type': plan.get('item_type', 'task'), 'parent_id': plan.get('parent_id'),
                      'state': states.get(row['id']) or plan.get('state') or 'backlog'})
    ids = {item['id'] for item in nodes}
    found = _query(memory, ids, ['plan_dependency', 'link'], EDGE_LIMIT_WORK_GRAPH + 1)
    if len(found) > EDGE_LIMIT_WORK_GRAPH:
        truncated = True
        found = found[:EDGE_LIMIT_WORK_GRAPH]
    result = [edge for edge in found
              if edge['type'] in {'depends_on', 'blocks'} and edge['from'] in ids and edge['to'] in ids]
    return {'nodes': nodes, 'edges': result, 'truncated': truncated}
