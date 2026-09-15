"""Bounded record relationships and explicitly authored project diagrams."""
import json
from collections import deque
from .core import InvalidRecord, Conflict, _text, dumps

PREFIX = 'workspace-map:'
KINDS = ('system', 'component', 'process', 'deliverable', 'work')
RELATIONS = ('depends_on', 'precedes', 'uses', 'produces', 'contains', 'implements')


def model(memory, episode_id, mode):
    memory.episode(episode_id)
    if mode not in {'workflow', 'architecture'}:
        raise InvalidRecord('Select workflow or architecture.')
    row = memory.db.execute('SELECT * FROM sources WHERE source_key=? ORDER BY version DESC LIMIT 1',
                            (PREFIX + episode_id + ':' + mode,)).fetchone()
    result = {'version': row['version'], 'source_id': row['id'], **json.loads(row['body'])} if row else {
        'version': 0, 'source_id': None, 'nodes': [], 'edges': [], 'reason': ''}
    result['reference_status'] = {node['reference']: record(memory, node['reference'])['status'] for node in result['nodes'] if node['reference']}
    return result


def save(memory, data, request_key, actor="workspace-user", evidence=None):
    if set(data) != {'episode_id', 'expected_version', 'mode', 'map_version', 'nodes', 'edges', 'reason'}:
        raise InvalidRecord('A map needs its work item, versions, mode, nodes, edges and reason.')
    ep = memory.episode(data['episode_id']); current = model(memory, ep['id'], data['mode'])
    if ep['version'] != data['expected_version'] or current['version'] != data['map_version']:
        raise Conflict('This work or diagram changed. Reload it before saving; your draft is retained.')
    if actor != 'workspace-user' and not evidence:
        raise InvalidRecord('Agent-authored diagrams need explained evidence references.')
    _text(data['reason'], 'map change reason', 2000)
    nodes, edges = data['nodes'], data['edges']
    if not isinstance(nodes, list) or not isinstance(edges, list) or len(nodes) > 100 or len(edges) > 200:
        raise InvalidRecord('A diagram supports up to 100 nodes and 200 relationships. Split larger diagrams by work item.')
    ids = set()
    for node in nodes:
        if not isinstance(node, dict) or set(node) != {'id', 'title', 'kind', 'description', 'reference', 'status'}:
            raise InvalidRecord('Each node needs id, title, kind, description, reference and status.')
        if not isinstance(node['id'], str) or not node['id'].startswith('node_') or node['id'] in ids:
            raise InvalidRecord('Node IDs must be unique generated node identifiers.')
        _text(node['id'], 'node id', 100); _text(node['title'], 'node title', 200)
        _text(node['description'], 'node description', 2000)
        if node['kind'] not in KINDS or node['status'] not in {'proposed', 'confirmed', 'retired'}:
            raise InvalidRecord('Select an available node type and status.')
        if node['reference']:
            record(memory, node['reference'])
        ids.add(node['id'])
    edge_ids = set()
    for edge in edges:
        if not isinstance(edge, dict) or set(edge) != {'id', 'from', 'to', 'type', 'reason', 'status'}:
            raise InvalidRecord('Each relationship needs id, from, to, type, reason and status.')
        _text(edge['id'], 'edge id', 100); _text(edge['reason'], 'relationship reason', 2000)
        if edge['id'] in edge_ids or edge['from'] not in ids or edge['to'] not in ids or edge['from'] == edge['to']:
            raise InvalidRecord('Relationships need unique IDs and two different nodes in this diagram.')
        if edge['type'] not in RELATIONS or edge['status'] not in {'proposed', 'confirmed', 'retired'}:
            raise InvalidRecord('Select an available relationship type and status.')
        edge_ids.add(edge['id'])
    if actor != 'workspace-user':
        old = {item['id']: item for item in current['nodes'] + current['edges']}
        if any(item['status'] == 'confirmed' and old.get(item['id']) != item for item in nodes + edges):
            raise InvalidRecord('New or changed agent-authored diagram items remain proposed until the user confirms them.')
    # Process feedback loops are valid; a drawn edge does not schedule execution.
    body = {'nodes': nodes, 'edges': edges, 'reason': data['reason']}
    source = memory.source(PREFIX + ep['id'] + ':' + data['mode'], data['mode'].capitalize() + ' for ' + ep['title'],
                           data['reason'], dumps(body), 'user' if actor == 'workspace-user' else 'tool', subject=ep['subject'])
    refs = {node['reference']: 'The diagram links ' + node['title'] + '. ' + node['description'] for node in nodes if node['reference']}
    source_refs = [{'source_id': rid, 'reason': reason} for rid, reason in refs.items() if rid.startswith('source_')]
    event_refs = [{'event_id': rid, 'reason': reason} for rid, reason in refs.items() if rid.startswith('event_')]
    event = memory.record(ep['id'], 'note', {'text': ('The user' if actor == 'workspace-user' else 'The assistant') + ' updates the ' + data['mode'] + ' diagram. ' + data['reason']},
                          expected_version=ep['version'], request_key=request_key + ':record', actor=actor,
                          evidence=list({ref['source_id']: ref for ref in source_refs + (evidence or [])}.values()) + [{'source_id': source['id'], 'reason': 'This version records the explicitly authored diagram.'}], links=event_refs)
    return {'event': event, 'map': model(memory, ep['id'], data['mode'])}


