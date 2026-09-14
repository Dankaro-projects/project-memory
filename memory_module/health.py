"""Separate database health, baseline state and observed host activation."""
import json
from pathlib import Path
import queue
import subprocess
import threading
import time
from . import __version__, codex_host
from .direction import PLACEHOLDER


def inspect(memory):
    direction = memory.direction()
    installed = codex_host.exists(memory)
    last = memory.db.execute('SELECT created_at FROM host_receipts ORDER BY rowid DESC LIMIT 1').fetchone() if installed else None
    failure_path = memory.path.with_suffix('.capture-error.json')
    failure = json.loads(failure_path.read_text()) if failure_path.exists() else None
    from .coverage import sessions
    coverage = sessions(memory,limit=5) if installed else {'sessions':[],'more':False}
    counts = {r[0]: r[1] for r in memory.db.execute('SELECT event_name,count(*) FROM host_receipts GROUP BY event_name')} if installed else {}
    groups=memory.metrics()['groups']
    costs={key: {'total':sum(g['reported_costs'][key]['total'] for g in groups),
                 'reported_decisions':sum(g['reported_costs'][key]['reported_decisions'] for g in groups)}
           for key in ('tokens','context_characters','human_corrections','repeated_research','maintenance_ms')}
    return {'measurements':costs,'decisions':sum(g['decisions'] for g in groups),
            'assessed':sum(g['assessed'] for g in groups),'project': memory.project, 'database': str(memory.path), 'package_version': __version__,
            'baseline': {'status': 'not_established' if direction['requirements'] == [PLACEHOLDER] else 'recorded',
                         'requirements': 0 if direction['requirements'] == [PLACEHOLDER] else len(direction['requirements']),
                         'version': direction['version'], 'evidence_status': direction.get('status', 'current'),
                         'coverage': 'unassessed',
                         'note': 'Captured files and a populated list do not prove that the product requirements are complete.'},
            'capture': {'observed_hooks': counts, 'last_receipt_at': last[0] if last else None,
                        'failure':failure, 'status':'failed' if failure else 'observed' if last else 'not_observed'},
            'recording_checks':coverage,
            'native_tools_in_current_task': 'unverified',
            'next_step': 'Confirm that this task can call memory_get. Use doctor for current host discovery; historical receipts alone do not establish activation.'}


def codex_hooks(project):
    """Use the installed host's read-only discovery. Never change trust here."""
    process = subprocess.Popen(['codex', 'app-server', '--stdio'], cwd=project, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    messages = queue.Queue()
    def read():
        for line in process.stdout:
            try: messages.put(json.loads(line))
            except ValueError: continue
        messages.put({'id': -1, 'error': {'message': 'Codex exited before answering.'}})
    threading.Thread(target=read, daemon=True).start()
    def request(i, method, params):
        process.stdin.write(json.dumps({'id': i, 'method': method, 'params': params}) + '\n'); process.stdin.flush()
        end = time.monotonic() + 15
        while time.monotonic() < end:
            try: item = messages.get(timeout=max(.1, end-time.monotonic()))
            except queue.Empty: raise TimeoutError('Codex did not answer ' + method) from None
            if item.get('id') in {i, -1}:
                if 'error' in item: raise RuntimeError(item['error']['message'])
                return item['result']
        raise TimeoutError('Codex did not answer ' + method)
    try:
        request(1, 'initialize', {'clientInfo': {'name': 'project-memory-doctor', 'version': __version__},
                                  'capabilities': {'experimentalApi': True}})
        result = request(2, 'hooks/list', {'cwds': [str(Path(project).resolve())]})
        return diagnose_hooks(result['data'][0])
    finally:
        process.terminate()
        try: process.wait(timeout=3)
        except subprocess.TimeoutExpired: process.kill(); process.wait()
        process.stdin.close(); process.stdout.close()


def diagnose_hooks(discovered):
    hooks = [h for h in discovered.get('hooks', []) if
             any(marker in h.get('command', '') for marker in ('project-memory', 'memory_module'))]
    selected = [{k: h.get(k) for k in ('eventName', 'event', 'source', 'sourcePath', 'enabled', 'trustStatus', 'command') if k in h} for h in hooks]
    groups = {}
    for h in hooks:
        if h.get('enabled'):
            event=h.get('eventName', h.get('event',''))
            groups.setdefault(event[:1].upper()+event[1:], []).append(h)
    duplicates = {event: len(rows) for event, rows in groups.items() if len(rows) > 1}
    wrong_host = any(h.get('enabled') and '--host claude' in h.get('command', '') for h in hooks)
    untrusted = sum(h.get('trustStatus') != 'trusted' for h in hooks if h.get('enabled'))
    errors = discovered.get('errors', [])
    missing=sorted(codex_host.EVENTS - groups.keys())
    ready = not missing and bool(hooks) and not (errors or duplicates or wrong_host or untrusted)
    return {'status': 'ready_for_new_session' if ready else 'needs_attention', 'hooks': selected,
            'missing_events': missing, 'untrusted': untrusted, 'duplicates': duplicates, 'wrong_host': wrong_host, 'errors': errors,
            'note': 'Discovery verifies configuration and trust, not execution or native tool exposure in an already open task.'}
