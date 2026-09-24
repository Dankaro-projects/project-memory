"""Idle work expires and can be restored.

On 24 September 2026 the project created 6.5 work items per active day and closed 2.0, and nothing ever left the list
unless someone closed it. The user chose that an open item archives itself after 14 days without activity, or after 7
days when it waits on another item, stays searchable, and returns by one sentence in the chat.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil
import tempfile
import unittest

from memory_module import Memory, codex_host, planning
from memory_module.planning import latest
from memory_module.workspace import action


class ExpiryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name)
        self.m = Memory.create(self.root / '.memory' / 'project.sqlite', 'Expiry', ['Keep the list short.'])
        codex_host.initialize(self.m)
        self.now = datetime.now(timezone.utc)

    def tearDown(self):
        self.m.close()
        shutil.rmtree(self.temp.name, ignore_errors=True)

    def item(self, key, state='ready', **payload):
        return action(self.m, 'plan', {'title': 'Item ' + key, 'objective': 'Do it.', 'criterion': 'It is done.', 'subject': 'code',
                                       'payload': {'state': state, 'next_action': 'Start.', 'autonomy': 'act', 'scope': 'Only this.',
                                                   'reason': 'Planned.', **payload}}, 'plan-' + key)['episode_id']

    def later(self, days):
        return self.now + timedelta(days=days)

    def test_an_item_idle_for_14_days_is_archived_and_a_recent_one_is_not(self):
        idle = self.item('idle')
        self.assertEqual(planning.expire(self.m, now=self.later(13)), [])
        [archived] = planning.expire(self.m, now=self.later(15))
        self.assertEqual(archived['episode_id'], idle)
        plan = latest(self.m, idle, 'work_plan')
        self.assertEqual(plan['state'], 'cancelled')
        self.assertEqual(plan['archived']['restore_state'], 'ready')
        self.assertEqual(plan['archived']['idle_days'], 14)
        # A second run finds nothing more to archive.
        self.assertEqual(planning.expire(self.m, now=self.later(16)), [])

    def test_an_item_waiting_on_another_expires_after_7_days(self):
        first = self.item('first', state='backlog')
        waiting = self.item('waiting', depends_on=[{'episode_id': first, 'reason': 'It needs the first item.'}])
        archived = planning.expire(self.m, now=self.later(8))
        self.assertEqual([item['episode_id'] for item in archived], [waiting])
        self.assertEqual(latest(self.m, waiting, 'work_plan')['archived']['idle_days'], 7)

    def test_finished_work_stays_and_a_parent_waits_for_its_open_children(self):
        done = self.item('done', state='cancelled')
        parent = self.item('parent', item_type='epic')
        child = self.item('child', parent_id=parent)
        first = {item['episode_id'] for item in planning.expire(self.m, now=self.later(15))}
        self.assertEqual(first, {child})
        second = {item['episode_id'] for item in planning.expire(self.m, now=self.later(15))}
        self.assertEqual(second, {parent})
        self.assertNotIn('archived', latest(self.m, done, 'work_plan'))

    def test_a_closed_episode_is_skipped_and_does_not_stop_the_others(self):
        closed = self.item('closed')
        other = self.item('other')
        self.m.record(closed, 'episode_status', {'status': 'abandoned', 'reason': 'Given up.'},
                      expected_version=self.m.episode(closed)['version'], actor='assistant', request_key='abandon')
        archived = [item['episode_id'] for item in planning.expire(self.m, now=self.later(15))]
        self.assertEqual(archived, [other])

    def test_the_periods_are_settings(self):
        idle = self.item('idle')
        planning.set_expiry_days(self.m, idle=30, waiting=10)
        self.assertEqual(planning.expiry_days(self.m), {'idle': 30, 'waiting': 10})
        self.assertEqual(planning.expire(self.m, now=self.later(15)), [])
        self.assertEqual([item['episode_id'] for item in planning.expire(self.m, now=self.later(31))], [idle])

    def test_a_restore_returns_the_item_to_its_earlier_state(self):
        idle = self.item('idle', state='review')
        planning.expire(self.m, now=self.later(15))
        source = self.m.source('ask', 'Restore', 'The user asks to restore it.', 'Bring back Item idle.', 'user', subject='code')['id']
        planning.restore(self.m, idle, evidence=[{'source_id': source, 'reason': 'The user asked in the chat.'}], request_key='restore')
        plan = latest(self.m, idle, 'work_plan')
        self.assertEqual(plan['state'], 'review')
        self.assertNotIn('archived', plan)

    def test_the_session_start_names_what_was_archived(self):
        self.item('idle')
        text = codex_host.expiry_sentence(planning.expire(self.m, now=self.later(15)))
        self.assertIn('Item idle', text)
        self.assertIn('restore', text)


if __name__ == '__main__':
    unittest.main()
