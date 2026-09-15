"""Static code structure, declared packages and the work, lessons and links attached to them.

Imports are read statically from source text. Dynamic loading, import hooks, path
aliases from build configuration and generated code are not detected.
"""
import ast
import json
from pathlib import Path, PurePosixPath
import re
import sys
import tomllib

from .core import Conflict, InvalidRecord, _digest, _text, dumps


MAX_FILE_BYTES = 1_000_000
MAX_SOURCE_FILES = 5000
MAX_MANIFESTS = 100
ATTACHED_LIMIT = 20

LANGUAGES = {
    '.py': 'python',
    '.js': 'javascript',
    '.jsx': 'javascript',
    '.mjs': 'javascript',
    '.cjs': 'javascript',
    '.ts': 'typescript',
    '.tsx': 'typescript',
    '.dart': 'dart',
}
SCRIPT_EXTENSIONS = ('.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs')
STATUS_ORDER = ('blocked', 'review', 'in_progress', 'guarded', 'idle')
NODE_BUILTINS = {
    'assert', 'async_hooks', 'buffer', 'child_process', 'cluster', 'console', 'constants', 'crypto',
    'dgram', 'diagnostics_channel', 'dns', 'domain', 'events', 'fs', 'http', 'http2', 'https',
    'inspector', 'module', 'net', 'os', 'path', 'perf_hooks', 'process', 'punycode', 'querystring',
    'readline', 'repl', 'stream', 'string_decoder', 'sys', 'timers', 'tls', 'trace_events', 'tty',
    'url', 'util', 'v8', 'vm', 'wasi', 'worker_threads', 'zlib',
}
NOTE = ('Imports are read statically from source files. Dynamic loading, import hooks, build path aliases '
        'and generated code are not detected. Python distribution names can differ from import names.')
N8N_NOTE = (' Workflows are read from exported n8n JSON files in the project. Only workflow names, node names, node types, '
            'connections, and credential names and ids are kept. Node parameters and credential values are not kept.')

_QUOTED = r'''(['"])([^'"\n]+)\1'''
SCRIPT_PATTERNS = [
    re.compile(r'''(?<![.\w$])import\s+[^'";]*?\bfrom\s*''' + _QUOTED),
    re.compile(r'(?<![.\w$])import\s*' + _QUOTED),
    re.compile(r'''(?<![.\w$])export\s+[^'";]*?\bfrom\s*''' + _QUOTED),
    re.compile(r'(?<![.\w$])require\s*\(\s*' + _QUOTED + r'\s*\)'),
    re.compile(r'(?<![.\w$])import\s*\(\s*' + _QUOTED + r'\s*\)'),
]
DART_PATTERN = re.compile(r'''^\s*(?:import|export|part)\s+(['"])([^'"\n]+)\1''', re.M)


def project_root(memory):
    """Return the project folder recorded for this database."""
    row = memory.db.execute("SELECT value FROM settings WHERE key='workspace_project'").fetchone()
    if row:
        return Path(json.loads(row[0]))
    row = memory.db.execute("SELECT value FROM settings WHERE key='review_host'").fetchone()
    if row:
        config = json.loads(row[0])
        if isinstance(config, dict) and config.get('project'):
            return Path(config['project'])
    return memory.path.parent.parent


# Package manifests, moved unchanged from project_dependencies.py.

def dependency_sections(title, body):
    """Preserve complete authored sections, including their nested exceptions."""
    lines = body.splitlines(keepends=True)
    headings, fence = [], None
    for i, line in enumerate(lines):
        marker = re.match(r'^\s{0,3}(`{3,}|~{3,})', line)
        if marker:
            run = marker[1]
            if fence is None:
                fence = run
            elif run[0] == fence[0] and len(run) >= len(fence):
                fence = None
        if fence or marker:
            continue
        heading = re.match(r'^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$', line)
        if heading:
            headings.append((i, len(heading[1]), heading[2]))
    sections, covered = [], -1
    for n, (start, level, heading) in enumerate(headings):
        if start < covered or not re.search(r'\bdependenc(?:y|ies)\b', heading, re.I):
            continue
        end = next((i for i, depth, _ in headings[n + 1:] if depth <= level), len(lines))
        sections.append({'heading': heading, 'body': ''.join(lines[start:end])})
        covered = end
    if not sections and re.search(r'\bdependenc(?:y|ies)\b', title, re.I):
        sections.append({'heading': title, 'body': body})
    return sections


def declarations(filename, text):
    result = []

    def requirements(values, group):
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            raise ValueError(group + ' must be a list of dependency declarations.')
        for value in values:
            match = re.match(r'^([A-Za-z0-9][A-Za-z0-9._-]*)(.*)$', value.strip())
            if not match or (match[2].strip() and match[2].lstrip()[0] not in '[<=>!~;@('):
                result.append({'issue': 'Unparsed dependency declaration: ' + value})
            else:
                result.append({'name': match[1], 'requirement': match[2].strip() or 'Any version',
                               'group': group, 'ecosystem': 'Python'})

    if filename == 'package.json':
        value = json.loads(text)
        if not isinstance(value, dict):
            raise ValueError('package.json must contain an object.')
        for key, group in [('dependencies', 'Runtime'), ('devDependencies', 'Development'),
                           ('peerDependencies', 'Peer'), ('optionalDependencies', 'Optional')]:
            dependencies = value.get(key, {})
            if not isinstance(dependencies, dict) or not all(isinstance(v, str) for v in dependencies.values()):
                raise ValueError(key + ' must map package names to version declarations.')
            # npm optionalDependencies overrides the same name in dependencies.
            optional = value.get('optionalDependencies', {})
            for name, requirement in dependencies.items():
                if key == 'dependencies' and isinstance(optional, dict) and name in optional:
                    continue
                result.append({'name': name, 'requirement': requirement, 'group': group, 'ecosystem': 'npm'})
    elif filename == 'pyproject.toml':
        value = tomllib.loads(text)
        project = value.get('project', {})
        if not isinstance(project, dict) or not isinstance(value.get('build-system', {}), dict):
            raise ValueError('Project and build-system sections must be tables.')
        requirements(project.get('dependencies', []), 'Runtime')
        optional = project.get('optional-dependencies', {})
        groups = value.get('dependency-groups', {})
        if not isinstance(optional, dict) or not isinstance(groups, dict):
            raise ValueError('Optional dependencies and dependency groups must be tables.')
        for name, deps in optional.items():
            requirements(deps, 'Optional: ' + name)
        requirements(value.get('build-system', {}).get('requires', []), 'Build')
        for name, deps in groups.items():
            if not isinstance(deps, list):
                raise ValueError('Dependency groups must contain lists.')
            requirements([dep for dep in deps if isinstance(dep, str)], 'Group: ' + name)
            for dep in deps:
                if not isinstance(dep, str):
                    result.append({'issue': 'Group ' + name + ' includes ' + json.dumps(dep) + '; see its declaration.'})
        dynamic = project.get('dynamic', [])
        if not isinstance(dynamic, list):
            raise ValueError('Dynamic fields must be a list.')
        if 'dependencies' in dynamic:
            result.append({'issue': 'Runtime dependencies are dynamic and cannot be read from this manifest.'})
    else:
        for line in text.replace('\\\n', '').splitlines():
            value = re.split(r'\s+#', line, maxsplit=1)[0].strip()
            if not value or value.startswith('#'):
                continue
            if value.startswith('-'):
                result.append({'issue': 'Requirements directive needs inspection: ' + value})
            else:
                requirements([value], 'Requirements')
    return result


