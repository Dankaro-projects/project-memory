"""The Obsidian vault export: the notes it writes, the folder it owns and the refresh it starts."""
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from memory_module import Memory, Conflict, InvalidRecord
from memory_module import vault

WIKILINK = re.compile(r'\[\[([^\]]+)\]\]')


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)
        self.now = '2026-09-23T10:00:00+00:00'
        self.m = Memory.create(self.root / 'project.sqlite', 'Vault project',
                               ['Keep the records honest.'], clock=lambda: self.now)
        self.ep = self.m.start('Read the fleet register', 'Report the classed tonnage',
                               'analysis', 'The report names its source.', subject='code')['id']
        self.source = self.m.source('fleet-register', 'Fleet register extract',
                                    'The register lists 243,000 vessels.',
                                    'The extract was taken on 1 September 2026.', 'tool', subject='code')['id']
        self.refs = [{'source_id': self.source, 'reason': 'The extract states the count.'}]
        self.key = 0
        self.vault = self.root / 'Vault'

    def tearDown(self):
        self.m.close()
        shutil.rmtree(self.tmp.name, ignore_errors=True)

    def record(self, kind, payload, **kw):
        self.key += 1
        return self.m.record(self.ep, kind, payload, expected_version=self.m.episode(self.ep)['version'],
                             request_key=str(self.key), actor='test', **kw)['id']

    def decision(self, text='Read the register directly', **kw):
        return self.record('decision', {'decision': text, 'why': 'The extract is current.',
                                        'expected': 'The report names the register.',
                                        'reconsider_when': 'The extract is older than one quarter.'},
                           evidence=self.refs, **kw)

    def managed(self):
        return self.vault / vault.FOLDER_NAME

    def export(self, **kw):
        return vault.export(self.m, kw.pop('destination', self.vault), **kw)

    def notes(self):
        return {path.relative_to(self.managed()).as_posix() for path in self.managed().rglob('*.md')}


class NoteTests(Fixture):
    def test_every_kind_receives_a_note_with_its_frontmatter(self):
        decision = self.decision()
        self.record('action', {'action': 'Read the register'}, decision_id=decision)
        self.record('outcome', {'observed': 'The register returned 243,000 vessels.', 'assessment': 'good',
                                'assessment_reason': 'The count matches the extract.', 'severity': 'none',
                                'attribution': 'agent', 'completion': 'complete'},
                    decision_id=decision, evidence=self.refs)
        result = self.export()
        self.assertEqual(result['written'], result['notes'])
        kinds = {}
        for path in self.managed().rglob('*.md'):
            text = path.read_text(encoding='utf-8')
            self.assertTrue(text.startswith('---\n'), path)
            self.assertIn('project_memory_export: true', text)
            self.assertIn('project: "Vault project"', text)
            if path.name == 'README.md':
                continue
            found = re.search(r'^kind: "([a-z_]+)"$', text, re.M)
            if found:
                kinds[found.group(1)] = kinds.get(found.group(1), 0) + 1
        self.assertEqual(set(kinds), {'episode', 'decision', 'action', 'outcome', 'source', 'project_revision'})
        note = next(p for p in self.managed().rglob('*.md') if 'Decision.' in p.name)
        text = note.read_text(encoding='utf-8')
        self.assertIn('state: "recorded"', text)
        self.assertIn('subject: "code"', text)
        self.assertIn('date: "2026-09-23"', text)
        self.assertIn('## Why\nThe extract is current.', text)

    def test_a_work_item_is_a_folder_and_evidence_sits_beside_it(self):
        self.decision()
        self.export()
        item = next(p for p in (self.managed() / 'Work items').iterdir() if p.is_dir())
        self.assertTrue((item / (item.name + '.md')).is_file())
        self.assertTrue(any('Decision.' in p.name for p in item.iterdir()))
        self.assertTrue(any(p.name.startswith('Source.') for p in (self.managed() / 'Sources').iterdir()))
        self.assertTrue((self.managed() / 'Requirements').is_dir())

    def test_a_lesson_sits_in_the_shared_folder(self):
        self.record('lesson', {'when': 'An extract is older than one quarter.', 'do': 'Read the register again.',
                               'because': 'The counts move with each quarter.', 'exceptions': 'None observed.',
                               'paths': ['src']}, evidence=self.refs)
        self.export()
        self.assertTrue(any(p.name.startswith('Lesson.') for p in (self.managed() / 'Lessons').iterdir()))

    def test_every_wikilink_resolves_to_a_note_of_the_same_run(self):
        decision = self.decision()
        self.record('action', {'action': 'Read the register'}, decision_id=decision)
        self.export()
        stems = {path.stem for path in self.managed().rglob('*.md')}
        links = set()
        for path in self.managed().rglob('*.md'):
            links.update(WIKILINK.findall(path.read_text(encoding='utf-8')))
        self.assertTrue(links)
        self.assertEqual(links - stems, set())

    def test_a_decision_links_its_work_item_and_its_evidence(self):
        self.decision()
        self.export()
        note = next(p for p in self.managed().rglob('*.md') if 'Decision.' in p.name)
        text = note.read_text(encoding='utf-8')
        self.assertIn('work_item: "[[Read the fleet register (', text)
        self.assertIn('evidence:\n  - "[[Source. Fleet register extract (', text)
        self.assertIn('## Evidence\n- [[Source. Fleet register extract (', text)

    def test_two_records_with_one_title_receive_two_names(self):
        self.record('note', {'text': 'The register was read twice.'})
        self.record('note', {'text': 'The register was read twice.'})
        self.export()
        names = [p.name for p in self.managed().rglob('*.md') if p.name.startswith('Note.')]
        self.assertEqual(len(names), 2)
        self.assertEqual(len(set(names)), 2)

    def test_two_work_items_with_one_title_receive_two_folders(self):
        self.m.start('Read the fleet register', 'Report the owners', 'analysis', 'The report names its source.',
                     subject='code')
        self.export()
        folders = [p.name for p in (self.managed() / 'Work items').iterdir() if p.is_dir()]
        self.assertEqual(len(folders), 2)
        self.assertEqual(len(set(folders)), 2)

    def test_a_long_title_is_shortened_in_the_name_and_kept_in_the_note(self):
        title = 'Read the register ' + 'and report the classed tonnage of every owner ' * 6
        self.decision(title.strip())
        self.export()
        note = next(p for p in self.managed().rglob('*.md') if 'Decision.' in p.name)
        self.assertLess(len(note.name), 200)
        self.assertIn('# ' + title.strip(), note.read_text(encoding='utf-8'))

    def test_a_colon_in_a_title_does_not_break_the_frontmatter(self):
        self.decision('Read the register: the classed tonnage of 2026')
        self.export()
        note = next(p for p in self.managed().rglob('*.md') if 'Decision.' in p.name)
        text = note.read_text(encoding='utf-8')
        self.assertIn('aliases:\n  - "Read the register the classed tonnage of 2026"', text)

    def test_the_readme_states_the_direction_of_the_export(self):
        self.export()
        text = (self.managed() / 'README.md').read_text(encoding='utf-8')
        self.assertIn('the source of truth', text)
        self.assertIn('project-memory export --obsidian', text)


