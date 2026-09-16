"""The git worktree of a delegated run and the evidence its changes produce.

This module holds the mechanics that delegation.py uses: reading the repository
layout, creating and removing a worktree, committing what a worker changed, and
turning the result into reviewable evidence. Renames are compared as a deletion
and an addition, so a file moved out of the plan paths counts as a change
outside them. Untracked files in common tool cache folders are not committed, so
a check that the worker runs does not turn valid work into a scope violation;
modified tracked files in those folders are still committed and compared. Values
that look like secrets are replaced in the text that leaves the run folder, and
text is extracted from changed Word, PowerPoint and Excel files so that the
reviewer can inspect them; other binary files cannot be inspected.
"""
import json
from pathlib import Path
import re

from .core import InvalidRecord
from . import documents
from .shared import git, git_message
from .templates import COMMIT_IDENTITY

DIFF_SOURCE_LIMIT = 1_000_000
PREVIEW_LIMIT = 100_000
TOOL_CACHE_PATTERNS = ('**/__pycache__/**', '**/*.pyc', '**/.pytest_cache/**', '**/.mypy_cache/**', '**/.ruff_cache/**',
                       '**/.hypothesis/**', '**/.tox/**', '**/.nox/**', '**/.venv/**', '**/node_modules/**', '**/.cache/**',
                       '**/.parcel-cache/**', '**/.eslintcache', '**/.coverage', '**/.DS_Store')
REDACTED = '[value removed by Project Memory]'
SECRET_PATTERNS = (
    (re.compile(r'(?i)\b(?:bearer|basic)\s+([A-Za-z0-9._~+/=-]{12,})'), 1),
    (re.compile(r'\b((?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{8,})'), 1),
    (re.compile(r'\b(sk-[A-Za-z0-9_-]{16,})'), 1),
    (re.compile(r'\b(gh[pousr]_[A-Za-z0-9]{20,})'), 1),
    (re.compile(r'\b(xox[abprs]-[A-Za-z0-9-]{10,})'), 1),
    (re.compile(r'\b(AKIA[0-9A-Z]{16})\b'), 1),
    (re.compile(r'\b(AIza[0-9A-Za-z_-]{30,})'), 1),
    (re.compile(r'(?i)"(?:api[_-]?key|apikey|access[_-]?token|refresh[_-]?token|client[_-]?secret|secret|password|passwd|'
                r'private[_-]?key|x-api-key)"\s*:\s*"([^"\\]{4,})"'), 1),
    (re.compile(r'(?i)"name"\s*:\s*"[^"]*(?:key|token|secret|password|authorization)[^"]*"\s*,\s*"value"\s*:\s*"([^"\\]{4,})"'), 1),
)


def repository(project):
    """Return the repository top level and the project's prefix inside it."""
    top = Path(git(project, 'rev-parse', '--show-toplevel').stdout.strip())
    return top, git(project, 'rev-parse', '--show-prefix').stdout.strip()


def project_relative(name, prefix, top):
    """Convert a repository path to a project path; paths outside the project become absolute."""
    if not prefix:
        return name
    return name[len(prefix):] if name.startswith(prefix) else str(top / name)


def status_paths(root):
    """Paths with uncommitted changes, relative to the repository top level."""
    entries = git(root, 'status', '--porcelain', '-z', '--untracked-files=all').stdout.split('\0')
    paths = []
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if len(entry) < 4:
            continue
        paths.append(entry[3:])
        if 'R' in entry[:2] or 'C' in entry[:2]:
            if index < len(entries) and entries[index]:
                paths.append(entries[index])
            index += 1
    return list(dict.fromkeys(paths))


def branch_exists(project, branch):
    return bool(branch) and git(project, 'rev-parse', '--verify', '--quiet', 'refs/heads/' + branch, check=False).returncode == 0


def cleanup(project, workspace, branch):
    """Remove a run's worktree and delete its branch. Missing items are not errors; failures are listed in errors."""
    result = {'worktree_removed': True, 'branch_deleted': True, 'errors': []}
    workspace = Path(workspace)
    if workspace.exists():
        removed = git(project, 'worktree', 'remove', '--force', str(workspace), check=False)
        result['worktree_removed'] = removed.returncode == 0 and not workspace.exists()
        if not result['worktree_removed']:
            result['errors'].append('The worktree was not removed: ' + git_message(removed))
    git(project, 'worktree', 'prune', check=False)
    if branch and branch_exists(project, branch):
        deleted = git(project, 'branch', '-D', branch, check=False)
        result['branch_deleted'] = deleted.returncode == 0
        if not result['branch_deleted']:
            result['errors'].append('The branch was not deleted: ' + git_message(deleted))
    return result


def cleaned(result):
    """True when a cleanup removed both the worktree and the branch."""
    return result['worktree_removed'] and result['branch_deleted']


def remains(project, workspace, branch):
    """True when the worktree or the branch of a run still exists."""
    return Path(workspace).exists() or branch_exists(project, branch)


