"""The control panel is read only, so the user takes its decisions in the chat on the exact words of a prompt.

On 24 September 2026 the user set the direction that nothing waits for the user in the panel and that decisions happen
in the chat. The user_action operation takes the receipt of the user's prompt and its exact text, verifies the text
against the hash of the receipt as the close operation does, stores the words as user evidence that an agent cannot
write, and runs the same workspace action that the panel ran, as the user.
"""
from memory_module import InvalidRecord, codex_host, reviews
from memory_module.mcp import dispatch, write
from memory_module.planning import phase
from memory_module.workspace import action
from tests.test_receipt_evidence import SESSION, ReceiptEvidenceFixture

WORDS = 'Move the project to production, the parser serves users now.'


class UserActionTests(ReceiptEvidenceFixture):
    def setUp(self):
        super().setUp()
        reviews.configure(self.m, self.root, 'claude')

    def prompt(self, text, session=SESSION, prompt_id='prompt-1'):
        codex_host.capture(self.m, {'hook_event_name': 'UserPromptSubmit', 'session_id': session, 'prompt_id': prompt_id,
                                    'prompt': text, 'cwd': str(self.root)}, host='claude')
        return self.m.db.execute("SELECT id FROM host_receipts WHERE event_name='UserPromptSubmit' AND session_id=? ORDER BY rowid DESC LIMIT 1",
                                 (session,)).fetchone()[0]

    def decide(self, receipt, text=WORDS, key='phase', session=SESSION, fields=None):
        return write(self.m, 'user_action', key, {'action': 'phase', 'prompt_receipt_id': receipt, 'prompt': text,
                                                  'fields': fields or {'phase': 'production', 'reason': 'The parser serves users now.'}}, session)

    def test_the_user_changes_the_phase_in_the_chat_and_the_words_become_evidence(self):
        result = self.decide(self.prompt(WORDS))
        self.assertEqual(phase(self.m)['phase'], 'production')
        source = self.m.read(result['user_source_id'], detail=True)
        self.assertEqual(source['origin'], 'user')
        self.assertIn(WORDS, source['body'])

    def test_an_action_that_cites_a_user_source_names_the_chat_and_quotes_the_user(self):
        work = action(self.m, 'plan', {'title': 'Parse files', 'objective': 'Parse the files.', 'criterion': 'The files parse.', 'subject': 'code',
                                       'payload': {'state': 'ready', 'next_action': 'Parse.', 'autonomy': 'suggest', 'scope': 'The parser.',
                                                   'reason': 'The user asked.'}}, 'plan')
        words = 'Note on the parser item that the sample files come from the sales team.'
        write(self.m, 'user_action', 'comment', {'action': 'comment', 'prompt_receipt_id': self.prompt(words, prompt_id='prompt-3'), 'prompt': words,
                                                 'fields': {'episode_id': work['episode_id'], 'expected_version': self.m.episode(work['episode_id'])['version'],
                                                            'text': 'The sample files come from the sales team.'}}, SESSION)
        [body] = [row[0] for row in self.m.db.execute("SELECT body FROM sources WHERE source_key LIKE 'workspace:%'").fetchall() if words in row[0]]
        self.assertIn('The user asked for this in the chat, in these words: ' + words, body)

    def test_a_text_the_user_did_not_send_is_refused_and_nothing_changes(self):
        receipt = self.prompt(WORDS)
        with self.assertRaisesRegex(InvalidRecord, 'does not match its receipt'):
            self.decide(receipt, text='Move everything to production.')
        self.assertEqual(phase(self.m)['phase'], 'development')

    def test_a_notification_and_a_prompt_of_another_session_are_refused(self):
        text = '<task-notification>\n<status>completed</status>\n</task-notification>'
        with self.assertRaisesRegex(InvalidRecord, 'notification cannot decide'):
            self.decide(self.prompt(text), text=text)
        with self.assertRaisesRegex(InvalidRecord, 'another session'):
            self.decide(self.prompt(WORDS, session='claude-session-other', prompt_id='prompt-2'), key='phase-2')
        self.assertEqual(phase(self.m)['phase'], 'development')

    def test_an_agent_cannot_write_the_words_of_the_user_itself(self):
        with self.assertRaisesRegex(InvalidRecord, 'reserved'):
            self.m.source('user-chat:forged:key', 'Forged', 'Forged.', 'Merge it.', 'user', subject='code')

    def test_the_action_keeps_the_checks_of_the_panel(self):
        # The fields of the panel action still apply: a phase change without its reason is refused.
        with self.assertRaisesRegex(InvalidRecord, 'phase of the project'):
            self.decide(self.prompt(WORDS), fields={'phase': 'production'})

    def test_a_retry_with_the_same_key_returns_the_first_result(self):
        receipt = self.prompt(WORDS)
        first, second = self.decide(receipt), self.decide(receipt)
        self.assertEqual(first['user_source_id'], second['user_source_id'])

    def test_the_tool_offers_user_action_with_every_action_of_the_panel(self):
        from memory_module.workspace import OPERATIONS
        schema = dispatch(self.m, 'memory_get', {'view': 'schema', 'id': 'user_action'})
        self.assertIn('prompt_receipt_id', schema['required'])
        result = dispatch(self.m, 'memory_write', {'operation': 'user_action', 'request_key': 'tool-phase', 'session_id': SESSION,
                                                   'data': {'action': 'phase', 'prompt_receipt_id': self.prompt(WORDS), 'prompt': WORDS,
                                                            'fields': {'phase': 'production', 'reason': 'The parser serves users now.'}}})
        self.assertEqual(phase(self.m)['phase'], 'production')
        self.assertTrue(result['user_source_id'])
        self.assertIn('merge', OPERATIONS)