def record(memory, rid):
    _text(rid, 'record id', 100)
    if rid.startswith('episode_'):
        ep = memory.episode(rid)
        return {'id': rid, 'title': ep['title'], 'kind': 'work', 'status': ep['status'], 'subject': ep['subject'], 'reference': rid}
    value = memory.read(rid)
    payload = value.get('payload', {})
    title = value.get('title') or next((payload[k] for k in ('decision', 'observed', 'action', 'do', 'question', 'text', 'reason') if k in payload), value['kind'])
    return {'id': rid, 'title': title, 'kind': value['kind'], 'status': value['status'], 'subject': value['subject'], 'reference': rid}


def neighbours(memory, rid):
    edges = []
    def edge(a, b, kind, reason):
        edges.append({'id': a + ':' + kind + ':' + b, 'from': a, 'to': b, 'type': kind, 'reason': reason, 'status': 'recorded'})
    if rid.startswith('episode_'):
        rows = memory.db.execute('SELECT id FROM events WHERE episode_id=? ORDER BY seq DESC LIMIT 101', (rid,))
        for r in rows:
            edge(rid, r['id'], 'contains', 'This record belongs to this work item.')
        from .planning import latest
        plan = latest(memory, rid, 'work_plan')
        for dep in (plan or {}).get('depends_on', []):
            edge(rid, dep['episode_id'], 'depends_on', dep['reason'])
        incoming = memory.db.execute('''SELECT e.episode_id,json_extract(j.value,'$.reason') reason FROM events e,
            json_each(e.payload,'$.depends_on') j WHERE e.kind='work_plan' AND json_extract(j.value,'$.episode_id')=?
            AND e.seq=(SELECT max(seq) FROM events WHERE episode_id=e.episode_id AND kind='work_plan') LIMIT 101''', (rid,))
        for r in incoming:
            edge(r['episode_id'], rid, 'depends_on', r['reason'])
    elif rid.startswith('source_'):
        value = memory.read(rid)
        for row in memory.db.execute('SELECT id,version FROM sources WHERE source_key=? AND version IN (?,?)',
                                     (value['source_key'], value['version'] - 1, value['version'] + 1)):
            older, newer = (row['id'], rid) if row['version'] < value['version'] else (rid, row['id'])
            edge(older, newer, 'revised_by', 'This version revises the earlier captured source; both remain readable.')
    else:
        value = memory.read(rid)
        edge(value['episode_id'], rid, 'contains', 'This record belongs to this work item.')
        for ref in value.get('evidence', []):
            edge(ref['source_id'], rid, 'supports', ref['reason'])
        for ref in value.get('links', []):
            edge(ref['event_id'], rid, 'informs', ref['reason'])
        if value.get('decision_id'):
            edge(value['decision_id'], rid, 'has_' + value['kind'], 'This ' + value['kind'] + ' records the decision lineage.')
        if value.get('supersedes'):
            edge(value['supersedes'], rid, 'revised_by', 'The earlier version remains part of the history.')
    for r in memory.db.execute('SELECT event_id,reason FROM dependencies WHERE source_id=? LIMIT 101', (rid,)):
        edge(rid, r['event_id'], 'supports', r['reason'])
    for r in memory.db.execute('SELECT event_id,reason FROM event_links WHERE prior_event_id=? LIMIT 101', (rid,)):
        edge(rid, r['event_id'], 'informs', r['reason'])
    for r in memory.db.execute('SELECT id,kind,decision_id,supersedes FROM events WHERE decision_id=? OR supersedes=? LIMIT 101', (rid, rid)):
        edge(rid, r['id'], 'revised_by' if r['supersedes'] == rid else 'has_' + r['kind'], 'This link preserves the recorded history.')
    return edges


def relationships(memory, focus, limit=40, depth=1):
    if not 1 <= limit <= 100 or not 1 <= depth <= 3:
        raise InvalidRecord('Use 1–100 nodes and depth 1–3.')
    nodes = {focus: record(memory, focus)}; edges = {}; queue = deque([(focus, 0)]); truncated = False
    while queue:
        rid, level = queue.popleft()
        if level >= depth:
            continue
        links = neighbours(memory, rid)
        if len(links) >= 101:
            truncated = True
        for edge in links:
            other = edge['to'] if edge['from'] == rid else edge['from']
            if other not in nodes:
                if len(nodes) >= limit:
                    truncated = True; continue
                nodes[other] = record(memory, other); queue.append((other, level + 1))
            if len(edges) < 200:
                edges[edge['id']] = edge
            else:
                truncated = True
    return {'nodes': list(nodes.values()), 'edges': list(edges.values()), 'focus': focus, 'truncated': truncated,
            'limit': limit, 'depth': depth, 'note': 'Only recorded relationships are shown. Expand a node to inspect its neighbourhood.'}
