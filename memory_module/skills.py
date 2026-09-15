"""Project-local skill packages and explicit, versioned work selections."""
import base64
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import stat
import zipfile

from .core import InvalidRecord, Conflict, _text, dumps

PREFIX = 'workspace-skill:'
SELECTION = 'workspace-skill-selection:'
MAX_BYTES = 1_000_000
MAX_FILES = 100


def project_root(memory):
    row = memory.db.execute("SELECT value FROM settings WHERE key='workspace_project'").fetchone()
    if row:
        return Path(json.loads(row[0]))
    from .reviews import configured
    config = configured(memory)
    return Path(config['project']) if config else memory.path.parent.parent


def metadata(text):
    """Read the standard scalar discovery fields; preserve all other YAML verbatim."""
    lines = text.replace('\r\n', '\n').splitlines()
    if not lines or lines[0] != '---' or '---' not in lines[1:]:
        raise InvalidRecord('SKILL.md needs YAML frontmatter with name and description.')
    header = lines[1:lines[1:].index('---') + 1]
    result = {}
    for i, line in enumerate(header):
        match = re.match(r'^(name|description|license|compatibility):\s*(.*)$', line)
        if not match:
            continue
        key, value = match.groups()
        if key in result:
            raise InvalidRecord('Duplicate skill metadata field: ' + key)
        if value in {'|', '|-', '|+', '>', '>-', '>+'}:
            block = []
            for extra in header[i + 1:]:
                if extra and not extra[0].isspace():
                    break
                block.append(extra.strip())
            value = ('\n' if value.startswith('|') else ' ').join(block).strip()
        elif value.startswith('"'):
            try:
                value = json.loads(value)
            except ValueError as exc:
                raise InvalidRecord('Use a plain, quoted or block scalar for ' + key + '.') from exc
        elif value.startswith("'") and value.endswith("'"):
            value = value[1:-1].replace("''", "'")
        else:
            value = value.split(' #', 1)[0].strip()
            continuation = []
            for extra in header[i + 1:]:
                if extra and not extra[0].isspace():
                    break
                continuation.append(extra.strip())
            if continuation:
                value += ' ' + ' '.join(continuation)
            if value.startswith(('[', '{', '&', '*', '!')):
                raise InvalidRecord('Use a scalar value for skill field ' + key + '.')
        result[key] = value
    if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', result.get('name', '')) or len(result['name']) > 64:
        raise InvalidRecord('Skill names use 1–64 lowercase letters, digits and single hyphens.')
    _text(result.get('description'), 'skill description', 1024)
    return result


def package(files):
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES:
        raise InvalidRecord('Import 1–100 files per skill.')
    result = {}; total = 0
    for item in files:
        if not isinstance(item, dict) or set(item) != {'path', 'content'}:
            raise InvalidRecord('Each skill file needs path and base64 content.')
        name = item['path']
        if not isinstance(name, str) or len(name) > 300:
            raise InvalidRecord('Invalid skill file path.')
        path = PurePosixPath(name)
        if path.is_absolute() or any(p in {'.', '..', ''} for p in name.split('/')) or '\\' in name or ':' in name:
            raise InvalidRecord('Skill files must stay inside their package.')
        if name in result or any(p.startswith('.') for p in path.parts):
            raise InvalidRecord('Duplicate or hidden skill files are not accepted.')
        try:
            raw = base64.b64decode(item['content'], validate=True)
        except (ValueError, TypeError) as exc:
            raise InvalidRecord('Invalid skill file encoding.') from exc
        total += len(raw)
        if total > MAX_BYTES:
            raise InvalidRecord('A skill package may contain at most 1 MB of files.')
        result[name] = item['content']
    if 'SKILL.md' not in result:
        raise InvalidRecord('Select a skill folder containing SKILL.md at its root.')
    try:
        info = metadata(base64.b64decode(result['SKILL.md']).decode('utf-8'))
    except UnicodeDecodeError as exc:
        raise InvalidRecord('SKILL.md must contain UTF-8 text.') from exc
    return {'metadata': info, 'files': dict(sorted(result.items()))}


