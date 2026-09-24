"""Regression tests for the review of the focused problems implementation (commit fbabcdc).

Each test reproduces one verified finding with the fixture of tests/test_focus.py and shows that the
start now ends, the check stays under the control of the user, and the report counts every run.
"""
import importlib.util
import sys
import unicodedata

from memory_module import InvalidRecord, delegation, reviews, workspace
from tests.test_focus import CHECK_FILE, CHECK_FILE_REFUSED, DUPLICATE_HYPOTHESIS, H1, H2, H3, FocusFixture, change, work_report

VALUE_TEST = ("import pathlib, unittest\n\n\n"
              "class ValueTests(unittest.TestCase):\n"
              "    def test_value(self):\n"
              "        self.assertIn('VALUE = 2', pathlib.Path('src/app.py').read_text())\n")


class MergeFailureTests(FocusFixture):
    def test_a_merge_conflict_after_a_passing_review_blocks_the_item_instead_of_leaving_it_reviewing(self):
        episode = self.prepared((H2, H3), max_attempts=1)
        self.attempts = {1: change('VALUE = 2\n')}

        def commit_on_main():
            (self.project / 'src/app.py').write_text('VALUE = 5\n')
            self.git('commit', '-q', '-am', 'The user changes the value on main.')
        self.before = {1: commit_on_main}
        view = self.start(episode)
        self.assertEqual(view['state'], 'blocked')
        [attempt] = view['attempts']
        self.assertEqual((attempt['check_passed'], attempt['review_state'], attempt['merge_state']), (True, 'pass', None))
        self.assertEqual((self.receipts('FocusCompleted'), len(self.receipts('FocusBlocked'))), ([], 1))
        plan = self.latest_plan(episode)
        self.assertEqual((plan['state'], plan['next_action']), ('blocked', self.focus.MERGE_FAILED_NEXT_ACTION))
        self.assertIn('Git could not merge the delegated work', plan['reason'])
        self.assertEqual((self.project / 'src/app.py').read_text(), 'VALUE = 5\n')
        # The branch remains for the user, and the item is no longer held by a running start.
        run = reviews.read(self.m, attempt['run_id'])
        self.assertNotEqual(self.git('branch', '--list', run['branch']), '')
        self.assertEqual(self.focus.after_review(self.m, self.review_runs(episode)[0]['id'])['start_key'], 'start')
        self.assertEqual(self.focus_lessons(), [])
        self.set_check(episode, timeout_seconds=120, key='check-after-conflict')
        self.before = {}
        self.assertEqual(self.start(episode, key='start-after-conflict')['state'], 'merged')
        self.assertEqual((self.project / 'src/app.py').read_text(), 'VALUE = 2\n')


class CancelledAttemptTests(FocusFixture):
    def queued_attempt(self, hypotheses=(H1, H2)):
        episode = self.prepared(hypotheses)
        self.hold = True
        [attempt] = self.start(episode)['attempts']
        reviews.cancel(self.m, attempt['run_id'])
        self.hold = False
        return episode, attempt['run_id']

    def assert_released(self, episode):
        view = self.focus.view(self.m, episode)
        self.assertEqual(view['state'], 'blocked')
        plan = self.latest_plan(episode)
        self.assertEqual((plan['state'], plan['next_action']), ('blocked', self.focus.CANCELLED_NEXT_ACTION))
        self.assertEqual([item['state'] for item in view['hypotheses']], ['ruled_out', 'open'])
        # No further attempt starts without the user, and the user can change the check and start again.
        self.assertEqual(self.run_count(), 1)
        self.set_check(episode, timeout_seconds=120, key='check-after-cancel')
        self.attempts = {1: change('VALUE = 2\n')}
        self.assertEqual(self.start(episode, key='start-after-cancel')['state'], 'merged')

    def test_a_queued_attempt_cancelled_before_it_runs_ends_the_start_when_reviews_are_resumed(self):
        episode, _ = self.queued_attempt()
        self.assertEqual(self.focus.view(self.m, episode)['state'], 'running')
        delegation.start_missing_reviews(self.m)
        self.assert_released(episode)

    def test_a_cancelled_attempt_whose_worker_process_still_starts_ends_the_start(self):
        episode, run_id = self.queued_attempt()
        delegation.execute(self.m, run_id)
        self.assert_released(episode)

    def test_the_panel_cancel_action_and_a_new_start_settle_the_cancelled_attempt(self):
        episode = self.prepared()
        self.hold = True
        [attempt] = self.start(episode)['attempts']
        self.hold = False
        workspace.action(self.m, 'cancel_run', {'run_id': attempt['run_id']}, 'panel-cancel')
        self.assert_released(episode)

        second = self.prepared(title='Second item', key='second-')
        self.hold = True
        [queued] = self.start(second, key='second-start')['attempts']
        reviews.cancel(self.m, queued['run_id'])
        self.hold = False
        # The start settles the cancelled attempt first, so it is not refused as already running.
        self.attempts = {1: change('VALUE = 2\n# The second item.\n')}
        self.assertEqual(self.start(second, key='second-again')['state'], 'merged')


