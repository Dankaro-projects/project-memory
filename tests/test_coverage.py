import shutil
import json
import queue
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen
from memory_module import Memory, InvalidRecord, Conflict, codex_host, reviews
from memory_module.coverage import inspect, hook
from memory_module.mcp import write, dispatch, tool_result
from memory_module.planning import latest, completed_result, card
from memory_module.live import Viewer
from memory_module.health import inspect as health


class CoverageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(ignore_cleanup_errors=True);self.root=Path(self.temp.name)
        self.m=Memory.create(self.root/'memory.sqlite','Coverage',['Keep the tagged legacy exception.'])
        codex_host.initialize(self.m);self.n=0
        self.source=self.m.source('request','The user requests both paths.','Preserve both encodings.','Strict UTF-8 rejects invalid bytes. Tagged Latin-1 succeeds.','user',subject='code')
        self.evidence=[{'source_id':self.source['id'],'reason':'The user states the required exception.'}]
    def tearDown(self):self.m.close();shutil.rmtree(self.temp.name, ignore_errors=True)
    def event(self,name,**kw):
        return {'hook_event_name':name,'session_id':'s','turn_id':'1',**kw}
    def capture(self,name,**kw):
        codex_host.capture(self.m,self.event(name,**kw))
        return self.m.db.execute('SELECT id FROM host_receipts WHERE event_name=? ORDER BY rowid DESC LIMIT 1',(name,)).fetchone()[0]
    def prompt(self,text='Preserve both encoding paths.',**kw):return self.capture('UserPromptSubmit',prompt=text,**kw)
    def tool(self,key='tool',complete=True,command='private token',**kw):
        # Only a call with an effect outside the repository needs reconciliation when it reports no result.
        rid=self.capture('PreToolUse',tool_name='Bash',tool_use_id=key,tool_input={'command':command},**kw)
        if complete:self.capture('PostToolUse',tool_name='Bash',tool_use_id=key,tool_response={'exit_code':0},**kw)
        return rid
    def plan(self,prompt=None):
        self.n+=1
        data={'title':'Preserve parser behaviour','objective':'Support both paths.','criterion':'Both encoding paths pass.','subject':'code','actor':'fixture','evidence':self.evidence,'payload':{'state':'in_progress','scope':'Strict UTF-8 rejects invalid bytes. Tagged Latin-1 succeeds.','autonomy':'act','next_action':'Check both paths.','reason':'The user requires both paths.'}}
        if prompt:data['checkpoint']={'prompt_ids':[prompt],'effect':'new_work','reason':'The request requires both encoding paths.','requirements':['Strict UTF-8 rejects invalid bytes.','Tagged Latin-1 succeeds.']}
        return write(self.m,'plan','plan'+str(self.n),data,session_id='s')
    def decision(self,ep):
        self.n+=1
        return write(self.m,'record','decision'+str(self.n),{'episode_id':ep,'expected_version':self.m.episode(ep)['version'],'kind':'decision','actor':'fixture','evidence':self.evidence,'payload':{'decision':'Keep strict and legacy decoding.','why':'Both paths are required.','expected':'Both fixtures pass.','uncertainty':'Production input remains untested.','alternatives':['Drop the legacy path.'],'reconsider_when':'The contract changes.'}},session_id='s')
    def outcome(self,ep,d,completion='complete'):
        self.n+=1
        return write(self.m,'record','outcome'+str(self.n),{'episode_id':ep,'expected_version':self.m.episode(ep)['version'],'kind':'outcome','actor':'fixture','decision_id':d,'evidence':self.evidence,'payload':{'observed':'The fixture completed.','assessment':'good','assessment_reason':'Both paths passed in this fixture.','severity':'none','attribution':'The fixture assesses the selected decoder.','completion':completion}})
    def checkpoint(self,prompt,**kw):
        self.n+=1
        return write(self.m,'checkpoint','ack'+str(self.n),{'prompt_ids':[prompt],'effect':'informational','reason':'This was a read-only lookup with no new work.',**kw},session_id='s')
    def issues(self):return {x['type'] for x in inspect(self.m,'s')['issues']}
    def test_unbound_activity_and_chat_only_completion_intervene_once(self):
        self.prompt();self.tool()
        stop=self.event('Stop',last_assistant_message='Finished. All tests pass.')
        self.capture('Stop',last_assistant_message='Finished. All tests pass.')
        self.assertEqual(self.issues(),{'intent_unassessed','activity_unassigned'})
        self.assertEqual(hook(self.m,stop)['decision'],'block')
        for _ in range(5):self.assertEqual(hook(self.m,stop),{})
        self.assertTrue(self.issues());self.assertEqual(self.m.db.execute('SELECT count(*) FROM events').fetchone()[0],0)
    def test_informational_lookup_closes_without_creating_work(self):
        p=self.prompt('What does this file say?');self.tool();self.checkpoint(p)
        self.assertFalse(self.issues());self.assertEqual(hook(self.m,self.event('Stop')),{})
        self.assertEqual(self.m.db.execute('SELECT count(*) FROM episodes').fetchone()[0],0)
    def test_a_turn_that_only_reads_needs_no_assessment(self):
        self.prompt('Where did we leave off?')
        self.capture('PreToolUse',tool_name='Read',tool_use_id='r1',tool_input={'file_path':'README.md'})
        self.capture('PostToolUse',tool_name='Read',tool_use_id='r1',tool_response={'ok':True})
        self.capture('PreToolUse',tool_name='Bash',tool_use_id='r2',tool_input={'command':'git log -5 --oneline && git status -sb | head -3'})
        self.capture('PostToolUse',tool_name='Bash',tool_use_id='r2',tool_response={'exit_code':0})
        self.assertFalse(self.issues());self.assertEqual(hook(self.m,self.event('Stop')),{})
    def test_a_turn_that_may_change_something_still_needs_assessment(self):
        for turn,command in enumerate(('git commit -m x','sed -i s/a/b/ f','cat a > b','python3 - <<EOF\nprint(1)\nEOF','echo $(touch x)','find . -delete')):
            with self.subTest(command=command):
                p=self.prompt('Change it.',turn_id=str(turn))
                self.capture('PreToolUse',tool_name='Bash',tool_use_id=command,tool_input={'command':command},turn_id=str(turn))
                self.capture('PostToolUse',tool_name='Bash',tool_use_id=command,tool_response={'exit_code':0},turn_id=str(turn))
                self.assertEqual(self.issues(),{'intent_unassessed','activity_unassigned'})
                self.checkpoint(p)
    def test_the_read_only_classification_of_shell_commands(self):
        read=['ls -la','cd /x && sed -n 1,20p a.py','grep -n "a>b" f | head','git -C /x log --oneline','git branch','git tag --sort=-creatordate','wc -l *.py 2>/dev/null | sort -n']
        write=['git tag v1','git branch -D x','git diff --output=x','rm f','uv run pytest','sed --in-place s/a/b/ f','cat "unbalanced']
        for command in read:self.assertTrue(codex_host.read_only('Bash',{'command':command}),command)
        for command in write:self.assertFalse(codex_host.read_only('Bash',{'command':command}),command)
        self.assertTrue(codex_host.read_only('exec_command',{'cmd':'git status'}))
        self.assertTrue(codex_host.read_only('shell',{'command':['bash','-lc','ls']}))
        self.assertFalse(codex_host.read_only('Edit',{'file_path':'a'}))
    def test_greeting_without_activity_needs_no_administration(self):
        self.prompt('Thank you.');self.assertFalse(self.issues());self.assertEqual(hook(self.m,self.event('Stop')),{})
    def test_bundled_checkpoint_plan_and_missing_outcome(self):
        p=self.prompt();plan=self.plan(p);d=self.decision(plan['episode_id']);self.tool()
        self.assertEqual(self.issues(),{'outcome_missing'})
        write(self.m,'record','note',{'episode_id':plan['episode_id'],'expected_version':self.m.episode(plan['episode_id'])['version'],'kind':'note','actor':'fixture','payload':{'text':'The work is finished.'}})
        self.assertIn('outcome_missing',self.issues())
        self.outcome(plan['episode_id'],d['id']);self.assertFalse(self.issues())
    def test_changed_prompt_requires_revised_plan_and_blocks_old_completion(self):
        p=self.prompt();plan=self.plan(p);ep=plan['episode_id'];d=self.decision(ep);self.tool()
        p2=self.prompt('Also preserve CRLF.',turn_id='2')
        with self.assertRaises(InvalidRecord):self.checkpoint(p2,effect='changed',episode_id=ep,plan_id=latest(self.m,ep,'work_plan')['id'],requirements=['Preserve CRLF.'])
        self.outcome(ep,d['id']);self.assertFalse(completed_result(self.m,ep))
        updated={k:v for k,v in latest(self.m,ep,'work_plan').items() if k!='id'};updated['scope']+=' Preserve CRLF.'
        write(self.m,'plan','revise',{'episode_id':ep,'expected_version':self.m.episode(ep)['version'],'payload':updated,'actor':'fixture','evidence':self.evidence,'checkpoint':{'prompt_ids':[p2],'effect':'changed','reason':'The user adds a newline constraint.','requirements':['Strict UTF-8 rejects invalid bytes.','Tagged Latin-1 succeeds.','Preserve CRLF.']}},session_id='s')
        self.assertNotIn('intent_unassessed',self.issues());self.assertFalse(completed_result(self.m,ep))
    def test_delayed_assessment_rolls_back_bundled_write(self):
        p=self.prompt();self.prompt('The scope changes.',turn_id='2')
        with self.assertRaises(Conflict):self.plan(p)
        self.assertEqual(self.m.db.execute('SELECT count(*) FROM episodes').fetchone()[0],0)
    def test_another_session_cannot_assess_or_bind_owned_work(self):
        p=self.prompt();plan=self.plan(p);d=self.decision(plan['episode_id'])
        with self.assertRaises(InvalidRecord):write(self.m,'checkpoint','cross',{'prompt_ids':[p],'effect':'informational','reason':'Wrong session.'},session_id='other')
        with self.assertRaises(Conflict):codex_host.bind(self.m,'other',d['id'],'other-bind')
    def test_stale_plan_cannot_be_acknowledged_but_unrelated_notes_do_not_conflict(self):
        p=self.prompt();plan=self.plan(p);ep=plan['episode_id'];old=latest(self.m,ep,'work_plan')
        write(self.m,'record','progress-note',{'episode_id':ep,'kind':'note','expected_version':self.m.episode(ep)['version'],'actor':'fixture','payload':{'text':'Inspection continues.'}})
        self.checkpoint(p,effect='unchanged',episode_id=ep,plan_id=old['id'])
        payload={k:v for k,v in old.items() if k!='id'};payload['scope']+=' Preserve newlines.'
        write(self.m,'plan','concurrent-scope',{'episode_id':ep,'expected_version':self.m.episode(ep)['version'],'payload':payload,'actor':'fixture','evidence':self.evidence},session_id='s')
        with self.assertRaises(Conflict):self.checkpoint(p,effect='unchanged',episode_id=ep,plan_id=old['id'])
    def test_unknown_side_effect_is_never_closed_by_intent(self):
        p=self.prompt();rid=self.tool(complete=False,command='git push origin main');self.capture('Interrupt');self.checkpoint(p)
        self.assertEqual(self.issues(),{'execution_unconfirmed'})
        for resolution in ['unknown','completed']:
            write(self.m,'reconcile',resolution,{'receipt_id':rid,'resolution':resolution,'reason':'The marker file was inspected; the host completion status is separate.','evidence':self.evidence})
            self.assertEqual('execution_unconfirmed' in self.issues(),resolution=='unknown')
        self.assertEqual(self.m.db.execute("SELECT count(*) FROM host_receipts WHERE event_name='PreToolUse'").fetchone()[0],1)
    def test_a_background_agent_in_flight_does_not_block_the_main_conversation(self):
        p=self.prompt('Research this in the background.');self.checkpoint(p)
        self.tool('search',complete=False,command='git push origin main',agent_id='a1',agent_type='general-purpose')
        self.tool('write',agent_id='a1',agent_type='general-purpose')
        self.assertFalse(self.issues());self.assertEqual(hook(self.m,self.event('Stop')),{})
        self.assertEqual(codex_host.status(self.m)['unconfirmed_total'],1)
        self.tool('main',complete=False,command='git push origin main')
        self.assertEqual(self.issues(),{'execution_unconfirmed','activity_unassigned'})
    def test_a_background_notification_is_not_a_request_to_assess(self):
        p=self.prompt('Run the suite in the background.');self.checkpoint(p)
        for turn,text in enumerate(['<task-notification>\n<task-id>b1</task-id>\n<status>completed</status>\n</task-notification>',
                     'Another Claude session sent a message:\n<agent-message from="a1">\nThe report.\n</agent-message>'],2):
            self.prompt(text,turn_id=str(turn));self.tool(text[:8],turn_id=str(turn))
            self.assertEqual(self.issues(),{'activity_unassigned'})
        self.checkpoint(p)
        self.assertFalse(self.issues());self.assertEqual(hook(self.m,self.event('Stop',turn_id='3')),{})
        q=self.prompt('Now change the parser.',turn_id='4');self.tool('change',turn_id='4')
        self.assertIn('intent_unassessed',self.issues())
        with self.assertRaises(Conflict):self.checkpoint(p)
        self.checkpoint(q);self.assertFalse(self.issues())
    def test_explicit_unknown_execution_remains_visible_without_another_stop(self):
        p=self.prompt();rid=self.tool(complete=False,command='git push origin main');self.checkpoint(p)
        write(self.m,'reconcile','unknown-done',{'receipt_id':rid,'resolution':'unknown','reason':'The marker exists; final process completion is not established.','evidence':self.evidence})
        self.assertIn('execution_unconfirmed',self.issues())
        self.assertEqual(hook(self.m,self.event('Stop')),{})
    def test_redelivery_keeps_the_original_binding_and_prompt_snapshot(self):
        p=self.prompt();pre=self.tool(complete=False);plan=self.plan(p);self.decision(plan['episode_id'])
        self.assertEqual(self.prompt(),p)
        self.assertEqual(self.tool(complete=False),pre)
        self.assertIsNone(codex_host.read_receipt(self.m,pre)['decision_id'])
        with self.assertRaises(Conflict):self.prompt('Different content with the same host identity.')
    def test_switching_work_does_not_hide_the_previous_missing_outcome(self):
        p=self.prompt();first=self.plan(p);d1=self.decision(first['episode_id']);self.tool()
        p2=self.prompt('Inspect another independent case.',turn_id='2');second=self.plan(p2);d2=self.decision(second['episode_id']);self.tool('second');self.outcome(second['episode_id'],d2['id'])
        state=inspect(self.m,'s');self.assertIsNone(state['active']);self.assertEqual(state['open_decisions_total'],1)
        self.assertEqual(state['open_decisions'][0]['decision_id'],d1['id'])
    def test_prompt_privacy_and_paged_context(self):
        for i in range(12):self.prompt('secret '+str(i),turn_id=str(i));self.tool(str(i),turn_id=str(i))
        result=dispatch(self.m,'memory_get',{'view':'coverage','session_id':'s','limit':2,'max_chars':2500})
        self.assertEqual(result['pending_total'],12);self.assertEqual(len(result['pending_prompts']),2)
        self.assertTrue(result['more']);self.assertLessEqual(len(json.dumps(tool_result(result))),2500)
        self.assertNotIn('secret',json.dumps([dict(r) for r in self.m.db.execute('SELECT payload FROM host_receipts')]))
    def test_required_checklist_rejects_missing_and_duplicate_conditions(self):
        p=self.prompt();plan=self.plan(p);reviews.configure(self.m,self.root,'codex');snap,_=reviews.snapshot(self.m,plan['episode_id'],'intent')
        report={'verdict':'pass','summary':'The checked fixtures passed.','checks':[{'criterion':snap['checklist'][0]['id'],'evidence':'The fixture passed.','result':'met'}],'findings':[],'lesson_proposals':[]}
        with self.assertRaises(InvalidRecord):reviews.validate_report(report,snap['checklist'])
        report['checks']=[{'criterion':x['id'],'evidence':'The fixture result is available.','result':'met'} for x in snap['checklist']]
        reviews.validate_report(report,snap['checklist'])
        report['checks'].append(report['checks'][0])
        with self.assertRaises(InvalidRecord):reviews.validate_report(report,snap['checklist'])
        self.assertIn('Tagged Latin-1 succeeds.',[x['condition'] for x in snap['checklist']])
    def test_real_hook_lock_failure_survives_recovery_and_is_visible_over_http(self):
        p=self.prompt();ready=queue.Queue()
        def serve():
            with Viewer(self.m.path,'test-token') as server:
                ready.put(server);server.serve_forever()
        thread=threading.Thread(target=serve,daemon=True);thread.start();server=ready.get(timeout=3)
        url=f'http://127.0.0.1:{server.server_port}/test-token/api/'
        try:
            with urlopen(url+'health') as response:tag=response.headers['ETag']
            self.m.db.execute('BEGIN IMMEDIATE')
            event=self.event('PreToolUse',tool_name='Bash',tool_use_id='blocked')
            run=subprocess.run([sys.executable,'-m','memory_module.codex_host','--db',str(self.m.path)],input=json.dumps(event),text=True,capture_output=True,timeout=5)
            self.assertEqual(run.returncode,2);self.assertIn('capture failed',run.stderr)
            self.m.db.rollback()
            with urlopen(Request(url+'health',headers={'If-None-Match':tag})) as response:
                self.assertEqual(response.status,200);self.assertEqual(json.load(response)['capture']['status'],'failed')
            event=self.event('SessionStart',source='resume')
            run=subprocess.run([sys.executable,'-m','memory_module.codex_host','--db',str(self.m.path)],input=json.dumps(event),text=True,capture_output=True,timeout=5)
            self.assertEqual(run.returncode,0);self.assertIn('capture gap',run.stdout)
            state=inspect(self.m,'s');self.assertIn('capture_gap',self.issues())
            with self.assertRaises(InvalidRecord):self.checkpoint(p)
            self.checkpoint(p,gap_ids=state['capture_gaps']);self.assertNotIn('capture_gap',self.issues())
            with urlopen(url+'coverage?session_id=s') as response:self.assertEqual(json.load(response)['status'],'recorded')
        finally:server.shutdown();thread.join()


if __name__=='__main__':unittest.main()
