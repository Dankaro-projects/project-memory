"""A check can confirm a fact outside the repository from command output that the hooks recorded.

A release item cannot reach Done on the implementer's own summary of a workflow run, a published artefact or an
installed version, because receipts keep only the hash of a tool result. On 18 September 2026 the check of release
0.6.0b5 ended uncertain for that reason. The evidence operation now reads the exact output of a named receipt from
the session transcript and stores it only when its hash matches the receipt. A criterion that no machine can
confirm goes to the user once, and a finished check is reported once per result.
"""
import hashlib
import json
from unittest.mock import patch

from memory_module import InvalidRecord, codex_host, reviews, sessions
from memory_module.core import dumps
from memory_module.mcp import write
from memory_module.workspace import action
from tests.test_sessions import Fixture, claude_line

SESSION = 'claude-session-evidence-1'
OUTPUT = {'stdout': 'Run 1234 completed with conclusion success.\nproject-memory 0.6.0b5', 'stderr': '',
          'interrupted': False, 'isImage': False}


class ReceiptEvidenceFixture(Fixture):
    def call(self, identifier, output=OUTPUT, transcript_output=None, command='gh run view 1234'):
        """A Bash call observed by the hooks, and its transcript entry unless transcript_output is False."""
        codex_host.capture(self.m, {'hook_event_name': 'PreToolUse', 'session_id': SESSION, 'tool_name': 'Bash',
                                    'tool_use_id': identifier, 'tool_input': {'command': command}, 'cwd': str(self.root)}, host='claude')
        codex_host.capture(self.m, {'hook_event_name': 'PostToolUse', 'session_id': SESSION, 'tool_name': 'Bash',
                                    'tool_use_id': identifier, 'tool_input': {'command': command}, 'tool_response': output,
                                    'cwd': str(self.root)}, host='claude')
        self.lines.append(claude_line('assistant', [{'type': 'tool_use', 'id': identifier, 'name': 'Bash', 'input': {'command': command}}],
                                      '2026-09-18T10:00:01Z', session=SESSION))
        if transcript_output is not False:
            shown = output if transcript_output is None else transcript_output
            self.lines.append(claude_line('user', [{'type': 'tool_result', 'tool_use_id': identifier, 'content': shown['stdout']}],
                                          '2026-09-18T10:00:02Z', session=SESSION, extra={'toolUseResult': shown}))
        self.transcript(SESSION, self.lines)
        return self.m.db.execute("SELECT id FROM host_receipts WHERE tool_use_id=? AND event_name='PostToolUse'", (identifier,)).fetchone()[0]

    def setUp(self):
        super().setUp()
        self.lines = []


