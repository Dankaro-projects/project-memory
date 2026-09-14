"""Execute a failing baseline and repair, retaining both in an invented work history."""
import argparse,json,subprocess,sys
from pathlib import Path
from memory_module import Memory
from memory_module.mcp import write
from memory_module.planning import latest
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--project',required=True)
a=p.parse_args();project=Path(a.project).resolve()
fixture=json.loads((project/'fixture.json').read_text());ep=fixture['repair']['episode_id']
with Memory(project/'.memory/project.sqlite') as m:
 if m.episode(ep)['version'] != 1: raise RuntimeError('Use a fresh planning fixture; existing work must not be repeated.')
 evidence=[dict(r) for r in m.db.execute('SELECT source_id,reason FROM dependencies WHERE event_id=?',(fixture['repair']['id'],))]
 def record(kind,payload,**extra):return m.record(ep,kind,payload,expected_version=m.episode(ep)['version'],request_key='browser-exercise-'+str(m.episode(ep)['version']),actor='verification',evidence=evidence,**extra)
 def attempt(choice,why,expected,supersedes=None):
  d=record('decision',{'decision':choice,'why':why,'expected':expected,'reconsider_when':'The observed tests contradict the expectation.','uncertainty':'Production effect is unmeasured.','alternatives':['Change the public signature.']},supersedes=supersedes)
  record('action',{'action':'Run all three parser tests.'},decision_id=d['id']);return d
 baseline=attempt('Check the current parser against the recorded contract.','The existing decoder replaces invalid bytes and ignores the legacy flag.','The tests identify whether both conditions are preserved.')
 run=subprocess.run([sys.executable,'test_parser.py'],cwd=project,text=True,capture_output=True)
 assert run.returncode==1
 source=m.source('parser:baseline','Parser baseline','Two encoding checks fail.',run.stdout+run.stderr,'tool',subject='code');evidence.append({'source_id':source['id'],'reason':'The actual test output establishes the failure.'})
 record('outcome',{'observed':'Two of three tests fail: invalid bytes do not raise, and the legacy endpoint loses its encoding.','assessment':'bad','assessment_reason':'The current parser violates two explicit conditions.','severity':'major','attribution':'The existing decoder replaces errors and ignores the legacy flag.','completion':'partial','failure_type':'encoding_contract'},decision_id=baseline['id'])
 repair=attempt('Select Latin-1 only when legacy is true; otherwise decode strict UTF-8.','The baseline failure identifies the exact lost conditions.','All three tests pass with the public signature intact.',baseline['id'])
 (project/'parser.py').write_text("def decode_payload(data, legacy=False):\n    return data.decode('latin-1' if legacy else 'utf-8')\n")
 run=subprocess.run([sys.executable,'test_parser.py'],cwd=project,text=True,capture_output=True);assert run.returncode==0
 source=m.source('parser:recovery','Parser recovery','All three encoding checks pass.',run.stdout+run.stderr,'tool',subject='code');evidence.append({'source_id':source['id'],'reason':'Independent execution confirms the local recovery.'})
 record('outcome',{'observed':'All three parser tests pass; the signature and legacy exception remain intact. Production effect remains unmeasured.','assessment':'good','assessment_reason':'The local completion criterion is satisfied.','severity':'none','attribution':'The decoder now selects the explicitly supported encoding.','completion':'complete'},decision_id=repair['id'])
 plan=latest(m,ep,'work_plan');plan.pop('id');plan.update(state='done',next_action='No implementation remains; inspect the local evidence before any production claim.',reason='The local repair meets the recorded completion criterion.')
 write(m,'plan','browser-exercise-done',{'episode_id':ep,'expected_version':m.episode(ep)['version'],'payload':plan,'actor':'verification','evidence':evidence})
 for i in range(11):record('note',{'text':f'Additional review note {i+1}: production effect remains unmeasured.'})
 m.export_html(project/'completed-board.html',include_bodies=True)
 print(json.dumps({'baseline_exit':1,'recovery_exit':0,'episode':ep,'decision':repair['id'],'events':m.episode(ep)['version']}))
