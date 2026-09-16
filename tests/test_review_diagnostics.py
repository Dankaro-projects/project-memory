"""Review contracts and real process termination; no provider calls in unit tests."""
import shutil
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from memory_module import Memory, InvalidRecord, Conflict, hosts, reviews
from memory_module.core import dumps
from memory_module.cli import main
from memory_module.install import setup
from memory_module.mcp import write
from memory_module.planning import latest
from memory_module.hosts import RunLog as ReviewLog
from memory_module.workspace import action


class ReviewDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name).resolve()
        info = setup(self.root,requirements=['Keep the tagged legacy exception.','The original research used PDF attachments.'])
        self.m = Memory(info['database'])
        self.work = action(self.m,'plan',{'title':'Repair the parser','objective':'Preserve both decoding paths.',
            'criterion':'UTF-8 rejects invalid bytes; tagged Latin-1 succeeds.', 'subject':'code',
            'payload':{'state':'ready','next_action':'Inspect the implementation.', 'scope':'Change decoding only; preserve the tagged exception.',
                       'autonomy':'act','reason':'The user requests the repair.'}},'fixture')
        self.ep = self.work['episode_id']
        reviews.configure(self.m,self.root,'codex')

    def tearDown(self):
        self.m.close()
        shutil.rmtree(self.temp.name, ignore_errors=True)

    def request(self):
        return reviews.request(self.m,self.ep,request_key='check',retry=True)

    def report(self, snapshot):
        return {'verdict':'pass','summary':'The fixture supports both paths.',
                'checks':[{'criterion':c['id'],'evidence':'parser.py preserves the stated condition.','result':'met'} for c in snapshot['checklist']],
                'constraint_checks':[{'constraint':c['id'],'applicability':'applies' if c['id']!='P002' else 'not_applicable',
                    'reason':'The task changes decoding only.', 'evidence':'The scope identifies the affected parser.',
                    'result':'met' if c['id']!='P002' else 'not_applicable'} for c in snapshot['constraints']],
                'findings':[], 'lesson_proposals':[]}

    def execute_child(self, body, timeout=3):
        run = self.request()
        folder = self.m.path.parent/'agent-runs'/run['id']
        answer = self.report(run['snapshot'])
        program = 'import json,time,pathlib,sys\nfolder=pathlib.Path('+repr(str(folder))+')\nreport='+repr(answer)+'\n'+body
        with patch.object(reviews,'command',return_value=[sys.executable,'-u','-c',program]):
            reviews.execute(self.m,run['id'],timeout=timeout)
        return reviews.read(self.m,run['id'])

    def test_scope_and_project_constraints_are_preserved_without_becoming_task_deliverables(self):
        snapshot,_ = reviews.snapshot(self.m,self.ep,'outcome')
        self.assertEqual(len(snapshot['checklist']),1)
        self.assertEqual(snapshot['requirements'],self.m.requirements)
        self.assertEqual([c['condition'] for c in snapshot['constraints'][1:]],self.m.requirements)
        self.assertTrue(snapshot['constraints'][0]['always_applies'])
        report = self.report(snapshot)
        reviews.validate_report(report,snapshot['checklist'],snapshot['constraints'])
        for change in ['missing','duplicate','unknown','dismiss_scope']:
            broken = json.loads(json.dumps(report))
            mapping = broken['constraint_checks']
            if change=='missing': mapping.pop()
            if change=='duplicate': mapping[-1]=dict(mapping[0])
            if change=='unknown': mapping[1].update(applicability='uncertain',result='unknown')
            if change=='dismiss_scope': mapping[0].update(applicability='not_applicable',result='not_applicable')
            with self.subTest(change=change), self.assertRaises(InvalidRecord):
                reviews.validate_report(broken,snapshot['checklist'],snapshot['constraints'])
        legacy = {k:v for k,v in report.items() if k!='constraint_checks'}
        reviews.validate_report(legacy,snapshot['checklist'])

    def test_manifest_preserves_complete_sources_and_large_records_on_demand(self):
        snapshot,_ = reviews.snapshot(self.m,self.ep,'outcome')
        body = 'Historical evidence. '*10000+'Except for the tagged legacy route.'
        snapshot['sources'][0]['body'] = body
        folder = self.root/'.memory/packet';folder.mkdir()
        packet = reviews.evidence_manifest(snapshot,folder)
        self.assertLess(len(json.dumps(packet)),10000)
        self.assertEqual(packet['checklist'],snapshot['checklist'])
        self.assertEqual(packet['constraints'],snapshot['constraints'])
        restored = json.loads(Path(packet['sources'][0]['file']).read_text())
        self.assertEqual(restored['body'],body)
        self.assertEqual(json.loads((folder/'context.json').read_text())['sources'],snapshot['sources'])

    def test_host_schema_enforces_exact_ids_without_changing_legacy_schema(self):
        snapshot,_ = reviews.snapshot(self.m,self.ep,'outcome')
        schema = reviews.report_schema(snapshot)
        checks = schema['properties']['checks']
        self.assertEqual(checks['items']['properties']['criterion']['enum'],['C001'])
        self.assertEqual(checks['minItems'],checks['maxItems'])
        self.assertEqual(schema['properties']['constraint_checks']['items']['properties']['constraint']['enum'],['S001','P001','P002'])
        self.assertNotIn('enum',reviews.REPORT_SCHEMA['properties']['checks']['items']['properties']['criterion'])

    def test_report_budget_scales_to_many_constraints_without_removing_checks(self):
        snapshot,_ = reviews.snapshot(self.m,self.ep,'outcome')
        snapshot['checklist'] = [{'id':f'C{i:03}', 'condition':'Preserve the tagged exception.'} for i in range(1,7)]
        snapshot['constraints'] = [{'id':f'P{i:03}', 'condition':'Preserve the stated project constraint.'} for i in range(1,26)]
        schema = reviews.report_schema(snapshot)
        checks = schema['properties']['checks']
        constraints = schema['properties']['constraint_checks']
        self.assertEqual(checks['minItems'],6)
        self.assertEqual(constraints['minItems'],25)
        limit = checks['items']['properties']['evidence']['maxLength']
        report = self.report(snapshot)
        report['summary'] = 's' * schema['properties']['summary']['maxLength']
        for item in report['checks']:
            item['evidence'] = 'e' * limit
        for item in report['constraint_checks']:
            for field in ('reason','evidence'):
                self.assertEqual(constraints['items']['properties'][field]['maxLength'],limit)
                item[field] = 'e' * limit
        report['findings'] = ['f' * 1900]
        self.assertLessEqual(len(dumps(report)),reviews.REPORT_MAX_CHARACTERS)
        reviews.validate_report(report,snapshot['checklist'],snapshot['constraints'])
        self.assertNotIn('maxLength',reviews.REPORT_SCHEMA['properties']['checks']['items']['properties']['evidence'])

    def test_report_limit_counts_serialized_characters_and_preserves_the_boundary(self):
        snapshot,_ = reviews.snapshot(self.m,self.ep,'outcome')
        report = self.report(snapshot)
        report['findings'] = ['f'*5000, 'é'*5000, '\\"'*1000]
        report['findings'].append('x'*(reviews.REPORT_MAX_CHARACTERS-len(dumps(report))-3))
        self.assertEqual(len(dumps(report)),reviews.REPORT_MAX_CHARACTERS)
        reviews.validate_report(report,snapshot['checklist'],snapshot['constraints'])
        report['findings'][-1] += 'x'
        with self.assertRaisesRegex(InvalidRecord,'exceeds 16,000 characters'):
            reviews.validate_report(report,snapshot['checklist'],snapshot['constraints'])

    def test_every_review_role_receives_the_complete_report_budget(self):
        for role in reviews.ROLES:
            run = reviews.request(self.m,self.ep,role,request_key='budget:'+role)
            with patch.object(reviews,'command',return_value=[sys.executable,'-c','pass']) as command:
                reviews.execute(self.m,run['id'],timeout=3)
            prompt = command.call_args.args[3]
            self.assertIn('16,000 characters',prompt)
            self.assertIn('12,000 characters',prompt)
            self.assertIn('shorten wording, not coverage',prompt)

    def test_progress_preserves_scope_evidence_and_outcome_signature(self):
        _,signature = reviews.snapshot(self.m,self.ep,'outcome')
        old = self.m.read(self.work['id'])
        data = {'episode_id':self.ep,'expected_version':self.m.episode(self.ep)['version'], 'actor':'assistant',
                'payload':{'state':'review','next_action':'Inspect the pending review.','reason':'Implementation is available for review.'}}
        result = write(self.m,'progress','progress',data)
        self.assertEqual(write(self.m,'progress','progress',data),result)
        current = self.m.read(result['id'])
        self.assertEqual(current['payload']['scope'],old['payload']['scope'])
        self.assertEqual(current['evidence'],old['evidence'])
        self.assertEqual(reviews.snapshot(self.m,self.ep,'outcome')[1],signature)
        with self.assertRaises(InvalidRecord):
            write(self.m,'progress','bad-progress',{**data,'payload':{**data['payload'],'scope':'The review is pending.'}})
        with self.assertRaises(Conflict):
            write(self.m,'progress','stale-progress',data)
        with self.assertRaises(InvalidRecord):
            write(self.m,'progress','false-done',{**data,'expected_version':result['version'], 'payload':{'state':'done','reason':'Assume completion.'}})

    def test_wait_legacy_alias_returns_at_its_deadline_without_cancelling(self):
        run = self.request()
        started = time.monotonic()
        result = subprocess.run([sys.executable,'-m','memory_module.cli','review','--db',str(self.m.path),
                                 '--wait',run['id'],'--max-seconds','1'],capture_output=True,text=True,timeout=4)
        elapsed = time.monotonic()-started
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue(json.loads(result.stdout)['wait_expired'])
        self.assertGreaterEqual(elapsed,1)
        self.assertLess(elapsed,3)
        unchanged = reviews.read(self.m,run['id'])
        self.assertEqual(unchanged['state'],'queued')
        self.assertEqual(unchanged['snapshot']['execution_limit_seconds'],300)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(['review','--db',str(self.m.path),'--wait',run['id'],'--wait-seconds','1','--max-seconds','1']),1)

    def test_successful_child_report_and_usage_are_recorded(self):
        result = self.execute_child("(folder/'answer.json').write_text(json.dumps(report))\nprint(json.dumps({'type':'turn.completed','usage':{'input_tokens':75,'output_tokens':20}}))\n")
        self.assertEqual(result['state'],'pass')
        self.assertEqual(result['metrics']['provider_usage'][0]['input_tokens'],75)
        self.assertEqual(result['metrics']['termination_reason'],'completed')
        self.assertTrue(result['metrics']['report_valid'])

    def test_active_timeout_retains_activity_usage_and_report_without_approval(self):
        result = self.execute_child("print(json.dumps({'type':'item.started','item':{'type':'command_execution'}}))\nprint(json.dumps({'type':'item.completed','item':{'type':'command_execution','exit_code':1}}))\n(folder/'answer.json').write_text(json.dumps(report))\nprint(json.dumps({'type':'turn.completed','usage':{'input_tokens':90}}))\ntime.sleep(10)\n",timeout=.4)
        self.assertEqual(result['state'],'timed_out')
        metrics = result['metrics']
        self.assertEqual(metrics['termination_reason'],'execution_deadline')
        self.assertEqual(metrics['completed_inspections'],1)
        self.assertEqual(metrics['failed_inspections'],1)
        self.assertEqual(metrics['phase'],'report_received')
        self.assertIsNotNone(metrics['last_activity_at'])
        self.assertEqual(metrics['provider_usage'][0]['input_tokens'],90)
        self.assertIsNotNone(result['report'])
        self.assertEqual(reviews.current(self.m,self.ep)['state'],'timed_out')
        self.assertNotIn('command',json.dumps(metrics))

    def test_silent_timeout_does_not_invent_transport_failure_or_zero_usage(self):
        result = self.execute_child('time.sleep(10)\n',timeout=.3)
        metrics = result['metrics']
        self.assertEqual(result['state'],'timed_out')
        self.assertIsNone(metrics['provider_usage'])
        self.assertIsNone(metrics['last_activity_at'])
        self.assertEqual(metrics['host_error_events'],0)
        self.assertEqual(metrics['phase'],'starting')

    def test_invalid_report_and_host_failure_are_distinct(self):
        result = self.execute_child("(folder/'answer.json').write_text('{}')\n")
        self.assertEqual(result['state'],'failed')
        self.assertEqual(result['metrics']['termination_reason'],'invalid_report')
        self.assertIsNone(result['report'])
        run = reviews.request(self.m,self.ep,request_key='second',retry=True)
        with patch.object(reviews,'command',return_value=[sys.executable,'-u','-c',"import json;print(json.dumps({'type':'turn.failed'}));raise SystemExit(2)"]):
            reviews.execute(self.m,run['id'],timeout=2)
        result = reviews.read(self.m,run['id'])
        self.assertEqual(result['metrics']['termination_reason'],'host_exit')
        self.assertEqual(result['metrics']['host_error_events'],1)

    def test_host_unavailability_is_a_distinct_state_and_marks_the_host(self):
        result = self.execute_child("print(json.dumps({'type':'error','message':'You have hit your usage limit. Try again in 5 minutes.'}))\nraise SystemExit(1)\n")
        self.assertEqual(result['state'],'host_unavailable')
        self.assertEqual(result['metrics']['termination_reason'],'host_unavailable')
        self.assertIn('usage limit',result['error'])
        availability = hosts.availability(self.m,'codex')
        self.assertFalse(availability['available'])
        self.assertIsNotNone(availability['until'])
        with self.assertRaisesRegex(InvalidRecord,'No configured agent host is available'):
            reviews.request(self.m,self.ep,request_key='during-limit',retry=True)
        hosts.mark_available(self.m,'codex')
        run = reviews.request(self.m,self.ep,request_key='after-limit',retry=True)
        folder = self.m.path.parent/'agent-runs'/run['id']
        program = 'import json,pathlib\nfolder=pathlib.Path('+repr(str(folder))+')\n(folder/"answer.json").write_text(json.dumps('+repr(self.report(run['snapshot']))+'))\n'
        with patch.object(reviews,'command',return_value=[sys.executable,'-c',program]):
            reviews.execute(self.m,run['id'],timeout=3)
        self.assertEqual(reviews.read(self.m,run['id'])['state'],'pass')
        self.assertTrue(hosts.availability(self.m,'codex')['available'])

    def test_cancellation_terminates_child_and_retains_its_progress(self):
        code = "sys.path.insert(0,"+repr(str(Path(__file__).resolve().parents[1]))+")\nfrom memory_module import Memory,reviews\nprint(json.dumps({'type':'thread.started'}))\nwith Memory("+repr(str(self.m.path))+") as m: reviews.cancel(m,folder.name)\ntime.sleep(10)\n"
        result = self.execute_child(code)
        self.assertEqual(result['state'],'cancelled')
        self.assertEqual(result['metrics']['termination_reason'],'cancelled')
        self.assertEqual(result['metrics']['last_event'],'thread.started')
        self.assertEqual(self.m.db.execute("SELECT count(*) FROM host_receipts WHERE event_name='ReviewFinished'").fetchone()[0],1)

    def test_incremental_codex_and_claude_events_handle_split_lines(self):
        folder = self.root/'.memory/logs';folder.mkdir()
        path = folder/'output.jsonl'
        path.write_bytes(b'{"type":"item.star')
        log = ReviewLog(folder)
        self.assertEqual(log.read()['unparsed_events'],0)
        with path.open('ab') as stream: stream.write(b'ted","item":{"type":"command_execution"}}\n')
        self.assertEqual(log.read()['phase'],'inspecting')
        events = [{'type':'assistant','message':{'model':'fixture-model','content':[{'type':'tool_use'}]}},
                  {'type':'user','message':{'content':[{'type':'tool_result','is_error':True}]}},
                  {'type':'result','structured_output':{},'usage':{'input_tokens':40}}]
        with path.open('a') as stream: stream.write('\n'.join(json.dumps(e) for e in events))
        metrics = log.read(final=True)
        self.assertEqual(metrics['completed_inspections'],1)
        self.assertEqual(metrics['failed_inspections'],1)
        self.assertEqual(metrics['provider_usage']['input_tokens'],40)
        self.assertEqual(metrics['model'],'fixture-model')
        self.assertIsNotNone(log.result)


