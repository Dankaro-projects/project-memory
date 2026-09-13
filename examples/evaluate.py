"""Comparable local tasks against a selected module checkout; no model-quality claim."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
import time
from memory_module import Memory, Hooks, InvalidRecord, dumps


def run(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    with Memory.create(output/'memory.sqlite', 'Comparable cases', ['Preserve evidence and conditions.']) as m:
        def source(key, text, subject='code'):
            return m.source(key, key, text, text, 'tool', subject=subject)['id']
        ep = m.start('Repair the decoder', 'Read the agreed byte stream', 'repair', 'Both agreed fixtures pass', subject='code')
        def record(kind, payload, **kwargs):
            return m.record(ep['id'], kind, payload, expected_version=m.episode(ep['id'])['version'],
                            request_key=str(m.episode(ep['id'])['version']), actor='case-runner', **kwargs)
        contract = source('encoding-contract', 'Decode documented UTF-8 input; preserve errors for invalid bytes.')
        refs = [{'source_id':contract, 'reason':'The fixture contract.'}]
        decision = record('decision', {'decision':'Decode this byte stream as ASCII.', 'why':'Initial implementation assumes ASCII.',
            'expected':'Both fixtures pass.', 'uncertainty':'The accented fixture may disprove the encoding assumption.',
            'alternatives':['Use UTF-8 when the contract requires it.'], 'reconsider_when':'A fixture fails.', 'case_id':'decoder', 'condition':'comparable'})
        record('action', {'action':'test'}, decision_id=decision['id'])
        code = "assert 'caf\u00e9'.encode('utf-8').decode('ascii') == 'caf\u00e9'"
        first = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True)
        first_source = source('first-run', f'Exit {first.returncode}. {first.stderr}')
        failed = record('outcome', {'observed':'The cobalt fixture raised UnicodeDecodeError.', 'assessment':'bad',
            'assessment_reason':'Actual fixture failure.', 'severity':'minor', 'attribution':'ASCII rejects UTF-8 accented bytes.'},
            decision_id=decision['id'], evidence=[{'source_id':first_source, 'reason':'Actual process result.'}])
        revised = record('decision', {'decision':'Use UTF-8 for the documented input.', 'why':'The contract specifies UTF-8.',
            'expected':'Accented input passes and invalid bytes still raise.', 'uncertainty':'Only these fixtures are covered.',
            'alternatives':['Reject inputs that violate the documented contract.'], 'reconsider_when':'The encoding contract changes.',
            'case_id':'decoder','condition':'comparable'}, supersedes=decision['id'], evidence=refs,
            links=[{'event_id':failed['id'],'reason':'The failed attempt motivates the revision.'}])
        record('action', {'action':'test'}, decision_id=revised['id'])
        code2 = "assert 'caf\u00e9'.encode('utf-8').decode('utf-8') == 'caf\u00e9'\ntry:\n b'\\xff'.decode('utf-8')\nexcept UnicodeDecodeError:\n pass\nelse:\n raise AssertionError('Invalid bytes were accepted')"
        second = subprocess.run([sys.executable, '-c', code2], capture_output=True, text=True)
        proof = source('second-run', f'Exit {second.returncode}. Both fixtures ran.')
        passed = record('outcome', {'observed':'The cobalt fixtures passed after recovery.', 'assessment':'good',
            'assessment_reason':'Both independent assertions ran.', 'severity':'none', 'attribution':'The documented encoding was used.'},
            decision_id=revised['id'], evidence=[{'source_id':proof,'reason':'Actual execution.'}])
        results = {}
        packet=m.context('cobalt', subject='code', budget=5000)
        results['outcome_recall'] = {'passed':{failed['id'],passed['id']} <= {r['id'] for r in packet['records']},
            'context_characters':len(dumps(packet)), 'expected_outcomes':2,
            'retrieved_outcomes':sum(r['kind']=='outcome' for r in packet['records'])}
        m.source('encoding-contract', 'Changed contract', 'The new producer supplies Latin-1.', 'Use Latin-1 for the new producer.', 'tool',subject='code')
        results['stale_decision_dependency'] = {'passed':m.read(passed['id'])['status']=='needs_review', 'actual':m.read(passed['id'])['status']}
        research=m.start('Research capture','Keep justified findings','research','Evidence is required',subject='research')
        try:
            Hooks(m,'host').capture(trigger='research_completed', episode_id=research['id'], payload={'question':'Which encoding?', 'findings':'Use UTF-8.', 'gaps':'Producer unverified.'},expected_version=0,request_key='research-without-evidence',evidence=[])
            rejected=False
        except InvalidRecord: rejected=True
        results['research_requires_evidence']={'passed':rejected}
        results['recovery_completion']={'passed':first.returncode!=0 and second.returncode==0,'before_exit':first.returncode,'after_exit':second.returncode,'fixtures_after':2}
        outcome_tokens = None
        result={'cases':results, 'quality_checks_passed':sum(x['passed'] for x in results.values()), 'quality_checks_total':len(results),
            'completion':{'attempted':4,'completed':4,'skipped':0}, 'context':{'characters':len(dumps(packet)), 'model_tokens':outcome_tokens},
            'repeated_research':{'external_fetches':0,'meaning':'These are local execution and retrieval cases; no real research savings are inferred.'},
            'corrections':{'human':None,'automated_code_repairs':1}, 'maintenance':{'runtime_dependencies':0,'manual_steps_in_runner':0,'elapsed_ms':round((time.perf_counter()-started)*1000)},
            'python':platform.python_version(), 'module':str(Path(sys.modules['memory_module'].__file__).resolve()),
            'source_hashes':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(sys.modules['memory_module'].__file__).parent.glob('*.py')},
            'limitation':'Deterministic local tasks test recall, lineage and capture, not independent human judgment or model improvement.'}
        (output/'result.json').write_text(json.dumps(result,indent=2)+'\n')
        (output/'history.md').write_text(m.history())
        m.export_html(output/'viewer.html')
        print(json.dumps(result,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True)
    run(parser.parse_args().output)
