"""Explicit local Markdown capture using the existing immutable source versions."""

import os
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname

from .core import InvalidRecord, _digest, _time

PREFIX = 'local-markdown:'
MAX_BYTES = 5_000_000


def document_path(source_key):
    if not source_key.startswith(PREFIX):
        return None
    uri = urlsplit(source_key[len(PREFIX):])
    if uri.scheme != 'file' or uri.netloc or uri.query or uri.fragment:
        return None
    path = Path(url2pathname(uri.path))
    return path if path.is_absolute() else None


def read_markdown(path):
    if path.suffix.lower() not in {'.md', '.markdown'} or not path.is_file():
        raise InvalidRecord('Select an existing local Markdown file.')
    before = path.stat()
    with path.open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    after = path.stat()
    if (before.st_ino, before.st_mtime_ns, before.st_size) != (after.st_ino, after.st_mtime_ns, after.st_size):
        raise InvalidRecord('The document changed while it was being read. Retry after the save completes.')
    if len(raw) > MAX_BYTES:
        raise InvalidRecord('The document exceeds 5 MB. Select a smaller document.')
    body = raw.decode('utf-8')
    if not body.strip():
        raise InvalidRecord('The document is empty.')
    return body


def sync(memory, *, limit=100, offset=0, check=False):
    """Inspect or refresh only Markdown paths that were explicitly captured before."""
    if type(limit) is not int or not 1 <= limit <= 1000 or type(offset) is not int or offset < 0:
        raise InvalidRecord('Use limit 1–1000 and a nonnegative offset.')
    rows = memory.db.execute('''SELECT id,source_key,subject,content_hash FROM sources s WHERE source_key LIKE ?
        AND version=(SELECT max(version) FROM sources x WHERE x.source_key=s.source_key)
        ORDER BY source_key LIMIT ? OFFSET ?''', (PREFIX+'%', limit+1, offset)).fetchall()
    records = []
    for row in rows[:limit]:
        path = document_path(row['source_key'])
        item = {'id': row['id'], 'path': str(path), 'created': False,
                'status': file_status(row['source_key'], row['content_hash'])}
        if path is not None and any(p.is_symlink() for p in (path, *path.parents)):
            item['status'] = 'symlink_requires_review'
        elif not check and item['status'] == 'file_changed':
            try: item.update(capture(memory, str(path), subject=row['subject']))
            except (OSError, UnicodeError, InvalidRecord) as exc:
                item.update(status='capture_failed', error=str(exc))
        records.append(item)
    return {'documents': records, 'created': sum(r['created'] for r in records),
            'more': len(rows)>limit, 'next_offset': offset+len(records),
            'mode': 'check' if check else 'sync',
            'note': 'Only previously selected Markdown paths are checked. Captures preserve source text without approving its meaning. Missing or moved files require explicit selection; symlinks require review.'}


def file_status(source_key, content_hash):
    path = document_path(source_key)
    if path is None:
        return None
    try:
        if not path.exists():
            return 'file_missing'
        body = read_markdown(path)
    except (OSError, UnicodeError, InvalidRecord):
        return 'file_unreadable'
    return 'current_copy' if _digest(body) == content_hash else 'file_changed'


def capture(memory, path, *, subject=None, review_after=None):
    """Capture bytes as UTF-8 text; never infer decisions or rewrite the file."""
    if not isinstance(path, str) or not path.strip():
        raise InvalidRecord('path must name a local Markdown file.')
    # Keep the selected path, including a symlink, so retargeting it is detected.
    path = Path(os.path.abspath(os.path.expanduser(path)))
    body = read_markdown(path)
    key = PREFIX + path.as_uri()
    if subject is not None:memory._subject(subject)
    with memory._write():
        previous = memory.db.execute('SELECT id,version,subject,content_hash,review_after FROM sources WHERE source_key=? ORDER BY version DESC LIMIT 1', (key,)).fetchone()
        subject=subject if subject is not None else (previous['subject'] if previous else 'general')
        if previous and previous['subject'] != subject:
            raise InvalidRecord('This document already belongs to '+previous['subject']+'. Omit subject when refreshing to retain it. Use explicit cross-subject evidence links.')
        deadline = _time(review_after) if review_after is not None else (previous['review_after'] if previous else None)
        if previous and previous['content_hash'] == _digest(body) and previous['review_after'] == deadline:
            return {'id': previous['id'], 'version': previous['version'], 'status': memory.source_status(previous['id']), 'created': False}
        result = memory.source(key, path.name,
            'This file is captured verbatim. Importing it does not approve its proposals or change project requirements.',
            body, 'document', subject=subject, review_after=deadline)
        return {**result, 'created': True}