class EvidenceTests(ReceiptEvidenceFixture):
    def test_matching_output_is_stored_as_receipt_verified_evidence(self):
        receipt = self.call('toolu_run_view')
        result = sessions.receipt_evidence(self.m, [receipt], 'evidence-1', found=self.found)
        [reference] = result['evidence']
        source = self.m.read(reference['source_id'], detail=True)
        self.assertEqual(source['source_key'], 'receipt-evidence:' + receipt)
        self.assertEqual(source['origin'], 'tool')
        self.assertIn('Run 1234 completed with conclusion success.', source['body'])
        expected = hashlib.sha256(dumps(OUTPUT).encode()).hexdigest()
        self.assertIn(expected, source['body'])
        self.assertIn(receipt, source['body'])
        self.assertIn('verified', reference['reason'])

    def test_a_repeated_request_returns_the_same_evidence(self):
        receipt = self.call('toolu_run_view')
        first = sessions.receipt_evidence(self.m, [receipt], 'evidence-1', found=self.found)
        again = sessions.receipt_evidence(self.m, [receipt], 'evidence-2', found=self.found)
        self.assertEqual(first['evidence'], again['evidence'])
        self.assertEqual(self.m.db.execute("SELECT count(*) FROM sources WHERE source_key LIKE 'receipt-evidence:%'").fetchone()[0], 1)

    def test_an_edited_output_is_refused_and_nothing_is_stored(self):
        good = self.call('toolu_good')
        edited = self.call('toolu_edited', transcript_output={**OUTPUT, 'stdout': 'Run 1234 completed with conclusion failure.'})
        with self.assertRaises(InvalidRecord) as caught:
            sessions.receipt_evidence(self.m, [good, edited], 'evidence-edited', found=self.found)
        self.assertIn('does not match', str(caught.exception))
        self.assertEqual(self.m.db.execute("SELECT count(*) FROM sources WHERE source_key LIKE 'receipt-evidence:%'").fetchone()[0], 0)

    def test_a_receipt_without_a_transcript_entry_is_refused(self):
        receipt = self.call('toolu_lost', transcript_output=False)
        with self.assertRaises(InvalidRecord) as caught:
            sessions.receipt_evidence(self.m, [receipt], 'evidence-lost', found=self.found)
        self.assertIn('transcript', str(caught.exception))

    def test_only_a_completed_tool_call_can_be_evidence(self):
        self.call('toolu_run_view')
        pre = self.m.db.execute("SELECT id FROM host_receipts WHERE event_name='PreToolUse'").fetchone()[0]
        with self.assertRaises(InvalidRecord):
            sessions.receipt_evidence(self.m, [pre], 'evidence-pre', found=self.found)
        with self.assertRaises(InvalidRecord):
            sessions.receipt_evidence(self.m, [], 'evidence-none', found=self.found)

    def test_an_agent_cannot_write_the_reserved_key_itself(self):
        with self.assertRaises(InvalidRecord):
            self.m.source('receipt-evidence:host_forged', 'Forged', 'A forged result.', 'Run succeeded.', 'tool', subject='code')

    def test_receipts_still_keep_only_hashes_and_sizes(self):
        receipt = self.call('toolu_run_view')
        payload = codex_host.read_receipt(self.m, receipt)['payload']
        self.assertEqual(set(payload['tool_response']), {'sha256', 'bytes'})
        self.assertNotIn('Run 1234', json.dumps(payload))

    def test_the_mcp_evidence_operation_takes_receipt_ids(self):
        receipt = self.call('toolu_run_view')
        with patch.object(sessions, 'folders', return_value=self.found):
            result = write(self.m, 'evidence', 'mcp-evidence', {}, SESSION, [receipt])
        self.assertEqual(result['evidence'][0]['source_id'],
                         self.m.db.execute("SELECT id FROM sources WHERE source_key=?", ('receipt-evidence:' + receipt,)).fetchone()[0])


