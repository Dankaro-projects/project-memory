"""Project scoped, authenticated loopback server for the control panel. SQLite remains authoritative.

GET `api/<name>` serves the read functions in api.py with ETags. POST
`api/actions` sends one human action to workspace.py. The server binds to
127.0.0.1 only, requires the token in the path, checks Host and Origin, requires
the CSRF header and JSON content for writes, and exits after ten idle minutes.
"""
import argparse
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import TCPServer
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import threading
import time
from urllib.parse import parse_qs, urlsplit
from urllib.request import build_opener, ProxyHandler
import uuid

from . import Memory
from . import api
from .core import MemoryError, InvalidRecord, Conflict, dumps
from .documents import document_path, PREFIX
from .install import atomic

IDLE_SECONDS = 600
MAX_ACTION_BYTES = 2_000_000
MAX_QUERY = 4096
TREE_INTERVAL = 5.0
EXPIRED_RUN_SECONDS = 30
ERRORS = (MemoryError, ValueError, TypeError, KeyError, RecursionError, sqlite3.Error, OSError, subprocess.SubprocessError)


def html():
    """The control panel page for live use, with hashed script and style elements."""
    from .viewer import html_template, render
    data = {'live': True, 'project': 'Project Memory', 'exported_at': '', 'requirements': [], 'records': [], 'pending': [],
            'scope': {}, 'source_bodies_included': False, 'responses': {}}
    return render(html_template(), data, live=True).encode()


def file_fingerprint(root):
    """A metadata only fingerprint of the project files. It reads no file contents."""
    from .reviews import project_paths
    base, names = project_paths(root)
    digest = hashlib.sha256()
    for name in names:
        try:
            value = (base / name).lstat()
            digest.update(dumps([name, value.st_size, value.st_mtime_ns, value.st_ino]).encode())
        except FileNotFoundError:
            digest.update(dumps([name, None]).encode())
    return digest.hexdigest()


