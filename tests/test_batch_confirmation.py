"""The user confirms several criteria across work items with one statement.

On 23 September 2026 the latest checks of the 19 work items in review held 42 criteria that no machine could confirm
because no evidence was stored (unknown) and 2 that only the user can judge (needs_user). The single confirmation took
only the second kind, one criterion at a time, so the pile could not be closed. The user chose that both kinds are
offered, each labelled, and that Done still needs a new passing check of each item.
"""
import json
from unittest.mock import patch

from memory_module import InvalidRecord, api, reviews
from memory_module.workspace import action
from tests.test_receipt_evidence import ReceiptEvidenceFixture


class BatchConfirmationTests(ReceiptEvidenceFixture):
    def setUp(self):
        super().setUp()
        reviews.configure(self.m, self.root, 'claude')
        self.first = self.item('first', ['The release notes read well.', 'The workflow run of 14 September passed.'])
        self.second = self.item('second', ['The installed tool reports the new version.'])

    def item(self, key, acceptance):
        work = action(self.m, 'plan', {
            'title': 'Release ' + key, 'objective': 'Publish the release.', 'criterion': 'The release is published.',
            'subject': 'code', 'payload': {'state': 'ready', 'next_action': 'Publish.', 'autonomy': 'act', 'scope': 'Publish.',
                                           'reason': 'The user asked for the release.', 'acceptance': acceptance}}, 'plan-' + key)
        episode = work['episode_id']
        source = self.m.source('run-' + key, 'Run', 'Run.', 'Run passed.', 'tool', subject='code')['id']
        evidence = [{'source_id': source, 'reason': 'Run.'}]
        d = self.m.record(episode, 'decision', {'decision': 'Publish.', 'why': 'Asked.', 'expected': 'A release.', 'reconsider_when': 'It fails.',
                                                'alternatives': ['Wait.'], 'uncertainty': 'None.'},
                          expected_version=self.m.episode(episode)['version'], request_key='d-' + key, actor='assistant', evidence=evidence)
        a = self.m.record(episode, 'action', {'action': 'Publish.'}, expected_version=d['version'], request_key='a-' + key,
                          actor='assistant', decision_id=d['id'])
        self.m.record(episode, 'outcome', {'assessment': 'good', 'observed': 'Published.', 'assessment_reason': 'Run passed.',
                                           'severity': 'none', 'attribution': 'Release.', 'completion': 'complete'},
                      expected_version=a['version'], request_key='o-' + key, actor='assistant', decision_id=d['id'], evidence=evidence)
        return episode

    def finished(self, episode, results, verdict='uncertain'):
        with patch.object(reviews, 'launch'):
            run = reviews.request(self.m, episode, 'outcome', request_key='check-' + episode)
        checks = [{'criterion': cid, 'evidence': 'The reviewer found no stored output.', 'result': result} for cid, result in results.items()]
        report = {'verdict': verdict, 'summary': 'Fixture.', 'checks': checks, 'findings': [], 'lesson_proposals': []}
        with self.m._write():
            self.m.db.execute("UPDATE review_runs SET state=?,report=? WHERE id=?", (verdict, json.dumps(report), run['id']))

    def counts(self):
        return (self.m.db.execute("SELECT count(*) FROM sources WHERE source_key LIKE 'criterion-confirmation:%'").fetchone()[0],
                self.m.db.execute("SELECT count(*) FROM events WHERE kind='note'").fetchone()[0])

    def test_unknown_and_needs_user_criteria_are_offered_and_met_or_unmet_are_not(self):
        self.finished(self.first, {'C001': 'needs_user', 'C002': 'unknown', 'C003': 'met'})
        self.finished(self.second, {'C001': 'unmet', 'C002': 'met'})
        offered = reviews.confirmable(self.m, self.first)
        self.assertEqual([(item['criterion'], item['result']) for item in offered], [('C001', 'needs_user'), ('C002', 'unknown')])
        # C001 is the criterion of the work item, and the acceptance criteria follow from C002.
        self.assertEqual(offered[1]['condition'], 'The release notes read well.')
        self.assertEqual(reviews.confirmable(self.m, self.second), [])
        # The Stop hook still names only the criteria that a reviewer said need the user.
        self.assertEqual([item['criterion'] for item in reviews.awaiting_user(self.m, self.first)], ['C001'])

    def test_one_statement_confirms_criteria_across_work_items(self):
        self.finished(self.first, {'C001': 'needs_user', 'C002': 'unknown', 'C003': 'met'})
        self.finished(self.second, {'C001': 'unknown', 'C002': 'met'})
        result = action(self.m, 'confirm_criteria', {'statement': 'I checked the release page and the installed version.', 'items': [
            {'episode_id': self.first, 'criterion': 'C001'}, {'episode_id': self.first, 'criterion': 'C002'},
            {'episode_id': self.second, 'criterion': 'C001'}]}, 'batch-1')
        self.assertEqual(result['confirmed'], 3)
        self.assertEqual(result['episode_ids'], [self.first, self.second])
        self.assertEqual(reviews.confirmable(self.m, self.first), [])
        self.assertEqual(reviews.confirmable(self.m, self.second), [])
        source = self.m.read(result['items'][1]['evidence'][0]['source_id'], detail=True)
        self.assertEqual(source['origin'], 'user')
        value, _ = reviews.snapshot(self.m, self.first, 'outcome')
        meanings = {item['criterion']: item['meaning'] for item in value['user_confirmations']}
        self.assertIn('No machine found evidence', meanings['C002'])
        self.assertNotIn('No machine found evidence', meanings['C001'])
        self.assertTrue(all('Treat it as met' in meaning for meaning in meanings.values()))

    def test_one_criterion_that_is_not_open_refuses_the_whole_batch(self):
        self.finished(self.first, {'C001': 'needs_user', 'C002': 'unknown', 'C003': 'met'})
        self.finished(self.second, {'C001': 'unmet', 'C002': 'met'})
        before = self.counts()
        with self.assertRaises(InvalidRecord) as refused:
            action(self.m, 'confirm_criteria', {'statement': 'All good.', 'items': [
                {'episode_id': self.first, 'criterion': 'C001'}, {'episode_id': self.second, 'criterion': 'C001'}]}, 'batch-2')
        self.assertIn('nothing was recorded', str(refused.exception))
        self.assertEqual(self.counts(), before)
        self.assertEqual(len(reviews.confirmable(self.m, self.first)), 2)
        with self.assertRaises(InvalidRecord):
            action(self.m, 'confirm_criteria', {'statement': 'All good.', 'items': []}, 'batch-3')

    def test_the_single_confirmation_accepts_an_unknown_criterion(self):
        self.finished(self.first, {'C001': 'met', 'C002': 'unknown', 'C003': 'met'})
        result = action(self.m, 'confirm_criterion', {'episode_id': self.first, 'criterion': 'C002', 'statement': 'The run passed.'}, 'one')
        self.assertEqual(result['result'], 'unknown')
        with self.assertRaises(InvalidRecord):
            action(self.m, 'confirm_criterion', {'episode_id': self.first, 'criterion': 'C003', 'statement': 'Met anyway.'}, 'two')

    def test_done_still_needs_a_new_passing_check_after_a_confirmation(self):
        self.finished(self.first, {'C001': 'needs_user', 'C002': 'unknown', 'C003': 'met'})
        action(self.m, 'confirm_criteria', {'statement': 'Checked.', 'items': [
            {'episode_id': self.first, 'criterion': 'C001'}, {'episode_id': self.first, 'criterion': 'C002'}]}, 'batch-4')
        plan = self.m.read(self.m.db.execute("SELECT id FROM events WHERE episode_id=? AND kind='work_plan' ORDER BY seq DESC LIMIT 1",
                                             (self.first,)).fetchone()[0])
        with patch.object(reviews, 'launch'), self.assertRaises(InvalidRecord):
            action(self.m, 'plan', {'episode_id': self.first, 'expected_version': self.m.episode(self.first)['version'],
                                    'payload': {**plan['payload'], 'state': 'done'}}, 'done')

    def test_now_lists_the_criteria_to_confirm_by_work_item(self):
        progress = self.m.read(self.m.db.execute("SELECT id FROM events WHERE episode_id=? AND kind='work_plan' ORDER BY seq DESC LIMIT 1",
                                                 (self.first,)).fetchone()[0])
        action(self.m, 'plan', {'episode_id': self.first, 'expected_version': self.m.episode(self.first)['version'],
                                'payload': {**progress['payload'], 'state': 'review'}}, 'to-review')
        self.finished(self.first, {'C001': 'needs_user', 'C002': 'unknown', 'C003': 'met'})
        now = api.now(self.m, {})
        [group] = now['confirmations']
        self.assertEqual(group['episode_id'], self.first)
        # An unknown criterion lacks machine evidence, which is the agent's to attach, so only needs_user is listed.
        self.assertEqual([item['criterion'] for item in group['criteria']], ['C001'])
        [kind] = [kind for kind in now['attention_kinds'] if kind['type'] == 'criteria_to_confirm']
        self.assertEqual(kind['count'], 1)
