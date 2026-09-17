"""Agent checks read the worktree of the work (section 17.13 of .memory/build/rebuild-spec.md).

A check that ran in the project folder while the work was committed in another git worktree could not read the code
and returned uncertain for every criterion. A work plan now records the worktree, and the check reads it.
"""
from pathlib import Path
import subprocess
import tempfile
import unittest

from memory_module import InvalidRecord, Memory, reviews
from memory_module.install import setup
from memory_module.workspace import action


def git(folder, *args):
    subprocess.run(['git', '-C', str(folder), *args], check=True, capture_output=True, text=True)


class CheckWorktreeTests(unittest.TestCase):
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
        git(self.worktree, 'commit', '-q', '-am', 'Change on the branch')
        self.other = base / 'other'
        self.other.mkdir()
        git(self.other, 'init', '-q')
        info = setup(self.root, requirements=['Keep the parser small.'])
        self.m = Memory(info['database'])
        reviews.configure(self.m, self.root, 'codex')

    def tearDown(self):
        self.m.close()
        self.temp.cleanup()

    def item(self, worktree=None, key='fixture'):
        payload = {'state': 'ready', 'next_action': 'Check the parser.', 'scope': 'Change the parser only.',
                   'autonomy': 'act', 'reason': 'The user requests the change.'}
        if worktree is not None:
            payload['worktree'] = str(worktree)
        return action(self.m, 'plan', {'title': 'Change the parser', 'objective': 'Set the value to 2.',
                                       'criterion': 'parser.py sets VALUE to 2.', 'subject': 'code', 'payload': payload}, key)['episode_id']

    def test_a_check_of_a_plan_with_a_worktree_reads_that_worktree(self):
        episode = self.item(self.worktree)
        value, _ = reviews.snapshot(self.m, episode, 'outcome')
        self.assertEqual(value['project'], str(self.worktree))
        self.assertEqual(value['checked_folder']['branch'], 'wave')
        run = reviews.request(self.m, episode, request_key='check-worktree', retry=True)
        self.assertEqual(run['snapshot']['project'], str(self.worktree))

    def test_a_change_in_the_worktree_makes_the_check_stale_and_a_change_in_the_project_folder_does_not(self):
        episode = self.item(self.worktree)
        _, first = reviews.snapshot(self.m, episode, 'outcome')
        (self.root / 'parser.py').write_text('VALUE = 3\n', encoding='utf-8')
        self.assertEqual(reviews.snapshot(self.m, episode, 'outcome')[1], first)
        (self.worktree / 'parser.py').write_text('VALUE = 4\n', encoding='utf-8')
        self.assertNotEqual(reviews.snapshot(self.m, episode, 'outcome')[1], first)

    def test_a_missing_or_foreign_worktree_is_refused_with_its_fix(self):
        missing = self.item(self.root.parent / 'gone', key='missing')
        with self.assertRaisesRegex(InvalidRecord, 'does not exist'):
            reviews.snapshot(self.m, missing, 'outcome')
        foreign = self.item(self.other, key='foreign')
        with self.assertRaisesRegex(InvalidRecord, 'not a git worktree of the project repository'):
            reviews.snapshot(self.m, foreign, 'outcome')
        with self.assertRaisesRegex(InvalidRecord, 'absolute path'):
            self.item('relative/folder', key='relative')

    def test_a_plan_without_a_worktree_is_checked_in_the_project_folder_and_names_its_branch(self):
        episode = self.item()
        value, _ = reviews.snapshot(self.m, episode, 'outcome')
        self.assertEqual(value['project'], str(self.root))
        self.assertEqual(value['checked_folder']['branch'], 'main')


if __name__ == '__main__':
    unittest.main()
