"""Check public source and package members without printing sensitive content."""
from pathlib import Path, PurePosixPath
import argparse
import re
import subprocess


ROOT_FILES = {
    '.gitignore', 'README.md', 'CHANGELOG.md', 'CONTRIBUTING.md', 'LICENSE',
    'SECURITY.md', 'pyproject.toml',
}
DOCS = {
    'agents.md', 'control-panel.md', 'evidence.md', 'record-fields.md',
    'releasing.md', 'setup.md', 'user-guide.md',
}
FIXED_FILES = {
    '.agents/plugins/marketplace.json', '.claude-plugin/marketplace.json',
    '.github/ISSUE_TEMPLATE/bug_report.yml', '.github/workflows/ci.yml',
    '.github/workflows/release.yml',
    'plugins/project-memory/.claude-plugin/plugin.json',
    'plugins/project-memory/.codex-plugin/plugin.json',
    'plugins/project-memory/.mcp.json', 'plugins/project-memory/claude-hooks.json',
    'plugins/project-memory/skills/project-memory/SKILL.md',
    'examples/__init__.py', 'examples/demo.py', 'examples/project.json',
    'examples/episode.json',
}
RUNTIME_ASSETS = {
    'memory_module/assets/manrope-latin-400.woff2',
    'memory_module/assets/manrope-latin-700.woff2',
}


def agent_role_file(name):
    """Role instructions shipped with the package, one Markdown file per run role."""
    path = PurePosixPath(name)
    return path.parent.as_posix() == 'memory_module/agents' and path.suffix == '.md'
PRIVATE_PATTERNS = {
    'possible credential': re.compile(
        rb'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}'
        rb'|sk-proj-[A-Za-z0-9_-]{30,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)'),
    'personal filesystem path': re.compile(
        rb'(?:/(?:Users|home)/[A-Za-z0-9_.-]+/|[A-Za-z]:\\Users\\[A-Za-z0-9_.-]+\\)'),
    'host or provider session identifier': re.compile(
        rb'\b01[0-9a-f]{6}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b', re.I),
    'private workspace URL': re.compile(rb'https?://127\.0\.0\.1:\d+/[A-Za-z0-9_-]{20,}/'),
}


def runtime_file(name):
    path = PurePosixPath(name)
    return name in RUNTIME_ASSETS or agent_role_file(name) or (path.parent.as_posix() in {'memory_module/ui', 'memory_module/vendor'}
        and path.suffix in {'.js', '.css'}) or (
        path.parent.as_posix() == 'memory_module' and path.suffix in {'.py', '.html'})


def source_file(name):
    path = PurePosixPath(name)
    return (
        name in ROOT_FILES or name in FIXED_FILES or runtime_file(name)
        or (path.parent.as_posix() == 'docs' and path.name in DOCS)
        or (path.parent.as_posix() in {'scripts', 'tests', 'tests/integration', 'tests/browser'}
            and path.suffix == '.py' and not path.name.startswith('.'))
        or (path.parent.as_posix() == 'tests/browser' and path.suffix == '.cjs')
    )


def check_member(name, body, kind='source'):
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or '\\' in name:
        raise ValueError(f'Unsafe member path: {name}')
    if kind == 'wheel':
        metadata = (len(path.parts) > 1 and path.parts[0].endswith('.dist-info')
                    and '/'.join(path.parts[1:]) in {
                        'METADATA', 'WHEEL', 'entry_points.txt', 'RECORD', 'licenses/LICENSE'})
        allowed = runtime_file(name) or metadata
    else:
        allowed = source_file(name) or (kind == 'sdist' and name == 'PKG-INFO')
    if not allowed:
        raise ValueError(f'Unapproved public file: {name}')
    if name in RUNTIME_ASSETS and path.suffix == '.woff2':
        if not body.startswith(b'wOF2'):
            raise ValueError(f'Invalid font file: {name}')
        return
    try:
        body.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise ValueError(f'Unexpected binary file: {name}') from exc
    for description, pattern in PRIVATE_PATTERNS.items():
        if pattern.search(body):
            raise ValueError(f'{description.capitalize()} in {name}; inspect privately.')


def check_repository(root):
    files = subprocess.check_output(
        ['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=root)
    names = sorted(set(files.decode('utf-8').strip('\0').split('\0')) - {''})
    checked = 0
    for name in names:
        path = root / name
        if path.is_symlink():
            raise ValueError(f'Public symlink requires review: {name}')
        if path.exists():
            check_member(name, path.read_bytes())
            checked += 1
    return checked


def check_commit(root, ref):
    entries = subprocess.check_output(['git', 'ls-tree', '-rz', ref], cwd=root)
    checked = 0
    for entry in entries.rstrip(b'\0').split(b'\0'):
        if not entry:
            continue
        header, name = entry.split(b'\t', 1)
        mode, kind, oid = header.split()
        name = name.decode('utf-8')
        if kind != b'blob' or mode not in {b'100644', b'100755'}:
            raise ValueError(f'Unexpected public member: {name}')
        body = subprocess.check_output(['git', 'cat-file', 'blob', oid.decode()], cwd=root)
        check_member(name, body)
        checked += 1
    return checked


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ref', help='Check the committed tree at this Git ref.')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        count = check_commit(root, args.ref) if args.ref else check_repository(root)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(f'Publication check passed for {count} source files. History requires a separate audit.')
