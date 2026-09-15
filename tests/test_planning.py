"""Exercise continuation gates and board state against real SQLite history."""
import json
from pathlib import Path
import tempfile
import unittest
from memory_module import Memory, Conflict, InvalidRecord, BudgetTooSmall
from memory_module import codex_host
from memory_module.mcp import write, dispatch, tool_result
from memory_module.planning import board, card, latest, next_work


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.m=Memory.create(self.root/'memory.sqlite','Planning',['Keep strict UTF-8 except on the tagged legacy endpoint.'])
        codex_host.initialize(self.m)
        self.source=self.m.source('user','Scope','User instruction','Preserve strict UTF-8 and the tagged legacy Latin-1 endpoint.','user')
        self.evidence=[{'source_id':self.source['id'],'reason':'The user defines the goal and the exception.'}]
        self.counter=0
    def tearDown(self):
        self.m.close();self.tmp.cleanup()
    def key(self):
        self.counter+=1;return str(self.counter)
    def plan(self, **changes):
        return {'state':'ready','next_action':'Inspect both parser endpoints.','scope':'Keep UTF-8 strict except the tagged legacy Latin-1 endpoint.','autonomy':'act','reason':'The user requests the repair.',**changes}
    def work(self, subject='code', **changes):
        return write(self.m,'plan',self.key(),{'title':'Repair parsing','objective':'Preserve strict UTF-8 and the legacy exception.','criterion':'Both encoding paths behave as requested.','subject':subject,'payload':self.plan(**changes),'actor':'assistant','evidence':self.evidence})
    def revise(self, work, **changes):
        old=latest(self.m,work['episode_id'],'work_plan');old.pop('id')
        return write(self.m,'plan',self.key(),{'episode_id':work['episode_id'],'expected_version':self.m.episode(work['episode_id'])['version'],'payload':{**old,**changes},'actor':'assistant','evidence':self.evidence},session_id='session')
    def decision(self, work):
        ep=work['episode_id']
        return self.m.record(ep,'decision',{'decision':'Preserve both explicit encoding paths.','why':'The user identifies the legacy exception.','expected':'Both endpoints retain the documented behaviour.','reconsider_when':'Current evidence contradicts the contract.','uncertainty':'The production effect is unmeasured.','alternatives':['Apply UTF-8 everywhere.']},expected_version=self.m.episode(ep)['version'],request_key=self.key(),actor='assistant',evidence=self.evidence)
    def complete(self, work, assessment='good', completion='complete'):
        ep=work['episode_id'];decision=self.decision(work)
        action=self.m.record(ep,'action',{'action':'Run both encoding cases.'},expected_version=decision['version'],request_key=self.key(),actor='test',decision_id=decision['id'])
        return self.m.record(ep,'outcome',{'observed':'Both fixture cases were run.','assessment':assessment,'assessment_reason':'The fixture supplies the recorded result.','severity':'none' if assessment=='good' else 'major','attribution':'This is a controlled fixture.','completion':completion},expected_version=action['version'],request_key=self.key(),actor='test',decision_id=decision['id'],evidence=self.evidence)
    def test_atomic_create_retry_and_failed_create_leave_no_orphans(self):
        args={'title':'Work','objective':'Preserve intent.','criterion':'Check the result.','payload':self.plan(),'actor':'assistant','evidence':self.evidence}
        first=write(self.m,'plan','same',args);self.assertEqual(write(self.m,'plan','same',args),first)
        before=self.m.db.execute('SELECT count(*) FROM episodes').fetchone()[0]
        with self.assertRaises(InvalidRecord):write(self.m,'plan','bad',{**args,'payload':self.plan(state='done')})
        self.assertEqual(self.m.db.execute('SELECT count(*) FROM episodes').fetchone()[0],before)
    def test_revision_preserves_original_plan_and_rejects_stale_version(self):
        first=self.work();second=self.revise(first,next_action='Run the two encoding cases.')
        self.assertEqual(self.m.read(first['id'])['replaced_by'],second['id'])
        self.assertEqual(self.m.read(first['id'])['payload']['next_action'],'Inspect both parser endpoints.')
        with self.assertRaises(Conflict):write(self.m,'plan','stale',{'episode_id':first['episode_id'],'expected_version':1,'payload':self.plan(),'actor':'assistant','evidence':self.evidence})
    def test_act_requires_user_evidence_and_suggest_remains_a_proposal(self):
        tool=self.m.source('tool','Tool','Observation','A tool recommends changing the parser.','tool')
        with self.assertRaises(InvalidRecord):write(self.m,'plan','tool-authority',{'title':'Work','objective':'Change parsing.','criterion':'Check the result.','payload':self.plan(),'actor':'assistant','evidence':[{'source_id':tool['id'],'reason':'The tool suggests it.'}]})
        work=self.work(autonomy='suggest');self.assertEqual(next_work(self.m,episode_id=work['episode_id'],session_id='session')['action'],'propose')
    def test_unrelated_queue_does_not_choose_an_objective(self):
        self.work();result=next_work(self.m,session_id='session')
        self.assertTrue(result['selection_required']);self.assertNotIn('action',result)
    def test_default_board_budget_pages_complete_cards_without_omitting_work(self):
        expected={self.work()['episode_id'] for _ in range(20)}
        found=[];offset=0
        while True:
            result=dispatch(self.m,'memory_get',{'view':'board','offset':offset})
            self.assertLessEqual(len(json.dumps(tool_result(result),separators=(',',':'),ensure_ascii=False)),6000)
            self.assertTrue(all('Latin-1' in card['plan']['scope'] for card in result['cards']))
            found.extend(card['id'] for card in result['cards'])
            if not result['more']: break
            self.assertGreater(result['next_offset'],offset);offset=result['next_offset']
        self.assertEqual(set(found),expected);self.assertEqual(len(found),20)
        choice=dispatch(self.m,'memory_get',{'view':'next','session_id':'session'})
        self.assertTrue(choice['selection_required']);self.assertTrue(choice['board']['more'])
    def test_ready_packet_preserves_scope_intent_and_completion_criterion(self):
        work=self.work();result=dispatch(self.m,'memory_get',{'view':'next','id':work['episode_id'],'session_id':'session','max_chars':3000})
        self.assertEqual(result['action'],'continue')
        self.assertIn('legacy',result['work']['intent']);self.assertIn('Latin-1',result['work']['plan']['scope'])
        self.assertEqual(result['work']['done_when'],'Both encoding paths behave as requested.')
        self.assertLess(len(json.dumps(tool_result(result))),3000)
        with self.assertRaises(BudgetTooSmall):dispatch(self.m,'memory_get',{'view':'next','id':work['episode_id'],'max_chars':500})
    def test_dependency_requires_current_completion_and_cycles_are_rejected(self):
        first=self.work();second=self.work(depends_on=[{'episode_id':first['episode_id'],'reason':'The parser result must exist before review.'}])
        self.assertEqual(card(self.m,second['episode_id'])['state'],'blocked')
        with self.assertRaises(InvalidRecord):self.revise(first,depends_on=[{'episode_id':second['episode_id'],'reason':'This would form a cycle.'}])
        self.complete(first);self.assertEqual(card(self.m,second['episode_id'])['state'],'ready')
        self.m.source('user','Scope','Changed instruction','Only UTF-8 is now approved.','user')
        self.assertEqual(card(self.m,second['episode_id'])['state'],'blocked')
    def test_unconfirmed_action_survives_unknown_reconciliation(self):
        work=self.work();decision=self.decision(work);codex_host.bind(self.m,'session',decision['id'],'bind')
        codex_host.capture(self.m,{'hook_event_name':'PreToolUse','session_id':'session','turn_id':'turn','tool_use_id':'once','tool_name':'Bash','tool_input':{'command':'non-idempotent'}})
        marker=self.root/'marker';marker.write_text('once\n')
        pending=codex_host.status(self.m,'session')['unconfirmed'][0]
        codex_host.reconcile(self.m,pending['id'],'unknown','The marker exists; process completion remains unknown.',self.evidence,'reconcile')
        self.assertEqual(next_work(self.m,episode_id=work['episode_id'],session_id='session')['action'],'reconcile')
        self.assertEqual(marker.read_text(),'once\n');self.assertEqual(card(self.m,work['episode_id'])['state'],'blocked')
    def test_other_session_must_inspect_the_owner(self):
        work=self.work();self.revise(work,state='in_progress')
        self.assertEqual(next_work(self.m,episode_id=work['episode_id'],session_id='another')['action'],'inspect_owner')
        self.assertEqual(next_work(self.m,episode_id=work['episode_id'],session_id='session')['action'],'continue')
    def test_completed_tool_without_an_outcome_does_not_repeat_the_saved_action(self):
        work=self.work();decision=self.decision(work);codex_host.bind(self.m,'session',decision['id'],'bind')
        event={'session_id':'session','turn_id':'turn','tool_use_id':'once','tool_name':'Bash'}
        codex_host.capture(self.m,dict(event,hook_event_name='PreToolUse'))
        codex_host.capture(self.m,dict(event,hook_event_name='PostToolUse',tool_response={'exit_code':0}))
        self.assertEqual(next_work(self.m,episode_id=work['episode_id'],session_id='session')['action'],'inspect_execution')
    def test_done_requires_actual_good_complete_outcome(self):
        work=self.work()
        with self.assertRaises(InvalidRecord):self.revise(work,state='done')
        self.complete(work,assessment='bad')
        with self.assertRaises(InvalidRecord):self.revise(work,state='done')
        other=self.work();self.complete(other);self.revise(other,state='done')
        self.assertEqual(card(self.m,other['episode_id'])['state'],'done')
        self.m.source('user','Scope','Changed instruction','The earlier completion criterion changes.','user')
        self.assertEqual(card(self.m,other['episode_id'])['state'],'review')
    def test_revised_decision_does_not_inherit_an_earlier_attempt_outcome(self):
        work=self.work();outcome=self.complete(work,assessment='bad')
        previous=self.m.read(outcome['id'])['decision_id']
        ep=work['episode_id'];old=self.m.read(previous)
        decision=self.m.record(ep,'decision',old['payload'],expected_version=self.m.episode(ep)['version'],
                               request_key=self.key(),actor='test',evidence=self.evidence,supersedes=previous)
        self.assertIsNone(card(self.m,ep)['outcome'])
        self.assertEqual(next_work(self.m,episode_id=ep)['action'],'continue')
        self.m.record(ep,'action',{'action':'Run the revised repair.'},expected_version=decision['version'],
                      request_key=self.key(),actor='test',decision_id=decision['id'])
        self.assertEqual(next_work(self.m,episode_id=ep)['action'],'inspect_execution')
    def test_scope_drift_invalidates_completion_but_progress_does_not(self):
        work=self.work();self.complete(work)
        self.assertEqual(next_work(self.m,episode_id=work['episode_id'],session_id='session')['action'],'finalize')
        self.revise(work,state='done')
        self.assertEqual(card(self.m,work['episode_id'])['state'],'done')
        self.revise(work,state='review',scope='A new explicitly authorised scope needs an additional endpoint.')
        self.assertTrue(any('scope' in i['reason'] for i in card(self.m,work['episode_id'])['issues']))
        with self.assertRaises(InvalidRecord):self.revise(work,state='done')
    def test_corrected_outcome_reassesses_inherited_source_without_rewriting_history(self):
        work=self.work()
        measured=self.m.source('measurement','Measured result','Original observation','Both cases were inspected.','tool')
        user_evidence=self.evidence
        self.evidence=[*user_evidence,{'source_id':measured['id'],'reason':'The original inspection informs the decision.'}]
        outcome=self.complete(work)
        self.evidence=user_evidence
        original=self.m._event(outcome['id'])
        current=self.m.source('measurement','Measured result','Corrected observation','The strict and tagged legacy cases are verified.','tool')
        self.assertTrue(self.m._needs_review(outcome['id']))
        corrected=self.m.record(work['episode_id'],'outcome',original['payload'],
            expected_version=self.m.episode(work['episode_id'])['version'],request_key=self.key(),actor='test',
            decision_id=original['decision_id'],supersedes=outcome['id'],
            evidence=[{'source_id':current['id'],'reason':'The corrected evidence explicitly reassesses the result.'}])
        self.assertFalse(self.m._needs_review(corrected['id']))
        self.assertTrue(self.m._needs_review(original['decision_id']))
        self.assertEqual(self.m._event(outcome['id']),original)
        self.revise(work,state='done')
        self.assertEqual(card(self.m,work['episode_id'])['state'],'done')
        self.m.source('measurement','Measured result','Later change','The result now needs a new assessment.','tool')
        self.assertTrue(self.m._needs_review(corrected['id']))
        self.assertNotEqual(card(self.m,work['episode_id'])['state'],'done')

    def test_reassessment_requires_current_matching_evidence_and_keeps_direct_stale_references(self):
        for case in ('unrelated','direct_stale','review_due'):
            with self.subTest(case=case):
                work=self.work()
                measured=self.m.source(case,'Measured result','Original observation','Both cases were inspected.','tool')
                user_evidence=self.evidence
                stale={'source_id':measured['id'],'reason':'The original observation informs the decision.'}
                self.evidence=[*user_evidence,stale]
                outcome=self.complete(work);original=self.m._event(outcome['id'])
                self.evidence=user_evidence
                current=self.m.source(case,'Measured result','Correction',case,'tool',
                    **({'review_after':'2000-01-01T00:00:00+00:00'} if case=='review_due' else {}))
                if case=='unrelated':
                    current=self.m.source('different-key','Other source','Other observation','This does not reassess the changed source.','tool')
                evidence=[{'source_id':current['id'],'reason':'This fixture tests the boundary of reassessment.'}]
                if case=='direct_stale':evidence.append(stale)
                corrected=self.m.record(work['episode_id'],'outcome',original['payload'],
                    expected_version=self.m.episode(work['episode_id'])['version'],request_key=self.key(),actor='test',
                    decision_id=original['decision_id'],supersedes=outcome['id'],evidence=evidence)
                self.assertTrue(self.m._needs_review(corrected['id']))
                with self.assertRaises(InvalidRecord):self.revise(work,state='done')
    def test_review_guidance_waits_only_for_the_existing_active_check(self):
        import shlex
        from memory_module import reviews
        reviews.configure(self.m,self.root,'codex')
        work=self.work();self.complete(work);ep=work['episode_id']
        self.assertEqual(next_work(self.m,episode_id=ep)['action'],'request_review')
        run=reviews.request(self.m,ep,request_key=self.key())
        step=next_work(self.m,episode_id=ep)['next_step']
        self.assertEqual(step['action'],'wait_review')
        self.assertEqual(step['check_id'],run['id'])
        self.assertEqual(shlex.split(step['wait_command'])[4:6],['--wait',run['id']])
        expired=reviews.wait(self.m,run['id'],seconds=0)
        self.assertTrue(expired['wait_expired']);self.assertEqual(expired['id'],run['id'])
        with self.assertRaises(InvalidRecord) as caught:self.revise(work,state='done')
        self.assertEqual(caught.exception.details['next_step']['check_id'],run['id'])
        self.assertEqual(self.m.db.execute('SELECT count(*) FROM review_runs').fetchone()[0],1)
        with self.m._write():self.m.db.execute("UPDATE review_runs SET state='pass' WHERE id=?",(run['id'],))
        ready=next_work(self.m,episode_id=ep)
        self.assertEqual(ready['action'],'finalize');self.assertNotIn('wait_command',ready['next_step'])
        self.revise(work,state='done');self.assertEqual(card(self.m,ep)['state'],'done')

    def test_passed_review_with_changed_evidence_names_the_source_instead_of_waiting(self):
        from memory_module import reviews
        reviews.configure(self.m,self.root,'codex')
        work=self.work();outcome=self.complete(work);ep=work['episode_id']
        original=self.m._event(outcome['id'])
        run=reviews.request(self.m,ep,request_key=self.key())
        with self.m._write():self.m.db.execute("UPDATE review_runs SET state='pass' WHERE id=?",(run['id'],))
        current=self.m.source('user','Scope','Revised scope','The contract now needs a fresh assessment.','user')
        next_step=next_work(self.m,episode_id=ep)['next_step']
        self.assertEqual(next_step['action'],'refresh_evidence')
        self.assertEqual(next_step['read_with'],{'view':'record','id':self.source['id']})
        self.assertNotIn('wait_command',next_step)
        check=card(self.m,ep)['agent_check']
        self.assertEqual((check['state'],check['run_state']),('stale','pass'))
        packet=dispatch(self.m,'memory_get',{'view':'reviews','id':ep})
        self.assertEqual(packet['current']['state'],'stale')
        self.assertEqual(packet['runs'][0]['state'],'pass')
        self.evidence=[{'source_id':current['id'],'reason':'Current intent evidence.'}]
        with self.assertRaises(InvalidRecord) as caught:
            self.revise(work,state='done')
        self.assertEqual(caught.exception.details['next_step']['action'],'refresh_evidence')
        self.assertNotIn('wait_command',caught.exception.details['next_step'])
        self.assertEqual(self.m._event(outcome['id']),original)

    def test_stale_project_files_and_finished_review_states_never_offer_wait(self):
        from memory_module import reviews
        reviews.configure(self.m,self.root,'codex')
        for state,action in [('changes_required','review_findings'),('uncertain','review_findings'),
                             ('timed_out','inspect_review'),('failed','inspect_review'),('cancelled','inspect_review'),('pass','refresh_review')]:
            with self.subTest(state=state):
                work=self.work();self.complete(work);ep=work['episode_id']
                run=reviews.request(self.m,ep,request_key=self.key())
                with self.m._write():self.m.db.execute('UPDATE review_runs SET state=? WHERE id=?',(state,run['id']))
                if state=='pass':(self.root/'changed.txt').write_text('The implementation changed after review.')
                step=next_work(self.m,episode_id=ep)['next_step']
                self.assertEqual(step['action'],action);self.assertNotIn('wait_command',step)
                with self.assertRaises(InvalidRecord) as caught:self.revise(work,state='done')
                self.assertEqual(caught.exception.details['next_step']['action'],action)

    def test_completion_error_explains_missing_and_partial_outcomes(self):
        for completion in (None,'partial'):
            with self.subTest(completion=completion):
                work=self.work()
                if completion:self.complete(work,completion=completion)
                with self.assertRaises(InvalidRecord) as caught:self.revise(work,state='done')
                step=caught.exception.details['next_step']
                self.assertEqual(step['action'],'review_outcome' if completion else 'assess_outcome')
                self.assertNotIn('wait_command',step)

    def test_sprint_filters_and_closed_sprint_do_not_hide_unfinished_work(self):
        sprint=write(self.m,'sprint','sprint',{'title':'Sprint 1','objective':'Repair parsing.','criterion':'Both cases pass.','payload':{'starts_on':'2026-09-14','ends_on':'2026-09-21','status':'active','reason':'The iteration is scheduled.'},'actor':'assistant','evidence':self.evidence})
        work=self.work(sprint_id=sprint['episode_id']);self.work()
        self.assertEqual(board(self.m,sprint_id=sprint['episode_id'])['total'],1)
        self.assertEqual(board(self.m,sprint_id='unassigned')['total'],1)
        p=latest(self.m,sprint['episode_id'],'sprint');p.pop('id');p['status']='closed'
        write(self.m,'sprint','close',{'episode_id':sprint['episode_id'],'expected_version':1,'payload':p,'actor':'assistant','evidence':self.evidence})
        self.assertEqual(board(self.m,sprint_id=sprint['episode_id'])['total'],1)
        with self.assertRaises(InvalidRecord):self.revise(work,next_action='Continue the repair.')
    def test_existing_data_trigger_upgrade_is_transactional(self):
        ep=self.m.start('Historical work','Keep history.','test','Exact records remain.')
        note=self.m.record(ep['id'],'note',{'text':'The original record stays.'},expected_version=0,request_key='old',actor='test')
        old=tuple(self.m.db.execute('SELECT * FROM events WHERE id=?',(note['id'],)).fetchone())
        trigger=self.m.db.execute("SELECT sql FROM sqlite_master WHERE name='checked_event'").fetchone()[0]
        self.m.db.execute('DROP TRIGGER checked_event');self.m.db.execute(trigger.replace(",'work_plan','sprint'",''))
        self.work()
        self.assertEqual(tuple(self.m.db.execute('SELECT * FROM events WHERE id=?',(note['id'],)).fetchone()),old)
        self.assertEqual(self.m.db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
        self.assertEqual(card(self.m,ep['id'])['state'],'backlog')
        with self.assertRaises(Exception):self.m.db.execute("UPDATE events SET payload='{}' WHERE id=?",(note['id'],))
    def test_board_pages_and_context_keep_explained_cross_subject_dependencies(self):
        first=self.work();second=self.work(subject='writing',depends_on=[{'episode_id':first['episode_id'],'reason':'The coding result supplies evidence for review.'}])
        a=board(self.m,limit=1);b=board(self.m,limit=1,offset=1)
        self.assertTrue(a['more']);self.assertFalse(b['more']);self.assertNotEqual(a['cards'][0]['id'],b['cards'][0]['id'])
        context=self.m.context('parser',subject='writing',budget=20000)
        self.assertIn('supplies evidence',json.dumps(context))
        self.assertNotIn(first['id'],json.dumps(context))
    def test_hook_points_to_intent_only_for_planned_active_work(self):
        work=self.work();decision=self.decision(work);codex_host.bind(self.m,'session',decision['id'],'bind')
        for event in ['SessionStart','Stop','PostCompact']:
            result=codex_host.capture(self.m,{'hook_event_name':event,'session_id':'session','turn_id':event})
            self.assertIn('memory_get next',result['hookSpecificOutput']['additionalContext'])
        self.assertNotIn('memory_get next',codex_host.session_context(self.m,'unrelated'))
    def test_live_board_observes_an_external_plan_update(self):
        import os,signal
        from urllib.request import build_opener,ProxyHandler
        from memory_module.live import start
        work=self.work();server=start(self.m.path);opener=build_opener(ProxyHandler({}))
        def read(endpoint):
            with opener.open(server['url']+endpoint,timeout=5) as response:return json.load(response)
        try:
            before=read('api/board');self.assertEqual(before['cards'][0]['state'],'ready')
            self.revise(work,state='blocked',next_action='Obtain the missing local evidence.')
            after=read('api/board');self.assertNotEqual(before['revision'],after['revision'])
            self.assertEqual(after['cards'][0]['state'],'blocked')
            self.assertEqual(after['cards'][0]['plan']['next_action'],'Obtain the missing local evidence.')
            self.assertEqual(read('api/sprints')['sprints'],[])
        finally:
            os.kill(server['pid'],signal.SIGTERM)
            import time
            for _ in range(100):
                try:read('api/health')
                except OSError:break
                time.sleep(.01)


if __name__=='__main__':unittest.main()