class CheckFileTests(FocusFixture):
    def commit_value_test(self):
        (self.project / 'tests/__init__.py').write_text('')
        (self.project / 'tests/test_value.py').write_text(VALUE_TEST)
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'Add the value test.')

    def test_unittest_class_and_method_targets_and_pytest_node_ids_name_their_file(self):
        self.commit_value_test()
        for argument in ('tests.test_value', 'tests.test_value.ValueTests', 'tests.test_value.ValueTests.test_value',
                         'tests/test_value.py', 'tests/test_value.py::ValueTests::test_value'):
            with self.subTest(argument=argument):
                self.assertEqual(self.focus.check_files(self.project, [sys.executable, '-m', 'unittest', argument]),
                                 ['tests/test_value.py'])

    def test_an_attempt_that_rewrites_a_test_named_by_class_is_refused(self):
        self.commit_value_test()
        episode = self.plan()
        self.propose(episode, (H1,), max_attempts=1)
        self.set_check(episode, [sys.executable, '-m', 'unittest', 'tests.test_value.ValueTests'])
        self.assertEqual([item['path'] for item in self.latest_plan(episode)['focus']['check']['files']], ['tests/test_value.py'])
        forged = VALUE_TEST.replace("self.assertIn('VALUE = 2', pathlib.Path('src/app.py').read_text())", 'pass')
        self.attempts = {1: {'write': {'src/app.py': 'VALUE = 3\n', 'tests/test_value.py': forged},
                             'report': work_report(changed_files=['src/app.py'])}}
        view = self.start(episode)
        run = reviews.read(self.m, view['attempts'][0]['run_id'])
        self.assertEqual(run['state'], 'scope_violation')
        self.assertIn(CHECK_FILE_REFUSED.replace(CHECK_FILE, 'tests/test_value.py'), run['error'])
        self.assertEqual((view['state'], (self.project / 'src/app.py').read_text()), ('blocked', 'VALUE = 1\n'))


