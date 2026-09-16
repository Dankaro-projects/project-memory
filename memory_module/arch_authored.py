"""Authored components: the parts of a project that people and agents describe rather than derive from files.

A component is stored as a versioned source with the key `component:<slug>` and
a JSON body. An agent may propose a component; only the user confirms one, and
only the user changes a component the user has confirmed. The authored layer
places these components in the model and merges one whose path names an
extracted component or a service into that node.
"""
import json
import re

from . import guards
from .arch_base import ATTACHED_LIMIT, N8N_PREFIX, empty_node, link_rows, project_root, table_exists
from .core import Conflict, InvalidRecord, USER_ACTOR, _digest, _text, dumps

COMPONENT_KINDS = ('system', 'component', 'service', 'workflow', 'integration', 'dataset', 'stakeholder',
                   'workstream', 'deliverable', 'process')
COMPONENT_STATUSES = ('proposed', 'confirmed', 'retired')
SLUG = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*')
MAX_SLUG = 80
COMPONENT_SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS component_revisions (
 request_key TEXT PRIMARY KEY, component_id TEXT NOT NULL, source_id TEXT NOT NULL REFERENCES sources(id),
 actor TEXT NOT NULL, evidence TEXT NOT NULL, signature TEXT NOT NULL, created_at TEXT NOT NULL
)''',
    'CREATE INDEX IF NOT EXISTS component_revisions_component ON component_revisions(component_id, created_at)',
    '''CREATE TRIGGER IF NOT EXISTS immutable_component_revisions_update BEFORE UPDATE ON component_revisions BEGIN
 SELECT RAISE(ABORT, 'Component revisions cannot be changed; save a new version.'); END''',
    '''CREATE TRIGGER IF NOT EXISTS immutable_component_revisions_delete BEFORE DELETE ON component_revisions BEGIN
 SELECT RAISE(ABORT, 'Component revisions cannot be deleted.'); END''',
)


def _slugify(title):
    text = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')
    text = text[:MAX_SLUG].strip('-')
    return text or 'component'


def _slug(component_id):
    """Return the slug of `component:<slug>` or of a bare slug, or raise."""
    if not isinstance(component_id, str):
        raise InvalidRecord('component_id must be text.')
    slug = component_id[len('component:'):] if component_id.startswith('component:') else component_id
    if len(slug) > MAX_SLUG or not SLUG.fullmatch(slug):
        raise InvalidRecord('An authored component id is component: followed by lowercase letters, digits and single hyphens, '
                            f'at most {MAX_SLUG} characters.')
    return slug


def _component_path_value(path):
    from .graph import _component_path
    if path is None:
        return None
    return _component_path(path)


def _evidence(memory, evidence):
    evidence = [] if evidence is None else evidence
    if not isinstance(evidence, list) or len(evidence) > 20:
        raise InvalidRecord('evidence must be a list of at most 20 references.')
    for ref in evidence:
        if not isinstance(ref, dict) or set(ref) != {'source_id', 'reason'}:
            raise InvalidRecord('Each evidence reference needs source_id and reason.')
        _text(ref['source_id'], 'source_id', 200)
        _text(ref['reason'], 'evidence reason', 2000)
        memory.source_status(ref['source_id'])
    if len({ref['source_id'] for ref in evidence}) != len(evidence):
        raise InvalidRecord('Evidence references must be unique.')
    return sorted(evidence, key=lambda ref: ref['source_id'])


# An omitted path keeps the stored path of an existing component; an explicit None clears it.
KEEP_PATH = object()


def save_component(memory, *, component_id=None, title, kind, description, status, actor, request_key, evidence=None, path=KEEP_PATH):
    """Store a new version of an authored component as the source `component:<slug>`.

    An actor other than workspace-user cannot set the status confirmed and cannot
    change a component that the user has confirmed, so agent authored components
    stay proposed until the user confirms them. The same request key with the same
    content returns the original result; different content raises Conflict.
    """
    from .graph import authored_component
    _text(title, 'title', 200)
    _text(description, 'description', 4000)
    _text(actor, 'actor', 200)
    _text(request_key, 'request_key', 200)
    if kind not in COMPONENT_KINDS:
        raise InvalidRecord('kind must be one of ' + ', '.join(COMPONENT_KINDS) + '.')
    if status not in COMPONENT_STATUSES:
        raise InvalidRecord('status must be proposed, confirmed or retired.')
    slug = _slug(component_id) if component_id is not None else None
    if path is KEEP_PATH:
        # A revision that does not name a path keeps the one that connects the component to the project,
        # so a caller that sends only the fields it changes cannot silently drop it.
        stored = authored_component(memory, 'component:' + slug) if slug is not None else None
        path = stored['path'] if stored else None
    else:
        path = _component_path_value(path)
    evidence = _evidence(memory, evidence)
    body = {'title': title, 'kind': kind, 'description': description, 'status': status, 'path': path}
    signature = _digest(dumps([slug, body, actor, evidence]))
    with memory._write():
        for statement in COMPONENT_SCHEMA:
            memory.db.execute(statement)
        prior = memory.db.execute('SELECT component_id, signature FROM component_revisions WHERE request_key=?', (request_key,)).fetchone()
        if prior:
            if prior['signature'] != signature:
                raise Conflict('Request key was already used for a different component version.')
            return {**component(memory, prior['component_id']), 'duplicate': True, 'changed': False}
        root = project_root(memory)

        def folder_conflict(candidate):
            # `component:<folder>` also names a project folder, so an authored id may equal a folder only when it describes that folder.
            try:
                return path != candidate and (root / candidate).is_dir()
            except OSError:
                return False

        if slug is None:
            base = _slugify(title)
            slug = base
            if folder_conflict(slug):
                slug = (base + '-' + kind)[:MAX_SLUG].strip('-')
            number = 2
            while authored_component(memory, 'component:' + slug) or folder_conflict(slug):
                suffix = '-' + str(number)
                slug = base[:MAX_SLUG - len(suffix)].strip('-') + suffix
                number += 1
        elif folder_conflict(slug) and not authored_component(memory, 'component:' + slug):
            raise InvalidRecord(f'The id component:{slug} already names the project folder {slug}. Choose another id, '
                                'or set the path to that folder when the component describes it.', component_id='component:' + slug)
        identifier = 'component:' + slug
        current = authored_component(memory, identifier)
        if actor != USER_ACTOR:
            if current and current['status'] == 'confirmed':
                raise InvalidRecord('The user confirmed this component, so only the user can change it. Propose the change in a note or as a new proposed component.',
                                    component_id=identifier)
            if status == 'confirmed':
                raise InvalidRecord('Only the user can confirm a component. Save it as proposed so the user can confirm it in the control panel.',
                                    component_id=identifier)
        unchanged = current and all(current[key] == value for key, value in body.items())
        if unchanged:
            source_id = current['source_id']
        else:
            summary = f'This authored {kind} is recorded with the status {status}. It describes the project and does not grant permission.'
            source_id = memory.source(identifier, title, summary, dumps(body), 'tool', subject='general')['id']
        memory.db.execute('INSERT INTO component_revisions VALUES (?,?,?,?,?,?,?)',
                          (request_key, identifier, source_id, actor, dumps(evidence), signature, memory.now()))
    return {**component(memory, identifier), 'duplicate': False, 'changed': not unchanged}


def _authored_items(memory):
    """Latest version of every authored component, ordered by id."""
    from .graph import authored_component
    rows = memory.db.execute('''SELECT DISTINCT source_key FROM sources WHERE source_key LIKE 'component:%'
        ORDER BY source_key''').fetchall()
    result = []
    for row in rows:
        item = authored_component(memory, row[0])
        if item is None or item['kind'] not in COMPONENT_KINDS or item['status'] not in COMPONENT_STATUSES:
            continue
        result.append(item)
    return result


def _revision_details(memory, component_id):
    if not table_exists(memory, 'component_revisions'):
        return {'actor': None, 'evidence': [], 'revisions': 0}
    row = memory.db.execute('''SELECT actor, evidence FROM component_revisions WHERE component_id=?
        ORDER BY created_at DESC, rowid DESC LIMIT 1''', (component_id,)).fetchone()
    total = memory.db.execute('SELECT count(*) FROM component_revisions WHERE component_id=?', (component_id,)).fetchone()[0]
    if row is None:
        return {'actor': None, 'evidence': [], 'revisions': 0}
    return {'actor': row['actor'], 'evidence': json.loads(row['evidence']), 'revisions': total}


def component(memory, component_id):
    """One authored component with its latest author, evidence, links and linked work."""
    from .graph import authored_component
    identifier = 'component:' + _slug(component_id)
    item = authored_component(memory, identifier)
    if item is None:
        raise InvalidRecord('The authored component was not found.', component_id=identifier)
    links = [row for row in link_rows(memory) if identifier in (row['from_id'], row['to_id'])]
    work = []
    for row in links:
        other = row['to_id'] if row['from_id'] == identifier else row['from_id']
        if other.startswith('episode_') and other not in work:
            work.append(other)
    return {**item, **_revision_details(memory, identifier),
            'links': [{'id': row['id'], 'from': row['from_id'], 'to': row['to_id'], 'type': row['type'], 'reason': row['reason']}
                      for row in links[:ATTACHED_LIMIT]],
            'links_total': len(links), 'work': work[:ATTACHED_LIMIT], 'work_total': len(work)}


def components(memory, *, status=None, kind=None, limit=100, offset=0):
    """Page authored components, optionally filtered by status or kind."""
    if status is not None and status not in COMPONENT_STATUSES:
        raise InvalidRecord('status must be proposed, confirmed or retired.')
    if kind is not None and kind not in COMPONENT_KINDS:
        raise InvalidRecord('kind must be one of ' + ', '.join(COMPONENT_KINDS) + '.')
    if type(limit) is not int or not 1 <= limit <= 500 or type(offset) is not int or offset < 0:
        raise InvalidRecord('Use limit 1 to 500 and a nonnegative offset.')
    items = [item for item in _authored_items(memory)
             if (status is None or item['status'] == status) and (kind is None or item['kind'] == kind)]
    page = [component(memory, item['id']) for item in items[offset:offset + limit]]
    return {'components': page, 'total': len(items), 'offset': offset, 'more': offset + len(page) < len(items),
            'note': 'Authored components describe the project. Proposed components are not confirmed by the user.'}


SYSTEM_KINDS = ('system', 'service', 'integration')


def layer(memory, names, nodes, members):
    """Add authored components. One whose path equals an extracted component path is merged into that node.

    Returns {authored id: model node id} for every authored component in the model.
    """
    by_path = {}
    for node in nodes.values():
        if node['kind'] in ('component', 'workflow') and node['path'] is not None:
            by_path.setdefault(node['id'][len('component:'):], node['id'])
            by_path.setdefault(node['path'], node['id'])
        elif node['kind'] == 'service':
            # An authored system with the path service:<name> describes the service found in workflow exports.
            by_path.setdefault(node['id'], node['id'])
    placed = {}
    for item in _authored_items(memory):
        summary = {'id': item['id'], 'title': item['title'], 'kind': item['kind'], 'description': item['description'],
                   'status': item['status'], 'version': item['version']}
        slug = item['id'][len('component:'):]
        target = by_path.get(item['path']) if item['path'] else None
        # An equal id merges only when the authored item names no other path; otherwise it would take over a folder.
        if target is None and item['id'] in nodes and (not item['path'] or item['path'] == slug):
            target = item['id']
        if target is None and not item['path'] and item['kind'] in SYSTEM_KINDS and 'service:' + slug in nodes:
            target = 'service:' + slug
        if target is not None:
            nodes[target]['authored'] = summary
            nodes[target]['authored_status'] = item['status']
            placed[item['id']] = target
            continue
        node_id = item['id']
        if node_id in nodes:
            node_id = 'component:authored:' + slug
        node = empty_node(node_id, item['kind'], item['title'], item['path'], layer='authored')
        node['authored'] = summary
        node['authored_status'] = item['status']
        if item['status'] != 'confirmed':
            node['flags'].append(item['status'])
        if node_id != item['id']:
            node['flags'].append('id_conflict')
        path = item['path']
        if path and path.startswith(N8N_PREFIX):
            path = path[len(N8N_PREFIX):]
        nodes[node_id] = node
        members[node_id] = [name for name in names if guards.match_path(name, [path])] if path else []
        placed[item['id']] = node_id
    return placed


def attach_paths(nodes, relative, attached_sets):
    """Attach entries whose patterns overlap an authored path, so a planned deliverable shows work before its file exists.

    `relative` returns the path patterns of one entry as guards compares them.
    """
    for node_id, node in nodes.items():
        path = node['path']
        if node.get('layer') != 'authored' or not path or path.startswith('service:'):
            continue
        if path.startswith(N8N_PREFIX):
            path = path[len(N8N_PREFIX):]
        for entries, attached in attached_sets:
            current = attached.setdefault(node_id, [])
            for entry in entries:
                if entry not in current and any(guards.patterns_overlap(pattern, path) for pattern in relative(entry['paths'])):
                    current.append(entry)