def redact(text):
    """Replace values that look like secrets. Return the text and the number of replaced values."""
    count = 0

    def replace(match, group):
        nonlocal count
        count += 1
        start, end = match.span(group)
        offset = match.start()
        whole = match.group(0)
        return whole[:start - offset] + REDACTED + whole[end - offset:]

    for pattern, group in SECRET_PATTERNS:
        text = pattern.sub(lambda match, group=group: replace(match, group), text)
    return text, count


def _binary_changes(project, workspace, base, commit, prefix, top):
    """Changed binary files with text extracted from Word, PowerPoint and Excel files."""
    output = git(project, 'diff', '--numstat', '-z', '--no-renames', base, commit).stdout
    result = []
    for entry in output.split('\0'):
        parts = entry.split('\t', 2)
        if len(parts) != 3 or parts[0] != '-' or parts[1] != '-':
            continue
        name = parts[2]
        item = {'path': project_relative(name, prefix, top), 'preview': 'none', 'text': None}
        path = Path(workspace) / name
        suffix = path.suffix.lower()
        try:
            if not path.exists():
                item['preview'] = 'deleted'
            elif suffix in documents.OFFICE_SUFFIXES and path.is_file() and path.stat().st_size <= documents.MAX_BYTES:
                text = documents.office_text(path.read_bytes(), suffix)
                if len(text) > PREVIEW_LIMIT:
                    text = text[:PREVIEW_LIMIT] + f'\n\nThis preview is shortened to {PREVIEW_LIMIT:,} characters.'
                item.update(preview='extracted', text=text)
        except (OSError, InvalidRecord) as exc:
            item['preview_error'] = str(exc)
        result.append(item)
    return result


def _data_warnings(workspace, names, prefix, top):
    """Warnings for changed workflow exports that contain pinned data, which can hold client records."""
    warnings = []
    for name in names:
        if not name.lower().endswith('.json'):
            continue
        path = Path(workspace) / name
        try:
            if not path.is_file() or path.stat().st_size > documents.MAX_BYTES:
                continue
            value = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError, UnicodeDecodeError):
            continue
        items = value if isinstance(value, list) else [value]
        if any(isinstance(item, dict) and isinstance(item.get('nodes'), list) and item.get('pinData') for item in items):
            warnings.append(f'The workflow export {project_relative(name, prefix, top)} contains pinned data, which can hold client records.')
    return warnings


def collect(project, workspace, base, branch, prefix, run_id):
    """Commit worktree changes on the branch and return the changed project paths, the commit, the diff and binary changes."""
    head = git(workspace, 'symbolic-ref', '--quiet', '--short', 'HEAD', check=False).stdout.strip()
    if head != branch:
        raise InvalidRecord('The worker moved the worktree away from its branch. Inspect the worktree before retrying.')
    # Tracked files first, including those in cache folders, then untracked files outside tool cache folders.
    git(workspace, 'add', '-u')
    git(workspace, 'add', '-A', '--', '.', *(':(exclude,glob)' + pattern for pattern in TOOL_CACHE_PATTERNS))
    if git(workspace, 'diff', '--cached', '--quiet', check=False).returncode == 1:
        git(workspace, *COMMIT_IDENTITY, 'commit', '--no-verify', '-q', '-m', f'Delegated work {run_id}')
    commit = git(project, 'rev-parse', 'refs/heads/' + branch).stdout.strip()
    top = Path(git(project, 'rev-parse', '--show-toplevel').stdout.strip())
    # Without rename detection a file moved out of another folder is reported at its old path as well.
    names = [name for name in git(project, 'diff', '--name-only', '--no-renames', '-z', base, commit).stdout.split('\0') if name]
    return {'changed': [project_relative(name, prefix, top) for name in names], 'commit': commit,
            'diff': git(project, 'diff', '--no-renames', base, commit).stdout,
            'binary': _binary_changes(project, workspace, base, commit, prefix, top),
            'warnings': _data_warnings(workspace, names, prefix, top)}


def diff_body(diff, binary, folder):
    """The source body of a run diff: redacted, with extracted binary text, bounded. Return (body, redactions)."""
    body = diff if diff.strip() else 'The delegated run changed no files.'
    if binary:
        body += '\n\nText extracted from changed binary files for review.\n'
        for item in binary:
            text = item['text'] if item['preview'] == 'extracted' else (
                'The file was deleted.' if item['preview'] == 'deleted' else 'No text preview is available for this file.')
            body += f'\n### {item["path"]}\n\n{text}\n'
    body, redactions = redact(body)
    if len(body) > DIFF_SOURCE_LIMIT:
        body = body[:DIFF_SOURCE_LIMIT] + f'\n\nThis diff was truncated to {DIFF_SOURCE_LIMIT:,} characters. The complete diff is in {folder / "diff.patch"}.'
    return body, redactions
