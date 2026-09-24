import shutil
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from memory_module import Memory, InvalidRecord, Conflict, BudgetTooSmall, dumps
from memory_module import codex_host
from memory_module.mcp import dispatch, serve, tool_result, write


class CodexTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(ignore_cleanup_errors=True);self.root=Path(self.temp.name)
        self.m=Memory.create(self.root/'memory.sqlite','Tests',['Preserve exceptions.'])
        codex_host.initialize(self.m)
    def tearDown(self):
        self.m.close();shutil.rmtree(self.temp.name, ignore_errors=True)
    def event(self,name,**kw):
        return {'hook_event_name':name,'session_id':'session','turn_id':'turn','tool_name':'Bash','tool_use_id':'call','tool_input':{'command':'python3 secret'},**kw}
    def decision(self):
        ep=write(self.m,'start','start',{'title':'Parser','objective':'Repair parser','task_type':'repair','criterion':'Unicode passes','subject':'code'})
        src=write(self.m,'source','source',{'source_key':'contract','title':'Contract','summary':'Use UTF-8.','body':'UTF-8 input only.','origin':'document','subject':'code'})
        data={'episode_id':ep['id'],'kind':'decision','payload':{'decision':'Use UTF-8.','why':'Contract.','expected':'Fixture passes.','uncertainty':'Other encodings untested.','alternatives':['Reject invalid bytes.'],'reconsider_when':'Contract changes.'},'expected_version':0,'actor':'reviewer','evidence':[{'source_id':src['id'],'reason':'Encoding contract.'}]}
        return write(self.m,'record','choice',data,session_id='session')
    def test_receipts_do_not_invent_decisions_or_store_prompts(self):
        codex_host.capture(self.m,self.event('PreToolUse'))
        codex_host.capture(self.m,self.event('PostToolUse',tool_response='Process exited with code 1\nsecret token'))
        self.assertEqual(self.m.db.execute('SELECT count(*) FROM events').fetchone()[0],0)
        records=[codex_host.read_receipt(self.m,r['id']) for r in codex_host.status(self.m)['recent']]
        self.assertNotIn('secret',dumps(records));self.assertEqual(records[0]['payload']['tool_response']['exit_code'],1)
        self.assertEqual(codex_host.status(self.m)['unconfirmed_total'],0)
    def test_interrupted_action_is_not_repeated_and_reconciles_with_evidence(self):
        d=self.decision();event=self.event('PreToolUse',tool_input={'command':'git push origin main'})
        codex_host.capture(self.m,event);codex_host.capture(self.m,event)
        self.assertEqual(self.m.db.execute("SELECT count(*) FROM events WHERE kind='action'").fetchone()[0],1)
        codex_host.capture(self.m,self.event('Interrupt'))
        row=codex_host.status(self.m)['unconfirmed'][0]
        self.assertEqual(row['decision_id'],d['id'])
        with self.assertRaises(InvalidRecord):codex_host.reconcile(self.m,row['id'],'completed','Checked.',[], 'r')
        src=self.m.source('actual','Actual state','Marker exists.','Read marker once.','tool',subject='code')
        proof=[{'source_id':src['id'],'reason':'Observed side effect.'}]
        write(self.m,'reconcile','recovery',{'receipt_id':row['id'],'resolution':'completed','reason':'The marker was present after interruption.','evidence':proof})
        self.assertEqual(codex_host.status(self.m)['unconfirmed_total'],0)
        self.assertEqual(self.m.db.execute("SELECT count(*) FROM host_receipts WHERE event_name='Interrupt'").fetchone()[0],1)
    def test_post_call_remains_with_original_decision(self):
        d=self.decision();codex_host.capture(self.m,self.event('PreToolUse'))
        # A result from a later polling turn belongs to its original preparation.
        codex_host.capture(self.m,self.event('PostToolUse',turn_id='later',tool_response={'exit_code':0}))
        self.assertEqual(codex_host.status(self.m)['recent'][0]['decision_id'],d['id'])
    def test_completed_choice_does_not_capture_unrelated_later_tools(self):
        d=self.decision();codex_host.capture(self.m,self.event('PreToolUse'))
        detail=self.m.read(d['id'])
        self.m.record(detail['episode_id'],'outcome',{'observed':'The fixture passed.','assessment':'good','assessment_reason':'The agreed fixture passed.','severity':'none','attribution':'The selected decoder matched the contract.'},decision_id=d['id'],expected_version=2,request_key='done',actor='reviewer',evidence=[{'source_id':detail['evidence'][0]['source_id'],'reason':'The fixture checks this contract.'}])
        self.assertIsNone(codex_host.status(self.m,session_id='session')['active'])
        codex_host.capture(self.m,self.event('PreToolUse',tool_use_id='unrelated'))
        self.assertIsNone(codex_host.status(self.m)['recent'][0]['decision_id'])
        codex_host.capture(self.m,self.event('PostToolUse',tool_response={'exit_code':0}))
        self.assertEqual(codex_host.status(self.m)['recent'][0]['decision_id'],d['id'])
    def test_adapter_mutation_retries_do_not_create_source_versions(self):
        data={'source_key':'test','title':'Test','summary':'Test','body':'Test','origin':'tool'}
        first=write(self.m,'source','stable',data);second=write(self.m,'source','stable',data)
        self.assertEqual(first,second)
        self.assertEqual(self.m.db.execute('SELECT count(*) FROM sources').fetchone()[0],1)
        with self.assertRaises(Conflict):write(self.m,'source','stable',{**data,'body':'Changed'})
    def test_context_budget_covers_mcp_envelope_and_seen(self):
        self.m.source('test','Encoding','Preserve invalid-byte errors.','Encoding UTF-8','document',subject='research')
        packet=dispatch(self.m,'memory_context',{'query':'encoding','subject':'research','max_chars':1200})
        self.assertEqual(packet['used'],len(dumps(tool_result(packet))))
        self.assertLessEqual(packet['used'],1200)
        seen={r['id']:r['signature'] for r in packet['records'] if 'signature' in r}
        again=dispatch(self.m,'memory_context',{'query':'encoding','subject':'research','max_chars':1200,'seen':seen})
        self.assertLess(again['used'],packet['used'])
    def test_mcp_protocol_parse_errors_and_tools_are_separate(self):
        messages=['bad json',dumps({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25'}}),dumps({'jsonrpc':'2.0','method':'notifications/initialized'}),dumps({'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'bad','arguments':{}}}),dumps({'jsonrpc':'2.0','id':3,'method':'tools/list'})]
        out=io.StringIO();serve(self.m,io.StringIO('\n'.join(messages)),out)
        values=[json.loads(s) for s in out.getvalue().splitlines()]
        self.assertEqual(len(values),4);self.assertEqual(values[0]['error']['code'],-32700)
        self.assertTrue(values[2]['result']['isError']);self.assertEqual(len(values[3]['result']['tools']),3)
    def test_schema_rejection_names_missing_unexpected_and_nested_fields(self):
        cases=[('memory_context',{'query':'test','sprint_id':'unsupported'},
                {'arguments.subject':'missing','arguments.sprint_id':'unexpected'}),
               ('memory_get',{'view':'records','ids':['source',3]}, {'arguments.ids[1]':'wrong_type'}),
               ('memory_write',{'operation':'source','request_key':'rejected','data':{
                   'source_key':'key','title':'Title','summary':'Summary','origin':'tool','unexpected':'value'}},
                {'arguments.data.body':'missing','arguments.data.unexpected':'unexpected'}),
               ('memory_write',{'operation':'record','request_key':'rejected','data':{'payload':{'alternatives':'not a list'}}},
                {'arguments.data.payload.alternatives':'wrong_type'}),
               ('memory_get',{'view':'board','limit':True},{'arguments.limit':'wrong_type'}),
               ('memory_get',{'view':'board','limit':0},{'arguments.limit':'minimum'})]
        before=self.m.db.total_changes
        for name,args,expected in cases:
            with self.subTest(name=name,args=args),self.assertRaises(InvalidRecord) as caught:
                dispatch(self.m,name,args)
            details=caught.exception.details
            self.assertEqual({e['field']:e['problem'] for e in details['field_errors']},expected)
            self.assertEqual(details['execution'],'not_started')
            self.assertEqual(details['next_step']['action'],'correct_arguments')
        self.assertEqual(self.m.db.total_changes,before)

    def test_real_mcp_process_recovers_from_rejected_write_without_duplicate_effects(self):
        data={'source_key':'corrected','title':'Corrected request','summary':'Protocol recovery fixture.',
              'body':'The exception remains unchanged.','origin':'tool'}
        args={'operation':'source','request_key':'corrected-once','data':data}
        invalid={**args,'data':{**data,'extra_field':'unsupported'}}
        requests=[{'jsonrpc':'2.0','id':i,'method':'tools/call','params':{'name':'memory_write','arguments':value}}
                  for i,value in enumerate([invalid,args,args],1)]
        requests.insert(0,{'jsonrpc':'2.0','id':0,'method':'initialize','params':{'protocolVersion':'2025-11-25'}})
        run=subprocess.run([sys.executable,'-m','memory_module.mcp','--db',str(self.m.path)],
            input='\n'.join(dumps(r) for r in requests)+'\n',text=True,capture_output=True,check=True,
            cwd=Path(__file__).resolve().parent.parent)
        results=[json.loads(line)['result'] for line in run.stdout.splitlines()][1:]
        error=json.loads(results[0]['content'][0]['text'])
        self.assertTrue(results[0]['isError']);self.assertEqual(error['execution'],'not_started')
        self.assertIn('arguments.data.extra_field',error['message'])
        self.assertEqual(error['next_step']['read_with'],{'view':'schema','id':'source'})
        self.assertFalse(results[1]['isError']);self.assertFalse(results[2]['isError'])
        self.assertEqual(results[1],results[2])
        self.assertEqual(self.m.db.execute("SELECT count(*) FROM sources WHERE source_key='corrected'").fetchone()[0],1)
        with self.assertRaises(Conflict):dispatch(self.m,'memory_write',{**args,'data':{**data,'body':'Changed content.'}})

    def test_nested_record_and_review_schema_recovery_in_a_real_mcp_process(self):
        episode=self.m.start('Review fixture','Verify recovery.','test','Preserve the source.',subject='code')
        source=self.m.source('review-contract','Contract','Preserve the exception.','The exception remains supported.','user')
        evidence=[{'source_id':source['id'],'reason':'The recorded contract governs this code review.'}]
        note={'operation':'record','request_key':'nested-note','data':{'episode_id':episode['id'],'kind':'note',
            'expected_version':0,'actor':'fixture','payload':{'text':'Example'}}}
        review={'operation':'record','request_key':'code-review','data':{'episode_id':episode['id'],'kind':'review',
            'expected_version':1,'actor':'fixture','evidence':evidence,
            'payload':{'target':'Parser','revision':'fixture','summary':'The exception is retained.','findings':[]}}}
        bad_note={**note,'data':{**note['data'],'payload':{'text':'Example','unexpected':True}}}
        bad_review={**review,'data':{k:v for k,v in review['data'].items() if k!='actor'}}
        wrong_type={**review,'data':{**review['data'],'actor':3}}
        nested_review={**review,'data':{**review['data'],'payload':{**review['data']['payload'],
            'findings':[{'location':'parser.py','issue':'Inspect the exception.','severity':'minor','extra':True}]}}}
        calls=[bad_note,note,note,bad_review,wrong_type,nested_review,review,review]
        requests=[{'jsonrpc':'2.0','id':0,'method':'initialize','params':{'protocolVersion':'2025-11-25'}}]
        requests.extend({'jsonrpc':'2.0','id':i,'method':'tools/call','params':{'name':'memory_write','arguments':value}}
                        for i,value in enumerate(calls,1))
        result=subprocess.run([sys.executable,'-m','memory_module.mcp','--db',str(self.m.path)],
            input='\n'.join(dumps(r) for r in requests)+'\n',text=True,capture_output=True,check=True,
            cwd=Path(__file__).resolve().parent.parent)
        responses=[json.loads(line)['result'] for line in result.stdout.splitlines()][1:]
        for index,field,schema_id in [(0,'arguments.data.payload.unexpected','note'),(3,'arguments.data.actor','review'),
                                      (4,'arguments.data.actor','review'),(5,'arguments.data.payload.findings[0].extra','review')]:
            error=json.loads(responses[index]['content'][0]['text'])
            self.assertTrue(responses[index]['isError']);self.assertEqual(error['execution'],'not_started')
            self.assertEqual(error['field_errors'][0]['field'],field)
            self.assertEqual(error['next_step']['read_with'],{'view':'schema','id':schema_id})
            metadata=dispatch(self.m,'memory_get',error['next_step']['read_with'])
            self.assertEqual(metadata['kind'],schema_id)
        for index in [1,2,6,7]:self.assertFalse(responses[index]['isError'],responses[index])
        self.assertEqual(responses[1],responses[2]);self.assertEqual(responses[6],responses[7])
        self.assertEqual(self.m.db.execute('SELECT count(*) FROM events WHERE episode_id=?',(episode['id'],)).fetchone()[0],2)
        with self.assertRaises(InvalidRecord) as caught:
            dispatch(self.m,'memory_write',{'operation':'review','request_key':'agent-check','data':{}})
        self.assertEqual(caught.exception.details['next_step']['read_with'],{'view':'schema','id':'agent_check'})

    def test_nested_component_fields_are_rejected_before_writing_and_corrected_once(self):
        source=self.m.source('component-request','Architecture request','Record a proposed stakeholder.','The user names the finance team as a stakeholder.','user')
        reference={'source_id':source['id'],'reason':'The user names this stakeholder.'}
        args={'operation':'component','request_key':'component-recovery','data':{'title':'Finance team','kind':'stakeholder',
            'description':'The finance team approves the budget.','status':'proposed','actor':'assistant','evidence':[reference]}}
        def components():
            return self.m.db.execute("SELECT count(*) FROM sources WHERE source_key LIKE 'component:%'").fetchone()[0]
        for bad in [{**reference,'unexpected':True},{**reference,'reason':3}]:
            with self.assertRaises(InvalidRecord) as caught:
                dispatch(self.m,'memory_write',{**args,'data':{**args['data'],'evidence':[bad]}})
            details=caught.exception.details
            self.assertEqual(details['execution'],'not_started')
            self.assertTrue(details['field_errors'][0]['field'].startswith('arguments.data.evidence[0].'))
            self.assertEqual(details['next_step']['read_with'],{'view':'schema','id':'component'})
        with self.assertRaises(InvalidRecord) as caught:
            dispatch(self.m,'memory_write',{**args,'data':{**args['data'],'kind':'feature'}})
        self.assertEqual(caught.exception.details['field_errors'][0]['problem'],'invalid_choice')
        self.assertEqual(components(),0)
        result=dispatch(self.m,'memory_write',args)
        self.assertEqual(dispatch(self.m,'memory_write',args),result)
        self.assertEqual((result['id'],result['status']),('component:finance-team','proposed'))
        self.assertEqual(components(),1)
        confirm={**args['data'],'component_id':result['id'],'status':'confirmed'}
        with self.assertRaises(InvalidRecord):
            dispatch(self.m,'memory_write',{'operation':'component','request_key':'agent-confirms','data':confirm})
        with self.assertRaises(InvalidRecord):
            dispatch(self.m,'memory_write',{'operation':'component','request_key':'impersonated','data':{**confirm,'actor':'workspace-user'}})
        self.assertEqual(components(),1)

    def test_every_advertised_write_operation_rejects_unknown_fields_at_protocol_boundary(self):
        from memory_module.mcp import TOOLS
        operations=next(t for t in TOOLS if t['name']=='memory_write')['inputSchema']['properties']['operation']['enum']
        requests=[{'jsonrpc':'2.0','id':0,'method':'initialize','params':{'protocolVersion':'2025-11-25'}}]
        requests.extend({'jsonrpc':'2.0','id':i,'method':'tools/call','params':{'name':'memory_write','arguments':{
            'operation':op,'request_key':'reject-'+op,'data':{'unexpected':True}}}} for i,op in enumerate(operations,1))
        before={table:self.m.db.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in ['sources','events','episodes','adapter_requests']}
        process=subprocess.run([sys.executable,'-m','memory_module.mcp','--db',str(self.m.path)],
            input='\n'.join(dumps(r) for r in requests)+'\n',text=True,capture_output=True,check=True,
            cwd=Path(__file__).resolve().parent.parent)
        responses=[json.loads(line)['result'] for line in process.stdout.splitlines()][1:]
        self.assertEqual(len(responses),len(operations))
        for op,result in zip(operations,responses):
            with self.subTest(operation=op):
                self.assertTrue(result['isError'])
                error=json.loads(result['content'][0]['text'])
                self.assertEqual(error['execution'],'not_started')
                self.assertIn({'field':'arguments.data.unexpected','problem':'unexpected'},
                    [{k:e[k] for k in ['field','problem']} for e in error['field_errors']])
                self.assertEqual(error['next_step']['read_with'],{'view':'schema','id':'agent_check' if op=='review' else op})
                metadata=dispatch(self.m,'memory_get',error['next_step']['read_with'])
                if op=='record':self.assertIn('record_kinds',metadata)
                elif op in {'start','source','document'}:self.assertIn('fields',metadata)
                else:self.assertEqual(metadata['operation'],op)
                if op=='sync':self.assertEqual(set(metadata['fields']),{'limit','offset','check'})
                if op=='approve_requirements':self.assertEqual(set(metadata['required']),{'requirements','reason','actor','evidence','expected_version'})
        self.assertEqual(before,{table:self.m.db.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in before})

    def test_link_schema_correction_and_replay_in_a_real_mcp_process(self):
        research=self.m.start('Market research','Collect the regional evidence.','research','The evidence is recorded.',subject='research')
        report=self.m.start('Client report','Write the recommendations.','deliverable','The client receives the report.',subject='writing')
        def links():
            exists=self.m.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='links'").fetchone()
            return self.m.db.execute('SELECT count(*) FROM links').fetchone()[0] if exists else 0
        def call(args):
            requests=[{'jsonrpc':'2.0','id':0,'method':'initialize','params':{'protocolVersion':'2025-11-25'}},
                      {'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'memory_write','arguments':args}}]
            result=subprocess.run([sys.executable,'-m','memory_module.mcp','--db',str(self.m.path)],
                input='\n'.join(dumps(r) for r in requests)+'\n',text=True,capture_output=True,check=True,
                cwd=Path(__file__).resolve().parent.parent)
            reply=json.loads(result.stdout.splitlines()[-1])['result']
            return reply['isError'],json.loads(reply['content'][0]['text'])
        link={'operation':'link','request_key':'link-once','data':{'from_id':report['id'],'to_id':research['id'],'type':'depends_on',
            'reason':'The report uses the research findings.','actor':'assistant'}}
        for data,problem in [({**link['data'],'unexpected':True},'unexpected'),({**link['data'],'type':'needs'},'invalid_choice'),
                             ({k:v for k,v in link['data'].items() if k!='reason'},'missing')]:
            failed,error=call({**link,'data':data});self.assertTrue(failed)
            self.assertEqual(error['execution'],'not_started')
            self.assertIn(problem,[issue['problem'] for issue in error['field_errors']])
            self.assertEqual(error['next_step']['read_with'],{'view':'schema','id':'link'})
        self.assertEqual(links(),0)
        failed,result=call(link);self.assertFalse(failed,result)
        self.assertEqual(call(link),(False,result));self.assertEqual(links(),1)
        failed,conflict=call({**link,'data':{**link['data'],'reason':'Changed request.'}})
        self.assertTrue(failed);self.assertEqual(conflict['error'],'Conflict');self.assertNotIn('execution',conflict)
        failed,error=call({**link,'request_key':'impersonated','data':{**link['data'],'type':'relates_to','actor':'workspace-user'}})
        self.assertTrue(failed);self.assertEqual(error['error'],'InvalidRecord');self.assertEqual(links(),1)
        graph=dispatch(self.m,'memory_get',{'view':'graph','id':report['id']})
        self.assertIn((report['id'],'depends_on',research['id']),[(e['from'],e['type'],e['to']) for e in graph['edges']])

    def test_mcp_process_restart_preserves_idempotent_capture(self):
        request={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'memory_write','arguments':{
            'operation':'source','request_key':'restart-source','data':{'source_key':'restart','title':'Restart evidence','summary':'The same request is replayed after restart.','body':'The fixture uses the same request key and content.','origin':'tool'}}}}
        command=[sys.executable,'-m','memory_module.mcp','--db',str(self.root/'memory.sqlite')]
        results=[]
        for _ in range(2):
            initialize={'jsonrpc':'2.0','id':0,'method':'initialize','params':{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'restart-test','version':'1'}}}
            run=subprocess.run(command,input=dumps(initialize)+'\n'+dumps(request)+'\n',text=True,capture_output=True,check=True,cwd=Path(__file__).resolve().parent.parent)
            result=json.loads(run.stdout.splitlines()[-1])['result'];self.assertFalse(result['isError']);results.append(json.loads(result['content'][0]['text']))
        self.assertEqual(results[0]['id'],results[1]['id'])
        self.assertEqual(self.m.db.execute("SELECT count(*) FROM sources WHERE source_key='restart'").fetchone()[0],1)
    def test_receipt_metadata_is_immutable(self):
        codex_host.capture(self.m,self.event('PreToolUse'))
        with self.assertRaises(sqlite3.IntegrityError):self.m.db.execute('DELETE FROM host_receipts')
    def test_memory_tools_do_not_capture_themselves(self):
        for server in ['memory','project_memory']:
            codex_host.capture(self.m,self.event('PreToolUse',tool_name='mcp__'+server+'__memory_context'))
        self.assertEqual(self.m.db.execute('SELECT count(*) FROM host_receipts').fetchone()[0],0)
    def test_small_read_does_not_cut_record_conditions(self):
        source=self.m.source('test','Test','x'*1900,'body','tool')
        with self.assertRaises(BudgetTooSmall):dispatch(self.m,'memory_get',{'view':'record','id':source['id'],'max_chars':500})


