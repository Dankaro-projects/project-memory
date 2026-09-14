"""Invented sprint and continuation fixture. No external data or model calls."""
import argparse
import json
from pathlib import Path
from memory_module import Memory
from memory_module.install import setup
from memory_module.mcp import write, dispatch, tool_result
from memory_module import codex_host


def seed(project, *, extra=0):
    project=Path(project).resolve()
    if project.exists() and any(project.iterdir()):
        raise FileExistsError('Use a fresh empty directory for the planning fixture.')
    project.mkdir(parents=True,exist_ok=True)
    setup(project)
    (project/'CONTRACT.md').write_text('New uploads use strict UTF-8. Invalid bytes raise UnicodeDecodeError. Only a separately tagged legacy endpoint uses Latin-1. Production effect is unmeasured. Repair decode_payload(data, legacy=False) in parser.py and run test_parser.py. Do not change the public function signature or relax invalid-byte handling.\n')
    (project/'parser.py').write_text("def decode_payload(data, legacy=False):\n    return data.decode('utf-8', errors='replace')\n")
    (project/'test_parser.py').write_text("import unittest\nfrom parser import decode_payload\nclass ParserTests(unittest.TestCase):\n    def test_utf8(self): self.assertEqual(decode_payload('café'.encode()), 'café')\n    def test_invalid(self):\n        with self.assertRaises(UnicodeDecodeError): decode_payload(b'\\xff')\n    def test_legacy(self): self.assertEqual(decode_payload(b'caf\\xe9', legacy=True), 'café')\nif __name__=='__main__': unittest.main()\n",encoding='utf-8')
    with Memory(project/'.memory/project.sqlite') as m:
        authority=m.source('user:parser','Repair the parser','The user authorises a bounded parser repair.',(project/'CONTRACT.md').read_text(),'user',subject='code')
        source=m.document(str(project/'CONTRACT.md'),subject='code')
        evidence=[{'source_id':authority['id'],'reason':'The user defines the authorised scope and exception.'}, {'source_id':source['id'],'reason':'The current local contract defines the required behaviour.'}]
        def work(title,objective,criterion,state='ready',subject='code',**extra):
            return write(m,'plan',title,{'title':title,'objective':objective,'criterion':criterion,'subject':subject,'payload':{'state':state,'next_action':extra.pop('next_action','Implement the parser repair and run test_parser.py.'),'scope':'Preserve decode_payload(data, legacy=False). Keep UTF-8 strict; only the tagged legacy endpoint uses Latin-1. Do not claim a production improvement.','autonomy':'act','reason':'The user requests a bounded repair.',**extra},'actor':'fixture','evidence':evidence})
        sprint=write(m,'sprint','sprint',{'title':'Sprint 1 · Reliable continuation','objective':'Continue useful work without losing its conditions.','criterion':'Inspect actual execution, blockers and decision history.','subject':'general','payload':{'starts_on':'2026-09-14','ends_on':'2026-09-21','status':'active','reason':'This invented iteration exercises the work board.'},'actor':'fixture','evidence':evidence})
        repair=work('Preserve strict encoding and the legacy exception','Restore the requested parser behaviour.','The three tests pass without changing the signature.',sprint_id=sprint['episode_id'],priority='high')
        dependent=work('Review the release claim','Describe the measured result accurately.','The claim distinguishes local tests from production results.',subject='writing',sprint_id=sprint['episode_id'],depends_on=[{'episode_id':repair['episode_id'],'reason':'The release claim requires the parser result.'}],next_action='Review the parser evidence before writing the release claim.')
        proposal=work('Review a broader encoding policy','Consider whether the legacy endpoint still needs Latin-1.','The user decides whether the supported contract should change.','review',autonomy='suggest',owner='human',next_action='Ask the user to review the policy with current evidence.')
        for i in range(extra):work(f'Follow-up {i+1:02d}','Inspect one additional bounded case.','Record its observed outcome.','backlog')
        m.export_html(project/'board.html',include_bodies=True)
        result={'repair':repair,'dependent':dependent,'proposal':proposal,'sprint':sprint,
                'next_reply_characters':len(json.dumps(tool_result(dispatch(m,'memory_get',{'view':'next','id':repair['episode_id'],'session_id':'fixture','max_chars':6000})),ensure_ascii=False))}
    (project/'fixture.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--project',required=True);p.add_argument('--extra',type=int,default=0)
    a=p.parse_args();print(json.dumps(seed(a.project,extra=a.extra),indent=2))
