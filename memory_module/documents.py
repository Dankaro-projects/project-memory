"""Explicit local document capture using the existing immutable source versions.

Markdown and other text files are captured verbatim. Word, PowerPoint and Excel
files are Office Open XML archives; their text is extracted with the standard
library and captured instead of the binary file. Formatting, images, charts and
formulas are not part of the extracted text.
"""

import io
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
from urllib.request import url2pathname
import zipfile
from xml.etree import ElementTree

from .core import InvalidRecord, _digest, _time

PREFIX = 'local-markdown:'
FILE_PREFIX = 'local-file:'
PREFIXES = (PREFIX, FILE_PREFIX)
MAX_BYTES = 5_000_000
MARKDOWN_SUFFIXES = {'.md', '.markdown'}
TEXT_SUFFIXES = {'.txt', '.text', '.csv', '.tsv', '.json', '.yaml', '.yml', '.xml', '.html', '.htm'}
OFFICE_SUFFIXES = {'.docx', '.pptx', '.xlsx'}
SUPPORTED = MARKDOWN_SUFFIXES | TEXT_SUFFIXES | OFFICE_SUFFIXES
MAX_MEMBER_BYTES = 20_000_000
MAX_EXTRACTED_CHARACTERS = 2_000_000
UNSUPPORTED = ('Select an existing local document: Markdown, plain text, CSV, JSON, YAML, XML, HTML, '
               'or a Word, PowerPoint or Excel file.')
WORD = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
DRAWING = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
SHEET = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'


def source_key(path):
    """The source key of a captured file. Markdown keeps its original prefix."""
    prefix = PREFIX if path.suffix.lower() in MARKDOWN_SUFFIXES else FILE_PREFIX
    return prefix + path.as_uri()


def document_path(source_key):
    prefix = next((value for value in PREFIXES if source_key.startswith(value)), None)
    if prefix is None:
        return None
    uri = urlsplit(source_key[len(prefix):])
    if uri.scheme != 'file' or uri.netloc or uri.query or uri.fragment:
        return None
    path = Path(url2pathname(uri.path))
    return path if path.is_absolute() else None


def _number(name):
    found = re.search(r'(\d+)\.xml$', name)
    return int(found.group(1)) if found else 0


def _member(archive, name):
    info = archive.getinfo(name)
    if info.file_size > MAX_MEMBER_BYTES:
        raise InvalidRecord('A part of the Office file is too large to extract. Select a smaller file.')
    return ElementTree.fromstring(archive.read(name))


def _paragraphs(root, paragraph, text):
    lines = []
    for item in root.iter(paragraph):
        line = ''.join(node.text or '' for node in item.iter(text)).strip()
        if line:
            lines.append(line)
    return lines


def _word(archive):
    return _paragraphs(_member(archive, 'word/document.xml'), WORD + 'p', WORD + 't')


def _slides(archive):
    names = sorted((name for name in archive.namelist() if re.fullmatch(r'ppt/slides/slide\d+\.xml', name)), key=_number)
    lines = []
    for name in names:
        number = _number(name)
        lines.append(f'Slide {number}')
        lines.extend(_paragraphs(_member(archive, name), DRAWING + 'p', DRAWING + 't'))
        notes = f'ppt/notesSlides/notesSlide{number}.xml'
        if notes in archive.namelist():
            text = [line for line in _paragraphs(_member(archive, notes), DRAWING + 'p', DRAWING + 't') if not line.isdigit()]
            if text:
                lines.append(f'Notes for slide {number}')
                lines.extend(text)
    return lines


def _sheets(archive):
    shared = []
    if 'xl/sharedStrings.xml' in archive.namelist():
        for item in _member(archive, 'xl/sharedStrings.xml').iter(SHEET + 'si'):
            shared.append(''.join(node.text or '' for node in item.iter(SHEET + 't')))
    titles = []
    if 'xl/workbook.xml' in archive.namelist():
        titles = [sheet.get('name') for sheet in _member(archive, 'xl/workbook.xml').iter(SHEET + 'sheet')]
    names = sorted((name for name in archive.namelist() if re.fullmatch(r'xl/worksheets/sheet\d+\.xml', name)), key=_number)
    lines = []
    for index, name in enumerate(names):
        title = titles[index] if index < len(titles) and titles[index] else f'Sheet {_number(name)}'
        lines.append('Sheet ' + title)
        for row in _member(archive, name).iter(SHEET + 'row'):
            cells = []
            for cell in row.iter(SHEET + 'c'):
                kind = cell.get('t')
                value = cell.find(SHEET + 'v')
                if kind == 's' and value is not None and (value.text or '').isdigit() and int(value.text) < len(shared):
                    text = shared[int(value.text)]
                elif kind == 'inlineStr':
                    text = ''.join(node.text or '' for node in cell.iter(SHEET + 't'))
                else:
                    text = value.text if value is not None and value.text else ''
                cells.append(text.replace('\t', ' ').replace('\n', ' '))
            if any(cells):
                lines.append('\t'.join(cells).rstrip('\t'))
    return lines


