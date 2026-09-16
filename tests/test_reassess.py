"""The reassess action: the user records a new outcome of a decision, and only that removes a failure from the recurrence count."""
import unittest

from memory_module import Conflict, InvalidRecord, guards, mcp
from memory_module.core import USER_ACTOR
from memory_module.mcp import dispatch
from memory_module.workspace import action, reassess
from tests.test_guards import Fixture


class ReassessTests(Fixture):
    def setUp(self):
        super().setUp()
        self.review = self.accept(self.lesson(failure_type='lost_text'))

    def counted(self):
        return guards.recurrence_count(self.m, 'lost_text', self.review)

    def failure(self, session):
        episode = self.work(session=session)
        outcome = self.attempt(episode, failure_type='lost_text')
        return episode, outcome, self.m._event(outcome)['decision_id']

    def reassess(self, decision_id, key, assessment='good', **extra):
        data = {'decision_id': decision_id, 'assessment': assessment, 'reason': 'The fixture passes after the decoder fix.', **extra}
        return action(self.m, 'reassess', data, key)

    def agent_outcome(self, episode_id, decision_id, key, assessment='good', **extra):
        latest = self.m.db.execute("SELECT id FROM events WHERE decision_id=? AND kind='outcome' ORDER BY seq DESC LIMIT 1",
                                   (decision_id,)).fetchone()['id']
        return dispatch(self.m, 'memory_write', {'operation': 'record', 'request_key': key, 'data': {
            'episode_id': episode_id, 'kind': 'outcome', 'decision_id': decision_id, 'supersedes': latest,
            'expected_version': self.version(episode_id), 'actor': 'assistant', 'evidence': self.evidence, 'payload': {
                'observed': 'The fixture ran again.', 'assessment': assessment, 'assessment_reason': 'The agent ran the fixture.',
                'severity': 'none' if assessment == 'good' else 'major', 'attribution': 'The decoder change.', **extra}}})

    def counted_ids(self):
        return sorted(outcome['id'] for entry in guards.recurrences(self.m) for outcome in entry['outcomes'])

    def test_the_action_clears_exactly_one_counted_failure(self):
        first_episode, first, first_decision = self.failure('first')
        second_episode, second, second_decision = self.failure('second')
        self.assertEqual(self.counted(), 2)
        result = self.reassess(first_decision, 'reassess-first', outcome_id=first)
        self.assertEqual(self.counted(), 1)
        [entry] = guards.recurrences(self.m)
        self.assertEqual([outcome['id'] for outcome in entry['outcomes']], [second])
        record = self.m._event(result['id'])
        self.assertEqual((record['kind'], record['actor'], record['decision_id'], record['supersedes']),
                         ('outcome', USER_ACTOR, first_decision, first))
        self.assertEqual((record['payload']['assessment'], record['payload']['severity']), ('good', 'none'))
        self.assertEqual(result['supersedes'], first)
        # The user source states what the user saved.
        source = self.m.read(self.m.read(result['id'])['evidence'][0]['source_id'])
        self.assertEqual(source['origin'], 'user')
        # A repeated request returns the same result and records nothing more.
        self.assertEqual(self.reassess(first_decision, 'reassess-first', outcome_id=first), result)
        self.assertEqual(self.counted(), 1)
        with self.assertRaises(Conflict):
            self.reassess(first_decision, 'reassess-first', assessment='unknown')

    def test_a_bad_reassessment_keeps_the_failure_counted_once_in_its_place(self):
        _, outcome, decision = self.failure('first')
        result = self.reassess(decision, 'reassess-bad', assessment='bad', outcome_id=outcome)
        payload = self.m._event(result['id'])['payload']
        self.assertEqual((payload['failure_type'], payload['severity']), ('lost_text', 'major'))
        self.assertEqual(result['reassessed'], outcome)
        self.assertEqual(self.counted(), 1)
        self.assertEqual(self.counted_ids(), [outcome])
        # The user may change the assessment again: a later good assessment of the same outcome clears it.
        self.reassess(decision, 'reassess-good', outcome_id=outcome)
        self.assertEqual(self.counted(), 0)
        # A reassessment is not itself an outcome that can be reassessed.
        with self.assertRaises(InvalidRecord):
            self.reassess(decision, 'reassess-reassessment', outcome_id=result['id'])

    def test_a_bad_reassessment_after_a_good_agent_outcome_keeps_the_failure_type(self):
        episode, outcome, decision = self.failure('first')
        self.agent_outcome(episode, decision, 'agent-good')
        result = self.reassess(decision, 'reassess-bad', assessment='bad', outcome_id=outcome)
        payload = self.m._event(result['id'])['payload']
        self.assertEqual((payload['failure_type'], payload['severity']), ('lost_text', 'major'))
        self.assertEqual(self.counted(), 1)
        self.assertEqual(self.counted_ids(), [outcome])

    def test_a_bad_reassessment_of_a_failure_before_the_acceptance_is_not_a_recurrence(self):
        episode = self.work(session='early')
        # A second failure type keeps this failure apart from the guard of setUp, which was accepted first.
        early = self.attempt(episode, failure_type='late_type')
        decision = self.m._event(early)['decision_id']
        later = self.accept(self.lesson(failure_type='late_type'))
        counts = lambda: (guards.recurrence_count(self.m, 'late_type', later, after=False),
                          guards.recurrence_count(self.m, 'late_type', later))
        self.assertEqual(counts(), (1, 0))
        self.reassess(decision, 'reassess-early', assessment='bad', outcome_id=early)
        self.assertEqual(counts(), (1, 0))
        self.assertEqual([entry['failure_type'] for entry in guards.recurrences(self.m)], [])
        self.assertEqual(self.counted(), 0)

    def test_two_failures_of_one_decision_leave_the_count_one_at_a_time(self):
        episode, first, decision = self.failure('first')
        second = self.agent_outcome(episode, decision, 'agent-bad', assessment='bad', failure_type='lost_text')['id']
        _, other, other_decision = self.failure('other')
        self.assertEqual(self.counted(), 3)
        self.reassess(decision, 'reassess-first', outcome_id=first)
        self.assertEqual(self.counted(), 2)
        self.assertEqual(self.counted_ids(), sorted([second, other]))
        self.reassess(decision, 'reassess-second', outcome_id=second)
        self.assertEqual(self.counted_ids(), [other])

    def test_an_agent_outcome_does_not_clear_the_failure(self):
        episode, outcome, decision = self.failure('first')
        self.agent_outcome(episode, decision, 'agent-good')
        self.assertEqual(self.counted(), 1)
        self.reassess(decision, 'reassess-after-agent')
        self.assertEqual(self.counted(), 0)

    def test_the_action_is_not_reachable_over_mcp(self):
        episode, outcome, decision = self.failure('first')
        self.assertNotIn('reassess', mcp.OPERATIONS)
        [tool] = [tool for tool in mcp.TOOLS if tool['name'] == 'memory_write']
        self.assertNotIn('reassess', tool['inputSchema']['properties']['operation']['enum'])
        with self.assertRaises(InvalidRecord):
            dispatch(self.m, 'memory_write', {'operation': 'reassess', 'request_key': 'mcp-reassess',
                                              'data': {'decision_id': decision, 'assessment': 'good', 'reason': 'The agent says so.'}})
        # An agent cannot record the outcome under the name of the user either.
        with self.assertRaises(InvalidRecord):
            dispatch(self.m, 'memory_write', {'operation': 'record', 'request_key': 'mcp-user-outcome', 'data': {
                'episode_id': episode, 'kind': 'outcome', 'decision_id': decision, 'supersedes': outcome,
                'expected_version': self.version(episode), 'actor': USER_ACTOR, 'evidence': self.evidence, 'payload': {
                    'observed': 'The fixture passes now.', 'assessment': 'good', 'assessment_reason': 'The agent ran it.',
                    'severity': 'none', 'attribution': 'The decoder change.'}}})
        self.assertEqual(self.counted(), 1)

    def test_the_action_refuses_invalid_and_stale_requests(self):
        episode, outcome, decision = self.failure('first')
        with self.assertRaises(InvalidRecord):
            self.reassess(outcome, 'not-a-decision')
        with self.assertRaises(InvalidRecord):
            self.reassess(decision, 'pending', assessment='pending')
        with self.assertRaises(InvalidRecord):
            action(self.m, 'reassess', {'decision_id': decision, 'assessment': 'good', 'reason': 'Fine.', 'actor': 'assistant'}, 'actor')
        # A direct call of the handler refuses another actor name as well, as the instructions handler does.
        with self.assertRaises(InvalidRecord) as refused:
            reassess(self.m, {'decision_id': decision, 'assessment': 'good', 'reason': 'Fine.', 'actor': 'assistant'}, 'direct')
        self.assertIn('user action', str(refused.exception))
        self.assertEqual(self.counted(), 1)
        with self.assertRaises(InvalidRecord):
            action(self.m, 'reassess', {'decision_id': decision, 'assessment': 'good', 'reason': ' '}, 'empty-reason')
        undecided = self.work(session='undecided')
        chosen = self.decision(undecided)
        with self.assertRaises(InvalidRecord):
            self.reassess(chosen['id'], 'no-outcome')
        _, foreign, _ = self.failure('foreign')
        with self.assertRaises(InvalidRecord):
            self.reassess(decision, 'foreign-outcome', outcome_id=foreign)
        self.assertEqual(self.counted(), 2)

    def test_the_panel_reassesses_a_counted_failure_after_a_newer_agent_outcome(self):
        # The panel sends the counted outcome, which is no longer the latest outcome of its decision.
        episode, outcome, decision = self.failure('first')
        newer = self.agent_outcome(episode, decision, 'agent-newer')['id']
        self.assertEqual([item['id'] for item in guards.recurrences(self.m)[0]['outcomes']], [outcome])
        result = self.reassess(decision, 'panel', outcome_id=outcome)
        self.assertEqual((result['reassessed'], result['supersedes']), (outcome, newer))
        self.assertEqual(self.counted(), 0)
        self.assertEqual(guards.recurrences(self.m), [])


if __name__ == '__main__':
    unittest.main()
