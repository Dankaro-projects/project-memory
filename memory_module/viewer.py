"""Bounded, read-only HTML snapshots that embed api.py responses. No browser database engine or network.

The snapshot holds `{'live': False, 'project', 'exported_at', 'scope', 'source_bodies_included', 'responses', 'omitted'}`.
Each response key is the endpoint name followed by its sorted query, for example
`records?limit=100&view=decisions`, which is the request the control panel makes
in live mode. The exported content is already limited to the scope, so keys do
not repeat the scope filters. Project wide views (now, work_graph, architecture,
learning, agents, kickoff, plan and components) are exported only when the
export has no scope, because they list records outside it; a scoped snapshot
names each of them in `omitted` with the reason. A record key holds the same
response as the live request, including the first body slice of a source when
bodies are included. A lineage graph shows the recorded neighbourhood of a decision and
can name related records outside a scope, as evidence references always did.
"""
import base64
import hashlib
from pathlib import Path
from subprocess import SubprocessError
from urllib.parse import urlencode

from . import api, delegation
from .core import InvalidRecord, Conflict, dumps, _time

UI_SCRIPTS = ('state.js', 'records.js', 'api.js', 'sync.js', 'navigation.js',
              'board.js', 'editor.js', 'reviews.js', 'approvals.js', 'skills.js', 'map.js', 'dependencies.js', 'reading.js', 'overview.js', 'boot.js')
PROJECT_VIEWS = ('now', 'work_graph', 'architecture', 'learning', 'agents', 'kickoff', 'plan', 'components')
RECORD_LIMIT = '100'


def html_template():
    root = Path(__file__).parent
    template = (root / 'viewer.html').read_text(encoding='utf-8')
    template = template.replace('__WORKSPACE_CSS__', (root / 'ui/workspace.css').read_text(encoding='utf-8'))
    script = '\n'.join((root / 'ui' / name).read_text(encoding='utf-8') for name in UI_SCRIPTS)
    template = template.replace('__WORKSPACE_JS__', script)
    # Opening the source file shows launch instructions; rendered pages reveal the workspace.
    template = template.replace('<div id="workspace-app" hidden>', '<div id="workspace-app">')
    for weight in (400, 700):
        font = (root / 'assets' / f'manrope-latin-{weight}.woff2').read_bytes()
        template = template.replace(f'__MANROPE_{weight}__', base64.b64encode(font).decode())
    return template


def render(template, data, *, live=False):
    """The page with hashed style and script elements and the escaped data.

    The hashes replace their placeholders before the data is inserted, so record
    text that contains a placeholder is kept unchanged.
    """
    page = template
    for tag in ('style', 'script'):
        # Only the executable script has no attributes.
        body = template.split('<' + tag + '>', 1)[1].split('</' + tag + '>', 1)[0]
        digest = base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()
        page = page.replace('__' + tag.upper() + '_HASH__', digest)
    if live:
        page = page.replace("base-uri 'none'", "connect-src 'self'; base-uri 'none'")
    encoded = dumps(data).replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e')
    return page.replace('__MEMORY_DATA__', encoded)


def response_key(endpoint, params=None):
    """The key of an embedded response: the endpoint and its query with sorted parameters."""
    if not params:
        return endpoint
    return endpoint + '?' + urlencode(sorted((key, str(value)) for key, value in params.items()))


def _where(pairs):
    conditions = []
    arguments = []
    for column, value, operator in pairs:
        if value is not None:
            conditions.append(f'{column}{operator}?')
            arguments.append(value)
    return (' WHERE ' + ' AND '.join(conditions) if conditions else ''), arguments


