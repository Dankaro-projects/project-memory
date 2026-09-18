"""A check reads its evidence even when it runs in a worktree of its own.

A check runs in the worktree recorded on the work plan, while the folder that holds its records, its sources and
its receipts belongs to the project database. The reviewer has Read, Glob and Grep and may open the checked folder
only, so for a work item whose worktree lies outside the project every file is unreachable, and a linked worktree
holds a .git file rather than a history. On 18 September 2026 a check returned seven criteria and four constraints
as unknown for that reason alone. The evidence is now carried in the packet, and the commits of the checked folder
with it.
"""
from pathlib import Path
import subprocess
import tempfile
import unittest

from memory_module import Memory, reviews
from memory_module.install import setup
from memory_module.workspace import action


def git(folder, *args):
    subprocess.run(['git', '-C', str(folder), *args], check=True, capture_output=True, text=True)


class CheckEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        base = Path(self.temp.name).resolve()
        self.root = base / 'project'
        self.root.mkdir()
        git(self.root, 'init', '-q', '-b', 'main')
        git(self.root, 'config', 'user.email', 'test@example.invalid')
        git(self.root, 'config', 'user.name', 'Test')
        (self.root / 'parser.py').write_text('VALUE = 1\n', encoding='utf-8')
        git(self.root, 'add', 'parser.py')
        git(self.root, 'commit', '-q', '-m', 'Start')
        self.worktree = base / 'project-wave'
        git(self.root, 'worktree', 'add', '-q', '-b', 'wave', str(self.worktree))
        (self.worktree / 'parser.py').write_text('VALUE = 2\n', encoding='utf-8')
        git(self.worktree, 'commit', '-q', '-am', 'Change the parser on the branch')
        info = setup(self.root, requirements=['Keep the parser small.'])
        self.m = Memory(info['database'])
        reviews.configure(self.m, self.root, 'codex')
        self.packets = base / 'packets'
        self.packets.mkdir()

    def tearDown(self):
        self.m.close()
        self.temp.cleanup()

    def item(self, key, worktree=None, body='The user asked for the change.'):
        payload = {'state': 'ready', 'next_action': 'Check the parser.', 'autonomy': 'act',
                   'scope': 'Change the parser only.', 'reason': 'The user requests the change.'}
        if worktree is not None:
            payload['worktree'] = str(worktree)
        episode = action(self.m, 'plan', {'title': 'Change the parser', 'objective': 'Set the value to 2.',
                                          'criterion': 'parser.py sets VALUE to 2.', 'subject': 'code',
                                          'payload': payload}, key)['episode_id']
        source = self.m.source(source_key='results-' + key, title='Measured results of ' + key, origin='tool',
                               subject='code', summary='What was run.', body=body)['id']
        self.m.record(episode_id=episode, kind='decision', actor='assistant', expected_version=1,
                      request_key=key + '-decision', evidence=[{'source_id': source, 'reason': 'The user asked.'}],
                      payload={'decision': 'Set the value to 2.', 'why': 'The user asked.', 'expected': 'The value is 2.',
                               'alternatives': ['Leave it at 1.'], 'uncertainty': 'None observed.',
                               'reconsider_when': 'The parser changes.'})
        return episode, source

    def stored(self, packet, key):
        """The source of this fixture, found by its key, because a work plan records one of its own."""
        return next(entry for entry in packet['sources'] if entry.get('source_key') == 'results-' + key)

    def packet(self, episode, key, readable):
        value, _ = reviews.snapshot(self.m, episode, 'outcome')
        folder = self.packets / key
        folder.mkdir()
        return reviews.evidence_manifest(value, folder, readable=readable)

    def test_an_unreadable_folder_puts_every_record_and_source_in_the_packet(self):
        episode, source = self.item('inline', worktree=self.worktree)
        packet = self.packet(episode, 'inline', readable=False)
        self.assertTrue(packet['sources'])
        for group in ('records', 'sources'):
            for entry in packet[group]:
                self.assertNotIn('file', entry)
        self.assertEqual(self.stored(packet, 'inline')['body'], 'The user asked for the change.')
        self.assertIn('carried in the packet itself', packet['evidence_access'])

    def test_a_readable_folder_keeps_the_packet_it_has_today(self):
        episode, _ = self.item('files')
        packet = self.packet(episode, 'files', readable=True)
        self.assertTrue(all('file' in entry for entry in packet['sources']))
        self.assertIn('The files contain complete records.', packet['evidence_access'])

    def test_a_record_too_long_for_the_packet_says_how_much_was_left_out(self):
        episode, _ = self.item('long', worktree=self.worktree, body='A measured line.\n' * 4000)
        entry = self.stored(self.packet(episode, 'long', readable=False), 'long')
        self.assertEqual(entry['characters_omitted'], entry['characters'] - len(entry['partial_content']))
        self.assertLessEqual(len(entry['partial_content']), reviews.EVIDENCE_INLINE_CHARACTERS)
        self.assertGreater(entry['characters_omitted'], 0)

    def test_the_run_folder_is_readable_only_inside_the_checked_folder(self):
        inside = self.root / '.memory' / 'agent-runs' / 'check_one'
        self.assertTrue(reviews.folder_is_readable(inside, str(self.root)))
        self.assertFalse(reviews.folder_is_readable(inside, str(self.worktree)))

    def test_a_requested_check_carries_the_commits_of_the_checked_folder(self):
        episode, _ = self.item('history', worktree=self.worktree)
        history = reviews.request(self.m, episode, request_key='check-history', retry=True)['snapshot']['checked_folder_history']
        self.assertTrue(history['read'])
        self.assertEqual(history['commits'][0]['subject'], 'Change the parser on the branch')
        self.assertTrue(any('parser.py' in row for row in history['commits'][0]['changed']))
        self.assertEqual(history['head_on_a_remote'], False)
        self.assertEqual(history['remote_references'], [])

    def test_the_history_stays_outside_the_signature_and_writes_nothing_into_the_worktree(self):
        episode, _ = self.item('signature', worktree=self.worktree)
        before = sorted(path.name for path in self.worktree.iterdir())
        _, first = reviews.snapshot(self.m, episode, 'outcome')
        run = reviews.request(self.m, episode, request_key='check-signature', retry=True)
        self.assertEqual(run['signature'], first)
        self.assertNotIn('checked_folder_history', reviews.snapshot(self.m, episode, 'outcome')[0])
        self.assertEqual(sorted(path.name for path in self.worktree.iterdir()), before)


if __name__ == '__main__':
    unittest.main()
