"""The structure of a project in layers, with the work, lessons, guards and links attached to it.

`arch_code` reads source files and package manifests, `arch_n8n` reads exported
n8n workflows and `arch_authored` holds the components that people and agents
describe. This module composes those layers into one model and attaches the
recorded work to it. It is also the interface the rest of the package reads
through, so the names it imports below are part of its contract.

Imports are read statically from source text. Dynamic loading, import hooks, path
aliases from build configuration and generated code are not detected.
"""
import json
from pathlib import Path, PurePosixPath

from . import arch_authored, arch_code, arch_n8n, guards
from .arch_base import ATTACHED_LIMIT, MAX_FILE_BYTES, N8N_PREFIX, join, link_rows, project_root
from .core import InvalidRecord

# Read by graph.py, mcp.py, workspace.py, api.py and the tests through this module.
from .arch_code import LANGUAGES, declarations, dependency_sections, pubspec  # noqa: F401
from .arch_n8n import _read_workflow, credential_service, node_service  # noqa: F401
from .arch_authored import COMPONENT_KINDS, COMPONENT_STATUSES, component, components, save_component  # noqa: F401

LAYERS = ('code', 'n8n', 'authored')
STATUS_ORDER = ('blocked', 'review', 'in_progress', 'guarded', 'idle')
KIND_ORDER = {'file': 0, 'n8n_node': 0, 'component': 1, 'workflow': 1, 'package': 3, 'service': 3}
WORK_ORDER = {state: index for index, state in
              enumerate(('blocked', 'review', 'in_progress', 'ready', 'backlog', 'done', 'cancelled'))}
NOTE = ('Imports are read statically from source files. Dynamic loading, import hooks, build path aliases '
        'and generated code are not detected. Python distribution names can differ from import names.')


def _layers(layers):
    if layers is None:
        return LAYERS
    if isinstance(layers, str):
        layers = [part.strip() for part in layers.split(',') if part.strip()]
    if not isinstance(layers, (list, tuple, set, frozenset)) or not layers:
        raise InvalidRecord('layers must list at least one of code, n8n and authored.')
    unknown = sorted(str(layer) for layer in layers if layer not in LAYERS)
    if unknown:
        raise InvalidRecord('layers must contain only code, n8n and authored.', unknown=unknown)
    return tuple(layer for layer in LAYERS if layer in layers)


# What the recorded history attaches to the structure.

def _work_items(memory):
    """Every work item with a plan: its recorded state and its path patterns, which can be empty."""
    rows = memory.db.execute("""SELECT ep.id, ep.created_at, p.payload FROM episodes ep
        JOIN events p ON p.episode_id=ep.id AND p.kind='work_plan'
         AND p.seq=(SELECT max(n.seq) FROM events n WHERE n.episode_id=ep.id AND n.kind='work_plan')
        WHERE ep.task_type!='sprint' ORDER BY ep.created_at, ep.id""")
    result = []
    for row in rows:
        payload = json.loads(row['payload'])
        paths = payload.get('paths')
        result.append({'id': row['id'], 'state': payload.get('state'), 'paths': paths if isinstance(paths, list) else []})
    return result


def _lessons(memory):
    rows = memory.db.execute("""SELECT e.id, e.payload FROM events e WHERE e.kind='lesson'
        AND NOT EXISTS (SELECT 1 FROM events n WHERE n.supersedes=e.id) ORDER BY e.rowid""")
    result = []
    for row in rows:
        payload = json.loads(row['payload'])
        review = memory._lesson_review(row['id'])
        status = review['status'] if review else 'proposed'
        if status in {'rejected', 'retired'}:
            continue
        paths = payload.get('paths')
        # An accepted review that carries any trigger field replaces all lesson triggers, as in guards.active_guards.
        if review and review['status'] in {'accepted', 'needs_review'} and any(key in review for key in guards.TRIGGERS):
            paths = review.get('paths', [])
        if isinstance(paths, list) and paths:
            result.append({'id': row['id'], 'status': status, 'paths': paths})
    return result


def _attach(node, key, ids):
    node[key] = ids[:ATTACHED_LIMIT]
    node[key + '_total'] = len(ids)


