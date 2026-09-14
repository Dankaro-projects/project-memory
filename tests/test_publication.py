"""Exercise the public boundary with files that must never ship."""
import io
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile

from scripts.check_publication import check_commit, check_member, check_repository


class PublicationTests(unittest.TestCase):
    def test_private_content_is_rejected_without_echoing_it(self):
        samples = [
            b'gh' + b'p_' + b'a' * 40,
            b'/' + b'Users/private-person/project/document.md',
            b'C:' + b'\\Users\\private-person\\project',
            b'01' + b'a01234-1234-7123-8123-123456789abc',
            b'http://127.0.0.1:1234/' + b'private-capability' * 2 + b'/',
        ]
        for body in samples:
            for kind in ('source', 'wheel', 'sdist', 'bundle'):
                with self.subTest(kind=kind, length=len(body)):
                    with self.assertRaises(ValueError) as error:
                        check_member('memory_module/cli.py', body, kind)
                    self.assertNotIn(body.decode(), str(error.exception))

    def test_generated_files_and_unreviewed_reports_are_rejected(self):
        for name in ('docs/verification-new.json', 'docs/session.md', 'docs/images/viewer.png',
                     'tests/browser/trace.zip', '.memory/project.sqlite', 'results/output.py',
                     'memory_module/debug.log', '../README.md'):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    check_member(name, b'generated')
        with self.assertRaises(ValueError):
            check_member('project_memory_mcp-1.dist-info/results/session.json', b'{}', 'wheel')

    def test_repo_check_catches_staged_ignored_files_and_new_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            (root / 'README.md').write_text('A synthetic project.\n')
            (root / '.gitignore').write_text('results/\n')
            (root / 'results').mkdir()
            generated = root / 'results/private.json'
            generated.write_text('{}')
            self.assertEqual(check_repository(root), 2)
            subprocess.run(['git', 'add', '-f', 'results/private.json'], cwd=root, check=True)
            with self.assertRaisesRegex(ValueError, 'Unapproved public file'):
                check_repository(root)
            subprocess.run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test',
                            'commit', '-qm', 'Record a synthetic publication defect'], cwd=root, check=True)
            generated.unlink()
            self.assertEqual(check_repository(root), 2)
            with self.assertRaisesRegex(ValueError, 'Unapproved public file'):
                check_commit(root, 'HEAD')

    def test_actual_archives_reject_a_private_report(self):
        script = Path(__file__).resolve().parents[1] / 'scripts/check_artifacts.py'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            members = {'memory_module/viewer.html': b'<html></html>'}
            for weight in (400, 700):
                members[f'memory_module/assets/manrope-latin-{weight}.woff2'] = b'wOF2fixture'
            for name in ('fixture.whl', 'fixture.mcpb'):
                with zipfile.ZipFile(root / name, 'w') as archive:
                    for member, body in members.items():
                        archive.writestr(member, body)

            def source_archive(extra):
                with tarfile.open(root / 'fixture.tar.gz', 'w:gz') as archive:
                    for name, body in {**members, **extra}.items():
                        entry = tarfile.TarInfo('fixture/' + name)
                        entry.size = len(body)
                        archive.addfile(entry, io.BytesIO(body))

            source_archive({})
            result = subprocess.run([sys.executable, str(script), str(root)], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            source_archive({'docs/verification-private.json': b'{"private": true}'})
            result = subprocess.run([sys.executable, str(script), str(root)], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b'Unapproved public file', result.stderr)
            source_archive({})
            with zipfile.ZipFile(root / 'fixture.mcpb', 'a') as archive:
                archive.writestr('trace.json', b'{}')
            result = subprocess.run([sys.executable, str(script), str(root)], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b'Unapproved public file', result.stderr)


if __name__ == '__main__':
    unittest.main()