class CheckTests(ReceiptEvidenceFixture):
    def setUp(self):
        super().setUp()
        reviews.configure(self.m, self.root, 'claude')
        self.work = action(self.m, 'plan', {
            'title': 'Release 9.9.9', 'objective': 'Publish the release.', 'criterion': 'The release workflow run succeeded.',
            'subject': 'code', 'payload': {'state': 'ready', 'next_action': 'Publish.', 'autonomy': 'act',
                                           'scope': 'Publish the release.', 'reason': 'The user asked for the release.',
                                           'acceptance': ['The user approves the release notes wording.']}}, 'plan')
        self.ep = self.work['episode_id']

    def complete(self, evidence):
        d = self.m.record(self.ep, 'decision', {'decision': 'Publish.', 'why': 'Asked.', 'expected': 'A release.',
                                                'reconsider_when': 'It fails.', 'alternatives': ['Wait.'], 'uncertainty': 'None.'},
                          expected_version=self.m.episode(self.ep)['version'], request_key='d', actor='assistant', evidence=evidence)
        a = self.m.record(self.ep, 'action', {'action': 'Publish.'}, expected_version=d['version'], request_key='a',
                          actor='assistant', decision_id=d['id'])
        self.m.record(self.ep, 'outcome', {'assessment': 'good', 'observed': 'Published.', 'assessment_reason': 'Run passed.',
                                           'severity': 'none', 'attribution': 'Release.', 'completion': 'complete'},
                      expected_version=a['version'], request_key='o', actor='assistant', decision_id=d['id'], evidence=evidence)
        return d

    def test_a_check_snapshot_states_that_its_evidence_was_verified_against_receipts(self):
        receipt = self.call('toolu_run_view')
        evidence = sessions.receipt_evidence(self.m, [receipt], 'evidence-1', found=self.found)['evidence']
        self.complete(evidence)
        value, _ = reviews.snapshot(self.m, self.ep, 'outcome')
        [source] = [s for s in value['sources'] if s['source_key'].startswith('receipt-evidence:')]
        self.assertEqual(source['verification']['receipt_id'], receipt)
        self.assertIn('verified', source['verification']['statement'])
        ordinary = [s for s in value['sources'] if not s['source_key'].startswith('receipt-evidence:')]
        self.assertTrue(all('verification' not in s for s in ordinary))

    def finished(self, results, verdict='uncertain'):
        run = reviews.request(self.m, self.ep, 'outcome', request_key='check-' + str(len(results)))
        checks = [{'criterion': cid, 'evidence': 'Fixture.', 'result': result} for cid, result in results.items()]
        report = {'verdict': verdict, 'summary': 'Fixture.', 'checks': checks, 'findings': [], 'lesson_proposals': []}
        with self.m._write():
            self.m.db.execute("UPDATE review_runs SET state=?,report=? WHERE id=?", (verdict, json.dumps(report), run['id']))
        return run

    def test_a_report_can_name_a_criterion_that_only_the_user_can_confirm(self):
        checks = [{'criterion': 'C001', 'evidence': 'Receipt verified output.', 'result': 'met'},
                  {'criterion': 'C002', 'evidence': 'Only the user can judge the wording.', 'result': 'needs_user'}]
        report = {'verdict': 'uncertain', 'summary': 'One criterion needs the user.', 'checks': checks, 'findings': [], 'lesson_proposals': []}
        reviews.validate_report(report)
        with self.assertRaises(InvalidRecord):
            reviews.validate_report({**report, 'verdict': 'pass'})

    def test_the_user_confirms_a_criterion_once_with_user_origin(self):
        self.complete([{'source_id': self.m.source('r', 'Run', 'Run.', 'Run passed.', 'tool', subject='code')['id'], 'reason': 'Run.'}])
        with patch.object(reviews, 'launch'):
            self.finished({'C001': 'met', 'C002': 'needs_user'})
        [offered] = reviews.awaiting_user(self.m, self.ep)
        self.assertEqual(reviews.listing(self.m, self.ep)['awaiting_user'], [offered])
        self.assertEqual(offered['criterion'], 'C002')
        self.assertEqual(offered['condition'], 'The user approves the release notes wording.')
        result = action(self.m, 'confirm_criterion', {'episode_id': self.ep, 'criterion': 'C002',
                                                      'statement': 'I approve the release notes wording.'}, 'confirm-1')
        source = self.m.read(result['evidence'][0]['source_id'], detail=True)
        self.assertEqual(source['origin'], 'user')
        self.assertIn('I approve the release notes wording.', source['body'])
        self.assertEqual(reviews.awaiting_user(self.m, self.ep), [])
        with self.assertRaises(InvalidRecord):
            action(self.m, 'confirm_criterion', {'episode_id': self.ep, 'criterion': 'C002', 'statement': 'Again.'}, 'confirm-2')
        value, _ = reviews.snapshot(self.m, self.ep, 'outcome')
        [confirmation] = value['user_confirmations']
        self.assertEqual(confirmation['criterion'], 'C002')
        self.assertEqual(confirmation['origin'], 'user')

    def test_an_agent_cannot_confirm_for_the_user(self):
        with self.assertRaises(InvalidRecord):
            self.m.source('criterion-confirmation:' + self.ep + ':C002', 'Forged', 'Forged.', 'Approved.', 'user', subject='code')

    def test_a_confirmation_does_not_carry_over_to_a_changed_criterion(self):
        self.complete([{'source_id': self.m.source('r', 'Run', 'Run.', 'Run passed.', 'tool', subject='code')['id'], 'reason': 'Run.'}])
        with patch.object(reviews, 'launch'):
            self.finished({'C001': 'met', 'C002': 'needs_user'})
        action(self.m, 'confirm_criterion', {'episode_id': self.ep, 'criterion': 'C002', 'statement': 'Approved.'}, 'confirm-1')
        plan = self.m.read(self.work['id'])
        action(self.m, 'plan', {'episode_id': self.ep, 'expected_version': self.m.episode(self.ep)['version'],
                                'payload': {**plan['payload'], 'acceptance': ['The user approves the changelog.']}}, 'revise')
        value, _ = reviews.snapshot(self.m, self.ep, 'outcome')
        # The field is left out when empty, so that checks made before it existed stay current.
        self.assertNotIn('user_confirmations', value)

    def test_the_stop_hook_reports_a_finished_check_once_per_result(self):
        d = self.complete([{'source_id': self.m.source('r', 'Run', 'Run.', 'Run passed.', 'tool', subject='code')['id'], 'reason': 'Run.'}])
        codex_host.bind(self.m, SESSION, d['id'], 'bind')
        with patch.object(reviews, 'launch'):
            first = reviews.hook(self.m, {'hook_event_name': 'Stop', 'session_id': SESSION, 'turn_id': 'turn-1'}, 'claude')
            self.assertEqual(first['decision'], 'block')
            self.assertEqual(reviews.hook(self.m, {'hook_event_name': 'Stop', 'session_id': SESSION, 'turn_id': 'turn-2'}, 'claude'), {})
            run = self.m.db.execute('SELECT id FROM review_runs').fetchone()[0]
            report = {'verdict': 'uncertain', 'summary': 'Fixture.', 'findings': [], 'lesson_proposals': [],
                      'checks': [{'criterion': 'C001', 'evidence': 'Fixture.', 'result': 'unknown'}]}
            with self.m._write():
                self.m.db.execute("UPDATE review_runs SET state='uncertain',report=? WHERE id=?", (json.dumps(report), run))
            changed = reviews.hook(self.m, {'hook_event_name': 'Stop', 'session_id': SESSION, 'turn_id': 'turn-3'}, 'claude')
            self.assertEqual(changed['decision'], 'block')
            self.assertIn('uncertain', changed['reason'])
            self.assertEqual(reviews.hook(self.m, {'hook_event_name': 'Stop', 'session_id': SESSION, 'turn_id': 'turn-4'}, 'claude'), {})
            self.assertEqual(reviews.hook(self.m, {'hook_event_name': 'Stop', 'session_id': SESSION}, 'claude'), {})

    def test_the_stop_notice_asks_for_the_confirmation_of_the_user(self):
        d = self.complete([{'source_id': self.m.source('r', 'Run', 'Run.', 'Run passed.', 'tool', subject='code')['id'], 'reason': 'Run.'}])
        codex_host.bind(self.m, SESSION, d['id'], 'bind')
        with patch.object(reviews, 'launch'):
            self.finished({'C001': 'met', 'C002': 'needs_user'})
            notice = reviews.hook(self.m, {'hook_event_name': 'Stop', 'session_id': SESSION, 'turn_id': 'turn-1'}, 'claude')
        self.assertIn('C002', notice['reason'])
        self.assertIn('control panel', notice['reason'])