def pubspec(text):
    """Read the package name and direct dependencies from a pubspec.yaml file.

    Only the top level name and the dependency sections are read. Nested version
    constraints and sources are summarised as the requirement text.
    """
    name = None
    result = []
    groups = {'dependencies': 'Runtime', 'dev_dependencies': 'Development', 'dependency_overrides': 'Override'}
    group = None
    entry = None
    for raw in text.splitlines():
        line = re.split(r'\s+#', raw, maxsplit=1)[0].rstrip()
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        indent = len(line) - len(line.lstrip(' '))
        stripped = line.strip()
        if indent == 0:
            entry = None
            key, _, value = stripped.partition(':')
            group = groups.get(key)
            if key == 'name' and value.strip():
                name = value.strip().strip('\'"')
            continue
        if group is None:
            continue
        match = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$', stripped)
        if entry is None or indent <= entry[0]:
            if not match:
                continue
            requirement = match[2].strip().strip('\'"')
            entry = (indent, {'name': match[1], 'requirement': requirement or 'Any version',
                              'group': group, 'ecosystem': 'pub'})
            result.append(entry[1])
        elif entry[1]['requirement'] == 'Any version':
            entry[1]['requirement'] = stripped
        else:
            entry[1]['requirement'] += '; ' + stripped
    return name, result


def _manifest_kind(name):
    base = PurePosixPath(name).name
    if base in {'package.json', 'pyproject.toml', 'pubspec.yaml'}:
        return base
    if re.fullmatch(r'requirements(?:[._-].+)?\.txt', base):
        return 'requirements'
    return None


def _read_inside(root, name, issues):
    path = root / name
    try:
        if not path.resolve().is_relative_to(root):
            raise ValueError('The file points outside the project.')
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError('The file exceeds 1 MB.')
        return path.read_bytes()
    except (OSError, ValueError) as exc:
        issues.append({'path': name, 'message': str(exc)})
        return None


def _packages(root, paths):
    manifests = [name for name in paths if _manifest_kind(name)]
    issues = []
    if len(manifests) > MAX_MANIFESTS:
        issues.append({'manifest': None, 'message': f'The project has {len(manifests)} manifests. Only the first {MAX_MANIFESTS} were read.'})
        manifests = manifests[:MAX_MANIFESTS]
    declared, files, pub_names = [], [], {}
    for name in manifests:
        kind = _manifest_kind(name)
        before = len(declared)
        problems = []
        data = _read_inside(root, name, problems)
        for problem in problems:
            issues.append({'manifest': name, 'message': problem['message']})
        if data is not None:
            try:
                text = data.decode('utf-8')
                if kind == 'pubspec.yaml':
                    package_name, entries = pubspec(text)
                    if package_name:
                        pub_names[str(PurePosixPath(name).parent)] = package_name
                else:
                    entries = declarations(PurePosixPath(name).name, text)
                for item in entries:
                    if 'issue' in item:
                        issues.append({'manifest': name, 'message': item['issue']})
                    else:
                        declared.append({**item, 'manifest': name})
            except (ValueError, TypeError) as exc:
                issues.append({'manifest': name, 'message': str(exc)})
        files.append({'path': name, 'count': len(declared) - before})
    declared.sort(key=lambda p: (p['name'].lower(), p['manifest'], p['group']))
    return {'declared': declared, 'manifests': files, 'issues': issues}, pub_names


# Source parsing.

def _strip_script_comments(text):
    """Remove comments while keeping string literals, so commented imports are ignored."""
    out = []
    i = 0
    n = len(text)
    quote = None
    while i < n:
        c = text[i]
        if quote:
            out.append(c)
            if c == '\\' and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == quote or (c == '\n' and quote != '`'):
                quote = None
            i += 1
            continue
        if c in '\'"`':
            quote = c
            out.append(c)
            i += 1
            continue
        if text.startswith('//', i):
            end = text.find('\n', i)
            i = n if end < 0 else end
            continue
        if text.startswith('/*', i):
            end = text.find('*/', i + 2)
            segment = text[i:n if end < 0 else end + 2]
            out.append('\n' * segment.count('\n'))
            i = n if end < 0 else end + 2
            continue
        out.append(c)
        i += 1
    return ''.join(out)


def _parse_python(data):
    tree = ast.parse(data)
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(['import', alias.name, 0, []])
        elif isinstance(node, ast.ImportFrom):
            imports.append(['from', node.module or '', node.level, [alias.name for alias in node.names]])
    return imports


def _parse_file(path, name, language, stat, cache, seen):
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    if cache is not None and key in cache:
        seen[key] = cache[key]
        return cache[key]
    data = path.read_bytes()
    lines = data.count(b'\n') + (1 if data and not data.endswith(b'\n') else 0)
    result = {'language': language, 'lines': lines, 'imports': [], 'issue': None}
    if language == 'python':
        try:
            result['imports'] = _parse_python(data)
        except SyntaxError as exc:
            result['issue'] = {'path': name, 'line': exc.lineno, 'message': 'Python syntax error: ' + str(exc.msg) + '.'}
        except ValueError as exc:
            result['issue'] = {'path': name, 'line': None, 'message': 'Python source could not be parsed: ' + str(exc) + '.'}
    else:
        text = data.decode('utf-8', errors='replace')
        if language == 'dart':
            result['imports'] = [match[2] for match in DART_PATTERN.finditer(text)]
        else:
            text = _strip_script_comments(text)
            found = []
            for pattern in SCRIPT_PATTERNS:
                found.extend((match.start(), match[2]) for match in pattern.finditer(text))
            result['imports'] = [spec for _, spec in sorted(found)]
    seen[key] = result
    return result


