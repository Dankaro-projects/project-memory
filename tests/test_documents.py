import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from memory_module import Memory, InvalidRecord, BudgetTooSmall, dumps
from memory_module import codex_host
from memory_module.mcp import dispatch, tool_result, write


class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.m = Memory.create(self.root/'memory.sqlite', 'Documents', ['Preserve quality and explicit acceptance.'])
        codex_host.initialize(self.m)
        self.path = self.root/'Product vision.md'
        self.body = '# Product vision\r\n\r\nUse local storage, except when a customer explicitly requests an export.\r\n'
        self.path.write_bytes(self.body.encode())

    def tearDown(self):
        self.m.close()
        self.temp.cleanup()

    def decision(self, source):
        ep = self.m.start('Amberlake rollout', 'Check the storage approach.', 'review', 'The exception survives.', subject='code')
        result = self.m.record(ep['id'], 'decision', {'decision':'Use local storage.', 'why':'The vision requires it.',
            'expected':'The project remains portable.', 'reconsider_when':'The vision changes.'}, expected_version=0,
            request_key='choice', actor='test', evidence=[{'source_id':source, 'reason':'This version states the agreed scope.'}])
        return self.m.read(result['id'])

    def test_verbatim_capture_reuses_unchanged_file_without_accepting_claims(self):
        first = self.m.document(str(self.path))
        again = self.m.document(str(self.path))
        self.assertFalse(again['created'])
        self.assertEqual(first['id'], again['id'])
        self.assertEqual(self.m.read(first['id'], detail=True)['body'], self.body)
        self.assertEqual(self.m.db.execute('SELECT count(*) FROM events').fetchone()[0], 0)
        self.assertEqual(self.m.requirements, ['Preserve quality and explicit acceptance.'])

    def test_changed_missing_and_restored_files_propagate_to_decision_and_seen(self):
        source = self.m.document(str(self.path))['id']
        decision = self.decision(source)
        packet = self.m.context('storage', subject='code', budget=4000)
        seen = {r['id']:r['signature'] for r in packet['records'] if 'signature' in r}
        self.path.write_text('# Product vision\nThe project now permits a hosted export.')
        self.assertEqual(self.m.read(source)['status'], 'file_changed')
        self.assertEqual(self.m.read(decision['id'])['status'], 'needs_review')
        again = self.m.context('storage', subject='code', budget=4000, seen=seen)
        self.assertIn(decision['id'], [r['id'] for r in again['records']])
        self.assertTrue(self.m.signals()['signals'])
        self.path.unlink()
        self.assertEqual(self.m.read(source)['status'], 'file_missing')
        self.path.write_bytes(self.body.encode())
        self.assertEqual(self.m.read(source)['status'], 'current_copy')

    def test_refresh_preserves_old_evidence_and_unchanged_subject(self):
        old = self.m.document(str(self.path))['id']
        decision = self.decision(old)
        self.path.write_text('# Product vision\nUse a reviewed export option.')
        new = self.m.document(str(self.path))
        self.assertEqual(new['version'], 2)
        self.assertEqual(self.m.read(old, detail=True)['body'], self.body)
        self.assertEqual(self.m.read(old)['status'], 'superseded')
        self.assertEqual(self.m.read(decision['id'])['evidence'][0]['source_id'], old)
        with self.assertRaises(InvalidRecord): self.m.document(str(self.path), subject='code')

    def test_capture_handles_unreadable_and_oversized_files_without_partial_writes(self):
        source = self.m.document(str(self.path))['id']
        self.path.write_bytes(b'\xff')
        self.assertEqual(self.m.read(source)['status'], 'file_unreadable')
        with self.assertRaises(UnicodeError): self.m.document(str(self.path))
        self.path.write_bytes(b'x'*5_000_001)
        self.assertEqual(self.m.read(source)['status'], 'file_unreadable')
        with self.assertRaises(InvalidRecord): self.m.document(str(self.path))
        self.assertEqual(self.m.db.execute('SELECT count(*) FROM sources').fetchone()[0], 1)

    def test_cli_batch_is_atomic_and_does_not_rewrite_files(self):
        run = subprocess.run([sys.executable, '-m', 'memory_module', '--db', str(self.m.path), 'document',
            str(self.path), str(self.root/'missing.md'), '--subject', 'general'], capture_output=True, text=True)
        self.assertEqual(run.returncode, 2)
        self.assertEqual(self.m.db.execute('SELECT count(*) FROM sources').fetchone()[0], 0)
        self.assertEqual(self.path.read_bytes(), self.body.encode())

    def test_legacy_document_sources_do_not_start_reading_arbitrary_paths(self):
        source = self.m.source(str(self.path), 'Earlier source', 'Earlier capture.', 'Earlier capture.', 'document')['id']
        self.path.unlink()
        self.assertEqual(self.m.read(source)['status'], 'current_copy')

    def test_explicit_capture_deadline_does_not_renew_on_unchanged_import(self):
        source = self.m.document(str(self.path), review_after='2000-01-01T00:00:00Z')
        again = self.m.document(str(self.path))
        self.assertEqual(again['status'], 'review_due')
        self.assertEqual(again['id'], source['id'])

    def test_compact_search_finds_oversized_lesson_and_batch_preserves_exceptions(self):
        source = self.m.document(str(self.path))['id']
        decision = self.decision(source)
        lesson = self.m.record(decision['episode_id'], 'lesson', {'when':'The Amberlake project uses local storage.',
            'do':'Check the export exception.', 'because':'The project must preserve the original conditions. '*170,
            'exceptions':'Do not apply this lesson when the customer explicitly requests a reviewed export.'},
            expected_version=1, request_key='lesson', actor='test', evidence=[{'source_id':source, 'reason':'The vision contains an exception.'}])
        packet = dispatch(self.m, 'memory_context', {'query':'Amberlake', 'subject':'code', 'max_chars':2500})
        self.assertNotIn(lesson['id'], [r['id'] for r in packet['records']])
        index = dispatch(self.m, 'memory_get', {'view':'search', 'query':'Amberlake', 'subject':'code', 'max_chars':1500})
        item = next(r for r in index['records'] if r['id']==lesson['id'])
        self.assertEqual(item['lesson_status'], 'proposed')
        self.assertNotIn('signature', item)  # Discovery cannot mark unread content as seen.
        details = dispatch(self.m, 'memory_get', {'view':'records', 'ids':[lesson['id'], decision['id']], 'max_chars':20000})
        self.assertEqual(details['records'][0]['payload']['exceptions'], self.m.read(lesson['id'])['payload']['exceptions'])
        with self.assertRaises(BudgetTooSmall): dispatch(self.m, 'memory_get', {'view':'records', 'ids':[lesson['id']], 'max_chars':500})

    def test_index_pages_cover_all_matches_and_subject_boundary(self):
        for i in range(11): self.m.source(str(i), 'Amberlake document '+str(i), 'Check details.', 'Amberlake','document',subject='code')
        self.m.source('other', 'Other subject', 'Amberlake', 'Amberlake', 'document', subject='writing')
        ids=[]; offset=0
        while True:
            page = dispatch(self.m, 'memory_get', {'view':'search', 'query':'Amberlake', 'subject':'code', 'max_chars':1000, 'offset':offset})
            self.assertLessEqual(len(dumps(tool_result(page))), 1000)
            ids.extend(r['id'] for r in page['records'])
            if not page['more']: break
            self.assertGreater(page['next_offset'], offset)
            offset=page['next_offset']
        self.assertEqual(len(set(ids)),11)
        for args in [{'view':'search','query':'Amberlake'}, {'view':'records','ids':[]}, {'view':'records','ids':['missing']}]:
            with self.assertRaises(InvalidRecord): dispatch(self.m, 'memory_get', args)

    def test_exact_source_slices_reconstruct_escaped_unicode_with_fewer_calls(self):
        self.path.write_text('# Escapes\n'+('Café "\\\n'*300))
        source=self.m.document(str(self.path))['id']; parts=[]; offset=0;calls=0
        while True:
            part=dispatch(self.m, 'memory_get', {'view':'record','id':source,'body_offset':offset,'max_chars':1800})
            self.assertLessEqual(len(dumps(tool_result(part))), 1800)
            parts.append(part['body']);calls+=1
            if not part['body_more']:break
            self.assertGreater(part['next_offset'],offset)
            offset=part['next_offset']
        self.assertEqual(''.join(parts), self.m.read(source,detail=True)['body'])
        self.assertLess(calls,25)
        with self.assertRaises(InvalidRecord): dispatch(self.m,'memory_get',{'view':'record','id':source,'body_offset':999999})

    def test_document_write_retry_keeps_original_receipt_after_file_changes(self):
        with self.assertRaises(InvalidRecord): write(self.m,'document','relative',{'path':'Product vision.md'})
        data={'path':str(self.path),'subject':'general'}
        first=write(self.m,'document','capture',data)
        self.path.write_text('# Updated vision\nUse reviewed exports.')
        self.assertEqual(write(self.m,'document','capture',data), first)
        fresh=write(self.m,'document','refresh',data)
        self.assertEqual(fresh['version'],2)

    def test_live_evaluator_counts_errors_when_host_omits_iserror(self):
        from tests.integration.codex_documents import call_failed
        failed={'result':{'content':[{'type':'text','text':dumps({'error':'InvalidRecord','message':'max_chars must be 500–20000.'})}]},'error':None}
        self.assertTrue(call_failed(failed))
        self.assertFalse(call_failed({'result':{'content':[{'type':'text','text':dumps({'records':[]})}]}}))


if __name__ == '__main__': unittest.main()