def unpack(encoded):
    try:
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > MAX_BYTES:
            raise InvalidRecord('The ZIP archive exceeds 1 MB.')
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = [f for f in archive.infolist() if not f.is_dir()]
            if len(entries) > MAX_FILES or sum(f.file_size for f in entries) > MAX_BYTES:
                raise InvalidRecord('The expanded skill exceeds the file or size limit.')
            for f in entries:
                if stat.S_ISLNK(f.external_attr >> 16) or f.flag_bits & 1:
                    raise InvalidRecord('Skill archives cannot contain links or encrypted files.')
            # Accept either a package root or a single enclosing folder.
            names = [f.filename for f in entries]
            if any(PurePosixPath(n).is_absolute() or '..' in PurePosixPath(n).parts or '\\' in n or ':' in n for n in names):
                raise InvalidRecord('Archive paths must stay inside the skill package.')
            prefix = names[0].split('/')[0] + '/' if names and all('/' in n and n.split('/')[0] == names[0].split('/')[0] for n in names) else ''
            return package([{'path': f.filename[len(prefix):], 'content': base64.b64encode(archive.read(f)).decode()} for f in entries])
    except (zipfile.BadZipFile, ValueError, RuntimeError) as exc:
        raise InvalidRecord('The skill ZIP could not be read.') from exc


def local_packages(memory):
    root = project_root(memory).resolve()
    found = []
    for location in ('.agents/skills', '.claude/skills', '.codex/skills'):
        folder = root / location
        if folder.is_symlink() or not folder.is_dir() or not folder.resolve().is_relative_to(root):
            continue
        for child in sorted(folder.iterdir()):
            if child.is_symlink() or not child.is_dir():
                continue
            manifest = child / 'SKILL.md'
            if manifest.is_symlink() or not manifest.is_file():
                continue
            relative = child.relative_to(root).as_posix()
            value = {'id': 'local_' + hashlib.sha256(relative.encode()).hexdigest()[:24], 'location': relative,
                     'origin': 'project', 'status': 'available', 'path': child}
            try:
                if manifest.stat().st_size > MAX_BYTES:
                    raise InvalidRecord('SKILL.md exceeds 1 MB.')
                text = manifest.read_text(encoding='utf-8')
                value.update(metadata(text)); value['revision'] = hashlib.sha256(text.encode()).hexdigest()
            except (OSError, UnicodeError, InvalidRecord) as exc:
                value.update(name=child.name, description='', status='invalid', error=str(exc))
            found.append(value)
    return found


def catalog(memory, limit=25, offset=0, query=''):
    if not 1 <= limit <= 100 or offset < 0:
        raise InvalidRecord('Use limit 1–100 and a nonnegative offset.')
    values = [{k: v for k, v in item.items() if k != 'path'} for item in local_packages(memory)]
    rows = memory.db.execute('''SELECT id,source_key,version,summary,content_hash FROM sources s WHERE source_key LIKE ?
        AND version=(SELECT max(version) FROM sources WHERE source_key=s.source_key) ORDER BY source_key''', (PREFIX + '%',))
    for row in rows:
        values.append({'id': row['id'], 'name': row['source_key'][len(PREFIX):], 'description': row['summary'], 'origin': 'imported', 'status': 'available',
                       'version': row['version'], 'revision': row['content_hash'], 'location': 'Project Memory'})
    values = [v for v in values if query.lower() in (v['name'] + ' ' + v['description']).lower()]
    return {'skills': values[offset:offset + limit], 'total': len(values), 'more': offset + limit < len(values),
            'next_offset': min(offset + limit, len(values))}


