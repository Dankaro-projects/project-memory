"""Focused problems: several independent attempts on one problem, decided by a check the user set.

This file is the acceptance check of sections 11.1 to 11.4 and the contract of section 11.9 in
.memory/build/rebuild-spec.md. It uses the fake host processes and the real git fixture of
tests/test_delegation.py. No real host runs.
"""
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from memory_module import Memory, InvalidRecord, Conflict, core, delegation, install, mcp, planning, reviews, workspace
from tests.test_delegation import WORKER, work_report

CHECK_SCRIPT = ("import pathlib, sys\n"
                "lines = pathlib.Path('src/app.py').read_text().splitlines()\n"
                "print('Observed ' + (lines[0] if lines else 'nothing'))\n"
                "sys.exit(0 if 'VALUE = 2' in lines else 1)\n")
CHECK_FILE = 'tests/check_value.py'
PROBLEM = 'The value in src/app.py is wrong after loading.'
# The fake worker of tests/test_delegation.py, with the host usage taken from the spec when the spec names it.
FOCUS_WORKER = WORKER.replace("{'input_tokens': 10}", "spec.get('usage', {'input_tokens': 10})")
assert FOCUS_WORKER.count("spec.get('usage'") == 2

H1 = {'statement': 'The loader reads a cached copy of the value.', 'approach': 'Set the value to three in the source file.'}
H2 = {'statement': 'The source file holds the wrong value.', 'approach': 'Set the value to two in the source file.'}
H3 = {'statement': 'The value is overwritten after it is loaded.', 'approach': 'Remove the second assignment of the value.'}

CHECK_USER_ONLY = ('Only the user can set the check of a focused problem, in the control panel, because the check runs a '
                   'command on this computer.')
START_USER_ONLY = 'Only the user can start a focused problem, in the control panel.'
CHECK_MISSING = 'The focused problem has no check. Ask the user to set the check in the control panel before any attempt starts.'
NOT_ELIGIBLE = ('A focused problem starts only when the work item is blocked, when an earlier delegated run of it failed its '
                'review or its check, or when an outcome of it repeats a guarded failure.')
HYPOTHESIS_MISSING = 'Record at least one open hypothesis before the focused problem starts.'
DUPLICATE_HYPOTHESIS = 'This hypothesis repeats an earlier hypothesis of this work item. State a different hypothesis.'
CHECK_FILE_CHANGED = ('A file that the check names changed after the check was set: tests/check_value.py. Set the check again '
                      'before the attempts start.')
CHECK_FILE_UNCOMMITTED = ('The check names a file that is not committed with its current content: checks/value.py. Commit the '
                          'file before setting the check, because every attempt starts from the last commit.')
CHECK_FILE_REFUSED = ('The worker changed files that the check of the focused problem names: tests/check_value.py. '
                      'An attempt may not change its own check, so this attempt is refused.')
BLOCKED_NEXT_ACTION = ('Every attempt of the focused problem failed its check. Ask the user for direction before another '
                       'attempt.')
REVIEW_FAILED_NEXT_ACTION = ('The selected attempt passed its check but not its work review. Ask the user for direction before '
                             'another attempt.')
REQUEST_FAILED_NEXT_ACTION = ('The next attempt of the focused problem could not be requested. Ask the user for direction before '
                              'another attempt.')
ALREADY_RUNNING = 'A focused problem of this work item is already running. Wait for it to finish.'
CHECK_WHILE_RUNNING = 'The check cannot change while attempts of the focused problem are running. Wait until they finish.'
PROJECT_LIMIT = 'Two agent runs are already active for this project. Wait for one of them or cancel it first.'
PARALLEL_LIMIT = 'Parallel mode allows 1 or 2 attempts, because a project runs at most two agent runs at the same time.'
HYPOTHESIS_RESULT_ONLY = 'Only Project Memory records the result of a hypothesis, after it runs the check of the focused problem.'
HYPOTHESIS_STARTS_OPEN = 'A new hypothesis starts in the open state.'
FOCUS_ACTOR_RESERVED = ('The actor name focus-orchestrator is reserved for the checks that Project Memory runs. Use your own '
                        'actor name.')
PROBLEM_TEXT = 'The focused problem needs a problem statement of 1 to 4,000 characters.'
ATTEMPT_COUNT = 'The focused problem allows 1 to 3 attempts.'
MODE_TEXT = 'The focus mode must be relay or parallel.'
COMMAND_TEXT = 'The check must be a list of one or more arguments. No shell is used, so write each argument separately.'
TIMEOUT_TEXT = 'The check timeout must be between 10 and 1,800 seconds.'
BLOCK_KEYS = 'The focus block accepts only problem, max_attempts, mode and check.'
REPORT_BASIS = ('Every number comes from receipts, checks and reviews recorded by Project Memory, never from the report of '
                'an agent.')
FOCUS_ACTOR = 'focus-orchestrator'


def change(text, usage=None, **report):
    """A fake worker that writes the value file and returns a valid work report, with the host usage when given."""
    spec = {'write': {'src/app.py': text}, 'report': work_report(**report)}
    if usage is not None:
        spec['usage'] = usage
    return spec


def cut(text, limit):
    """A prompt line cut to its limit, as section 11.9.5 describes."""
    return text if len(text) <= limit else text[:limit - 3] + '...'


def section(prompt):
    """The three lines of the focus section of a worker prompt."""
    lines = prompt.split('\n')
    start = next(index for index, line in enumerate(lines) if line.startswith('Focused problem: '))
    return lines[start:start + 3]


