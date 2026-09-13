"""Explicit local Markdown capture using the existing immutable source versions."""

import os
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .core import InvalidRecord, _digest, _time

PREFIX = 'local-markdown:'
MAX_BYTES = 5_000_000


def document_path(source_key):
    if not source_key.startswith(PREFIX):
        return None
    uri = urlsplit(source_key[len(PREFIX):])
    if uri.scheme != 'file' or uri.netloc or uri.query or uri.fragment:
        return None
    path = Path(unquote(uri.path))
    return path if path.is_absolute() else None


def read_markdown(path):
    if path.suffix.lower() not in {'.md', '.markdown'} or not path.is_file():
        raise InvalidRecord('Select an existing local Markdown file.')
    with path.open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise InvalidRecord('The document exceeds 5 MB. Select a smaller document.')
    body = raw.decode('utf-8')
    if not body.strip():
        raise InvalidRecord('The document is empty.')
    return body


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


def capture(memory, path, *, subject='general', review_after=None):
    """Capture bytes as UTF-8 text; never infer decisions or rewrite the file."""
    if not isinstance(path, str) or not path.strip():
        raise InvalidRecord('path must name a local Markdown file.')
    # Keep the selected path, including a symlink, so retargeting it is detected.
    path = Path(os.path.abspath(os.path.expanduser(path)))
    body = read_markdown(path)
    key = PREFIX + path.as_uri()
    memory._subject(subject)
    with memory._write():
        previous = memory.db.execute('SELECT * FROM sources WHERE source_key=? ORDER BY version DESC LIMIT 1', (key,)).fetchone()
        if previous and previous['subject'] != subject:
            raise InvalidRecord('A document cannot change subject. Use explicit cross-subject evidence links.')
        deadline = _time(review_after) if review_after is not None else (previous['review_after'] if previous else None)
        if previous and previous['content_hash'] == _digest(body) and previous['review_after'] == deadline:
            return {'id': previous['id'], 'version': previous['version'], 'status': memory.source_status(previous['id']), 'created': False}
        result = memory.source(key, path.name,
            'This file is captured verbatim. Importing it does not approve its proposals or change project requirements.',
            body, 'document', subject=subject, review_after=deadline)
        return {**result, 'created': True}
