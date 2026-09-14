import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import queue
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from memory_module import Memory, Conflict, InvalidRecord
from memory_module.install import setup
from memory_module import reviews, codex_host
from memory_module.live import Viewer
from memory_module.mcp import write
from memory_module.planning import latest, card
from memory_module.workspace import action


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve()
        self.info=setup(self.root,requirements=['Keep strict UTF-8 and the tagged Latin-1 exception.'])
        self.m=Memory(self.info['database'])
        self.payload={'state':'ready','next_action':'Inspect the parser.','scope':'Keep strict UTF-8 and the tagged Latin-1 exception.',
                      'autonomy':'act','reason':'The user requests the repair.','owner':'agent','priority':'normal','sprint_id':None,'depends_on':[]}
        self.data={'title':'Repair parsing','objective':'Preserve both encoding paths.','criterion':'Strict UTF-8 rejects invalid bytes. Tagged legacy Latin-1 succeeds.',
                   'subject':'code','payload':self.payload}
        self.work=action(self.m,'plan',self.data,'first')
    def tearDown(self):self.m.close();self.temp.cleanup()
    def completed(self):
        ep=self.work['episode_id'];source=self.m.source('test','Actual parser result','Both routes were checked.','The fixture result is recorded explicitly.','tool',subject='code')
        evidence=[{'source_id':source['id'],'reason':'This fixture supplies the observed result.'}]
        d=self.m.record(ep,'decision',{'decision':'Preserve both routes.','why':'The user requires the exception.','expected':'Both paths behave as required.','reconsider_when':'The contract changes.'},
            expected_version=self.m.episode(ep)['version'],request_key='decision',actor='test',evidence=evidence)
        a=self.m.record(ep,'action',{'action':'Run parser checks.'},expected_version=d['version'],request_key='action',actor='test',decision_id=d['id'])
        result=self.m.record(ep,'outcome',{'assessment':'good','observed':'Both routes pass in this fixture.','assessment_reason':'The recorded checks cover both routes.','severity':'none','attribution':'This is a fixture.','completion':'complete'},
            expected_version=a['version'],request_key='outcome',actor='test',decision_id=d['id'],evidence=evidence)
        return d,result
    def update(self,**changes):
        return {'episode_id':self.work['episode_id'],'expected_version':self.m.episode(self.work['episode_id'])['version'],'payload':{**self.payload,**changes}}
    def pass_review(self,run):
        report={'verdict':'pass','summary':'The fixture covers the criterion.','checks':[{'criterion':'Both paths work.','evidence':'Fixture evidence.','result':'met'}],'findings':[],'lesson_proposals':[]}
        with self.m._write():self.m.db.execute("UPDATE review_runs SET state='pass',report=? WHERE id=?",(json.dumps(report),run['id']))
    def test_browser_intent_and_lost_response_retry_preserve_one_work_item(self):
        self.assertEqual(action(self.m,'plan',self.data,'first'),self.work)
        self.assertEqual(self.m.db.execute('SELECT count(*) FROM episodes').fetchone()[0],1)
        self.assertEqual(self.m.db.execute('SELECT count(*) FROM sources').fetchone()[0],1)
        plan=self.m.read(self.work['id']);self.assertEqual(plan['evidence'][0]['origin'],'user')
        with self.assertRaises(Conflict):action(self.m,'plan',{**self.data,'title':'Another action'},'first')
    def test_conflict_keeps_old_data_and_rejects_all_partial_writes(self):
        draft=self.update(priority='high');action(self.m,'comment',{'episode_id':self.work['episode_id'],'expected_version':1,'text':'Preserve the tagged exception.'},'comment')
        counts=[self.m.db.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ('sources','events')]
        with self.assertRaises(Conflict):action(self.m,'plan',draft,'stale')
        self.assertEqual(counts,[self.m.db.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ('sources','events')])
        self.assertEqual(latest(self.m,self.work['episode_id'],'work_plan')['priority'],'normal')
    def test_structured_plan_rejects_cycles_and_false_completion(self):
        with self.assertRaises(InvalidRecord):action(self.m,'plan',self.update(depends_on=[{'episode_id':self.work['episode_id'],'reason':'Self reference.'}]),'cycle')
        with self.assertRaises(InvalidRecord):action(self.m,'plan',self.update(state='done'),'false-done')
        with self.assertRaises(InvalidRecord):action(self.m,'plan',self.update(state='in_progress',session_id='invented'),'false-owner')
    def test_review_is_required_only_after_enablement_and_progress_keeps_pass(self):
        reviews.configure(self.m,self.root,'codex');self.completed();ep=self.work['episode_id']
        self.assertEqual(card(self.m,ep)['state'],'review')
        with self.assertRaises(InvalidRecord):action(self.m,'plan',self.update(state='done'),'unchecked')
        run=reviews.request(self.m,ep,request_key='review');self.pass_review(run)
        self.assertEqual(reviews.current(self.m,ep)['state'],'pass')
        action(self.m,'plan',self.update(state='done'),'done')
        self.assertEqual(card(self.m,ep)['state'],'done')
        (self.root/'parser.py').write_text('changed = True\n')
        self.assertEqual(reviews.current(self.m,ep)['state'],'stale')
        self.assertEqual(card(self.m,ep)['state'],'review')
        self.assertEqual(reviews.read(self.m,run['id'])['state'],'pass')
        with self.assertRaises(sqlite3.IntegrityError):
            with self.m._write():self.m.db.execute("UPDATE review_runs SET state='failed' WHERE id=?",(run['id'],))
    def test_enabling_reviews_does_not_rewrite_historical_completion(self):
        self.completed();ep=self.work['episode_id'];action(self.m,'plan',self.update(state='done'),'done')
        reviews.configure(self.m,self.root,'codex');self.assertEqual(card(self.m,ep)['state'],'done')
    def test_board_keeps_full_review_evidence_behind_an_explicit_read(self):
        reviews.configure(self.m,self.root,'codex');self.completed();ep=self.work['episode_id']
        run=reviews.request(self.m,ep,request_key='bounded-review');self.pass_review(run)
        check=card(self.m,ep)['agent_check']
        self.assertNotIn('report',check);self.assertEqual(check['read_full_with']['id'],run['id'])
        self.assertTrue(reviews.read(self.m,run['id'])['report']['checks'])
    def test_intent_snapshot_preserves_both_original_and_revised_evidence(self):
        reviews.configure(self.m,self.root,'codex')
        old=self.m.read(self.work['id'])['evidence'][0]['source_id']
        new=self.m.source('workspace:first','Revised contract','The user changes the allowed route.','Only the tagged migration route may use Latin-1.','user',subject='code')
        snapshot,_=reviews.snapshot(self.m,self.work['episode_id'],'intent')
        by_id={s['id']:s for s in snapshot['sources']}
        self.assertIn(old,by_id);self.assertIn(new['id'],by_id)
        self.assertIn('Only the tagged migration route',by_id[new['id']]['body'])
    def test_a_revised_decision_can_continue_after_an_unsuccessful_review(self):
        from memory_module.planning import next_work
        reviews.configure(self.m,self.root,'codex');decision,_=self.completed();ep=self.work['episode_id']
        run=reviews.request(self.m,ep,request_key='failed-review')
        with self.m._write():self.m.db.execute("UPDATE review_runs SET state='changes_required',error='The fixture requires a repair.' WHERE id=?",(run['id'],))
        self.assertEqual(card(self.m,ep)['state'],'review')
        old=self.m.read(decision['id']);payload={**old['payload'],'decision':'Restore the missing legacy path before reassessing completion.'}
        self.m.record(ep,'decision',payload,expected_version=self.m.episode(ep)['version'],actor='test',request_key='repair-decision',supersedes=decision['id'],
                      evidence=[{'source_id':e['source_id'],'reason':e['reason']} for e in old['evidence']])
        self.assertEqual(card(self.m,ep)['state'],'ready')
        self.assertEqual(next_work(self.m,episode_id=ep)['action'],'continue')
        with self.assertRaises(InvalidRecord):action(self.m,'plan',self.update(state='done'),'premature-repair-done')
    def test_progress_on_an_older_plan_without_optional_fields_keeps_its_evidence(self):
        minimal={k:v for k,v in self.payload.items() if k not in {'owner','priority','sprint_id','depends_on'}}
        self.work=action(self.m,'plan',{**self.data,'payload':minimal},'minimal')
        reviews.configure(self.m,self.root,'codex');self.completed();ep=self.work['episode_id']
        run=reviews.request(self.m,ep,request_key='minimal-review');self.pass_review(run)
        before=self.m.read(self.work['id'])['evidence']
        action(self.m,'plan',self.update(state='done',next_action='The result is complete.'),'minimal-done')
        self.assertEqual(self.m.read(latest(self.m,ep,'work_plan')['id'])['evidence'],before)
        self.assertEqual(reviews.current(self.m,ep)['state'],'pass')
    def test_deduplication_cancellation_and_explicit_retry(self):
        reviews.configure(self.m,self.root,'codex');ep=self.work['episode_id']
        first=reviews.request(self.m,ep,'intent',request_key='first-review')
        self.assertEqual(reviews.request(self.m,ep,'intent',request_key='duplicate')['id'],first['id'])
        self.assertEqual(reviews.cancel(self.m,first['id'])['state'],'cancelled')
        self.assertEqual(reviews.request(self.m,ep,'intent',request_key='automatic')['state'],'cancelled')
        retry=reviews.request(self.m,ep,'intent',request_key='human-retry',retry=True)
        self.assertNotEqual(retry['id'],first['id'])
        with self.assertRaises(Conflict):reviews.request(self.m,ep,'outcome',request_key='parallel')
    def test_false_pass_and_missing_evidence_are_rejected(self):
        report={'verdict':'pass','summary':'The host claims success.','checks':[{'criterion':'Preserve exception.','evidence':'No direct evidence.','result':'unknown'}],'findings':[],'lesson_proposals':[]}
        with self.assertRaises(InvalidRecord):reviews.validate_report(report)
        with self.assertRaises(InvalidRecord):reviews.validate_report({**report,'checks':[]})
    def test_replaying_a_committed_outcome_recovers_an_unlaunched_check(self):
        from unittest.mock import patch
        reviews.configure(self.m,self.root,'codex');decision,outcome=self.completed()
        record=self.m.read(outcome['id'])
        data={'episode_id':self.work['episode_id'],'kind':'outcome','payload':record['payload'],'expected_version':outcome['version'],
              'actor':'test','decision_id':decision['id'],'supersedes':outcome['id'],
              'evidence':[{'source_id':e['source_id'],'reason':e['reason']} for e in record['evidence']]}
        with patch.object(reviews,'launch',side_effect=OSError('The launcher was unavailable.')):
            first=write(self.m,'record','recover-outcome',data)
        self.assertEqual(first['agent_check']['state'],'unavailable')
        version=self.m.episode(self.work['episode_id'])['version']
        with patch.object(reviews,'launch') as launched:
            again=write(self.m,'record','recover-outcome',data)
            launched.assert_called_once()
        self.assertEqual(again['id'],first['id']);self.assertEqual(again['agent_check']['state'],'queued')
        self.assertEqual(self.m.episode(self.work['episode_id'])['version'],version)
        self.assertEqual(self.m.db.execute('SELECT count(*) FROM review_runs').fetchone()[0],1)
    def test_interrupted_worker_is_not_automatically_retried(self):
        reviews.configure(self.m,self.root,'codex');ep=self.work['episode_id']
        run=reviews.request(self.m,ep,'recovery',request_key='recovery')
        with self.m._write():self.m.db.execute("UPDATE review_runs SET state='running',updated_at='2000-01-01T00:00:00+00:00' WHERE id=?",(run['id'],))
        self.assertEqual(reviews.read(self.m,run['id'])['state'],'interrupted')
        self.assertEqual(reviews.request(self.m,ep,'recovery',request_key='no-retry')['id'],run['id'])
    def test_stop_after_a_block_records_the_new_message_without_a_capture_loop(self):
        event={'hook_event_name':'Stop','session_id':'host','turn_id':'turn','last_assistant_message':'The task claims completion.'}
        codex_host.capture(self.m,event)
        codex_host.capture(self.m,{**event,'last_assistant_message':'The review finds a missing requirement.','stop_hook_active':True})
        codex_host.capture(self.m,{**event,'last_assistant_message':'The review finds a missing requirement.','stop_hook_active':True})
        self.assertEqual(self.m.db.execute("SELECT count(*) FROM host_receipts WHERE event_name='Stop'").fetchone()[0],2)
    def test_live_revision_detects_unregistered_artifact_changes(self):
        reviews.configure(self.m,self.root,'codex');self.completed()
        run=reviews.request(self.m,self.work['episode_id'],request_key='revision-review');self.pass_review(run)
        with Viewer(self.m.path,'revision-test-token') as server:
            before=server.revision()
            (self.root/'parser.py').write_text('The implementation changes without a memory write.')
            self.assertNotEqual(server.revision(),before)
            self.assertEqual(card(server.memory,self.work['episode_id'])['state'],'review')
    def test_stop_blocks_once_even_when_the_host_omits_its_block_flag(self):
        from unittest.mock import patch
        reviews.configure(self.m,self.root,'codex');decision,_=self.completed()
        codex_host.bind(self.m,'host',decision['id'],'bind-test')
        event={'hook_event_name':'Stop','session_id':'host','turn_id':'turn'}
        with patch.object(reviews,'launch'):
            self.assertEqual(reviews.hook(self.m,event,'codex')['decision'],'block')
            self.assertEqual(reviews.hook(self.m,event,'codex'),{})
            self.assertEqual(reviews.hook(self.m,{**event,'stop_hook_active':True},'claude'),{})
        self.assertEqual(self.m.db.execute("SELECT count(*) FROM review_runs").fetchone()[0],1)
    def test_live_api_uses_origin_csrf_versions_and_shared_history(self):
        ready=queue.Queue()
        def serve():
            with Viewer(self.m.path,'workspace-test-token') as server:
                ready.put(server);server.serve_forever()
        thread=threading.Thread(target=serve,daemon=True);thread.start();server=ready.get(timeout=3)
        try:
            base=f'http://127.0.0.1:{server.server_port}/workspace-test-token/'
            origin=f'http://127.0.0.1:{server.server_port}'
            try:
                health=json.load(urlopen(base+'api/health'))
                def post(data,**headers):
                    return urlopen(Request(base+'api/actions',data=json.dumps(data).encode(),headers={'Content-Type':'application/json','Origin':origin,'X-Project-Memory':health['csrf'],**headers}))
                payload={'operation':'plan','data':self.update(priority='high'),'request_key':'http-edit'}
                with self.assertRaises(HTTPError) as wrong:post(payload,Origin='https://example.com')
                self.assertEqual(wrong.exception.code,403)
                with self.assertRaises(HTTPError):post(payload,**{'X-Project-Memory':'wrong'})
                first=json.load(post(payload));self.assertEqual(json.load(post(payload)),first)
                page=json.load(urlopen(base+'api/board?episode='+self.work['episode_id']))
                self.assertEqual(page['cards'][0]['plan']['priority'],'high')
                with self.assertRaises(HTTPError) as conflict:post({**payload,'request_key':'stale-edit'})
                self.assertEqual(conflict.exception.code,409)
                self.assertEqual(self.m.read(self.work['id'])['payload']['priority'],'normal')
            finally:server.shutdown();thread.join()
        finally:
            if thread.is_alive():server.shutdown();thread.join()


if __name__=='__main__':unittest.main()
