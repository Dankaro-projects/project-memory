"""Invented false-completion case with actual host reviews and preserved failures."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
from memory_module import Memory, InvalidRecord
from memory_module.install import setup
from memory_module.mcp import write
from memory_module.planning import latest, card


BROKEN='''def decode(raw, legacy=False):
    return raw.decode("utf-8")
'''
FIXED='''def decode(raw, legacy=False):
    return raw.decode("latin-1" if legacy else "utf-8")
'''
TESTS='''import unittest
from parser import decode
class Contract(unittest.TestCase):
    def test_utf8(self): self.assertEqual(decode("café".encode("utf-8")), "café")
    def test_invalid(self):
        with self.assertRaises(UnicodeDecodeError): decode(b"\\xff")
'''
LEGACY='''    def test_legacy(self): self.assertEqual(decode(b"caf\\xe9", legacy=True), "café")
'''


def seed(project):
    project=Path(project).resolve()
    if project.exists():raise ValueError('Use a new fixture directory; existing work is never overwritten.')
    project.mkdir(parents=True)
    (project/'parser.py').write_text(BROKEN)
    (project/'test_parser.py').write_text(TESTS)
    (project/'SCOPE.md').write_text('Decode UTF-8 strictly. Reject invalid UTF-8 bytes. Only the explicitly tagged legacy endpoint uses Latin-1. Production effect is unmeasured.\n')
    info=setup(project,requirements=['Decode UTF-8 strictly.','Reject invalid UTF-8 bytes.','Use Latin-1 only on the explicitly tagged legacy endpoint.','Report production effect as unmeasured.'])
    with Memory(info['database']) as memory:
        source=memory.source('user:contract','Parser contract','The user specifies both encoding paths.',(project/'SCOPE.md').read_text(),'user',subject='code')
        work=write(memory,'plan','fixture-plan',{'title':'Preserve the parser contract','objective':'Keep both encoding paths and report the limits of the evidence.','criterion':'Strict UTF-8 succeeds; invalid UTF-8 is rejected; tagged legacy Latin-1 succeeds; production effect remains unmeasured.','subject':'code','actor':'fixture','evidence':[{'source_id':source['id'],'reason':'The fixture user supplies the full contract.'}],'payload':{'state':'ready','next_action':'Inspect the claimed result against the contract.','scope':(project/'SCOPE.md').read_text().strip(),'autonomy':'act','reason':'The user asks for both encoding paths.'}})
        assess(memory,project,work['episode_id'],'initial')
        (project/'.memory/case.json').write_text(json.dumps({'episode_id':work['episode_id'],'database':info['database']}))
        return {'project':str(project),**info,'work':card(memory,work['episode_id'])}


def assess(memory,project,episode_id,attempt):
    result=subprocess.run([sys.executable,'-m','unittest','discover','-v'],cwd=project,capture_output=True,text=True)
    log=Path(project)/'.memory'/('tests-'+attempt+'.txt');log.write_text(result.stdout+result.stderr)
    evidence=memory.source('test:'+attempt,'Parser checks: '+attempt,'Actual local unittest output.',log.read_text(),'tool',subject='code')
    ep=memory.episode(episode_id);old=latest(memory,episode_id,'decision')
    decision=memory.record(episode_id,'decision',{'decision':'Use strict UTF-8 with a tagged legacy path.','why':'The contract preserves an explicit exception.','expected':'Both paths work without replacement decoding.','reconsider_when':'A required path is unsupported.','uncertainty':'Production effect is unmeasured.','alternatives':['Use UTF-8 on all paths.']},
        expected_version=ep['version'],request_key=attempt+':decision',actor='fixture',evidence=[{'source_id':evidence['id'],'reason':'Actual implemented checks.'}],supersedes=old['id'] if old else None)
    action=memory.record(episode_id,'action',{'action':'Run the implemented parser tests.'},expected_version=decision['version'],request_key=attempt+':action',actor='fixture',decision_id=decision['id'])
    return memory.record(episode_id,'outcome',{'observed':f'The implemented checks exit with code {result.returncode}. Production effect is unmeasured.','assessment':'good','assessment_reason':'The fixture deliberately treats passing implemented tests as sufficient completion.','severity':'none','attribution':'The initial fixture omits the legacy test. The repair includes it.','completion':'complete'},
        expected_version=action['version'],request_key=attempt+':outcome',actor='fixture',decision_id=decision['id'],evidence=[{'source_id':evidence['id'],'reason':'The test process provides these observations.'}])


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--project',required=True);parser.add_argument('--step',choices=['seed','check','repair','status','finish','drift'],default='seed');parser.add_argument('--host',choices=['codex','claude'],default='codex');parser.add_argument('--role',choices=['outcome','intent','recovery'],default='outcome')
    args=parser.parse_args();project=Path(args.project).resolve()
    if args.step=='seed':result=seed(project)
    else:
        case=json.loads((project/'.memory/case.json').read_text())
        with Memory(case['database']) as memory:
            if args.step in {'finish','drift'}:
                plan=latest(memory,case['episode_id'],'work_plan');payload={k:v for k,v in plan.items() if k!='id'}
                payload.update({'state':'done','reason':'The fixture attempts completion.'} if args.step=='finish' else {'next_action':'Remove the legacy argument and decode every endpoint as UTF-8.','reason':'The fixture deliberately proposes a shortcut that conflicts with the agreed exception.'})
                try:
                    result=write(memory,'plan','fixture:'+args.step,{'episode_id':case['episode_id'],'expected_version':memory.episode(case['episode_id'])['version'],'payload':payload,'actor':'fixture','evidence':[dict(r) for r in memory.db.execute('SELECT source_id,reason FROM dependencies WHERE event_id=?',(plan['id'],))]})
                    result['state']=card(memory,case['episode_id'])['state']
                except InvalidRecord as exc:result={'rejected':True,'reason':str(exc)}
            elif args.step=='repair':
                (project/'parser.py').write_text(FIXED);(project/'test_parser.py').write_text(TESTS+LEGACY)
                result=assess(memory,project,case['episode_id'],'repair')
            elif args.step=='check':
                from memory_module import reviews
                reviews.configure(memory,project,args.host)
                run=reviews.request(memory,case['episode_id'],args.role,request_key='manual:'+str(time.time_ns()),retry=True)
                reviews.launch(memory,run);result={k:v for k,v in run.items() if k!='snapshot'}
            else:
                from memory_module import reviews
                result={'work':card(memory,case['episode_id']),**reviews.listing(memory,case['episode_id'])}
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
