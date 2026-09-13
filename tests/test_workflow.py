import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from memory_module import Memory, Hooks, CaptureFailure, InvalidRecord, Conflict, migrate, dumps
from memory_module.core import SCHEMA


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = '2026-09-12T12:00:00+00:00'
        self.m = Memory.create(self.root/'memory.sqlite', 'Checks', ['Professional plain English.'], clock=lambda:self.now)
        self.ep = self.m.start('Repair parser', 'Read all records', 'repair', 'Unicode input passes', subject='code')
        self.h = Hooks(self.m, 'host')
        self.sid = self.m.source('test-output', 'Test result', 'Test output', 'Failed on Unicode', 'tool', subject='code')['id']
        self.refs = [{'source_id':self.sid, 'reason':'Observed output.'}]
        self.n = 0

    def tearDown(self):
        self.m.close(); self.temp.cleanup()

    def record(self, kind, payload, **kwargs):
        self.n += 1
        return self.m.record(self.ep['id'], kind, payload, expected_version=self.m.episode(self.ep['id'])['version'],
                             request_key='record-'+str(self.n), actor='reviewer', **kwargs)

    def before(self, **changes):
        args = dict(episode_id=self.ep['id'],operation='edit',expected_version=0,request_key='op',
                    evidence=self.refs,decision={'decision':'Fix Unicode parsing','why':'Test failed',
                    'expected':'Unicode test passes','reconsider_when':'Regression fails',
                    'review_after':'2026-09-13T12:00:00Z','follow_up_owner':'test-host'})
        args.update(changes); return args

    def outcome(self, ticket, assessment='good'):
        return self.record('outcome', {'observed':'Test completed','assessment':assessment,
            'assessment_reason':'Fixture comparison','severity':'none','attribution':'Not established'},
            decision_id=ticket['decision_id'], evidence=self.refs)

    def test_subject_context_excludes_other_subjects(self):
        writing = self.m.start('Parser prose', 'Describe Unicode parser', 'copy', 'Plain language', subject='writing')
        self.m.record(writing['id'],'note',{'text':'Parser prose guidance'},expected_version=0,request_key='writing',actor='writer')
        self.record('note',{'text':'Parser code guidance'})
        packet = self.m.context('parser',episode_id=self.ep['id'],budget=5000)
        self.assertFalse(any(r.get('subject')=='writing' for r in packet['records']))
        self.assertTrue(any(r.get('subject')=='code' for r in packet['records']))
        with self.assertRaises(InvalidRecord):
            self.m.context('parser',episode_id=self.ep['id'],subject='writing')

    def test_general_sources_are_opt_in(self):
        source=self.m.source('common','Unicode policy','Unicode policy','shared','user')['id']
        self.assertNotIn(source,[r['id'] for r in self.m.search('Unicode',subject='code')['records']])
        self.assertIn(source,[r['id'] for r in self.m.search('Unicode',subject='code',include_general=True)['records']])

    def test_review_requires_code_scope_revision_and_evidence(self):
        payload={'target':'parser.py','revision':'sha256:fixture','summary':'Missing case','findings':[
            {'location':'parse','issue':'Unicode not handled','severity':'major'}]}
        with self.assertRaises(InvalidRecord): self.record('review',payload)
        review=self.h.capture(trigger='review_completed',episode_id=self.ep['id'],payload=payload,
                             expected_version=0,request_key='review',evidence=self.refs)
        self.assertEqual(self.m.read(review['id'])['kind'],'review')
        with self.assertRaises(InvalidRecord): self.record('review',{**payload,'findings':[{'issue':'x'}]},evidence=self.refs)
        self.ep=self.m.start('Copy','Copy','writing','Clear',subject='writing')
        with self.assertRaises(InvalidRecord): self.record('review',payload,evidence=self.refs)

    def test_research_is_separate_from_code(self):
        with self.assertRaises(InvalidRecord):
            self.record('research',{'question':'q','findings':'f','gaps':'g'},evidence=self.refs)

    def test_unknown_hook_rejected(self):
        with self.assertRaises(InvalidRecord):
            self.h.capture(trigger='some_event',episode_id=self.ep['id'],payload={},expected_version=0,request_key='x',evidence=[])

    def test_callback_sees_persisted_decision_and_action(self):
        def operation():
            with Memory(self.m.path) as other:
                self.assertEqual(other.db.execute('SELECT kind FROM events ORDER BY seq').fetchall()[0][0],'decision')
                self.assertEqual(other.episode(self.ep['id'])['version'],2)
            return 42
        run=self.h.run(operation,summarize=lambda result:f'Returned {result}',**self.before())
        self.assertEqual(run['result'],42)
        self.assertEqual(self.m.metrics()['groups'][0]['assessed'],0)
        self.assertEqual(self.m.pending()['decisions'][0]['state'],'consequence_pending')

    def test_missing_schedule_prevents_callback(self):
        called=[]; args=self.before();args['decision'].pop('follow_up_owner')
        with self.assertRaises(InvalidRecord): self.h.run(lambda:called.append(True),**args)
        self.assertEqual(called,[])
        self.assertEqual(self.m.episode(self.ep['id'])['version'],0)

    def test_conflict_does_not_run_callback(self):
        self.record('note',{'text':'Another worker changed the state'})
        called=[]
        with self.assertRaises(Conflict): self.h.run(lambda:called.append(True),**self.before())
        self.assertEqual(called,[])
        self.assertEqual(self.m.inspect(self.ep['id'])['episode']['version'],1)

    def test_storage_failure_before_action_blocks_callback(self):
        self.m.db.execute("CREATE TRIGGER deny_action BEFORE INSERT ON events WHEN NEW.kind='action' BEGIN SELECT RAISE(ABORT,'disk failure'); END")
        called=[]
        with self.assertRaises(sqlite3.IntegrityError):self.h.run(lambda:called.append(True),**self.before())
        self.assertEqual(called,[])
        self.assertEqual(self.m.pending()['decisions'],[])
        self.assertEqual(self.m.episode(self.ep['id'])['version'],0)

    def test_callback_exception_is_recorded_without_success_claim(self):
        def fail(): raise RuntimeError('sensitive exception message')
        with self.assertRaises(RuntimeError):self.h.run(fail,**self.before())
        event=self.m.db.execute("SELECT payload FROM events WHERE kind='action_result'").fetchone()[0]
        self.assertEqual(json.loads(event)['execution_status'],'failed')
        self.assertNotIn('sensitive',event)
        self.assertEqual(self.m.metrics()['groups'][0]['assessed'],0)

    def test_capture_failure_after_execution_prevents_automatic_retry(self):
        called=[]
        self.m.db.execute("CREATE TRIGGER deny_result BEFORE INSERT ON events WHEN NEW.kind='action_result' BEGIN SELECT RAISE(ABORT,'disk failure'); END")
        with self.assertRaises(CaptureFailure):self.h.run(lambda:called.append(True),**self.before())
        self.assertEqual(self.m.pending()['decisions'][0]['state'],'execution_unconfirmed')
        with self.assertRaises(Conflict):self.h.run(lambda:called.append(True),**self.before())
        self.assertEqual(called,[True])

    def test_interrupted_prepared_action_is_never_rerun(self):
        self.h.before_action(**self.before())
        with self.assertRaises(Conflict):self.h.before_action(**self.before())
        self.assertEqual(self.h.resume()['pending']['decisions'][0]['state'],'execution_unconfirmed')

    def test_result_retry_deduplicates_and_rejects_changed_content(self):
        ticket=self.h.before_action(**self.before())
        args=dict(ticket=ticket,execution_status='completed',summary='Returned',expected_version=2)
        one=self.h.after_action(**args);two=self.h.after_action(**args)
        self.assertEqual(one['id'],two['id']);self.assertTrue(two['duplicate'])
        with self.assertRaises(Conflict):self.h.after_action(**{**args,'summary':'Different'})

    def test_wrong_ticket_rejected(self):
        ticket=self.h.before_action(**self.before());ticket['request_key']='forged'
        with self.assertRaises(InvalidRecord):
            self.h.after_action(ticket=ticket,execution_status='completed',summary='x',expected_version=2)

    def test_concurrent_change_after_callback_requires_reconciliation(self):
        def operation(): self.record('note',{'text':'Concurrent update'})
        with self.assertRaises(CaptureFailure):self.h.run(operation,**self.before())
        self.assertEqual(self.m.pending()['decisions'][0]['state'],'execution_unconfirmed')

    def test_defer_requires_owner_date_and_keeps_history(self):
        ticket=self.h.before_action(**self.before())
        self.now='2026-09-14T00:00:00Z';self.assertIn(ticket['decision_id'],self.m.due()['decisions'])
        self.record('follow_up',{'review_after':'2026-09-16T00:00:00Z','owner':'reviewer','reason':'Result not available yet'},decision_id=ticket['decision_id'])
        self.assertNotIn(ticket['decision_id'],self.m.due()['decisions'])
        self.now='2026-09-17T00:00:00Z';self.assertEqual(self.m.due()['checks'][0]['owner'],'reviewer')
        self.outcome(ticket);self.assertEqual(self.m.pending()['decisions'],[])

    def test_settled_blocks_new_work_but_can_reopen(self):
        ticket=self.h.before_action(**self.before())
        with self.assertRaises(InvalidRecord):self.record('episode_status',{'status':'settled','reason':'Done'})
        self.outcome(ticket)
        self.record('episode_status',{'status':'settled','reason':'Fixture checked'})
        with self.assertRaises(InvalidRecord):self.record('note',{'text':'New work'})
        self.record('episode_status',{'status':'reopened','reason':'New requirement'})
        self.record('note',{'text':'Additional work'})
        self.assertEqual(self.m.episode(self.ep['id'])['status'],'reopened')

    def test_links_are_explicit_unique_and_propagate_stale_evidence(self):
        first=self.record('note',{'text':'Observed failure'},evidence=self.refs)
        second=self.record('note',{'text':'Investigate this'},links=[{'event_id':first['id'],'reason':'Observed failure motivated investigation'}])
        with self.assertRaises(InvalidRecord):self.record('note',{'text':'bad'},links=[{'event_id':first['id'],'reason':' ' }])
        with self.assertRaises(sqlite3.IntegrityError):self.m.db.execute('INSERT INTO event_links VALUES (?,?,?)',(second['id'],first['id'],'duplicate'))
        with self.assertRaises(sqlite3.IntegrityError):self.m.db.execute('INSERT INTO event_links VALUES (?,?,?)',(first['id'],second['id'],'cycle'))
        self.m.source('test-output','New run','Passed','Passed on Unicode','tool',subject='code')
        self.assertEqual(self.m.read(second['id'])['status'],'needs_review')
        self.assertIn(first['id'],self.m.history())

    def test_revised_prior_event_flags_dependent_for_review(self):
        first=self.record('decision',{'decision':'Try A','why':'Evidence','expected':'x','reconsider_when':'y'})
        dependent=self.record('note',{'text':'Depends on A'},links=[{'event_id':first['id'],'reason':'Uses this choice'}])
        self.record('decision',{'decision':'Try B','why':'New evidence','expected':'x','reconsider_when':'y'},supersedes=first['id'])
        self.assertEqual(self.m.read(dependent['id'])['status'],'needs_review')

    def test_links_cannot_reference_another_project(self):
        with Memory.create(self.root/'other.sqlite','other',['rule']) as other:
            ep=other.start('x','x','x','x')
            ev=other.record(ep['id'],'note',{'text':'other'},expected_version=0,request_key='x',actor='x')
        with self.assertRaises(InvalidRecord):self.record('note',{'text':'bad'},links=[{'event_id':ev['id'],'reason':'other'}])

    def test_lesson_review_requires_evidence_and_records_status(self):
        lesson=self.record('lesson',{'when':'Reading Unicode','do':'Specify UTF-8','because':'Test failed','exceptions':'Other documented encoding'},evidence=self.refs)
        self.assertEqual(self.m.read(lesson['id'])['lesson_status'],'proposed')
        payload={'lesson_id':lesson['id'],'status':'accepted','reason':'Checked fixture and exception'}
        with self.assertRaises(InvalidRecord):self.record('lesson_review',payload,evidence=self.refs)
        self.record('lesson_review',payload,evidence=self.refs,links=[{'event_id':lesson['id'],'reason':'Lesson checked'}])
        self.assertEqual(self.m.read(lesson['id'])['status'],'accepted')
        self.m.source('test-output','New result','Different','Different evidence','tool',subject='code')
        self.assertEqual(self.m.read(lesson['id'])['status'],'needs_review')

    def test_wrapper_count_includes_surrounding_text(self):
        prefix='host wrapper: ';suffix=' end'
        packet=self.m.context('Unicode',subject='code',budget=2500,count_tokens=lambda x:len(x.encode()),wrapper_prefix=prefix,wrapper_suffix=suffix)
        self.assertEqual(packet['used'],len((prefix+dumps(packet)+suffix).encode()))
        self.assertLessEqual(packet['used'],2500)

    def test_html_scope_limits_and_injection_escaping(self):
        self.record('note',{'text':'</script><script>window.injected=true</script>'})
        out=self.root/'snapshot.html';result=self.m.export_html(out,episode_id=self.ep['id'])
        html=out.read_text();self.assertNotIn('<script>window.injected',html)
        self.assertIn('\\u003c/script',html);self.assertTrue(result['snapshot'])
        with self.assertRaises(Conflict):self.m.export_html(out)
        with self.assertRaises(InvalidRecord):self.m.export_html(self.root/'too-small.html',max_records=1)
        self.assertFalse((self.root/'too-small.html').exists())
        with self.assertRaises(InvalidRecord):self.m.export_html(self.root/'too-big.html',max_bytes=1024)

    def test_html_source_body_is_opt_in(self):
        for included in [False,True]:
            out=self.root/('bodies-'+str(included)+'.html');self.m.export_html(out,include_bodies=included)
            text=out.read_text().split('<script id="memory-data" type="application/json">')[1].split('</script>')[0]
            data=json.loads(text);source=next(r for r in data['records'] if r['id']==self.sid)
            self.assertEqual('body' in source['detail'],included)

    def test_sql_rejects_invalid_json_null_id_and_subject_change(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.m.db.execute("INSERT INTO events VALUES ('e',?,1,'note','bad','today','a',NULL,NULL,'k','h','code')",(self.ep['id'],))
        with self.assertRaises(sqlite3.IntegrityError):self.m.db.execute("UPDATE episodes SET subject='writing'")
        with self.assertRaises(InvalidRecord):self.m.source('test-output','x','x','x','tool',subject='writing')

    def test_migration_preserves_old_file_history_and_duplicate_key(self):
        old=self.root/'version1.sqlite';db=sqlite3.connect(old);db.executescript(SCHEMA)
        db.executemany('INSERT INTO settings VALUES (?,?)',[('project','old'),('requirements','["rule"]')])
        db.execute("INSERT INTO episodes VALUES ('ep','t','o','task','criterion','2026-09-12T00:00:00+00:00',0)")
        from memory_module.core import _digest
        payload={'text':'old event'}
        signature=_digest(dumps(['ep','note',payload,'old-actor',[],None,None]))
        db.execute("INSERT INTO events VALUES ('old-event','ep',1,'note',?,'2026-09-12T00:00:00+00:00','old-actor',NULL,NULL,'old-key',?)",(dumps(payload),signature))
        db.execute("INSERT INTO search_index VALUES ('old-event','note','Old note','old event')")
        db.execute("UPDATE episodes SET version=1 WHERE id='ep'")
        db.execute('PRAGMA user_version=1');db.commit();db.close()
        original=old.read_bytes();target=self.root/'migrated.sqlite';migrate(old,target)
        self.assertEqual(old.read_bytes(),original)
        with Memory(target) as migrated:
            self.assertEqual(migrated.episode('ep')['subject'],'general')
            self.assertEqual(migrated.read('old-event')['payload'],{'text':'old event'})
            retry=migrated.record('ep','note',{'text':'old event'},expected_version=0,request_key='old-key',actor='old-actor')
            self.assertTrue(retry['duplicate'])
            self.assertIn('old-event',[r['id'] for r in migrated.search('old')['records']])
            migrated.record('ep','note',{'text':'new'},expected_version=1,request_key='x',actor='x')
        with self.assertRaises(Conflict):migrate(old,target)
        with self.assertRaises(InvalidRecord):Memory(old)

    def test_cli_html_and_hooks_work_without_installation(self):
        cmd=[sys.executable,'-m','memory_module','--db',str(self.m.path)]
        result=subprocess.run(cmd+['hook'],input=dumps({'actor':'host','event':'resume'}),text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr);self.assertIn('pending',json.loads(result.stdout))
        result=subprocess.run(cmd+['html',str(self.root/'cli.html')],text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_real_host_example_records_failure_repair_and_unconfirmed_action(self):
        from examples.host_example import run
        result=run(self.root/'host example')
        self.assertNotEqual(result['first_exit_code'],0)
        self.assertEqual(result['second_exit_code'],0)
        self.assertEqual(result['pending']['decisions'][0]['state'],'execution_unconfirmed')
        self.assertTrue((self.root/'host example'/'memory-viewer.html').exists())

    def test_pending_pagination_exposes_omissions(self):
        for i in range(3):
            ep=self.m.start(str(i),'o','t','c')
            self.m.record(ep['id'],'decision',{'decision':'wait','why':'x','expected':'x','reconsider_when':'x'},
                          expected_version=0,request_key='pending-'+str(i),actor='host')
        first=self.m.pending(limit=2);last=self.m.pending(limit=2,offset=2)
        self.assertEqual(first['total'],3);self.assertTrue(first['more']);self.assertFalse(last['more'])
        self.assertEqual(len(first['decisions'])+len(last['decisions']),3)

    def test_maintenance_preserves_search_and_history(self):
        self.record('note',{'text':'Unicode parser'})
        before=self.m.history();found=self.m.search('Unicode')
        self.assertEqual(self.m.maintain()['integrity'],'ok')
        self.assertEqual(self.m.history(),before);self.assertEqual(self.m.search('Unicode'),found)


if __name__=='__main__':unittest.main()
