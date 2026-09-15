"""Check the actual wheel, source archive and desktop bundle before publishing."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import tarfile
import zipfile

from check_publication import check_member


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
    ui_files = ('state.js', 'records.js', 'api.js', 'sync.js', 'navigation.js', 'board.js', 'editor.js', 'reviews.js', 'approvals.js', 'skills.js', 'map.js', 'boot.js', 'workspace.css')
    for name in tuple('memory_module/ui/' + name for name in ui_files) + ('memory_module/viewer.html',
                 'memory_module/assets/manrope-latin-400.woff2',
                 'memory_module/assets/manrope-latin-700.woff2'):
        if name not in members:
            raise ValueError(f'Required viewer asset is missing: {name}')
    return {'file': path.name, 'bytes': path.stat().st_size, 'members': len(members),
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def main():
    root = Path(sys.argv[1] if len(sys.argv) > 1 else 'dist')
    report = []
    for pattern, kind in (('*.whl', 'wheel'), ('*.tar.gz', 'sdist'), ('*.mcpb', 'bundle')):
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
