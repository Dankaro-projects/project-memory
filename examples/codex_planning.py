"""Run a real Codex continuation against the invented parser fixture."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
from examples.codex_app_client import CodexClient, project_database
from examples.codex_cases import final_json
from memory_module import Memory
from memory_module.planning import card


def tool_error(call):
    result=call.get('result') or {}
    if call.get('error') or result.get('isError'):
        return True
    for item in result.get('content',[]):
        if item.get('type')=='text':
            try: value=json.loads(item['text'])
            except (ValueError,KeyError): continue
            if isinstance(value,dict) and value.get('error'):
                return True
    return False


def run(project,output, *, resume_thread=None, timeout=600):
    project=Path(project).resolve();output=Path(output);output.mkdir(parents=True,exist_ok=False)
    fixture=json.loads((project/'fixture.json').read_text());episode=fixture['repair']['episode_id']
    client=CodexClient(project,output/'events.jsonl')
    try:
        if resume_thread:
            thread=resume_thread
            client.request('thread/resume',{'threadId':thread,'cwd':str(project),'approvalPolicy':'never','sandbox':'workspace-write','excludeTurns':True})
            prompt=f'Resume the interrupted parser task {episode}. Inspect its next-work state and the existing execution evidence. The repair and tests may already have completed; do not repeat a completed edit or command. Finish any missing outcome assessment and work-state update if the evidence supports it. Preserve the original scope and do not start another task. Return only JSON with encoding, invalid_bytes, legacy_exception, production_status and work_status.'
        else:
            thread=client.start()
            prompt=f'Continue the parser repair recorded in Project Memory for episode {episode}. Recover its intent and existing scope, complete the authorised local work, verify it, and leave its recorded work state accurate. Do not broaden the encoding policy or start other queued work. Return only JSON with encoding, invalid_bytes, legacy_exception, production_status and work_status.'
        turn=client.turn(thread,prompt)
        try: end=client.complete(turn,timeout=timeout)
        except TimeoutError: end={'status':'timed_out'}
        answer,raw=final_json(client.events,turn)
        check=subprocess.run([sys.executable,'test_parser.py'],cwd=project,text=True,capture_output=True) if end['status']=='completed' and not resume_thread else None
        with Memory(project_database(project)) as m:
            state=card(m,episode)
            calls=[e['params']['item'] for e in client.events if e.get('method')=='item/completed' and e.get('params',{}).get('item',{}).get('type')=='mcpToolCall']
            hook_counts=dict(m.db.execute('SELECT event_name,count(*) FROM host_receipts WHERE session_id=? GROUP BY event_name',(thread,)))
        result={'thread_id':thread,'completion':end['status'],'output':answer,'independent_tests_pass':check.returncode==0 if check else None,
                'board_state':state['state'],'issues':state['issues'],'hook_counts':hook_counts,
                'memory_calls':len(calls),'memory_errors':sum(tool_error(c) for c in calls),
                'command_executions':sum(e.get('method')=='item/completed' and e.get('params',{}).get('item',{}).get('type')=='commandExecution' for e in client.events),
                'error_measurement':'Checks transport errors and JSON error records inside tool content; the host may omit isError.',
                'strict_json_only':raw.strip().startswith('{') and raw.strip().endswith('}'),
                'limits':'One invented repair; the host/model remains responsible for interpreting scope and selecting tools. This does not measure daily productivity.'}
        (output/'result.json').write_text(json.dumps(result,indent=2)+'\n');(output/'answer.txt').write_text(raw)
        if check: (output/'test-output.txt').write_text(check.stdout+check.stderr)
        print(json.dumps(result,indent=2))
    finally:client.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--project',required=True);p.add_argument('--output',required=True)
    p.add_argument('--resume-thread');p.add_argument('--timeout',type=int,default=600)
    a=p.parse_args();run(a.project,a.output,resume_thread=a.resume_thread,timeout=a.timeout)
