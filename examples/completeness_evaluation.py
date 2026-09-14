"""Reproduce omission handling at the real hook-process and SQLite boundaries."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def main():
    p=argparse.ArgumentParser();p.add_argument('--package-root',type=Path,default=Path(__file__).resolve().parents[1]);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    root=a.package_root.resolve();env={**os.environ,'PYTHONPATH':str(root)};started=time.monotonic()
    with tempfile.TemporaryDirectory(prefix='memory-completeness-eval-') as d:
        database=Path(d)/'memory.sqlite'
        subprocess.run([sys.executable,'-c',"from memory_module import Memory,codex_host; import sys; m=Memory.create(sys.argv[1],'Omission fixture',['Keep the tagged legacy exception.']); codex_host.initialize(m); m.close()",str(database)],cwd=root,env=env,check=True,capture_output=True)
        outputs=[];latencies=[]
        events=[{'hook_event_name':'UserPromptSubmit','prompt':'Preserve the tagged legacy exception.'},{'hook_event_name':'PreToolUse','tool_name':'Bash','tool_use_id':'lookup','tool_input':{'command':'fixture lookup'}},{'hook_event_name':'PostToolUse','tool_name':'Bash','tool_use_id':'lookup','tool_response':{'exit_code':0}},*([{'hook_event_name':'Stop','last_assistant_message':'Finished. All tests pass.'}]*3)]
        for event in events:
            t=time.monotonic();r=subprocess.run([sys.executable,'-m','memory_module.codex_host','--db',str(database)],input=json.dumps({'session_id':'fixture','turn_id':'1',**event}),cwd=root,env=env,text=True,capture_output=True,check=True)
            latencies.append(round((time.monotonic()-t)*1000,2));outputs.append(json.loads(r.stdout))
        code='''import inspect,json,sys
from memory_module import Memory,reviews,InvalidRecord
from memory_module.mcp import write
with Memory(sys.argv[1]) as m:
 report={'verdict':'pass','summary':'The checked fixture passed.','checks':[{'criterion':'C001','evidence':'The one implemented test passed.','result':'met'}],'findings':[],'lesson_proposals':[]}
 accepted=True
 try:
  if 'checklist' in inspect.signature(reviews.validate_report).parameters:reviews.validate_report(report,[{'id':'C001','condition':'UTF-8 works.'},{'id':'C002','condition':'The tagged legacy exception works.'}])
  else:reviews.validate_report(report)
 except InvalidRecord:accepted=False
 result={'partial_checklist_accepted':accepted,'integrity':m.db.execute('PRAGMA integrity_check').fetchone()[0],'episodes_created':m.db.execute('SELECT count(*) FROM episodes').fetchone()[0]}
 try:
  from memory_module.coverage import inspect as coverage
  before=coverage(m,'fixture',limit=3);prompt=before['pending_prompts'][0]['id']
  ack=write(m,'checkpoint','fixture-assessment',{'prompt_ids':[prompt],'effect':'informational','reason':'The fixture lookup is complete and introduces no material work.'},session_id='fixture')
  result.update(issues_before=[i['type'] for i in before['issues']],checkpoint=ack,issues_after=coverage(m,'fixture')['issues'])
 except ImportError:result.update(issues_before=None,checkpoint=None,issues_after=None)
 print(json.dumps(result))'''
        r=subprocess.run([sys.executable,'-c',code,str(database)],cwd=root,env=env,text=True,capture_output=True,check=True)
        result={'fixture':'One lookup; no initial assessment; chat-only completion; three Stop deliveries.','boundary':'Actual hook subprocesses and SQLite; no model calls.',**json.loads(r.stdout),'stop_interventions':sum(o.get('decision')=='block' for o in outputs[3:]),'hook_output_characters':sum(len(json.dumps(o)) for o in outputs),'hook_process_ms':latencies,'duration_ms':round((time.monotonic()-started)*1000),'model_tokens':None,'human_corrections':0,'repeated_fixture_operations':0}
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))


if __name__=='__main__':main()
