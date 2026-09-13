import json
import sqlite3
import tempfile
from pathlib import Path
import unittest
from memory_module import Memory, Hooks, InvalidRecord
from memory_module.mcp import dispatch
from memory_module.codex_host import initialize


class ImprovementTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.m=Memory.create(Path(self.temp.name)/'memory.sqlite','Quality',['Do useful work and preserve exceptions.'])
        self.ep=self.m.start('Decoder','Decode documented bytes','repair','Both fixtures pass',subject='code')
        self.s=self.m.source('contract','Contract','UTF-8','UTF-8','document',subject='code')['id']
        self.refs=[{'source_id':self.s,'reason':'Agreed contract.'}]
    def tearDown(self):self.m.close();self.temp.cleanup()
    def record(self,kind,payload,**kw):
        v=self.m.episode(self.ep['id'])['version']
        return self.m.record(self.ep['id'],kind,payload,expected_version=v,request_key=str(v),actor='evaluator',**kw)
    def attempt(self,assessment,previous=None):
        d=self.record('decision',{'decision':'Use UTF-8.','why':'Documented contract.','expected':'Fixtures pass.','uncertainty':'Only documented inputs are covered.','alternatives':['Reject malformed input.'],'reconsider_when':'Contract changes.','condition':'fixed','case_id':'decoder','model':'fixed'},evidence=self.refs,supersedes=previous)
        self.record('action',{'action':'test'},decision_id=d['id'])
        o=self.record('outcome',{'observed':'Cobalt UTF-8 fixtures were executed.','assessment':assessment,'assessment_reason':'Observed fixture results.','severity':'minor' if assessment=='bad' else 'none','attribution':'Decoder selection.','failure_type':'encoding','completion':'complete','context_characters':321,'research_calls':1,'repeated_research':0,'maintenance_ms':2},decision_id=d['id'],evidence=self.refs)
        return d,o
    def test_recall_includes_failure_and_recovery(self):
        a,failed=self.attempt('bad');b,passed=self.attempt('good',a['id'])
        found=self.m.context('cobalt',subject='code',budget=5000)
        self.assertTrue({failed['id'],passed['id']}<={r['id'] for r in found['records']})
        history=self.m.lineage(b['id'],limit=2)
        self.assertTrue(history['more']);self.assertEqual(history['records'][0]['id'],a['id'])
        self.assertEqual(history['total'],6)
    def test_outcome_inherits_stale_decision_evidence(self):
        d,o=self.attempt('good')
        self.m.source('contract','New contract','Latin-1','Latin-1','document',subject='code')
        self.assertEqual(self.m.read(o['id'])['status'],'needs_review')
        self.assertIn(o['id'],[r for s in self.m.signals()['signals'] for r in s['record_ids']])
    def test_failure_candidates_keep_success_denominator_and_require_review(self):
        a,_=self.attempt('bad');b,_=self.attempt('bad',a['id']);self.attempt('good',b['id'])
        signals=[s for s in self.m.signals(limit=100)['signals'] if s['type']=='repeated_failure']
        self.assertEqual(len(signals),1);self.assertEqual(signals[0]['assessed'],3);self.assertEqual(signals[0]['successful'],1)
        self.assertEqual(self.m.db.execute("SELECT count(*) FROM events WHERE kind='lesson'").fetchone()[0],0)
        lesson=self.record('lesson',{'pattern_type':'anti_pattern','when':'Input encoding is documented.','do':'Read that contract before selecting a decoder.','because':'Two attempts used the wrong encoding.','exceptions':'Explore alternatives when the contract is absent; retain errors for malformed input.'},evidence=self.refs)
        self.assertEqual(self.m.read(lesson['id'])['lesson_status'],'proposed')
    def test_recovery_can_use_observed_failure_without_false_revision_drift(self):
        first,failed=self.attempt('bad')
        choice=self.record('decision',{'decision':'Repair the encoding.','why':'The first fixture failed.','expected':'Both pass.','reconsider_when':'Contract changes.'},
            supersedes=first['id'],links=[{'event_id':failed['id'],'reason':'Observed failure motivating the repair.'}],evidence=self.refs)
        self.assertEqual(self.m.read(choice['id'])['status'],'recorded')

    def test_explicit_hook_context_is_bounded_and_subject_scoped(self):
        from memory_module.codex_host import capture
        initialize(self.m)
        self.m.source('research','Encoding research','UTF-8 except legacy.','UTF-8 except legacy.','document',subject='research')
        result=capture(self.m,{'session_id':'s','turn_id':'t','hook_event_name':'UserPromptSubmit','prompt':'[memory:research] encoding\nReview the parser.'})
        self.assertLessEqual(len(json.dumps(result,ensure_ascii=False,sort_keys=True,separators=(',',':'))),2500)
        context=result['hookSpecificOutput']['additionalContext']
        self.assertIn('UTF-8 except legacy',context)
        self.assertEqual(self.m.db.execute("SELECT count(*) FROM host_receipts WHERE event_name='ContextProvided'").fetchone()[0],1)
        self.assertNotIn('Decode documented bytes',context)

    def test_metrics_include_completion_effort_and_unacted_work(self):
        self.attempt('good')
        ep=self.m.start('Difficult work','Keep unresolved task','hard','External result verified',subject='research')
        self.m.record(ep['id'],'episode_status',{'status':'abandoned','reason':'External dependency unavailable.'},expected_version=0,request_key='abandoned',actor='evaluator')
        metric=self.m.metrics();self.assertEqual(metric['episodes']['abandoned'],1);self.assertEqual(metric['episodes']['without_decisions'],1)
        costs=metric['groups'][0]['reported_costs'];self.assertEqual(costs['context_characters']['total'],321)
        self.assertEqual(costs['tokens']['reported_decisions'],0)
    def test_codex_invalid_argument_does_not_crash_server(self):
        initialize(self.m)
        for data in [{'view':'record','id':True},{'query':'x','subject':'code','seen':True}]:
            with self.assertRaises(InvalidRecord):dispatch(self.m,'memory_get' if 'view' in data else 'memory_context',data)
    def test_input_audit_counts_full_requests_and_resume(self):
        from examples.context_audit import audit
        root=Path(self.temp.name)
        def usage(last,total):return json.dumps({'method':'thread/tokenUsage/updated','params':{'tokenUsage':{'last':{'inputTokens':last},'total':{'inputTokens':total}}}})+'\n'
        (root/'events.jsonl').write_text(usage(9000,9000)+usage(9999,18999))
        self.assertTrue(audit(root)['meets_target'])
        (root/'resume.jsonl').write_text(usage(10000,28999))
        self.assertFalse(audit(root)['meets_target'])
        (root/'resume.jsonl').write_text('')
        self.assertFalse(audit(root)['meets_target'])
    def test_task_quality_requires_actual_required_operations(self):
        from examples.codex_cases import execution_checks
        self.assertFalse(all(execution_checks('encoding','without_memory',[],0).values()))
        event={'method':'item/completed','params':{'item':{'type':'commandExecution','exitCode':0,'command':'cat parser.py','aggregatedOutput':'def decode_new_file(value):'}}}
        self.assertTrue(all(execution_checks('encoding','without_memory',[event],1).values()))
        self.assertFalse(all(execution_checks('limits','with_memory',[],0).values()))
    def test_capture_enforces_subject_and_evidence_without_changing_legacy_api(self):
        payload={'before':'Improves accuracy.','after':'Accuracy is unmeasured.','reason':'No study.','scope':'This release.'}
        with self.assertRaises(InvalidRecord):Hooks(self.m,'host').capture(trigger='user_correction',episode_id=self.ep['id'],payload=payload,expected_version=0,request_key='bad',evidence=self.refs)
        legacy=self.record('correction',payload,evidence=self.refs)
        self.assertEqual(self.m.read(legacy['id'])['kind'],'correction')


if __name__=='__main__':unittest.main()
