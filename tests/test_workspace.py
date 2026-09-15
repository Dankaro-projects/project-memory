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
from memory_module import graph, guards, templates, delegation
from tests.test_api import fake_run, LAUNCHER


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
    def test_review_context_preserves_failures_and_unknown_calls_without_dumping_completed_receipts(self):
        folder=self.root/'.memory/review-context';folder.mkdir()
        def receipt(rid,event,tool,payload=None):return {'id':rid,'event_name':event,'session_id':'session','tool_use_id':tool,'payload':payload or {}}
        records=[receipt('pre-ok','PreToolUse','ok'),receipt('post-ok','PostToolUse','ok'),
                 receipt('pre-unknown','PreToolUse','unknown'),receipt('pre-reconciled','PreToolUse','reconciled'),
                 receipt('reconciliation','Reconciled','',{'receipt_id':'pre-reconciled','resolution':'completed'}),
                 receipt('post-failed','PostToolUse','failure',{'tool_response':{'exit_code':1}})]
        original={'receipts':records,'criterion':'Preserve every condition and exception.','sources':[{'body':'Complete original source text.'}]}
        packet=reviews.review_evidence(original,folder)
        self.assertNotIn('receipts',packet);self.assertEqual(packet['sources'],original['sources'])
        self.assertEqual([r['id'] for r in packet['execution']['unconfirmed']],['pre-unknown'])
        self.assertEqual([r['id'] for r in packet['execution']['reported_failures']],['post-failed'])
        self.assertEqual(json.loads((folder/'receipts.json').read_text()),records)
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
    def test_long_reviews_have_an_explicit_bounded_time_allowance(self):
        reviews.configure(self.m,self.root,'codex');ep=self.work['episode_id']
        for value in [0,901,True]:
            with self.assertRaises(InvalidRecord):reviews.request(self.m,ep,request_key='bad-limit',max_seconds=value)
        run=reviews.request(self.m,ep,request_key='extended-review',max_seconds=900)
        self.assertEqual(run['snapshot']['execution_limit_seconds'],900)
        self.assertEqual(reviews.current(self.m,ep)['state'],'queued')
    def test_false_pass_and_missing_evidence_are_rejected(self):
        report={'verdict':'pass','summary':'The host claims success.','checks':[{'criterion':'Preserve exception.','evidence':'No direct evidence.','result':'unknown'}],'findings':[],'lesson_proposals':[]}
        with self.assertRaises(InvalidRecord):reviews.validate_report(report)
        with self.assertRaises(InvalidRecord):reviews.validate_report({**report,'checks':[]})
    def test_done_without_a_check_requests_one_and_keeps_the_work_open(self):
        from unittest.mock import patch
        reviews.configure(self.m,self.root,'codex');self.completed();ep=self.work['episode_id']
        with patch.object(reviews,'launch') as launched:
            with self.assertRaises(InvalidRecord) as caught:action(self.m,'plan',self.update(state='done'),'done-without-check')
            check=caught.exception.details['agent_check']
            self.assertTrue(check['requested']);self.assertEqual(check['state'],'queued')
            self.assertGreaterEqual(launched.call_count,1)
            with self.assertRaises(InvalidRecord) as again:action(self.m,'plan',self.update(state='done'),'done-while-checking')
        self.assertEqual(again.exception.details['next_step']['action'],'wait_review')
        self.assertEqual(again.exception.details['next_step']['check_id'],check['id'])
        self.assertEqual(self.m.db.execute('SELECT count(*) FROM review_runs').fetchone()[0],1)
        self.assertEqual(reviews.read(self.m,check['id'])['role'],'outcome')
        self.assertNotEqual(latest(self.m,ep,'work_plan')['state'],'done')
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
            server.tree_interval=0
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
    def lesson(self):
        source=self.m.source('lesson-evidence','Parser result','The tagged fixture failed.','The tagged path was omitted.','tool',subject='code')
        ep=self.work['episode_id']
        return self.m.record(ep,'lesson',{'when':'Text is decoded.','do':'Check the tagged path.','because':'The tagged fixture failed.',
            'exceptions':'Undocumented encodings.','pattern_type':'recovery'},expected_version=self.m.episode(ep)['version'],actor='test',
            request_key='lesson',evidence=[{'source_id':source['id'],'reason':'The fixture shows the failure.'}])
    def test_human_requirement_and_lesson_reviews_preserve_exceptions(self):
        """Moved from the retired tests/test_workspace_knowledge.py."""
        ep=self.work['episode_id']
        counts=lambda:[self.m.db.execute('SELECT count(*) FROM '+name).fetchone()[0] for name in ('sources','events')]
        action(self.m,'requirements',{'requirements':['Preserve tagged exceptions.'],'reason':'The user clarifies the scope.','expected_version':self.m.direction()['version']},'requirements')
        self.assertEqual(self.m.requirements,['Preserve tagged exceptions.'])
        source=self.m.source('fixture-result','Observed evidence','The fixture preserves exceptions.','The tagged record passes.','tool')
        lesson=self.m.record(ep,'lesson',{'when':'The reviewer evaluates tagged records.','do':'Preserve the explicit exception.',
            'because':'The evidence requires this condition.','exceptions':'This does not apply to untagged records.'},
            expected_version=self.m.episode(ep)['version'],request_key='lesson-exception',actor='test',evidence=[{'source_id':source['id'],'reason':'This fixture establishes the condition.'}])
        action(self.m,'lesson_review',{'lesson_id':lesson['id'],'expected_version':self.m.episode(ep)['version'],'status':'accepted','reason':'The user checks the complete condition and exception.'},'accept-exception')
        self.assertEqual(self.m.read(lesson['id'])['status'],'accepted')
        self.assertIn('untagged',self.m.read(lesson['id'])['payload']['exceptions'])
        self.m.source('fixture-result','Changed evidence','The condition changes.','The tagged record fails.','tool')
        before=counts()
        with self.assertRaises(InvalidRecord):
            action(self.m,'lesson_review',{'lesson_id':lesson['id'],'expected_version':self.m.episode(ep)['version'],'status':'accepted','reason':'Attempt to accept stale evidence.'},'stale-accept')
        self.assertEqual(before,counts())
    def test_saving_progress_reuses_evidence_without_an_unused_user_source(self):
        count=lambda:self.m.db.execute('SELECT count(*) FROM sources').fetchone()[0]
        before=count();action(self.m,'plan',self.update(priority='high'),'priority')
        self.assertEqual(count(),before)
        self.assertEqual(self.m.read(latest(self.m,self.work['episode_id'],'work_plan')['id'])['evidence'],self.m.read(self.work['id'])['evidence'])
        action(self.m,'plan',self.update(paths=['src/parser.py']),'governed-paths')
        self.assertEqual(count(),before+1)
        body=self.m.read(self.m.read(latest(self.m,self.work['episode_id'],'work_plan')['id'])['evidence'][0]['source_id'],detail=True)['body']
        self.assertIn('The allowed paths are: src/parser.py.',body)
    def test_plan_accepts_item_type_acceptance_and_parent(self):
        parent=action(self.m,'plan',{**self.data,'title':'Import epic','payload':{**self.payload,'item_type':'epic'}},'epic')
        child=action(self.m,'plan',{**self.data,'title':'Import story','payload':{**self.payload,'item_type':'story','parent_id':parent['episode_id'],
            'acceptance':['Tagged Latin-1 files import without loss.']}},'story')
        plan=latest(self.m,child['episode_id'],'work_plan')
        self.assertEqual((plan['item_type'],plan['parent_id']),('story',parent['episode_id']))
        body=self.m.read(self.m.read(plan['id'])['evidence'][0]['source_id'],detail=True)['body']
        self.assertIn('Acceptance criterion: Tagged Latin-1 files import without loss.',body)
        self.assertIn('This work item belongs to Import epic.',body)
        with self.assertRaises(InvalidRecord):
            action(self.m,'plan',{**self.data,'title':'Bad item','payload':{**self.payload,'item_type':'feature'}},'bad-type')
    def test_allow_paths_adds_unique_patterns_with_user_evidence(self):
        action(self.m,'plan',self.update(paths=['src/parser.py']),'paths')
        ep=self.work['episode_id'];version=self.m.episode(ep)['version']
        data={'episode_id':ep,'expected_version':version,'paths':['docs/**','src/parser.py','docs/**'],'reason':'The parser notes live in docs.'}
        result=action(self.m,'allow_paths',data,'allow')
        self.assertEqual((result['added'],result['paths']),(['docs/**'],['src/parser.py','docs/**']))
        self.assertEqual(action(self.m,'allow_paths',data,'allow'),result)
        plan=self.m.read(latest(self.m,ep,'work_plan')['id'])
        self.assertEqual(plan['payload']['paths'],['src/parser.py','docs/**'])
        self.assertEqual((plan['actor'],plan['evidence'][0]['origin']),('workspace-user','user'))
        self.assertEqual(plan['payload']['scope'],self.payload['scope'])
        self.assertEqual(guards.scope_changes(self.m),[])
        with self.assertRaises(Conflict):action(self.m,'allow_paths',data,'stale-allow')
        with self.assertRaises(InvalidRecord):
            action(self.m,'allow_paths',{**data,'expected_version':self.m.episode(ep)['version'],'paths':['docs/**']},'again')
        with self.assertRaises(InvalidRecord):
            action(self.m,'allow_paths',{**data,'expected_version':self.m.episode(ep)['version'],'paths':['../outside']},'outside')
    def test_allow_paths_rejects_patterns_for_the_whole_project_or_computer(self):
        action(self.m,'plan',self.update(paths=['src/parser.py']),'paths')
        ep=self.work['episode_id']
        for index,paths in enumerate((['/'],['/**'],['**'],['.'],['**/*'],['docs/**','/'])):
            with self.subTest(paths=paths),self.assertRaises(InvalidRecord):
                action(self.m,'allow_paths',{'episode_id':ep,'expected_version':self.m.episode(ep)['version'],'paths':paths,'reason':'Allow everything.'},'broad-'+str(index))
        self.assertEqual(latest(self.m,ep,'work_plan')['paths'],['src/parser.py'])
        result=action(self.m,'allow_paths',{'episode_id':ep,'expected_version':self.m.episode(ep)['version'],'paths':['**/*.md'],'reason':'The notes are Markdown.'},'markdown')
        self.assertEqual(result['added'],['**/*.md'])
    def test_request_work_review_retry_with_the_same_key_returns_the_first_review(self):
        from unittest.mock import patch
        reviews.configure(self.m,self.root,'codex');ep=self.work['episode_id']
        run=fake_run(self.m,ep,project=self.root)
        fake_run(self.m,ep,role='work_review',state='cancelled',parent=run,project=self.root)
        def queue(memory,run_id,*,request_key,max_seconds=900):
            return reviews.read(memory,fake_run(memory,ep,role='work_review',state='queued',parent=run_id,project=self.root,request_key=request_key))
        with patch.object(delegation,'request_review',side_effect=queue) as requested,patch.object(reviews,'launch') as launched:
            first=action(self.m,'request_work_review',{'run_id':run},'ws-rwr')
            again=action(self.m,'request_work_review',{'run_id':run},'ws-rwr')
        self.assertEqual((again['id'],again['state'],again['parent_run']),(first['id'],'queued',run))
        requested.assert_called_once()
        launched.assert_called_once()
    def test_lesson_review_triggers_apply_only_on_acceptance(self):
        lesson=self.lesson();ep=self.work['episode_id']
        base={'lesson_id':lesson['id'],'reason':'The user checks the lesson.','paths':['src/**'],'keywords':['encoding'],'failure_type':'lost_text'}
        with self.assertRaises(InvalidRecord):
            action(self.m,'lesson_review',{**base,'status':'rejected','expected_version':self.m.episode(ep)['version']},'reject-with-triggers')
        action(self.m,'lesson_review',{**base,'status':'accepted','expected_version':self.m.episode(ep)['version']},'accept')
        [guard]=guards.active_guards(self.m)
        self.assertEqual((guard['lesson_id'],guard['paths'],guard['keywords'],guard['failure_type']),(lesson['id'],['src/**'],['encoding'],'lost_text'))
    def test_link_component_and_kickoff_answers_record_the_user(self):
        ep=self.work['episode_id']
        data={'from_id':ep,'to_id':'component:src','type':'affects_component','reason':'The parser lives in src.'}
        first=action(self.m,'link',data,'link');self.assertEqual(action(self.m,'link',data,'link'),first)
        edge=next(e for e in graph.edges(self.m,[ep]) if e['origin']=='link')
        self.assertEqual((edge['to'],edge['type']),('component:src','affects_component'))
        self.assertEqual(self.m.db.execute('SELECT actor FROM links WHERE id=?',(first['id'],)).fetchone()[0],'workspace-user')
        item=action(self.m,'component',{'title':'Client CRM','kind':'system','description':'The CRM holds client records.','status':'confirmed'},'crm')
        self.assertEqual((item['status'],item['actor']),('confirmed','workspace-user'))
        self.assertEqual(self.m.read(item['evidence'][0]['source_id'])['origin'],'user')
        project=self.root/'automation'
        result=templates.scaffold(project,'automation',git=False,_launcher=LAUNCHER)
        question=templates.TEMPLATES['automation']['questions'][0]['id']
        with Memory(result['database']) as other:
            with self.assertRaises(InvalidRecord):action(other,'answer_kickoff',{'question_ids':['unknown_question'],'text':'An answer.'},'unknown')
            note=action(other,'answer_kickoff',{'question_ids':[question],'text':'The client sends orders by e-mail.'},'answer')
            answered={q['id']:q for q in templates.kickoff(other)['questions']}[question]
            self.assertEqual(answered['answered_by'],note['id'])
            self.assertEqual(other.read(note['id'])['actor'],'workspace-user')
    def test_removed_operations_and_unknown_fields_are_rejected(self):
        for operation in ('skill_import','skill_selection','map'):
            with self.assertRaises(InvalidRecord):action(self.m,operation,{},'removed-'+operation)
        with self.assertRaises(InvalidRecord):action(self.m,'comment',{'episode_id':self.work['episode_id'],'text':'Missing version.'},'missing')
        with self.assertRaises(InvalidRecord):action(self.m,'merge',{'run_id':'check_x','actor':'assistant'},'actor')
    def test_agent_run_actions_use_the_user_and_start_a_worker_once(self):
        from unittest.mock import patch
        ep=self.work['episode_id']
        run={'id':'check_fixture','episode_id':ep,'role':'work','host':'codex','state':'queued','error':'','parent_run':None,'branch':'pm/fixture'}
        with patch.object(delegation,'request_work',return_value=run) as requested,patch.object(delegation,'launch') as launched:
            first=action(self.m,'delegate',{'episode_id':ep,'host':'codex'},'delegate')
            again=action(self.m,'delegate',{'episode_id':ep,'host':'codex'},'delegate')
        self.assertEqual((first,again['id']),(again,'check_fixture'))
        self.assertEqual(requested.call_args.kwargs['request_key'],'delegate:delegate')
        launched.assert_called_once()
        with patch.object(delegation,'merge',return_value={'merged':True}) as merged,patch.object(delegation,'discard',return_value={'discarded':True}) as discarded:
            action(self.m,'merge',{'run_id':'check_fixture','override_reason':'The user inspected the diff.'},'merge')
            action(self.m,'discard',{'run_id':'check_other','reason':'The approach changed.'},'discard')
            with self.assertRaises(Conflict):action(self.m,'merge',{'run_id':'check_other'},'merge')
        self.assertEqual((merged.call_args.kwargs['actor'],merged.call_args.kwargs['override_reason']),('workspace-user','The user inspected the diff.'))
        self.assertEqual((discarded.call_args.kwargs['actor'],discarded.call_args.kwargs['reason']),('workspace-user','The approach changed.'))
    def test_review_and_cancel_actions_request_one_check(self):
        from unittest.mock import patch
        reviews.configure(self.m,self.root,'codex');ep=self.work['episode_id']
        with patch.object(reviews,'launch') as launched:
            first=action(self.m,'review',{'episode_id':ep,'role':'intent'},'intent-check')
            self.assertEqual(action(self.m,'review',{'episode_id':ep,'role':'intent'},'intent-check')['id'],first['id'])
            launched.assert_called_once()
            with self.assertRaises(InvalidRecord):action(self.m,'review',{'episode_id':ep,'role':'work'},'bad-role')
        self.assertEqual(first['state'],'queued')
        self.assertEqual(action(self.m,'cancel_run',{'run_id':first['id']},'cancel')['state'],'cancelled')
    def test_request_work_review_requires_a_completed_run_without_a_current_review(self):
        from unittest.mock import patch
        reviews.configure(self.m,self.root,'codex');ep=self.work['episode_id']
        run=fake_run(self.m,ep,project=self.root)
        review={'id':'check_review','episode_id':ep,'role':'work_review','host':'claude','state':'queued','error':'','parent_run':run,'branch':None}
        with patch.object(delegation,'request_review',return_value=review) as requested,patch.object(reviews,'launch') as launched:
            self.assertEqual(action(self.m,'request_work_review',{'run_id':run},'rereview')['id'],'check_review')
            launched.assert_called_once()
            fake_run(self.m,ep,role='work_review',state='pass',parent=run,project=self.root)
            with self.assertRaises(InvalidRecord):action(self.m,'request_work_review',{'run_id':run},'rereview-after-pass')
            unfinished=fake_run(self.m,ep,state='failed',project=self.root)
            with self.assertRaises(InvalidRecord):action(self.m,'request_work_review',{'run_id':unfinished},'rereview-failed')
        requested.assert_called_once()
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