# Import resolution.

def _parent(name):
    parent = str(PurePosixPath(name).parent)
    return parent


def _join(base, *parts):
    """Join POSIX parts and normalise dots; None when the result leaves the project."""
    segments = [] if base in ('', '.') else base.split('/')
    for part in parts:
        for piece in part.split('/'):
            if piece in ('', '.'):
                continue
            if piece == '..':
                if not segments:
                    return None
                segments.pop()
            else:
                segments.append(piece)
    return '/'.join(segments) if segments else '.'


def _python_roots(name, files):
    """Folders from which absolute imports in this file can resolve."""
    roots = ['.', 'src']
    folder = _parent(name)
    roots.append(folder)
    top = folder
    while top != '.' and _join(top, '__init__.py') in files:
        top = _parent(top)
    roots.append(top)
    result = []
    for root in roots:
        if root not in result:
            result.append(root)
    return result


def _python_module(base, dotted, files):
    parts = [p for p in dotted.split('.') if p]
    stem = _join(base, *parts) if parts else base
    if stem is None:
        return None
    if parts:
        candidate = stem + '.py'
        if candidate in files:
            return candidate
    candidate = _join(stem, '__init__.py')
    if candidate in files:
        return candidate
    return None


def _resolve_python(name, item, files, internal_tops):
    """Return ('file', path), ('package', top name) or None for one import statement."""
    mode, module, level, names = item
    targets = []
    if level:
        base = _parent(name)
        for _ in range(level - 1):
            if base == '.':
                return []
            base = _parent(base)
        found = False
        for symbol in names:
            if symbol == '*':
                continue
            dotted = (module + '.' + symbol) if module else symbol
            target = _python_module(base, dotted, files)
            if target:
                targets.append(('file', target))
                found = True
        if not found:
            target = _python_module(base, module, files)
            if target:
                targets.append(('file', target))
        return targets
    top = module.split('.')[0]
    if not top or top in sys.stdlib_module_names:
        return []
    roots = _python_roots(name, files)
    for root in roots:
        if mode == 'from':
            hits = []
            for symbol in names:
                if symbol != '*':
                    target = _python_module(root, module + '.' + symbol, files)
                    if target:
                        hits.append(('file', target))
            if hits:
                return hits
        parts = module.split('.')
        for size in range(len(parts), 0, -1):
            target = _python_module(root, '.'.join(parts[:size]), files)
            if target:
                return [('file', target)]
    if top in internal_tops:
        return []
    return [('package', top)]


def _resolve_script(name, spec, files):
    if ':' in spec or spec.startswith('/') or spec.startswith('#'):
        return []
    if spec.startswith('.'):
        stem = _join(_parent(name), spec)
        if stem is None:
            return []
        candidates = [stem]
        for suffix in ('.js', '.jsx', '.mjs', '.cjs'):
            if stem.endswith(suffix):
                bare = stem[:-len(suffix)]
                candidates.extend(bare + ext for ext in ('.ts', '.tsx', '.mts', '.cts'))
        candidates.extend(stem + ext for ext in SCRIPT_EXTENSIONS)
        candidates.extend(_join(stem, 'index' + ext) for ext in SCRIPT_EXTENSIONS)
        for candidate in candidates:
            if candidate in files:
                return [('file', candidate)]
        return []
    if spec.startswith('@'):
        parts = spec.split('/')
        if len(parts) < 2 or len(parts[0]) < 2 or not parts[1]:
            return []
        package = parts[0] + '/' + parts[1]
    else:
        package = spec.split('/')[0]
    if not package or package in NODE_BUILTINS or package.startswith('~'):
        return []
    return [('package', package)]


def _pubspec_folder(name, pub_names):
    folder = _parent(name)
    while True:
        if folder in pub_names:
            return folder
        if folder == '.':
            return None
        folder = _parent(folder)


def _resolve_dart(name, spec, files, pub_names):
    if spec.startswith('dart:'):
        return []
    if spec.startswith('package:'):
        rest = spec[len('package:'):]
        package, _, inner = rest.partition('/')
        folder = _pubspec_folder(name, pub_names)
        if folder is not None and pub_names[folder] == package:
            target = _join(folder, 'lib', inner)
            return [('file', target)] if target in files else []
        return [('package', package)] if package else []
    if ':' in spec:
        return []
    target = _join(_parent(name), spec)
    return [('file', target)] if target in files else []


def _normal_package(ecosystem, name):
    if ecosystem == 'python':
        return re.sub(r'[-_.]+', '-', name).lower()
    return name


# Attachments.

def _matcher():
    """Return the path matcher from guards, or a local matcher while guards is absent."""
    try:
        from . import guards
        return guards.match_path, True
    except ImportError:
        return _fallback_match, False


_PATTERN_CACHE = {}


def _fallback_match(path, patterns):
    """Temporary matcher with the documented pattern semantics, used until guards.py exists."""
    path = str(path)
    for pattern in patterns:
        if not any(ch in pattern for ch in '*?['):
            literal = pattern.rstrip('/')
            if literal in ('', '.') or path == literal or path.startswith(literal + '/'):
                return True
            continue
        compiled = _PATTERN_CACHE.get(pattern)
        if compiled is None:
            text = ''
            i = 0
            while i < len(pattern):
                if pattern.startswith('**/', i):
                    text += '(?:.*/)?'
                    i += 3
                elif pattern.startswith('**', i):
                    text += '.*'
                    i += 2
                elif pattern[i] == '*':
                    text += '[^/]*'
                    i += 1
                elif pattern[i] == '?':
                    text += '[^/]'
                    i += 1
                else:
                    text += re.escape(pattern[i])
                    i += 1
            compiled = re.compile(text + r'(?:/.*)?\Z')
            _PATTERN_CACHE[pattern] = compiled
        if compiled.match(path):
            return True
    return False


def _relative_patterns(patterns, root):
    result = []
    for pattern in patterns:
        if not isinstance(pattern, str) or not pattern:
            continue
        if pattern.startswith('/'):
            try:
                inside = PurePosixPath(pattern).relative_to(PurePosixPath(root.as_posix()))
            except ValueError:
                continue
            pattern = str(inside)
        result.append(pattern)
    return result


