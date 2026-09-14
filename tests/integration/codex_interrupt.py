"""Interrupt and resume an actual Codex tool call after a checked side effect."""
import argparse
import json
from pathlib import Path
import queue
import time
from tests.integration.codex_app_client import CodexClient, project_database
from tests.integration.codex_documents import call_failed
from memory_module import Memory
from memory_module.codex_host import status


def run(project,output,planned=False):
    project=Path(project).resolve();output=Path(output);output.mkdir(parents=True,exist_ok=False)
    marker=project/(output.name+'-marker.txt')
    if marker.exists():raise FileExistsError('Use a fresh project; the marker must not exist.')
    client=CodexClient(project,output/'events.jsonl')
    try:
        thread=client.start()
        prompt='''This is a real interruption test. Create a code episode through memory_write start with criterion "The marker is written once and is reconciled after interruption". Store this instruction as a source, then record a decision with its evidence, explicit uncertainty and alternatives, binding it to the memory session ID. Then run exactly one shell command: python3 -u -c "from pathlib import Path; import time; p=Path('interruption-marker.txt'); p.open('a').write('once\\n'); print('marker written', flush=True); time.sleep(40)". Wait for it; the test harness will interrupt you. Do not rerun the command.'''
        prompt=prompt.replace('interruption-marker.txt',marker.name)
        if planned:
            prompt=prompt.replace('Create a code episode through memory_write start with criterion', 'Use memory_get schema id plan and checkpoint, then create a code work plan through memory_write plan with criterion')
            prompt+=' The plan owns this session and permits only this fixture. Include an explicit intent checkpoint with the observed prompt ID, effect new_work and the requirements to write once, inspect before retrying and preserve uncertainty. Do not request a model review.'
        turn=client.turn(thread,prompt)
        deadline=time.monotonic()+240;interrupted=False
        while time.monotonic()<deadline:
            if marker.exists():
                client.request('turn/interrupt',{'threadId':thread,'turnId':turn});interrupted=True;break
            try:client.receive(timeout=.2)
            except queue.Empty:pass
        if not interrupted:raise TimeoutError('The command did not create its marker.')
        # Interrupt completion can arrive while the interrupt response is read.
        if not any(e.get('method')=='turn/completed' and e['params']['turn']['id']==turn for e in client.events):client.complete(turn)
        with Memory(project_database(project)) as m:
            before=status(m,session_id=thread,limit=20)
            (output/'after-interrupt.json').write_text(json.dumps(before,indent=2)+'\n')
        client.close()
        # A new host process resumes the same actual Codex thread.
        client=CodexClient(project,output/'resume.jsonl')
        client.request('thread/resume',{'threadId':thread,'cwd':str(project),'approvalPolicy':'never','sandbox':'workspace-write','excludeTurns':True})
        resume_prompt='''Resume the interrupted task. Do not rerun the marker-writing command. Use memory_get status with your session ID and limit 3 to inspect capture. Read interruption-marker.txt with the shell, record the actual text as a source, and reconcile any unconfirmed original tool receipt as completed only in the sense that its marker was written; explicitly state that the sleeping process completion is unknown. Use resolution unknown if the process completion cannot be established. Record the outcome against the criterion, using unknown where uncertainty remains. Finish with the observed marker count and any unresolved execution state.'''
        if planned:
            resume_prompt='''Resume this same work. Never rerun the marker-writing command. Inspect status and coverage for your session, read interruption-marker.txt once, and record the observed marker count as evidence. A written marker proves that side effect only. Reconcile an absent final result as unknown unless you verify process completion independently. Record an outcome with completion blocked if uncertainty remains. Acknowledge this recovery request with an unchanged intent checkpoint for the existing plan; include it with the outcome write. Do not start a reviewer. Finish with the observed count and the remaining uncertainty.'''
        turn2=client.turn(thread,resume_prompt.replace('interruption-marker.txt',marker.name))
        finished=client.complete(turn2)
        with Memory(project_database(project)) as m:
            after=status(m,session_id=thread,limit=20)
            (output/'after-resume.json').write_text(json.dumps(after,indent=2)+'\n')
            hooks={r[0]:r[1] for r in m.db.execute('SELECT event_name,count(*) FROM host_receipts WHERE session_id=? GROUP BY event_name',(thread,))}
            m.export_html(output/'viewer.html')
        calls=[e['params']['item'] for e in client.events if e.get('method')=='item/completed' and e['params']['item'].get('type')=='mcpToolCall']
        result={'thread_id':thread,'interrupt_turn':turn,'resume_turn':turn2,'marker_lines':marker.read_text().splitlines(),
            'resume_adapter_calls':len(calls),'resume_adapter_errors':sum(call_failed(c) for c in calls),
            'not_repeated':marker.read_text()=='once\n','hook_counts':hooks,'unconfirmed_after_interrupt':before['unconfirmed_total'],
            'unconfirmed_after_resume':after['unconfirmed_total'],'resume_status':finished['status'],
            'limits':'The marker confirms the side effect. Interruption is not proof of rollback or of process completion. Unknown execution remains visible.'}
        (output/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2),flush=True)
    finally:client.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--project',required=True);p.add_argument('--output',required=True);p.add_argument('--planned',action='store_true');a=p.parse_args();run(a.project,a.output,a.planned)
