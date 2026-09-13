"""A real local tool run using invented code and writing fixtures. No LLM calls."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from memory_module import Memory, Hooks


def run(output):
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=False)
    script=output/'parser.py'
    script.write_text("def decode(value):\n    return value.decode('ascii')\n\nif __name__ == '__main__':\n    assert decode('café'.encode('utf-8')) == 'café'\n",encoding='utf-8')
    with Memory.create(output/'memory.sqlite','Memory module example',[
        'This project uses invented fixtures and actual local Python checks; it is not an AI learning evaluation.',
        'Use professional plain English. Keep code, writing and research records separate.',
        'Check recorded evidence before claiming success. A completed tool call is not necessarily a good outcome.',
    ]) as m:
        ep=m.start('Repair Unicode decoding','Decode the UTF-8 fixture correctly','parser repair',
                   'The café fixture decodes correctly and the process exits with code 0',subject='code')
        host=Hooks(m,'python-host');counter=0
        def append(episode,kind,payload,**kwargs):
            nonlocal counter;counter+=1
            return m.record(episode['id'],kind,payload,expected_version=m.episode(episode['id'])['version'],
                            request_key=f'example:{counter}',actor='scripted-reviewer',**kwargs)
        def source(key,title,text,subject='code'):
            return m.source(key,title,title,text,'tool',subject=subject)['id']
        initial=source('parser.py','Initial parser',script.read_text(encoding='utf-8'))
        def check(key,why,supersedes=None,evidence=None):
            result=host.run(lambda:subprocess.run([sys.executable,str(script)],capture_output=True,text=True,encoding='utf-8'),
                summarize=lambda p:f'Python process returned exit code {p.returncode}.',
                episode_id=ep['id'],operation='test',expected_version=m.episode(ep['id'])['version'],request_key=key,
                decision={'decision':'Run the Unicode fixture.','why':why,'expected':'The fixture passes.',
                          'reconsider_when':'Python returns a nonzero exit code.','review_after':m.now(),
                          'follow_up_owner':'python-host','model':'scripted-example'},
                supersedes=supersedes,evidence=evidence)
            process=result['result']
            output_text=json.dumps({'returncode':process.returncode,'stdout':process.stdout,'stderr':process.stderr},ensure_ascii=False)
            proof=source(key+'-output','Python fixture output',output_text)
            append(ep,'outcome',{'observed':f'Python returned {process.returncode}.',
                'assessment':'good' if process.returncode==0 else 'bad','assessment_reason':'Compared the actual exit code with the fixed success criterion.',
                'severity':'none' if process.returncode==0 else 'minor','attribution':'Only this local fixture was checked.',
                'failure_type':'none' if process.returncode==0 else 'encoding'},decision_id=result['ticket']['decision_id'],
                evidence=[{'source_id':proof,'reason':'Actual process output.'}])
            return result['ticket']['decision_id'],proof,process.returncode
        first,failed,first_code=check('first-test','Establish the current fixture result.',evidence=[{'source_id':initial,'reason':'Code under test.'}])
        old_revision=hashlib.sha256(script.read_bytes()).hexdigest()
        review=host.capture(trigger='review_completed',episode_id=ep['id'],expected_version=m.episode(ep['id'])['version'],
            request_key='parser-review',payload={'target':'parser.py','revision':old_revision,'summary':'The decoder uses ASCII for a UTF-8 input.',
            'findings':[{'location':'decode','issue':'The input contains é, which the ASCII decoder rejects.','severity':'major'}]},
            evidence=[{'source_id':initial,'reason':'The decoder selection in the stored source.'},{'source_id':failed,'reason':'Actual UnicodeDecodeError.'}])
        repair=host.run(lambda:script.write_text(script.read_text(encoding='utf-8').replace("decode('ascii')","decode('utf-8')"),encoding='utf-8'),
            summarize=lambda n:f'Wrote {n} characters to parser.py.',episode_id=ep['id'],operation='edit',
            expected_version=m.episode(ep['id'])['version'],request_key='repair',supersedes=first,
            decision={'decision':'Use UTF-8 for this input.','why':'The input fixture is encoded as UTF-8.',
                      'expected':'The next fixture run passes.','reconsider_when':'The input encoding contract changes.',
                      'review_after':m.now(),'follow_up_owner':'python-host','model':'scripted-example'},
            evidence=[{'source_id':failed,'reason':'Failure motivating this change.'}],
            links=[{'event_id':review['id'],'reason':'Review identified the decoder mismatch.'}])
        fixed=source('parser.py','Corrected parser',script.read_text(encoding='utf-8'))
        second,passed,second_code=check('second-test','Check the repaired code against the same fixture.',
                                      supersedes=repair['ticket']['decision_id'],evidence=[{'source_id':fixed,'reason':'Corrected code under test.'}])
        append(ep,'outcome',{'observed':f'The test after the edit returned {second_code}.','assessment':'good' if second_code==0 else 'bad',
            'assessment_reason':'Actual post-edit test against the original criterion.','severity':'none','attribution':'The edit changed the decoder; only this fixture was checked.'},
            decision_id=repair['ticket']['decision_id'],evidence=[{'source_id':passed,'reason':'Post-edit fixture output.'}])
        lesson=host.capture(trigger='lesson_proposed',episode_id=ep['id'],expected_version=m.episode(ep['id'])['version'],
            request_key='parser-lesson',payload={'when':'A byte input has a documented UTF-8 encoding.','do':'Decode it explicitly as UTF-8.',
            'because':'The ASCII decoder failed this fixture; the UTF-8 decoder passed.','exceptions':'Use the specified encoding when it differs; do not assume every byte stream is UTF-8.'},
            evidence=[{'source_id':failed,'reason':'Failure before repair.'},{'source_id':passed,'reason':'Result after repair.'}])
        append(ep,'lesson_review',{'lesson_id':lesson['id'],'status':'accepted','reason':'The fixture supports this narrow encoding condition; no broader improvement is claimed.'},
            evidence=[{'source_id':passed,'reason':'Checked local result.'}],links=[{'event_id':lesson['id'],'reason':'Scoped lesson under review.'}])
        append(ep,'episode_status',{'status':'settled','reason':'The fixture and the edit consequences were checked.'})
        writing=m.start('Correct a completion claim','Distinguish a proposal from completed work','copy review','Describe only the work actually completed',subject='writing')
        writing_proof=source('writing-fixture','Invented writing correction fixture',
            'Before: The feature improves accuracy.\nAfter: The feature is intended to improve accuracy; it has not been evaluated.',subject='writing')
        host.capture(trigger='user_correction',episode_id=writing['id'],expected_version=0,request_key='writing-correction',
            payload={'before':'The feature improves accuracy.','after':'The feature is intended to improve accuracy; it has not been evaluated.',
                     'reason':'The original statement described an untested benefit as established.','scope':'Claims about untested features in this example.'},
            evidence=[{'source_id':writing_proof,'reason':'Invented fixture for demonstrating writing capture, not an actual user correction.'}])
        research=m.start('Inspect the encoding evidence','Record what the local evidence establishes','evidence review','Claims match the captured fixture outputs',subject='research')
        host.capture(trigger='research_completed',episode_id=research['id'],expected_version=0,request_key='research-result',
            payload={'question':'Does this test demonstrate improved AI judgment?','queries':['Stored Python fixture output'],
                     'findings':'It demonstrates that the specific code fixture failed and passed after an edit.','gaps':'No AI model or independent case set was evaluated.'},
            evidence=[{'source_id':passed,'reason':'The measured fixture result supports a narrow conclusion.'}],
            links=[{'event_id':lesson['id'],'reason':'Check the scope of the conclusion drawn from this lesson.'}])
        waiting=m.start('Check a delayed consequence','Keep an unresolved result visible','follow-up example','External result is checked when available',subject='general')
        host.before_action(episode_id=waiting['id'],operation='execute',expected_version=0,request_key='interrupted-example',
            decision={'decision':'Prepare an example action without executing it.','why':'Demonstrate reconciliation after an interruption.',
                      'expected':'The host will establish whether the action ran.','reconsider_when':'The host reports its execution state.',
                      'review_after':m.now(),'follow_up_owner':'example-host'})
        m.export_html(output/'memory-viewer.html',include_bodies=True)
        (output/'history.md').write_text(m.history(),encoding='utf-8')
        result={'first_exit_code':first_code,'second_exit_code':second_code,'metrics':m.metrics(),
                'pending':m.pending(),'viewer':'memory-viewer.html','limit':'Scripted fixtures and actual Python processes; no model learning or token savings measured.'}
        (output/'result.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
        return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',default='example-run')
    result=run(parser.parse_args().output)
    print(json.dumps({k:result[k] for k in ['first_exit_code','second_exit_code','viewer','limit']},indent=2))
