"""Independent attempts on a recorded problem, decided by a user-owned check."""
import hashlib
import json
import os
from pathlib import Path
import posixpath
import re
import subprocess
import sys
import time
import unicodedata

from .core import Conflict, InvalidRecord, USER_ACTOR, _text, dumps
from . import codex_host, delegation, guards, planning, reviews
from .shared import git, latest_review, settlement
from .worktree import cleanup, remains

FOCUS_ACTOR = 'focus-orchestrator'
MAX_ATTEMPTS = 3
PARALLEL_ATTEMPTS = 2
DEFAULT_ATTEMPTS = 2
MODES = ('relay', 'parallel')
RULED_OUT_LIMIT = 600
OUTPUT_TAIL = 2000
CHECK_USER_ONLY = ('Only the user can set the check of a focused problem, in the control panel, because the check runs a '
                   'command on this computer.')
START_USER_ONLY = 'Only the user can start a focused problem, in the control panel.'
CHECK_MISSING = 'The focused problem has no check. Ask the user to set the check in the control panel before any attempt starts.'
NOT_ELIGIBLE = ('A focused problem starts only when the work item is blocked, when an earlier delegated run of it failed its '
                'review or its check, or when an outcome of it repeats a guarded failure.')
HYPOTHESIS_MISSING = 'Record at least one open hypothesis before the focused problem starts.'
DUPLICATE_HYPOTHESIS = 'This hypothesis repeats an earlier hypothesis of this work item. State a different hypothesis.'
CHECK_FILE_CHANGED = ('A file that the check names changed after the check was set: {path}. Set the check again '
                      'before the attempts start.')
CHECK_FILE_UNCOMMITTED = ('The check names a file that is not committed with its current content: {path}. Commit the '
                          'file before setting the check, because every attempt starts from the last commit.')
CHECK_FILE_REFUSED = ('The worker changed files that the check of the focused problem names: {files}. '
                      'An attempt may not change its own check, so this attempt is refused.')
CHECK_SUPPORT_REFUSED = ('The worker changed files that the check of the focused problem can load: {files}. An attempt may not '
                         'change the folder of a file the check names, and may not add a module that takes the place of a '
                         'standard Python module or of a test configuration module, so this attempt is refused.')
BLOCKED_NEXT_ACTION = ('Every attempt of the focused problem failed its check. Ask the user for direction before another '
                       'attempt.')
REVIEW_FAILED_NEXT_ACTION = ('The selected attempt passed its check but not its work review. Ask the user for direction before '
                             'another attempt.')
REQUEST_FAILED_NEXT_ACTION = ('The next attempt of the focused problem could not be requested. Ask the user for direction before '
                              'another attempt.')
CANCELLED_NEXT_ACTION = ('An attempt of the focused problem was cancelled. Ask the user for direction before another attempt.')
MERGE_FAILED_NEXT_ACTION = ('The selected attempt passed its check and its work review, but Project Memory could not merge it. '
                            'Ask the user to resolve the cause, then merge or discard the run.')
# Module names that Python or a test runner loads by name from any folder on the import path.
SHADOWED_MODULES = frozenset(sys.stdlib_module_names) | {'sitecustomize', 'usercustomize', 'conftest'}
ALREADY_RUNNING = 'A focused problem of this work item is already running. Wait for it to finish.'
CHECK_WHILE_RUNNING = 'The check cannot change while attempts of the focused problem are running. Wait until they finish.'
PARALLEL_LIMIT = 'Parallel mode allows 1 or 2 attempts, because a project runs at most two agent runs at the same time.'
HYPOTHESIS_RESULT_ONLY = 'Only Project Memory records the result of a hypothesis, after it runs the check of the focused problem.'
HYPOTHESIS_STARTS_OPEN = 'A new hypothesis starts in the open state.'
FOCUS_ACTOR_RESERVED = ('The actor name focus-orchestrator is reserved for the checks that Project Memory runs. Use your own '
                        'actor name.')
REPORT_BASIS = ('Every number comes from receipts, checks and reviews recorded by Project Memory, never from the report of '
                'an agent.')


def validate_block(value):
    if not isinstance(value, dict) or set(value) - {'problem', 'max_attempts', 'mode', 'check'}:
        raise InvalidRecord('The focus block accepts only problem, max_attempts, mode and check.')
    problem = value.get('problem')
    if not isinstance(problem, str) or not problem.strip() or len(problem) > 4000:
        raise InvalidRecord('The focused problem needs a problem statement of 1 to 4,000 characters.')
    count = value.get('max_attempts', DEFAULT_ATTEMPTS)
    if type(count) is not int or not 1 <= count <= MAX_ATTEMPTS:
        raise InvalidRecord('The focused problem allows 1 to 3 attempts.')
    mode = value.get('mode', 'relay')
    if mode not in MODES:
        raise InvalidRecord('The focus mode must be relay or parallel.')
    if mode == 'parallel' and count > PARALLEL_ATTEMPTS:
        raise InvalidRecord(PARALLEL_LIMIT)
    if 'check' in value:
        check = value['check']
        command = check.get('command') if isinstance(check, dict) else None
        if (not isinstance(command, list) or not 1 <= len(command) <= 50
                or any(not isinstance(arg, str) or not arg.strip() for arg in command)):
            raise InvalidRecord('The check must be a list of one or more arguments. No shell is used, so write each argument separately.')
        timeout = check.get('timeout_seconds')
        if type(timeout) is not int or not 10 <= timeout <= 1800:
            raise InvalidRecord('The check timeout must be between 10 and 1,800 seconds.')


