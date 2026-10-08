"""A log records the host that wrote it, so a Claude session is not reviewed as Codex."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from memory_module import Memory, codex_host, hosts, reviews
from memory_module.mcp import write

SESSION = 'claude-session'
CODEX_SESSION = 'codex-session'


class HostAttributionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name).resolve()
        environment = patch.dict(os.environ, {'PROJECT_MEMORY_CODEX_BIN': sys.executable,
                                              'PROJECT_MEMORY_CLAUDE_BIN': sys.executable})
        environment.start()
        self.addCleanup(environment.stop)
        self.m = Memory.create(self.root / 'memory.sqlite', 'Host attribution', ['Keep the recorded host.'])
        codex_host.initialize(self.m)
        reviews.configure(self.m, self.root, 'codex')
        reviews.configure(self.m, self.root, 'claude')
        self.source = self.m.source('ask', 'Request', 'The user asks for a fix.', 'Fix the parser.', 'user', subject='code')['id']
        self.evidence = [{'source_id': self.source, 'reason': 'The user asked for the fix.'}]

    def tearDown(self):
        self.m.close()
        shutil.rmtree(self.temp.name, ignore_errors=True)

    def capture(self, session, host='codex'):
        codex_host.capture(self.m, {'hook_event_name': 'UserPromptSubmit', 'session_id': session,
                                    'prompt_id': session + '-prompt', 'prompt': 'Fix the parser so it accepts tabs.'},
                           host=host)

    def plan(self, session):
        return write(self.m, 'plan', 'plan-' + session, {'title': 'Fix the parser', 'objective': 'The parser accepts tabs.',
                                                         'criterion': 'The parser test passes.', 'subject': 'code', 'actor': 'agent',
                                                         'evidence': self.evidence,
                                                         'payload': {'state': 'ready', 'scope': 'The parser only.', 'autonomy': 'act',
                                                                     'next_action': 'Fix it.', 'reason': 'The user asked.'}},
                     session_id=session)['episode_id']

    def log(self, episode_id, session, key):
        entry = {'decision': 'Accept tabs as whitespace.', 'why': 'The user files use tabs.', 'action': 'Changed the tokenizer.',
                 'observed': 'The parser test passes.', 'assessment': 'good', 'completion': 'complete'}
        return write(self.m, 'log', key, {'episode_id': episode_id, 'expected_version': self.m.episode(episode_id)['version'],
                                          'actor': 'agent', 'evidence': self.evidence, 'payload': entry}, session_id=session)

    def bound_payload(self, episode_id):
        row = self.m.db.execute("SELECT payload FROM host_receipts WHERE episode_id=? AND event_name='DecisionBound' ORDER BY rowid DESC LIMIT 1",
                                (episode_id,)).fetchone()
        self.assertIsNotNone(row)
        return json.loads(row['payload'])

    def test_a_claude_log_is_bound_to_claude_and_reviewed_by_another_host(self):
        self.capture(SESSION, host='claude')
        episode = self.plan(SESSION)
        self.log(episode, SESSION, 'claude-log')
        self.assertEqual(self.bound_payload(episode).get('host'), 'claude')
        self.assertEqual(reviews.implementer_host(self.m, episode), 'claude')
        run = reviews.request(self.m, episode, 'outcome', request_key='claude-outcome')
        self.assertEqual(run['snapshot']['implementer_host'], 'claude')
        self.assertEqual(run['host'], 'codex')
        self.assertEqual(run['snapshot']['independence'], 'other_host')
        self.assertNotEqual(run['routing']['reason'], 'same_host')
        self.assertNotEqual([item['host'] for item in run['routing']['hosts']], ['claude'])

    def test_a_missing_host_on_the_bound_receipt_comes_from_the_session(self):
        self.capture(SESSION, host='claude')
        episode = self.plan(SESSION)
        with self.m._write():
            codex_host.receipt(self.m, session_id=SESSION, event_name='DecisionBound', episode_id=episode,
                               payload={'reason': 'Explicit decision recorded by the caller.'}, key='legacy-bind')
        self.assertEqual(reviews.implementer_host(self.m, episode), 'claude')

    def test_a_codex_log_omits_the_host_and_stays_codex(self):
        self.capture(CODEX_SESSION)
        episode = self.plan(CODEX_SESSION)
        self.log(episode, CODEX_SESSION, 'codex-log')
        self.assertNotIn('host', self.bound_payload(episode))
        self.assertEqual(reviews.implementer_host(self.m, episode), 'codex')

    def test_same_host_is_recorded_only_when_no_other_host_is_available(self):
        self.capture(SESSION, host='claude')
        episode = self.plan(SESSION)
        self.log(episode, SESSION, 'claude-only')
        hosts.mark_unavailable(self.m, 'codex', 'usage_limit')
        run = reviews.request(self.m, episode, 'outcome', request_key='claude-same-host')
        self.assertEqual(run['host'], 'claude')
        self.assertEqual(run['snapshot']['independence'], 'same_host')
        self.assertEqual(run['routing']['reason'], 'same_host')


if __name__ == '__main__':
    unittest.main()
