"""Write the records as a folder of Markdown notes that Obsidian opens as a vault.

The export runs in one direction. The database is the source of truth and the
notes are a copy for reading, so a note edited inside the managed folder is lost
on the next run. A thought of the user belongs in a file outside that folder,
captured with the document operation, which keeps it as evidence with a version.

The export owns one folder inside the vault and writes or removes nothing
outside it. Inside it, only a file that carries the marker of this project in its
frontmatter is replaced or removed. Any other file is kept and named in the
report, and a folder that already holds files without the marker refuses the
run, so a mistyped path cannot overwrite an unrelated folder.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from .core import Conflict, InvalidRecord, _digest, dumps

FOLDER_NAME = 'Project Memory'
MARKER = 'project_memory_export'
SHORT = 8
NOTE_LIMIT = 120

# Event kinds that carry reasoning. Host receipts are tool observations and are added only on request.
EVENT_KINDS = ('decision', 'action', 'action_result', 'outcome', 'review', 'correction', 'research', 'lesson',
               'lesson_review', 'follow_up', 'episode_status', 'note', 'work_plan', 'sprint', 'hypothesis')

KIND_LABELS = {'episode': 'Work item', 'decision': 'Decision', 'action': 'Action', 'action_result': 'Action result',
               'outcome': 'Outcome', 'review': 'Review', 'correction': 'Correction', 'research': 'Research',
               'lesson': 'Lesson', 'lesson_review': 'Lesson review', 'follow_up': 'Follow up',
               'episode_status': 'Work item status', 'note': 'Note', 'work_plan': 'Work plan', 'sprint': 'Sprint',
               'hypothesis': 'Hypothesis', 'source': 'Source', 'host_receipt': 'Host receipt',
               'project_revision': 'Requirements'}

FOLDERS = {'episode': 'Work items', 'source': 'Sources', 'lesson': 'Lessons', 'lesson_review': 'Lessons',
           'project_revision': 'Requirements', 'host_receipt': 'Host receipts'}

# Short values belong in the frontmatter, where Obsidian groups and filters by them.
LABEL_FIELDS = ('assessment', 'severity', 'completion', 'attribution', 'execution_status', 'status', 'state',
                'autonomy', 'priority', 'owner', 'item_type', 'pattern_type', 'failure_type', 'model',
                'follow_up_owner', 'review_after', 'session_id', 'starts_on', 'ends_on', 'target', 'revision',
                'lesson_id', 'sprint_id', 'parent_id', 'worktree', 'host_reference', 'case_id', 'condition',
                'project_revision')

# The order in which the recorded text of a kind reads as an argument.
FIELD_ORDER = {
    'decision': ('decision', 'why', 'expected', 'reconsider_when', 'uncertainty', 'assumptions', 'alternatives',
                 'lessons_considered'),
    'outcome': ('observed', 'assessment_reason', 'human_corrections', 'tokens', 'duration_ms', 'context_characters',
                'research_calls', 'repeated_research', 'maintenance_ms'),
    'action': ('action',),
    'action_result': ('summary', 'artifact', 'duration_ms'),
    'review': ('summary', 'findings'),
    'correction': ('before', 'after', 'reason', 'scope'),
    'research': ('question', 'findings', 'gaps', 'queries', 'refresh_reason'),
    'lesson': ('when', 'do', 'because', 'exceptions', 'paths', 'keywords', 'roles'),
    'lesson_review': ('reason', 'paths', 'keywords', 'roles'),
    'follow_up': ('reason',),
    'episode_status': ('reason',),
    'note': ('text', 'kickoff_answers'),
    'work_plan': ('next_action', 'reason', 'scope', 'acceptance', 'paths', 'depends_on', 'focus'),
    'sprint': ('reason',),
}

HEADINGS = {'why': 'Why', 'expected': 'Expected', 'reconsider_when': 'Reconsider when', 'do': 'Do',
            'when': 'When', 'because': 'Because', 'next_action': 'Next action', 'assessment_reason': 'Assessment',
            'observed': 'Observed', 'duration_ms': 'Duration in milliseconds', 'context_characters': 'Context characters',
            'maintenance_ms': 'Maintenance in milliseconds', 'kickoff_answers': 'Kickoff questions answered',
            'lessons_considered': 'Lessons considered', 'human_corrections': 'Human corrections'}

SCHEMA = (
    'CREATE TABLE IF NOT EXISTS vault_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)',
    '''CREATE TABLE IF NOT EXISTS vault_notes (
 record_id TEXT PRIMARY KEY, path TEXT NOT NULL, content_hash TEXT NOT NULL, written_at TEXT NOT NULL)''',
    'CREATE INDEX IF NOT EXISTS vault_note_paths ON vault_notes(path)',
)

FOOTER = ('This note is written from the records of Project Memory. The database is the source of truth, and an edit '
          'here is lost on the next run. Keep a thought of your own in a file outside this folder and capture it with '
          'the document operation.')


# Settings of this project.

def exists(memory):
    return bool(memory.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='vault_settings'").fetchone())


def ensure(memory):
    for statement in SCHEMA:
        memory.db.execute(statement)


def setting(memory, key, default=None):
    if not exists(memory):
        return default
    row = memory.db.execute('SELECT value FROM vault_settings WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else default


def configured(memory):
    """The vault folder recorded by an earlier run, or None."""
    value = setting(memory, 'vault')
    return Path(value) if value else None


def hook_enabled(memory):
    """The session stop hook refreshes the vault unless the user switched it off."""
    return configured(memory) is not None and setting(memory, 'hook', True) is True


def configure(memory, *, vault=None, hook=None):
    with memory._write():
        ensure(memory)
        if vault is not None:
            memory.db.execute('INSERT OR REPLACE INTO vault_settings VALUES (?,?)', ('vault', dumps(str(vault))))
        if hook is not None:
            memory.db.execute('INSERT OR REPLACE INTO vault_settings VALUES (?,?)', ('hook', dumps(bool(hook))))
    return {'vault': str(configured(memory)) if configured(memory) else None, 'hook': hook_enabled(memory)}


# Note names.

UNSAFE = re.compile(r'[\\/:*?"<>|\[\]#^]')


def _short(record_id):
    """The record identifier shortened for a file name, keeping its prefix."""
    prefix, _, rest = record_id.partition('_')
    return prefix + '_' + rest[:SHORT] if rest else record_id


def _clean(text, limit=None):
    """A title that every file system accepts, with the characters Obsidian reads as link syntax removed."""
    value = UNSAFE.sub(' ', str(text)).replace('\n', ' ').replace('\r', ' ')
    value = re.sub(r'\s+', ' ', value).strip(' .')
    if limit and len(value) > limit:
        value = value[:limit].rstrip() + '...'
    return value or 'Untitled'


def note_name(record, *, full_id=False):
    """The stem of the note file: the kind, the title and the identifier, which keeps it unique and stable."""
    label = KIND_LABELS.get(record['kind'], record['kind'].replace('_', ' ').capitalize())
    identifier = record['id'] if full_id else _short(record['id'])
    if record['kind'] == 'episode':
        return _clean(record['title'], NOTE_LIMIT) + ' (' + identifier + ')'
    if record['kind'] == 'project_revision':
        return _clean(record['title'])
    return label + '. ' + _clean(record['title'], NOTE_LIMIT) + ' (' + identifier + ')'


def _folder(record, work_names):
    """A work item is a folder holding its own records; evidence and lessons sit in shared folders beside it."""
    kind = record['kind']
    if kind == 'episode':
        return 'Work items/' + work_names[record['id']]
    if kind in FOLDERS:
        return FOLDERS[kind]
    item = record.get('episode_id') or ''
    if item and item in work_names:
        return 'Work items/' + work_names[item]
    return 'Records'


def _names(records):
    """Assign one note path to each record, using the full identifier where two notes would collide."""
    # The work item folders are named first, because every record inside one is placed by its folder name.
    work_names, folders = {}, set()
    for record in records:
        if record['kind'] != 'episode':
            continue
        stem = note_name(record)
        if stem.lower() in folders:
            stem = note_name(record, full_id=True)
        folders.add(stem.lower())
        work_names[record['id']] = stem
    taken, paths = set(), {}
    for record in records:
        folder = _folder(record, work_names)
        stem = note_name(record)
        if (folder, stem.lower()) in taken:
            stem = note_name(record, full_id=True)
        taken.add((folder, stem.lower()))
        paths[record['id']] = (folder + '/' + stem + '.md', stem)
    return paths


# Rendering.

def _scalar(value):
    """One YAML scalar. Every string is quoted, so a colon or a wikilink cannot change the structure."""
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value).replace('\\', '\\\\').replace('"', '\\"').replace('\n', ' ')
    return '"' + text + '"'


def _frontmatter(fields):
    lines = ['---']
    for key, value in fields:
        if value is None or value == [] or value == '':
            continue
        if isinstance(value, (list, tuple)):
            lines.append(key + ':')
            lines.extend('  - ' + _scalar(item) for item in value)
        else:
            lines.append(key + ': ' + _scalar(value))
    lines.append('---')
    return '\n'.join(lines)


def _link(paths, record_id, fallback=None):
    """A wikilink to another note, or the identifier when that record is outside the export."""
    if record_id in paths:
        return '[[' + paths[record_id][1] + ']]'
    return fallback or record_id


def _heading(field):
    return HEADINGS.get(field) or field.replace('_', ' ').capitalize()


def _plain(value, paths):
    """One scalar in the body, as a wikilink when it names another record."""
    if isinstance(value, str) and _is_id(value):
        return _link(paths, value)
    return str(value)


def _value(value, paths):
    """One payload value as Markdown: a paragraph, a list of points, or a list of described items."""
    if isinstance(value, list):
        lines = []
        for item in value:
            if isinstance(item, dict):
                parts = []
                for key, inner in item.items():
                    parts.append(_heading(key).lower() + ': ' + _plain(inner, paths))
                lines.append('- ' + '; '.join(parts) + '.')
            else:
                lines.append('- ' + _plain(item, paths))
        return '\n'.join(lines)
    if isinstance(value, dict):
        return '\n'.join('- ' + _heading(key).lower() + ': ' + _plain(inner, paths) + '.' for key, inner in value.items())
    return _plain(value, paths)


ID = re.compile(r'^(event|episode|source|host)_[0-9a-f]{8,}$')


def _is_id(value):
    return bool(ID.match(value))


def render(memory, record, paths):
    """One note: the frontmatter Obsidian filters by, the recorded text unchanged, and the links to related records."""
    kind = record['kind']
    detail = record['detail']
    payload = detail.get('payload', {}) if isinstance(detail, dict) else {}
    item = record.get('episode_id') or ''
    fields = [('id', record['id']), ('kind', kind), ('state', record.get('state') or record.get('status', '')),
              ('subject', record.get('subject', '')), ('date', (record.get('date') or '')[:10])]
    if item and item != record['id']:
        fields.append(('work_item', _link(paths, item)))
    # A short value and a reference to another record read better as frontmatter, which Obsidian groups and filters by.
    promoted = [f for f, v in payload.items()
                if not isinstance(v, (list, dict)) and (f in LABEL_FIELDS or (isinstance(v, str) and _is_id(v)))]
    for field in promoted:
        value = payload[field]
        fields.append((field, _link(paths, value) if isinstance(value, str) and _is_id(value) else value))
    if kind == 'source':
        fields.extend([('source_key', detail.get('source_key', '')), ('version', detail.get('version', '')),
                       ('origin', detail.get('origin', ''))])
        if detail.get('document'):
            fields.append(('captured_file', detail['document']['path']))
    evidence = [_link(paths, ref['source_id']) for ref in detail.get('evidence', [])] if isinstance(detail, dict) else []
    if evidence:
        fields.append(('evidence', evidence))
    prior = [_link(paths, ref['event_id']) for ref in detail.get('links', [])] if isinstance(detail, dict) else []
    if prior:
        fields.append(('builds_on', prior))
    if isinstance(detail, dict) and detail.get('replaced_by'):
        fields.append(('replaced_by', _link(paths, detail['replaced_by'])))
    fields.extend([('aliases', [_clean(record['title'])]), (MARKER, True), ('project', memory.project)])

    body = ['# ' + _clean(record['title'])]
    if kind == 'episode':
        body.append('## Objective\n' + detail['objective'])
        body.append('## Completion criterion\n' + detail['criterion'])
    elif kind == 'source':
        body.append('## Summary\n' + detail.get('summary', ''))
        body.append('## Body\n' + memory.read(record['id'], detail=True)['body'])
    elif kind == 'project_revision':
        body.append('## Requirements\n' + '\n'.join('- ' + line for line in detail.get('requirements', [])))
        if detail.get('reason'):
            body.append('## Reason\n' + detail['reason'])
    elif kind == 'host_receipt':
        body.append('## Observed\n' + _value(detail, paths))
    order = [f for f in FIELD_ORDER.get(kind, ()) if f in payload and f not in promoted]
    order += [f for f in payload if f not in order and f not in promoted]
    for field in order:
        body.append('## ' + _heading(field) + '\n' + _value(payload[field], paths))
    if evidence:
        body.append('## Evidence\n' + '\n'.join(
            '- ' + _link(paths, ref['source_id']) + ' because ' + ref['reason'][0].lower() + ref['reason'][1:]
            for ref in detail['evidence']))
    body.append('---\n' + FOOTER)
    return _frontmatter(fields) + '\n\n' + '\n\n'.join(body) + '\n'


def readme(memory, counts):
    """The note that explains the folder to a reader who opens the vault without knowing where it came from."""
    return (_frontmatter([('kind', 'readme'), (MARKER, True), ('project', memory.project)]) + '\n\n'
            + '# ' + memory.project + '\n\n'
            + 'These notes are written from the records of Project Memory, the local memory of decisions, evidence '
              'and outcomes of this project. The database is the source of truth and these notes are a copy for '
              'reading.\n\n'
            + '## How to use the folder\n\n'
            + '- Read across time here: the trail of decisions on one topic, a lesson beside the failures that '
              'produced it, the evidence a decision rested on.\n'
            + '- Decide in the control panel, which you open with `project-memory view`. Nothing written here '
              'reaches the records.\n'
            + '- Link your own notes to these from outside this folder. An edit inside it is lost on the next run.\n'
            + '- Regenerate the folder with `project-memory export --obsidian <vault>`.\n\n'
            + '## What is here\n\n'
            + '\n'.join('- ' + name + ': ' + str(count) + '.' for name, count in counts) + '\n\n'
            + '---\n' + FOOTER + '\n')


# The export.

def _records(memory, include_receipts):
    """Every record in the export, oldest first, so that a work item is named before the records inside it."""
    from . import api, direction
    ids = [r[0] for r in memory.db.execute('SELECT id FROM episodes ORDER BY created_at, rowid')]
    kinds = EVENT_KINDS
    ids += [r[0] for r in memory.db.execute(
        'SELECT id FROM events WHERE kind IN (' + ','.join('?' * len(kinds)) + ') ORDER BY rowid', kinds)]
    ids += [r[0] for r in memory.db.execute('SELECT id FROM sources ORDER BY checked_at, rowid')]
    if include_receipts and memory.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='host_receipts'").fetchone():
        ids += [r[0] for r in memory.db.execute('SELECT id FROM host_receipts ORDER BY created_at, rowid')]
    records = [api.row(memory, rid) for rid in ids]
    revisions = direction.history(memory, limit=1000)['revisions']
    records += [api.row(memory, 'direction_' + str(item['version'])) for item in reversed(revisions)]
    return records


def _marked(path):
    """Whether a file carries the export marker, read from its frontmatter alone."""
    try:
        with path.open('r', encoding='utf-8') as stream:
            head = stream.read(4000)
    except (OSError, UnicodeDecodeError):
        return False
    if not head.startswith('---'):
        return False
    front = head.split('\n---', 1)[0]
    return (MARKER + ': true') in front


def _check_folder(managed, known):
    """Refuse a folder that holds files of someone else, so a mistyped path cannot overwrite an unrelated folder."""
    if not managed.exists():
        return []
    foreign = []
    for path in sorted(managed.rglob('*')):
        if path.is_dir():
            continue
        if str(path.relative_to(managed)) in known:
            continue
        if not _marked(path):
            foreign.append(str(path.relative_to(managed)))
    if foreign and not known:
        raise Conflict('The folder ' + str(managed) + ' holds ' + str(len(foreign)) + ' file(s) that this export did '
                       'not write, and no note of this project. Choose another vault, or remove the folder first. '
                       'The first is ' + foreign[0] + '.')
    return foreign


def export(memory, vault=None, *, include_receipts=False, full=False, hook=None):
    """Write the records into the managed folder of the vault and report what changed."""
    started = time.monotonic()
    destination = Path(vault).expanduser().resolve() if vault else configured(memory)
    if destination is None:
        raise InvalidRecord('Name the vault folder once with --obsidian; later runs reuse it.')
    managed = destination / FOLDER_NAME
    own_cache = getattr(memory, '_api_cache', None) is None
    if own_cache:
        memory._api_cache = {}
    try:
        records = _records(memory, include_receipts)
        paths = _names(records)
        stored = {row['record_id']: dict(row) for row in memory.db.execute(
            'SELECT record_id,path,content_hash FROM vault_notes')} if exists(memory) else {}
        foreign = _check_folder(managed, {item['path'] for item in stored.values()})
        written, moved, unchanged, counts = [], [], 0, {}
        current = {}
        for record in records:
            relative, _ = paths[record['id']]
            counts[KIND_LABELS.get(record['kind'], record['kind'])] = counts.get(
                KIND_LABELS.get(record['kind'], record['kind']), 0) + 1
            content = render(memory, record, paths)
            digest = _digest(content)
            current[record['id']] = {'path': relative, 'content_hash': digest}
            before = stored.get(record['id'])
            if before and before['path'] != relative:
                old = managed / before['path']
                if old.is_file() and _marked(old):
                    old.unlink()
                moved.append(relative)
            elif before and before['content_hash'] == digest and not full and (managed / relative).is_file():
                unchanged += 1
                continue
            target = managed / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding='utf-8')
            written.append(relative)
        removed = []
        for record_id, before in stored.items():
            if record_id in current:
                continue
            old = managed / before['path']
            if old.is_file() and _marked(old):
                old.unlink()
            removed.append(before['path'])
        managed.mkdir(parents=True, exist_ok=True)
        (managed / 'README.md').write_text(readme(memory, sorted(counts.items())), encoding='utf-8')
        for folder in sorted((p for p in managed.rglob('*') if p.is_dir()), reverse=True):
            if not any(folder.iterdir()):
                folder.rmdir()
    finally:
        if own_cache:
            del memory._api_cache
    now = memory.now()
    with memory._write():
        ensure(memory)
        memory.db.execute('DELETE FROM vault_notes')
        memory.db.executemany('INSERT INTO vault_notes VALUES (?,?,?,?)',
                              [(rid, item['path'], item['content_hash'], now) for rid, item in current.items()])
        memory.db.execute('INSERT OR REPLACE INTO vault_settings VALUES (?,?)', ('vault', dumps(str(destination))))
        memory.db.execute('INSERT OR REPLACE INTO vault_settings VALUES (?,?)',
                          ('include_receipts', dumps(bool(include_receipts))))
        memory.db.execute('INSERT OR REPLACE INTO vault_settings VALUES (?,?)', ('exported_at', dumps(now)))
        if hook is not None:
            memory.db.execute('INSERT OR REPLACE INTO vault_settings VALUES (?,?)', ('hook', dumps(bool(hook))))
    return {'vault': str(destination), 'folder': str(managed), 'notes': len(current), 'written': len(written),
            'moved': len(moved), 'removed': len(removed), 'unchanged': unchanged, 'kept': foreign,
            'hook': hook_enabled(memory), 'seconds': round(time.monotonic() - started, 3),
            'note': 'The records in the database stay the source of truth. These notes are a copy for reading, and an '
                    'edit inside the folder is lost on the next run.'}


def latest_record(memory):
    """The time of the newest record of any kind, which decides whether the vault is behind."""
    times = [memory.db.execute('SELECT max(created_at) FROM episodes').fetchone()[0],
             memory.db.execute('SELECT max(created_at) FROM events').fetchone()[0],
             memory.db.execute('SELECT max(checked_at) FROM sources').fetchone()[0]]
    return max([t for t in times if t], default='')


def needs_refresh(memory):
    """Whether a record was written after the last export and after the last refresh that was already started."""
    if not hook_enabled(memory):
        return False
    done = max(setting(memory, 'exported_at', '') or '', setting(memory, 'refresh_requested_at', '') or '')
    return latest_record(memory) > done


def refresh_detached(memory):
    """Start a refresh that outlives the hook, because a full run does not fit the ten second hook budget."""
    from .install import python_args
    folder = Path(memory.path).parent / 'vault-runs'
    folder.mkdir(parents=True, exist_ok=True)
    log = folder / 'refresh.log'
    with memory._write():
        ensure(memory)
        memory.db.execute('INSERT OR REPLACE INTO vault_settings VALUES (?,?)',
                          ('refresh_requested_at', dumps(memory.now())))
    with log.open('a', encoding='utf-8') as out:
        process = subprocess.Popen(python_args('memory_module.vault') + ['--db', str(memory.path)],
                                   stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                                   start_new_session=os.name != 'nt')
    return {'started': True, 'pid': process.pid, 'log': str(log)}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Refresh the Obsidian vault of a project.')
    parser.add_argument('--db', required=True)
    args = parser.parse_args(argv)
    from .core import Memory
    with Memory(args.db) as memory:
        result = export(memory, include_receipts=setting(memory, 'include_receipts', False))
    print(dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