class HookRuleTests(unittest.TestCase):
    """The rules the assistant receives before an edit, composed at the assistant budget."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name).resolve()
        self.m = Memory.create(self.root / '.memory' / 'memory.sqlite', 'Hook rules', ['Preserve recorded scope.'])
        codex_host.initialize(self.m)
        source = self.m.source('user-scope', 'Scope', 'User instruction', 'Change only the parser.', 'user')
        self.evidence = [{'source_id': source['id'], 'reason': 'The user defines the scope.'}]
        self.lessons = self.m.start('Lessons', 'Collect parser lessons.', 'learning',
                                    'Lessons are reviewed.', subject='code')['id']
        self.counter = 0

    def tearDown(self):
        self.m.close()
        shutil.rmtree(self.temp.name, ignore_errors=True)

    def key(self):
        self.counter += 1
        return 'key-' + str(self.counter)

    def version(self):
        return self.m.episode(self.lessons)['version']

    def rule(self, do='Run the Unicode fixture first.', **triggers):
        """Record a lesson and accept it, so that it becomes an active guard."""
        payload = {'when': 'Editing the parser.', 'do': do, 'because': 'Earlier edits lost characters.',
                   'exceptions': 'Documentation changes.', **triggers}
        lesson = self.m.record(self.lessons, 'lesson', payload, expected_version=self.version(),
                               request_key=self.key(), actor='assistant', evidence=self.evidence)['id']
        self.m.record(self.lessons, 'lesson_review', {'lesson_id': lesson, 'status': 'accepted',
                                                      'reason': 'The user reviewed the lesson.'},
                      expected_version=self.version(), request_key=self.key(), actor='workspace-user',
                      evidence=self.evidence, links=[{'event_id': lesson, 'reason': 'This review assesses the lesson.'}])
        return lesson

    def edit(self, session='session', call='call-1', target='src/app.py'):
        event = {'hook_event_name': 'PreToolUse', 'session_id': session, 'turn_id': 'turn', 'tool_name': 'Edit',
                 'tool_use_id': call, 'tool_input': {'file_path': str(self.root / target)}}
        return codex_host.capture(self.m, event, host='claude')

    def context(self, result):
        return result.get('hookSpecificOutput', {}).get('additionalContext', '')

    def shown(self):
        return [json.loads(row[0])['lesson_id'] for row in self.m.db.execute(
            "SELECT payload FROM host_receipts WHERE event_name='GuardShown' ORDER BY rowid")]

    def test_the_hook_carries_the_assistant_rules_once_in_a_session(self):
        assistant = self.rule(roles=['assistant'], paths=['src/**'], do='Run the Unicode fixture first.')
        worker = self.rule(roles=['worker'], paths=['src/**'], do='Read the worker checklist.')
        without_roles = self.rule(paths=['src/**'], do='Keep the tagged exception.')
        first = self.context(self.edit())
        self.assertIn(assistant, first)
        self.assertIn('Run the Unicode fixture first.', first)
        self.assertIn(without_roles, first)
        self.assertIn('Keep the tagged exception.', first)
        self.assertNotIn(worker, first)
        self.assertLessEqual(len(first), codex_host.HOOK_CHARACTERS)
        self.assertEqual(self.context(self.edit(call='call-2')), '')
        other = self.context(self.edit(session='other', call='call-3'))
        self.assertIn(assistant, other)
        self.assertEqual(self.context(self.edit(session='third', call='call-4', target='docs/notes.md')), '')
        self.assertEqual(sorted(self.shown()), sorted([assistant, without_roles] * 2))

    def test_a_rule_that_does_not_fit_is_named_and_waits_for_the_next_edit(self):
        rules = [self.rule(roles=['assistant'], paths=['src/**'], do=f'Run check number {index}.' + ' Keep the recorded conditions.' * 3)
                 for index in range(9)]
        first = self.context(self.edit())
        carried = self.shown()
        self.assertTrue(0 < len(carried) < len(rules))
        self.assertLessEqual(len(first), codex_host.HOOK_CHARACTERS)
        self.assertIn('were not carried here', first)
        # Every rule left out is named, and none of them is recorded as shown.
        for lesson in rules:
            self.assertIn(lesson, first)
        second = self.context(self.edit(call='call-2'))
        later = [lesson for lesson in self.shown() if lesson not in carried]
        self.assertTrue(later)
        self.assertTrue(all(lesson in second for lesson in later))
        self.assertEqual(len(set(self.shown())), len(self.shown()))

    def prompt(self, session='session', text='Repair the parser now.'):
        event = {'hook_event_name': 'UserPromptSubmit', 'session_id': session, 'prompt_id': 'p1', 'prompt': text}
        return self.context(codex_host.capture(self.m, event, host='claude'))

    def start(self, session='session'):
        event = {'hook_event_name': 'SessionStart', 'session_id': session}
        return self.context(codex_host.capture(self.m, event, host='claude'))

    def test_the_session_start_hook_carries_the_base_text_of_the_assistant(self):
        from memory_module import guards, workspace
        context = self.start()
        self.assertIn(guards.shipped_base('assistant'), context)
        self.assertLessEqual(len(context), codex_host.HOOK_CHARACTERS)
        workspace.action(self.m, 'instructions', {'role': 'assistant', 'text': 'Record every check you run.'}, 'base-1')
        self.assertIn('Record every check you run.', self.start(session='second'))
        self.assertNotIn(guards.shipped_base('assistant'), self.start(session='third'))

    def test_a_rule_of_the_role_reaches_the_prompt_hook(self):
        from memory_module import guards
        rule = self.rule(roles=['assistant'], do='State the recorded scope before editing.')
        keyword = self.rule(roles=['assistant'], keywords=['parser'], do='Read the parser fixture first.')
        other = self.rule(roles=['assistant'], paths=['src/**'], do='Run the Unicode fixture first.')
        context = self.prompt()
        self.assertIn('State the recorded scope before editing.', context)
        self.assertIn('Read the parser fixture first.', context)
        self.assertNotIn(other, context)
        self.assertIn('Memory session: session.', context)
        self.assertLessEqual(len(context), codex_host.HOOK_CHARACTERS)
        # Each rule is shown once in a session, so the next prompt repeats neither of them.
        self.assertNotIn(rule, self.prompt())
        self.assertEqual(sorted(self.shown()), sorted([rule, keyword]))
        self.assertLessEqual(guards.compose(self.m, 'assistant', text='Repair the parser now.')['used'],
                             guards.ROLE_BUDGETS['assistant'])

    def test_a_rule_that_never_fits_is_reported_although_no_rule_is_carried(self):
        long_rule = self.rule(roles=['assistant'], paths=['src/**'],
                              do='Run the Unicode fixture first. ' + 'Keep every recorded exception. ' * 25)
        context = self.context(self.edit())
        self.assertIn(long_rule, context)
        self.assertIn('were not carried here', context)
        self.assertEqual(self.shown(), [])
        # The panel reports the same rule as left out, with the reason, instead of as waiting for a run.
        from memory_module import api
        role = api.instructions(self.m)['roles']['assistant']
        self.assertEqual([entry['lesson_id'] for entry in role['omitted']], [long_rule])
        self.assertIn('no run of this role can carry it', role['omitted'][0]['reason'])

    def test_the_rule_text_of_the_hook_stays_within_the_assistant_budget(self):
        from memory_module import guards
        for index in range(8):
            self.rule(roles=['assistant'], paths=['src/**'],
                      do=f'Run check number {index}.' + ' Keep the recorded conditions.' * 3)
        context = self.context(self.edit())
        rules = context.split('These accepted rules also apply')[0].strip()
        self.assertLessEqual(len(rules), guards.ROLE_BUDGETS['assistant'])
        self.assertIn('were not carried here', context)
        self.assertLessEqual(len(context), codex_host.HOOK_CHARACTERS)

    def test_the_composed_rules_stay_within_the_assistant_budget(self):
        from memory_module import guards
        for index in range(3):
            self.rule(roles=['assistant'], paths=['src/**'], do=f'Run check number {index}.')
        result = codex_host.assistant_rules(self.m, session_id='direct', targets=['src/app.py'], root=self.root)
        composed = guards.compose(self.m, 'assistant', paths=['src/app.py'], root=self.root)
        self.assertEqual(result['rule_ids'], composed['rule_ids'])
        self.assertLessEqual(composed['used'], guards.ROLE_BUDGETS['assistant'])
        self.assertEqual(codex_host.assistant_rules(self.m, session_id='direct', targets=[], root=self.root)['text'], '')

    def cost(self, call):
        """The result of one hook call and the number of database statements it ran."""
        statements = []
        self.m.db.set_trace_callback(statements.append)
        try:
            return call(), len(statements)
        finally:
            self.m.db.set_trace_callback(None)

    def test_an_edit_the_rules_do_not_cover_costs_almost_nothing(self):
        """A rule is selected by its triggers before its acceptance is confirmed, so an edit outside
        the recorded paths does not pay the freshness check of every accepted rule."""
        for index in range(24):
            self.rule(roles=['assistant'], paths=['src/**'], do=f'Run check number {index}.')
        (outside, outside_cost) = self.cost(lambda: self.context(self.edit(target='docs/notes.md')))
        (covered, covered_cost) = self.cost(lambda: self.context(self.edit(call='call-2')))
        self.assertEqual(outside, '')
        self.assertIn('Run check number 23.', covered)
        self.assertLess(outside_cost * 4, covered_cost)
        self.assertLess(outside_cost, 60)

    def test_a_later_edit_in_the_same_session_does_not_compose_the_rules_again(self):
        """Every rule the session has seen is filtered out before the freshness check, so the cost of
        the rules falls on the first edit and not on every edit that follows."""
        for index in range(24):
            self.rule(roles=['assistant'], paths=['src/**'], do=f'Run check number {index}.')
        (first, first_cost) = self.cost(lambda: self.context(self.edit()))
        self.assertIn('Run check number 23.', first)
        costs = []
        for call in range(2, 6):
            (context, cost) = self.cost(lambda call=call: self.context(self.edit(call='call-%d' % call)))
            costs.append(cost)
            self.assertNotIn('Run check number 23.', context)
        self.assertLess(max(costs) * 4, first_cost)
        self.assertLess(max(costs), 80)


if __name__=='__main__':unittest.main()