def validate_hypothesis(payload):
    required = {'statement', 'approach', 'state'}
    optional = {'run_id', 'attempt', 'evidence_summary'}
    if not isinstance(payload, dict) or required - payload.keys() or payload.keys() - required - optional:
        raise InvalidRecord('A hypothesis requires statement, approach and state; optional: run_id, attempt and evidence_summary.')
    _text(payload['statement'], 'statement', 1000)
    _text(payload['approach'], 'approach', 2000)
    if payload['state'] not in ('open', 'confirmed', 'ruled_out'):
        raise InvalidRecord('The hypothesis state must be open, confirmed or ruled_out.')
    if 'attempt' in payload and (type(payload['attempt']) is not int or not 1 <= payload['attempt'] <= MAX_ATTEMPTS):
        raise InvalidRecord('The hypothesis attempt must be an integer from 1 to 3.')
    if 'run_id' in payload:
        _text(payload['run_id'], 'run_id', 200)
    if 'evidence_summary' in payload:
        value = payload['evidence_summary']
        if not isinstance(value, str) or len(value) > 2000:
            raise InvalidRecord('The hypothesis evidence summary must be text of at most 2,000 characters.')


def normalise(statement):
    # NFKC first, so a statement typed with combining accents (common in text from macOS) equals its composed form.
    return re.sub(r'[\W_]+', ' ', unicodedata.normalize('NFKC', statement).lower(), flags=re.UNICODE).strip()


def check_hypothesis(memory, episode, payload, supersedes, actor):
    """Validate one hypothesis lineage inside the event write transaction."""
    if supersedes is not None:
        if actor != FOCUS_ACTOR:
            raise InvalidRecord(HYPOTHESIS_RESULT_ONLY)
        previous = memory._event(supersedes)
        if previous['episode_id'] != episode['id'] or previous['kind'] != 'hypothesis':
            raise InvalidRecord('A hypothesis must supersede a hypothesis of the same work item.')
        if memory.db.execute('SELECT 1 FROM events WHERE supersedes=?', (supersedes,)).fetchone():
            raise Conflict('supersedes must identify the latest record of this hypothesis.')
        if any(payload[key] != previous['payload'][key] for key in ('statement', 'approach')):
            raise InvalidRecord('A hypothesis result must keep its statement and approach unchanged.')
        if payload['state'] == previous['payload']['state'] or not payload.get('run_id') or not payload.get('evidence_summary'):
            raise InvalidRecord('A hypothesis result needs a new state, run_id and evidence_summary.')
        return
    if payload['state'] != 'open':
        raise InvalidRecord(HYPOTHESIS_STARTS_OPEN)
    statement = normalise(payload['statement'])
    rows = memory.db.execute("SELECT payload FROM events WHERE episode_id=? AND kind='hypothesis'", (episode['id'],))
    if any(normalise(json.loads(row[0])['statement']) == statement for row in rows):
        raise InvalidRecord(DUPLICATE_HYPOTHESIS)


def _hypotheses(memory, episode_id):
    """Keep the first id and insertion order while following each independent revision chain."""
    roots, result = {}, {}
    for row in memory.db.execute("SELECT id,payload,supersedes FROM events WHERE episode_id=? AND kind='hypothesis' ORDER BY seq",
                                 (episode_id,)):
        root = roots[row['supersedes']] if row['supersedes'] else row['id']
        roots[row['id']] = root
        payload = json.loads(row['payload'])
        result[root] = {'id': root, 'record_id': row['id'], 'statement': payload['statement'], 'approach': payload['approach'],
                        'state': payload['state'], 'run_id': payload.get('run_id'), 'evidence_summary': payload.get('evidence_summary')}
    return list(result.values())


def _receipts(memory, episode_id=None, *, start_key=None, name=None):
    if not codex_host.exists(memory):
        return []
    conditions, args = ["event_name LIKE 'Focus%'"], []
    for clause, value in [('episode_id=?', episode_id), ("json_extract(payload,'$.start_key')=?", start_key), ('event_name=?', name)]:
        if value is not None:
            conditions.append(clause)
            args.append(value)
    rows = memory.db.execute('SELECT event_name,payload FROM host_receipts WHERE ' + ' AND '.join(conditions) + ' ORDER BY rowid', args)
    return [{'name': row['event_name'], **json.loads(row['payload'])} for row in rows]


def _receipt(memory, name, payload):
    delegation._ensure_receipts(memory)
    with memory._write():
        return codex_host.receipt(memory, session_id=payload['episode_id'], event_name=name, episode_id=payload['episode_id'],
                                  payload=payload, key='focus:' + payload['episode_id'] + ':' + payload['start_key'] + ':' + name
                                  + ':' + payload.get('run_id', payload.get('to_run', '')))