class Viewer(ThreadingHTTPServer):
    """Each connection is read in its own thread, so a slow client cannot stall the others.

    Endpoint reads share one read-only connection and run one at a time under `lock`.
    """
    allow_reuse_address = True
    daemon_threads = True
    tree_interval = TREE_INTERVAL

    def __init__(self, path, token, port=0):
        self.memory = Memory(path, read_only=True, any_thread=True)
        self.lock = threading.Lock()
        self.token = token
        self.boot = uuid.uuid4().hex
        self.memory._review_tree_cache = {}
        self.memory._architecture_cache = {}
        self.memory._api_cache = {}
        self.cache_revision = None
        self.csrf = secrets.token_urlsafe(24)
        self.last_access = time.monotonic()
        self.version = None
        self.paths = []
        self.next_review = None
        self.due_count = 0
        self.tree_checked = None
        self.tree_value = None
        self.db_identity = Path(path).stat().st_ino
        super().__init__(('127.0.0.1', port), Handler)

    def server_bind(self):
        # HTTPServer resolves a hostname here; this fixed loopback service needs no DNS.
        TCPServer.server_bind(self)
        self.server_name = '127.0.0.1'
        self.server_port = self.server_address[1]

    def project_state(self):
        """Project file state, recomputed at most once every tree_interval seconds.

        With agent checks, the content signature detects files that change without
        a database write. Otherwise a metadata fingerprint suffices, because only the
        architecture view reads project files.
        """
        from .reviews import project_tree
        moment = time.monotonic()
        if self.tree_checked is not None and moment - self.tree_checked < self.tree_interval:
            return self.tree_value
        try:
            signature = project_tree(self.memory)
            if signature is not None:
                value = ('review_project', signature)
            else:
                from .architecture import project_root
                value = ('project_files', file_fingerprint(project_root(self.memory)))
        except (OSError, subprocess.SubprocessError, InvalidRecord) as exc:
            # A failed check is retried on the next request instead of being kept for the interval.
            self.tree_checked = None
            return ('project_unavailable', str(exc))
        self.tree_checked = moment
        self.tree_value = value
        return value

    def revision(self):
        if self.memory.path.stat().st_ino != self.db_identity:
            self.last_access = 0
            raise InvalidRecord('The database file was replaced. Restart the viewer with project-memory view.')
        version = self.memory.db.execute('PRAGMA data_version').fetchone()[0]
        moment = self.memory.now()
        if version != self.version or self.next_review and moment >= self.next_review:
            self.due_count, self.next_review = self.memory.db.execute(
                'SELECT count(CASE WHEN review_after<=? THEN 1 END),min(CASE WHEN review_after>? THEN review_after END) FROM sources',
                (moment, moment)).fetchone()
            self.paths = [path for item in self.memory.db.execute('SELECT DISTINCT source_key FROM sources WHERE source_key LIKE ?', (PREFIX + '%',))
                          if (path := document_path(item[0])) is not None]
            self.version = version
        from .capture_errors import signature as failure_signature
        stats = [('capture_failures', failure_signature(self.memory.path))]
        for path in self.paths:
            try:
                value = path.stat()
                stats.append((str(path), value.st_mtime_ns, value.st_size, value.st_ino))
            except OSError:
                stats.append((str(path), None))
        stats.append(('project', self.project_state()))
        from .reviews import exists
        # Unavailability expires with time, without a database write.
        stats.append(('hosts', [item['available'] for item in api.host_overview(self.memory)['hosts']]))
        if exists(self.memory):
            from datetime import datetime, timezone
            expired = [item['id'] for item in self.memory.db.execute("SELECT id,updated_at FROM review_runs WHERE state IN ('queued','running','cancelling')")
                       if (datetime.now(timezone.utc) - datetime.fromisoformat(item['updated_at'])).total_seconds() > EXPIRED_RUN_SECONDS]
            stats.append(('expired_checks', expired))
        return self.boot + ':' + str(version) + ':' + str(self.due_count) + ':' + hashlib.sha256(dumps(stats).encode()).hexdigest()[:16]

    def read(self, name, params, revision):
        """Run one endpoint in a single read transaction, with results cached for this revision."""
        if self.cache_revision != revision:
            self.memory._api_cache = {}
            self.cache_revision = revision
        self.memory.db.execute('BEGIN')
        try:
            value = api.ENDPOINTS[name](self.memory, params)
        finally:
            self.memory.db.rollback()
        value = {'revision': revision, **value}
        if name == 'health':
            value.update(csrf=self.csrf, interactive=True)
        return value

    def server_close(self):
        super().server_close()
        self.memory.close()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def origin(self):
        return f'http://127.0.0.1:{self.server.server_port}'

    def route(self, path):
        """Return the part of the path after a valid token, or None."""
        parts = path.split('/', 2)
        if len(parts) < 3 or parts[0] != '':
            return None
        if not hmac.compare_digest(parts[1].encode(), self.server.token.encode()):
            return None
        return parts[2]

    def send_json(self, status, value, cache='no-store', etag=None):
        body = dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.security_headers(cache)
        if etag:
            self.send_header('ETag', etag)
        self.end_headers()
        self.write(body)

    def security_headers(self, cache):
        self.send_header('Cache-Control', cache)
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')

    def write(self, body):
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        origin = self.origin()
        endpoint = self.route(urlsplit(self.path).path)
        csrf = self.headers.get('X-Project-Memory') or ''
        if (self.headers.get('Host') != origin[7:] or self.headers.get('Origin') != origin
                or not hmac.compare_digest(csrf.encode(), self.server.csrf.encode()) or endpoint is None
                or self.headers.get('Content-Type') != 'application/json'):
            self.send_error(403)
            return
        if endpoint != 'api/actions':
            self.send_error(404)
            return
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= MAX_ACTION_BYTES:
                raise InvalidRecord('Workspace actions must be JSON smaller than 2 MB.')
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise InvalidRecord('The action must be an object.')
            self.server.last_access = time.monotonic()
            from .workspace import action
            with Memory(self.server.memory.path) as memory:
                value = action(memory, **data)
            status = 200
        except ERRORS as exc:
            status = 409 if isinstance(exc, Conflict) else 400
            value = {'error': type(exc).__name__, 'message': str(exc), **getattr(exc, 'details', {})}
        self.send_json(status, value)

    def do_GET(self):
        origin = self.origin()
        target = urlsplit(self.path)
        endpoint = self.route(target.path)
        if self.headers.get('Host') != origin[7:] or self.headers.get('Origin', origin) != origin or endpoint is None:
            self.send_error(403)
            return
        self.server.last_access = time.monotonic()
        if len(target.query) > MAX_QUERY:
            self.send_error(414)
            return
        params = {key: values[-1] for key, values in parse_qs(target.query).items()}
        if endpoint == '':
            try:
                body = html()
            except ERRORS as exc:
                self.send_json(500, {'error': type(exc).__name__, 'message': str(exc)})
                return
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.security_headers('no-cache, private')
            self.end_headers()
            self.write(body)
            return
        name = endpoint[4:] if endpoint.startswith('api/') else None
        if name not in api.ENDPOINTS:
            self.send_error(404)
            return
        try:
            with self.server.lock:
                revision = self.server.revision()
                etag = '"' + hashlib.sha256((revision + target.path + target.query).encode()).hexdigest() + '"'
                unchanged = self.headers.get('If-None-Match') == etag
                value = None if unchanged else self.server.read(name, params, revision)
            if unchanged:
                self.send_response(304)
                self.send_header('ETag', etag)
                self.end_headers()
                return
        except ERRORS as exc:
            self.send_json(400, {'error': type(exc).__name__, 'message': str(exc), **getattr(exc, 'details', {})},
                           cache='no-cache, private')
            return
        self.send_json(200, value, cache='no-cache, private', etag=etag)