class FocusFixture(unittest.TestCase):
    """The fixture of tests/test_delegation.py with a check file inside the plan paths."""

    def setUp(self):
        self.focus = importlib.import_module('memory_module.focus')
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
        (self.project / 'tests').mkdir()
        (self.project / CHECK_FILE).write_text(CHECK_SCRIPT)
        (self.project / 'docs').mkdir()
        (self.project / 'docs/notes.md').write_text('# Notes\n')
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'Initial')
        info = install.setup(self.project)
        self.m = Memory(info['database'])
        reviews.configure(self.m, self.project, 'codex')
        reviews.configure(self.m, self.project, 'claude')
        config = reviews.configured(self.m)
        self.work_host = config.get('work_host') or config['hosts'][0]
        self.other_host = next(name for name in config['hosts'] if name != self.work_host)
        self.attempts = {}
        self.workers = {}
        self.reviewers = {}
        self.prompts = []
        # Callables run in the fixture process before the fake worker of an attempt starts, by attempt number.
        self.before = {}
        # While hold is true, a launched run stays queued, so an attempt remains active.
        self.hold = False
        for target, name, effect in ((delegation, 'work_command', self.fake_worker),
                                     (reviews, 'command', self.fake_reviewer),
                                     (delegation, 'launch', self.run_now)):
            patcher = patch.object(target, name, side_effect=effect)
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        if hasattr(self, 'm'):
            self.m.close()
        if hasattr(self, 'temp'):
            shutil.rmtree(self.temp.name, ignore_errors=True)

    # Fixture helpers copied from tests/test_delegation.py and adapted to attempts.

    def git(self, *args):
        result = subprocess.run(['git', '-C', str(self.project), *args], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def plan(self, title='Fix the value', paths=('src/**', 'tests/**'), state='blocked', **extra):
        source = self.m.source('user:' + title, 'User request', 'The user asks for the change.',
                               'Change the value within the source folder.', 'user', subject='code')
        payload = {'state': state, 'next_action': 'Change the value.', 'scope': 'Change the value in the source folder only.',
                   'autonomy': 'act', 'reason': 'The user requests the change.', 'paths': list(paths), **extra}
        result = planning.save(self.m, 'work_plan', payload=payload, actor='workspace-user',
                               evidence=[{'source_id': source['id'], 'reason': 'The user requests this change.'}],
                               title=title, objective='Set the value to two.', criterion='VALUE equals 2 in src/app.py.',
                               subject='code', request_key='plan:' + title)
        return result['episode_id']

    def fake_worker(self, host, worktree, folder, prompt):
        given = json.loads((Path(folder) / 'input.json').read_text())
        attempt = (given.get('focus') or {}).get('attempt')
        self.prompts.append({'host': host, 'attempt': attempt, 'prompt': prompt})
        if attempt in self.before:
            self.before[attempt]()
        spec = self.attempts.get((attempt, host)) or self.attempts.get(attempt) or self.workers.get(host) \
            or change('VALUE = 2\n')
        return [sys.executable, '-c', FOCUS_WORKER, json.dumps(spec), str(folder), host]

    def fake_reviewer(self, host, project, folder, prompt):
        spec = {'review': True, **self.reviewers.get(host, {})}
        return [sys.executable, '-c', WORKER, json.dumps(spec), str(folder), host]

    def run_now(self, memory, run):
        if not self.hold:
            delegation.execute(memory, run['id'])

    def receipts(self, name):
        return [json.loads(row[0]) for row in self.m.db.execute(
            'SELECT payload FROM host_receipts WHERE event_name=? ORDER BY rowid', (name,))]

    def work_runs(self, episode):
        return [run for run in reversed(delegation.runs(self.m, episode_id=episode, limit=100)['runs']) if run['role'] == 'work']

    def review_runs(self, episode):
        return [run for run in delegation.runs(self.m, episode_id=episode, limit=100)['runs'] if run['role'] == 'work_review']

    def latest_plan(self, episode):
        return planning.latest(self.m, episode, 'work_plan')

    def focus_lessons(self):
        return [row[0] for row in self.m.db.execute(
            "SELECT id FROM events WHERE kind='lesson' AND actor=? ORDER BY rowid", (FOCUS_ACTOR,))]

    def propose(self, episode, hypotheses=(H1, H2), *, max_attempts=2, mode='relay', key='propose', problem=PROBLEM):
        return self.focus.propose(self.m, episode, problem=problem,
                                  actor='assistant', request_key=key, max_attempts=max_attempts, mode=mode,
                                  hypotheses=[dict(item) for item in hypotheses])

    def set_check(self, episode, command=None, *, timeout_seconds=60, actor='workspace-user', key='check'):
        return self.focus.set_check(self.m, episode, command=command or [sys.executable, CHECK_FILE],
                                    timeout_seconds=timeout_seconds, request_key=key, actor=actor)

    def prepared(self, hypotheses=(H1, H2), *, title='Fix the value', paths=('src/**', 'tests/**'), key='', **options):
        """A blocked item with a proposal and the check. A second item in one test passes its own title and key."""
        episode = self.plan(title, paths=paths)
        self.propose(episode, hypotheses, key=key + 'propose', **options)
        self.set_check(episode, key=key + 'check')
        return episode

    def check_receipt(self, run_id):
        return next(item for item in self.receipts('FocusCheckRecorded') if item['run_id'] == run_id)

    def decision_with_action(self, episode):
        """A decision and its action in the item, with the evidence an outcome of it cites."""
        source = self.m.source('result:' + episode, 'Observed result', 'The value is one.', 'VALUE equals 1.', 'tool', subject='code')
        evidence = [{'source_id': source['id'], 'reason': 'The fixture records the observed value.'}]
        decision = self.m.record(episode, 'decision', {'decision': 'Set the value to two.', 'why': 'The user requests it.',
                                                       'expected': 'VALUE equals 2.', 'reconsider_when': 'The request changes.'},
                                 expected_version=self.m.episode(episode)['version'], request_key='decision:' + episode,
                                 actor='assistant', evidence=evidence)
        self.m.record(episode, 'action', {'action': 'Edit the value.'}, expected_version=decision['version'],
                      request_key='action:' + episode, actor='assistant', decision_id=decision['id'])

        def outcome(key, assessment, actor, **extra):
            latest = self.m.db.execute("SELECT id FROM events WHERE decision_id=? AND kind='outcome' ORDER BY seq DESC LIMIT 1",
                                       (decision['id'],)).fetchone()
            payload = {'observed': 'VALUE equals 1.', 'assessment': assessment, 'assessment_reason': 'The file shows the value.',
                       'severity': 'none' if assessment == 'good' else 'minor', 'attribution': 'The edit.', **extra}
            return self.m.record(episode, 'outcome', payload, expected_version=self.m.episode(episode)['version'],
                                 request_key=key, actor=actor, decision_id=decision['id'], evidence=evidence,
                                 supersedes=latest[0] if latest else None)['id']
        return decision['id'], outcome

    def start(self, episode, key='start', actor='workspace-user'):
        return self.focus.start(self.m, episode, request_key=key, actor=actor)

    def run_count(self):
        return self.m.db.execute("SELECT count(*) FROM review_runs WHERE role='work'").fetchone()[0]

    def rule(self, **triggers):
        """Record and accept a lesson in its own work item, as tests/test_delegation.py does."""
        learning = self.m.start('Lessons', 'Collect lessons.', 'learning', 'Lessons are reviewed.', subject='code')['id']
        source = self.m.source('user-lessons', 'Lessons', 'User instruction', 'Keep the exception.', 'user', subject='code')
        evidence = [{'source_id': source['id'], 'reason': 'The user asks for the lesson.'}]
        payload = {'when': 'Loading the value.', 'do': 'Refresh the cached value.', 'because': 'A stale value was served.',
                   'exceptions': 'Documentation changes.', **triggers}
        lesson = self.m.record(learning, 'lesson', payload, expected_version=self.m.episode(learning)['version'],
                               request_key='lesson', actor='assistant', evidence=evidence)['id']
        self.m.record(learning, 'lesson_review', {'lesson_id': lesson, 'status': 'accepted', 'reason': 'The user reviewed it.'},
                      expected_version=self.m.episode(learning)['version'], request_key='lesson:review',
                      actor='workspace-user', evidence=evidence,
                      links=[{'event_id': lesson, 'reason': 'This review assesses the lesson.'}])
        return lesson


class EligibilityTests(FocusFixture):
    def test_a_ready_item_without_failures_is_not_eligible_and_does_not_start(self):
        episode = self.plan(state='ready')
        self.propose(episode)
        self.set_check(episode)
        self.assertFalse(self.focus.eligible(self.m, episode))
        self.assertEqual(self.focus.reasons(self.m, episode), [])
        with self.assertRaises(InvalidRecord) as refused:
            self.start(episode)
        self.assertEqual(str(refused.exception), NOT_ELIGIBLE)
        self.assertEqual(self.run_count(), 0)
        self.assertEqual(self.receipts('FocusStarted'), [])

    def test_a_blocked_item_is_eligible(self):
        episode = self.plan(state='blocked')
        self.assertTrue(self.focus.eligible(self.m, episode))
        self.assertEqual([reason['type'] for reason in self.focus.reasons(self.m, episode)], ['blocked'])
        self.assertTrue(all(reason['reason'] for reason in self.focus.reasons(self.m, episode)))

    def test_a_delegated_run_that_failed_its_review_makes_the_item_eligible(self):
        episode = self.plan(state='ready')
        self.reviewers = {'codex': {'verdict': 'changes_required'}, 'claude': {'verdict': 'changes_required'}}
        run = delegation.request_work(self.m, episode, request_key='earlier-delegation')
        delegation.launch(self.m, run)
        self.assertEqual(delegation.latest_review(self.m, run['id'])['state'], 'changes_required')
        self.assertTrue(self.focus.eligible(self.m, episode))
        self.assertEqual([reason['type'] for reason in self.focus.reasons(self.m, episode)], ['failed_run'])

    def test_a_guarded_recurrence_makes_the_item_eligible_until_the_user_reassesses(self):
        episode = self.plan(state='ready')
        self.rule(failure_type='stale_value')
        source = self.m.source('result:' + episode, 'Observed result', 'The value is one.', 'VALUE equals 1.', 'tool', subject='code')
        evidence = [{'source_id': source['id'], 'reason': 'The fixture records the observed value.'}]
        decision = self.m.record(episode, 'decision', {'decision': 'Set the value to two.', 'why': 'The user requests it.',
                                                       'expected': 'VALUE equals 2.', 'reconsider_when': 'The request changes.'},
                                 expected_version=self.m.episode(episode)['version'], request_key='decision',
                                 actor='assistant', evidence=evidence)
        self.m.record(episode, 'action', {'action': 'Edit the value.'}, expected_version=decision['version'],
                      request_key='action', actor='assistant', decision_id=decision['id'])

        def outcome(key, assessment, actor, supersedes=None, **extra):
            payload = {'observed': 'VALUE equals 1.', 'assessment': assessment, 'assessment_reason': 'The file shows the value.',
                       'severity': 'none' if assessment == 'good' else 'minor', 'attribution': 'The edit.', **extra}
            return self.m.record(episode, 'outcome', payload, expected_version=self.m.episode(episode)['version'],
                                 request_key=key, actor=actor, decision_id=decision['id'], evidence=evidence,
                                 supersedes=supersedes)['id']

        unguarded = outcome('unguarded', 'bad', 'assistant', failure_type='unguarded_type')
        self.assertFalse(self.focus.eligible(self.m, episode))
        guarded = outcome('guarded', 'bad', 'assistant', supersedes=unguarded, failure_type='stale_value')
        self.assertTrue(self.focus.eligible(self.m, episode))
        self.assertEqual([reason['type'] for reason in self.focus.reasons(self.m, episode)], ['guarded_recurrence'])
        outcome('reassessed', 'good', 'workspace-user', supersedes=guarded)
        self.assertFalse(self.focus.eligible(self.m, episode))

    def test_the_reassess_action_removes_the_reason_and_an_earlier_failure_is_no_recurrence(self):
        episode = self.plan(state='ready')
        decision, outcome = self.decision_with_action(episode)
        # A failure recorded before the guard was accepted is not a recurrence of that guard.
        early = outcome('early', 'bad', 'assistant', failure_type='stale_value')
        self.rule(failure_type='stale_value')
        self.assertFalse(self.focus.eligible(self.m, episode))
        # The same failure type after the acceptance is, until the user reassesses that outcome in the panel.
        later = outcome('later', 'bad', 'assistant', failure_type='stale_value')
        self.assertEqual([reason['type'] for reason in self.focus.reasons(self.m, episode)], ['guarded_recurrence'])
        workspace.action(self.m, 'reassess', {'decision_id': decision, 'outcome_id': early, 'assessment': 'good',
                                              'reason': 'The early outcome was measured before the fix.'}, 'reassess-early')
        self.assertTrue(self.focus.eligible(self.m, episode))
        workspace.action(self.m, 'reassess', {'decision_id': decision, 'outcome_id': later, 'assessment': 'good',
                                              'reason': 'The value is two after the fix.'}, 'reassess-later')
        self.assertFalse(self.focus.eligible(self.m, episode))


class CheckTests(FocusFixture):
    def test_only_the_user_sets_the_check_and_the_named_files_are_recorded_with_digests(self):
        episode = self.plan()
        self.propose(episode)
        self.assertEqual(self.focus.view(self.m, episode)['state'], 'awaiting_check')
        self.assertNotIn('check', self.latest_plan(episode)['focus'])
        with self.assertRaises(InvalidRecord) as refused:
            self.set_check(episode, actor='assistant', key='agent-check')
        self.assertEqual(str(refused.exception), CHECK_USER_ONLY)
        self.assertNotIn('check', self.latest_plan(episode)['focus'])

        digest = hashlib.sha256((self.project / CHECK_FILE).read_bytes()).hexdigest()
        self.set_check(episode)
        check = self.latest_plan(episode)['focus']['check']
        self.assertEqual(check['command'], [sys.executable, CHECK_FILE])
        self.assertEqual(check['timeout_seconds'], 60)
        self.assertEqual(check['files'], [{'path': CHECK_FILE, 'sha256': digest}])
        self.assertEqual(self.m.read(self.latest_plan(episode)['id'])['actor'], 'workspace-user')
        self.assertEqual(self.focus.view(self.m, episode)['state'], 'ready')

        self.set_check(episode, ['python3', '-m', 'unittest', 'tests.check_value'], key='module-check')
        self.assertEqual([item['path'] for item in self.latest_plan(episode)['focus']['check']['files']], [CHECK_FILE])
        with self.assertRaisesRegex(InvalidRecord, 'The check timeout must be between 10 and 1,800 seconds.'):
            self.set_check(episode, timeout_seconds=5, key='short-check')
        with self.assertRaisesRegex(InvalidRecord, 'No shell is used'):
            self.set_check(episode, 'python3 tests/check_value.py', key='shell-check')

    def test_an_agent_plan_revision_cannot_add_change_or_remove_the_check_but_may_carry_it(self):
        episode = self.plan()
        self.propose(episode)
        self.set_check(episode)
        plan = self.latest_plan(episode)
        evidence = [{'source_id': item['source_id'], 'reason': item['reason']} for item in self.m.read(plan['id'])['evidence']]
        payload = {key: value for key, value in plan.items() if key != 'id'}

        def revise(focus, key):
            return planning.save(self.m, 'work_plan', episode_id=episode, expected_version=self.m.episode(episode)['version'],
                                 payload={**payload, 'focus': focus}, actor='assistant', evidence=evidence, request_key=key)

        changed = {**payload['focus'], 'check': {**payload['focus']['check'], 'timeout_seconds': 900}}
        removed = {key: value for key, value in payload['focus'].items() if key != 'check'}
        for focus, key in ((changed, 'agent-changes-check'), (removed, 'agent-removes-check')):
            with self.assertRaises(InvalidRecord) as refused:
                revise(focus, key)
            self.assertEqual(str(refused.exception), CHECK_USER_ONLY)
        other = self.plan('Other item')
        with self.assertRaises(InvalidRecord) as refused:
            planning.save(self.m, 'work_plan', episode_id=other, expected_version=self.m.episode(other)['version'],
                          payload={**payload, 'focus': changed}, actor='assistant', evidence=evidence, request_key='agent-adds-check')
        self.assertEqual(str(refused.exception), CHECK_USER_ONLY)

        planning.progress(self.m, episode_id=episode, expected_version=self.m.episode(episode)['version'],
                          payload={'next_action': 'Wait for the user to start the attempts.', 'reason': 'The check is set.'},
                          actor='assistant', request_key='agent-progress')
        self.assertEqual(self.latest_plan(episode)['focus']['check'], payload['focus']['check'])

    def test_the_focus_block_is_validated(self):
        episode = self.plan()
        with self.assertRaises(InvalidRecord) as refused:
            self.propose(episode, max_attempts=4, key='too-many')
        self.assertEqual(str(refused.exception), ATTEMPT_COUNT)
        with self.assertRaises(InvalidRecord) as refused:
            self.propose(episode, mode='debate', key='bad-mode')
        self.assertEqual(str(refused.exception), MODE_TEXT)
        with self.assertRaises(InvalidRecord) as refused:
            self.propose(episode, mode='parallel', max_attempts=3, key='parallel-three')
        self.assertEqual(str(refused.exception), PARALLEL_LIMIT)
        self.assertNotIn('focus', self.latest_plan(episode))

        check = {'command': [sys.executable, CHECK_FILE], 'timeout_seconds': 60}
        refusals = [({'problem': ''}, PROBLEM_TEXT), ({'problem': 'x' * 4001}, PROBLEM_TEXT), ({'max_attempts': 2}, PROBLEM_TEXT),
                    ({'problem': 'A problem.', 'owner': 'assistant'}, BLOCK_KEYS),
                    ({'problem': 'A problem.', 'max_attempts': 0}, ATTEMPT_COUNT),
                    ({'problem': 'A problem.', 'max_attempts': True}, ATTEMPT_COUNT),
                    ({'problem': 'A problem.', 'max_attempts': '2'}, ATTEMPT_COUNT),
                    ({'problem': 'A problem.', 'mode': 'parallel', 'max_attempts': 3}, PARALLEL_LIMIT),
                    ({'problem': 'A problem.', 'check': {**check, 'command': []}}, COMMAND_TEXT),
                    ({'problem': 'A problem.', 'check': {**check, 'command': ['python3', '']}}, COMMAND_TEXT),
                    ({'problem': 'A problem.', 'check': {**check, 'command': ['python3'] * 51}}, COMMAND_TEXT),
                    ({'problem': 'A problem.', 'check': {**check, 'command': 'python3 tests/check_value.py'}}, COMMAND_TEXT),
                    ({'problem': 'A problem.', 'check': {**check, 'timeout_seconds': 9}}, TIMEOUT_TEXT),
                    ({'problem': 'A problem.', 'check': {**check, 'timeout_seconds': 1801}}, TIMEOUT_TEXT),
                    ({'problem': 'A problem.', 'check': {**check, 'timeout_seconds': True}}, TIMEOUT_TEXT)]
        for block, message in refusals:
            with self.subTest(block=block), self.assertRaises(InvalidRecord) as refused:
                self.focus.validate_block(block)
            self.assertEqual(str(refused.exception), message)
        for block in ({'problem': 'x' * 4000}, {'problem': 'A problem.', 'max_attempts': 3, 'mode': 'relay'},
                      {'problem': 'A problem.', 'max_attempts': 2, 'mode': 'parallel',
                       'check': {**check, 'command': ['python3'] * 50, 'timeout_seconds': 1800}},
                      {'problem': 'A problem.', 'max_attempts': 1, 'check': {**check, 'timeout_seconds': 10}}):
            with self.subTest(block=block):
                self.focus.validate_block(block)

        self.propose(episode)
        for command, timeout, message in (([], 60, COMMAND_TEXT), ([sys.executable, CHECK_FILE], 1801, TIMEOUT_TEXT)):
            with self.assertRaises(InvalidRecord) as refused:
                self.focus.set_check(self.m, episode, command=command, timeout_seconds=timeout, request_key='invalid-' + str(timeout),
                                     actor='workspace-user')
            self.assertEqual(str(refused.exception), message)
        self.assertNotIn('check', self.latest_plan(episode)['focus'])

    def test_a_check_file_must_be_committed_because_the_attempts_start_from_the_last_commit(self):
        episode = self.plan(paths=('src/**',))
        (self.project / 'checks').mkdir()
        (self.project / 'checks/value.py').write_text(CHECK_SCRIPT)
        self.propose(episode, (H2,), max_attempts=1)
        with self.assertRaises(InvalidRecord) as refused:
            self.set_check(episode, [sys.executable, 'checks/value.py'])
        self.assertEqual(str(refused.exception), CHECK_FILE_UNCOMMITTED)
        self.assertNotIn('check', self.latest_plan(episode)['focus'])
        self.git('add', 'checks/value.py')
        self.git('commit', '-q', '-m', 'Add the check')
        self.set_check(episode, [sys.executable, 'checks/value.py'], key='committed-check')
        self.assertEqual([item['path'] for item in self.latest_plan(episode)['focus']['check']['files']], ['checks/value.py'])
        self.start(episode)
        view = self.focus.view(self.m, episode)
        self.assertEqual((view['state'], view['attempts'][0]['check_passed']), ('merged', True))

    def test_the_check_and_a_second_start_are_refused_while_attempts_run(self):
        episode = self.prepared((H1, H2))
        self.attempts = {1: change('VALUE = 2\n')}
        self.hold = True
        self.start(episode)
        view = self.focus.view(self.m, episode)
        self.assertEqual((view['state'], [item['run_state'] for item in view['attempts']]), ('running', ['queued']))
        self.assertEqual([item['check_passed'] for item in view['attempts']], [None])
        with self.assertRaises(Conflict) as refused:
            self.start(episode, key='second-start')
        self.assertEqual(str(refused.exception), ALREADY_RUNNING)
        with self.assertRaises(Conflict) as refused:
            self.set_check(episode, timeout_seconds=120, key='check-while-running')
        self.assertEqual(str(refused.exception), CHECK_WHILE_RUNNING)
        self.assertEqual(self.latest_plan(episode)['focus']['check']['timeout_seconds'], 60)
        self.assertEqual(len(self.receipts('FocusStarted')), 1)
        self.hold = False
        delegation.execute(self.m, view['attempts'][0]['run_id'])
        self.assertEqual(self.focus.view(self.m, episode)['state'], 'merged')
        self.set_check(episode, timeout_seconds=120, key='check-after-run')
        self.assertEqual(self.latest_plan(episode)['focus']['check']['timeout_seconds'], 120)

    def test_the_check_cannot_be_set_or_started_over_mcp(self):
        episode = self.plan()
        self.assertEqual([name for name in mcp.OPERATIONS if 'focus' in name], ['focus_propose'])
        data = {'episode_id': episode, 'problem': 'The value in src/app.py is wrong after loading.', 'actor': 'assistant',
                'hypotheses': [dict(H1)], 'check': {'command': [sys.executable, CHECK_FILE], 'timeout_seconds': 60}}
        with self.assertRaises(InvalidRecord):
            mcp.dispatch(self.m, 'memory_write', {'operation': 'focus_propose', 'request_key': 'with-check', 'data': data})
        self.assertNotIn('focus', self.latest_plan(episode))
        data.pop('check')
        mcp.dispatch(self.m, 'memory_write', {'operation': 'focus_propose', 'request_key': 'proposal', 'data': data})
        self.assertEqual(self.latest_plan(episode)['focus']['problem'], data['problem'])
        self.assertNotIn('check', self.latest_plan(episode)['focus'])

        plan = self.latest_plan(episode)
        evidence = [{'source_id': item['source_id'], 'reason': item['reason']} for item in self.m.read(plan['id'])['evidence']]
        payload = {key: value for key, value in plan.items() if key != 'id'}
        payload['focus'] = {**payload['focus'], 'check': {'command': [sys.executable, CHECK_FILE], 'timeout_seconds': 60}}
        with self.assertRaises(InvalidRecord) as refused:
            mcp.dispatch(self.m, 'memory_write', {'operation': 'plan', 'request_key': 'plan-with-check', 'data': {
                'episode_id': episode, 'expected_version': self.m.episode(episode)['version'], 'payload': payload,
                'actor': 'assistant', 'evidence': evidence}})
        self.assertIn(CHECK_USER_ONLY, str(refused.exception))
        self.assertNotIn('check', self.latest_plan(episode)['focus'])

        view = mcp.dispatch(self.m, 'memory_get', {'view': 'focus', 'id': episode, 'max_chars': 20000})
        self.assertEqual(view['state'], 'awaiting_check')
        self.assertEqual([item['statement'] for item in view['hypotheses']], [H1['statement']])
        self.assertIn('report', view)
        overall = mcp.dispatch(self.m, 'memory_get', {'view': 'focus', 'max_chars': 20000})
        self.assertEqual(overall, self.focus.report(self.m))
        self.assertEqual((overall['problems'], overall['basis']), (0, REPORT_BASIS))

        # An agent cannot write under the name of the orchestrator that records the check results.
        with self.assertRaises(InvalidRecord) as refused:
            mcp.dispatch(self.m, 'memory_write', {'operation': 'progress', 'request_key': 'orchestrator-name', 'data': {
                'episode_id': episode, 'expected_version': self.m.episode(episode)['version'], 'actor': 'Focus_Orchestrator',
                'payload': {'next_action': 'Start the attempts.', 'reason': 'The agent claims the name.'}}})
        self.assertEqual(str(refused.exception), FOCUS_ACTOR_RESERVED)


class HypothesisTests(FocusFixture):
    def test_distinct_hypotheses_are_kept_and_a_normalised_duplicate_is_refused(self):
        self.assertIn('hypothesis', core.KINDS)
        self.assertEqual(self.focus.normalise('  The Cache, is   STALE! '), 'the cache is stale')
        self.assertEqual(self.focus.normalise('Le cache est PÉRIMÉ !'), 'le cache est périmé')
        self.assertEqual(self.focus.normalise('snake_case value 2'), 'snake case value 2')
        episode = self.plan()
        first = self.focus.hypothesis(self.m, episode, request_key='h1', actor='assistant', **H1)
        again = self.focus.hypothesis(self.m, episode, request_key='h1', actor='assistant', **H1)
        self.assertEqual((again['id'], again['duplicate']), (first['id'], True))
        with self.assertRaises(InvalidRecord) as refused:
            self.focus.hypothesis(self.m, episode, request_key='h1-again', actor='claude-worker',
                                  statement='the LOADER reads a cached copy, of the value',
                                  approach='Clear the cache before loading.')
        self.assertEqual(str(refused.exception), DUPLICATE_HYPOTHESIS)
        with self.assertRaises(InvalidRecord) as raw:
            self.m.record(episode, 'hypothesis', {'statement': 'The loader reads a cached copy of the value',
                                                  'approach': 'Another approach.', 'state': 'open'},
                          expected_version=self.m.episode(episode)['version'], request_key='raw-duplicate', actor='assistant')
        self.assertEqual(str(raw.exception), DUPLICATE_HYPOTHESIS)
        self.focus.hypothesis(self.m, episode, request_key='h2', actor='assistant', **H2)
        hypotheses = self.focus.view(self.m, episode)['hypotheses']
        self.assertEqual([item['statement'] for item in hypotheses], [H1['statement'], H2['statement']])
        self.assertEqual({item['state'] for item in hypotheses}, {'open'})
        # Letters outside ASCII are letters, so these two statements differ.
        self.focus.hypothesis(self.m, episode, request_key='accented', actor='assistant',
                              statement='Le cache est périmé.', approach='Vider le cache.')
        self.focus.hypothesis(self.m, episode, request_key='plain', actor='assistant',
                              statement='Le cache est p rim.', approach='Vider le cache.')
        # The duplicate rule holds within one work item. Another item may state the same hypothesis.
        other = self.plan('Other item')
        self.focus.hypothesis(self.m, other, request_key='other-h1', actor='assistant', **H1)
        self.assertEqual([item['statement'] for item in self.focus.view(self.m, other)['hypotheses']], [H1['statement']])
        for payload in ({'statement': '', 'approach': 'An approach.', 'state': 'open'},
                        {'statement': 'x' * 1001, 'approach': 'An approach.', 'state': 'open'},
                        {'statement': 'A new statement.', 'approach': 'x' * 2001, 'state': 'open'},
                        {'statement': 'A new statement.', 'approach': 'An approach.', 'state': 'likely'},
                        {'statement': 'A new statement.', 'approach': 'An approach.', 'state': 'open', 'attempt': 4},
                        {'statement': 'A new statement.', 'approach': 'An approach.', 'state': 'open', 'owner': 'assistant'}):
            with self.subTest(payload=payload), self.assertRaises(InvalidRecord):
                self.focus.validate_hypothesis(payload)

    def test_only_the_orchestrator_records_the_result_of_a_hypothesis(self):
        episode = self.plan()
        self.propose(episode, (H1,))
        first = self.focus.view(self.m, episode)['hypotheses'][0]['id']
        confirmed = {**H1, 'state': 'confirmed', 'evidence_summary': 'The check passed with exit code 0.'}
        with self.assertRaises(InvalidRecord) as refused:
            self.m.record(episode, 'hypothesis', confirmed, expected_version=self.m.episode(episode)['version'],
                          request_key='forged-result', actor='assistant', supersedes=first)
        self.assertEqual(str(refused.exception), HYPOTHESIS_RESULT_ONLY)
        with self.assertRaises(InvalidRecord) as refused:
            self.m.record(episode, 'hypothesis', {**H3, 'state': 'confirmed'}, expected_version=self.m.episode(episode)['version'],
                          request_key='confirmed-at-once', actor='assistant')
        self.assertEqual(str(refused.exception), HYPOTHESIS_STARTS_OPEN)
        self.assertEqual([item['state'] for item in self.focus.view(self.m, episode)['hypotheses']], ['open'])

    def test_a_proposal_by_an_agent_records_the_problem_and_hypotheses_without_a_check(self):
        episode = self.plan()
        result = self.propose(episode)
        self.assertEqual(len(result['hypothesis_ids']), 2)
        focus = self.latest_plan(episode)['focus']
        self.assertEqual((focus['max_attempts'], focus['mode']), (2, 'relay'))
        self.assertNotIn('check', focus)
        self.assertEqual(self.m.read(self.latest_plan(episode)['id'])['actor'], 'assistant')
        with self.assertRaises(InvalidRecord) as refused:
            self.propose(episode, (H3, H2), key='propose-again', problem='Another problem.')
        self.assertEqual(str(refused.exception), DUPLICATE_HYPOTHESIS)
        # A refused proposal records nothing: neither the new problem nor the hypothesis before the duplicate.
        self.assertEqual(self.latest_plan(episode)['focus']['problem'], PROBLEM)
        self.assertEqual(len(self.focus.view(self.m, episode)['hypotheses']), 2)

    def test_each_step_needs_the_record_before_it(self):
        bare = self.m.start('No plan', 'Nothing is planned.', 'code', 'Nothing.', subject='code')['id']
        with self.assertRaises(InvalidRecord) as refused:
            self.focus.propose(self.m, bare, problem=PROBLEM, actor='assistant', request_key='no-plan')
        self.assertEqual(str(refused.exception), 'Record a work plan before proposing a focused problem.')
        episode = self.plan()
        view = self.focus.view(self.m, episode)
        self.assertEqual((view['state'], view['focus'], view['hypotheses'], view['attempts'], view['selected_run']),
                         ('none', None, [], [], None))
        with self.assertRaises(InvalidRecord) as refused:
            self.set_check(episode)
        self.assertEqual(str(refused.exception), 'Record the focused problem before setting its check.')
        with self.assertRaises(InvalidRecord) as refused:
            self.start(episode)
        self.assertEqual(str(refused.exception), 'Record the focused problem before starting it.')
        self.assertEqual((self.run_count(), self.receipts('FocusStarted')), (0, []))

    def test_start_requires_the_user_a_check_an_open_hypothesis_and_an_unchanged_check_file(self):
        episode = self.plan()
        self.propose(episode, ())
        with self.assertRaises(InvalidRecord) as refused:
            self.start(episode, actor='assistant')
        self.assertEqual(str(refused.exception), START_USER_ONLY)
        with self.assertRaises(InvalidRecord) as refused:
            self.start(episode, key='no-check')
        self.assertEqual(str(refused.exception), CHECK_MISSING)
        self.set_check(episode)
        with self.assertRaises(InvalidRecord) as refused:
            self.start(episode, key='no-hypothesis')
        self.assertEqual(str(refused.exception), HYPOTHESIS_MISSING)
        self.focus.hypothesis(self.m, episode, request_key='h1', actor='assistant', **H1)
        (self.project / CHECK_FILE).write_text(CHECK_SCRIPT + '# A later edit.\n')
        with self.assertRaises(InvalidRecord) as refused:
            self.start(episode, key='uncommitted-check')
        self.assertEqual(str(refused.exception), CHECK_FILE_CHANGED)
        self.git('commit', '-q', '-am', 'Edit the check')
        with self.assertRaises(InvalidRecord) as refused:
            self.start(episode, key='changed-check')
        self.assertEqual(str(refused.exception), CHECK_FILE_CHANGED)
        self.assertEqual(self.run_count(), 0)
        self.assertEqual(self.receipts('FocusStarted'), [])

    def test_a_refused_request_at_start_leaves_no_run_and_no_receipt(self):
        episode = self.prepared((H2,), max_attempts=1)
        (self.project / 'src/app.py').write_text('VALUE = 5\n')
        with self.assertRaises(InvalidRecord) as refused:
            self.start(episode)
        self.assertTrue(str(refused.exception).startswith('Uncommitted changes exist inside the plan paths: src/app.py.'),
                        str(refused.exception))
        self.assertEqual((self.run_count(), self.receipts('FocusStarted'), self.receipts('FocusAttemptRequested')), (0, [], []))
        self.assertEqual(self.focus.view(self.m, episode)['state'], 'ready')
        # Nothing was recorded, so the same start succeeds once the edit is gone.
        self.git('checkout', '--', 'src/app.py')
        self.start(episode)
        self.assertEqual(self.focus.view(self.m, episode)['state'], 'merged')
        self.assertEqual(len(self.receipts('FocusStarted')), 1)


class RelayTests(FocusFixture):
    def test_a_relay_whose_first_attempt_fails_merges_the_second(self):
        episode = self.prepared()
        self.attempts = {1: change('VALUE = 3\n'), 2: change('VALUE = 2\n')}
        view = self.start(episode)
        self.assertEqual((self.project / 'src/app.py').read_text(), 'VALUE = 2\n')
        merged = self.receipts('DelegationMerged')
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]['actor'], FOCUS_ACTOR)

        view = self.focus.view(self.m, episode)
        self.assertEqual(view['state'], 'merged')
        attempts = view['attempts']
        self.assertEqual([item['attempt'] for item in attempts], [1, 2])
        self.assertEqual([item['host'] for item in attempts], [self.work_host, self.other_host])
        self.assertEqual([item['check_passed'] for item in attempts], [False, True])
        self.assertEqual([item['exit_code'] for item in attempts], [1, 0])
        self.assertEqual([item['selected'] for item in attempts], [False, True])
        self.assertEqual(attempts[1]['review_state'], 'pass')
        self.assertEqual([item['merge_state'] for item in attempts], ['discarded', 'merged'])
        self.assertEqual(view['selected_run'], attempts[1]['run_id'])
        self.assertEqual(merged[0]['run_id'], attempts[1]['run_id'])

        hypotheses = view['hypotheses']
        self.assertEqual([item['state'] for item in hypotheses], ['ruled_out', 'confirmed'])
        self.assertEqual(hypotheses[0]['evidence_summary'],
                         'The check failed with exit code 1. Last output line: Observed VALUE = 3.')
        self.assertEqual(hypotheses[0]['run_id'], attempts[0]['run_id'])

        first = reviews.read(self.m, attempts[0]['run_id'])
        self.assertFalse((self.project / first['workspace']).exists())
        self.assertEqual(self.git('branch', '--list', first['branch']), '')
        self.assertEqual(len(self.work_runs(episode)), 2)
        self.assertEqual(len(self.review_runs(episode)), 1)
        self.assertEqual((len(self.receipts('FocusCheckRecorded')), len(self.receipts('FocusSelected')),
                          len(self.receipts('FocusCompleted'))), (2, 1, 1))
        prompts = {item['attempt']: section(item['prompt']) for item in self.prompts if item['attempt']}
        self.assertEqual(prompts[2], ['Focused problem: ' + PROBLEM,
                                      'Hypothesis of this attempt: ' + H2['statement'] + ' Approach: ' + H2['approach'],
                                      'Ruled out hypotheses: 1. ' + H1['statement'] + ' Evidence: The check failed with exit code 1. '
                                      'Last output line: Observed VALUE = 3.'])
        # A repeated start with the same key starts nothing new.
        self.start(episode)
        self.assertEqual(len(self.work_runs(episode)), 2)
        self.assertEqual(len(self.receipts('FocusStarted')), 1)

    def test_a_new_start_after_a_block_starts_again_with_the_open_hypothesis(self):
        episode = self.prepared()
        self.attempts = {1: change('VALUE = 3\n'), 2: change('VALUE = 4\n')}
        self.start(episode)
        self.assertEqual(self.focus.view(self.m, episode)['state'], 'blocked')
        with self.assertRaises(InvalidRecord) as refused:
            self.start(episode, key='start-without-hypothesis')
        self.assertEqual(str(refused.exception), HYPOTHESIS_MISSING)
        third = self.focus.hypothesis(self.m, episode, request_key='h3', actor='assistant', **H3)
        self.attempts = {1: change('VALUE = 2\n')}
        self.start(episode, key='start-again')
        view = self.focus.view(self.m, episode)
        self.assertEqual(len(self.receipts('FocusStarted')), 2)
        self.assertEqual(view['state'], 'merged')
        self.assertEqual([(item['attempt'], item['hypothesis_id'], item['host']) for item in view['attempts']],
                         [(1, third['id'], self.work_host)])
        self.assertEqual([item['state'] for item in view['hypotheses']], ['ruled_out', 'ruled_out', 'confirmed'])
        self.assertEqual(section([item for item in self.prompts if item['attempt']][-1]['prompt'])[2],
                         'Ruled out hypotheses: 1. ' + H1['statement'] + ' Evidence: The check failed with exit code 1. '
                         'Last output line: Observed VALUE = 3. 2. ' + H2['statement'] + ' Evidence: The check failed with '
                         'exit code 1. Last output line: Observed VALUE = 4.')
        report = self.focus.report(self.m, episode_id=episode)
        self.assertEqual((report['problems'], report['solved'], report['blocked'], report['running']), (2, 1, 1, 0))

    def test_ruled_out_hypotheses_reach_the_next_prompt_within_600_characters(self):
        filler = 'the loader keeps an earlier copy of the settings in memory and '
        long = [{'statement': f'The value stays wrong in case {name} because ' + filler * 6 + 'nothing refreshes it.',
                 'approach': f'Apply the {name} repair to the source file.'} for name in ('alpha', 'beta', 'gamma')]
        episode = self.prepared(long, max_attempts=3)
        self.attempts = {1: change('VALUE = 3\n'), 2: change('VALUE = 4\n'), 3: change('VALUE = 2\n')}
        self.start(episode)
        prompts = {item['attempt']: item for item in self.prompts if item['attempt']}
        self.assertEqual(sorted(prompts), [1, 2, 3])
        self.assertEqual([prompts[n]['host'] for n in (1, 2, 3)], [self.work_host, self.other_host, self.work_host])

        self.assertEqual(section(prompts[1]['prompt'])[1],
                         cut('Hypothesis of this attempt: ' + long[0]['statement'] + ' Approach: ' + long[0]['approach'], 300))
        for number in (1, 2, 3):
            lines = section(prompts[number]['prompt'])
            self.assertEqual(lines[0], 'Focused problem: The value in src/app.py is wrong after loading.')
            self.assertTrue(lines[1].startswith('Hypothesis of this attempt: ' + long[number - 1]['statement'][:120]), lines[1])
            self.assertTrue(lines[2].startswith('Ruled out hypotheses: '), lines[2])
            self.assertLessEqual(len(lines[1]), 300)
            self.assertLessEqual(len(lines[2]), 600)
            self.assertLessEqual(sum(len(line) for line in lines), 1200)
        self.assertEqual(section(prompts[1]['prompt'])[2], 'Ruled out hypotheses: None.')
        second = section(prompts[2]['prompt'])[2]
        self.assertIn('1. ' + long[0]['statement'], second)
        self.assertIn('Evidence: The check failed with exit code 1.', second)
        self.assertNotIn(long[1]['statement'][:40], second)
        third = section(prompts[3]['prompt'])[2]
        self.assertEqual(len(third), 600)
        self.assertTrue(third.endswith('...'))
        self.assertIn('1. ' + long[0]['statement'][:100], third)

        attempts = self.focus.view(self.m, episode)['attempts']
        snapshot = reviews.read(self.m, attempts[2]['run_id'])['snapshot']['focus']
        hypotheses = self.focus.view(self.m, episode)['hypotheses']
        self.assertEqual(snapshot['attempt'], 3)
        self.assertEqual(snapshot['hypothesis']['id'], hypotheses[2]['id'])
        self.assertEqual([item['hypothesis_id'] for item in snapshot['ruled_out']], [hypotheses[0]['id'], hypotheses[1]['id']])
        self.assertEqual(snapshot['check_files'], [CHECK_FILE])
        self.assertEqual((self.project / 'src/app.py').read_text(), 'VALUE = 2\n')

    def test_a_long_problem_is_cut_to_the_line_limit_in_the_prompt(self):
        problem = 'The value in src/app.py is wrong after loading, because ' + 'the loader keeps an older copy of it ' * 12
        episode = self.prepared((H2,), max_attempts=1, problem=problem)
        self.start(episode)
        [prompt] = [item['prompt'] for item in self.prompts if item['attempt']]
        lines = section(prompt)
        self.assertEqual(lines[0], ('Focused problem: ' + problem)[:297] + '...')
        self.assertEqual(len(lines[0]), 300)
        self.assertEqual(lines[2], 'Ruled out hypotheses: None.')

    def test_a_rerouted_attempt_keeps_its_place_and_records_the_reroute(self):
        episode = self.prepared((H2,), max_attempts=1)
        self.attempts = {(1, self.work_host): {'unavailable': 'You have hit your usage limit. Try again in 20 minutes.'},
                         1: change('VALUE = 2\n')}
        self.start(episode)
        attempts = self.focus.view(self.m, episode)['attempts']
        self.assertEqual(len(attempts), 1)
        first = next(run for run in self.work_runs(episode) if run['state'] == 'host_unavailable')
        self.assertEqual((attempts[0]['host'], attempts[0]['rerouted_from']), (self.other_host, first['id']))
        self.assertTrue(attempts[0]['check_passed'])
        rerouted = self.receipts('FocusAttemptRerouted')
        self.assertEqual(len(rerouted), 1)
        self.assertEqual((rerouted[0]['from_run'], rerouted[0]['to_run']), (first['id'], attempts[0]['run_id']))
        self.assertEqual((self.project / 'src/app.py').read_text(), 'VALUE = 2\n')

    def test_an_unavailable_attempt_without_another_host_is_judged_and_one_host_takes_every_attempt(self):
        reviews.remove_host(self.m, self.other_host)
        episode = self.prepared()
        self.attempts = {1: change('VALUE = 3\n'), 2: change('VALUE = 4\n')}
        self.start(episode)
        view = self.focus.view(self.m, episode)
        self.assertEqual([item['host'] for item in view['attempts']], [self.work_host, self.work_host])
        self.assertEqual(view['state'], 'blocked')

        second = self.prepared((H2,), max_attempts=1, title='Second item', key='second-')
        self.attempts = {1: {'unavailable': 'You have hit your usage limit. Try again in 20 minutes.'}}
        self.start(second, key='second-start')
        view = self.focus.view(self.m, second)
        [attempt] = view['attempts']
        run = reviews.read(self.m, attempt['run_id'])
        self.assertEqual((view['state'], attempt['run_state'], attempt['rerouted_from']), ('blocked', 'host_unavailable', None))
        self.assertEqual((attempt['check_passed'], attempt['exit_code'], attempt['changed_lines']), (False, None, None))
        self.assertEqual(view['hypotheses'][0]['evidence_summary'], run['error'][:300])
        self.assertEqual(self.receipts('FocusAttemptRerouted'), [])
        self.assertEqual(self.latest_plan(second)['next_action'], BLOCKED_NEXT_ACTION)

    def test_a_later_relay_attempt_that_cannot_be_requested_blocks_the_item(self):
        episode = self.prepared()
        self.attempts = {1: change('VALUE = 3\n')}
        # While attempt 1 runs, the user edits a file inside the plan paths, so attempt 2 cannot be requested.
        self.before = {1: lambda: (self.project / 'src/app.py').write_text('VALUE = 7\n')}
        self.start(episode)
        view = self.focus.view(self.m, episode)
        plan = self.latest_plan(episode)
        self.assertEqual((view['state'], plan['state'], plan['next_action']), ('blocked', 'blocked', REQUEST_FAILED_NEXT_ACTION))
        self.assertIn('Uncommitted changes exist inside the plan paths: src/app.py.', plan['reason'])
        self.assertEqual([item['state'] for item in view['hypotheses']], ['ruled_out', 'open'])
        self.assertEqual((len(self.work_runs(episode)), len(self.receipts('FocusBlocked'))), (1, 1))

    def test_a_timed_out_check_and_a_long_check_output_are_recorded(self):
        verbose = self.plan('Verbose check')
        self.propose(verbose, (H2,), max_attempts=1, key='verbose-propose')
        self.set_check(verbose, [sys.executable, '-c', "print('a' * 3000)\nprint('b' * 250)\nraise SystemExit(3)"],
                       timeout_seconds=10, key='verbose-check')
        self.start(verbose, key='verbose-start')
        [attempt] = self.focus.view(self.m, verbose)['attempts']
        receipt = self.check_receipt(attempt['run_id'])
        output = 'a' * 3000 + '\n' + 'b' * 250 + '\n'
        self.assertEqual((receipt['check_passed'], receipt['exit_code'], receipt['timed_out']), (False, 3, False))
        self.assertEqual(receipt['output_tail'], output[-2000:])
        self.assertEqual(receipt['evidence_summary'], 'The check failed with exit code 3. Last output line: ' + 'b' * 200 + '.')

        slow = self.plan('Slow check')
        self.propose(slow, (H2,), max_attempts=1, key='slow-propose')
        self.set_check(slow, [sys.executable, '-c', 'import time\ntime.sleep(30)'], timeout_seconds=10, key='slow-check')
        self.start(slow, key='slow-start')
        view = self.focus.view(self.m, slow)
        receipt = self.check_receipt(view['attempts'][0]['run_id'])
        self.assertEqual((receipt['check_passed'], receipt['exit_code'], receipt['timed_out']), (False, None, True))
        self.assertGreaterEqual(receipt['duration_ms'], 10000)
        self.assertLess(receipt['duration_ms'], 25000)
        self.assertEqual(view['hypotheses'][0]['evidence_summary'], 'The check did not finish within 10 seconds.')
        self.assertEqual(self.focus.report(self.m)['checks'], {'passed': 0, 'failed': 2, 'not_run': 0})

    def test_an_attempt_that_edits_a_file_named_by_the_check_is_refused(self):
        episode = self.prepared((H1,), max_attempts=1)
        # The worker leaves the check file out of its report. The refusal rests on the collected diff.
        self.attempts = {1: {'write': {'src/app.py': 'VALUE = 2\n', CHECK_FILE: 'import sys\nsys.exit(0)\n'},
                             'report': work_report(changed_files=['src/app.py'])}}
        self.start(episode)
        attempts = self.focus.view(self.m, episode)['attempts']
        run = reviews.read(self.m, attempts[0]['run_id'])
        self.assertEqual(run['state'], 'scope_violation')
        self.assertIn(CHECK_FILE_REFUSED, run['error'])
        self.assertEqual(run['metrics']['check_files_changed'], [CHECK_FILE])
        self.assertEqual(run['snapshot']['focus']['check_files'], [CHECK_FILE])
        self.assertEqual((attempts[0]['check_passed'], attempts[0]['exit_code']), (False, None))
        self.assertEqual(self.focus.view(self.m, episode)['hypotheses'][0]['state'], 'ruled_out')
        self.assertEqual((self.project / 'src/app.py').read_text(), 'VALUE = 1\n')
        self.assertEqual((self.project / CHECK_FILE).read_text(), CHECK_SCRIPT)
        self.assertEqual(self.receipts('DelegationMerged'), [])
        self.assertEqual(self.review_runs(episode), [])
        self.assertEqual(self.focus.view(self.m, episode)['state'], 'blocked')

    def test_no_passing_attempt_marks_the_item_blocked_with_every_hypothesis(self):
        episode = self.prepared()
        self.attempts = {1: change('VALUE = 3\n'), 2: change('VALUE = 4\n')}
        self.start(episode)
        plan = self.latest_plan(episode)
        self.assertEqual((plan['state'], plan['next_action']), ('blocked', BLOCKED_NEXT_ACTION))
        self.assertEqual(self.m.read(plan['id'])['actor'], FOCUS_ACTOR)
        self.assertIn(H1['statement'], plan['reason'])
        self.assertIn(H2['statement'], plan['reason'])
        blocked = self.receipts('FocusBlocked')
        self.assertEqual(len(blocked), 1)
        self.assertEqual([item['statement'] for item in blocked[0]['hypotheses']], [H1['statement'], H2['statement']])
        self.assertTrue(all(item['evidence_summary'] for item in blocked[0]['hypotheses']))
        view = self.focus.view(self.m, episode)
        self.assertEqual(view['state'], 'blocked')
        self.assertEqual([item['state'] for item in view['hypotheses']], ['ruled_out', 'ruled_out'])
        self.assertEqual(self.review_runs(episode), [])
        self.assertEqual(self.receipts('DelegationMerged'), [])
        for attempt in view['attempts']:
            run = reviews.read(self.m, attempt['run_id'])
            self.assertFalse((self.project / run['workspace']).exists())
            self.assertEqual(attempt['merge_state'], 'discarded')
        self.assertEqual((self.project / 'src/app.py').read_text(), 'VALUE = 1\n')
        self.assertEqual(self.focus_lessons(), [])
        self.assertFalse(self.focus.summary(self.m, episode)['proposed'])
        self.assertEqual(self.focus_lessons(), [])
        self.assertIn('failed_run', [reason['type'] for reason in self.focus.reasons(self.m, episode)])

    def test_a_selected_attempt_that_fails_its_work_review_blocks_the_item(self):
        episode = self.prepared((H2,), max_attempts=1)
        self.reviewers = {'codex': {'verdict': 'changes_required'}, 'claude': {'verdict': 'changes_required'}}
        self.start(episode)
        view = self.focus.view(self.m, episode)
        self.assertEqual(view['state'], 'blocked')
        [attempt] = view['attempts']
        self.assertEqual((attempt['check_passed'], attempt['selected'], attempt['review_state']), (True, True, 'changes_required'))
        [completed] = self.receipts('FocusCompleted')
        self.assertEqual((completed['merge'], completed['review_state'], completed['run_id']),
                         ('not_merged', 'changes_required', attempt['run_id']))
        plan = self.latest_plan(episode)
        self.assertEqual((plan['state'], plan['next_action']), ('blocked', REVIEW_FAILED_NEXT_ACTION))
        self.assertEqual(self.m.read(plan['id'])['actor'], FOCUS_ACTOR)
        self.assertEqual(len(self.receipts('FocusBlocked')), 1)
        self.assertEqual(self.receipts('DelegationMerged'), [])
        self.assertEqual((self.project / 'src/app.py').read_text(), 'VALUE = 1\n')
        self.assertEqual(self.focus_lessons(), [])
        self.assertFalse(self.focus.summary(self.m, episode)['proposed'])
        self.assertIn('failed_run', [reason['type'] for reason in self.focus.reasons(self.m, episode)])
        report = self.focus.report(self.m, episode_id=episode)
        self.assertEqual((report['solved'], report['blocked'], report['reviews']), (0, 1, {'changes_required': 1}))

    def test_in_production_the_selected_attempt_waits_for_the_user(self):
        planning.set_phase(self.m, phase='production', reason='The product serves customers, so the user merges changes.',
                           actor='workspace-user')
        episode = self.prepared((H2,), max_attempts=1)
        self.start(episode)
        view = self.focus.view(self.m, episode)
        self.assertEqual(view['state'], 'waiting_for_user')
        self.assertEqual(view['attempts'][0]['review_state'], 'pass')
        self.assertEqual(self.receipts('DelegationMerged'), [])
        self.assertEqual((self.project / 'src/app.py').read_text(), 'VALUE = 1\n')
        self.assertEqual(self.receipts('FocusCompleted')[0]['merge'], 'waiting_for_user')