class CheckIsolationTests(FocusFixture):
    def worktree(self, episode):
        run = self.work_runs(episode)[-1]
        return self.project / run['workspace']

    def test_a_check_file_hidden_from_git_in_the_worktree_does_not_decide_the_check(self):
        episode = self.prepared((H1,), max_attempts=1)
        self.before = {1: lambda: self.git('-C', str(self.worktree(episode)), 'update-index', '--skip-worktree', CHECK_FILE)}
        self.attempts = {1: {'write': {'src/app.py': 'VALUE = 3\n', CHECK_FILE: "print('forged')\n"},
                             'report': work_report(changed_files=['src/app.py'])}}
        view = self.start(episode)
        [attempt] = view['attempts']
        run = reviews.read(self.m, attempt['run_id'])
        self.assertEqual((run['state'], run['metrics']['changed_files']), ('completed', ['src/app.py']))
        receipt = self.check_receipt(attempt['run_id'])
        self.assertEqual((receipt['check_passed'], receipt['exit_code']), (False, 1))
        self.assertIn('Observed VALUE = 3', receipt['output_tail'])
        self.assertEqual((view['state'], view['hypotheses'][0]['state']), ('blocked', 'ruled_out'))
        self.assertEqual((self.project / 'src/app.py').read_text(), 'VALUE = 1\n')
        self.assertEqual(self.receipts('DelegationMerged'), [])

    def test_forged_bytecode_left_out_of_the_collected_commit_does_not_decide_the_check(self):
        episode = self.plan()
        self.propose(episode, (H1,), max_attempts=1)
        self.set_check(episode, [sys.executable, '-m', 'tests.check_value'])

        def forge():
            source = self.worktree(episode) / CHECK_FILE
            stat = source.stat()
            code = compile("print('forged bytecode')\nimport sys\nsys.exit(0)\n", str(source), 'exec')
            data = importlib._bootstrap_external._code_to_timestamp_pyc(code, stat.st_mtime, stat.st_size)
            cached = __import__('pathlib').Path(importlib.util.cache_from_source(str(source)))
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_bytes(data)
        self.before = {1: forge}
        self.attempts = {1: change('VALUE = 3\n')}
        view = self.start(episode)
        [attempt] = view['attempts']
        receipt = self.check_receipt(attempt['run_id'])
        self.assertEqual(reviews.read(self.m, attempt['run_id'])['metrics']['changed_files'], ['src/app.py'])
        self.assertFalse(receipt['check_passed'])
        self.assertNotIn('forged bytecode', receipt['output_tail'])
        self.assertEqual((view['state'], (self.project / 'src/app.py').read_text()), ('blocked', 'VALUE = 1\n'))

    def test_an_added_module_that_shadows_a_standard_module_is_refused(self):
        episode = self.prepared((H1,), max_attempts=1)
        self.attempts = {1: {'write': {'src/app.py': 'VALUE = 3\n', 'tests/pathlib.py': 'import sys\nsys.exit(0)\n'},
                             'report': work_report(changed_files=['src/app.py', 'tests/pathlib.py'])}}
        view = self.start(episode)
        run = reviews.read(self.m, view['attempts'][0]['run_id'])
        self.assertEqual(run['state'], 'scope_violation')
        self.assertIn(self.focus.CHECK_SUPPORT_REFUSED.format(files='tests/pathlib.py'), run['error'])
        self.assertEqual(run['metrics']['check_support_changed'], ['tests/pathlib.py'])
        self.assertEqual((view['state'], (self.project / 'src/app.py').read_text()), ('blocked', 'VALUE = 1\n'))
        self.assertEqual(self.receipts('DelegationMerged'), [])

    def test_check_support_names_loadable_files_and_leaves_ordinary_changes(self):
        base = self.git('rev-parse', 'HEAD')
        changed = ['src/app.py', 'src/pathlib.py', 'src/json/__init__.py', 'conftest.py', 'tests/helper.py',
                   'lib/site.pth', CHECK_FILE, 'docs/notes.md']
        self.assertEqual(self.focus.check_support(self.project, base, changed, [CHECK_FILE]),
                         ['src/pathlib.py', 'src/json/__init__.py', 'conftest.py', 'tests/helper.py', 'lib/site.pth'])
        # A check at the project root does not protect every root file, and a module already committed may change.
        (self.project / 'src/types.py').write_text('NAME = 1\n')
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'Add a module named like a standard module.')
        head = self.git('rev-parse', 'HEAD')
        self.assertEqual(self.focus.check_support(self.project, head, ['app.py', 'src/types.py'], ['check.py']), [])


