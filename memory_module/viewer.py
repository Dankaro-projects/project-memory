"""Bounded, read-only HTML snapshots. No browser database engine or network."""

import hashlib
import base64
from pathlib import Path
from .core import InvalidRecord, Conflict, dumps, _time


UI_SCRIPTS = ('state.js', 'records.js', 'api.js', 'sync.js', 'navigation.js',
              'board.js', 'editor.js', 'reviews.js', 'approvals.js', 'skills.js', 'map.js', 'reading.js', 'overview.js', 'boot.js')


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
            raise InvalidRecord('Subject does not match the episode.')
    since = _time(since) if since else None
    until = _time(until) if until else None
    if since and until and since > until:
        raise InvalidRecord('since must not be later than until.')
    conditions, args = [], []
    for column, value, comparison in [('episode_id', episode_id, '='), ('subject', subject, '='),
                                       ('created_at', since, '>='), ('created_at', until, '<=')]:
        if value is not None:
            conditions.append(f'{column}{comparison}?')
            args.append(value)
    where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
    # One read transaction prevents a snapshot assembled from different commits.
    memory.db.execute('BEGIN')
    try:
        event_ids = memory.db.execute('SELECT id FROM events' + where + ' ORDER BY created_at,rowid LIMIT ?',
                                      (*args, max_records + 1)).fetchall()
        if len(event_ids) > max_records:
            raise InvalidRecord('Export exceeds max_records. Select an episode, subject or date range.')
        events = [memory.read(row[0]) for row in event_ids]
        if episode_id:
            episodes = [memory.episode(episode_id)]
        else:
            ep_where = ' WHERE subject=?' if subject else ''
            ep_args = (subject,) if subject else ()
            ep_rows = memory.db.execute('SELECT id FROM episodes' + ep_where + ' ORDER BY created_at LIMIT ?', (*ep_args, max_records+1)).fetchall()
            episodes = [memory.episode(row[0]) for row in ep_rows]
            if since or until:
                relevant = {e['episode_id'] for e in events}
                episodes = [ep for ep in episodes if ep['id'] in relevant]
        # Include referenced evidence even outside the date/subject filter, with
        # its original subject shown. Unrelated sources obey the export filters.
        from .direction import history
        revisions=history(memory,max_records)
        if revisions['more']:raise InvalidRecord('Project revision history exceeds max_records.')
        linked_sources = {r['source_id'] for e in events for r in e['evidence']}
        source_ids = set(linked_sources) | {ref['source_id'] for rev in revisions['revisions'] for ref in rev['evidence']}
        if not episode_id:
            source_conditions, source_args = [], []
            for column, value, op in [('subject', subject, '='), ('checked_at', since, '>='), ('checked_at', until, '<=')]:
                if value is not None:
                    source_conditions.append(f'{column}{op}?'); source_args.append(value)
            source_where = ' WHERE ' + ' AND '.join(source_conditions) if source_conditions else ''
            source_ids.update(row[0] for row in memory.db.execute('SELECT id FROM sources' + source_where + ' LIMIT ?',
                                                                (*source_args, max_records+1)))
        if len(events) + len(episodes) + len(source_ids) > max_records:
            raise InvalidRecord('Export exceeds max_records including episodes and evidence. Narrow its scope.')
        sources = [memory.read(sid, detail=include_bodies and not memory.db.execute(
            'SELECT source_key FROM sources WHERE id=?', (sid,)).fetchone()[0].startswith(('workspace-skill:', 'workspace-skill-snapshot:'))) for sid in sorted(source_ids)]
        selected = {e['id'] for e in events}
        pending = [r for r in memory.pending(episode_id, limit=max_records)['decisions'] if r['id'] in selected]
        rows = []
        for ep in episodes:
            rows.append({'id': ep['id'], 'kind': 'episode', 'subject': ep['subject'], 'status': ep['status'],
                         'date': ep['created_at'], 'title': ep['title'], 'episode_id': ep['id'], 'detail': ep})
        for event in events:
            payload = event['payload']
            title = next((payload[k] for k in ['decision', 'summary', 'observed', 'question', 'do', 'text', 'reason', 'action'] if k in payload), event['kind'])
            rows.append({'id': event['id'], 'kind': event['kind'], 'subject': event['subject'], 'status': event['status'],
                         'date': event['created_at'], 'title': title, 'episode_id': event['episode_id'], 'detail': event})
        from .codex_host import exists, read_receipt
        if exists(memory):
            clauses, host_args = [], []
            for column, value, op in [('h.episode_id', episode_id, '='), ('ep.subject', subject, '='),
                                      ('h.created_at', since, '>='), ('h.created_at', until, '<=')]:
                if value is not None:
                    clauses.append(f'{column}{op}?'); host_args.append(value)
            host_where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
            host_rows = memory.db.execute('SELECT h.id,ep.subject FROM host_receipts h LEFT JOIN episodes ep ON ep.id=h.episode_id' + host_where + ' ORDER BY h.rowid LIMIT ?', (*host_args,max_records+1)).fetchall()
            if len(rows) + len(sources) + len(host_rows) > max_records:
                raise InvalidRecord('Export exceeds max_records including host receipts. Narrow its scope.')
            for host in host_rows:
                item = read_receipt(memory, host['id'])
                state = 'observed'
                if item['event_name'] == 'PreToolUse':
                    reported = memory.db.execute("SELECT 1 FROM host_receipts WHERE session_id=? AND tool_use_id=? AND event_name='PostToolUse' AND coalesce(json_extract(payload,'$.host'),'codex')=?", (item['session_id'],item['tool_use_id'],item['payload'].get('host','codex'))).fetchone()
                    reconciled = memory.db.execute("SELECT 1 FROM host_receipts WHERE event_name='Reconciled' AND json_extract(payload,'$.receipt_id')=? AND json_extract(payload,'$.resolution')!='unknown'",(item['id'],)).fetchone()
                    if not reported and not reconciled:
                        state = 'execution_unconfirmed'
                rows.append({'id':item['id'],'kind':'host_receipt','subject':host['subject'] or 'general','status':state,
                    'date':item['created_at'],'title':f'{item["event_name"]}: {item["tool_name"] or "Host session"}',
                    'episode_id':item['episode_id'] or '', 'detail':item})
        for source in sources:
            rows.append({'id': source['id'], 'kind': 'source', 'subject': source['subject'], 'status': source['status'],
                         'date': source['checked_at'], 'title': source['title'], 'episode_id': '', 'detail': source})
        revisions=revisions['revisions']
        if len(rows)+len(revisions)>max_records:
            raise InvalidRecord('Export exceeds max_records including project revisions.')
        for rev in revisions:
            rows.append({'id':'direction_'+str(rev['version']),'kind':'project_revision','subject':'general',
                'status':memory.direction().get('status','current') if rev['version']==memory.direction()['version'] else 'historical',
                'date':rev.get('created_at',''),'title':'Project requirements, version '+str(rev['version']),
                'episode_id':'','detail':rev})
        from .planning import card, latest
        work = [card(memory, ep['id']) for ep in episodes if ep['task_type']!='sprint']
        sprints = [{'id':ep['id'],'title':ep['title'],'intent':ep['objective'],'version':ep['version'],
                    'schedule':latest(memory,ep['id'],'sprint')} for ep in episodes if ep['task_type']=='sprint']
        from .maps import model
        maps = [{'episode_id': ep['id'], 'mode': mode, **model(memory, ep['id'], mode)}
                for ep in episodes for mode in ('workflow', 'architecture')
                if memory.db.execute('SELECT 1 FROM sources WHERE source_key=?',
                    ('workspace-map:' + ep['id'] + ':' + mode,)).fetchone()]
        snapshot = {'project': memory.project, 'exported_at': memory.now(), 'requirements': memory.requirements,
                    'scope': {'episode_id': episode_id, 'subject': subject, 'since': since, 'until': until},
                    'source_bodies_included': include_bodies, 'maps': maps, 'records': rows, 'pending': pending, 'work':work, 'sprints':sprints}
        encoded = dumps(snapshot).replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e')
        template = html_template()
        html = template.replace('__MEMORY_DATA__', encoded)
        for tag in ['style', 'script']:
            # Only the executable script has no attributes.
            body = template.split('<' + tag + '>', 1)[1].split('</' + tag + '>', 1)[0]
            digest = base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()
            html = html.replace('__' + tag.upper() + '_HASH__', digest)
        content = html.encode('utf-8')
        if len(content) > max_bytes:
            raise InvalidRecord('Export exceeds max_bytes. Narrow its scope or omit source bodies.')
    finally:
        memory.db.rollback()  # End the read transaction, including on failure.
    try:
        if replace:
            if destination.suffix.lower() != '.html':raise InvalidRecord('Use an .html destination when replacing a snapshot.')
            from .install import atomic
            atomic(destination, content.decode('utf-8'))
        else:
            with destination.open('xb') as stream:
                stream.write(content)
    except FileExistsError as exc:
        raise Conflict('HTML destination already exists. Choose a new filename for the snapshot.') from exc
    return {'path': str(destination.resolve()), 'records': len(rows), 'bytes': len(content),
            'exported_at': snapshot['exported_at'], 'snapshot': True}