class ReceiptTests(Fixture):
    def receipt(self):
        from memory_module import codex_host
        codex_host.initialize(self.m)
        with self.m._write():
            codex_host.receipt(self.m, session_id='s1', turn_id='t1', event_name='PreToolUse',
                               payload={'tool': 'Bash'}, tool_name='Bash')

    def test_receipts_are_left_out_by_default_and_added_on_request(self):
        self.receipt()
        self.export()
        self.assertFalse((self.managed() / 'Host receipts').exists())
        result = self.export(include_receipts=True)
        self.assertTrue((self.managed() / 'Host receipts').is_dir())
        self.assertGreaterEqual(result['written'], 1)

    def test_dropping_the_receipts_removes_their_notes(self):
        self.receipt()
        self.export(include_receipts=True)
        result = self.export()
        self.assertGreaterEqual(result['removed'], 1)
        self.assertFalse((self.managed() / 'Host receipts').exists())


class RefreshTests(Fixture):
    def test_a_second_run_writes_nothing(self):
        self.decision()
        self.export()
        result = self.export()
        self.assertEqual(result['written'], 0)
        self.assertEqual(result['unchanged'], result['notes'])

    def test_a_new_record_appears_without_a_full_run(self):
        self.export()
        before = self.notes()
        self.decision()
        result = self.export()
        self.assertEqual(result['written'], 1)
        self.assertEqual(len(self.notes() - before), 1)

    def test_a_changed_state_rewrites_its_note(self):
        decision = self.decision()
        self.export()
        note = next(p for p in self.managed().rglob('*.md') if 'Decision.' in p.name)
        self.assertIn('state: "recorded"', note.read_text(encoding='utf-8'))
        self.m.source('fleet-register', 'Fleet register extract', 'The register lists 244,000 vessels.',
                      'The extract was taken on 1 October 2026.', 'tool', subject='code')
        result = self.export()
        self.assertGreaterEqual(result['written'], 1)
        self.assertIn('state: "needs_review"', note.read_text(encoding='utf-8'))
        self.assertTrue(decision)

    def test_full_rewrites_every_note(self):
        self.decision()
        self.export()
        result = self.export(full=True)
        self.assertEqual(result['written'], result['notes'])
        self.assertEqual(result['unchanged'], 0)

    def test_a_note_deleted_by_hand_is_written_again(self):
        self.decision()
        self.export()
        note = next(p for p in self.managed().rglob('*.md') if 'Decision.' in p.name)
        note.unlink()
        self.export()
        self.assertTrue(note.is_file())

    def test_the_folder_is_recorded_and_reused(self):
        self.export()
        self.assertEqual(vault.configured(self.m), self.vault.resolve())
        result = vault.export(self.m)
        self.assertEqual(result['vault'], str(self.vault.resolve()))
        fresh = Memory.create(self.root / 'other.sqlite', 'Other project', ['Keep the records honest.'])
        with self.assertRaises(InvalidRecord):
            vault.export(fresh)
        fresh.close()


