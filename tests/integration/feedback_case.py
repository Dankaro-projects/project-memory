"""Compare the same invented first-beta workflow in two source checkouts.

Run this file with PYTHONPATH pointing at either checkout. It prints measurements,
not a claim about human productivity or model tokens. No private feedback is read.
"""
import json
from pathlib import Path
import statistics
import tempfile
import time
from memory_module import Memory, BudgetTooSmall, dumps, __version__
from memory_module.install import setup
from memory_module import codex_host
from memory_module.mcp import dispatch, tool_result


def timed(fn):
    values=[];result=None
    for _ in range(5):
        before=time.perf_counter_ns();result=fn();values.append((time.perf_counter_ns()-before)/1e6)
    return result,{'samples_ms':values,'median_ms':statistics.median(values)}


def run():
    with tempfile.TemporaryDirectory(prefix='pm-feedback-case-') as temp:
        root=Path(temp).resolve()
        requirements=[f'Requirement {i+1}: '+('Keep the scope explicit and preserve the user-approved exception. '*4) for i in range(24)]
        info=setup(root,requirements=requirements)
        with Memory(info['database']) as m:
            for i in range(20):
                path=root/f'document-{i:02}.md'
                path.write_text(f'# Scope {i}\n'+('Keep UTF-8 strict; use Latin-1 only for the tagged legacy endpoint.\n'*60))
                m.document(str(path),subject='research')
            for i in range(11):m.source(f'research-{i}',f'Encoding research {i}','Use strict UTF-8 except on the tagged legacy endpoint.','The production effect has not been measured.','tool',subject='research')
            checks={};measurements={}
            before=time.perf_counter_ns()
            try:dispatch(m,'memory_context',{'query':'encoding','subject':'research','max_chars':6000})
            except BudgetTooSmall as exc:
                checks['small_budget_rejects_incomplete_constraints']=True
                checks['budget_error_has_recovery_route']=bool(getattr(exc,'details',{}).get('next_call'))
            measurements['small_budget_ms']=(time.perf_counter_ns()-before)/1e6
            args={'query':'encoding','subject':'research','max_chars':14000}
            first,timing=timed(lambda:dispatch(m,'memory_context',args));measurements['context']=timing
            seen={r['id']:r['signature'] for r in first['records'] if 'signature' in r}
            second=dispatch(m,'memory_context',{**args,'seen':seen})
            measurements['first_context_characters']=len(dumps(tool_result(first)))
            measurements['repeat_context_characters']=len(dumps(tool_result(second)))
            checks['first_context_preserves_every_requirement']=all(t in first['records'][0]['text'] for t in requirements)
            checks['discovered_records_remain_readable']=all(m.read(r['id']) for r in first['records'][1:])
            path=root/'document-00.md';old=m.document(str(path),subject='research')['id']
            path.write_text('# Changed scope\nKeep UTF-8 strict; the tagged legacy endpoint still uses Latin-1.\n')
            codex_host.capture(m,{'hook_event_name':'SessionStart','session_id':'trial'})
            count=m.db.execute('SELECT count(*) FROM sources').fetchone()[0]
            checks['hook_captures_changed_selected_document']=count==32
            manual_refreshes=0
            if count==31:m.document(str(path),subject='research');manual_refreshes=1
            checks['old_source_and_exception_survive']=m.read(old,detail=True)['body'].endswith('Keep UTF-8 strict; use Latin-1 only for the tagged legacy endpoint.\n')
            checks['capture_does_not_approve_requirements']=m.requirements==requirements
            measurements['explicit_document_refresh_commands']=manual_refreshes
            counter=iter(range(5))
            exported,timing=timed(lambda:m.export_html(root/f'view-{next(counter)}.html'))
            measurements['snapshot']=timing;measurements['snapshot_bytes']=exported['bytes']
            try:
                from memory_module.api import page
                from memory_module.live import html
            except ImportError:
                measurements['live_initial_bytes']=None;checks['live_view_available']=False
            else:
                records,timing=timed(lambda:page(m,{'view':'sources','limit':'25'}))
                measurements['live_page']=timing
                measurements['live_initial_bytes']=len(html())+len(dumps(records).encode())
                checks['live_view_available']=True
                checks['paged_view_covers_all_sources']=records['total']==32 and records['more']
            return {'version':__version__,'fixture':{'requirements':24,'initial_sources':31,'selected_markdown':20},'checks':checks,'measurements':measurements,
                    'unmeasured':['human reminders','human maintenance time','daily completion','model input tokens','competition'],
                    'limits':'Five warm local samples per timed function. Exact characters are MCP results, not full model inputs. Initial live bytes are HTML plus one source page, excluding health and later detail requests. This scripted case does not measure autonomous semantic coverage.'}


if __name__=='__main__':print(json.dumps(run(),indent=2))