class ResumeTests(Fixture):
    """On 18 September 2026 a resume did not lead to the ready item the earlier session had planned next."""

    def item(self, key, title, state, priority):
        return action(self.m, 'plan', {'title': title, 'objective': 'Do it.', 'criterion': 'It is done.', 'subject': 'code',
                                       'payload': {'state': state, 'next_action': 'Start with the ' + key + ' tests.',
                                                   'autonomy': 'act', 'scope': 'Only this.', 'reason': 'Planned.',
                                                   'priority': priority, 'owner': 'human'}}, key)['episode_id']

    def test_the_session_start_context_names_the_work_to_continue(self):
        self.assertEqual(sessions.work_to_continue(self.m, room=2000), '')
        ready = self.item('ready', 'Let checks confirm facts', 'ready', 'high')
        running = self.item('running', 'Repair the parser', 'in_progress', 'normal')
        self.item('later', 'Someday', 'backlog', 'low')
        text = sessions.work_to_continue(self.m, room=2000)
        # The most recent activity comes first: the item planned last leads.
        self.assertLess(text.index(running), text.index(ready))
        self.assertNotIn('..', text)
        self.assertIn('Let checks confirm facts', text)
        self.assertIn('Start with the ready tests.', text)
        self.assertNotIn('Someday', text)
        self.assertLessEqual(len(sessions.work_to_continue(self.m, room=200)), 200)
        started = codex_host.capture(self.m, {'hook_event_name': 'SessionStart', 'session_id': 'resume-session-1',
                                              'source': 'startup', 'cwd': str(self.root)}, host='claude')
        self.assertIn(ready, started['hookSpecificOutput']['additionalContext'])
