"""Claude Code host compatibility: aliases, compaction context, project configuration."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from memory_module import Memory, InvalidRecord, Conflict, dumps
from memory_module import codex_host, install
from memory_module.cli import doctor
from memory_module.mcp import write


class ClaudeCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.m=Memory.create(self.root/'memory.sqlite','Tests',['Preserve exceptions.'])
        codex_host.initialize(self.m)
    def tearDown(self):
        self.m.close();self.temp.cleanup()
    def event(self,name,**kw):
        # Claude Code sends no turn_id; it sends transcript_path, cwd and permission_mode instead.
        base={'hook_event_name':name,'session_id':'claude-session','transcript_path':'/tmp/t.jsonl','cwd':str(self.root),'permission_mode':'default'}
        if name in {'PreToolUse','PostToolUse','PostToolUseFailure'}:
            base.update({'tool_name':'Bash','tool_use_id':'toolu_1','tool_input':{'command':'python3 secret'}})
        return {**base,**kw}
    def capture(self,name,**kw):return codex_host.capture(self.m,self.event(name,**kw),host='claude')
    def decision(self):
        ep=write(self.m,'start','start',{'title':'Parser','objective':'Repair parser','task_type':'repair','criterion':'Unicode passes','subject':'code'})
        src=write(self.m,'source','source',{'source_key':'contract','title':'Contract','summary':'Use UTF-8.','body':'UTF-8 input only.','origin':'document','subject':'code'})
        data={'episode_id':ep['id'],'kind':'decision','payload':{'decision':'Use UTF-8.','why':'Contract.','expected':'Fixture passes.','uncertainty':'Other encodings untested.','alternatives':['Reject invalid bytes.'],'reconsider_when':'Contract changes.'},'expected_version':0,'actor':'reviewer','evidence':[{'source_id':src['id'],'reason':'Encoding contract.'}]}
        return write(self.m,'record','choice',data,session_id='claude-session')
    def counts(self):
        return {r[0]:r[1] for r in self.m.db.execute('SELECT event_name,count(*) FROM host_receipts GROUP BY event_name')}

    def test_all_claude_lifecycle_events_are_accepted_and_interrupt_is_not_expected(self):
        for name in sorted(codex_host.HOST_EVENTS['claude']):
            self.capture(name,source='startup',trigger='auto',reason='other',prompt='hello')
        self.assertEqual(set(self.counts()),codex_host.HOST_EVENTS['claude'])
        self.assertNotIn('Interrupt',codex_host.HOST_EVENTS['claude'])
        with self.assertRaises(InvalidRecord):self.capture('Interrupt')
        with self.assertRaises(InvalidRecord):self.capture('Notification')
        with self.assertRaises(InvalidRecord):codex_host.capture(self.m,self.event('SessionStart'),host='cursor')
    def test_post_tool_use_failure_closes_the_unconfirmed_receipt_and_keeps_the_failure(self):
        d=self.decision();self.capture('PreToolUse')
        self.assertEqual(codex_host.status(self.m)['unconfirmed_total'],1)
        self.capture('PostToolUseFailure',error='Command failed: secret token')
        state=codex_host.status(self.m)
        self.assertEqual(state['unconfirmed_total'],0)
        recent=codex_host.read_receipt(self.m,state['recent'][0]['id'])
        self.assertEqual(recent['event_name'],'PostToolUse');self.assertEqual(recent['decision_id'],d['id'])
        self.assertEqual(recent['payload']['host_event'],'PostToolUseFailure');self.assertTrue(recent['payload']['failed'])
        self.assertEqual(recent['payload']['host'],'claude');self.assertNotIn('secret',dumps(recent))
    def test_session_start_after_compaction_restores_facts_without_record_text(self):
        d=self.decision();self.capture('PreToolUse')
        result=self.capture('SessionStart',source='compact')
        context=result['hookSpecificOutput']['additionalContext']
        self.assertEqual(result['hookSpecificOutput']['hookEventName'],'SessionStart')
        self.assertIn('claude-session',context);self.assertIn('compacted',context)
        self.assertIn(d['id'],context);self.assertIn('1 tool calls need reconciliation',context)
        self.assertNotIn('UTF-8',context)
        self.capture('PostCompact',trigger='auto')
        self.assertEqual(self.counts()['PostCompact'],1)
        plain=self.capture('SessionStart',source='startup')['hookSpecificOutput']['additionalContext']
        self.assertNotIn('compacted',plain)
    def test_claude_prompts_receive_context_only_when_state_needs_attention(self):
        self.assertEqual(self.capture('UserPromptSubmit',prompt='plain question'),{})
        self.assertEqual(self.counts()['UserPromptSubmit'],1)
        d=self.decision()
        context=self.capture('UserPromptSubmit',prompt='next step')['hookSpecificOutput']['additionalContext']
        self.assertIn(d['id'],context);self.assertNotIn('compacted',context)
        self.capture('PreToolUse')
        self.assertIn('1 tool calls need reconciliation',self.capture('UserPromptSubmit',prompt='again')['hookSpecificOutput']['additionalContext'])
        explicit=self.capture('UserPromptSubmit',prompt='[memory:code] parser encoding')['hookSpecificOutput']['additionalContext']
        self.assertIn('Explicit code context',explicit)
        codex=codex_host.capture(self.m,{'hook_event_name':'UserPromptSubmit','session_id':'codex','turn_id':'t1','prompt':'plain'})
        self.assertIn('Use memory_context before repeating research',codex['hookSpecificOutput']['additionalContext'])
    def test_repeated_lifecycle_events_without_turn_ids_do_not_collide(self):
        self.capture('UserPromptSubmit',prompt='first');self.capture('UserPromptSubmit',prompt='second')
        self.capture('Stop',last_assistant_message='one');self.capture('Stop',last_assistant_message='two')
        self.capture('SessionStart',source='resume');self.capture('SessionStart',source='resume')
        counts=self.counts()
        self.assertEqual(counts['UserPromptSubmit'],2);self.assertEqual(counts['Stop'],2);self.assertEqual(counts['SessionStart'],2)
        self.capture('UserPromptSubmit',prompt='third',prompt_id='p3');self.capture('UserPromptSubmit',prompt='third',prompt_id='p3')
        self.assertEqual(self.counts()['UserPromptSubmit'],3)
    def test_codex_keys_are_unchanged_and_codex_session_start_reports_context(self):
        event={'hook_event_name':'SessionStart','session_id':'codex','turn_id':'','source':'startup'}
        codex_host.capture(self.m,event);codex_host.capture(self.m,event)
        self.assertEqual(self.counts()['SessionStart'],1)
        row=codex_host.read_receipt(self.m,codex_host.status(self.m)['recent'][0]['id'])
        self.assertNotIn('host',row['payload'])
        self.assertIn('Memory session: codex',codex_host.capture(self.m,{**event,'turn_id':'t2'})['hookSpecificOutput']['additionalContext'])
    def test_memory_tools_are_not_captured_under_any_server_name(self):
        for tool in ['mcp__project_memory__memory_get','mcp__project-memory__memory_context','mcp__plugin_project-memory_project_memory__memory_write']:
            self.assertEqual(self.capture('PreToolUse',tool_name=tool),{})
        self.assertEqual(self.counts(),{})
        self.capture('PreToolUse',tool_name='mcp__other__memory_lookup')
        self.assertEqual(self.counts()['PreToolUse'],1)


class ClaudeInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.project=Path(self.temp.name)
        self.launch=[sys.executable,'-m','memory_module.cli']
    def tearDown(self):self.temp.cleanup()
    def setup(self,**kwargs):return install.setup(self.project,_launcher=self.launch,**kwargs)
    def test_claude_setup_writes_project_files_repeats_and_uninstalls_cleanly(self):
        (self.project/'.mcp.json').write_text(json.dumps({'mcpServers':{'other':{'command':'x'}}}))
        (self.project/'.claude').mkdir()
        (self.project/'.claude/settings.local.json').write_text(json.dumps({'permissions':{'allow':['Bash(ls)']},'hooks':{'Stop':[{'hooks':[{'type':'command','command':'other'}]}]}}))
        a=self.setup(client='claude',trust=True);b=self.setup(client='claude',trust=True)
        self.assertEqual(a['database'],b['database']);self.assertEqual(a['client'],'claude')
        servers=json.loads((self.project/'.mcp.json').read_text())['mcpServers']
        self.assertEqual(set(servers),{'other','project_memory'})
        self.assertEqual(servers['project_memory']['args'][-3:],['serve','--db',a['database']])
        settings=json.loads((self.project/'.claude/settings.local.json').read_text())
        self.assertEqual(settings['permissions'],{'allow':['Bash(ls)']})
        self.assertEqual(settings['enabledMcpjsonServers'],['project_memory'])
        self.assertEqual(set(settings['hooks']),codex_host.HOST_EVENTS['claude']|{'PostToolUseFailure'})
        self.assertEqual(len(settings['hooks']['Stop']),2)
        command=settings['hooks']['PreToolUse'][0]['hooks'][0]['command']
        self.assertTrue(command.endswith('--host claude'));self.assertEqual(settings['hooks']['PreToolUse'][0]['matcher'],'.*')
        self.assertFalse((self.project/'.codex').exists())
        report=doctor(Path(a['database']),'claude')
        self.assertEqual(report['client'],'claude');self.assertNotIn('Interrupt',report['missing_hook_events'])
        self.assertIn('PostCompact',report['missing_hook_events'])
        install.uninstall(self.project)
        self.assertEqual(json.loads((self.project/'.mcp.json').read_text()),{'mcpServers':{'other':{'command':'x'}}})
        settings=json.loads((self.project/'.claude/settings.local.json').read_text())
        self.assertEqual(settings,{'permissions':{'allow':['Bash(ls)']},'hooks':{'Stop':[{'hooks':[{'type':'command','command':'other'}]}]},'enabledMcpjsonServers':[]})
        with Memory(a['database']) as memory:self.assertTrue(codex_host.exists(memory))
    def test_claude_setup_refuses_an_unmanaged_server_and_mcp_trust(self):
        (self.project/'.mcp.json').write_text(json.dumps({'mcpServers':{'project_memory':{'command':'theirs'}}}))
        with self.assertRaises(Conflict):self.setup(client='claude')
        with self.assertRaises(ValueError):self.setup(client='mcp',trust=True)
    def test_installed_hook_command_captures_claude_payloads_in_a_real_process(self):
        info=self.setup(client='claude')
        settings=json.loads((self.project/'.claude/settings.local.json').read_text())
        command=settings['hooks']['SessionStart'][0]['hooks'][0]['command']
        payload={'session_id':'live','transcript_path':'/tmp/t.jsonl','cwd':str(self.project),'hook_event_name':'SessionStart','source':'compact'}
        run=subprocess.run(command,shell=True,input=json.dumps(payload),text=True,capture_output=True,timeout=60)
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertIn('compacted',json.loads(run.stdout)['hookSpecificOutput']['additionalContext'])
        self.assertEqual(doctor(Path(info['database']),'claude')['observed_hooks'],{'SessionStart':1})
    def test_hook_resolves_the_project_database_and_is_silent_without_one(self):
        env={'PATH':'/usr/bin:/bin','PYTHONPATH':str(Path(__file__).resolve().parent.parent)}
        payload=json.dumps({'session_id':'plugin','hook_event_name':'PreToolUse','tool_name':'Bash','tool_input':{'command':'ls'},'tool_use_id':'toolu_2'})
        base=[sys.executable,'-m','memory_module.cli','hook','--host','claude','--project',str(self.project)]
        run=subprocess.run(base,input=payload,text=True,capture_output=True,timeout=60,env=env)
        self.assertEqual((run.returncode,run.stdout.strip()),(0,'{}'),run.stderr)
        info=self.setup(client='mcp')
        run=subprocess.run(base,input=payload,text=True,capture_output=True,timeout=60,env=env)
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertEqual(doctor(Path(info['database']),'claude')['observed_hooks'],{'PreToolUse':1})
        run=subprocess.run(base[:-2]+['--db',str(self.project/'missing.sqlite')],input=payload,text=True,capture_output=True,timeout=60,env=env)
        self.assertEqual(run.returncode,2)


if __name__=='__main__':unittest.main()