def _running(receipts):
    ended = {entry['start_key'] for entry in receipts if entry['name'] in ('FocusCompleted', 'FocusBlocked')}
    return any(entry['name'] == 'FocusStarted' and entry['start_key'] not in ended for entry in receipts)


def _scoped(episode_id, key):
    """An internal request key for a start key, unique across work items, because run request keys are unique per project."""
    return hashlib.sha256((episode_id + '\0' + key).encode()).hexdigest()[:32]


def settle(memory, episode_id=None):
    """Judge attempts of running starts that ended without reaching judge, such as a queued attempt that was cancelled.

    A cancelled queued run never executes, so delegation.after_work never calls judge for it. Without this step the
    start would stay running with no active run, and the item could neither start again nor change its check.
    """
    judged = []
    for started in _receipts(memory, episode_id, name='FocusStarted'):
        entries = _receipts(memory, started['episode_id'], start_key=started['start_key'])
        if any(item['name'] in ('FocusCompleted', 'FocusBlocked') for item in entries):
            continue
        done = {item['run_id'] for item in entries if item['name'] == 'FocusCheckRecorded'}
        done |= {item['from_run'] for item in entries if item['name'] == 'FocusAttemptRerouted'}
        runs = [item['run_id'] for item in entries if item['name'] == 'FocusAttemptRequested']
        runs += [item['to_run'] for item in entries if item['name'] == 'FocusAttemptRerouted']
        for run_id in runs:
            if run_id not in done and reviews.read(memory, run_id)['state'] == 'cancelled':
                judge(memory, run_id)
                judged.append(run_id)
    return judged


def reasons(memory, episode_id):
    memory.episode(episode_id)
    result = []
    plan = planning.latest(memory, episode_id, 'work_plan')
    if plan and plan['state'] == 'blocked':
        result.append({'type': 'blocked', 'reason': 'The work item is blocked.'})
    failed = reviews.exists(memory) and memory.db.execute("""SELECT 1 FROM review_runs r JOIN review_runs w ON w.id=r.parent_run
        WHERE w.episode_id=? AND w.role='work' AND r.role='work_review' AND r.state='changes_required' LIMIT 1""",
        (episode_id,)).fetchone()
    if failed or any(item['check_passed'] is False for item in _receipts(memory, episode_id, name='FocusCheckRecorded')):
        result.append({'type': 'failed_run', 'reason': 'An earlier delegated run failed its review or its focused check.'})
    for guard in guards.active_guards(memory):
        if guard['failure_type'] and memory.db.execute(f"""SELECT 1 FROM events o WHERE {guards.COUNTED_FAILURE}
            AND o.episode_id=? AND json_extract(o.payload,'$.failure_type')=?
            AND o.rowid > (SELECT rowid FROM events WHERE id=?) LIMIT 1""",
            (episode_id, guard['failure_type'], guard['review_id'])).fetchone():
            result.append({'type': 'guarded_recurrence', 'reason': 'An outcome repeats a failure recorded after its guard was accepted.'})
            break
    return result


def eligible(memory, episode_id):
    return bool(reasons(memory, episode_id))


def hypothesis(memory, episode_id, *, statement, approach, request_key, actor):
    return memory.record(episode_id, 'hypothesis', {'statement': statement, 'approach': approach, 'state': 'open'},
                         expected_version=memory.episode(episode_id)['version'], request_key=request_key, actor=actor)


def _revise(memory, episode_id, changes, *, request_key, actor):
    plan = planning.latest(memory, episode_id, 'work_plan')
    record = memory.read(plan['id'])
    return planning.save(memory, 'work_plan', episode_id=episode_id, expected_version=memory.episode(episode_id)['version'],
                          payload={**record['payload'], **changes}, actor=actor, request_key=request_key,
                          evidence=[{'source_id': item['source_id'], 'reason': item['reason']} for item in record['evidence']],
                          links=record['links'], session_id=record['payload'].get('session_id'))


def propose(memory, episode_id, *, problem, actor, request_key, max_attempts=DEFAULT_ATTEMPTS, mode='relay', hypotheses=None):
    with memory._write():
        plan = planning.latest(memory, episode_id, 'work_plan')
        if not plan:
            raise InvalidRecord('Record a work plan before proposing a focused problem.')
        block = {'problem': problem, 'max_attempts': max_attempts, 'mode': mode}
        if (plan.get('focus') or {}).get('check') is not None:
            block['check'] = plan['focus']['check']
        validate_block(block)
        if hypotheses is not None and (not isinstance(hypotheses, list) or len(hypotheses) > MAX_ATTEMPTS):
            raise InvalidRecord('Provide at most three hypotheses with statement and approach.')
        seen = {normalise(item['statement']) for item in _hypotheses(memory, episode_id)}
        for item in hypotheses or []:
            if not isinstance(item, dict) or set(item) != {'statement', 'approach'}:
                raise InvalidRecord('Each hypothesis needs statement and approach.')
            validate_hypothesis({**item, 'state': 'open'})
            statement = normalise(item['statement'])
            if statement in seen:
                raise InvalidRecord(DUPLICATE_HYPOTHESIS)
            seen.add(statement)
        saved = _revise(memory, episode_id, {'focus': block}, request_key=request_key, actor=actor)
        ids = [hypothesis(memory, episode_id, **item, actor=actor, request_key=f'{request_key}:hypothesis:{index}')['id']
               for index, item in enumerate(hypotheses or [], 1)]
    return {'episode_id': episode_id, 'plan_id': saved['id'], 'focus': block, 'hypothesis_ids': ids}