def office_text(data, suffix):
    """Return the text of a Word, PowerPoint or Excel file, or raise InvalidRecord."""
    suffix = suffix.lower()
    if suffix not in OFFICE_SUFFIXES:
        raise InvalidRecord(UNSUPPORTED)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            lines = {'.docx': _word, '.pptx': _slides, '.xlsx': _sheets}[suffix](archive)
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError, ValueError) as exc:
        raise InvalidRecord('The Office file could not be read as a Word, PowerPoint or Excel document.') from exc
    text = '\n'.join(lines)
    if len(text) > MAX_EXTRACTED_CHARACTERS:
        text = text[:MAX_EXTRACTED_CHARACTERS] + f'\n\nThe extracted text was truncated to {MAX_EXTRACTED_CHARACTERS:,} characters.'
    return text


def read_document(path):
    """Read a supported local document and return its text."""
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED or not path.is_file():
        raise InvalidRecord(UNSUPPORTED)
    before = path.stat()
    with path.open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    after = path.stat()
    if (before.st_ino, before.st_mtime_ns, before.st_size) != (after.st_ino, after.st_mtime_ns, after.st_size):
        raise InvalidRecord('The document changed while it was being read. Retry after the save completes.')
    if len(raw) > MAX_BYTES:
        raise InvalidRecord('The document exceeds 5 MB. Select a smaller document.')
    body = office_text(raw, suffix) if suffix in OFFICE_SUFFIXES else raw.decode('utf-8')
    if not body.strip():
        raise InvalidRecord('The document is empty.')
    return body


read_markdown = read_document


def sync(memory, *, limit=100, offset=0, check=False):
    """Inspect or refresh only document paths that were explicitly captured before."""
    if type(limit) is not int or not 1 <= limit <= 1000 or type(offset) is not int or offset < 0:
        raise InvalidRecord('Use limit 1–1000 and a nonnegative offset.')
    rows = memory.db.execute('''SELECT id,source_key,subject,content_hash FROM sources s WHERE (source_key LIKE ? OR source_key LIKE ?)
        AND version=(SELECT max(version) FROM sources x WHERE x.source_key=s.source_key)
        ORDER BY source_key LIMIT ? OFFSET ?''', (PREFIX+'%', FILE_PREFIX+'%', limit+1, offset)).fetchall()
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
            'note': 'Only previously selected document paths are checked. Captures preserve source text without approving its meaning. Missing or moved files require explicit selection; symlinks require review.'}


def file_status(source_key, content_hash):
    path = document_path(source_key)
    if path is None:
        return None
    try:
        if not path.exists():
            return 'file_missing'
        body = read_document(path)
    except (OSError, UnicodeError, InvalidRecord):
        return 'file_unreadable'
    return 'current_copy' if _digest(body) == content_hash else 'file_changed'


def capture(memory, path, *, subject=None, review_after=None):
    """Capture a document as UTF-8 text; never infer decisions or rewrite the file."""
    if not isinstance(path, str) or not path.strip():
        raise InvalidRecord('path must name a local document.')
    # Keep the selected path, including a symlink, so retargeting it is detected.
    path = Path(os.path.abspath(os.path.expanduser(path)))
    body = read_document(path)
    key = source_key(path)
    if subject is not None:memory._subject(subject)
    with memory._write():
        previous = memory.db.execute('SELECT id,version,subject,content_hash,review_after FROM sources WHERE source_key=? ORDER BY version DESC LIMIT 1', (key,)).fetchone()
        subject=subject if subject is not None else (previous['subject'] if previous else 'general')
        if previous and previous['subject'] != subject:
            raise InvalidRecord('This document already belongs to '+previous['subject']+'. Omit subject when refreshing to retain it. Use explicit cross-subject evidence links.')
        deadline = _time(review_after) if review_after is not None else (previous['review_after'] if previous else None)
        if previous and previous['content_hash'] == _digest(body) and previous['review_after'] == deadline:
            return {'id': previous['id'], 'version': previous['version'], 'status': memory.source_status(previous['id']), 'created': False}
        if path.suffix.lower() in OFFICE_SUFFIXES:
            summary = ('The text of this file was extracted and captured. Formatting, images, charts and formulas are not included. '
                       'Importing it does not approve its proposals or change project requirements.')
        else:
            summary = 'This file is captured verbatim. Importing it does not approve its proposals or change project requirements.'
        result = memory.source(key, path.name, summary, body, 'document', subject=subject, review_after=deadline)
        return {**result, 'created': True}
