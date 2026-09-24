"""Delegated work with fake host processes and real git in temporary repositories. No real host runs."""
import shutil
import base64
from datetime import datetime, timezone
import io
import json
import os
import zipfile
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from memory_module import Memory, InvalidRecord, Conflict, codex_host, delegation, hosts, install, reviews
from memory_module.planning import save

ROOT = Path(__file__).resolve().parents[1]

WORKER = r'''
import base64, json, pathlib, sys, time
spec = json.loads(sys.argv[1])
folder = pathlib.Path(sys.argv[2])
host = sys.argv[3]
if spec.get('cancel'):
    sys.path.insert(0, spec['root'])
    from memory_module import Memory, reviews
    print(json.dumps({'type': 'thread.started'}), flush=True)
    with Memory(spec['database']) as memory:
        reviews.cancel(memory, folder.name)
for name, text in spec.get('write', {}).items():
    path = pathlib.Path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
for name, data in spec.get('binary', {}).items():
    path = pathlib.Path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(base64.b64decode(data))
for old, new in spec.get('move', {}).items():
    pathlib.Path(new).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(old).rename(new)
if spec.get('unavailable'):
    if host == 'codex':
        print(json.dumps({'type': 'error', 'message': spec['unavailable']}), flush=True)
    else:
        print(json.dumps({'type': 'result', 'is_error': True, 'result': spec['unavailable']}), flush=True)
    sys.exit(1)
time.sleep(spec.get('sleep', 0))
report = spec.get('report')
if report is None and spec.get('review'):
    snapshot = json.loads((folder / 'input.json').read_text())
    verdict = spec.get('verdict', 'pass')
    result = 'met' if verdict == 'pass' else 'unmet'
    report = {'verdict': verdict, 'summary': 'The fixture reviewer inspected the diff.',
              'checks': [{'criterion': item['id'], 'evidence': 'The diff shows the change.', 'result': result}
                         for item in snapshot['checklist']],
              'findings': [], 'lesson_proposals': spec.get('lessons', [])}
    if 'constraints' in snapshot:
        report['constraint_checks'] = [{'constraint': item['id'], 'applicability': 'applies',
                                        'reason': 'The constraint applies to this change.',
                                        'evidence': 'The diff.', 'result': result} for item in snapshot['constraints']]
if report is not None:
    if host == 'codex':
        (folder / 'answer.json').write_text(json.dumps(report))
        print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 10}}), flush=True)
    else:
        print(json.dumps({'type': 'result', 'structured_output': report, 'usage': {'input_tokens': 10}}), flush=True)
'''

LESSON = {'proposal': 'Run the value checks before changing shared values.', 'basis': 'The change touched a shared value.',
          'conditions': 'When a value in the source folder changes.', 'exceptions': 'Changes that touch documentation only.'}


