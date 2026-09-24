"""Work closes by machine evidence, and a criterion without evidence goes back to the agent.

On 24 September 2026, 50 of the 53 criteria waiting for the user were unknown: the check found no machine evidence, which
the agent can attach, not the user. 14 of the 20 items in review had a good and complete outcome and waited only for a
check that went stale after their session ended, because only the Stop hook of that session ran a check again.
"""
from unittest.mock import patch

from memory_module import api, reviews
from memory_module.workspace import action
from tests import test_batch_confirmation as batch
from tests.test_receipt_evidence import ReceiptEvidenceFixture


class CloseByMachineTests(ReceiptEvidenceFixture):
    def setUp(self):
        super().setUp()
        reviews.configure(self.m, self.root, 'claude')
        self.first = self.item('first', ['The release notes read well.', 'The workflow run of 14 September passed.'])
        self.second = self.item('second', ['The installed tool reports the new version.'])

    item = batch.BatchConfirmationTests.item
    finished = batch.BatchConfirmationTests.finished

    def only_unknown(self):
        progress = self.m.read(self.m.db.execute("SELECT id FROM events WHERE episode_id=? AND kind='work_plan' ORDER BY seq DESC LIMIT 1",
                                                 (self.first,)).fetchone()[0])
        action(self.m, 'plan', {'episode_id': self.first, 'expected_version': self.m.episode(self.first)['version'],
                                'payload': {**progress['payload'], 'state': 'review'}}, 'to-review')
        self.finished(self.first, {'C001': 'unknown', 'C002': 'unknown', 'C003': 'met'})

    def test_unknown_criteria_are_not_offered_to_the_user(self):
        self.only_unknown()
        self.assertEqual(reviews.pending_confirmations(self.m, [self.first]), [])
        self.assertFalse([kind for kind in api.now(self.m, {})['attention_kinds'] if kind['type'] == 'criteria_to_confirm'])

    def test_a_done_request_on_a_stale_check_requests_a_new_one(self):
        self.finished(self.first, {'C001': 'met', 'C002': 'met', 'C003': 'met'}, verdict='pass')
        (self.root / 'changed.txt').write_text('A later change of the project files.')
        with patch.object(reviews, 'launch'):
            result = reviews.request_for_done(self.m, self.first)
        self.assertTrue(result['requested'])
        self.assertEqual(result['state'], 'queued')

    def stale(self):
        for episode in (self.first, self.second):
            self.finished(episode, {'C001': 'met'}, verdict='pass')
        (self.root / 'changed.txt').write_text('A later change of the project files.')

    def test_session_start_reruns_stale_checks_of_finished_work_within_a_daily_budget(self):
        self.stale()
        start = {'hook_event_name': 'SessionStart', 'session_id': 'new-session', 'source': 'startup'}
        with patch.object(reviews, 'launch') as launched:
            reviews.hook(self.m, start, 'claude')
        self.assertEqual(launched.call_count, 2)
        queued = self.m.db.execute("SELECT episode_id FROM review_runs WHERE state='queued' AND request_key LIKE 'refresh:%'").fetchall()
        self.assertEqual({row[0] for row in queued}, {self.first, self.second})
        # A second session start finds the checks already requested for this evidence and requests nothing more.
        with patch.object(reviews, 'launch') as launched:
            reviews.hook(self.m, {**start, 'session_id': 'another-session'}, 'claude')
        self.assertEqual(launched.call_count, 0)

    def test_the_daily_budget_limits_the_reruns(self):
        self.stale()
        reviews.set_refresh_limit(self.m, 1)
        with patch.object(reviews, 'launch') as launched:
            reviews.hook(self.m, {'hook_event_name': 'SessionStart', 'session_id': 'new-session', 'source': 'startup'}, 'claude')
        self.assertEqual(launched.call_count, 1)
        self.assertEqual(reviews.refresh_limit(self.m), 1)

    def test_unfinished_work_is_not_rechecked_at_session_start(self):
        partial = action(self.m, 'plan', {
            'title': 'Release partial', 'objective': 'Publish the release.', 'criterion': 'The release is published.',
            'subject': 'code', 'payload': {'state': 'ready', 'next_action': 'Publish.', 'autonomy': 'act', 'scope': 'Publish.',
                                           'reason': 'The user asked for the release.'}}, 'plan-partial')['episode_id']
        source = self.m.source('run-partial', 'Run', 'Run.', 'Run failed.', 'tool', subject='code')['id']
        evidence = [{'source_id': source, 'reason': 'Run.'}]
        d = self.m.record(partial, 'decision', {'decision': 'Publish.', 'why': 'Asked.', 'expected': 'A release.', 'reconsider_when': 'It fails.',
                                                'alternatives': ['Wait.'], 'uncertainty': 'None.'},
                          expected_version=self.m.episode(partial)['version'], request_key='d-partial', actor='assistant', evidence=evidence)
        a = self.m.record(partial, 'action', {'action': 'Publish.'}, expected_version=d['version'], request_key='a-partial',
                          actor='assistant', decision_id=d['id'])
        self.m.record(partial, 'outcome', {'assessment': 'good', 'observed': 'Half published.', 'assessment_reason': 'Run failed.',
                                           'severity': 'minor', 'attribution': 'Release.', 'completion': 'partial'},
                      expected_version=a['version'], request_key='o-partial', actor='assistant', decision_id=d['id'], evidence=evidence)
        with patch.object(reviews, 'launch'):
            reviews.hook(self.m, {'hook_event_name': 'SessionStart', 'session_id': 'new-session', 'source': 'startup'}, 'claude')
        self.assertFalse(self.m.db.execute("SELECT 1 FROM review_runs WHERE episode_id=?", (partial,)).fetchone())
