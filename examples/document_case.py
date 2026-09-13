"""Comparable document/retrieval case; runs against 0.3.0 or the current module."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

from memory_module import Memory, MemoryError, __version__, dumps
from memory_module.codex_host import initialize
from memory_module.mcp import dispatch, tool_result


def run(output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    vision = output/'VISION.md'
    original = '# Amberlake product vision\n\nThe product keeps customer data locally. A reviewed export is allowed only when a customer explicitly requests it.\n\n'
    original += 'The evaluation must retain the original scope and all documented exceptions.\n'*28
    vision.write_text(original)
    costs = {'tool_calls':0, 'response_characters':0}
    calls=[]
    with Memory.create(output/'memory.sqlite', 'Amberlake', ['Preserve quality and explicit acceptance.']) as m:
        initialize(m)
        def capture():
            if hasattr(m, 'document'):
                return m.document(str(vision))
            return m.source('local-markdown:'+vision.as_uri(),vision.name,
                'This file is captured verbatim. Importing it does not approve its proposals or change project requirements.',
                vision.read_text(), 'document')
        first=capture(); second=capture()
        ep=m.start('Amberlake export decision', 'Decide how to export data.', 'planning', 'The scope and exception remain explicit.', subject='code')
        refs=[{'source_id':first['id'], 'reason':'This captured vision states the local storage constraint and its export exception.'}]
        decision=m.record(ep['id'],'decision',{'decision':'Keep Amberlake data local and require an explicit request before exporting.',
            'why':'The vision requires local storage with a conditional export option.', 'expected':'Customers retain control of exports.',
            'uncertainty':'The export option has not been tested with customers.', 'alternatives':['Remove export.','Export automatically.'],
            'reconsider_when':'The product vision or customer evidence changes.'},expected_version=0,request_key='decision',actor='evaluation',evidence=refs)
        lesson=m.record(ep['id'],'lesson',{'when':'The Amberlake project keeps data locally.', 'do':'Preserve the export exception.',
            'because':'The original scope matters when the same evidence is reused. '*140,
            'exceptions':'Do not apply this lesson when a customer explicitly requests a reviewed export.'},
            expected_version=1,request_key='lesson',actor='evaluation',evidence=refs)
        def call(name,args):
            started=time.perf_counter()
            value=dispatch(m,name,args)
            size=len(dumps(tool_result(value)))
            calls.append({'tool':name,'arguments':args,'response_characters':size,'elapsed_ms':round((time.perf_counter()-started)*1000,3)})
            costs['tool_calls']+=1;costs['response_characters']+=size
            return value
        context=call('memory_context',{'query':'Amberlake','subject':'code','max_chars':2500})
        try:
            index=call('memory_get',{'view':'search','query':'Amberlake','subject':'code','max_chars':1500})
            discoverable=lesson['id'] in [r['id'] for r in index['records']]
        except MemoryError as exc:
            discoverable=False;index={'unavailable':str(exc)}
        expansion_before=dict(costs)
        try:
            expanded=call('memory_get',{'view':'records','ids':[decision['id'],lesson['id']],'max_chars':20000})['records']
        except MemoryError:
            expanded=[call('memory_get',{'view':'record','id':i,'max_chars':20000}) for i in [decision['id'],lesson['id']]]
        batch_cost={k:costs[k]-expansion_before[k] for k in costs}
        slices_before=dict(costs);offset=0;parts=[]
        while True:
            part=call('memory_get',{'view':'record','id':first['id'],'body_offset':offset,'max_chars':2500})
            parts.append(part['body'])
            if not part['body_more']:break
            if part['next_offset']<=offset:raise RuntimeError('Source expansion made no progress.')
            offset=part['next_offset']
        slice_cost={k:costs[k]-slices_before[k] for k in costs}
        vision.write_text('# Amberlake product vision\n\nCustomers may now request scheduled exports after review.\n')
        checks={
            'oversized_lesson_discoverable':discoverable,
            'full_records_preserve_exception':expanded[1]['payload']['exceptions']=='Do not apply this lesson when a customer explicitly requests a reviewed export.',
            'source_reconstructed_verbatim':''.join(parts)==original,
            'changed_file_flags_dependent_decision':m.read(decision['id'])['status']=='needs_review',
            'unchanged_import_creates_no_version':first['id']==second['id'],
            'lesson_remains_proposed':m.read(lesson['id']).get('lesson_status')=='proposed',
        }
        refresh=capture()
        checks['refresh_preserves_original_evidence']=m.read(first['id'],detail=True)['body']==original and m.read(decision['id'])['evidence'][0]['source_id']==first['id']
        # These are deterministic API tasks, not model input or human-effort measurements.
        result={'module_version':__version__,'checks':checks,'passed':sum(checks.values()),'total':len(checks),
            'context_omitted':context['omitted'],'discovery':index,'batch_expansion':batch_cost,'source_expansion':slice_cost,
            'successful_read_calls':costs,'source_versions_after_refresh':refresh['version'],
            'original_sha256':hashlib.sha256(original.encode()).hexdigest(),
            'model_tokens':None,'human_corrections':None,'long_term_maintenance':None,
            'method':'Fixed scripted tasks. Failed unsupported requests are reported as unavailable and excluded from successful read-call counts. Batch comparison is conditional on already knowing the IDs.'}
        (output/'calls.json').write_text(json.dumps(calls,indent=2)+'\n')
        (output/'result.json').write_text(json.dumps(result,indent=2)+'\n')
        m.export_html(output/'viewer.html',include_bodies=True)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True)
    args=parser.parse_args();print(json.dumps(run(args.output),indent=2))
