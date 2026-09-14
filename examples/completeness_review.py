"""Compare an omitted exception with its repair using one bounded reviewer each."""
import argparse
import json
from pathlib import Path
from examples.agent_check_case import seed, assess, FIXED, TESTS, LEGACY
from memory_module import Memory, reviews


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--project',type=Path,required=True);parser.add_argument('--host',choices=['codex','claude'],default='codex');args=parser.parse_args()
    project=args.project.resolve();info=seed(project);ep=info['work']['id'];results=[]
    with Memory(info['database']) as memory:
        reviews.configure(memory,project,args.host)
        for phase in ['omitted_exception','repaired_exception']:
            if phase=='repaired_exception':
                (project/'parser.py').write_text(FIXED);(project/'test_parser.py').write_text(TESTS+LEGACY)
                assess(memory,project,ep,'repair')
            run=reviews.request(memory,ep,request_key=phase,max_seconds=90)
            reviews.execute(memory,run['id'])
            result=reviews.read(memory,run['id']);result.pop('snapshot');results.append(result)
            print(json.dumps({'phase':phase,'state':result['state'],'metrics':result['metrics'],'report':result['report'],'error':result['error']}),flush=True)
    (project/'.memory/comparison.json').write_text(json.dumps(results,indent=2)+'\n')


if __name__=='__main__':main()
