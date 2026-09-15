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

from .core import InvalidRecord


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
    rows = memory.db.execute("""SELECT ep.id, ep.created_at, p.payload FROM episodes ep
        JOIN events p ON p.episode_id=ep.id AND p.kind='work_plan'
         AND p.seq=(SELECT max(n.seq) FROM events n WHERE n.episode_id=ep.id AND n.kind='work_plan')
        WHERE ep.task_type!='sprint' ORDER BY ep.created_at, ep.id""")
    result = []
    for row in rows:
        payload = json.loads(row['payload'])
        paths = payload.get('paths')
        if isinstance(paths, list) and paths:
            result.append({'id': row['id'], 'state': payload.get('state'), 'paths': paths})
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


def _links(memory):
    if not _table_exists(memory, 'links'):
        return {}
    rows = memory.db.execute("""SELECT l.id, l.from_id, l.to_id FROM links l
        WHERE l.retires IS NULL AND NOT EXISTS (SELECT 1 FROM links r WHERE r.retires=l.id)
        ORDER BY l.created_at, l.id""")
    result = {}
    for row in rows:
        for endpoint in (row['from_id'], row['to_id']):
            ids = result.setdefault(endpoint, [])
            if row['id'] not in ids:
                ids.append(row['id'])
    return result


def _attach(node, key, ids):
    node[key] = ids[:ATTACHED_LIMIT]
    node[key + '_total'] = len(ids)


# Model.

def _empty_node(node_id, kind, title, path):
    return {'id': node_id, 'kind': kind, 'title': title, 'path': path, 'languages': [], 'files': 0,
            'lines': 0, 'status': 'idle', 'work': [], 'work_total': 0, 'lessons': [], 'lessons_total': 0,
            'links': [], 'links_total': 0, 'flags': []}


def _component_id(path):
    return 'component:' + path


def model(memory, *, level='component', focus=None, work_states=None, cache=None):
    """Return the static structure of the project with work, lessons and links attached."""
    from .reviews import project_paths
    if level not in {'component', 'file'}:
        raise InvalidRecord('Architecture level must be component or file.')
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
    used = set()
    for package_id, use in package_uses.items():
        for name in use['files']:
            if level == 'file' and _parent(name) != focus:
                continue
            key = (owner(name), package_id, 'uses_package')
            edges[key] = edges.get(key, 0) + 1
            used.add(package_id)
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
        package_nodes[package_id] = (node, import_id)
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
        package_nodes[package_id] = (node, package_id)
    final_edges = []
    for (a, b, kind), weight in sorted(edges.items()):
        if kind == 'uses_package':
            ecosystem = b.split(':')[1]
            name = b.split(':', 2)[2]
            declared_id = declared_normal.get((ecosystem, _normal_package(ecosystem, name)))
            if declared_id:
                b = declared_id
        final_edges.append({'from': a, 'to': b, 'type': kind, 'weight': weight})
    targets = {edge['to'] for edge in final_edges if edge['type'] == 'uses_package'}
    for package_id, (node, import_id) in package_nodes.items():
        if level == 'file' and package_id not in targets:
            continue
        nodes[package_id] = node
        members[package_id] = []

    # Merge duplicate aggregated edges after package id normalisation.
    merged = {}
    for edge in final_edges:
        key = (edge['from'], edge['to'], edge['type'])
        merged[key] = merged.get(key, 0) + edge['weight']
    final_edges = [{'from': a, 'to': b, 'type': kind, 'weight': weight} for (a, b, kind), weight in sorted(merged.items())]

    match, from_guards = _matcher()
    work = _work_items(memory)
    lessons = _lessons(memory)
    guard_list = _guards(memory, lessons)
    link_map = _links(memory)
    recorded = {item['id']: item['state'] for item in work}
    states = work_states if work_states is not None else recorded

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
    guarded = matching(guard_list, 'paths')
    rank = {state: index for index, state in enumerate(('blocked', 'review', 'in_progress', 'ready', 'backlog', 'done', 'cancelled'))}
    for node_id, node in nodes.items():
        items = work_by_node.get(node_id, [])
        items = sorted(items, key=lambda item: rank.get(states.get(item['id']), len(rank)))
        _attach(node, 'work', [item['id'] for item in items])
        _attach(node, 'lessons', [item['id'] for item in lessons_by_node.get(node_id, [])])
        _attach(node, 'links', link_map.get(node_id, []))
        status = 'idle'
        found = {states.get(item['id']) for item in items}
        if guarded.get(node_id):
            found.add('guarded')
        for candidate in STATUS_ORDER:
            if candidate in found:
                status = candidate
                break
        node['status'] = status

    language_totals = {}
    for parsed in sources.values():
        total = language_totals.setdefault(parsed['language'], {'files': 0, 'lines': 0})
        total['files'] += 1
        total['lines'] += parsed['lines']
    ordered = sorted(nodes.values(), key=lambda n: ({'file': 0, 'component': 1, 'package': 2}[n['kind']], n['id']))
    note = NOTE
    if not from_guards:
        note += ' Path patterns use a temporary local matcher because the guards module is not available.'
    return {'root': str(root), 'level': level, 'focus': focus, 'nodes': ordered, 'edges': final_edges,
            'packages': packages, 'languages': language_totals, 'issues': issues,
            'truncated': truncated, 'note': note}