def model(memory, *, level='component', focus=None, work_states=None, cache=None, layers=None):
    """Return the structure of the project in layers, with work, lessons and links attached.

    The code layer reads imports statically. The n8n layer reads exported workflow
    files. The authored layer holds components that people and agents describe,
    such as systems, stakeholders, workstreams and deliverables.
    """
    from .reviews import project_paths
    if level not in {'component', 'file'}:
        raise InvalidRecord('Architecture level must be component or file.')
    layers = _layers(layers)
    if focus is not None:
        if not isinstance(focus, str):
            raise InvalidRecord('Focus must be a component path.')
        if focus.startswith('component:'):
            focus = focus[len('component:'):]
        focus = focus.strip('/') or '.'
        if focus != '.' and (join('.', focus) != focus):
            raise InvalidRecord('Focus must be a relative component path without dot segments.')
    if level == 'file' and focus is None:
        raise InvalidRecord('The file level needs a focus component.')
    if work_states is not None and not isinstance(work_states, dict):
        raise InvalidRecord('Work states must map episode ids to states.')
    if cache is not None and not isinstance(cache, dict):
        raise InvalidRecord('The architecture cache must be a dictionary.')

    root, paths = project_paths(project_root(memory))
    names = [PurePosixPath(Path(name).as_posix()).as_posix() for name in paths]
    nodes = {}
    members = {}
    edges = []
    packages = {'declared': [], 'manifests': [], 'issues': []}
    languages = {}
    issues = []
    truncated = False
    workflow_focus = level == 'file' and focus.startswith(N8N_PREFIX)
    parts = []
    if workflow_focus:
        if 'n8n' not in layers:
            raise InvalidRecord('A workflow focus needs the n8n layer.')
        parts.append(arch_n8n.layer(root, names, cache, focus_file=focus[len(N8N_PREFIX):]))
    elif level == 'file':
        if 'code' not in layers:
            raise InvalidRecord('The file level of a code component needs the code layer.')
        parts.append(arch_code.layer(root, names, level, focus, cache))
    else:
        if 'code' in layers:
            parts.append(arch_code.layer(root, names, level, focus, cache))
        if 'n8n' in layers:
            parts.append(arch_n8n.layer(root, names, cache))
    for part in parts:
        for node_id, node in part['nodes'].items():
            nodes.setdefault(node_id, node)
            members.setdefault(node_id, part['members'][node_id])
        edges.extend(part['edges'])
        truncated = truncated or part['truncated']
        if 'packages' in part:
            packages = part['packages']
            languages = part['languages']
            issues.extend(part['issues'])

    placed = {}
    if level == 'component' and 'authored' in layers:
        placed = arch_authored.layer(memory, names, nodes, members)
    work = _work_items(memory)
    lessons = _lessons(memory)
    guard_list = [guard for guard in guards.active_guards(memory) if guard.get('paths')]
    rows = link_rows(memory)
    recorded = {item['id']: item['state'] for item in work}
    states = work_states if work_states is not None else recorded

    def canonical(endpoint):
        return placed.get(endpoint, endpoint)

    link_map = {}
    linked_work = {}
    for row in rows:
        a = canonical(row['from_id'])
        b = canonical(row['to_id'])
        for endpoint, other in ((a, b), (b, a)):
            ids = link_map.setdefault(endpoint, [])
            if row['id'] not in ids:
                ids.append(row['id'])
            if other.startswith('episode_'):
                linked = linked_work.setdefault(endpoint, [])
                if other not in linked:
                    linked.append(other)
        if placed and (row['from_id'] in placed or row['to_id'] in placed) and a in nodes and b in nodes and a != b:
            # A recorded link that repeats a relation the imports or the workflow export already show is the same
            # relation stated twice, so one edge carries both instead of drawing the pair a second time.
            same = next((edge for edge in edges if edge['from'] == a and edge['to'] == b and edge['type'] == row['type']), None)
            if same is None:
                edges.append({'from': a, 'to': b, 'type': row['type'], 'weight': 1, 'layer': 'authored', 'link_id': row['id']})
            else:
                same.setdefault('link_id', row['id'])
                recorded = same.setdefault('link_ids', [])
                if row['id'] not in recorded:
                    recorded.append(row['id'])

    # guards decides how a pattern matches a path. Its root rules are applied once per entry here, not once per file.
    roots = guards._roots(root)

    def relative(patterns):
        return [guards._relative(guards.normalize(value), roots) for value in patterns if isinstance(value, str) and value]

    def matching(entries, key):
        attached = {}
        for entry in entries:
            patterns = relative(entry[key])
            if not patterns:
                continue
            for node_id, node_files in members.items():
                if any(guards.match_path(name, patterns) for name in node_files):
                    attached.setdefault(node_id, []).append(entry)
        return attached

    work_by_node = matching(work, 'paths')
    lessons_by_node = matching(lessons, 'paths')
    if placed:
        arch_authored.attach_paths(nodes, relative, ((work, work_by_node), (lessons, lessons_by_node)))
    guarded = matching(guard_list, 'paths')
    for node_id, node in nodes.items():
        items = [entry['id'] for entry in work_by_node.get(node_id, [])]
        items.extend(episode for episode in linked_work.get(node_id, []) if episode not in items)
        items = sorted(items, key=lambda item: WORK_ORDER.get(states.get(item), len(WORK_ORDER)))
        _attach(node, 'work', items)
        _attach(node, 'lessons', [item['id'] for item in lessons_by_node.get(node_id, [])])
        _attach(node, 'links', link_map.get(node_id, []))
        status = 'idle'
        found = {states.get(item) for item in items}
        if guarded.get(node_id):
            found.add('guarded')
        for candidate in STATUS_ORDER:
            if candidate in found:
                status = candidate
                break
        node['status'] = status

    ordered = sorted(nodes.values(), key=lambda n: (KIND_ORDER.get(n['kind'], 2), n['id']))
    edges.sort(key=lambda edge: (edge['from'], edge['to'], edge['type'], edge.get('connection', '')))
    return {'root': str(root), 'level': level, 'focus': focus, 'layers': list(layers), 'nodes': ordered, 'edges': edges,
            'packages': packages, 'languages': languages, 'issues': issues,
            'truncated': truncated, 'note': NOTE + arch_n8n.NOTE}
