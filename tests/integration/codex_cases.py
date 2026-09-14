"""Small paired live Codex tasks. Fixtures and rubrics are fixed before each run."""
import argparse
import json
from pathlib import Path
import re
import time
from tests.integration.codex_app_client import CodexClient, project_database
from memory_module import Memory

FACTS = {
 'encoding':'Current ingest contract: new files are UTF-8 byte streams. Decode strictly. Invalid bytes must raise UnicodeDecodeError. Latin-1 is only for the separately tagged legacy endpoint; do not apply it to new files.',
 'release':'The test measured 20% lower median latency over 40 simulated requests. Accuracy was not measured. Production performance was not evaluated. Describe the result as a simulation, not a deployed accuracy improvement.',
 'limits':'Current API contract dated 2026-09-12: standard requests allow 3 attachments. The separately enabled legacy endpoint allows 5. This replaces the 2024 standard limit of 5. Do not apply the legacy exception to standard requests.'}
TASKS = {
 'encoding':{'subject':'research','task':'Review parser.py against the current ingest contract. Return JSON with defect (boolean), encoding (text), invalid_bytes (text), and reason (text). Read parser.py. Do not edit it.','checks':['defect','utf8','strict_error']},
 'release':{'subject':'writing','task':'Correct this sentence using the recorded test evidence: "The release improved production accuracy by 20%." Return JSON with text (the corrected sentence) and limitation (what remains unmeasured).','checks':['latency','20_percent','simulation','accuracy_unmeasured']},
 'limits':{'subject':'research','task':'Determine the current standard attachment limit and its exception as of 2026-09-12. Return JSON with standard_limit (integer), legacy_limit (integer), and date (text). Recheck any stale source.','checks':['standard3','legacy5','current_date']}}


def grade(name,value):
    text=json.dumps(value).lower()
    if name=='encoding':return {'defect':value.get('defect') is True,'utf8':re.sub(r'[^a-z0-9]','',str(value.get('encoding','')).lower())=='utf8','strict_error':any(s in str(value.get('invalid_bytes','')).lower() for s in ['raise','reject','error'])}
    if name=='release':return {'latency':'latency' in str(value.get('text','')).lower(),'20_percent':'20' in str(value.get('text','')),'simulation':'simulat' in str(value.get('text','')).lower(),'accuracy_unmeasured':'accuracy' in str(value.get('limitation','')).lower() and any(s in str(value.get('limitation','')).lower() for s in ['unmeasured','not measured','not evaluated'])}
    return {'standard3':value.get('standard_limit')==3,'legacy5':value.get('legacy_limit')==5,'current_date':'2026-09-12' in str(value.get('date',''))}


def final_json(events,turn):
    messages=[e['params']['item']['text'] for e in events if e.get('method')=='item/completed' and e['params'].get('turnId')==turn and e['params']['item'].get('type')=='agentMessage' and e['params']['item'].get('phase')=='final_answer']
    text=messages[-1] if messages else ''
    try:return json.loads(text.strip().removeprefix('```json').removeprefix('```').removesuffix('```').strip()),text
    except ValueError:return {},text


def execution_checks(name,condition,events,research_reads):
    checks={}
    if condition=='without_memory' or name=='limits':
        checks['required_source_read']=research_reads>0
    if name=='encoding':
        checks['parser_read']=any(e.get('method')=='item/completed' and
            e['params']['item'].get('type')=='commandExecution' and
            e['params']['item'].get('exitCode')==0 and
            'parser.py' in e['params']['item'].get('command','') and
            'def decode_new_file' in e['params']['item'].get('aggregatedOutput','') for e in events)
    return checks


def fixture_file(path, text):
    if path.exists():
        if path.read_text()!=text:raise FileExistsError(f'Fixture differs; preserve and inspect {path} before rerunning.')
    else:path.write_text(text)


