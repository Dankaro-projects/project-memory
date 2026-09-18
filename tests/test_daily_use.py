"""Daily use: a session digest counts the files its commits changed, a source reads in one call, metrics are paged."""
import json
import os
import subprocess
import unittest

from memory_module import sessions
from memory_module.mcp import dispatch
from tests.test_sessions import Fixture, claude_line


class CommittedFilesTests(Fixture):
    def git(self, *args, at=None):
        env = {**os.environ, 'GIT_AUTHOR_NAME': 'Test', 'GIT_AUTHOR_EMAIL': 'test@example.com',
               'GIT_COMMITTER_NAME': 'Test', 'GIT_COMMITTER_EMAIL': 'test@example.com'}
        if at:
            env.update(GIT_AUTHOR_DATE=at, GIT_COMMITTER_DATE=at)
        subprocess.run(['git', '-C', str(self.root), *args], check=True, capture_output=True, env=env)

    def test_a_session_that_edits_through_the_shell_reports_the_files_of_its_commits(self):
        self.git('init', '-q')
        (self.root / 'version.py').write_text('VERSION = "2"\n')
        (self.root / 'pyproject.toml').write_text('version = "2"\n')
        self.git('add', 'version.py', 'pyproject.toml')
        self.git('commit', '-q', '-m', 'Prepare version 2', at='2026-09-10T10:01:30+00:00')
        self.transcript('s1', [
            claude_line('user', 'Prepare the release.', '2026-09-10T10:00:00Z', cwd=str(self.root)),
            claude_line('assistant', [{'type': 'tool_use', 'id': 't1', 'name': 'Bash',
                                       'input': {'command': "sed -i '' s/1/2/ version.py pyproject.toml && git commit -am x"}}],
                        '2026-09-10T10:01:00Z'),
            claude_line('user', [{'type': 'tool_result', 'tool_use_id': 't1', 'content': 'ok'}], '2026-09-10T10:02:00Z'),
        ])
        sessions.collect(self.m, found=self.found)
        [digest] = sessions.digests(self.m)
        self.assertEqual(digest['files'], 2)
        source = self.m.read(digest['source_id'], detail=True)
        self.assertIn('2 files changed, 1 commits', source['summary'])
        self.assertIn('- version.py', source['body'])
        self.assertIn('Files changed: 2.', source['body'])

    def test_the_record_view_of_a_source_carries_the_start_of_its_body(self):
        body = 'Line of the digest.\n' * 2000
        source = self.m.source('long', 'A long source', 'Summary only.', body, 'tool')
        found = dispatch(self.m, 'memory_get', {'view': 'record', 'id': source['id']})
        self.assertTrue(found['body'].startswith('Line of the digest.'))
        self.assertTrue(found['body_more'])
        self.assertEqual(found['body_characters'], len(body))
        self.assertLessEqual(len(json.dumps(found)), 6000)
        following = dispatch(self.m, 'memory_get', {'view': 'record', 'id': source['id'], 'body_offset': found['next_offset']})
        self.assertEqual(following['body_offset'], found['next_offset'])


class MetricsTests(Fixture):
    def test_metrics_page_their_groups_instead_of_refusing(self):
        for number in range(60):
            episode = self.m.start(f'Work {number}', 'Do the work.', 'code', f'Criterion number {number} ' + 'x' * 300, subject='code')
            self.m.record(episode['id'], 'decision', {
                'decision': 'Do it.', 'why': 'Needed.', 'expected': 'Done.', 'reconsider_when': 'Never.',
                'uncertainty': 'None known.', 'alternatives': ['Skip it.']},
                expected_version=episode['version'], request_key=f'd{number}', actor='test',
                evidence=[{'source_id': self.m.source(f's{number}', 'Fact', 'Fact.', 'Fact.', 'user')['id'], 'reason': 'Basis.'}])
        found = dispatch(self.m, 'memory_get', {'view': 'metrics', 'max_chars': 3000})
        self.assertLessEqual(len(json.dumps(found)), 3000)
        self.assertEqual(found['totals']['decisions'], 60)
        self.assertEqual(found['groups_total'], 60)
        self.assertTrue(found['more'])
        self.assertGreater(len(found['groups']), 0)
        following = dispatch(self.m, 'memory_get', {'view': 'metrics', 'offset': found['next_offset'], 'max_chars': 3000})
        self.assertEqual(following['offset'], found['next_offset'])


if __name__ == '__main__':
    unittest.main()
