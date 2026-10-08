"""A check snapshot attaches receipt-verified MCP output when the plan already names mcp: paths.

A live n8n change is outside the checked folder. Naming receipt ids by hand is not required when
the plan paths already include the server. The worker is told that a live workflow counts only
after that workflow is exported or cited this way.
"""
from datetime import datetime, timezone
from unittest.mock import patch

from memory_module import codex_host, delegation, planning, reviews, sessions
from tests.test_receipt_evidence import SESSION, OUTPUT, ReceiptEvidenceFixture
from tests.test_sessions import claude_line

LIVE = 'A live workflow counts only after it is exported or cited as receipt-verified output.'
TOOL = 'mcp__n8n-mcp__update_workflow'
READ = 'mcp__n8n-mcp__get_workflow_details'


class McpSnapshotTests(ReceiptEvidenceFixture):
    def setUp(self):
        super().setUp()
        reviews.configure(self.m, self.root, 'claude')
        self.ep = None

    def allow(self, paths, key):
        source = self.m.source('scope-' + key, 'Scope', 'The user sets the scope.', 'Change the lead intake workflow.', 'user')
        evidence = [{'source_id': source['id'], 'reason': 'The user sets the scope.'}]
        payload = {'state': 'in_progress', 'next_action': 'Update the workflow.', 'scope': 'The lead intake workflow.',
                   'autonomy': 'act', 'reason': 'The user asks for it.', 'paths': list(paths), 'item_type': 'workflow'}
        if self.ep:
            planning.save(self.m, 'work_plan', payload=payload, actor='workspace-user', evidence=evidence,
                          episode_id=self.ep, expected_version=self.m.episode(self.ep)['version'],
                          request_key=key, session_id=SESSION)
            return self.ep
        self.ep = planning.save(self.m, 'work_plan', payload=payload, actor='assistant', evidence=evidence,
                                title='Lead intake', objective='Update the lead intake workflow.',
                                criterion='The sample lead is posted.', request_key=key, session_id=SESSION)['episode_id']
        return self.ep

    def mcp_call(self, identifier, tool=TOOL, output=None, transcript_output=None):
        output = OUTPUT if output is None else output
        event = {'session_id': SESSION, 'tool_name': tool, 'tool_use_id': identifier,
                 'tool_input': {'workflowId': 'wf-lead'}, 'cwd': str(self.root)}
        codex_host.capture(self.m, {**event, 'hook_event_name': 'PreToolUse'}, host='claude')
        codex_host.capture(self.m, {**event, 'hook_event_name': 'PostToolUse', 'tool_response': output}, host='claude')
        self.lines.append(claude_line('assistant', [{'type': 'tool_use', 'id': identifier, 'name': tool,
                                                    'input': {'workflowId': 'wf-lead'}}],
                                      '2026-09-18T10:00:01Z', session=SESSION))
        if transcript_output is not False:
            shown = output if transcript_output is None else transcript_output
            self.lines.append(claude_line('user', [{'type': 'tool_result', 'tool_use_id': identifier, 'content': shown['stdout']}],
                                          '2026-09-18T10:00:02Z', session=SESSION, extra={'toolUseResult': shown}))
        self.transcript(SESSION, self.lines)
        return self.m.db.execute("SELECT id FROM host_receipts WHERE tool_use_id=? AND event_name='PostToolUse'",
                                 (identifier,)).fetchone()[0]

    def outcome(self):
        with patch.object(sessions, 'folders', return_value=self.found):
            return reviews.snapshot(self.m, self.ep, 'outcome')

    def verified(self, value):
        return [source for source in value['sources'] if source['source_key'].startswith('receipt-evidence:')]

    def test_mcp_paths_attach_receipt_verified_output_without_naming_receipt_ids(self):
        self.allow(['workflows/lead-intake.json', 'mcp:n8n-mcp'], 'plan')
        receipt = self.mcp_call('toolu_n8n_update')
        self.call('toolu_bash_view')
        value, signature = self.outcome()
        [source] = self.verified(value)
        self.assertEqual(source['verification']['receipt_id'], receipt)
        self.assertIn('verified', source['verification']['statement'])
        self.assertIn('Run 1234 completed with conclusion success.', source['body'])
        self.assertEqual(source['origin'], 'tool')
        again, again_signature = self.outcome()
        self.assertEqual(again_signature, signature)
        self.assertEqual([item['source_key'] for item in self.verified(again)], [source['source_key']])
        self.assertEqual(self.m.db.execute("SELECT count(*) FROM sources WHERE source_key LIKE 'receipt-evidence:%'").fetchone()[0], 1)

    def test_paths_without_an_mcp_target_do_not_attach_the_output(self):
        self.allow(['workflows/lead-intake.json', 'mcp:n8n-mcp'], 'plan')
        self.mcp_call('toolu_n8n_update')
        self.allow(['workflows/lead-intake.json'], 'drop-mcp')
        value, _ = self.outcome()
        self.assertEqual(self.verified(value), [])
        self.assertEqual(self.m.db.execute("SELECT count(*) FROM sources WHERE source_key LIKE 'receipt-evidence:%'").fetchone()[0], 0)

    def test_a_read_or_another_server_is_not_attached(self):
        self.allow(['mcp:n8n-mcp', 'mcp:other-server'], 'plan')
        self.mcp_call('toolu_n8n_read', tool=READ)
        kept = self.mcp_call('toolu_n8n_update')
        self.allow(['mcp:other-server'], 'other-only')
        value, _ = self.outcome()
        self.assertEqual(self.verified(value), [])
        self.allow(['mcp:n8n-mcp'], 'n8n-only')
        value, _ = self.outcome()
        [source] = self.verified(value)
        self.assertEqual(source['verification']['receipt_id'], kept)
        self.assertNotIn('toolu_n8n_read', source['source_key'])

    def test_a_mismatched_transcript_is_left_out_and_the_check_still_opens(self):
        self.allow(['mcp:n8n-mcp'], 'plan')
        self.mcp_call('toolu_n8n_bad', transcript_output={**OUTPUT, 'stdout': 'workflow failed'})
        value, _ = self.outcome()
        self.assertEqual(self.verified(value), [])
        self.assertEqual(self.m.db.execute("SELECT count(*) FROM sources WHERE source_key LIKE 'receipt-evidence:%'").fetchone()[0], 0)

    def test_only_the_latest_twenty_matching_receipts_are_attached(self):
        self.allow(['mcp:n8n-mcp'], 'plan')
        receipts = []
        for index in range(21):
            output = {**OUTPUT, 'stdout': f'workflow update {index}\n'}
            receipts.append(self.mcp_call(f'toolu_n8n_{index:02d}', output=output))
        value, _ = self.outcome()
        attached = {source['verification']['receipt_id'] for source in self.verified(value)}
        self.assertEqual(attached, set(receipts[1:]))
        self.assertNotIn(receipts[0], attached)
        self.assertEqual(len(attached), sessions.RECEIPT_EVIDENCE_LIMIT)
        bodies = ' '.join(source['body'] for source in self.verified(value))
        self.assertNotIn('workflow update 0\n', bodies)
        self.assertIn('workflow update 20\n', bodies)


class WorkerPromptTests(ReceiptEvidenceFixture):
    def prompt(self, paths, instructions=None):
        snapshot = {'paths': paths, 'base_commit': 'abc123'}
        return delegation.worker_prompt(snapshot, datetime(2026, 10, 8, tzinfo=timezone.utc), 600, instructions=instructions)

    def test_the_worker_prompt_says_a_live_workflow_counts_only_after_export_or_citation(self):
        self.assertIn(LIVE, self.prompt(['workflows/lead-intake.json']))
        self.assertEqual(self.prompt(['mcp:n8n-mcp']).count(LIVE), 1)
        custom = 'Work only inside the allowed paths.'
        self.assertIn(LIVE, self.prompt(['mcp:n8n-mcp'], instructions=custom))
        self.assertNotIn(LIVE, self.prompt(['workflows/lead-intake.json'], instructions=custom))
