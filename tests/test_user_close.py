"""The user can close a work item in the chat, and an agent cannot close one on words the user did not send.

On 24 September 2026 the user asked to close the release item 0.6.0b12, because 0.6.0b13 had replaced it. Done was
refused because the last check was uncertain, and the only route was to rewrite the acceptance and run another check.
The close operation takes the receipt of the user's prompt and its exact text. The capture keeps only the hash of a
prompt, so Project Memory compares the hash and stores the text as the user's own evidence under a reserved key.
"""
from unittest.mock import patch

from memory_module import InvalidRecord, codex_host, reviews
from memory_module.mcp import write
from memory_module.planning import latest
from memory_module.workspace import action
from tests.test_receipt_evidence import SESSION, ReceiptEvidenceFixture

WORDS = '1. yes I do. 2. yes close'


class UserCloseTests(ReceiptEvidenceFixture):
    def setUp(self):
        super().setUp()
        reviews.configure(self.m, self.root, 'claude')
        self.work = action(self.m, 'plan', {
            'title': 'Release 9.9.9', 'objective': 'Publish the release.', 'criterion': 'The installed tool reports 9.9.9.',
            'subject': 'code', 'payload': {'state': 'review', 'next_action': 'Confirm the install.', 'autonomy': 'act',
                                           'scope': 'Publish the release.', 'reason': 'The user asked for the release.'}}, 'plan')
        self.ep = self.work['episode_id']

    def prompt(self, text, session=SESSION, prompt_id='prompt-1'):
        codex_host.capture(self.m, {'hook_event_name': 'UserPromptSubmit', 'session_id': session, 'prompt_id': prompt_id,
                                    'prompt': text, 'cwd': str(self.root)}, host='claude')
        return self.m.db.execute("SELECT id FROM host_receipts WHERE event_name='UserPromptSubmit' AND session_id=? ORDER BY rowid DESC LIMIT 1",
                                 (session,)).fetchone()[0]

    def close(self, receipt, text=WORDS, session=SESSION, key='close'):
        return write(self.m, 'close', key, {'episode_id': self.ep, 'expected_version': self.m.episode(self.ep)['version'],
                                            'prompt_receipt_id': receipt, 'prompt': text, 'actor': 'claude',
                                            'reason': 'Version 9.9.10 replaced this release on purpose.'}, session)

    def test_the_user_closes_an_item_with_the_words_of_their_prompt(self):
        receipt = self.prompt(WORDS)
        self.close(receipt)
        plan = latest(self.m, self.ep, 'work_plan')
        self.assertEqual(plan['state'], 'done')
        sources = [self.m.read(e['source_id'], detail=True) for e in self.m.read(plan['id'])['evidence']]
        [source] = [s for s in sources if s['source_key'].startswith('user-closure:' + self.ep + ':')]
        self.assertEqual(source['origin'], 'user')
        self.assertIn(WORDS, source['body'])

    def test_a_text_the_user_did_not_send_is_refused(self):
        receipt = self.prompt(WORDS)
        with self.assertRaisesRegex(InvalidRecord, 'does not match its receipt'):
            self.close(receipt, text='yes close all of them')
        self.assertEqual(latest(self.m, self.ep, 'work_plan')['state'], 'review')

    def test_a_notification_cannot_close_an_item(self):
        text = '<task-notification>\n<status>completed</status>\n</task-notification>'
        receipt = self.prompt(text)
        with self.assertRaisesRegex(InvalidRecord, 'notification cannot close'):
            self.close(receipt, text=text)

    def test_a_prompt_of_another_session_is_refused(self):
        receipt = self.prompt(WORDS, session='claude-session-other')
        with self.assertRaisesRegex(InvalidRecord, 'another session'):
            self.close(receipt)

    def test_an_agent_cannot_write_a_closure_itself(self):
        with self.assertRaisesRegex(InvalidRecord, 'reserved'):
            self.m.source('user-closure:' + self.ep + ':forged', 'Forged', 'Forged.', 'Close it.', 'user', subject='code')

    def test_progress_still_cannot_reach_done_without_completion_evidence(self):
        with self.assertRaisesRegex(InvalidRecord, 'Done requires'):
            write(self.m, 'progress', 'progress-done', {'episode_id': self.ep, 'expected_version': self.m.episode(self.ep)['version'],
                                                        'actor': 'claude', 'payload': {'state': 'done', 'reason': 'Done.'}}, SESSION)

    def test_a_closed_item_counts_as_complete_for_the_work_that_depends_on_it(self):
        from memory_module.planning import completion
        self.close(self.prompt(WORDS))
        self.assertTrue(completion(self.m, self.ep))