def check_files(project, command):
    project = Path(project).resolve()
    files = set()
    for argument in command:
        if Path(argument).is_absolute():
            continue
        # A pytest node id such as tests/test_x.py::test_one names the file before the first ::.
        candidates = [Path(argument.split('::', 1)[0])]
        parts = argument.split('.')
        if all(part.isidentifier() for part in parts):
            # A unittest target may name a module, a class or a method, so every leading part is tried as a module.
            candidates += [Path('/'.join(parts[:end]) + '.py') for end in range(len(parts), 0, -1)]
        for candidate in candidates:
            path = project / candidate
            if path.is_file() and path.resolve().is_relative_to(project):
                files.add(Path(os.path.normpath(candidate)).as_posix())
    return sorted(files)


def _committed(project, path):
    tracked = git(project, 'ls-files', '--error-unmatch', '--', path, check=False)
    return (tracked.returncode == 0 and not git(project, 'status', '--porcelain', '--', path).stdout
            and git(project, 'cat-file', '-e', 'HEAD:./' + path, check=False).returncode == 0)


def check_support(project, base, changed, check_files):
    """The changed files, other than the named check files, that the check can load in place of what the user set.

    These are files in the folder of a named check file other than the project folder, and added modules whose
    name takes the place of a standard Python module or of a module that Python or pytest loads by name.
    """
    named = set(check_files)
    folders = {posixpath.dirname(path) for path in named} - {''}
    result = []
    for path in changed:
        if path in named or path.startswith('/'):
            continue
        parts = path.split('/')
        name = parts[-1]
        stem = (parts[-2] if len(parts) > 1 else None) if name == '__init__.py' else name[:-3] if name.endswith('.py') else None
        if name.endswith('.pth'):
            stem = 'sitecustomize'
        added = stem in SHADOWED_MODULES and git(project, 'cat-file', '-e', base + ':./' + path, check=False).returncode != 0
        if posixpath.dirname(path) in folders or added or stem in ('sitecustomize', 'usercustomize', 'conftest'):
            result.append(path)
    return result


def set_check(memory, episode_id, *, command, timeout_seconds, request_key, actor):
    if actor != USER_ACTOR:
        raise InvalidRecord(CHECK_USER_ONLY)
    settle(memory, episode_id)
    with memory._write():
        block = (planning.latest(memory, episode_id, 'work_plan') or {}).get('focus')
        if not block:
            raise InvalidRecord('Record the focused problem before setting its check.')
        if _running(_receipts(memory, episode_id)):
            raise Conflict(CHECK_WHILE_RUNNING)
        check = {'command': command, 'timeout_seconds': timeout_seconds}
        block = {**block, 'check': check}
        validate_block(block)
        config = reviews.configured(memory)
        if not config:
            raise InvalidRecord('Configure an agent host before setting the check of a focused problem.')
        project = Path(config['project'])
        check['files'] = []
        for path in check_files(project, command):
            if not _committed(project, path):
                raise InvalidRecord(CHECK_FILE_UNCOMMITTED.format(path=path))
            check['files'].append({'path': path, 'sha256': hashlib.sha256((project / path).read_bytes()).hexdigest()})
        check['set_at'] = memory.now()
        _revise(memory, episode_id, {'focus': block}, request_key=request_key, actor=actor)
    return block


def _ruled_out(memory, episode_id):
    return [{'hypothesis_id': item['id'], **{key: item[key] for key in ('statement', 'evidence_summary', 'run_id')}}
            for item in _hypotheses(memory, episode_id) if item['state'] == 'ruled_out']


def _request_attempt(memory, started, number, *, problem):
    episode_id, key = started['episode_id'], started['start_key']
    entry = next(item for item in _hypotheses(memory, episode_id) if item['id'] == started['hypothesis_ids'][number - 1])
    config = reviews.configured(memory)
    if not config:
        raise InvalidRecord('Run setup with --client codex or --client claude before delegating work.')
    work = config.get('work_host') or config['hosts'][0]
    other = next((host for host in config['hosts'] if host != work), work)
    attempt = {'problem': problem, 'mode': started['mode'], 'start_key': key, 'attempt': number,
               'hypothesis': {field: entry[field] for field in ('id', 'statement', 'approach')},
               'ruled_out': [{field: item[field] for field in ('hypothesis_id', 'statement', 'evidence_summary')}
                             for item in _ruled_out(memory, episode_id)],
               'check': {field: started['check'][field] for field in ('command', 'timeout_seconds')},
               'check_files': [item['path'] for item in started['check'].get('files', [])]}
    return delegation.request_work(memory, episode_id, request_key=f'focus:{_scoped(episode_id, key)}:attempt:{number}',
                                   host=work if number % 2 else other, focus_attempt=attempt)


