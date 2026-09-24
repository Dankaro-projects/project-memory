"""The agent records a piece of work in two writes: its plan and one log entry.

On 24 September 2026 recording one small fix took six writes (a plan, a plan revision, a source, a decision, an action
and an outcome), two further writes were refused because a decision and then an action were missing, and the Stop hook
asked for an intent checkpoint after every turn. A log entry records the decision, its action and its outcome at once,
and a record written in a turn assesses that turn.
"""
import shutil
import tempfile
import unittest
from pathlib import Path

from memory_module import Memory, InvalidRecord, codex_host
from memory_module.coverage import inspect
from memory_module.mcp import write
from memory_module.planning import latest

SESSION = 's'


class RecordLessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.m = Memory.create(Path(self.temp.name) / 'memory.sqlite', 'Record less', ['Keep records short.'])
        codex_host.initialize(self.m)
        self.source = self.m.source('ask', 'Request', 'The user asks for a fix.', 'Fix the parser.', 'user', subject='code')['id']
        self.evidence = [{'source_id': self.source, 'reason': 'The user asked for the fix.'}]

    def tearDown(self):
        self.m.close()
        shutil.rmtree(self.temp.name, ignore_errors=True)

    def capture(self, name, **kw):
        codex_host.capture(self.m, {'hook_event_name': name, 'session_id': SESSION, 'turn_id': '1', **kw})

    def plan(self):
        return write(self.m, 'plan', 'plan', {'title': 'Fix the parser', 'objective': 'The parser accepts tabs.',
                                              'criterion': 'The parser test passes.', 'subject': 'code', 'actor': 'agent',
                                              'evidence': self.evidence,
                                              'payload': {'state': 'ready', 'scope': 'The parser only.', 'autonomy': 'act',
                                                          'next_action': 'Fix it.', 'reason': 'The user asked.'}},
                     session_id=SESSION)

    def log(self, episode_id, key='log', **payload):
        entry = {'decision': 'Accept tabs as whitespace.', 'why': 'The user files use tabs.', 'action': 'Changed the tokenizer.',
                 'observed': 'The parser test passes.', 'assessment': 'good', 'completion': 'complete', **payload}
        return write(self.m, 'log', key, {'episode_id': episode_id, 'expected_version': self.m.episode(episode_id)['version'],
                                          'actor': 'agent', 'evidence': self.evidence, 'payload': entry}, session_id=SESSION)

    def test_one_log_entry_records_the_decision_its_action_and_its_outcome(self):
        ep = self.plan()['episode_id']
        result = self.log(ep)
        decision, action, outcome = (latest(self.m, ep, kind) for kind in ('decision', 'action', 'outcome'))
        self.assertEqual(decision['decision'], 'Accept tabs as whitespace.')
        self.assertEqual(action['action'], 'Changed the tokenizer.')
        self.assertEqual(outcome['completion'], 'complete')
        self.assertEqual(result['decision_id'], decision['id'])
        self.assertEqual(self.m.read(outcome['id'])['decision_id'], decision['id'])

    def test_a_second_log_entry_replaces_the_current_decision(self):
        ep = self.plan()['episode_id']
        first = self.log(ep)
        second = self.log(ep, key='log-2', decision='Normalise tabs first.', observed='Both tests pass.')
        self.assertEqual(self.m.read(second['decision_id'])['supersedes'], first['decision_id'])

    def test_a_log_entry_needs_the_six_fields(self):
        ep = self.plan()['episode_id']
        with self.assertRaises(InvalidRecord):
            write(self.m, 'log', 'bad', {'episode_id': ep, 'expected_version': self.m.episode(ep)['version'], 'actor': 'agent',
                                         'evidence': self.evidence, 'payload': {'decision': 'Only this.'}}, session_id=SESSION)

    def test_a_small_fix_takes_two_writes_and_leaves_nothing_to_assess(self):
        self.capture('UserPromptSubmit', prompt='Fix the parser so it accepts tabs.')
        self.capture('PreToolUse', tool_name='Edit', tool_use_id='e1', tool_input={'file_path': 'parser.py'})
        self.capture('PostToolUse', tool_name='Edit', tool_use_id='e1', tool_response={'ok': True})
        ep = self.plan()['episode_id']
        self.log(ep)
        self.assertEqual(inspect(self.m, SESSION)['issues'], [])

    def test_a_turn_without_any_record_still_needs_an_assessment(self):
        self.capture('UserPromptSubmit', prompt='Change the parser.')
        self.capture('PreToolUse', tool_name='Edit', tool_use_id='e1', tool_input={'file_path': 'parser.py'})
        self.capture('PostToolUse', tool_name='Edit', tool_use_id='e1', tool_response={'ok': True})
        self.assertIn('intent_unassessed', {issue['type'] for issue in inspect(self.m, SESSION)['issues']})


if __name__ == '__main__':
    unittest.main()


class OutsideEffectTests(RecordLessTests):
    def call(self, identifier, tool, tool_input):
        self.capture('PreToolUse', tool_name=tool, tool_use_id=identifier, tool_input=tool_input)

    def unconfirmed(self):
        return codex_host.status(self.m, SESSION)['unconfirmed_total']

    def test_a_local_call_without_a_result_needs_no_reconciliation(self):
        self.call('edit', 'Edit', {'file_path': 'parser.py'})
        self.call('test', 'Bash', {'command': '.venv/bin/python -m unittest -q'})
        self.call('commit', 'Bash', {'command': 'git commit -m "Fix the parser"'})
        self.assertEqual(self.unconfirmed(), 0)

    def test_a_call_with_an_effect_outside_the_repository_still_does(self):
        self.call('push', 'Bash', {'command': 'git push origin main'})
        self.call('publish', 'Bash', {'command': 'cd dist && uv publish'})
        self.call('send', 'mcp__claude_ai_Gmail__send_message', {'to': 'someone'})
        self.assertEqual(self.unconfirmed(), 3)

    def test_the_classification(self):
        external = codex_host.external_effect
        self.assertTrue(external('Bash', {'command': 'gh pr create --fill'}))
        self.assertTrue(external('Bash', {'command': 'curl -X POST https://example.com'}))
        self.assertTrue(external('Bash', {'command': 'npm publish'}))
        self.assertFalse(external('Bash', {'command': 'npm test'}))
        self.assertFalse(external('mcp__claude_ai_Supabase__list_tables', {}))
        self.assertTrue(external('mcp__claude_ai_Supabase__execute_sql', {}))
        self.assertFalse(external('mcp__project_memory__memory_write', {}))
        self.assertFalse(external('Write', {'file_path': 'a.txt'}))