def _table_exists(memory, name):
    return bool(memory.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


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
        if review and review['status'] in {'accepted', 'needs_review'} and any(key in review for key in ('paths', 'keywords', 'failure_type')):
            paths = review.get('paths', [])
        if isinstance(paths, list) and paths:
            result.append({'id': row['id'], 'status': status, 'paths': paths})
    return result


def _guards(memory, lessons):
    try:
        from . import guards
    except ImportError:
        return [{'lesson_id': item['id'], 'paths': item['paths']} for item in lessons if item['status'] == 'accepted']
    return [guard for guard in guards.active_guards(memory) if guard.get('paths')]


def _link_rows(memory):
    """Active explicit graph links, oldest first. Empty when the links table does not exist."""
    if not _table_exists(memory, 'links'):
        return []
    rows = memory.db.execute("""SELECT l.id, l.from_id, l.to_id, l.type, l.reason FROM links l
        WHERE l.retires IS NULL AND NOT EXISTS (SELECT 1 FROM links r WHERE r.retires=l.id)
        ORDER BY l.created_at, l.id""")
    return [dict(row) for row in rows]


def _attach(node, key, ids):
    node[key] = ids[:ATTACHED_LIMIT]
    node[key + '_total'] = len(ids)


# Authored components.

COMPONENT_KINDS = ('system', 'component', 'service', 'workflow', 'integration', 'dataset', 'stakeholder',
                   'workstream', 'deliverable', 'process')
COMPONENT_STATUSES = ('proposed', 'confirmed', 'retired')
USER_ACTOR = 'workspace-user'
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


def save_component(memory, *, component_id=None, title, kind, description, status, actor, request_key, evidence=None, path=None):
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
    path = _component_path_value(path)
    slug = _slug(component_id) if component_id is not None else None
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
    if not _table_exists(memory, 'component_revisions'):
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
    links = [row for row in _link_rows(memory) if identifier in (row['from_id'], row['to_id'])]
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


# Exported n8n workflows.

N8N_PREFIX = 'n8n:'
MAX_WORKFLOWS = 500
N8N_STICKY = 'n8n-nodes-base.stickyNote'
N8N_EXECUTE_TYPES = {'n8n-nodes-base.executeWorkflow', '@n8n/n8n-nodes-langchain.toolWorkflow'}
N8N_TRIGGER_TYPES = {'n8n-nodes-base.webhook', 'n8n-nodes-base.cron', 'n8n-nodes-base.interval', 'n8n-nodes-base.start',
                     'n8n-nodes-base.emailReadImap'}
# Built-in logic and generic AI building blocks. They do not name an outside system.
N8N_UTILITY = {
    'aggregate', 'agent', 'agentTool', 'chainLlm', 'chainRetrievalQa', 'chainSummarization', 'chat', 'chatTrigger', 'code',
    'compareDatasets', 'compression', 'convertToFile', 'cron', 'crypto', 'dateTime', 'debugHelper', 'documentBinaryInputLoader',
    'documentDefaultDataLoader', 'documentJsonInputLoader', 'editImage', 'errorTrigger', 'executeCommand', 'executeWorkflow',
    'executeWorkflowTrigger', 'executionData', 'extractFromFile', 'filter', 'form', 'formTrigger', 'function', 'functionItem',
    'html', 'httpRequest', 'if', 'informationExtractor', 'interval', 'itemLists', 'limit', 'localFileTrigger', 'manualChatTrigger',
    'manualTrigger', 'markdown', 'mcpClientTool', 'mcpTrigger', 'memoryBufferWindow', 'memoryManager', 'merge', 'moveBinaryData',
    'n8n', 'n8nTrigger', 'noOp', 'readBinaryFile', 'readBinaryFiles', 'readWriteFile', 'removeDuplicates', 'renameKeys',
    'respondToWebhook', 'retrieverContextualCompression', 'retrieverMultiQuery', 'retrieverVectorStore', 'retrieverWorkflow',
    'scheduleTrigger', 'sentimentAnalysis', 'set', 'simulate', 'simulateTrigger', 'sort', 'splitInBatches', 'splitOut',
    'spreadsheetFile', 'start', 'stickyNote', 'stopAndError', 'summarize', 'switch', 'textClassifier', 'toolCalculator',
    'toolCode', 'toolExecutor', 'toolHttpRequest', 'toolThink', 'toolWorkflow', 'totp', 'vectorStoreInMemory',
    'vectorStoreInMemoryInsert', 'vectorStoreInMemoryLoad', 'wait', 'webhook', 'workflowTrigger', 'writeBinaryFile', 'xml',
}
N8N_UTILITY_PREFIXES = ('outputParser', 'textSplitter')
N8N_MODEL_PREFIXES = ('lmChat', 'lm', 'embeddings', 'vectorStore', 'memory', 'tool', 'retriever')
N8N_MODEL_SUFFIXES = ('Chat', 'Insert', 'Load')
N8N_GENERIC_CREDENTIALS = {'httpHeaderAuth', 'httpBasicAuth', 'httpQueryAuth', 'httpDigestAuth', 'httpCustomAuth',
                           'httpSslAuth', 'httpBearerAuth', 'oAuth1Api', 'oAuth2Api', 'jwtAuth'}
N8N_CREDENTIAL_SUFFIXES = ('OAuth2Api', 'OAuth1Api', 'Oauth2Api', 'ServiceAccountApi', 'TokenApi', 'AppToken', 'AccessToken',
                           'ApiKey', 'Api', 'OAuth2', 'Oauth2')
SERVICE_ALIASES = (('open-ai', 'openai'), ('git-hub', 'github'), ('git-lab', 'gitlab'), ('hub-spot', 'hubspot'),
                   ('my-sql', 'mysql'), ('mongo-db', 'mongodb'), ('serp-api', 'serpapi'), ('you-tube', 'youtube'),
                   ('linked-in', 'linkedin'), ('word-press', 'wordpress'), ('drop-box', 'dropbox'),
                   ('share-point', 'sharepoint'), ('one-drive', 'onedrive'), ('send-grid', 'sendgrid'),
                   ('mail-chimp', 'mailchimp'), ('quick-books', 'quickbooks'), ('click-up', 'clickup'),
                   ('post-hog', 'posthog'), ('email-send', 'smtp'), ('email-read-imap', 'imap'))


def _service_slug(name):
    text = re.sub(r'(?<=[a-z0-9])(?=[A-Z])', '-', name).lower()
    text = re.sub(r'[^a-z0-9._-]+', '-', text).strip('-._')
    for old, new in SERVICE_ALIASES:
        text = re.sub(r'(?<![a-z0-9])' + re.escape(old) + r'(?![a-z0-9])', new, text)
    return text[:100].strip('-._') or None


def node_service(node_type):
    """Service name for an n8n node type, or None for built-in logic.

    `n8n-nodes-base.slack` and `n8n-nodes-base.slackTrigger` become `slack`;
    `@n8n/n8n-nodes-langchain.lmChatOpenAi` becomes `openai`.
    """
    if not isinstance(node_type, str) or '.' not in node_type:
        return None
    package, _, name = node_type.rpartition('.')
    if not name or name in N8N_UTILITY or name.startswith(N8N_UTILITY_PREFIXES):
        return None
    if 'langchain' in package:
        for prefix in N8N_MODEL_PREFIXES:
            if name.startswith(prefix) and len(name) > len(prefix) and name[len(prefix)].isupper():
                name = name[len(prefix):]
                break
        for suffix in N8N_MODEL_SUFFIXES:
            if name.endswith(suffix) and len(name) > len(suffix):
                name = name[:-len(suffix)]
    for suffix in ('Trigger', 'Tool'):
        if name.endswith(suffix) and len(name) > len(suffix):
            name = name[:-len(suffix)]
            if name in N8N_UTILITY:
                return None
    return _service_slug(name)


def credential_service(credential_type):
    """Service name for an n8n credential type, or None for generic authentication."""
    if not isinstance(credential_type, str) or not credential_type or credential_type in N8N_GENERIC_CREDENTIALS:
        return None
    name = credential_type
    for suffix in N8N_CREDENTIAL_SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix):
            name = name[:-len(suffix)]
            break
    return _service_slug(name)


