"""What every architecture layer shares: the project folder, path rules, bounded file reads and the node shape.

The code layer, the n8n layer and the authored layer all build nodes of one
shape, read project files under one set of limits and compare relative POSIX
paths the same way, so those rules are written here once and each layer calls
them. Every function reads; none creates a table.
"""
import json
from pathlib import Path, PurePosixPath
from stat import S_ISREG

MAX_FILE_BYTES = 1_000_000
ATTACHED_LIMIT = 20
N8N_PREFIX = 'n8n:'


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


def table_exists(memory, name):
    """True when an optional table exists. Read paths ask before they select, so they never create it."""
    return bool(memory.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def link_rows(memory):
    """Active explicit graph links, oldest first. Empty when the links table does not exist."""
    if not table_exists(memory, 'links'):
        return []
    rows = memory.db.execute("""SELECT l.id, l.from_id, l.to_id, l.type, l.reason FROM links l
        WHERE l.retires IS NULL AND NOT EXISTS (SELECT 1 FROM links r WHERE r.retires=l.id)
        ORDER BY l.created_at, l.id""")
    return [dict(row) for row in rows]


# Relative POSIX paths inside the project.

def parent(name):
    return str(PurePosixPath(name).parent)


def join(base, *parts):
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


# Reading project files.

class Unreadable(Exception):
    """A project file was not read. The message states the reason in one sentence."""


def readable(root, name):
    """Return (path, stat) for a regular project file within the size limit.

    The stat is None when the name is not a regular file. A path that leaves the
    project, a file above the size limit and a file that cannot be read raise
    Unreadable, so no layer reads outside the project and each one decides
    whether to report the reason or to pass over the file.
    """
    path = root / name
    try:
        if not path.resolve().is_relative_to(root):
            raise Unreadable('Skipped because the file points outside the project.')
        stat = path.stat()
    except OSError as exc:
        raise Unreadable('The file could not be read: ' + str(exc) + '.') from exc
    if not S_ISREG(stat.st_mode):
        return path, None
    if stat.st_size > MAX_FILE_BYTES:
        raise Unreadable('Skipped because the file exceeds 1 MB.')
    return path, stat


# The node shape every layer builds.

def empty_node(node_id, kind, title, path, layer='code'):
    return {'id': node_id, 'kind': kind, 'title': title, 'path': path, 'layer': layer, 'languages': [], 'files': 0,
            'lines': 0, 'status': 'idle', 'work': [], 'work_total': 0, 'lessons': [], 'lessons_total': 0,
            'links': [], 'links_total': 0, 'flags': []}
