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
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.m=Memory.create(self.root/'memory.sqlite','Tests',['Preserve exceptions.'])
        codex_host.initialize(self.m)
    def tearDown(self):
        self.m.close();self.temp.cleanup()
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
        d=self.decision();event=self.event('PreToolUse')
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
        codex_host.capture(self.m,self.event('PreToolUse',tool_name='mcp__memory__memory_context'))
        self.assertEqual(self.m.db.execute('SELECT count(*) FROM host_receipts').fetchone()[0],0)
    def test_small_read_does_not_cut_record_conditions(self):
        source=self.m.source('test','Test','x'*1900,'body','tool')
        with self.assertRaises(BudgetTooSmall):dispatch(self.m,'memory_get',{'view':'record','id':source['id'],'max_chars':500})


if __name__=='__main__':unittest.main()
