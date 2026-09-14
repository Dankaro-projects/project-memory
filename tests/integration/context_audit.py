"""Audit complete model inputs from actual Codex provider usage notifications."""
import argparse
import json
from pathlib import Path


def audit(directory, target=10000):
    cases=[]
    paths=set(Path(directory).rglob('events.jsonl'))|set(Path(directory).rglob('resume.jsonl'))
    for path in sorted(paths):
        inputs=[];unmeasured=0
        for line in path.read_text().splitlines():
            event=json.loads(line)
            if event.get('method')=='thread/tokenUsage/updated':
                usage=event['params']['tokenUsage']
                value=usage.get('last',{}).get('inputTokens')
                if type(value) is int and value>0:inputs.append(value)
                else:unmeasured+=1
        cases.append({'case':str(path.parent.relative_to(directory)),'log':path.name,
            'input_tokens_per_notification':inputs,'maximum_input_tokens':max(inputs) if inputs else None,
            'unmeasured_or_reset_notifications':unmeasured,
            'meets_target':bool(inputs) and not unmeasured and all(n<target for n in inputs)})
    return {'target_input_tokens_exclusive':target,'measurement':'Provider last.inputTokens includes instructions, tool definitions, history, task and memory. It is not an estimate from character counts.',
        'cases':cases,'meets_target':bool(cases) and all(c['meets_target'] for c in cases),
        'limitations':'Notifications may repeat reports or reset counters after compaction. Zero or missing counts are unmeasured and cannot establish a pass. Hidden host requests without usage notifications are outside this audit. The adapter cannot enforce a pre-request limit on the full native Codex prompt.'}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('directory',type=Path);p.add_argument('--output',type=Path);p.add_argument('--target',type=int,default=10000)
    a=p.parse_args();result=audit(a.directory,a.target);text=json.dumps(result,indent=2)+'\n'
    if a.output:
        with a.output.open('x') as f:f.write(text)
    print(text,end='')
    raise SystemExit(0 if result['meets_target'] else 1)