class CheckInstructionTests(unittest.TestCase):
    """Every agent check composes its instructions, keeps the exact text and records it in its metrics."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name).resolve()
        info = setup(self.root,requirements=['Keep the tagged legacy exception.'])
        self.m = Memory(info['database'])
        self.ep = action(self.m,'plan',{'title':'Repair the parser','objective':'Preserve both decoding paths.',
            'criterion':'UTF-8 rejects invalid bytes; tagged Latin-1 succeeds.', 'subject':'code',
            'payload':{'state':'ready','next_action':'Inspect the implementation.',
                       'scope':'Change decoding only; preserve the tagged exception.',
                       'autonomy':'act','reason':'The user requests the repair.'}},'fixture')['episode_id']
        reviews.configure(self.m,self.root,'codex')
        self.counter = 0
        source = self.m.source('user-lessons','Lessons','User instruction','Review the parser work.','user',subject='code')
        self.evidence = [{'source_id':source['id'],'reason':'The user asks for the lesson.'}]
        self.learning = self.m.start('Lessons','Collect parser lessons.','learning','Lessons are reviewed.',subject='code')['id']

    def tearDown(self):
        self.m.close()
        shutil.rmtree(self.temp.name, ignore_errors=True)

    def report(self, snapshot):
        """A passing report for the checklist and the constraints of a snapshot."""
        return {'verdict':'pass','summary':'The fixture supports both paths.',
                'checks':[{'criterion':c['id'],'evidence':'parser.py preserves the stated condition.','result':'met'}
                          for c in snapshot['checklist']],
                'constraint_checks':[{'constraint':c['id'],'applicability':'applies',
                    'reason':'The task changes decoding only.','evidence':'The scope identifies the parser.',
                    'result':'met'} for c in snapshot['constraints']],
                'findings':[], 'lesson_proposals':[]}

    def key(self):
        self.counter += 1
        return 'lesson-key-'+str(self.counter)

    def rule(self, *, do='Read the recorded conditions first.', **triggers):
        """Record a lesson and accept it, so that it becomes a rule in force."""
        version = self.m.episode(self.learning)['version']
        payload = {'when':'Checking the parser.','do':do,'because':'Earlier checks missed a condition.',
                   'exceptions':'Documentation changes.', **triggers}
        lesson = self.m.record(self.learning,'lesson',payload,expected_version=version,request_key=self.key(),
                               actor='assistant',evidence=self.evidence)['id']
        self.m.record(self.learning,'lesson_review',{'lesson_id':lesson,'status':'accepted','reason':'The user reviewed it.'},
                      expected_version=self.m.episode(self.learning)['version'],request_key=self.key(),actor='workspace-user',
                      evidence=self.evidence,links=[{'event_id':lesson,'reason':'This review assesses the lesson.'}])
        return lesson

    def run_check(self, key, role='outcome', timeout=3):
        """Execute one check with a fake child process that returns a passing report."""
        run = reviews.request(self.m,self.ep,role,request_key=key,retry=True)
        folder = self.m.path.parent/'agent-runs'/run['id']
        answer = self.report(run['snapshot'])
        program = ('import json,pathlib\nfolder=pathlib.Path('+repr(str(folder))+')\n'
                   '(folder/"answer.json").write_text(json.dumps('+repr(answer)+'))\n'
                   'print(json.dumps({"type":"turn.completed","usage":{"input_tokens":10}}))\n')
        with patch.object(reviews,'command',return_value=[sys.executable,'-u','-c',program]):
            reviews.execute(self.m,run['id'],timeout=timeout)
        return reviews.read(self.m,run['id'])

    def test_a_check_composes_the_reviewer_instructions_and_keeps_the_exact_text(self):
        from memory_module import guards
        rule = self.rule(roles=['reviewer'],do='Read the recorded conditions first.')
        worker = self.rule(roles=['worker'],do='Read the worker checklist.')
        result = self.run_check('check-one')
        self.assertEqual(result['state'],'pass',result['error'])
        metrics = result['metrics']
        self.assertEqual(metrics['instruction_role'],'reviewer')
        self.assertEqual(metrics['instruction_source'],'instructions:reviewer')
        self.assertEqual(metrics['rule_ids'],[rule])
        self.assertEqual(metrics['rules_omitted'],[])
        self.assertEqual(metrics['instruction_base_source'],'agents/reviewer.md')
        stored = self.m.read(metrics['instruction_source_id'],detail=True)
        self.assertEqual(stored['source_key'],'instructions:reviewer')
        composed = guards.instructions(self.m,'reviewer',paths=[],text='')
        self.assertEqual(stored['body'],composed['text'])
        self.assertIn(guards.RULES_HEADING,stored['body'])
        self.assertIn('Do Read the recorded conditions first.',stored['body'])
        self.assertNotIn('Read the worker checklist.',stored['body'])
        self.assertEqual(metrics['instruction_characters'],len(stored['body']))
        prompt = (self.m.path.parent/'agent-runs'/result['id']/'prompt.txt').read_text()
        self.assertIn('Do Read the recorded conditions first.',prompt)
        self.assertIn(guards.shipped_base('reviewer').splitlines()[0][:60],prompt)
        second = self.run_check('check-two',role='intent')
        self.assertEqual(second['metrics']['instruction_source_id'],metrics['instruction_source_id'])
        self.assertEqual(self.m.db.execute("SELECT count(*) FROM sources WHERE source_key='instructions:reviewer'").fetchone()[0],1)

    def test_a_new_rule_writes_a_new_version_and_omissions_are_reported(self):
        from memory_module import guards
        rules = [self.rule(roles=['reviewer'],do=f'Read condition {index}.'+' Keep every recorded exception.'*3)
                 for index in range(9)]
        result = self.run_check('check-many')
        metrics = result['metrics']
        self.assertTrue(metrics['rule_ids'])
        self.assertLess(len(metrics['rule_ids']),len(rules))
        self.assertEqual(sorted(metrics['rule_ids']+[item['lesson_id'] for item in metrics['rules_omitted']]),sorted(rules))
        reasons = ' '.join(item['reason'] for item in metrics['rules_omitted'])
        self.assertTrue(str(guards.MAX_ACTIVE_RULES) in reasons or 'budget' in reasons)
        self.assertEqual(metrics['rules_accepted'],len(rules))
        stored = self.m.read(metrics['instruction_source_id'],detail=True)
        self.assertEqual(stored['version'],1)
        self.rule(roles=['reviewer'],do='Read the newest condition first.')
        again = self.run_check('check-after')
        self.assertEqual(self.m.read(again['metrics']['instruction_source_id'],detail=True)['version'],2)


if __name__=='__main__': unittest.main()
