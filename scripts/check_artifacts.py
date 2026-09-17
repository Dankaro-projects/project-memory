"""Check the actual wheel and source archive before publishing."""
import ast
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import tarfile
import zipfile

from check_publication import check_member


REQUIRED_MODULES = (
    'api', 'arch_authored', 'arch_base', 'arch_code', 'arch_n8n', 'architecture', 'capture_errors', 'cli',
    'codex_host', 'core', 'coverage', 'delegation', 'direction', 'documents', 'focus', 'graph', 'guards', 'health',
    'hive', 'hooks', 'host_profiles', 'hosts', 'install', 'live', 'machine', 'mcp', 'planning', 'reports', 'reviews', 'schema', 'sessions',
    'setup_codex', 'shared', 'templates', 'usage', 'viewer', 'workflow', 'workspace', 'worktree',
)
RETIRED_MODULES = ('maps', 'skills', 'project_dependencies', 'review_logs')


def ui_scripts(members):
    """The browser scripts that the packaged viewer.py embeds, read from the archive itself."""
    source = members.get('memory_module/viewer.py')
    if source is None:
        raise ValueError('Required module is missing: memory_module/viewer.py')
    for node in ast.parse(source.decode('utf-8')).body:
        if isinstance(node, ast.Assign) and any(getattr(target, 'id', None) == 'UI_SCRIPTS' for target in node.targets):
            return tuple(ast.literal_eval(node.value))
    raise ValueError('The packaged viewer does not declare its browser scripts.')


def inspect(path, kind):
    members = {}
    if kind == 'sdist':
        with tarfile.open(path) as archive:
            for entry in archive.getmembers():
                if entry.isdir():
                    continue
                if not entry.isfile():
                    raise ValueError(f'Unexpected archive link: {entry.name}')
                parts = PurePosixPath(entry.name).parts
                if len(parts) < 2 or '..' in parts or entry.name.startswith('/'):
                    raise ValueError(f'Unsafe archive path: {entry.name}')
                name = '/'.join(parts[1:])
                if name in members:
                    raise ValueError(f'Duplicate archive member: {name}')
                members[name] = archive.extractfile(entry).read()
    else:
        with zipfile.ZipFile(path) as archive:
            for entry in archive.infolist():
                if entry.is_dir():
                    continue
                if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError(f'Unexpected archive link: {entry.filename}')
                if entry.filename in members:
                    raise ValueError(f'Duplicate archive member: {entry.filename}')
                members[entry.filename] = archive.read(entry)
    for name, body in members.items():
        check_member(name, body, kind)
    for module in REQUIRED_MODULES:
        if f'memory_module/{module}.py' not in members:
            raise ValueError(f'Required module is missing: memory_module/{module}.py')
    for module in RETIRED_MODULES:
        if f'memory_module/{module}.py' in members:
            raise ValueError(f'Retired module is still packaged: memory_module/{module}.py')
    ui_files = (*ui_scripts(members), 'panel.css')
    for name in tuple('memory_module/ui/' + name for name in ui_files) + ('memory_module/viewer.html', 'memory_module/vendor/cytoscape.min.js',
                 'memory_module/assets/manrope-latin-400.woff2',
                 'memory_module/assets/manrope-latin-700.woff2'):
        if name not in members:
            raise ValueError(f'Required viewer asset is missing: {name}')
    return {'file': path.name, 'bytes': path.stat().st_size, 'members': len(members),
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def main():
    root = Path(sys.argv[1] if len(sys.argv) > 1 else 'dist')
    report = []
    for pattern, kind in (('*.whl', 'wheel'), ('*.tar.gz', 'sdist')):
        paths = list(root.glob(pattern))
        if len(paths) != 1:
            raise ValueError(f'Expected exactly one {kind} in {root}.')
        report.append(inspect(paths[0], kind))
    print(json.dumps({'passed': True, 'artifacts': report}, indent=2))


if __name__ == '__main__':
    try:
        main()
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