def _scope(memory, *, episode_id, subject, since, until, max_records):
    """The ids of every record inside the export scope, bounded by max_records."""
    from . import codex_host
    from .direction import history
    too_many = 'Export exceeds max_records. Select a work item, subject or date range.'
    where, arguments = _where([('episode_id', episode_id, '='), ('subject', subject, '='),
                               ('created_at', since, '>='), ('created_at', until, '<=')])
    event_rows = memory.db.execute('SELECT id,kind FROM events' + where + ' ORDER BY created_at,rowid LIMIT ?',
                                   (*arguments, max_records + 1)).fetchall()
    if len(event_rows) > max_records:
        raise InvalidRecord(too_many)
    events = [item['id'] for item in event_rows]
    decisions = [item['id'] for item in event_rows if item['kind'] == 'decision']
    if episode_id:
        episodes = [episode_id]
    else:
        clause, values = _where([('subject', subject, '=')])
        rows = memory.db.execute('SELECT id FROM episodes' + clause + ' ORDER BY created_at LIMIT ?', (*values, max_records + 1)).fetchall()
        episodes = [item[0] for item in rows]
        if since or until:
            relevant = {item[0] for item in memory.db.execute('SELECT DISTINCT episode_id FROM events' + where, arguments)}
            episodes = [item for item in episodes if item in relevant]
    revisions = history(memory, max_records)
    if revisions['more']:
        raise InvalidRecord('Project revision history exceeds max_records.')
    # Referenced evidence is included even outside the date or subject filter; unrelated sources obey the filters.
    sources = set()
    if events:
        marks = ','.join('?' for _ in events)
        sources.update(item[0] for item in memory.db.execute('SELECT DISTINCT source_id FROM dependencies WHERE event_id IN (' + marks + ')', events))
    sources.update(ref['source_id'] for revision in revisions['revisions'] for ref in revision['evidence'])
    if not episode_id:
        clause, values = _where([('subject', subject, '='), ('checked_at', since, '>='), ('checked_at', until, '<=')])
        sources.update(item[0] for item in memory.db.execute('SELECT id FROM sources' + clause + ' LIMIT ?', (*values, max_records + 1)))
    receipts = []
    if codex_host.exists(memory):
        clause, values = _where([('h.episode_id', episode_id, '='), ('ep.subject', subject, '='),
                                 ('h.created_at', since, '>='), ('h.created_at', until, '<=')])
        receipts = [item[0] for item in memory.db.execute(
            'SELECT h.id FROM host_receipts h LEFT JOIN episodes ep ON ep.id=h.episode_id' + clause + ' ORDER BY h.rowid LIMIT ?',
            (*values, max_records + 1))]
    directions = ['direction_' + str(revision['version']) for revision in revisions['revisions']]
    total = len(events) + len(episodes) + len(sources) + len(receipts) + len(directions)
    if total > max_records:
        raise InvalidRecord('Export exceeds max_records including work items, evidence, host receipts and project revisions. Narrow its scope.')
    return {'episodes': episodes, 'events': events, 'decisions': decisions, 'sources': sorted(sources),
            'receipts': receipts, 'directions': directions, 'total': total}


def _board(memory, episodes):
    """A board limited to the exported work items."""
    from .planning import STATES, card
    items = [card(memory, episode) for episode in episodes if memory.episode(episode)['task_type'] != 'sprint']
    counts = dict.fromkeys(STATES, 0)
    for item in items:
        counts[item['state']] += 1
    shown = items[:int(RECORD_LIMIT)]
    return {'cards': shown, 'counts': counts, 'total': len(items), 'offset': 0, 'more': len(shown) < len(items),
            'note': 'This board contains the work items inside the export scope.'}


def _sprints(memory, episodes):
    """The sprints inside the export scope."""
    from .planning import sprints
    found = [sprints(memory, 1, 0, episode)['sprints'][0] for episode in episodes if memory.episode(episode)['task_type'] == 'sprint']
    return {'sprints': found[:int(RECORD_LIMIT)], 'offset': 0, 'more': len(found) > int(RECORD_LIMIT)}