N8N_HTTP_TYPES = {'n8n-nodes-base.httpRequest', '@n8n/n8n-nodes-langchain.toolHttpRequest'}
HOST_SERVICE = re.compile(r'[a-z0-9][a-z0-9.-]{0,99}')


def request_host(item):
    """The host name of a static http or https URL in an HTTP Request node, or None.

    Only the host name is kept. The path, the query, user information and every
    other parameter are ignored, and URLs built from expressions are skipped.
    """
    from urllib.parse import urlsplit
    parameters = item.get('parameters')
    if not isinstance(parameters, dict):
        return None
    url = parameters.get('url')
    if not isinstance(url, str) or url.startswith('=') or '{{' in url:
        return None
    try:
        parts = urlsplit(url.strip())
        host = parts.hostname
    except ValueError:
        return None
    if parts.scheme not in ('http', 'https') or not host:
        return None
    host = host.rstrip('.')
    if host.startswith('www.'):
        host = host[len('www.'):]
    return host if HOST_SERVICE.fullmatch(host) else None


def _is_trigger(node_type):
    name = node_type.rpartition('.')[2]
    return node_type in N8N_TRIGGER_TYPES or (name.endswith('Trigger') and len(name) > len('Trigger'))


def _short(value, limit=300):
    return value[:limit] if isinstance(value, str) else None


def _call_target(item):
    """The workflow an Execute Workflow node calls: an id, a cached name or a file path. Expressions are ignored."""
    parameters = item.get('parameters')
    if not isinstance(parameters, dict):
        return {}
    target = {}
    source = parameters.get('source', 'database')
    if source == 'localFile' and isinstance(parameters.get('workflowPath'), str):
        target['path'] = parameters['workflowPath'][:1000]
    value = parameters.get('workflowId')
    if isinstance(value, dict):
        if isinstance(value.get('cachedResultName'), str):
            target['name'] = value['cachedResultName'][:300]
        value = value.get('value')
    if isinstance(value, (str, int)) and not isinstance(value, bool) and not str(value).startswith('='):
        target['id'] = str(value)[:200]
    return target


def _summarize_workflow(data):
    """Keep node names, types, flags, connections and credential names and ids. Parameters and secrets are not kept."""
    nodes = []
    for item in data['nodes']:
        if not isinstance(item, dict):
            continue
        name = _short(item.get('name'))
        node_type = _short(item.get('type'))
        if not name or not node_type or node_type == N8N_STICKY:
            continue
        credentials = []
        raw = item.get('credentials')
        if isinstance(raw, dict):
            for credential_type, value in raw.items():
                if not isinstance(credential_type, str):
                    continue
                entry = {'type': credential_type[:200]}
                if isinstance(value, dict):
                    for key in ('id', 'name'):
                        if isinstance(value.get(key), (str, int)) and not isinstance(value.get(key), bool):
                            entry[key] = str(value[key])[:200]
                elif isinstance(value, str):
                    # Older exports store only the credential name.
                    entry['name'] = value[:200]
                credentials.append(entry)
        host = request_host(item) if node_type in N8N_HTTP_TYPES else None
        services = []
        for service in [node_service(node_type)] + [credential_service(entry['type']) for entry in credentials] + [host]:
            if service and service not in services:
                services.append(service)
        nodes.append({'name': name, 'type': node_type, 'disabled': item.get('disabled') is True,
                      'trigger': _is_trigger(node_type), 'services': services, 'credentials': credentials, 'host': host,
                      'call': _call_target(item) if node_type in N8N_EXECUTE_TYPES else None})
    connections = []
    for source, outputs in data['connections'].items():
        if not isinstance(source, str) or not isinstance(outputs, dict):
            continue
        for connection, groups in outputs.items():
            if not isinstance(groups, list):
                continue
            for group in groups:
                if not isinstance(group, list):
                    continue
                for target in group:
                    if isinstance(target, dict) and isinstance(target.get('node'), str):
                        connections.append([source, target['node'], str(connection)[:100]])
    workflow_id = data.get('id')
    return {'name': _short(data.get('name')), 'active': data.get('active') is True, 'nodes': nodes, 'connections': connections,
            'id': str(workflow_id)[:200] if isinstance(workflow_id, (str, int)) and not isinstance(workflow_id, bool) else None}


def _is_workflow(value):
    return isinstance(value, dict) and isinstance(value.get('nodes'), list) and isinstance(value.get('connections'), dict)


