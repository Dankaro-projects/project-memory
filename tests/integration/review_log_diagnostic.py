"""Summarise existing private host logs without retrying a review or its work."""
import argparse
import json
from pathlib import Path
from memory_module.review_logs import ReviewLog


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('run_directories',nargs='+',type=Path)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    results=[]
    for folder in args.run_directories:
        snapshot=json.loads((folder/'input.json').read_text())
        metrics=ReviewLog(folder).read(final=True)
        results.append({'run':folder.name,'recorded_task_checklist_count':len(snapshot.get('checklist',[])),
                        'project_requirement_count':len(snapshot.get('requirements',[])),
                        'separate_constraint_count':len(snapshot['constraints']) if 'constraints' in snapshot else None,
                        'metrics':metrics,'final_answer_file_present':(folder/'answer.json').exists(),
                        'limits':'File timestamps measure local log writes, not provider transport timing. Usage is available only when the host emitted an aggregate usage event. Inspection counts do not establish successful application outcomes.'})
    output=json.dumps(results,indent=2)+'\n'
    if args.output: args.output.write_text(output,encoding='utf-8')
    else: print(output,end='')


if __name__=='__main__':main()