def load(memory, skill_id):
    _text(skill_id, 'skill id', 100)
    if skill_id.startswith('local_'):
        item = next((v for v in local_packages(memory) if v['id'] == skill_id), None)
        if not item or item['status'] == 'invalid':
            raise InvalidRecord('The project skill is missing or invalid.')
        folder = item.pop('path'); files = []; size = 0
        for p in folder.rglob('*'):
            if p.is_symlink():
                raise InvalidRecord('Skill packages cannot contain filesystem links.')
            if not p.is_file():
                continue
            size += p.stat().st_size
            if size > MAX_BYTES or len(files) >= MAX_FILES:
                raise InvalidRecord('The skill exceeds 100 files or 1 MB.')
            files.append({'path': p.relative_to(folder).as_posix(), 'content': base64.b64encode(p.read_bytes()).decode()})
        bundle = package(files)
        item['revision'] = hashlib.sha256(dumps(bundle).encode()).hexdigest()
    else:
        row = memory.db.execute('SELECT * FROM sources WHERE id=? AND (source_key LIKE ? OR source_key LIKE ?)', (skill_id, PREFIX + '%', 'workspace-skill-snapshot:%')).fetchone()
        if not row:
            raise InvalidRecord('Skill was not found in this project.')
        bundle = json.loads(row['body'])
        item = {'id': row['id'], **bundle['metadata'], 'revision': row['content_hash'], 'version': row['version'],
                'status': 'snapshot' if row['source_key'].startswith('workspace-skill-snapshot:') else memory.source_status(row['id']),
                'origin': 'snapshot' if row['source_key'].startswith('workspace-skill-snapshot:') else 'imported'}
    return item, bundle


def read(memory, skill_id, file=None, offset=0, limit=12000):
    if offset < 0 or not 1 <= limit <= 20000:
        raise InvalidRecord('Use a nonnegative offset and file limit 1–20000.')
    item, bundle = load(memory, skill_id)
    item['files'] = list(bundle['files'])
    if file is None:
        return item
    if file not in bundle['files']:
        raise InvalidRecord('Select a file from this skill package.')
    raw = base64.b64decode(bundle['files'][file])
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        return {**item, 'file': file, 'binary': True, 'bytes': len(raw)}
    if offset > len(text):
        raise InvalidRecord('The offset exceeds the file length.')
    return {**item, 'file': file, 'text': text[offset:offset + limit], 'offset': offset,
            'next_offset': min(offset + limit, len(text)), 'more': offset + limit < len(text)}


def import_package(memory, data, request_key, actor="workspace-user"):
    if set(data) - {'files', 'archive', 'expected_version', 'replace_skill_id'} or ('files' in data) == ('archive' in data):
        raise InvalidRecord('Supply skill files or a ZIP archive and expected_version.')
    bundle = unpack(data['archive']) if 'archive' in data else package(data['files'])
    key = PREFIX + bundle['metadata']['name']
    row = memory.db.execute('SELECT version,content_hash,id FROM sources WHERE source_key=? ORDER BY version DESC LIMIT 1', (key,)).fetchone()
    if data.get('expected_version') != (row['version'] if row else 0):
        raise Conflict('This skill already exists or changed. Review its current version before replacing it.')
    if row and data.get('replace_skill_id') != row['id']:
        raise Conflict('Select the exact imported skill being replaced.')
    if not row and data.get('replace_skill_id'):
        raise Conflict('The selected replacement does not match this package name.')
    result = memory.source(key, 'Skill: ' + bundle['metadata']['name'], bundle['metadata']['description'], dumps(bundle), 'user' if actor == 'workspace-user' else 'tool')
    return {'skill': read(memory, result['id'])}


def selections(memory, episode_id):
    memory.episode(episode_id)
    rows = memory.db.execute('''SELECT body FROM sources s WHERE source_key LIKE ?
        AND version=(SELECT max(version) FROM sources WHERE source_key=s.source_key)''', (SELECTION + episode_id + ':%',))
    values = [json.loads(r[0]) for r in rows]
    for item in values:
        try:
            if item['skill_id'].startswith('local_'):
                current = read(memory, item['skill_id'])
                item['current_status'] = 'changed' if current['revision'] != item['revision'] else current['status']
            else:
                item['current_status'] = memory.source_status(item['skill_id'])
        except (InvalidRecord, OSError):
            item['current_status'] = 'missing'
    return values