def _read_workflow(path):
    """The summary of an exported workflow, a list of summaries for a bulk export array, or None for other JSON."""
    data = path.read_bytes()
    if b'"nodes"' not in data or b'"connections"' not in data:
        return None
    try:
        value = json.loads(data.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return None
    if _is_workflow(value):
        return _summarize_workflow(value)
    if isinstance(value, list):
        found = [_summarize_workflow(item) for item in value if _is_workflow(item)]
        return found or None
    return None


def workflow_entries(name, summary):
    """(key, summary) pairs for one file. A bulk export array gives `<file>#<position>` keys, starting at 1."""
    if summary is None:
        return []
    if isinstance(summary, dict):
        return [(name, summary)]
    return [(f'{name}#{index + 1}', item) for index, item in enumerate(summary)]


def _workflow_files(root, names, cache):
    """Exported workflows as (key, file name, summary). A file holding an array of workflows yields one entry per workflow."""
    workflows = []
    truncated = False
    seen = set()
    for name in names:
        if truncated:
            break
        if PurePosixPath(name).suffix.lower() != '.json':
            continue
        path = root / name
        try:
            if path.is_symlink() and not path.resolve().is_relative_to(root):
                continue
            stat = path.stat()
            if not path.is_file() or stat.st_size > MAX_FILE_BYTES:
                continue
            key = ('n8n', str(path), stat.st_size, stat.st_mtime_ns)
            seen.add(key)
            if cache is not None and key in cache:
                summary = cache[key]
            else:
                summary = _read_workflow(path)
                if cache is not None:
                    cache[key] = summary
        except OSError:
            continue
        for key, item in workflow_entries(name, summary):
            if len(workflows) >= MAX_WORKFLOWS:
                truncated = True
                break
            workflows.append((key, name, item))
    if cache is not None and not truncated:
        for key in [key for key in cache if isinstance(key, tuple) and len(key) == 4 and key[0] == 'n8n' and key not in seen]:
            del cache[key]
    return workflows, truncated


SERVICE_TITLES = {'openai': 'OpenAI', 'github': 'GitHub', 'gitlab': 'GitLab', 'hubspot': 'HubSpot', 'mysql': 'MySQL',
                  'mongodb': 'MongoDB', 'smtp': 'SMTP', 'imap': 'IMAP', 'serpapi': 'SerpApi', 'youtube': 'YouTube',
                  'linkedin': 'LinkedIn'}


def _service_title(service):
    if '.' in service:
        # A host name from an HTTP Request node is shown as it is.
        return service
    return SERVICE_TITLES.get(service) or ' '.join(part.capitalize() for part in service.split('-'))


def _unique_credentials(entries):
    result = []
    for entry in entries:
        if entry not in result:
            result.append(entry)
    return result


def _n8n_layer(root, names, cache, focus_file=None):
    """Workflow and service nodes at component level, or the nodes of one workflow at file level."""
    workflows, truncated = _workflow_files(root, names, cache)
    by_file = {key: 'component:' + N8N_PREFIX + key for key, _, _ in workflows}
    by_workflow_id = {}
    by_name = {}
    by_basename = {}
    for key, name, summary in workflows:
        if summary['id']:
            by_workflow_id.setdefault(summary['id'], []).append(by_file[key])
        if summary['name']:
            by_name.setdefault(summary['name'], []).append(by_file[key])
        by_basename.setdefault(PurePosixPath(name).name, []).append(by_file[key])

    def resolve(call):
        if not call:
            return None
        if call.get('path'):
            found = by_basename.get(PurePosixPath(call['path'].replace('\\', '/')).name, [])
            if len(found) == 1:
                return found[0]
        for key, index in (('id', by_workflow_id), ('name', by_name)):
            found = index.get(call.get(key), [])
            if len(found) == 1:
                return found[0]
        return None

    nodes = {}
    members = {}
    edges = {}
    if focus_file is not None:
        workflow_id = by_file.get(focus_file)
        if workflow_id is None:
            raise InvalidRecord('The focus is not an exported n8n workflow in this project.', focus=N8N_PREFIX + focus_file)
        summary = {key: value for key, _, value in workflows}[focus_file]
        file_name = {key: name for key, name, _ in workflows}[focus_file]
        present = set()
        for item in summary['nodes']:
            node_id = workflow_id + '#' + item['name']
            node = _empty_node(node_id, 'n8n_node', item['name'], file_name, layer='n8n')
            node['n8n'] = {'type': item['type'], 'disabled': item['disabled'], 'trigger': item['trigger'],
                           'services': item['services'], 'credentials': item['credentials']}
            if item['call'] is not None:
                node['n8n']['calls'] = resolve(item['call'])
            if item['trigger']:
                node['flags'].append('trigger')
            if item['disabled']:
                node['flags'].append('disabled')
            nodes[node_id] = node
            members[node_id] = []
            present.add(item['name'])
        for source, target, connection in summary['connections']:
            if source in present and target in present:
                key = (workflow_id + '#' + source, workflow_id + '#' + target, 'connects', connection)
                edges[key] = edges.get(key, 0) + 1
        edge_list = [{'from': a, 'to': b, 'type': kind, 'connection': connection, 'weight': weight, 'layer': 'n8n'}
                     for (a, b, kind, connection), weight in sorted(edges.items())]
        return {'nodes': nodes, 'members': members, 'edges': edge_list, 'truncated': truncated}

    services = {}
    for key, name, summary in workflows:
        workflow_id = by_file[key]
        node = _empty_node(workflow_id, 'workflow', summary['name'] or PurePosixPath(name).stem, name, layer='n8n')
        node['files'] = 1
        node['languages'] = ['n8n']
        unresolved = 0
        for item in summary['nodes']:
            for service in item['services']:
                key = (workflow_id, 'service:' + service, 'uses')
                edges[key] = edges.get(key, 0) + 1
                entry = services.setdefault(service, {'types': set(), 'credentials': [], 'workflows': set()})
                entry['workflows'].add(workflow_id)
                if node_service(item['type']) == service or item.get('host') == service:
                    entry['types'].add(item['type'])
                entry['credentials'].extend(c for c in item['credentials'] if credential_service(c['type']) == service)
            if item['call'] is not None:
                target = resolve(item['call'])
                if target and target != workflow_id:
                    key = (workflow_id, target, 'calls')
                    edges[key] = edges.get(key, 0) + 1
                elif target is None:
                    unresolved += 1
        node['n8n'] = {'workflow_id': summary['id'], 'active': summary['active'], 'nodes': len(summary['nodes']),
                       'triggers': [item['name'] for item in summary['nodes'] if item['trigger']],
                       'disabled_nodes': sum(item['disabled'] for item in summary['nodes']),
                       'credentials': _unique_credentials(c for item in summary['nodes'] for c in item['credentials']),
                       'unresolved_calls': unresolved}
        if unresolved:
            node['flags'].append('unresolved_calls')
        nodes[workflow_id] = node
        members[workflow_id] = [name]
    for service, entry in sorted(services.items()):
        node_id = 'service:' + service
        node = _empty_node(node_id, 'service', _service_title(service), None, layer='n8n')
        node['n8n'] = {'node_types': sorted(entry['types']), 'credentials': _unique_credentials(entry['credentials']),
                       'workflows': len(entry['workflows'])}
        nodes[node_id] = node
        members[node_id] = []
    edge_list = [{'from': a, 'to': b, 'type': kind, 'weight': weight, 'layer': 'n8n'} for (a, b, kind), weight in sorted(edges.items())]
    return {'nodes': nodes, 'members': members, 'edges': edge_list, 'truncated': truncated}


# Model.

LAYERS = ('code', 'n8n', 'authored')
KIND_ORDER = {'file': 0, 'n8n_node': 0, 'component': 1, 'workflow': 1, 'package': 3, 'service': 3}


def _empty_node(node_id, kind, title, path, layer='code'):
    return {'id': node_id, 'kind': kind, 'title': title, 'path': path, 'layer': layer, 'languages': [], 'files': 0,
            'lines': 0, 'status': 'idle', 'work': [], 'work_total': 0, 'lessons': [], 'lessons_total': 0,
            'links': [], 'links_total': 0, 'flags': []}


def _component_id(path):
    return 'component:' + path


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


def _code_layer(root, names, level, focus, cache):
    """Source files, imports and declared packages."""
    issues = []
    truncated = False
    sources = {}
    seen = {}
    for name in names:
        language = LANGUAGES.get(PurePosixPath(name).suffix)
        if not language:
            continue
        if len(sources) >= MAX_SOURCE_FILES:
            truncated = True
            break
        path = root / name
        try:
            if path.is_symlink() and not path.resolve().is_relative_to(root):
                issues.append({'path': name, 'line': None, 'message': 'Skipped because the file points outside the project.'})
                continue
            stat = path.stat()
            if not path.is_file():
                continue
            if stat.st_size > MAX_FILE_BYTES:
                issues.append({'path': name, 'line': None, 'message': 'Skipped because the file exceeds 1 MB.'})
                continue
            parsed = _parse_file(path, name, language, stat, cache, seen)
        except OSError as exc:
            issues.append({'path': name, 'line': None, 'message': 'The file could not be read: ' + str(exc) + '.'})
            continue
        if parsed['issue']:
            issues.append(parsed['issue'])
        sources[name] = parsed
    if cache is not None:
        stale = [key for key in cache if isinstance(key, tuple) and len(key) == 3 and key not in seen]
        for key in stale:
            del cache[key]
        cache.update(seen)

    packages, pub_names = _packages(root, names)
    files = set(sources)
    python_tops = set()
    for name in files:
        if name.endswith('.py'):
            first = name.split('/')[0]
            python_tops.add(first[:-3] if first.endswith('.py') else first)
            if first == 'src' and '/' in name:
                second = name.split('/')[1]
                python_tops.add(second[:-3] if second.endswith('.py') else second)

    file_edges = {}
    package_uses = {}
    for name in sorted(sources):
        parsed = sources[name]
        targets = []
        for item in parsed['imports']:
            if parsed['language'] == 'python':
                targets.extend((kind, value, 'python') for kind, value in _resolve_python(name, item, files, python_tops))
            elif parsed['language'] == 'dart':
                targets.extend((kind, value, 'pub') for kind, value in _resolve_dart(name, item, files, pub_names))
            else:
                targets.extend((kind, value, 'npm') for kind, value in _resolve_script(name, item, files))
        for kind, value, ecosystem in dict.fromkeys(targets):
            if kind == 'file':
                if value != name:
                    file_edges[(name, value)] = file_edges.get((name, value), 0) + 1
            else:
                package_id = 'package:' + ecosystem + ':' + value
                package_uses.setdefault(package_id, {'ecosystem': ecosystem, 'name': value, 'files': set()})
                package_uses[package_id]['files'].add(name)

    if level == 'file':
        known = {_parent(name) for name in files}
        if focus not in known:
            raise InvalidRecord('The focus component has no source files in this project.', focus=focus)

    def owner(name):
        folder = _parent(name)
        if level == 'file' and folder == focus:
            return _component_id(name)
        return _component_id(folder)

    nodes = {}
    members = {}
    for name in sorted(sources):
        folder = _parent(name)
        if level == 'file' and folder == focus:
            node_id = _component_id(name)
            kind, title, path = 'file', PurePosixPath(name).name, name
        else:
            node_id = _component_id(folder)
            kind = 'component'
            title = root.name if folder == '.' else PurePosixPath(folder).name
            path = folder
        node = nodes.get(node_id)
        if node is None:
            node = _empty_node(node_id, kind, title, path)
            nodes[node_id] = node
            members[node_id] = []
        members[node_id].append(name)
        node['files'] += 1
        node['lines'] += sources[name]['lines']
        if sources[name]['language'] not in node['languages']:
            node['languages'].append(sources[name]['language'])

    edges = {}
    for (source, target), count in file_edges.items():
        a = owner(source)
        b = owner(target)
        if a == b:
            continue
        if level == 'file' and not (_parent(source) == focus or _parent(target) == focus):
            continue
        key = (a, b, 'imports')
        edges[key] = edges.get(key, 0) + count
    for package_id, use in package_uses.items():
        for name in use['files']:
            if level == 'file' and _parent(name) != focus:
                continue
            key = (owner(name), package_id, 'uses_package')
            edges[key] = edges.get(key, 0) + 1
    if level == 'file':
        connected = {node_id for key in edges for node_id in key[:2]}
        for node_id in list(nodes):
            if nodes[node_id]['kind'] == 'component' and node_id not in connected:
                del nodes[node_id]
                del members[node_id]

    declared_by_id = {}
    for item in packages['declared']:
        ecosystem = item['ecosystem'].lower()
        package_id = 'package:' + ecosystem + ':' + item['name']
        declared_by_id.setdefault(package_id, []).append(item)
    observed = {use['ecosystem'] for use in package_uses.values()}
    manifested = {item['ecosystem'].lower() for item in packages['declared']}
    declared_normal = {}
    for package_id, items in declared_by_id.items():
        ecosystem = items[0]['ecosystem'].lower()
        declared_normal[(ecosystem, _normal_package(ecosystem, items[0]['name']))] = package_id
    imported_normal = {}
    for package_id, use in package_uses.items():
        imported_normal[(use['ecosystem'], _normal_package(use['ecosystem'], use['name']))] = package_id

    package_nodes = {}
    for (ecosystem, normal), package_id in declared_normal.items():
        items = declared_by_id[package_id]
        node = _empty_node(package_id, 'package', items[0]['name'], items[0]['manifest'])
        node['ecosystem'] = ecosystem
        node['declared'] = [{'requirement': i['requirement'], 'group': i['group'], 'manifest': i['manifest']} for i in items]
        node['groups'] = sorted({i['group'] for i in items})
        node['manifests'] = sorted({i['manifest'] for i in items})
        import_id = imported_normal.get((ecosystem, normal))
        if import_id:
            node['files'] = len(package_uses[import_id]['files'])
            node['import_name'] = package_uses[import_id]['name']
        elif ecosystem in observed and node['groups'] != ['Build']:
            node['flags'].append('declared_not_imported')
        package_nodes[package_id] = node
    for (ecosystem, normal), package_id in imported_normal.items():
        if (ecosystem, normal) in declared_normal:
            continue
        use = package_uses[package_id]
        node = _empty_node(package_id, 'package', use['name'], None)
        node['ecosystem'] = ecosystem
        node['declared'] = []
        node['groups'] = []
        node['manifests'] = []
        node['files'] = len(use['files'])
        if ecosystem in manifested:
            node['flags'].append('imported_not_declared')
        package_nodes[package_id] = node
    merged = {}
    for (a, b, kind), weight in edges.items():
        if kind == 'uses_package':
            ecosystem = b.split(':')[1]
            name = b.split(':', 2)[2]
            declared_id = declared_normal.get((ecosystem, _normal_package(ecosystem, name)))
            if declared_id:
                b = declared_id
        merged[(a, b, kind)] = merged.get((a, b, kind), 0) + weight
    edge_list = [{'from': a, 'to': b, 'type': kind, 'weight': weight, 'layer': 'code'} for (a, b, kind), weight in sorted(merged.items())]
    targets = {edge['to'] for edge in edge_list if edge['type'] == 'uses_package'}
    for package_id, node in package_nodes.items():
        if level == 'file' and package_id not in targets:
            continue
        nodes[package_id] = node
        members[package_id] = []

    languages = {}
    for parsed in sources.values():
        total = languages.setdefault(parsed['language'], {'files': 0, 'lines': 0})
        total['files'] += 1
        total['lines'] += parsed['lines']
    return {'nodes': nodes, 'members': members, 'edges': edge_list, 'packages': packages, 'languages': languages,
            'issues': issues, 'truncated': truncated}


def _authored_layer(memory, names, nodes, members, match):
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
        node = _empty_node(node_id, item['kind'], item['title'], item['path'], layer='authored')
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
        members[node_id] = [name for name in names if match(name, [path])] if path else []
        placed[item['id']] = node_id
    return placed


SYSTEM_KINDS = ('system', 'service', 'integration')


def _overlap_matcher():
    try:
        from .guards import patterns_overlap
    except ImportError:
        return lambda pattern, path: _fallback_match(path, [pattern]) or path.startswith(pattern.rstrip('/*') + '/')
    return patterns_overlap


def _attach_authored_paths(nodes, root, attached_sets):
    """Attach entries whose patterns overlap an authored path, so a planned deliverable shows work before its file exists."""
    overlap = _overlap_matcher()
    for node_id, node in nodes.items():
        path = node['path']
        if node.get('layer') != 'authored' or not path or path.startswith('service:'):
            continue
        if path.startswith(N8N_PREFIX):
            path = path[len(N8N_PREFIX):]
        for entries, attached in attached_sets:
            current = attached.setdefault(node_id, [])
            for entry in entries:
                patterns = _relative_patterns(entry['paths'], root)
                if entry not in current and any(overlap(pattern, path) for pattern in patterns):
                    current.append(entry)


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
        if focus != '.' and (_join('.', focus) != focus):
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
        parts.append(_n8n_layer(root, names, cache, focus_file=focus[len(N8N_PREFIX):]))
    elif level == 'file':
        if 'code' not in layers:
            raise InvalidRecord('The file level of a code component needs the code layer.')
        parts.append(_code_layer(root, names, level, focus, cache))
    else:
        if 'code' in layers:
            parts.append(_code_layer(root, names, level, focus, cache))
        if 'n8n' in layers:
            parts.append(_n8n_layer(root, names, cache))
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

    match, from_guards = _matcher()
    placed = {}
    if level == 'component' and 'authored' in layers:
        placed = _authored_layer(memory, names, nodes, members, match)
    work = _work_items(memory)
    lessons = _lessons(memory)
    guard_list = _guards(memory, lessons)
    link_rows = _link_rows(memory)
    recorded = {item['id']: item['state'] for item in work}
    states = work_states if work_states is not None else recorded

    def canonical(endpoint):
        return placed.get(endpoint, endpoint)

    link_map = {}
    linked_work = {}
    for row in link_rows:
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
            edges.append({'from': a, 'to': b, 'type': row['type'], 'weight': 1, 'layer': 'authored', 'link_id': row['id']})

    def matching(entries, key):
        attached = {}
        for entry in entries:
            patterns = _relative_patterns(entry[key], root)
            if not patterns:
                continue
            for node_id, node_files in members.items():
                if any(match(name, patterns) for name in node_files):
                    attached.setdefault(node_id, []).append(entry)
        return attached

    work_by_node = matching(work, 'paths')
    lessons_by_node = matching(lessons, 'paths')
    if placed:
        _attach_authored_paths(nodes, root, ((work, work_by_node), (lessons, lessons_by_node)))
    guarded = matching(guard_list, 'paths')
    rank = {state: index for index, state in enumerate(('blocked', 'review', 'in_progress', 'ready', 'backlog', 'done', 'cancelled'))}
    for node_id, node in nodes.items():
        items = [entry['id'] for entry in work_by_node.get(node_id, [])]
        items.extend(episode for episode in linked_work.get(node_id, []) if episode not in items)
        items = sorted(items, key=lambda item: rank.get(states.get(item), len(rank)))
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
    note = NOTE + N8N_NOTE
    if not from_guards:
        note += ' Path patterns use a temporary local matcher because the guards module is not available.'
    return {'root': str(root), 'level': level, 'focus': focus, 'layers': list(layers), 'nodes': ordered, 'edges': edges,
            'packages': packages, 'languages': languages, 'issues': issues,
            'truncated': truncated, 'note': note}
