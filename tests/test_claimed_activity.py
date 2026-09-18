"""Tool activity of a claimed work item is assigned without a bound decision.

A decision binding ends at the first outcome, even a partial one. On 18 September 2026 the Stop hook then blocked a
session with 14 unassigned tool calls that all served the work item the session had claimed. A call without a bound
decision now names that item in its receipt payload, and the episode_id column stays empty, so check snapshots and
their signatures do not change.
"""
from memory_module import codex_host, coverage
from memory_module.mcp import write
from tests.test_sessions import Fixture

SESSION = 'claude-session-claimed-1'


class ClaimedActivityTests(Fixture):
    def claim(self, state='in_progress', episode_id=None, version=None):
        data = {'payload': {'state': state, 'next_action': 'Repair the parser.', 'autonomy': 'act', 'scope': 'The parser only.',
                            'reason': 'The user asked for it.'}, 'actor': 'assistant'}
        source = self.m.source('ask-' + state, 'Request', 'The user asked.', 'Repair the parser.', 'user', subject='code')['id']
        data['evidence'] = [{'source_id': source, 'reason': 'The request.'}]
        if episode_id:
            data.update(episode_id=episode_id, expected_version=version)
        else:
            data.update(title='Repair the parser', objective='Parse again.', criterion='The parser works.', subject='code')
        return write(self.m, 'plan', 'plan-' + state, data, SESSION)

    def call(self, identifier, session=SESSION):
        codex_host.capture(self.m, {'hook_event_name': 'PreToolUse', 'session_id': session, 'tool_name': 'Bash',
                                    'tool_use_id': identifier, 'tool_input': {'command': 'make build'}, 'cwd': str(self.root)}, host='claude')
        return self.m.db.execute("SELECT episode_id,payload FROM host_receipts WHERE tool_use_id=? AND event_name='PreToolUse'",
                                 (identifier,)).fetchone()

    def unassigned(self, session=SESSION):
        return [i for i in coverage.inspect(self.m, session)['issues'] if i['type'] == 'activity_unassigned']

    def test_a_call_of_a_claimed_item_is_assigned_without_entering_check_snapshots(self):
        episode = self.claim()['episode_id']
        row = self.call('toolu_claimed_1')
        self.assertIsNone(row['episode_id'])
        self.assertIn(episode, row['payload'])
        self.assertEqual(self.unassigned(), [])

    def test_a_call_without_a_claimed_item_still_counts(self):
        self.claim()
        self.call('toolu_other_1', session='claude-session-other-1')
        self.assertEqual(self.unassigned('claude-session-other-1')[0]['count'], 1)

    def test_a_call_after_the_item_left_progress_still_counts(self):
        created = self.claim()
        self.claim(state='ready', episode_id=created['episode_id'], version=self.m.episode(created['episode_id'])['version'])
        self.call('toolu_after_1')
        self.assertEqual(self.unassigned()[0]['count'], 1)
