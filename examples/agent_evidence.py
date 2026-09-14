"""Produce a public aggregate from the named synthetic cases; keep raw host logs private."""
import argparse
import json
from pathlib import Path
import statistics
import time
from memory_module import Memory, reviews
from memory_module.planning import board
from memory_module.live import Viewer

CASES=['agents-codex','agents-claude','agents-native-hook','agents-native-hook-final',
       'agents-native-interrupt','agents-native-interrupt-final','agents-intent',
       'agents-native-claude','agents-native-claude-final','agents-mcp-outcome']


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--results',type=Path,default=Path('results'));parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    cases=[]
    for name in CASES:
        private=args.results/name/'.memory';case=json.loads((private/'case.json').read_text())
        with Memory(case['database'],read_only=True) as memory:
            runs=reviews.listing(memory,case['episode_id'],100)['runs']
            values=[{'id':r['id'],'host':r['host'],'role':r['role'],'state':r['state'],'metrics':r['metrics'],
                     'criterion_results':[c['result'] for c in r['report']['checks']] if r['report'] else None} for r in reversed(runs)]
            hooks=[dict(r) for r in memory.db.execute('SELECT event_name,count(*) AS count FROM host_receipts GROUP BY event_name')]
        item={'case':name,'checks':values,'hooks':hooks}
        host=private/'host.jsonl'
        if host.exists():
            inputs=[]
            for line in host.read_text().splitlines():
                event=json.loads(line)
                if event.get('method')=='thread/tokenUsage/updated':inputs.append(event['params']['tokenUsage'].get('last',{}).get('inputTokens'))
            item['native_input_tokens_per_notification']=inputs
            item['maximum_native_input_tokens']=max((v for v in inputs if isinstance(v,int)),default=None)
        host=private/'host-output.json'
        if host.exists():
            value=json.loads(host.read_text());item['native_host']={k:value.get(k) for k in ('is_error','duration_ms','num_turns','usage')}
        cases.append(item)
    path=args.results/'agents-codex/.memory/project.sqlite'
    timings={}
    with Memory(path,read_only=True) as memory:
        for name,operation in [('two_card_board',lambda:board(memory))]:
            samples=[]
            for _ in range(5):
                started=time.perf_counter();operation();samples.append(round((time.perf_counter()-started)*1000,2))
            timings[name]={'milliseconds':samples,'median_ms':statistics.median(samples)}
    with Viewer(path,'local-measurement-token') as server:
        samples=[]
        for _ in range(5):
            started=time.perf_counter();server.revision();samples.append(round((time.perf_counter()-started)*1000,2))
        timings['revision_with_artifact_hash']={'milliseconds':samples,'median_ms':statistics.median(samples)}
    report={'date':'2026-09-14','scope':'Invented encoding contract, actual Python tests, real local host accounts and browser interactions. These are not customer or production outcomes.',
            'baseline':json.loads((args.results/'agents-baseline-completion.json').read_text()),
            'updated':json.loads((args.results/'agents-updated-completion.json').read_text()),'cases':cases,
            'browser':json.loads((args.results/'workspace-browser/browser-validation.json').read_text()),
            'offline_browser':json.loads((args.results/'workspace-offline-browser/browser-validation.json').read_text()),
            'local_latency':timings,'runtime_dependencies':0,
            'unmeasured':{'daily_repeated_research':None,'daily_human_corrections':None,'daily_maintenance_effort':None,'production_quality':None,'competitor_comparison':None},
            'interpretation':'Provider reviewer usage aggregates several requests. Cached tokens remain part of model input. Character counts are not token measurements. Native Codex notification counts measure full inputs where available; the 10K target is unmet. Small-fixture latency is not a scale benchmark. Earlier failed runs are retained.'}
    args.output.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'output':str(args.output),'cases':len(cases),'checks':sum(len(c['checks']) for c in cases)}))


if __name__=='__main__':main()