def run(project,output,hook_context=False):
    project=Path(project).resolve();output=Path(output).resolve();output.mkdir(parents=True,exist_ok=False)
    # These fixture reads are logged independently of the model's own claims.
    fixture=project/'research_fixture.py'
    fixture_file(fixture,'''import json,sys
from pathlib import Path
facts=json.loads(Path('fixture-facts.json').read_text())
with Path('research-access.jsonl').open('a') as f:f.write(json.dumps({'case':sys.argv[1]})+'\\n')
print(facts[sys.argv[1]])
print('\\n'.join('Archived test note %d: This unrelated example is not a current contract.'%i for i in range(180)))
''')
    fixture_file(project/'fixture-facts.json',json.dumps(FACTS,indent=2))
    fixture_file(project/'parser.py',"def decode_new_file(value):\n    return value.decode('latin-1', errors='replace')\n")
    access=project/'research-access.jsonl'
    with Memory(project_database(project)) as m:
        for name,body in FACTS.items():
            stored=body if name!='limits' else '2024 standard attachment limit: 5. This stored source is overdue for review; consult the current contract.'
            if not m.db.execute('SELECT 1 FROM sources WHERE source_key=?',('paired-'+name,)).fetchone():
                m.source('paired-'+name,name,stored,stored,'document',subject=TASKS[name]['subject'],review_after='2020-01-01T00:00:00Z' if name=='limits' else '2099-01-01T00:00:00Z')
    results=[]
    for name in TASKS:
        # Alternate order to reduce a simple always-second condition effect.
        order=['without_memory','with_memory'] if name!='release' else ['with_memory','without_memory']
        for condition in order:
            case=output/(name+'-'+condition);case.mkdir()
            task=TASKS[name]
            prompt=task['task']+'\nUse only the provided local evaluation data. '
            if condition=='with_memory':
                prompt+=f'First use memory_context with query "{name}", subject "{task["subject"]}" and max_chars 2500. Reuse sufficient current evidence. If it is missing or stale, read the current research source using python3 research_fixture.py {name}.'
            else:
                prompt+=f'For this control condition, read the current research source using python3 research_fixture.py {name}. Do not call memory tools.'
            if condition=='with_memory' and hook_context:
                prompt=f'[memory:{task["subject"]}] {name}\n'+task['task']+f' Use the bounded context supplied by the host. Reuse sufficient current evidence; if missing, omitted or stale, read the current source using python3 research_fixture.py {name}.'
            prompt+=' Do not read or change the database directly, inspect other projects, edit the fixture, delegate, or make network calls. Return only the requested JSON. No additional record administration is needed in this bounded retrieval evaluation; the harness records the measured result.'
            (case/'prompt.txt').write_text(prompt)
            before=len(access.read_text().splitlines()) if access.exists() else 0
            client=CodexClient(project,case/'events.jsonl');started=time.perf_counter()
            try:
                thread=client.start();turn=client.turn(thread,prompt);end=client.complete(turn)
                value,raw=final_json(client.events,turn);checks=grade(name,value)
                corrections=0
                if not all(checks.values()):
                    corrections=1
                    correction='The answer is incomplete or unsupported. Inspect the required evidence and answer the original request. Do not infer an expected answer from this feedback or claim verification without executing the required checks.'
                    (case/'automated-correction.txt').write_text(correction)
                    turn=client.turn(thread,correction);end=client.complete(turn);value,raw=final_json(client.events,turn)
                final_checks=grade(name,value)
                token_events=[e['params']['tokenUsage']['total'] for e in client.events if e.get('method')=='thread/tokenUsage/updated']
                tokens=token_events[-1] if token_events else None
                calls=[e['params']['item'] for e in client.events if e.get('method')=='item/completed' and e['params']['item'].get('type')=='mcpToolCall']
                packets=[]
                for c in calls:
                    if c['tool']=='memory_context' and c.get('result'):
                        try:packets.append(json.loads(c['result']['content'][0]['text']))
                        except (KeyError,ValueError,IndexError):pass
                after=len(access.read_text().splitlines()) if access.exists() else 0
                evidence_checks=execution_checks(name,condition,client.events,after-before)
                report={'case':name,'condition':condition,'model':client.model,'thread_id':thread,'completion':end['status'],
                    'first_checks':checks,'final_checks':final_checks,'output':value,'research_reads':after-before,
                    'execution_checks':evidence_checks,'quality_met':all(final_checks.values()) and all(evidence_checks.values()),
                    'repeated_current_research':(after-before) if name!='limits' else 0,
                    'automated_correction_prompts':corrections,'human_corrections':None,'provider_usage':tokens,
                    'memory_context_characters':[p.get('used') for p in packets],
                    'adapter_errors':sum(c['status']=='failed' for c in calls),'elapsed_ms':round((time.perf_counter()-started)*1000),
                    'manual_steps_during_task':0}
                (case/'answer.txt').write_text(raw);(case/'result.json').write_text(json.dumps(report,indent=2)+'\n')
                results.append(report);print(json.dumps(report),flush=True)
            finally:client.close()
    report={'context_delivery':'host_hook' if hook_context else 'mcp_call','cases':results,'rubrics':{k:v['checks'] for k,v in TASKS.items()},'fixtures':FACTS,
        'limits':'Three synthetic local task pairs, one run per condition, same model and tools. Provider input tokens include repeated system/tool/history input, not unique context size. Automated rubric corrections are not human corrections. Stale-source refresh is necessary research, not waste. No statistical or competitor superiority claim.'}
    (output/'result.json').write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--project',required=True);p.add_argument('--output',required=True);p.add_argument('--hook-context',action='store_true');a=p.parse_args();run(a.project,a.output,a.hook_context)