def _requested(memory, run):
    attempt = run['snapshot']['focus']
    _receipt(memory, 'FocusAttemptRequested', {'episode_id': run['episode_id'], 'start_key': attempt['start_key'],
             'attempt': attempt['attempt'], 'run_id': run['id'], 'host': run['host'], 'hypothesis_id': attempt['hypothesis']['id']})


def start(memory, episode_id, *, request_key, actor):
    if actor != USER_ACTOR:
        raise InvalidRecord(START_USER_ONLY)
    _text(request_key, 'request_key', 180)
    settle(memory, episode_id)
    with memory._write():
        receipts = _receipts(memory, episode_id)
        if any(item['name'] == 'FocusStarted' and item['start_key'] == request_key for item in receipts):
            return view(memory, episode_id)
        block = (planning.latest(memory, episode_id, 'work_plan') or {}).get('focus')
        if not block:
            raise InvalidRecord('Record the focused problem before starting it.')
        if not block.get('check'):
            raise InvalidRecord(CHECK_MISSING)
        if not eligible(memory, episode_id):
            raise InvalidRecord(NOT_ELIGIBLE)
        if _running(receipts):
            raise Conflict(ALREADY_RUNNING)
        opened = [item for item in _hypotheses(memory, episode_id) if item['state'] == 'open']
        if not opened:
            raise InvalidRecord(HYPOTHESIS_MISSING)
        config = reviews.configured(memory)
        if not config:
            raise InvalidRecord('Run setup with --client codex or --client claude before delegating work.')
        project = Path(config['project'])
        for item in block['check'].get('files', []):
            path = project / item['path']
            if (not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']
                    or not _committed(project, item['path'])):
                raise InvalidRecord(CHECK_FILE_CHANGED.format(path=item['path']))
        count = min(block.get('max_attempts', DEFAULT_ATTEMPTS), len(opened))
        started = {'episode_id': episode_id, 'start_key': request_key, 'mode': block.get('mode', 'relay'), 'attempts': count,
                   'check': block['check'], 'hypothesis_ids': [item['id'] for item in opened[:count]]}
        runs = [_request_attempt(memory, started, number, problem=block['problem'])
                for number in range(1, (count if started['mode'] == 'parallel' else 1) + 1)]
        _receipt(memory, 'FocusStarted', started)
        for run in runs:
            _requested(memory, run)
    for run in runs:
        delegation.launch(memory, run)
    return view(memory, episode_id)


def rank(attempts):
    return sorted(attempts, key=lambda item: (item['check_passed'] is not True,
                  item['changed_lines'] if item['changed_lines'] is not None else float('inf'), item['attempt']))