def export_html(memory, destination, *, episode_id=None, subject=None, since=None,
                until=None, replace=False, max_records=1000, max_bytes=10_000_000, include_bodies=False):
    if type(max_records) is not int or not 1 <= max_records <= 10000:
        raise InvalidRecord('max_records must be between 1 and 10000; narrow the export for larger histories.')
    if type(max_bytes) is not int or not 1024 <= max_bytes <= 50_000_000:
        raise InvalidRecord('max_bytes must be between 1024 and 50000000.')
    destination = Path(destination)
    if destination.resolve() == memory.path:
        raise InvalidRecord('HTML destination cannot be the database.')
    if subject is not None:
        memory._subject(subject)
    if episode_id:
        episode = memory.episode(episode_id)
        if subject is not None and subject != episode['subject']:
            raise InvalidRecord('Subject does not match the work item.')
    since = _time(since) if since else None
    until = _time(until) if until else None
    if since and until and since > until:
        raise InvalidRecord('since must not be later than until.')
    scoped = any(value is not None for value in (episode_id, subject, since, until))
    filters = {key: value for key, value in (('episode', episode_id), ('subject', subject), ('since', since), ('until', until)) if value}
    # One read transaction prevents a snapshot assembled from different commits.
    # The request cache keeps card states from being recomputed for every exported work item.
    own_cache = getattr(memory, '_api_cache', None) is None
    if own_cache:
        memory._api_cache = {}
    memory.db.execute('BEGIN')
    try:
        found = _scope(memory, episode_id=episode_id, subject=subject, since=since, until=until, max_records=max_records)
        responses = {}
        omitted = []
        health = api.health(memory, {})
        if scoped:
            titles = {item: memory.episode(item)['title'] for item in found['episodes']}
            health.update(episodes=[{'id': item, 'title': title} for item, title in titles.items()], episodes_more=False)
        responses['health'] = health
        for name in PROJECT_VIEWS:
            if scoped:
                omitted.append({'key': name, 'reason': 'This view covers the whole project. A scoped export leaves it out '
                                                       'so that records outside the scope are not included.'})
                continue
            try:
                responses[name] = api.ENDPOINTS[name](memory, {})
            except (InvalidRecord, OSError, SubprocessError, ValueError) as exc:
                omitted.append({'key': name, 'reason': str(exc)})
        responses['requirements'] = api.requirements(memory, {})
        list_params = {'limit': RECORD_LIMIT}
        responses[response_key('board', list_params)] = _board(memory, found['episodes']) if scoped else api.board(memory, list_params)
        responses[response_key('sprints', list_params)] = _sprints(memory, found['episodes']) if scoped else api.sprints(memory, list_params)
        for view in api.RECORD_VIEWS:
            params = {'view': view, 'limit': RECORD_LIMIT}
            responses[response_key('records', params)] = api.page(memory, {**params, **filters})
        for episode in found['episodes']:
            if memory.episode(episode)['task_type'] != 'sprint':
                responses[response_key('work', {'id': episode})] = api.work(memory, {'id': episode})
                for run in delegation.runs(memory, episode_id=episode, limit=100)['runs']:
                    responses[response_key('run', {'id': run['id']})] = api.run(memory, {'id': run['id']})
        record_ids = found['episodes'] + found['events'] + found['sources'] + found['receipts'] + found['directions']
        sources = set(found['sources'])
        for rid in record_ids:
            # Without bodies a source record carries its metadata only; otherwise the key matches the live response.
            value = api.record(memory, {'id': rid}) if include_bodies or rid not in sources else {'record': api.row(memory, rid)}
            responses[response_key('record', {'id': rid})] = value
        if include_bodies:
            for rid in found['sources']:
                offset = 0
                while True:
                    value = api.record(memory, {'id': rid, 'body_offset': str(offset)})
                    responses[response_key('record', {'body_offset': offset, 'id': rid})] = value
                    detail = value['record']['detail']
                    if not detail.get('body_more'):
                        break
                    offset = detail['next_offset']
        for decision in found['decisions']:
            responses[response_key('lineage', {'id': decision})] = api.lineage(memory, {'id': decision})
        snapshot = {'live': False, 'project': memory.project, 'exported_at': memory.now(),
                    'scope': {'episode_id': episode_id, 'subject': subject, 'since': since, 'until': until},
                    'source_bodies_included': include_bodies, 'responses': responses, 'omitted': omitted}
        content = render(html_template(), snapshot).encode('utf-8')
        if len(content) > max_bytes:
            raise InvalidRecord('Export exceeds max_bytes. Narrow its scope or omit source bodies.')
    finally:
        memory.db.rollback()  # End the read transaction, including on failure.
        if own_cache:
            del memory._api_cache
    try:
        if replace:
            if destination.suffix.lower() != '.html':
                raise InvalidRecord('Use an .html destination when replacing a snapshot.')
            from .install import atomic
            atomic(destination, content.decode('utf-8'))
        else:
            with destination.open('xb') as stream:
                stream.write(content)
    except FileExistsError as exc:
        raise Conflict('HTML destination already exists. Choose a new filename for the snapshot.') from exc
    return {'path': str(destination.resolve()), 'records': found['total'], 'responses': len(responses), 'bytes': len(content),
            'exported_at': snapshot['exported_at'], 'snapshot': True}