class PanelTests(FocusFixture):
    def test_saving_the_plan_form_keeps_the_focus_block_and_its_check(self):
        episode = self.prepared((H2,), max_attempts=1)
        focus_before = self.latest_plan(episode)['focus']
        plan = self.latest_plan(episode)
        # The payload shape that the plan form of the control panel sends, without a focus field.
        payload = {'state': plan['state'], 'next_action': 'Change the value after the review.', 'scope': plan['scope'],
                   'autonomy': plan['autonomy'], 'reason': 'The user edits the next action.', 'paths': plan['paths']}
        workspace.action(self.m, 'plan', {'episode_id': episode, 'expected_version': self.m.episode(episode)['version'],
                                          'payload': payload}, 'panel-plan-edit')
        self.assertEqual(self.latest_plan(episode)['focus'], focus_before)
        self.assertEqual(self.focus.view(self.m, episode)['state'], 'ready')

    def test_the_user_sets_the_check_and_starts_the_focused_problem(self):
        episode = self.plan()
        self.propose(episode, (H2,), max_attempts=1)
        self.assertEqual(self.focus.view(self.m, episode)['state'], 'awaiting_check')
        block = workspace.action(self.m, 'focus_check', {'episode_id': episode, 'command': [sys.executable, CHECK_FILE],
                                                         'timeout_seconds': 60}, 'panel-check')
        self.assertEqual(block['check']['files'][0]['path'], CHECK_FILE)
        view = workspace.action(self.m, 'focus_start', {'episode_id': episode}, 'panel-start')
        self.assertEqual(view['state'], 'merged')
        self.assertEqual((self.project / 'src/app.py').read_text(), 'VALUE = 2\n')


class KeyAndTextTests(FocusFixture):
    def test_the_same_start_key_starts_two_work_items(self):
        first = self.prepared((H2,), max_attempts=1)
        self.assertEqual(self.start(first, key='focus-start')['state'], 'merged')
        second = self.prepared((H3,), max_attempts=1, title='Second item', key='second-')
        self.attempts = {1: change('VALUE = 2\n# The second item.\n')}
        self.assertEqual(self.start(second, key='focus-start')['state'], 'merged')
        self.assertEqual(len(self.receipts('FocusStarted')), 2)

    def test_a_decomposed_statement_repeats_its_composed_form(self):
        episode = self.plan()
        composed = unicodedata.normalize('NFC', 'Le cache est périmé.')
        decomposed = unicodedata.normalize('NFD', composed)
        self.assertNotEqual(composed, decomposed)
        self.assertEqual(self.focus.normalise(decomposed), 'le cache est périmé')
        self.propose(episode, ({'statement': composed, 'approach': 'Clear the cache.'},))
        with self.assertRaises(InvalidRecord) as refused:
            self.focus.hypothesis(self.m, episode, statement=decomposed, approach='Clear the cache again.',
                                  request_key='decomposed', actor='assistant')
        self.assertEqual(str(refused.exception), DUPLICATE_HYPOTHESIS)

    def test_the_report_counts_the_run_on_the_unavailable_host_of_a_rerouted_attempt(self):
        episode = self.prepared((H2,), max_attempts=1)
        self.attempts = {(1, self.work_host): {'unavailable': 'You have hit your usage limit. Try again in 20 minutes.'},
                         1: change('VALUE = 2\n', usage={'input_tokens': 7, 'output_tokens': 3})}
        self.start(episode)
        first = next(run for run in self.work_runs(episode) if run['state'] == 'host_unavailable')
        [attempt] = self.focus.view(self.m, episode)['attempts']
        rerouted = reviews.read(self.m, attempt['run_id'])
        report = self.focus.report(self.m, episode_id=episode)
        self.assertEqual(report['hosts'], {self.work_host: 1, self.other_host: 1})
        durations = [(reviews.read(self.m, run_id)['metrics'] or {}).get('duration_ms') or 0 for run_id in (first['id'], rerouted['id'])]
        self.assertGreater(durations[0], 0)
        self.assertEqual(report['run_duration_ms'], sum(durations))
        self.assertEqual(report['tokens'], 10)
        [entry] = report['items'][0]['attempts']
        self.assertEqual((entry['rerouted_from'], entry['run_duration_ms']), ([first['id']], sum(durations)))


if __name__ == '__main__':
    import unittest
    unittest.main()