def _check(memory, run):
    attempt = run['snapshot']['focus']
    check = attempt['check']
    result = {'episode_id': run['episode_id'], 'start_key': attempt['start_key'], 'attempt': attempt['attempt'],
              'run_id': run['id'], 'host': run['host'], 'hypothesis_id': attempt['hypothesis']['id'],
              'command': check['command'], 'check_passed': False, 'exit_code': None, 'duration_ms': 0,
              'output_tail': '', 'timed_out': False, 'changed_lines': None,
              'evidence_summary': run['error'][:300] if run['error'] else 'The attempt changed no files.'}
    if run['state'] != 'completed' or not (run['metrics'] or {}).get('changed_files'):
        return result
    snapshot = run['snapshot']
    project = Path(snapshot['project'])
    commit = run['metrics'].get('commit') or run['branch']
    lines = git(project, 'diff', '--numstat', snapshot['base_commit'], commit).stdout.splitlines()
    result['changed_lines'] = sum(int(number) for line in lines for number in line.split('\t')[:2] if number.isdigit())
    # The check runs in a clean checkout of the collected commit, not in the worktree of the worker. A file hidden
    # from git, or a cache file that the collection leaves out, then cannot change the result of the check.
    tree = project / (run['workspace'] + '-check')
    cleanup(project, tree, None)
    begun = time.monotonic()
    output = ''
    try:
        git(project, 'worktree', 'add', '-q', '--detach', str(tree), commit)
        checked = subprocess.run(check['command'], cwd=tree / snapshot.get('repository_prefix', ''),
                                 timeout=check['timeout_seconds'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, errors='replace')
        output = checked.stdout
        result.update(check_passed=checked.returncode == 0, exit_code=checked.returncode)
        if result['check_passed']:
            result['evidence_summary'] = 'The check passed with exit code 0.'
        else:
            result['evidence_summary'] = f'The check failed with exit code {checked.returncode}.'
            nonempty = [line for line in output.splitlines() if line.strip()]
            if nonempty:
                result['evidence_summary'] += ' Last output line: ' + nonempty[-1][:200] + '.'
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ''
        if isinstance(output, bytes):
            output = output.decode('utf-8', errors='replace')
        result.update(timed_out=True, evidence_summary=f'The check did not finish within {check["timeout_seconds"]} seconds.')
    except (OSError, InvalidRecord) as exc:
        result['evidence_summary'] = ('The check could not start: ' + str(exc))[:2000]
    finally:
        cleanup(project, tree, None)
    result['duration_ms'] = round((time.monotonic() - begun) * 1000)
    result['output_tail'] = output[-OUTPUT_TAIL:]
    return result


def _discard(memory, run_id, reason):
    run = reviews.read(memory, run_id)
    project = Path(run['snapshot']['project'])
    if remains(project, project / run['workspace'], run['branch']):
        delegation.discard(memory, run_id, request_key='focus-discard:' + run_id, actor=FOCUS_ACTOR, reason=reason)


def _block(memory, started, next_action=BLOCKED_NEXT_ACTION, reason=''):
    episode_id, key = started['episode_id'], started['start_key']
    with memory._write():
        if _receipts(memory, episode_id, start_key=key, name='FocusBlocked'):
            return
        hypotheses = _ruled_out(memory, episode_id)
        detail = ' '.join(item['statement'] + ' ' + item['evidence_summary'] for item in hypotheses)
        _revise(memory, episode_id, {'state': 'blocked', 'next_action': next_action,
                                     'reason': (reason + ' ' + next_action + ' ' + detail).strip()[:2000]},
                 request_key='focus-block:' + _scoped(episode_id, key), actor=FOCUS_ACTOR)
        _receipt(memory, 'FocusBlocked', {'episode_id': episode_id, 'start_key': key, 'hypotheses': hypotheses})


def _review(memory, run_id):
    run = reviews.read(memory, run_id)
    try:
        follow = delegation.request_review(memory, run_id, request_key=run_id + ':work-review')
    except (InvalidRecord, Conflict) as exc:
        delegation._receipt(memory, run, 'DelegationFollowUpNotStarted',
                            {'run_id': run_id, 'state': run['state'], 'error': str(exc),
                             'retry': 'A missing work review starts when another agent run finishes or when a merge is requested.'},
                            run_id + ':follow-up-not-started')
        return
    delegation.launch(memory, follow)


def judge(memory, run_id):
    run = reviews.read(memory, run_id)
    attempt = run['snapshot'].get('focus')
    if run['role'] != 'work' or not attempt or run['state'] in reviews.ACTIVE:
        raise InvalidRecord('Only a finished focused attempt can be judged.')
    episode_id, key = run['episode_id'], attempt['start_key']
    earlier = _receipts(memory, episode_id, start_key=key, name='FocusCheckRecorded')
    prior = next((item for item in earlier if item['run_id'] == run_id), None)
    if prior:
        return {field: value for field, value in prior.items() if field != 'name'}
    result = _check(memory, run)
    with memory._write():
        earlier = _receipts(memory, episode_id, start_key=key, name='FocusCheckRecorded')
        prior = next((item for item in earlier if item['run_id'] == run_id), None)
        if prior:
            return {field: value for field, value in prior.items() if field != 'name'}
        entry = next(item for item in _hypotheses(memory, episode_id) if item['id'] == result['hypothesis_id'])
        memory.record(episode_id, 'hypothesis', {'statement': entry['statement'], 'approach': entry['approach'],
                      'state': 'confirmed' if result['check_passed'] else 'ruled_out', 'run_id': run_id,
                      'attempt': attempt['attempt'], 'evidence_summary': result['evidence_summary']},
                      expected_version=memory.episode(episode_id)['version'], request_key='focus-result:' + run_id,
                      actor=FOCUS_ACTOR, supersedes=entry['record_id'])
        _receipt(memory, 'FocusCheckRecorded', result)
    _advance(memory, run)
    return result


def _advance(memory, run):
    attempt = run['snapshot']['focus']
    episode_id, key = run['episode_id'], attempt['start_key']
    follow, selected = None, None
    with memory._write():
        receipts = _receipts(memory, episode_id, start_key=key)
        if any(item['name'] in ('FocusSelected', 'FocusBlocked', 'FocusCompleted') for item in receipts):
            return
        started = next(item for item in receipts if item['name'] == 'FocusStarted')
        results = [item for item in receipts if item['name'] == 'FocusCheckRecorded']
        if started['mode'] == 'parallel' and len(results) < started['attempts']:
            return
        best = rank(results)[0]
        if best['check_passed']:
            selected = best['run_id']
            _receipt(memory, 'FocusSelected', {'episode_id': episode_id, 'start_key': key, 'run_id': selected,
                     'attempt': best['attempt'], 'reason': 'The check passed; fewer changed lines and then the earlier attempt decide ties.'})
        else:
            for item in results:
                _discard(memory, item['run_id'], 'The check of the focused problem failed for this attempt.')
            number = max(item['attempt'] for item in results) + 1
            if any(reviews.read(memory, item['run_id'])['state'] == 'cancelled' for item in results):
                # A cancellation comes from the user, so no further attempt starts without the user.
                _block(memory, started, CANCELLED_NEXT_ACTION)
            elif started['mode'] == 'relay' and number <= started['attempts']:
                try:
                    follow = _request_attempt(memory, started, number, problem=attempt['problem'])
                    _requested(memory, follow)
                except (InvalidRecord, Conflict) as exc:
                    _block(memory, started, REQUEST_FAILED_NEXT_ACTION, str(exc))
            else:
                _block(memory, started)
    if selected:
        for item in results:
            if item['run_id'] != selected:
                _discard(memory, item['run_id'], 'Another attempt was selected by the check of the focused problem.')
        _review(memory, selected)
    elif follow:
        delegation.launch(memory, follow)


def after_review(memory, review_run_id):
    review = reviews.read(memory, review_run_id)
    if review['role'] != 'work_review' or review['state'] in reviews.ACTIVE:
        raise InvalidRecord('Only a finished work review can complete a focused problem.')
    run = reviews.read(memory, review['parent_run'])
    attempt = run['snapshot']['focus']
    episode_id, key = run['episode_id'], attempt['start_key']
    with memory._write():
        receipts = _receipts(memory, episode_id, start_key=key)
        completed = next((item for item in receipts if item['name'] in ('FocusCompleted', 'FocusBlocked')), None)
        if completed:
            return {field: value for field, value in completed.items() if field != 'name'}
        if not any(item['name'] == 'FocusSelected' and item['run_id'] == run['id'] for item in receipts):
            raise InvalidRecord('Only the selected focused attempt can complete its review.')
        merged = 'not_merged'
        if review['state'] == 'pass':
            merged = 'waiting_for_user'
            if planning.phase(memory)['phase'] != 'production':
                try:
                    with memory._write():
                        delegation.merge(memory, run['id'], request_key='focus-merge:' + run['id'], actor=FOCUS_ACTOR)
                except (InvalidRecord, Conflict) as exc:
                    # A merge conflict ends the start as blocked. The branch stays, so the user can merge or discard it.
                    _block(memory, {'episode_id': episode_id, 'start_key': key}, MERGE_FAILED_NEXT_ACTION, str(exc))
                    return {'episode_id': episode_id, 'start_key': key, 'run_id': run['id'], 'review_state': review['state'],
                            'merge': 'not_merged', 'error': str(exc)}
                merged = 'merged'
        result = {'episode_id': episode_id, 'start_key': key, 'run_id': run['id'], 'review_state': review['state'], 'merge': merged}
        _receipt(memory, 'FocusCompleted', result)
        if review['state'] != 'pass':
            _block(memory, {'episode_id': episode_id, 'start_key': key}, REVIEW_FAILED_NEXT_ACTION)
        else:
            summary(memory, episode_id)
    return result


def summary(memory, episode_id):
    with memory._write():
        receipts = _receipts(memory, episode_id)
        starts = [item for item in receipts if item['name'] == 'FocusStarted']
        key = starts[-1]['start_key'] if starts else None
        completed = next((item for item in receipts if item['name'] == 'FocusCompleted' and item['start_key'] == key), None)
        if not completed or completed['review_state'] != 'pass':
            return {'proposed': False, 'reason': 'No focused start has completed with a passing work review.'}
        run_id = completed['run_id']
        prior = memory.db.execute('SELECT id FROM events WHERE request_key=?', ('focus-lesson:' + run_id,)).fetchone()
        if prior:
            return {'proposed': True, 'lesson_id': prior[0], 'duplicate': True}
        checked = next(item for item in receipts if item['name'] == 'FocusCheckRecorded' and item['run_id'] == run_id)
        run = reviews.read(memory, run_id)
        attempt = run['snapshot']['focus']
        ruled = _ruled_out(memory, episode_id)
        because = f'The check passed for this approach in attempt {attempt["attempt"]}.'
        because += ' Ruled out: ' + '; '.join(item['statement'] for item in ruled) + '.' if ruled else ' No hypothesis was ruled out.'
        source = memory.source('focus-check:' + run_id, 'Focused problem check', checked['evidence_summary'],
                               dumps({field: value for field, value in checked.items() if field != 'name'}), 'tool',
                               subject=memory.episode(episode_id)['subject'])
        lesson = memory.record(episode_id, 'lesson', {'when': ('When the work meets this problem: ' + attempt['problem'])[:2000],
                               'do': attempt['hypothesis']['approach'], 'because': because[:12000],
                               'exceptions': 'None were observed in this focused problem.'},
                               expected_version=memory.episode(episode_id)['version'], actor=FOCUS_ACTOR,
                               request_key='focus-lesson:' + run_id,
                               evidence=[{'source_id': source['id'], 'reason': 'Project Memory ran this check on the selected attempt.'}])
    return {'proposed': True, 'lesson_id': lesson['id'], 'duplicate': lesson['duplicate']}


def _state(receipts):
    if any(item['name'] == 'FocusBlocked' for item in receipts):
        return 'blocked'
    completed = next((item for item in receipts if item['name'] == 'FocusCompleted'), None)
    if completed:
        return completed['merge'] if completed['review_state'] == 'pass' else 'blocked'
    return 'reviewing' if any(item['name'] == 'FocusSelected' for item in receipts) else 'running'


def view(memory, episode_id):
    memory.episode(episode_id)
    block = (planning.latest(memory, episode_id, 'work_plan') or {}).get('focus')
    receipts = _receipts(memory, episode_id)
    starts = [item for item in receipts if item['name'] == 'FocusStarted']
    selected, attempts = None, []
    state = 'none' if not block else 'awaiting_check' if not block.get('check') else 'ready'
    if starts:
        receipts = [item for item in receipts if item['start_key'] == starts[-1]['start_key']]
        state = _state(receipts)
        selected = next((item['run_id'] for item in receipts if item['name'] == 'FocusSelected'), None)
        for requested in (item for item in receipts if item['name'] == 'FocusAttemptRequested'):
            rerouted = next((item for item in receipts if item['name'] == 'FocusAttemptRerouted'
                             and item['attempt'] == requested['attempt']), None)
            run_id = rerouted['to_run'] if rerouted else requested['run_id']
            run = reviews.read(memory, run_id)
            checked = next((item for item in receipts if item['name'] == 'FocusCheckRecorded' and item['run_id'] == run_id), {})
            review = latest_review(memory, run_id)
            settled = settlement(memory, run_id)
            attempts.append({'attempt': requested['attempt'], 'run_id': run_id, 'host': run['host'],
                             'hypothesis_id': requested['hypothesis_id'], 'run_state': run['state'],
                             'rerouted_from': rerouted['from_run'] if rerouted else None,
                             **{field: checked.get(field) for field in ('check_passed', 'exit_code', 'duration_ms', 'changed_lines')},
                             'review_state': review['state'] if review else None,
                             'merge_state': ('merged' if settled['event_name'] == 'DelegationMerged' else 'discarded') if settled else None,
                             'selected': run_id == selected})
    why = reasons(memory, episode_id)
    return {'episode_id': episode_id, 'state': state, 'eligible': bool(why), 'reasons': why, 'focus': block,
            'hypotheses': [{key: value for key, value in item.items() if key != 'record_id'} for item in _hypotheses(memory, episode_id)],
            'attempts': sorted(attempts, key=lambda item: item['attempt']), 'selected_run': selected,
            'report': report(memory, episode_id=episode_id)}


def report(memory, *, episode_id=None):
    if episode_id is not None:
        memory.episode(episode_id)
    receipts = _receipts(memory, episode_id)
    totals = {'problems': 0, 'solved': 0, 'blocked': 0, 'running': 0, 'attempts': 0, 'hosts': {},
              'checks': {'passed': 0, 'failed': 0, 'not_run': 0}, 'tokens': 0, 'run_duration_ms': 0, 'check_duration_ms': 0,
              'reviews': {}, 'first_attempt_sufficient': 0, 'items': [], 'basis': REPORT_BASIS}
    for started in (item for item in receipts if item['name'] == 'FocusStarted'):
        entries = [item for item in receipts if item['episode_id'] == started['episode_id'] and item['start_key'] == started['start_key']]
        completed = next((item for item in entries if item['name'] == 'FocusCompleted'), None)
        blocked = any(item['name'] == 'FocusBlocked' for item in entries)
        totals['problems'] += 1
        totals['solved' if completed and completed['review_state'] == 'pass' else 'blocked' if blocked else 'running'] += 1
        checks = sorted((item for item in entries if item['name'] == 'FocusCheckRecorded'), key=lambda item: item['attempt'])
        sufficient = any(item['attempt'] == 1 and item['check_passed'] for item in checks)
        totals['first_attempt_sufficient'] += int(sufficient)
        item = {'episode_id': started['episode_id'], 'start_key': started['start_key'], 'state': _state(entries),
                'first_attempt_sufficient': sufficient, 'attempts': []}
        rerouted = {item['to_run']: item['from_run'] for item in entries if item['name'] == 'FocusAttemptRerouted'}
        for checked in checks:
            run = reviews.read(memory, checked['run_id'])
            # A rerouted attempt also cost the run on the unavailable host, so that run counts in tokens, duration and hosts.
            chain, current = [run], run['id']
            while current in rerouted:
                current = rerouted[current]
                chain.append(reviews.read(memory, current))
            tokens, duration = 0, 0
            for part in chain:
                metrics = part['metrics'] or {}
                usage = metrics.get('provider_usage') or {}
                usage = usage if isinstance(usage, list) else [usage]
                tokens += sum((turn.get('input_tokens') or 0) + (turn.get('output_tokens') or 0) for turn in usage)
                duration += metrics.get('duration_ms') or 0
                totals['hosts'][part['host']] = totals['hosts'].get(part['host'], 0) + 1
            review = latest_review(memory, run['id'])
            verdict = review['state'] if review else None
            totals['attempts'] += 1
            status = 'passed' if checked['check_passed'] else 'failed' if checked['exit_code'] is not None or checked['timed_out'] else 'not_run'
            totals['checks'][status] += 1
            totals['tokens'] += tokens
            totals['run_duration_ms'] += duration
            totals['check_duration_ms'] += checked['duration_ms']
            if verdict:
                totals['reviews'][verdict] = totals['reviews'].get(verdict, 0) + 1
            item['attempts'].append({field: checked[field] for field in ('attempt', 'run_id', 'host', 'check_passed', 'exit_code', 'changed_lines')}
                                   | {'check_duration_ms': checked['duration_ms'], 'run_duration_ms': duration,
                                      'tokens': tokens, 'review_state': verdict,
                                      'rerouted_from': [part['id'] for part in chain[1:]]})
        totals['items'].append(item)
    return totals