def start(path):
    path = Path(path).resolve()
    state = path.parent / ('viewer.json' if path.name == 'project.sqlite' else path.name + '.viewer.json')
    with state.with_suffix('.lock').open('a+b') as lock:
        if os.name == 'nt':
            import msvcrt
            lock.write(b'0')
            lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX)
        return _start(path, state)


def _start(path, state):
    from . import __version__
    previous = json.loads(state.read_text()) if state.exists() else {}
    if previous.get('database') != str(path):
        previous = {}
    port = previous.get('port', 0)
    token = previous.get('token')
    valid = (type(port) is int and 0 < port < 65536 and isinstance(token, str) and len(token) >= 24
             and all(c.isalnum() or c in '-_' for c in token))
    expected = f'http://127.0.0.1:{port}/{token}/' if valid else None
    if previous.get('database') == str(path) and previous.get('url') == expected and expected:
        try:
            with build_opener(ProxyHandler({})).open(previous['url'] + 'api/health', timeout=1) as response:
                health = json.load(response)
                if health.get('database') == str(path):
                    if health.get('package_version') == __version__:
                        return {**{k: v for k, v in previous.items() if k != 'token'}, 'reused': True}
                    # Keep an older viewer intact; the new release opens its own
                    # port instead of reusing stale code or killing an unchecked PID.
                    port = 0
                    token = None
                    valid = False
        except (OSError, ValueError):
            pass
    # The credential stays in a private state file, not the process arguments.
    token = token if valid else secrets.token_urlsafe(24)
    info = {'database': str(path), 'token': token, 'port': port if valid else 0, 'phase': 'starting'}
    atomic(state, dumps(info))
    state.chmod(0o600)
    log = state.with_suffix('.log')
    with log.open('w') as out:
        from .install import python_args
        process = subprocess.Popen(python_args('memory_module.live') + ['--db', str(path), '--state', str(state)],
                                   stdin=subprocess.DEVNULL, stdout=out, stderr=out, start_new_session=os.name != 'nt')
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        value = json.loads(state.read_text())
        if value.get('phase') == 'ready':
            import threading
            threading.Thread(target=process.wait, daemon=True).start()
            return {k: v for k, v in value.items() if k != 'token'}
        if process.poll() is not None:
            raise RuntimeError('The viewer did not start. ' + log.read_text()[-4000:])
        time.sleep(.05)
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    raise RuntimeError('Viewer startup timed out. ' + log.read_text()[-4000:])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', required=True)
    parser.add_argument('--state', required=True)
    args = parser.parse_args()
    state = Path(args.state)
    info = json.loads(state.read_text())
    with Viewer(args.db, info['token'], info.get('port', 0)) as server:
        info.update(port=server.server_port, url=f'http://127.0.0.1:{server.server_port}/{info["token"]}/', phase='ready', pid=os.getpid())
        atomic(state, dumps(info))
        state.chmod(0o600)
        server.timeout = 1
        while time.monotonic() - server.last_access < IDLE_SECONDS:
            server.handle_request()


if __name__ == '__main__':
    main()