class ParallelAndRankingTests(FocusFixture):
    def test_parallel_selects_the_passing_attempt_with_fewer_changed_lines(self):
        episode = self.prepared(mode='parallel')
        self.attempts = {1: change('VALUE = 2\n# First note.\n# Second note.\n# Third note.\n'), 2: change('VALUE = 2\n')}
        self.start(episode)
        view = self.focus.view(self.m, episode)
        attempts = view['attempts']
        self.assertEqual([item['check_passed'] for item in attempts], [True, True])
        self.assertEqual([item['changed_lines'] for item in attempts], [5, 2])
        self.assertEqual([item['selected'] for item in attempts], [False, True])
        self.assertEqual(self.receipts('FocusSelected')[0]['attempt'], 2)
        self.assertEqual([item['merge_state'] for item in attempts], ['discarded', 'merged'])
        self.assertEqual(len(self.review_runs(episode)), 1)
        self.assertEqual((self.project / 'src/app.py').read_text(), 'VALUE = 2\n')

    def test_parallel_without_a_passing_attempt_blocks_and_discards_every_attempt(self):
        episode = self.prepared(mode='parallel')
        self.attempts = {1: change('VALUE = 3\n'), 2: change('VALUE = 4\n')}
        self.start(episode)
        view = self.focus.view(self.m, episode)
        self.assertEqual(view['state'], 'blocked')
        self.assertEqual([item['check_passed'] for item in view['attempts']], [False, False])
        self.assertEqual([item['merge_state'] for item in view['attempts']], ['discarded', 'discarded'])
        for attempt in view['attempts']:
            run = reviews.read(self.m, attempt['run_id'])
            self.assertFalse((self.project / run['workspace']).exists())
            self.assertEqual(self.git('branch', '--list', run['branch']), '')
        self.assertEqual((self.receipts('FocusSelected'), self.review_runs(episode)), ([], []))
        self.assertEqual(self.latest_plan(episode)['next_action'], BLOCKED_NEXT_ACTION)
        self.assertEqual(len(self.receipts('FocusBlocked')), 1)

    def test_parallel_starts_only_when_the_project_has_room_for_both_attempts(self):
        episode = self.prepared(mode='parallel')
        other = self.plan('Other item', state='ready')
        self.hold = True
        waiting = delegation.request_work(self.m, other, request_key='other-work')
        self.hold = False
        with self.assertRaises(Conflict) as refused:
            self.start(episode)
        self.assertEqual(str(refused.exception), PROJECT_LIMIT)
        self.assertEqual((self.run_count(), self.receipts('FocusStarted'), self.receipts('FocusAttemptRequested')), (1, [], []))
        self.assertEqual(self.focus.view(self.m, episode)['state'], 'ready')
        reviews.cancel(self.m, waiting['id'])
        self.start(episode)
        view = self.focus.view(self.m, episode)
        self.assertEqual((view['state'], len(view['attempts'])), ('merged', 2))

    def test_rank_orders_by_passing_check_then_fewer_changed_lines_then_earlier_attempt(self):
        attempts = [{'attempt': 1, 'check_passed': False, 'changed_lines': 1},
                    {'attempt': 2, 'check_passed': True, 'changed_lines': 9},
                    {'attempt': 3, 'check_passed': True, 'changed_lines': None},
                    {'attempt': 4, 'check_passed': True, 'changed_lines': 3},
                    {'attempt': 5, 'check_passed': True, 'changed_lines': 3},
                    {'attempt': 6, 'check_passed': None, 'changed_lines': None}]
        before = [dict(item) for item in attempts]
        ranked = self.focus.rank(attempts)
        self.assertEqual([item['attempt'] for item in ranked], [4, 5, 2, 3, 1, 6])
        self.assertEqual(attempts, before)