def select(memory, data, request_key, actor="workspace-user", evidence=None):
    required = {'episode_id', 'expected_version', 'skill_id', 'state', 'reason', 'revision'}
    if set(data) != required or data['state'] not in {'selected', 'released', 'reported_use'}:
        raise InvalidRecord('Select a work item, skill version, state and reason.')
    if actor != 'workspace-user' and not evidence:
        raise InvalidRecord('Agent skill selection or reported use needs explained evidence references.')
    ep = memory.episode(data['episode_id'])
    if ep['version'] != data['expected_version']:
        raise Conflict('This work changed. Reload it before selecting a skill.')
    previous = next((item for item in selections(memory, ep['id']) if item['skill_id'] == data['skill_id']), None)
    if data['state'] == 'released':
        if not previous or previous['revision'] != data['revision']:
            raise Conflict('Review the current skill selection before releasing it.')
        body = {k: previous[k] for k in ('skill_id', 'revision', 'name', 'snapshot_source_id')}
    else:
        if not data['skill_id'].startswith('local_') and not memory.db.execute('SELECT 1 FROM sources WHERE id=? AND source_key LIKE ?', (data['skill_id'], PREFIX + '%')).fetchone():
            raise InvalidRecord('Select a current project skill, not a saved selection snapshot.')
        skill, bundle = load(memory, data['skill_id'])
        if skill['revision'] != data['revision']:
            raise Conflict('The skill changed. Review its new version before selecting it.')
        body = {'skill_id': data['skill_id'], 'revision': data['revision'], 'name': skill['name']}
        if data['skill_id'].startswith('local_'):
            snapshot = memory.source('workspace-skill-snapshot:' + data['skill_id'], 'Skill snapshot: ' + skill['name'],
                skill['description'], dumps(bundle), 'tool')
            body['snapshot_source_id'] = snapshot['id']
        else:
            body['snapshot_source_id'] = data['skill_id']
    _text(data['reason'], 'selection reason', 2000)
    body.update(state=data['state'], reason=data['reason'])
    selection_key = data['skill_id'] if data['skill_id'].startswith('local_') else body['name']
    source = memory.source(SELECTION + ep['id'] + ':' + selection_key, 'Skill selection: ' + body['name'],
                           data['reason'], dumps(body), 'user' if actor == 'workspace-user' else 'tool', subject=ep['subject'])
    event = memory.record(ep['id'], 'note', {'text': ('The user ' if actor == 'workspace-user' else 'The assistant ') + {'selected':'selects ', 'released':'releases ', 'reported_use':'reports using '}[data['state']] +
                          body['name'] + ' for this work. ' + data['reason']}, expected_version=ep['version'],
                          request_key=request_key + ':record', actor=actor,
                          evidence=(evidence or []) + [{'source_id': body['snapshot_source_id'], 'reason': 'This exact skill package is the version selected or reported as used.'}, {'source_id': source['id'], 'reason': 'This explicit selection or use report is not independent execution verification.'}])
    return {'event': event, 'selections': selections(memory, ep['id'])}


def signature(memory):
    """Observe local package changes without loading package bodies during polling."""
    root = project_root(memory).resolve()
    values = []
    for location in ('.agents/skills', '.claude/skills', '.codex/skills'):
        folder = root / location
        if folder.is_symlink() or not folder.is_dir() or not folder.resolve().is_relative_to(root):
            continue
        for child in sorted(folder.iterdir()):
            if child.is_symlink() or not child.is_dir():
                continue
            for index, path in enumerate(child.rglob('*')):
                if index > MAX_FILES:
                    values.append((str(child.relative_to(root)), 'file_limit'))
                    break
                try:
                    st = path.lstat()
                    values.append((str(path.relative_to(root)), st.st_mtime_ns, st.st_size, st.st_mode))
                except OSError as exc:
                    values.append((str(path.relative_to(root)), str(exc)))
    return hashlib.sha256(dumps(values).encode()).hexdigest()
