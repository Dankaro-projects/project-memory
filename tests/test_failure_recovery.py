"""Regression cases for capture loss, connection recovery and bounded reads."""
import shutil
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from memory_module import Memory, InvalidRecord, codex_host, reviews
from memory_module import capture_errors
from memory_module.coverage import inspect
from memory_module.install import setup
from memory_module.live import Viewer
from memory_module.mcp import dispatch, tool_result, write
from memory_module.planning import latest
from memory_module.workspace import action


class FailureRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name).resolve()
        self.info = setup(self.root,requirements=['Preserve the documented exception.'])
        self.m = Memory(self.info['database'])

    def tearDown(self):
        self.m.close()
        shutil.rmtree(self.temp.name, ignore_errors=True)

    def hook(self, event):
        return subprocess.run([sys.executable,'-m','memory_module.codex_host','--db',str(self.m.path)],
                              input=json.dumps(event),text=True,capture_output=True,timeout=8)

    def work(self):
        return action(self.m,'plan',{'title':'Inspect the parser','objective':'Preserve both paths.',
            'criterion':'The tagged exception still works.','subject':'code',
            'payload':{'state':'ready','next_action':'Read the parser.','scope':'Change the parser only; retain the tagged exception.',
                       'autonomy':'act','reason':'The user requests this check.'}},'plan')

    def test_real_failed_hooks_preserve_every_session_and_repeated_gap(self):
        for session in ('a','b'):
            codex_host.capture(self.m,{'session_id':session,'turn_id':'1','hook_event_name':'UserPromptSubmit','prompt':'Inspect the parser.'})
        self.m.db.execute('BEGIN IMMEDIATE')
        children = []
        try:
            for i,session in enumerate(('a','b','a')):
                child = subprocess.Popen([sys.executable,'-m','memory_module.codex_host','--db',str(self.m.path)],
                                         stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                child.stdin.write(json.dumps({'session_id':session,'turn_id':'1','hook_event_name':'PreToolUse','tool_name':'Read','tool_use_id':str(i)}))
                child.stdin.close();child.stdin = None
                children.append(child)
            for child in children:
                _,error = child.communicate(timeout=8)
                self.assertEqual(child.returncode,2,error)
        finally:
            self.m.db.rollback()
            for child in children:
                if child.poll() is None: child.kill();child.communicate()
        pending = capture_errors.summary(self.m.path)
        self.assertEqual(pending['count'],3)
        self.assertEqual(pending['sessions'],['a','b'])
        recovered = self.hook({'session_id':'b','hook_event_name':'SessionStart','source':'resume'})
        self.assertEqual(recovered.returncode,0,recovered.stderr)
        self.assertIsNone(capture_errors.summary(self.m.path))
        self.assertEqual(len(inspect(self.m,'a')['capture_gaps']),2)
        self.assertEqual(len(inspect(self.m,'b')['capture_gaps']),1)
        self.assertEqual(self.m.db.execute("SELECT count(*) FROM host_receipts WHERE event_name='PreToolUse'").fetchone()[0],0)

    def test_legacy_record_and_interrupted_cleanup_recover_once(self):
        legacy = self.m.path.with_suffix('.capture-error.json')
        legacy.write_text(json.dumps({'session_id':'legacy','event_name':'Stop','error':'OperationalError','created_at':self.m.now()}))
        capture_errors.record(self.m.path,{'session_id':'new','hook_event_name':'Stop'},OSError())
        unlink = Path.unlink
        with patch.object(Path,'unlink',side_effect=OSError('The cleanup was interrupted.')):
            with self.assertRaises(OSError): capture_errors.recover(self.m)
        self.assertEqual(len(capture_errors.paths(self.m.path)),2)
        self.assertEqual(self.m.db.execute("SELECT count(*) FROM host_receipts WHERE event_name='CaptureRecovered'").fetchone()[0],2)
        capture_errors.recover(self.m)
        self.assertEqual(capture_errors.paths(self.m.path),[])
        self.assertEqual(self.m.db.execute("SELECT count(*) FROM host_receipts WHERE event_name='CaptureRecovered'").fetchone()[0],2)
        self.assertIs(Path.unlink,unlink)

    def test_failure_during_recovery_is_not_removed_by_earlier_cleanup(self):
        capture_errors.record(self.m.path,{'session_id':'first'},OSError())
        unlink = Path.unlink
        def concurrent_failure(path, **kwargs):
            capture_errors.record(self.m.path,{'session_id':'second'},OSError())
            return unlink(path,**kwargs)
        with patch.object(Path,'unlink',concurrent_failure): capture_errors.recover(self.m)
        self.assertEqual(capture_errors.summary(self.m.path)['sessions'],['second'])
        capture_errors.recover(self.m)
        self.assertIsNone(capture_errors.summary(self.m.path))

    def test_invalid_sidecar_is_visible_and_never_discarded(self):
        path = self.m.path.with_suffix('.capture-error.json')
        path.write_text('{"session_id": ["invalid"]}')
        self.assertEqual(capture_errors.summary(self.m.path)['unreadable_records'],1)
        with self.assertRaises(InvalidRecord): capture_errors.recover(self.m)
        self.assertTrue(path.exists())

    def test_concurrent_recovery_processes_do_not_duplicate_or_lose_gaps(self):
        for session in ('a','b'):
            capture_errors.record(self.m.path,{'session_id':session},OSError())
        children = []
        try:
            for session in ('a','b'):
                child = subprocess.Popen([sys.executable,'-m','memory_module.codex_host','--db',str(self.m.path)],
                                         stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                child.stdin.write(json.dumps({'session_id':session,'hook_event_name':'SessionStart','source':'resume'}))
                child.stdin.close();child.stdin=None;children.append(child)
            for child in children:
                _,error = child.communicate(timeout=8)
                self.assertEqual(child.returncode,0,error)
        finally:
            for child in children:
                if child.poll() is None: child.kill();child.communicate()
        self.assertIsNone(capture_errors.summary(self.m.path))
        self.assertEqual(self.m.db.execute("SELECT count(*) FROM host_receipts WHERE event_name='CaptureRecovered'").fetchone()[0],2)

    def test_invalid_hook_payload_records_a_recoverable_unknown_interval(self):
        failed = self.hook([])
        self.assertEqual(failed.returncode,2)
        self.assertEqual(capture_errors.summary(self.m.path)['sessions'],['unknown'])
        capture_errors.recover(self.m)
        self.assertEqual(len(inspect(self.m,'unknown')['capture_gaps']),1)

    @unittest.skipIf(os.name=='nt' or (hasattr(os,'geteuid') and os.geteuid()==0),'Requires POSIX file permissions for an unprivileged process.')
    def test_real_mcp_file_error_returns_tool_error_and_answers_next_ping(self):
        source = self.root/'locked.md'
        source.write_text('The fixture deliberately denies read access.')
        source.chmod(0)
        requests = [{'jsonrpc':'2.0','id':1,'method':'initialize','params':{}},
                    {'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'memory_write','arguments':{
                        'operation':'document','request_key':'locked','data':{'path':str(source)}}}},
                    {'jsonrpc':'2.0','id':3,'method':'ping'}]
        try:
            result = subprocess.run([sys.executable,'-m','memory_module.mcp','--db',str(self.m.path)],
                                    input=''.join(json.dumps(r)+'\n' for r in requests),text=True,capture_output=True,timeout=8)
        finally: source.chmod(0o600)
        self.assertEqual(result.returncode,0,result.stderr)
        replies = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([r['id'] for r in replies],[1,2,3])
        self.assertTrue(replies[1]['result']['isError'])
        self.assertIn('PermissionError',json.dumps(replies[1]))
        self.assertEqual(replies[2]['result'],{})

    def test_large_direction_keeps_complete_versioned_text_and_approval_evidence(self):
        texts = [f'Requirement {i}: '+('Preserve all conditions. '*78)+'Except for the tagged legacy path.' for i in range(100)]
        source = self.m.source('approval','Approved direction','The user approves this fixture.','Keep all one hundred conditions.','user')
        evidence = [{'source_id':source['id'],'reason':f'Condition group {i}. '+('The approval retains this exception. '*50)} for i in range(20)]
        self.m.approve_requirements(requirements=texts,reason='The user approves the full revision.',actor='fixture',
                                   evidence=evidence,expected_version=0,request_key='approval')
        page = dispatch(self.m,'memory_get',{'view':'direction'})
        self.assertLessEqual(len(json.dumps(tool_result(page))),6000)
        self.assertEqual(page['current']['requirement_count'],100)
        self.assertNotIn('requirements',page['current'])
        pointer = page['current']['read_requirements_with']
        restored,refs = [],[]
        while True:
            result = dispatch(self.m,'memory_get',{**pointer,'max_chars':6000})
            self.assertLessEqual(len(json.dumps(tool_result(result))),6000)
            restored.extend(item['text'] for item in result['items']);refs.extend(result['evidence'])
            if not result['more']: break
            self.assertGreater(result['next_offset'],pointer['offset'])
            pointer = {**pointer,'offset':result['next_offset']}
        self.assertEqual(restored,texts);self.assertEqual(refs,evidence)
        self.m.approve_requirements(requirements=['A later approved revision.'],reason='The user changes the direction.',actor='fixture',
                                   evidence=evidence[:1],expected_version=1,request_key='second')
        old = dispatch(self.m,'memory_get',{'view':'requirements','version':1,'limit':1})
        self.assertEqual(old['items'][0]['text'],texts[0])
        original = dispatch(self.m,'memory_get',{'view':'requirements','version':0})
        self.assertEqual(original['items'][0]['text'],'Preserve the documented exception.')
        self.m.source('approval','Changed approval','The source has changed.','The original approval needs inspection.','user')
        stale = dispatch(self.m,'memory_get',{'view':'requirements','version':1,'limit':1})
        self.assertEqual(stale['status'],'needs_review')
        self.assertNotEqual(stale['signature'],old['signature'])
        self.assertEqual(stale['items'],old['items'])
        with self.assertRaises(InvalidRecord): dispatch(self.m,'memory_get',{'view':'requirements','version':99})

    def test_approval_evidence_is_paged_even_when_there_are_fewer_requirements(self):
        source = self.m.source('approval','Approval','The user approves this fixture.','Preserve the original exception.','user')
        evidence = [{'source_id':source['id'],'reason':f'Approval condition {i}.'} for i in range(20)]
        self.m.approve_requirements(requirements=['Keep the exception.'],reason='The user approves the condition.',actor='fixture',
                                   evidence=evidence,expected_version=0,request_key='approval')
        results = [dispatch(self.m,'memory_get',{'view':'requirements','offset':i,'limit':5}) for i in (0,5,10,15)]
        self.assertEqual([e for r in results for e in r['evidence']],evidence)
        self.assertEqual([r['more'] for r in results],[True,True,True,False])

    def test_live_etags_reuse_content_hash_and_recheck_changed_files(self):
        work = self.work();reviews.configure(self.m,self.root,'codex')
        check = reviews.request(self.m,work['episode_id'],request_key='review');reviews.cancel(self.m,check['id'])
        asset = self.root/'unrelated-asset.bin';asset.write_bytes(b'x'*4_000_000)
        ready = queue.Queue();reads = []
        def serve():
            with Viewer(self.m.path,'test-token') as server:
                ready.put(server);server.serve_forever()
        original = Path.open
        def measured(path, *args, **kwargs):
            if path==asset and args and args[0]=='rb': reads.append(path.stat().st_size)
            return original(path,*args,**kwargs)
        thread = threading.Thread(target=serve,daemon=True)
        with patch.object(Path,'open',measured):
            thread.start();server = ready.get(timeout=3)
            # Check project files on every request here; the default interval of 5 seconds is tested in test_api.
            server.tree_interval = 0
            url = f'http://127.0.0.1:{server.server_port}/test-token/api/health'
            def get(tag=None):
                try:
                    with urlopen(Request(url,headers={'If-None-Match':tag} if tag else {}),timeout=8) as response:
                        response.read();return response.status,response.headers['ETag']
                except HTTPError as error:
                    if error.code!=304: raise
                    return error.code,tag
            try:
                status,tag = get();self.assertEqual(status,200)
                self.assertEqual(get(tag)[0],304);self.assertEqual(get(tag)[0],304)
                initial_reads = 1 if os.name=='posix' else 3
                self.assertEqual(reads,[4_000_000]*initial_reads)
                before = asset.stat();asset.write_bytes(b'y'*4_000_000)
                os.utime(asset,ns=(before.st_atime_ns,before.st_mtime_ns))
                status,changed = get(tag)
                self.assertEqual(status,200);self.assertNotEqual(changed,tag)
                self.assertEqual(reads,[4_000_000]*(initial_reads+1))
                reviews.snapshot(self.m,work['episode_id'],'outcome')
                reviews.snapshot(self.m,work['episode_id'],'outcome')
                self.assertEqual(reads,[4_000_000]*(initial_reads+3))
            finally: server.shutdown();thread.join()

    def test_hash_cache_detects_add_rename_delete_and_atomic_replacement(self):
        cache = {};a = self.root/'a.txt';a.write_text('first')
        signatures = [reviews.tree_signature(self.root,cache=cache)]
        b = self.root/'b.txt';b.write_text('second');signatures.append(reviews.tree_signature(self.root,cache=cache))
        b.rename(self.root/'c.txt');signatures.append(reviews.tree_signature(self.root,cache=cache))
        (self.root/'c.txt').unlink();signatures.append(reviews.tree_signature(self.root,cache=cache))
        b.write_text('other');b.replace(a);signatures.append(reviews.tree_signature(self.root,cache=cache))
        self.assertNotEqual(signatures[0],signatures[1]);self.assertNotEqual(signatures[1],signatures[2])
        self.assertEqual(signatures[0],signatures[3]);self.assertNotEqual(signatures[0],signatures[4])
        self.assertEqual(signatures[-1],reviews.tree_signature(self.root))

    @unittest.skipUnless(os.name=='posix','Only POSIX metadata supports the viewer content cache.')
    def test_write_during_hashing_does_not_enter_the_cache(self):
        cache = {};asset = self.root/'asset.txt';asset.write_text('before')
        original = Path.open
        def concurrent_write(path,*args,**kwargs):
            if path==asset and args and args[0]=='rb':
                with original(asset,'w') as target: target.write('after')
            return original(path,*args,**kwargs)
        with patch.object(Path,'open',concurrent_write):
            with self.assertRaises(InvalidRecord): reviews.tree_signature(self.root,cache=cache)
        self.assertEqual(cache,{})
        self.assertEqual(reviews.tree_signature(self.root,cache=cache),reviews.tree_signature(self.root))

    def test_continuation_hint_updates_progress_without_rewriting_scope(self):
        work = self.work();before = latest(self.m,work['episode_id'],'work_plan')
        hint = dispatch(self.m,'memory_get',{'view':'next','id':work['episode_id']})['update']
        self.assertEqual(hint['operation'],'progress')
        data = {'episode_id':hint['episode_id'],'expected_version':hint['expected_version'],'actor':'fixture',
                'payload':{'state':'review','next_action':'Inspect the results.','reason':'The inspection has completed.'}}
        first = write(self.m,hint['operation'],'continue',data)
        self.assertEqual(write(self.m,hint['operation'],'continue',data),first)
        after = latest(self.m,work['episode_id'],'work_plan')
        for field in ('scope','depends_on','autonomy'):
            self.assertEqual(after.get(field),before.get(field))
        self.assertEqual(self.m.read(after['id'])['evidence'],self.m.read(before['id'])['evidence'])


if __name__=='__main__': unittest.main()
