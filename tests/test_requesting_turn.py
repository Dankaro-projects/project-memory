"""A check snapshot shows the turn that requested it.

A criterion of a check often speaks about the turn that asked for the check: that it changed no code, or which tool
calls it made. The snapshot held no evidence for either, because its receipts are selected by work item and come
from the sessions that built it. On 18 September 2026 a check of wave 10c confirmed every build criterion and
returned uncertain on exactly those two conditions. The requesting turn is now part of a requested check, outside
the snapshot signature, so that reading it does not make the check stale.
"""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from memory_module import Memory, cli, codex_host, reviews
from memory_module.install import setup
from memory_module.workspace import action


def git(folder, *args):
    subprocess.run(['git', '-C', str(folder), *args], check=True, capture_output=True, text=True)


class RequestingTurnTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name).resolve() / 'project'
        self.root.mkdir()
        git(self.root, 'init', '-q', '-b', 'main')
        git(self.root, 'config', 'user.email', 'test@example.invalid')
        git(self.root, 'config', 'user.name', 'Test')
        (self.root / 'parser.py').write_text('VALUE = 1\n', encoding='utf-8')
        git(self.root, 'add', 'parser.py')
        git(self.root, 'commit', '-q', '-m', 'Start')
        info = setup(self.root, requirements=['Keep the parser small.'])
        self.m = Memory(info['database'])
        reviews.configure(self.m, self.root, 'codex')
        self.episode = self.item('fixture')

    def item(self, key):
        return action(self.m, 'plan', {
            'title': 'Change the parser', 'objective': 'Set the value to 2.', 'criterion': 'parser.py sets VALUE to 2.',
            'subject': 'code', 'payload': {'state': 'ready', 'next_action': 'Check the parser.', 'autonomy': 'act',
                                           'scope': 'Change the parser only.', 'reason': 'The user requests the change.'}},
            key)['episode_id']

    def tearDown(self):
        self.m.close()
        self.temp.cleanup()

    def call(self, session, tool, event='PreToolUse', turn='turn-1'):
        codex_host.receipt(self.m, session_id=session, turn_id=turn, event_name=event,
                           tool_use_id=tool + turn + event, tool_name=tool, payload={'tool_name': tool})

    def turn(self, session=''):
        return reviews.requesting_turn(self.m, session, str(self.root))

    def test_the_tool_calls_of_the_requesting_session_are_named_and_counted(self):
        self.call('session-asking', 'Read')
        self.call('session-asking', 'Read', event='PostToolUse')
        self.call('session-asking', 'Bash')
        self.call('session-building', 'Edit')
        turn = self.turn('session-asking')
        self.assertEqual(turn['session_id'], 'session-asking')
        self.assertEqual(turn['tool_calls_total'], 3)
        self.assertEqual(turn['tools_used'], {'Read': 2, 'Bash': 1})
        self.assertEqual({call['turn_id'] for call in turn['tool_calls']}, {'turn-1'})
        self.assertNotIn('Edit', turn['tools_used'])

    def test_the_uncommitted_state_of_the_checked_folder_is_reported(self):
        clean = self.turn('session-asking')['working_tree']
        self.assertEqual((clean['read'], clean['clean'], clean['changed_paths']), (True, True, []))
        (self.root / 'parser.py').write_text('VALUE = 2\n', encoding='utf-8')
        changed = self.turn('session-asking')['working_tree']
        self.assertEqual((changed['read'], changed['clean'], changed['changed_total']), (True, False, 1))
        self.assertTrue(any('parser.py' in line for line in changed['changed_paths']))

    def test_a_folder_that_is_not_a_repository_reports_why_it_could_not_be_read(self):
        outside = Path(self.temp.name).resolve() / 'not-a-repository'
        outside.mkdir()
        tree = reviews.requesting_turn(self.m, 'session-asking', str(outside))['working_tree']
        self.assertFalse(tree['read'])
        self.assertTrue(tree['reason'])

    def test_a_request_without_a_session_says_that_no_tool_calls_are_recorded(self):
        turn = self.turn()
        self.assertIsNone(turn['session_id'])
        self.assertEqual((turn['tool_calls_total'], turn['tool_calls'], turn['tools_used']), (0, [], {}))
        self.assertIn('unknown rather than unmet', turn['meaning'])
        self.assertTrue(turn['working_tree']['read'])

    def test_the_tool_calls_are_bounded_and_say_how_many_are_left_out(self):
        for index in range(reviews.REQUESTING_TURN_CALLS + 5):
            self.call('session-long', 'Bash', turn='turn-' + str(index))
        turn = self.turn('session-long')
        self.assertEqual(turn['tool_calls_total'], reviews.REQUESTING_TURN_CALLS + 5)
        self.assertEqual(len(turn['tool_calls']), reviews.REQUESTING_TURN_CALLS)
        self.assertEqual(turn['tool_calls_omitted'], 5)
        self.assertEqual(turn['tool_calls'][-1]['turn_id'], 'turn-' + str(reviews.REQUESTING_TURN_CALLS + 4))

    def test_a_requested_check_carries_the_requesting_turn_without_changing_its_signature(self):
        _, first = reviews.snapshot(self.m, self.episode, 'outcome')
        self.assertNotIn('requesting_turn', reviews.snapshot(self.m, self.episode, 'outcome')[0])
        self.call('session-asking', 'Bash')
        run = reviews.request(self.m, self.episode, request_key='check', session_id='session-asking', retry=True)
        self.assertEqual(run['snapshot']['requesting_turn']['tool_calls_total'], 1)
        self.assertEqual(run['snapshot']['requesting_turn']['session_id'], 'session-asking')
        self.assertEqual(run['signature'], first)
        self.assertEqual(reviews.snapshot(self.m, self.episode, 'outcome')[1], first)

    def test_the_requesting_turn_reaches_the_reviewer_and_its_absence_is_explained(self):
        self.call('session-asking', 'Bash')
        run = reviews.request(self.m, self.episode, request_key='check', session_id='session-asking', retry=True)
        folder = Path(self.temp.name) / 'packet'
        folder.mkdir()
        packet = reviews.evidence_manifest(run['snapshot'], folder)
        self.assertEqual(packet['requesting_turn']['tools_used'], {'Bash': 1})
        self.assertEqual(reviews.snapshot_notes(run['snapshot']), [])
        older = {key: value for key, value in run['snapshot'].items() if key != 'requesting_turn'}
        legacy = reviews.evidence_manifest(older, folder)
        self.assertNotIn('requesting_turn', legacy)
        self.assertIn('unknown rather than unmet', ''.join(reviews.snapshot_notes(older)))
        self.assertIn('omit constraint_checks', ''.join(reviews.snapshot_notes({})))


    def review(self, episode, *arguments):
        """Run the review command as a user would, without launching a host process."""
        with mock.patch.object(reviews, 'launch'):
            code = cli.main(['review', '--db', str(self.m.path), '--project', str(self.root),
                             '--episode', episode, '--retry', *arguments])
        self.assertEqual(code, 0)
        latest = self.m.db.execute('SELECT id FROM review_runs ORDER BY rowid DESC LIMIT 1').fetchone()[0]
        return reviews.read(self.m, latest)

    def test_the_review_command_names_the_session_that_requested_the_check(self):
        self.call('session-command-line', 'Bash')
        self.call('session-command-line', 'Bash', event='PostToolUse')
        turn = self.review(self.episode, '--session', 'session-command-line')['snapshot']['requesting_turn']
        self.assertEqual(turn['session_id'], 'session-command-line')
        self.assertEqual((turn['tool_calls_total'], turn['tools_used']), (2, {'Bash': 2}))

    def test_the_review_command_still_works_without_a_session(self):
        turn = self.review(self.item('second'))['snapshot']['requesting_turn']
        self.assertIsNone(turn['session_id'])
        self.assertEqual(turn['tool_calls_total'], 0)
        self.assertTrue(turn['working_tree']['read'])


if __name__ == '__main__':
    unittest.main()