def pptx(text):
    """A minimal PowerPoint file with one slide that holds the text."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('[Content_Types].xml', '<Types/>')
        archive.writestr('ppt/slides/slide1.xml',
                         '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
                         'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:cSld><p:spTree><p:sp><p:txBody>'
                         '<a:p><a:r><a:t>' + text + '</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>')
    return base64.b64encode(buffer.getvalue()).decode()


def work_report(**changes):
    report = {'summary': 'The value is now two.', 'result': 'complete', 'changed_files': ['src/app.py'],
              'checks_run': [{'command': 'python3 -c "import app"', 'outcome': 'The import succeeded.'}],
              'notes': '', 'lesson_proposals': []}
    report.update(changes)
    return report


class DelegationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.base = Path(self.temp.name).resolve()
        self.project = self.base / 'project'
        self.project.mkdir()
        environment = patch.dict(os.environ, {'PROJECT_MEMORY_CODEX_BIN': sys.executable,
                                              'PROJECT_MEMORY_CLAUDE_BIN': sys.executable})
        environment.start()
        self.addCleanup(environment.stop)
        self.git('init', '-q')
        self.git('checkout', '-q', '-b', 'main')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.com')
        self.git('config', 'commit.gpgsign', 'false')
        (self.project / 'src').mkdir()
        (self.project / 'src/app.py').write_text('VALUE = 1\n')
        (self.project / 'docs').mkdir()
        (self.project / 'docs/notes.md').write_text('# Notes\n')
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'Initial')
        info = install.setup(self.project)
        self.m = Memory(info['database'])
        reviews.configure(self.m, self.project, 'codex')
        reviews.configure(self.m, self.project, 'claude')
        self.episode = self.plan('Update the value')
        self.workers = {}
        self.reviewers = {}
        for target, name, effect in ((delegation, 'work_command', self.fake_worker),
                                     (reviews, 'command', self.fake_reviewer),
                                     (delegation, 'launch', self.run_now)):
            patcher = patch.object(target, name, side_effect=effect)
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        self.m.close()
        shutil.rmtree(self.temp.name, ignore_errors=True)

    def git(self, *args, cwd=None):
        result = subprocess.run(['git', '-C', str(cwd or self.project), *args], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def plan(self, title, paths=('src/**',), autonomy='act', **extra):
        source = self.m.source('user:' + title, 'User request', 'The user asks for the change.',
                               'Change the value within the source folder.', 'user', subject='code')
        payload = {'state': 'ready', 'next_action': 'Change the value.', 'scope': 'Change the value in the source folder only.',
                   'autonomy': autonomy, 'reason': 'The user requests the change.', **extra}
        if paths is not None:
            payload['paths'] = list(paths)
        result = save(self.m, 'work_plan', payload=payload, actor='workspace-user',
                      evidence=[{'source_id': source['id'], 'reason': 'The user requests this change.'}],
                      title=title, objective='Set the value to two.', criterion='VALUE equals 2 in src/app.py.',
                      subject='code', request_key='plan:' + title)
        return result['episode_id']

    def fake_worker(self, host, worktree, folder, prompt):
        spec = self.workers.get(host, {'write': {'src/app.py': 'VALUE = 2\n'}, 'report': work_report()})
        return [sys.executable, '-c', WORKER, json.dumps(spec), str(folder), host]

    def fake_reviewer(self, host, project, folder, prompt):
        spec = {'review': True, **self.reviewers.get(host, {})}
        return [sys.executable, '-c', WORKER, json.dumps(spec), str(folder), host]

    def run_now(self, memory, run):
        delegation.execute(memory, run['id'])

    def delegate(self, key='delegate', episode=None, host='codex'):
        run = delegation.request_work(self.m, episode or self.episode, request_key=key, host=host)
        delegation.launch(self.m, run)
        return reviews.read(self.m, run['id'])

    def receipts(self, name):
        return self.m.db.execute('SELECT count(*) FROM host_receipts WHERE event_name=?', (name,)).fetchone()[0]

    def test_successful_delegation_is_reviewed_on_the_other_host_and_merged(self):
        queued = delegation.request_work(self.m, self.episode, request_key='delegate', host='codex')
        self.assertEqual((queued['state'], queued['host']), ('queued', 'codex'))
        self.assertTrue(queued['branch'].startswith('pm/'))
        self.assertEqual(queued['workspace'], '.memory/worktrees/' + queued['id'])
        snapshot = queued['snapshot']
        self.assertEqual(snapshot['base_commit'], self.git('rev-parse', 'HEAD'))
        self.assertEqual(snapshot['paths'], ['src/**'])
        self.assertEqual([item['id'] for item in snapshot['checklist']], ['C001'])
        self.assertEqual(delegation.request_work(self.m, self.episode, request_key='delegate')['id'], queued['id'])
        delegation.launch(self.m, queued)
        work = reviews.read(self.m, queued['id'])
        self.assertEqual(work['state'], 'completed', work['error'])
        self.assertEqual(work['metrics']['changed_files'], ['src/app.py'])
        commit = work['metrics']['commit']
        self.assertEqual(self.git('rev-parse', queued['branch']), commit)
        self.assertEqual(self.git('show', commit + ':src/app.py'), 'VALUE = 2')
        self.assertEqual((self.project / 'src/app.py').read_text(), 'VALUE = 1\n')
        source = self.m.read(work['metrics']['diff_source'], detail=True)
        self.assertEqual(source['source_key'], 'delegation:' + queued['id'])
        self.assertIn('+VALUE = 2', source['body'])
        listing = delegation.runs(self.m, episode_id=self.episode)['runs']
        review = next(run for run in listing if run['role'] == 'work_review')
        self.assertEqual((review['host'], review['state'], review['parent_run']), ('claude', 'pass', queued['id']))
        self.assertEqual(review['independence'], 'other_host')
        summary = next(run for run in listing if run['id'] == queued['id'])
        self.assertEqual((summary['review']['state'], summary['changed_files'], summary['merge']), ('pass', 1, None))
        self.assertGreaterEqual(self.receipts('HostAvailable'), 2)
        from memory_module import graph
        types = {edge['type'] for edge in graph.edges(self.m, [queued['id']])}
        self.assertTrue({'delegated_to', 'reviewed_by'} <= types, types)

        merged = delegation.merge(self.m, queued['id'], request_key='merge', actor='assistant')
        self.assertEqual((self.project / 'src/app.py').read_text(), 'VALUE = 2\n')
        self.assertEqual(merged['commit'], self.git('rev-parse', 'HEAD'))
        self.assertEqual(len(self.git('rev-list', '--parents', '-n', '1', 'HEAD').split()), 3)
        self.assertFalse((self.project / queued['workspace']).exists())
        self.assertEqual(self.git('branch', '--list', queued['branch']), '')
        again = delegation.merge(self.m, queued['id'], request_key='merge', actor='assistant')
        self.assertTrue(again['duplicate'])
        self.assertEqual(again['commit'], merged['commit'])
        self.assertEqual(self.receipts('DelegationMerged'), 1)
        with self.assertRaises(Conflict):
            delegation.merge(self.m, queued['id'], request_key='second-merge', actor='assistant')
        summary = next(run for run in delegation.runs(self.m)['runs'] if run['id'] == queued['id'])
        self.assertEqual(summary['merge']['state'], 'merged')

    def test_a_change_outside_the_plan_paths_is_a_scope_violation(self):
        self.workers['codex'] = {'write': {'src/app.py': 'VALUE = 2\n', 'docs/notes.md': 'Changed.\n'}, 'report': work_report()}
        run = self.delegate()
        self.assertEqual(run['state'], 'scope_violation')
        self.assertIn('docs/notes.md', run['error'])
        self.assertEqual(run['metrics']['outside_paths'], ['docs/notes.md'])
        self.assertFalse([r for r in delegation.runs(self.m)['runs'] if r['role'] == 'work_review'])
        with self.assertRaises(InvalidRecord):
            delegation.merge(self.m, run['id'], request_key='merge', actor='workspace-user', override_reason='Accept it.')
        result = delegation.discard(self.m, run['id'], request_key='discard', actor='workspace-user', reason='The change is out of scope.')
        self.assertTrue(result['cleanup']['worktree_removed'])
        self.assertTrue(result['cleanup']['branch_deleted'])
        self.assertFalse((self.project / run['workspace']).exists())
        self.assertEqual(self.git('branch', '--list', run['branch']), '')
        self.assertTrue(delegation.discard(self.m, run['id'], request_key='discard', actor='workspace-user', reason='The change is out of scope.')['duplicate'])
        self.assertEqual(delegation.runs(self.m)['runs'][0]['merge']['state'], 'discarded')
        with self.assertRaises(Conflict):
            delegation.discard(self.m, run['id'], request_key='discard-again', actor='workspace-user', reason='Again.')

    def test_uncommitted_plan_paths_and_unsuitable_plans_are_rejected(self):
        (self.project / 'src/app.py').write_text('VALUE = 3\n')
        with self.assertRaisesRegex(InvalidRecord, 'src/app.py'):
            delegation.request_work(self.m, self.episode, request_key='dirty')
        self.git('checkout', '--', 'src/app.py')
        (self.project / 'src/new.py').write_text('NEW = True\n')
        with self.assertRaisesRegex(InvalidRecord, 'src/new.py'):
            delegation.request_work(self.m, self.episode, request_key='untracked')
        (self.project / 'src/new.py').unlink()
        (self.project / 'docs/notes.md').write_text('An unrelated draft.\n')
        self.assertEqual(delegation.request_work(self.m, self.episode, request_key='clean')['state'], 'queued')
        suggest = self.plan('Suggest only', autonomy='suggest')
        with self.assertRaisesRegex(InvalidRecord, 'autonomy act'):
            delegation.request_work(self.m, suggest, request_key='suggest')
        unbounded = self.plan('No paths', paths=None)
        with self.assertRaisesRegex(InvalidRecord, 'plan paths'):
            delegation.request_work(self.m, unbounded, request_key='no-paths')
        with self.assertRaises(InvalidRecord):
            delegation.request_work(self.m, self.episode, request_key='bad-host', host='gemini')
        self.assertEqual(self.m.db.execute("SELECT count(*) FROM review_runs WHERE role='work'").fetchone()[0], 1)

    def test_a_failed_run_without_changes_removes_its_worktree(self):
        """A host that refuses to start leaves nothing to inspect, so nothing is left behind.

        A real Codex run failed this way: an invalid configuration override stopped it before
        any work began, and its worktree and branch stayed until someone removed them by hand.
        """
        self.workers['codex'] = {'write': {}}
        run = self.delegate()
        self.assertEqual(run['state'], 'failed')
        self.assertEqual(run['metrics']['changed_files'], [])
        self.assertFalse((self.project / run['workspace']).exists())
        self.assertEqual(self.git('branch', '--list', run['branch']), '')
        self.assertEqual(self.receipts('DelegationCleanedUp'), 1)

    def test_host_unavailability_reroutes_once_to_the_other_host(self):
        self.workers['codex'] = {'write': {'src/app.py': 'VALUE = 5\n'},
                                 'unavailable': 'You have hit your usage limit. Try again in 20 minutes.'}
        first = self.delegate()
        self.assertEqual(first['state'], 'host_unavailable')
        self.assertEqual(first['metrics']['host_unavailable']['reason'], 'usage_limit')
        self.assertFalse((self.project / first['workspace']).exists())
        self.assertEqual(self.git('branch', '--list', first['branch']), '')
        availability = hosts.availability(self.m, 'codex')
        self.assertFalse(availability['available'])
        self.assertIsNotNone(availability['until'])
        children = [r for r in delegation.runs(self.m)['runs'] if r['role'] == 'work' and r['parent_run'] == first['id']]
        self.assertEqual(len(children), 1)
        second = reviews.read(self.m, children[0]['id'])
        self.assertEqual((second['host'], second['state']), ('claude', 'completed'))
        self.assertEqual(second['snapshot']['rerouted_from'], first['id'])
        self.assertEqual(self.receipts('DelegationRerouted'), 1)
        review = delegation.latest_review(self.m, second['id'])
        self.assertEqual((review['host'], review['state'], review['snapshot']['independence']), ('claude', 'pass', 'same_host'))

    def test_a_rerouted_run_is_not_rerouted_again(self):
        self.workers['codex'] = {'unavailable': 'Rate limit reached. Try again in 30 seconds.'}
        self.workers['claude'] = {'unavailable': 'Claude AI usage limit reached|1999999999'}
        first = self.delegate()
        work = [r for r in delegation.runs(self.m)['runs'] if r['role'] == 'work']
        self.assertEqual(len(work), 2)
        self.assertEqual({r['state'] for r in work}, {'host_unavailable'})
        self.assertEqual(work[0]['parent_run'], first['id'])
        self.assertFalse(hosts.availability(self.m, 'claude')['available'])

    def test_both_hosts_unavailable_raise_before_any_run(self):
        until = '2999-01-01T00:00:00+00:00'
        hosts.mark_unavailable(self.m, 'codex', 'usage_limit', until)
        hosts.mark_unavailable(self.m, 'claude', 'authentication', until)
        with self.assertRaisesRegex(InvalidRecord, 'No configured agent host is available'):
            delegation.request_work(self.m, self.episode, request_key='blocked')
        self.assertEqual(self.m.db.execute("SELECT count(*) FROM review_runs WHERE role='work'").fetchone()[0], 0)

    def test_merge_without_a_passing_review_requires_a_user_override(self):
        self.reviewers['claude'] = {'verdict': 'changes_required'}
        run = self.delegate()
        self.assertEqual(delegation.latest_review(self.m, run['id'])['state'], 'changes_required')
        with self.assertRaises(InvalidRecord):
            delegation.merge(self.m, run['id'], request_key='agent', actor='assistant')
        with self.assertRaises(InvalidRecord):
            delegation.merge(self.m, run['id'], request_key='agent-override', actor='assistant', override_reason='The agent insists.')
        with self.assertRaises(InvalidRecord):
            delegation.merge(self.m, run['id'], request_key='user-no-reason', actor='workspace-user')
        self.assertEqual((self.project / 'src/app.py').read_text(), 'VALUE = 1\n')
        merged = delegation.merge(self.m, run['id'], request_key='user', actor='workspace-user',
                                  override_reason='The user inspected the diff and accepts it.')
        self.assertEqual((merged['review_state'], merged['override_reason']), ('changes_required', 'The user inspected the diff and accepts it.'))
        receipt = self.m.db.execute("SELECT payload FROM host_receipts WHERE event_name='DelegationMerged'").fetchone()[0]
        self.assertEqual(json.loads(receipt)['override_reason'], 'The user inspected the diff and accepts it.')
        self.assertEqual((self.project / 'src/app.py').read_text(), 'VALUE = 2\n')

    def test_a_merge_conflict_aborts_and_leaves_the_project_unchanged(self):
        run = self.delegate()
        (self.project / 'src/app.py').write_text('VALUE = 9\n')
        self.git('commit', '-q', '-am', 'A conflicting change')
        head = self.git('rev-parse', 'HEAD')
        with self.assertRaisesRegex(Conflict, 'aborted'):
            delegation.merge(self.m, run['id'], request_key='merge', actor='assistant')
        self.assertEqual(self.git('rev-parse', 'HEAD'), head)
        self.assertEqual(self.git('status', '--porcelain'), '')
        self.assertFalse((self.project / '.git/MERGE_HEAD').exists())
        self.assertEqual((self.project / 'src/app.py').read_text(), 'VALUE = 9\n')
        self.assertEqual(self.receipts('DelegationMerged'), 0)
        self.assertTrue((self.project / run['workspace']).exists())
        self.assertEqual(self.git('branch', '--list', run['branch']).lstrip('*+ '), run['branch'])

    def test_deadline_and_cancellation_terminate_the_worker(self):
        self.workers['codex'] = {'write': {'src/app.py': 'VALUE = 2\n'}, 'sleep': 30, 'report': work_report()}
        run = delegation.request_work(self.m, self.episode, request_key='deadline', host='codex')
        started = time.monotonic()
        delegation.execute(self.m, run['id'], timeout=1)
        self.assertLess(time.monotonic() - started, 10)
        result = reviews.read(self.m, run['id'])
        self.assertEqual(result['state'], 'timed_out')
        self.assertEqual(result['metrics']['termination_reason'], 'execution_deadline')
        self.assertEqual(result['metrics']['changed_files'], ['src/app.py'])
        self.assertIsNone(delegation.latest_review(self.m, run['id']))

        self.workers['codex'] = {'cancel': True, 'root': str(ROOT), 'database': str(self.m.path), 'sleep': 30}
        run = delegation.request_work(self.m, self.episode, request_key='cancel', host='codex')
        started = time.monotonic()
        delegation.execute(self.m, run['id'])
        self.assertLess(time.monotonic() - started, 10)
        result = reviews.read(self.m, run['id'])
        self.assertEqual(result['state'], 'cancelled')
        self.assertEqual(result['metrics']['termination_reason'], 'cancelled')
        self.assertEqual(self.receipts('DelegationFinished'), 2)

    def test_lesson_proposals_become_proposed_lessons_exactly_once(self):
        self.workers['codex'] = {'write': {'src/app.py': 'VALUE = 2\n'}, 'report': work_report(lesson_proposals=[LESSON])}
        self.reviewers['claude'] = {'lessons': [{**LESSON, 'proposal': 'Compare the diff with the plan paths first.'}]}
        run = self.delegate()
        rows = self.m.db.execute("SELECT id,actor FROM events WHERE kind='lesson' ORDER BY rowid").fetchall()
        self.assertEqual([row['actor'] for row in rows], ['codex-reviewer', 'claude-reviewer'])
        lesson = self.m.read(rows[0]['id'])
        self.assertEqual((lesson['status'], lesson['payload']['do']), ('proposed', LESSON['proposal']))
        source = self.m.read(lesson['evidence'][0]['source_id'])
        self.assertEqual(source['source_key'], 'run-report:' + run['id'])
        self.assertEqual(reviews.record_lesson_proposals(self.m, reviews.read(self.m, run['id'])), [rows[0]['id']])
        self.assertEqual(self.m.db.execute("SELECT count(*) FROM events WHERE kind='lesson'").fetchone()[0], 2)

    def test_outcome_check_lesson_proposals_link_to_the_checked_outcome(self):
        outcome = self.complete(self.episode)
        self.reviewers['claude'] = {'lessons': [LESSON]}
        run = reviews.request(self.m, self.episode, 'outcome', request_key='outcome-check')
        reviews.execute(self.m, run['id'], timeout=10)
        self.assertEqual(reviews.read(self.m, run['id'])['state'], 'pass')
        lesson = self.m.read(self.m.db.execute("SELECT id FROM events WHERE kind='lesson'").fetchone()[0])
        self.assertEqual(lesson['links'][0]['event_id'], outcome)
        reviews.propose_lessons(self.m, run['id'])
        self.assertEqual(self.m.db.execute("SELECT count(*) FROM events WHERE kind='lesson'").fetchone()[0], 1)

    def test_concurrency_limits_per_work_item_family_and_project(self):
        second = self.plan('Second item', paths=('docs/**',))
        first = delegation.request_work(self.m, self.episode, request_key='first')
        with self.assertRaisesRegex(Conflict, 'Delegated work is already running'):
            delegation.request_work(self.m, self.episode, request_key='again')
        check = reviews.request(self.m, self.episode, 'intent', request_key='intent')
        self.assertEqual(check['state'], 'queued')
        with self.assertRaisesRegex(Conflict, 'Two agent runs'):
            delegation.request_work(self.m, second, request_key='second')
        reviews.cancel(self.m, first['id'])
        self.assertEqual(delegation.request_work(self.m, second, request_key='second')['state'], 'queued')

    def test_reviews_prefer_the_host_that_did_not_implement_the_work(self):
        def request(key):
            run = reviews.request(self.m, self.episode, 'intent', request_key=key, retry=True)
            reviews.cancel(self.m, run['id'])
            return run['host'], run['snapshot']['independence']
        self.assertEqual(request('codex-default'), ('claude', 'other_host'))
        with self.m._write():
            codex_host.receipt(self.m, session_id='claude-session', event_name='DecisionBound', episode_id=self.episode,
                               payload={'host': 'claude', 'reason': 'Fixture binding.'}, key='fixture-bind')
        self.assertEqual(reviews.implementer_host(self.m, self.episode), 'claude')
        self.assertEqual(request('claude-bound'), ('codex', 'other_host'))
        hosts.mark_unavailable(self.m, 'codex', 'usage_limit')
        self.assertEqual(request('codex-unavailable'), ('claude', 'same_host'))
        hosts.mark_available(self.m, 'codex')
        reviews.remove_host(self.m, 'claude')
        self.assertEqual((reviews.configured(self.m)['host'], reviews.configured(self.m)['hosts']), ('codex', ['codex']))
        self.assertEqual(request('claude-implemented-single-host'), ('codex', 'other_host'))
        with self.m._write():
            codex_host.receipt(self.m, session_id='codex-session', event_name='DecisionBound', episode_id=self.episode,
                               payload={'reason': 'Codex receipts omit the host.'}, key='fixture-bind-codex')
        self.assertEqual(request('single-host'), ('codex', 'same_host'))

    def test_done_requests_a_missing_outcome_check_once(self):
        self.complete(self.episode)
        with patch.object(reviews, 'launch') as launched:
            result = reviews.request_for_done(self.m, self.episode)
            launched.assert_called_once()
            self.assertTrue(result['requested'])
            self.assertEqual((result['role'], result['state']), ('outcome', 'queued'))
            again = reviews.request_for_done(self.m, self.episode)
            launched.assert_called_once()
        self.assertFalse(again['requested'])
        self.assertEqual(again['next_step']['action'], 'wait_review')

    # Regression tests for the wave 2 review findings.

    def test_a_work_review_that_could_not_start_is_started_later(self):
        blocked = InvalidRecord('No configured agent host is available.')
        with patch.object(reviews, 'review_host', side_effect=blocked):
            run = self.delegate()
        self.assertEqual(run['state'], 'completed')
        self.assertIsNone(delegation.latest_review(self.m, run['id']))
        self.assertEqual(self.receipts('DelegationFollowUpNotStarted'), 1)
        started = delegation.start_missing_reviews(self.m)
        self.assertEqual(len(started), 1)
        self.assertEqual(delegation.latest_review(self.m, run['id'])['state'], 'pass')
        self.assertEqual(delegation.start_missing_reviews(self.m), [])
        merged = delegation.merge(self.m, run['id'], request_key='merge', actor='assistant')
        self.assertEqual(merged['review_state'], 'pass')

    def test_a_merge_request_restarts_a_review_that_did_not_assess_the_work(self):
        self.reviewers['claude'] = {'unavailable': 'Rate limit reached. Try again in 30 seconds.'}
        run = self.delegate()
        self.assertEqual(delegation.latest_review(self.m, run['id'])['state'], 'host_unavailable')
        self.reviewers.pop('claude')
        with self.assertRaisesRegex(InvalidRecord, 'started the work review'):
            delegation.merge(self.m, run['id'], request_key='first', actor='assistant')
        review = delegation.latest_review(self.m, run['id'])
        self.assertEqual((review['state'], review['host']), ('pass', 'codex'))
        self.assertEqual(delegation.merge(self.m, run['id'], request_key='second', actor='assistant')['review_state'], 'pass')
        self.assertEqual((self.project / 'src/app.py').read_text(), 'VALUE = 2\n')

    def test_a_discard_is_not_recorded_while_the_worktree_remains(self):
        self.workers['codex'] = {'write': {'src/app.py': 'VALUE = 2\n', 'docs/notes.md': 'Changed.\n'}, 'report': work_report()}
        run = self.delegate()
        workspace = self.project / run['workspace']
        self.git('worktree', 'lock', str(workspace))
        with self.assertRaisesRegex(Conflict, 'not recorded'):
            delegation.discard(self.m, run['id'], request_key='locked', actor='workspace-user', reason='Out of scope.')
        self.assertEqual(self.receipts('DelegationDiscarded'), 0)
        self.assertTrue(workspace.exists())
        self.git('worktree', 'unlock', str(workspace))
        result = delegation.discard(self.m, run['id'], request_key='unlocked', actor='workspace-user', reason='Out of scope.')
        self.assertTrue(result['discarded'])
        self.assertFalse(workspace.exists())
        self.assertEqual(self.git('branch', '--list', run['branch']), '')

    def test_a_merge_with_incomplete_cleanup_can_be_cleaned_up_by_discard(self):
        run = self.delegate()
        workspace = self.project / run['workspace']
        self.git('worktree', 'lock', str(workspace))
        merged = delegation.merge(self.m, run['id'], request_key='merge', actor='assistant')
        self.assertEqual((self.project / 'src/app.py').read_text(), 'VALUE = 2\n')
        self.assertFalse(merged['cleanup']['worktree_removed'])
        self.assertEqual(merged['next_step']['action'], 'discard')
        self.assertEqual(self.receipts('DelegationCleanupIncomplete'), 1)
        self.git('worktree', 'unlock', str(workspace))
        cleaned = delegation.discard(self.m, run['id'], request_key='cleanup', actor='workspace-user', reason='Remove the leftover worktree.')
        self.assertEqual((cleaned['discarded'], cleaned['settled_as']), (False, 'merged'))
        self.assertFalse(workspace.exists())
        self.assertEqual(self.git('branch', '--list', run['branch']), '')
        with self.assertRaises(Conflict):
            delegation.discard(self.m, run['id'], request_key='again', actor='workspace-user', reason='Again.')

    def test_an_interrupt_cancels_agent_checks_but_not_delegated_work(self):
        work = delegation.request_work(self.m, self.episode, request_key='work', session_id='sess')
        check = reviews.request(self.m, self.episode, 'intent', request_key='intent', session_id='sess')
        reviews.hook(self.m, {'hook_event_name': 'Interrupt', 'session_id': 'sess'}, 'codex')
        self.assertEqual(reviews.read(self.m, check['id'])['state'], 'cancelled')
        self.assertEqual(reviews.read(self.m, work['id'])['state'], 'queued')

    def test_tool_caches_are_ignored_and_renames_out_of_scope_are_violations(self):
        self.workers['codex'] = {'write': {'src/app.py': 'VALUE = 2\n', '.pytest_cache/v/cache/lastfailed': '{}',
                                           'src/__pycache__/app.cpython-314.pyc': 'cache'}, 'report': work_report()}
        run = self.delegate()
        self.assertEqual(run['state'], 'completed', run['error'])
        self.assertEqual(run['metrics']['changed_files'], ['src/app.py'])
        moved = self.plan('Move notes')
        self.workers['codex'] = {'move': {'docs/notes.md': 'src/notes.md'}, 'report': work_report(changed_files=['src/notes.md'])}
        run = self.delegate(key='move', episode=moved)
        self.assertEqual(run['state'], 'scope_violation')
        self.assertEqual(run['metrics']['outside_paths'], ['docs/notes.md'])
        self.assertIn('docs/notes.md', run['metrics']['changed_files'])

    def test_a_run_that_changes_nothing_is_cleaned_up(self):
        self.workers['codex'] = {'report': work_report(changed_files=[])}
        run = self.delegate()
        self.assertEqual(run['state'], 'completed')
        self.assertFalse((self.project / run['workspace']).exists())
        self.assertEqual(self.receipts('DelegationCleanedUp'), 1)
        with self.assertRaisesRegex(InvalidRecord, 'nothing to merge'):
            delegation.merge(self.m, run['id'], request_key='merge', actor='assistant')

    def test_heartbeat_keeps_a_run_active_during_git_steps(self):
        run = delegation.request_work(self.m, self.episode, request_key='beat')
        with self.m._write():
            self.m.db.execute("UPDATE review_runs SET state='running',updated_at='2000-01-01T00:00:00+00:00' WHERE id=?", (run['id'],))
        self.assertEqual(reviews.read(self.m, run['id'])['state'], 'interrupted')
        with delegation.heartbeat(self.m, run['id'], seconds=0.1):
            time.sleep(0.6)
        self.assertEqual(reviews.read(self.m, run['id'])['state'], 'running')

    def test_the_snapshot_carries_acceptance_sources_and_uncommitted_files(self):
        findings = self.project / 'docs/findings.md'
        findings.write_text('# Findings\n\nThe old finding.\n')
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'Findings')
        findings.write_text('# Findings\n\nPrices rose by 4 percent in 2025.\n')
        self.m.document(str(findings))
        acceptance = ['The report names three pricing options.', 'Each option cites a finding.']
        episode = self.plan('Pricing report', acceptance=acceptance, item_type='deliverable')
        snapshot = delegation.request_work(self.m, episode, request_key='report')['snapshot']
        self.assertEqual((snapshot['item_type'], snapshot['acceptance']), ('deliverable', acceptance))
        self.assertEqual([item['condition'] for item in snapshot['checklist']], ['VALUE equals 2 in src/app.py.', *acceptance])
        self.assertIn('docs/findings.md', snapshot['uncommitted_outside_paths'])
        self.assertEqual(snapshot['captured_uncommitted_paths'], ['docs/findings.md'])
        self.assertTrue(any('Prices rose by 4 percent' in source['body'] for source in snapshot['sources']))
        prompt = delegation.worker_prompt(snapshot, datetime.now(timezone.utc), 600)
        self.assertIn('docs/findings.md', prompt)
        self.assertIn('no network access', prompt)
        intent = reviews.snapshot(self.m, episode, 'intent')[0]
        self.assertEqual([item['condition'] for item in intent['checklist']][1:], acceptance)
        research = self.plan('Competitor price survey', paths=('docs/**',), item_type='research')
        reviews.cancel(self.m, self.m.db.execute("SELECT id FROM review_runs WHERE request_key='report'").fetchone()[0])
        (self.project / 'docs/findings.md').write_text('# Findings\n\nThe old finding.\n')
        limits = delegation.request_work(self.m, research, request_key='survey')['snapshot']['limitations']
        self.assertEqual(len(limits), 2)
        self.assertIn('research', limits[1])

    def test_office_files_are_previewed_and_secret_values_are_removed(self):
        export = {'name': 'Lead intake', 'connections': {}, 'pinData': {'Webhook': [{'json': {'email': 'anna@example.com'}}]},
                  'nodes': [{'name': 'Call CRM', 'type': 'n8n-nodes-base.httpRequest', 'parameters': {'headerParameters': {
                      'parameters': [{'name': 'Authorization', 'value': 'Bearer sk_live_FAKE_INLINE_TOKEN_123'}]}}}]}
        self.workers['codex'] = {'write': {'src/workflow.json': json.dumps(export, indent=2)},
                                 'binary': {'src/deck.pptx': pptx('Three pricing options for the client.')},
                                 'report': work_report(changed_files=['src/deck.pptx', 'src/workflow.json'])}
        run = self.delegate()
        self.assertEqual(run['state'], 'completed', run['error'])
        metrics = run['metrics']
        self.assertEqual(metrics['binary_files'], [{'path': 'src/deck.pptx', 'preview': 'extracted'}])
        self.assertGreaterEqual(metrics['redactions'], 1)
        self.assertIn('src/workflow.json', metrics['data_warnings'][0])
        body = self.m.read(metrics['diff_source'], detail=True)['body']
        self.assertIn('Three pricing options for the client.', body)
        self.assertNotIn('sk_live_FAKE_INLINE_TOKEN_123', body)
        review = delegation.latest_review(self.m, run['id'])
        self.assertIn('Three pricing options', review['snapshot']['binary_files'][0]['text'])
        self.assertNotIn('sk_live_FAKE_INLINE_TOKEN_123', review['snapshot']['diff'])
        self.assertTrue(review['snapshot']['data_warnings'])

    def complete(self, episode):
        source = self.m.source('result:' + episode, 'Observed result', 'The value is two.', 'VALUE equals 2.', 'tool', subject='code')
        evidence = [{'source_id': source['id'], 'reason': 'The fixture records the observed value.'}]
        decision = self.m.record(episode, 'decision', {'decision': 'Set the value to two.', 'why': 'The user requests it.',
                                                       'expected': 'VALUE equals 2.', 'reconsider_when': 'The request changes.'},
                                 expected_version=self.m.episode(episode)['version'], request_key='decision:' + episode,
                                 actor='test', evidence=evidence)
        action = self.m.record(episode, 'action', {'action': 'Edit the value.'}, expected_version=decision['version'],
                               request_key='action:' + episode, actor='test', decision_id=decision['id'])
        outcome = self.m.record(episode, 'outcome', {'assessment': 'good', 'observed': 'VALUE equals 2.',
                                                     'assessment_reason': 'The file shows the value.', 'severity': 'none',
                                                     'attribution': 'The edit.', 'completion': 'complete'},
                                expected_version=action['version'], request_key='outcome:' + episode, actor='test',
                                decision_id=decision['id'], evidence=evidence)
        return outcome['id']

    # The instructions a run receives.

    def rule(self, *, do='Keep the recorded exception.', **triggers):
        """Record a lesson in its own work item and accept it, so that it becomes a rule in force."""
        if not hasattr(self, 'learning'):
            self.learning = self.m.start('Lessons', 'Collect delegation lessons.', 'learning',
                                         'Lessons are reviewed.', subject='code')['id']
            self.lesson_count = 0
            source = self.m.source('user-lessons', 'Lessons', 'User instruction', 'Keep the exception.', 'user', subject='code')
            self.lesson_evidence = [{'source_id': source['id'], 'reason': 'The user asks for the lesson.'}]
        self.lesson_count += 1
        key = 'lesson-' + str(self.lesson_count)
        payload = {'when': 'Changing the value.', 'do': do, 'because': 'An earlier change lost the exception.',
                   'exceptions': 'Documentation changes.', **triggers}
        lesson = self.m.record(self.learning, 'lesson', payload, expected_version=self.m.episode(self.learning)['version'],
                               request_key=key, actor='assistant', evidence=self.lesson_evidence)['id']
        self.m.record(self.learning, 'lesson_review', {'lesson_id': lesson, 'status': 'accepted',
                                                       'reason': 'The user reviewed the lesson.'},
                      expected_version=self.m.episode(self.learning)['version'], request_key=key + ':review',
                      actor='workspace-user', evidence=self.lesson_evidence,
                      links=[{'event_id': lesson, 'reason': 'This review assesses the lesson.'}])
        return lesson

    def test_work_and_its_review_compose_and_keep_the_instructions_they_received(self):
        from memory_module import guards
        worker = self.rule(roles=['worker'], paths=['src/**'], do='Run the value checks first.')
        reviewer = self.rule(roles=['reviewer'], do='Read the recorded scope before the diff.')
        work = self.delegate()
        self.assertEqual(work['state'], 'completed', work['error'])
        metrics = work['metrics']
        self.assertEqual((metrics['instruction_role'], metrics['instruction_source']), ('worker', 'instructions:worker'))
        self.assertEqual(metrics['rule_ids'], [worker])
        self.assertEqual(metrics['rules_omitted'], [])
        stored = self.m.read(metrics['instruction_source_id'], detail=True)
        self.assertEqual(stored['source_key'], 'instructions:worker')
        self.assertIn('Do Run the value checks first.', stored['body'])
        self.assertNotIn('Read the recorded scope before the diff.', stored['body'])
        self.assertTrue(stored['body'].startswith(guards.shipped_base('worker')))
        prompt = (self.m.path.parent / 'agent-runs' / work['id'] / 'prompt.txt').read_text()
        self.assertIn('Do Run the value checks first.', prompt)
        review = next(run for run in delegation.runs(self.m, episode_id=self.episode)['runs'] if run['role'] == 'work_review')
        review = reviews.read(self.m, review['id'])
        self.assertEqual(review['metrics']['instruction_source'], 'instructions:reviewer')
        self.assertEqual(review['metrics']['rule_ids'], [reviewer])
        self.assertIn('Do Read the recorded scope before the diff.',
                      self.m.read(review['metrics']['instruction_source_id'], detail=True)['body'])
        # The recorded rule identifiers let the effectiveness counts attribute a verdict to the rule.
        effect = {item['lesson_id']: item for item in guards.effectiveness(self.m)}
        self.assertEqual((effect[reviewer]['runs'], effect[reviewer]['verdicts']['pass']), (1, 1))
        # The delegated work reports a result, so the verdict of the cross review that judged it
        # counts for the rules the worker received.
        self.assertEqual(effect[worker]['verdicts'], {'pass': 1, 'changes_required': 0, 'uncertain': 0, 'pending': 0})
        self.assertEqual(effect[reviewer]['state'], 'unproven')
        self.assertIn('small', effect[reviewer]['note'])

    def test_a_rule_reaches_the_worker_once_and_only_rules_of_its_role_travel_with_the_work(self):
        worker = self.rule(roles=['worker'], paths=['src/**'], do='Run the value checks first.')
        self.rule(roles=['reviewer'], paths=['src/**'], do='Read the recorded scope before the diff.')
        self.rule(roles=['assistant'], paths=['src/**'], do='State the recorded scope before editing.')
        queued = delegation.request_work(self.m, self.episode, request_key='delegate', host='codex')
        # The snapshot carries the rules of the worker only, so the other roles cost the worker nothing.
        self.assertEqual([guard['lesson_id'] for guard in queued['snapshot']['guards']], [worker])
        delegation.launch(self.m, queued)
        work = reviews.read(self.m, queued['id'])
        self.assertEqual(work['state'], 'completed', work['error'])
        folder = self.m.path.parent / 'agent-runs' / work['id']
        packet = (folder / 'prompt.txt').read_text()
        self.assertEqual(packet.count('Run the value checks first.'), 1)
        self.assertNotIn('Read the recorded scope before the diff.', packet)
        given = json.loads((folder / 'input.json').read_text())
        self.assertEqual((given['guards'], given['rules_in_instructions']), ([], [worker]))
        # The record of the run keeps every guard that matched the work.
        self.assertEqual([guard['lesson_id'] for guard in work['snapshot']['guards']], [worker])

    def test_the_review_does_not_compose_a_rule_it_already_carries_as_a_constraint(self):
        both = self.rule(roles=['worker', 'reviewer'], paths=['src/**'], do='Run the value checks first.')
        reviewer = self.rule(roles=['reviewer'], do='Read the recorded scope before the diff.')
        work = self.delegate()
        self.assertEqual(work['state'], 'completed', work['error'])
        found = next(run for run in delegation.runs(self.m, episode_id=self.episode)['runs'] if run['role'] == 'work_review')
        review = reviews.read(self.m, found['id'])
        metrics = review['metrics']
        self.assertEqual(metrics['rules_in_constraints'], [both])
        self.assertEqual(metrics['rule_ids'], [reviewer])
        self.assertEqual([entry['lesson_id'] for entry in metrics['rules_omitted']], [both])
        self.assertIn('already carries this rule among its constraints', metrics['rules_omitted'][0]['reason'])
        composed = self.m.read(metrics['instruction_source_id'], detail=True)['body']
        self.assertNotIn('Run the value checks first.', composed)
        self.assertIn('Read the recorded scope before the diff.', composed)
        constraints = [item['condition'] for item in review['snapshot']['constraints'] if item['id'].startswith('G')]
        self.assertEqual(len(constraints), 1)
        self.assertIn('Run the value checks first.', constraints[0])

    def test_rules_that_do_not_fit_the_worker_budget_are_reported_in_the_metrics(self):
        from memory_module import guards
        rules = [self.rule(roles=['worker'], paths=['src/**'],
                           do=f'Run value check {index}.' + ' Keep every recorded exception.' * 4)
                 for index in range(10)]
        work = self.delegate()
        metrics = work['metrics']
        self.assertEqual(work['state'], 'completed', work['error'])
        self.assertTrue(metrics['rule_ids'])
        self.assertLess(len(metrics['rule_ids']), len(rules))
        self.assertEqual(sorted(metrics['rule_ids'] + [item['lesson_id'] for item in metrics['rules_omitted']]), sorted(rules))
        self.assertTrue(all(item['reason'] for item in metrics['rules_omitted']))
        self.assertEqual((metrics['rules_matched'], metrics['rules_accepted']), (len(rules), len(rules)))
        body = self.m.read(metrics['instruction_source_id'], detail=True)['body']
        self.assertLessEqual(len(body) - len(guards.shipped_base('worker')) - len(guards.RULES_HEADING) - 2,
                             guards.ROLE_BUDGETS['worker'])

    def test_in_production_the_work_runs_and_only_the_user_merges_it(self):
        """Delegated work and its cross review still run in production; the merge waits for the user."""
        from memory_module import mcp, planning
        planning.set_phase(self.m, phase='production', reason='The product serves customers, so the user merges changes.',
                           actor='workspace-user')
        run = self.delegate()
        self.assertEqual(run['state'], 'completed', run['error'])
        summary = next(item for item in delegation.runs(self.m)['runs'] if item['id'] == run['id'])
        self.assertEqual(summary['review']['state'], 'pass')
        with self.assertRaisesRegex(InvalidRecord, 'prepared and waiting') as refused:
            delegation.merge(self.m, run['id'], request_key='merge', actor='assistant')
        details = refused.exception.details
        self.assertEqual((details['phase'], details['merge_actor']), ('production', 'workspace-user'))
        self.assertEqual(details['next_step']['action'], 'ask_user')
        self.assertIn('asks for the merge in the chat', str(refused.exception))
        with self.assertRaises(InvalidRecord) as over_mcp:
            mcp.write(self.m, 'merge', 'merge-over-mcp', {'run_id': run['id'], 'actor': 'assistant'})
        self.assertEqual(str(over_mcp.exception), delegation.PRODUCTION_MERGE_REFUSED)
        self.assertEqual(over_mcp.exception.details['execution'], 'not_started')
        self.assertEqual((self.project / 'src/app.py').read_text(), 'VALUE = 1\n')
        self.assertEqual(self.git('rev-parse', run['branch']), run['metrics']['commit'])
        self.assertEqual(self.receipts('DelegationMerged'), 0)
        merged = delegation.merge(self.m, run['id'], request_key='merge-in-panel', actor='workspace-user')
        self.assertTrue(merged['merged'])
        self.assertEqual((self.project / 'src/app.py').read_text(), 'VALUE = 2\n')
        self.assertEqual(self.receipts('DelegationMerged'), 1)

    def test_a_phase_recorded_after_a_merge_leaves_the_repeated_request_unchanged(self):
        """An idempotent replay reports the earlier merge, so a later phase change does not rewrite history."""
        from memory_module import planning
        run = self.delegate()
        merged = delegation.merge(self.m, run['id'], request_key='merge', actor='assistant')
        planning.set_phase(self.m, phase='production', reason='The change is released to customers.',
                           actor='workspace-user')
        again = delegation.merge(self.m, run['id'], request_key='merge', actor='assistant')
        self.assertTrue(again['duplicate'])
        self.assertEqual(again['commit'], merged['commit'])



class RunTableTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.path = Path(self.temp.name) / 'legacy.sqlite'
        with Memory.create(self.path, 'legacy', ['Keep the fixture.']) as memory:
            episode = memory.start('Legacy work', 'Keep it.', 'action', 'It is kept.', 'code')
            memory.db.executescript(reviews.SCHEMA)
            with memory._write():
                memory.db.execute('INSERT INTO review_runs (id,episode_id,role,signature,host,session_id,state,created_at,updated_at,request_key,snapshot) '
                                  "VALUES ('check_legacy',?,'outcome','s','codex','','pass',?,?,'legacy','{}')",
                                  (episode['id'], memory.now(), memory.now()))

    def tearDown(self):
        shutil.rmtree(self.temp.name, ignore_errors=True)

    def test_reads_tolerate_missing_columns_and_columns_are_added_once(self):
        with Memory(self.path, read_only=True) as memory:
            listing = delegation.runs(memory)
            self.assertEqual(listing['runs'][0]['parent_run'], None)
            self.assertEqual(reviews.active_runs(memory), [])
        with Memory(self.path) as memory:
            reviews.ensure_run_columns(memory)
            reviews.ensure_run_columns(memory)
            columns = [row[1] for row in memory.db.execute('PRAGMA table_info(review_runs)')]
            self.assertEqual([name for name in columns if name in reviews.RUN_COLUMNS], list(reviews.RUN_COLUMNS))


class UninstallHostTests(unittest.TestCase):
    def test_uninstall_removes_the_client_from_agent_hosts(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            project = Path(folder)
            launch = [sys.executable, '-m', 'memory_module.cli']
            info = install.setup(project, client='codex', _launcher=launch)
            install.setup(project, client='claude', _launcher=launch)
            with Memory(info['database']) as memory:
                self.assertEqual(reviews.configured(memory)['host'], 'claude')
            result = install.uninstall(project, 'claude')
            self.assertEqual(result['agent_hosts_removed'], ['claude'])
            with Memory(info['database']) as memory:
                config = reviews.configured(memory)
                self.assertEqual((config['host'], config['hosts']), ('codex', ['codex']))
            install.uninstall(project, 'codex')
            with Memory(info['database']) as memory:
                self.assertIsNone(reviews.configured(memory))


if __name__ == '__main__':
    unittest.main()