class FolderTests(Fixture):
    def test_nothing_outside_the_managed_folder_is_touched(self):
        self.vault.mkdir(parents=True)
        mine = self.vault / 'My note.md'
        mine.write_text('# My own note\n', encoding='utf-8')
        other = self.vault / 'Reading'
        other.mkdir()
        (other / 'Book.md').write_text('# A book\n', encoding='utf-8')
        self.export()
        self.assertEqual(mine.read_text(encoding='utf-8'), '# My own note\n')
        self.assertEqual((other / 'Book.md').read_text(encoding='utf-8'), '# A book\n')

    def test_a_folder_of_someone_else_refuses_the_run(self):
        self.managed().mkdir(parents=True)
        (self.managed() / 'Notes.md').write_text('# Not ours\n', encoding='utf-8')
        with self.assertRaises(Conflict) as caught:
            self.export()
        self.assertIn('Notes.md', str(caught.exception))
        self.assertEqual((self.managed() / 'Notes.md').read_text(encoding='utf-8'), '# Not ours\n')

    def test_a_file_without_the_marker_is_kept_and_reported(self):
        self.export()
        mine = self.managed() / 'My reading.md'
        mine.write_text('# My reading of the decisions\n', encoding='utf-8')
        result = self.export(full=True)
        self.assertEqual(result['kept'], ['My reading.md'])
        self.assertTrue(mine.is_file())

    def test_a_moved_note_leaves_no_copy_behind(self):
        self.decision()
        self.export()
        note = next(p for p in self.managed().rglob('*.md') if 'Decision.' in p.name)
        moved = note.parent / ('Decision. Moved by hand.md')
        note.rename(moved)
        self.m.db.execute('UPDATE vault_notes SET path=? WHERE path=?',
                          (moved.relative_to(self.managed()).as_posix(),
                           note.relative_to(self.managed()).as_posix()))
        self.m.db.commit()
        result = self.export()
        self.assertEqual(result['moved'], 1)
        self.assertFalse(moved.is_file())
        self.assertTrue(note.is_file())


class HookTests(Fixture):
    def test_the_refresh_is_needed_only_after_a_new_record(self):
        self.assertFalse(vault.needs_refresh(self.m))
        self.export()
        self.assertTrue(vault.hook_enabled(self.m))
        self.assertFalse(vault.needs_refresh(self.m))
        self.now = '2026-09-23T11:00:00+00:00'
        self.decision()
        self.assertTrue(vault.needs_refresh(self.m))

    def test_the_user_switches_the_refresh_off(self):
        self.export(hook=False)
        self.now = '2026-09-23T11:00:00+00:00'
        self.decision()
        self.assertFalse(vault.hook_enabled(self.m))
        self.assertFalse(vault.needs_refresh(self.m))
        vault.configure(self.m, hook=True)
        self.assertTrue(vault.needs_refresh(self.m))

    def test_an_unconfigured_project_starts_no_refresh(self):
        self.assertIsNone(vault.configured(self.m))
        self.assertFalse(vault.hook_enabled(self.m))

    def test_a_detached_refresh_writes_the_notes(self):
        self.export()
        self.now = '2026-09-23T11:00:00+00:00'
        self.decision()
        started = vault.refresh_detached(self.m)
        self.assertTrue(started['started'])
        self.assertFalse(vault.needs_refresh(self.m))
        import time
        for _ in range(100):
            if any('Decision.' in p.name for p in self.managed().rglob('*.md')):
                break
            time.sleep(0.1)
        self.assertTrue(any('Decision.' in p.name for p in self.managed().rglob('*.md')),
                        Path(started['log']).read_text(encoding='utf-8') if Path(started['log']).is_file() else '')
        import os
        try:
            os.waitpid(started['pid'], 0)
        except ChildProcessError:
            pass


if __name__ == '__main__':
    unittest.main()
