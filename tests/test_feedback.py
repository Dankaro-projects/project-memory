"""Regression cases from first-beta use; use invented project data only."""
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from memory_module import Memory, BudgetTooSmall, Conflict, dumps
from memory_module import codex_host
from memory_module.documents import sync
from memory_module.health import inspect, diagnose_hooks
from memory_module.install import setup
from memory_module.live import page, start, Viewer
from memory_module.mcp import dispatch, tool_result


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve()
        self.info=setup(self.root);self.m=Memory(self.info['database'])
    def tearDown(self):
        self.m.close();self.temp.cleanup()
    def test_local_viewer_starts_without_hostname_resolution(self):
        from unittest.mock import patch
        with patch('socket.getfqdn',side_effect=OSError('DNS is unavailable')):
            with Viewer(self.m.path,'local-test-token') as server:
                self.assertGreater(server.server_port,0)
                self.assertEqual(server.server_address[0],'127.0.0.1')

    def test_empty_baseline_is_not_confused_with_database_health(self):
        value=dispatch(self.m,'memory_get',{'view':'health'})
        self.assertEqual(value['baseline']['status'],'not_established')
        self.assertEqual(value['capture']['status'],'not_observed')
        self.assertEqual(value['native_tools_in_current_task'],'unverified')
        self.assertEqual(value['measurements']['tokens']['reported_decisions'],0)
        self.assertTrue(self.info['viewer']['available'])
    def test_discovery_uses_real_codex_event_case_and_reports_missing_trust_and_duplicates(self):
        hooks=[{'eventName':name[:1].lower()+name[1:],'command':'project-memory hook','enabled':True,'trustStatus':'trusted'} for name in codex_host.EVENTS]
        self.assertEqual(diagnose_hooks({'hooks':hooks})['status'],'ready_for_new_session')
        hooks.append({'eventName':'preToolUse','command':'project-memory hook --host claude','enabled':True,'trustStatus':'untrusted'})
        bad=diagnose_hooks({'hooks':hooks});self.assertTrue(bad['wrong_host']);self.assertEqual(bad['duplicates']['PreToolUse'],2)
        self.assertEqual(bad['untrusted'],1);self.assertTrue(diagnose_hooks({'hooks':hooks[:1]})['missing_events'])
        hooks[-1]['enabled']=False
        self.assertEqual(diagnose_hooks({'hooks':hooks})['status'],'ready_for_new_session')
    def test_codex_plugin_does_not_autodiscover_claude_hooks(self):
        root=Path(__file__).resolve().parents[1]/'plugins/project-memory'
        self.assertFalse((root/'hooks/hooks.json').exists())
        self.assertEqual(json.loads((root/'.codex-plugin/plugin.json').read_text())['hooks'],[])
        self.assertEqual(json.loads((root/'.claude-plugin/plugin.json').read_text())['hooks'],'./claude-hooks.json')
    def test_two_hosts_keep_separate_receipts_and_results(self):
        event={'hook_event_name':'PreToolUse','session_id':'same','turn_id':'same','tool_name':'Bash','tool_use_id':'same','tool_input':{'command':'echo checked'}}
        for host in ['codex','claude']:codex_host.capture(self.m,event,host)
        self.assertEqual(codex_host.status(self.m)['unconfirmed_total'],2)
        codex_host.capture(self.m,{**event,'hook_event_name':'PostToolUse','tool_response':'Done'},'claude')
        self.assertEqual(codex_host.status(self.m)['unconfirmed_total'],1)
        self.assertEqual(len([r for r in page(self.m,{'view':'captures'})['records'] if r['status']=='execution_unconfirmed']),1)
    def test_managed_claude_install_suppresses_the_plugin_duplicate_in_a_process(self):
        setup(self.root,client='claude')
        payload=json.dumps({'hook_event_name':'SessionStart','session_id':'same'})
        args=[sys.executable,'-m','memory_module.cli','hook','--project',str(self.root),'--host','claude']
        run=subprocess.run(args+['--if-unmanaged'],input=payload,text=True,capture_output=True,check=True)
        self.assertEqual(json.loads(run.stdout),{})
        self.assertEqual(self.m.db.execute('SELECT count(*) FROM host_receipts').fetchone()[0],0)
        subprocess.run(args,input=payload,text=True,capture_output=True,check=True)
        self.assertEqual(self.m.db.execute('SELECT count(*) FROM host_receipts').fetchone()[0],1)
    def test_hooks_sync_selected_files_without_approving_or_rewriting_them(self):
        path=self.root/'vision.md';path.write_text('Use local storage except when an export is explicitly approved.')
        source=self.m.document(str(path));before=self.m.requirements
        path.write_text('Use local storage. Retain the explicitly approved export exception.')
        codex_host.capture(self.m,{'hook_event_name':'SessionStart','session_id':'sync'})
        self.assertEqual(self.m.db.execute('SELECT count(*) FROM sources').fetchone()[0],2)
        self.assertEqual(self.m.read(source['id'])['status'],'superseded')
        self.assertEqual(self.m.requirements,before)
        self.assertEqual(sync(self.m)['created'],0)
        path.unlink();self.assertEqual(sync(self.m)['documents'][0]['status'],'file_missing')
        self.assertEqual(self.m.db.execute('SELECT count(*) FROM sources').fetchone()[0],2)
    def test_refresh_without_subject_keeps_the_existing_subject(self):
        path=self.root/'research.md';path.write_text('The legacy exception remains explicit.')
        first=self.m.document(str(path),subject='research');path.write_text('The exception still applies only to the tagged legacy endpoint.')
        refreshed=dispatch(self.m,'memory_write',{'operation':'document','request_key':'refresh','data':{'path':str(path)}})
        self.assertEqual(self.m.read(refreshed['id'])['subject'],'research')
        self.assertEqual(refreshed['version'],first['version']+1)

    def test_sync_paginates_and_does_not_follow_retargeted_symlinks(self):
        for i in range(3):
            p=self.root/f'{i}.md';p.write_text('Original evidence.');self.m.document(str(p));p.write_text('Changed evidence.')
        first=sync(self.m,limit=2);self.assertEqual(first['created'],2);self.assertTrue(first['more'])
        self.assertEqual(sync(self.m,limit=2,offset=first['next_offset'])['created'],1)
        path=self.root/'0.md';path.unlink()
        try:path.symlink_to(self.root/'1.md')
        except OSError as exc:
            if os.name=='nt' and getattr(exc,'winerror',None)==1314:self.skipTest('The runner does not permit symbolic links.')
            raise
        self.assertEqual(sync(self.m)['documents'][0]['status'],'symlink_requires_review')
    def test_large_constraints_can_be_read_completely_then_reused_until_they_change(self):
        self.m.close();path=self.root/'large.sqlite';self.m=Memory.create(path,'Large', ['Preserve condition '+str(i)+': '+('Explicit scope and exception. '*12) for i in range(30)])
        codex_host.initialize(self.m)
        with self.assertRaises(BudgetTooSmall) as caught:dispatch(self.m,'memory_context',{'query':'local','subject':'general','max_chars':2000})
        details=caught.exception.details;self.assertGreater(details['minimum_required'],2000)
        self.assertEqual(details['next_call']['arguments']['view'],'requirements')
        texts=[];offset=0
        while True:
            packet=dispatch(self.m,'memory_get',{'view':'requirements','limit':2,'offset':offset,'max_chars':2000})
            texts.extend(i['text'] for i in packet['items'])
            if not packet['more']:break
            offset=packet['next_offset']
        self.assertEqual(texts,self.m.requirements)
        result=dispatch(self.m,'memory_context',{'query':'local','subject':'general','max_chars':2000,'seen':{'requirements':packet['signature']}})
        self.assertLessEqual(len(dumps(tool_result(result))),2000)
        source=self.m.source('approval','Approval','The owner approves the additional exception.','Explicit approval.','user')
        self.m.approve_requirements(requirements=texts+['Preserve the additional exception.'],reason='Explicit approval.',actor='owner',evidence=[{'source_id':source['id'],'reason':'The owner approves this revision.'}],expected_version=0,request_key='approve')
        with self.assertRaises(BudgetTooSmall):dispatch(self.m,'memory_context',{'query':'local','subject':'general','max_chars':2000,'seen':{'requirements':packet['signature']}})
    def test_omitted_records_remain_discoverable_by_id(self):
        source=self.m.source('large','Large requirement','Research scope. '*120,'Evidence.','document',subject='research')
        result=dispatch(self.m,'memory_context',{'query':'scope','subject':'research','max_chars':2000})
        self.assertEqual(result['omitted_records'][0]['id'],source['id'])
        self.assertEqual(result['expand']['view'],'search')
    def test_snapshot_replace_is_explicit_and_preserves_the_old_file_on_error(self):
        target=self.root/'view.html';self.m.export_html(target);old=target.read_bytes()
        with self.assertRaises(Conflict):self.m.export_html(target)
        with self.assertRaises(Exception):self.m.export_html(target,replace=True,max_bytes=1024)
        self.assertEqual(target.read_bytes(),old)
        self.m.start('New work','Read the approved scope.','planning','Preserve scope.');self.m.export_html(target,replace=True)
        self.assertIn('New work',target.read_text())
    def test_live_pages_include_old_records_and_related_evidence_beyond_the_first_page(self):
        for i in range(105):self.m.source(str(i),'Evidence '+str(i),'The condition applies.','Body.','document',subject='research')
        ids=[]
        for offset in range(0,105,25):
            value=page(self.m,{'view':'sources','limit':'25','offset':str(offset),'subject':'research'})
            ids.extend(r['id'] for r in value['records']);self.assertEqual(value['total'],105)
            self.assertTrue(all('body' not in r['detail'] for r in value['records']))
        self.assertEqual(len(set(ids)),105)
        self.assertEqual(page(self.m,{'view':'sources','subject':'writing'})['total'],0)
        self.assertEqual(page(self.m,{'view':'sources','query':'Evidence 104'})['total'],1)


class LiveProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve()
        self.info=setup(self.root);self.m=Memory(self.info['database']);self.server=start(self.m.path)
    def tearDown(self):
        try:os.kill(self.server['pid'],signal.SIGTERM)
        except ProcessLookupError:pass
        self.m.close();self.temp.cleanup()
    def get(self,route='',headers=None):
        return urlopen(Request(self.server['url']+route,headers=headers or {}),timeout=3)
    def test_read_only_origin_auth_etag_and_external_wal_commits(self):
        with self.get('api/health') as response:
            health=json.load(response);etag=response.headers['ETag']
        with self.assertRaises(HTTPError) as unchanged:self.get('api/health',{'If-None-Match':etag})
        self.assertEqual(unchanged.exception.code,304)
        for headers in [{'Host':'evil.example'},{'Origin':'https://evil.example'}]:
            with self.assertRaises(HTTPError) as blocked:self.get('api/health',headers)
            self.assertEqual(blocked.exception.code,403)
        with self.assertRaises(HTTPError):urlopen(self.server['url'].replace(self.server['url'].split('/')[-2],'wrong')+'api/health')
        with self.assertRaises(HTTPError) as post:urlopen(Request(self.server['url']+'api/records',data=b'{}',method='POST'))
        self.assertEqual(post.exception.code,501)
        subprocess.run([sys.executable,'-c',"from memory_module import Memory; import sys; m=Memory(sys.argv[1]); m.start('External write','Keep the exception.','test','Exact text survives.');m.close()",str(self.m.path)],check=True)
        with self.get('api/health',{'If-None-Match':etag}) as response:self.assertNotEqual(json.load(response)['revision'],health['revision'])
        with self.get('api/records?view=episodes') as response:self.assertEqual(json.load(response)['records'][0]['title'],'External write')
        with Memory(self.m.path,read_only=True) as reader:
            with self.assertRaises(sqlite3.OperationalError):reader.db.execute("INSERT INTO settings VALUES ('unexpected','write')")
    def test_html_csp_and_body_slices_and_document_changes(self):
        path=self.root/'scope.md';path.write_text('Exact exception.\n'*1500);src=self.m.document(str(path))
        with self.get() as response:html=response.read().decode()
        for tag in ['style','script']:
            import base64
            body=html.split('<'+tag+'>')[1].split('</'+tag+'>')[0]
            self.assertIn('sha256-'+base64.b64encode(hashlib.sha256(body.encode()).digest()).decode(),html)
        pieces=[];offset=0
        while True:
            with self.get('api/record?id='+src['id']+'&body_offset='+str(offset)) as response:value=json.load(response)['record']['detail']
            pieces.append(value['body'])
            if not value['body_more']:break
            offset=value['next_offset']
        self.assertEqual(''.join(pieces),path.read_bytes().decode('utf-8'))
        with self.get('api/health') as response:etag=response.headers['ETag']
        path.write_text('A changed exception remains unapproved.')
        with self.get('api/health',{'If-None-Match':etag}) as response:self.assertEqual(response.status,200)
        with self.get('api/records?view=documents') as response:self.assertEqual(json.load(response)['records'][0]['status'],'file_changed')
    def test_expiring_evidence_changes_the_etag_without_a_database_write(self):
        from datetime import datetime, timezone, timedelta
        expires=datetime.now(timezone.utc)+timedelta(seconds=5)
        deadline=expires.isoformat()
        self.m.source('expires','Timed evidence','Check freshness.','Evidence.','tool',review_after=deadline)
        with self.get('api/health') as response:etag=response.headers['ETag']
        with self.get('api/records?view=sources') as response:self.assertEqual(json.load(response)['records'][0]['status'],'current_copy')
        time.sleep(max(0,(expires-datetime.now(timezone.utc)).total_seconds())+.05)
        with self.get('api/health',{'If-None-Match':etag}) as response:self.assertEqual(response.status,200)
        with self.get('api/records?view=sources') as response:self.assertEqual(json.load(response)['records'][0]['status'],'review_due')
    def test_project_revision_views_obey_filters_and_keep_initial_history(self):
        source=self.m.source('approval','Approval','Keep complete conditions.','The owner approves the revision.','user')
        self.m.approve_requirements(requirements=['Keep complete conditions.'],reason='The owner approves the revision.',actor='owner',evidence=[{'source_id':source['id'],'reason':'Explicit approval.'}],expected_version=0,request_key='approve')
        with self.get('api/records?view=direction&order=oldest&limit=1') as response:
            value=json.load(response);self.assertEqual(value['records'][0]['id'],'direction_0');self.assertTrue(value['more'])
        with self.get('api/records?view=direction&subject=code') as response:self.assertEqual(json.load(response)['total'],0)
        with self.get('api/records?view=direction&status=current') as response:self.assertEqual(json.load(response)['records'][0]['id'],'direction_1')

    def test_restart_reuses_the_url_and_concurrent_launches_share_one_process(self):
        first=self.server
        self.assertEqual(start(self.m.path)['pid'],first['pid'])
        commands=[[sys.executable,'-m','memory_module.cli','view','--db',str(self.m.path),'--no-open'] for _ in range(2)]
        processes=[subprocess.Popen(cmd,stdout=subprocess.PIPE,text=True) for cmd in commands]
        self.assertEqual({json.loads(p.communicate(timeout=5)[0])['pid'] for p in processes},{first['pid']})
        os.kill(first['pid'],signal.SIGTERM)
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            try:self.get('api/health').close()
            except OSError:break
            time.sleep(.02)
        self.server=start(self.m.path)
        self.assertNotEqual(self.server['pid'],first['pid']);self.assertEqual(self.server['url'],first['url'])
        with self.get('api/health') as response:self.assertEqual(response.status,200)


if __name__=='__main__':unittest.main()