class LearningTests(FocusFixture):
    def test_summary_proposes_one_lesson_from_the_passing_approach(self):
        episode = self.prepared()
        self.attempts = {1: change('VALUE = 3\n'), 2: change('VALUE = 2\n')}
        self.start(episode)
        lessons = self.focus_lessons()
        self.assertEqual(len(lessons), 1)
        lesson = self.m.read(lessons[0])
        self.assertEqual(lesson['status'], 'proposed')
        self.assertEqual(lesson['payload']['do'], H2['approach'])
        self.assertTrue(lesson['payload']['because'].startswith('The check passed for this approach in attempt 2.'))
        self.assertIn(H1['statement'], lesson['payload']['because'])
        selected = self.focus.view(self.m, episode)['selected_run']
        self.assertEqual(self.m.read(lesson['evidence'][0]['source_id'])['source_key'], 'focus-check:' + selected)
        again = self.focus.summary(self.m, episode)
        self.assertEqual((again['proposed'], again['lesson_id'], again['duplicate']), (True, lessons[0], True))
        self.assertEqual(self.focus_lessons(), lessons)

    def test_report_totals_come_from_receipts_and_checks_not_from_agent_reports(self):
        episode = self.prepared()
        claim = [{'command': 'python3 tests/check_value.py', 'outcome': 'The check passed.'}]
        # The host logs measure the tokens. One host reports its usage as a list of turns, the other as one object.
        self.attempts = {1: change('VALUE = 3\n', usage={'input_tokens': 10, 'output_tokens': 4}, summary='The value is fixed.',
                                   checks_run=claim),
                         2: change('VALUE = 2\n', usage={'input_tokens': 7, 'output_tokens': 3})}
        self.start(episode)
        report = self.focus.report(self.m, episode_id=episode)
        self.assertEqual((report['problems'], report['solved'], report['blocked'], report['running']), (1, 1, 0, 0))
        self.assertEqual(report['attempts'], 2)
        self.assertEqual(report['hosts'], {self.work_host: 1, self.other_host: 1})
        self.assertEqual(report['checks'], {'passed': 1, 'failed': 1, 'not_run': 0})
        self.assertEqual(report['tokens'], 24)
        self.assertEqual(report['reviews'], {'pass': 1})
        self.assertEqual(report['first_attempt_sufficient'], 0)
        self.assertEqual(report['basis'], REPORT_BASIS)
        runs = [reviews.read(self.m, item['run_id']) for item in self.focus.view(self.m, episode)['attempts']]
        self.assertEqual(report['run_duration_ms'], sum(run['metrics']['duration_ms'] for run in runs))
        self.assertEqual(report['check_duration_ms'], sum(item['duration_ms'] for item in self.receipts('FocusCheckRecorded')))
        item = report['items'][0]
        self.assertFalse(item['first_attempt_sufficient'])
        self.assertEqual([entry['check_passed'] for entry in item['attempts']], [False, True])
        self.assertEqual([entry['tokens'] for entry in item['attempts']], [14, 10])
        # The first worker claimed that the check passed. The report counts the check that Project Memory ran.
        self.assertEqual(runs[0]['report']['checks_run'], claim)
        self.assertEqual(item['attempts'][0]['check_passed'], False)
        overall = self.focus.report(self.m)
        self.assertEqual((overall['problems'], overall['attempts'], overall['checks']), (1, 2, report['checks']))

    def test_report_counts_solved_blocked_and_running_problems_and_first_attempt_success(self):
        solved = self.prepared((H2,), max_attempts=1)
        self.attempts = {1: change('VALUE = 2\n', usage={'input_tokens': 100, 'output_tokens': 20})}
        self.start(solved)
        blocked = self.prepared((H1,), max_attempts=1, title='Second item', key='second-')
        self.attempts = {1: change('VALUE = 3\n', usage={'input_tokens': 30, 'output_tokens': 5})}
        self.start(blocked, key='second-start')
        running = self.prepared((H3,), max_attempts=1, title='Third item', key='third-')
        self.hold = True
        self.start(running, key='third-start')

        report = self.focus.report(self.m)
        self.assertEqual((report['problems'], report['solved'], report['blocked'], report['running']), (3, 1, 1, 1))
        self.assertEqual((report['attempts'], report['hosts'], report['checks']),
                         (2, {self.work_host: 2}, {'passed': 1, 'failed': 1, 'not_run': 0}))
        self.assertEqual((report['tokens'], report['reviews'], report['first_attempt_sufficient']), (155, {'pass': 1}, 1))
        self.assertEqual([(entry['episode_id'], entry['state'], entry['first_attempt_sufficient']) for entry in report['items']],
                         [(solved, 'merged', True), (blocked, 'blocked', False), (running, 'running', False)])
        self.assertEqual([[attempt['tokens'] for attempt in entry['attempts']] for entry in report['items']], [[120], [35], []])
        self.assertEqual([[attempt['review_state'] for attempt in entry['attempts']] for entry in report['items']],
                         [['pass'], [None], []])
        single = self.focus.report(self.m, episode_id=blocked)
        self.assertEqual((single['problems'], single['blocked'], single['tokens'], single['first_attempt_sufficient']), (1, 1, 35, 0))


if __name__ == '__main__':
    unittest.main()
