"""Measure bounded planning replies on invented local data; these are not model tokens."""
import json
from pathlib import Path
from statistics import median
import tempfile
from time import perf_counter
from examples.planning_case import seed
from memory_module import Memory, __version__
from memory_module.mcp import dispatch, tool_result


def measure():
    results=[]
    with tempfile.TemporaryDirectory(prefix='memory-planning-') as directory:
        for count in (29, 100):
            project=Path(directory)/str(count);fixture=seed(project,extra=count-3)
            with Memory(project/'.memory/project.sqlite') as memory:
                for view in ('board','next'):
                    args={'view':view,'max_chars':20000 if view=='board' else 6000}
                    if view=='next': args.update(id=fixture['repair']['episode_id'],session_id='fixture')
                    else: args['limit']=10
                    dispatch(memory,'memory_get',args)
                    times=[]
                    for _ in range(5):
                        start=perf_counter();reply=tool_result(dispatch(memory,'memory_get',args))
                        encoded=json.dumps(reply,ensure_ascii=False,separators=(',',':'))
                        times.append(round((perf_counter()-start)*1000,3))
                    results.append({'work_items':count,'view':view,'milliseconds':times,
                                    'median_ms':median(times),'serialized_reply_characters':len(encoded)})
    return {'version':__version__,'samples':results,'limits':'Five warm local samples. Includes JSON serialisation; excludes host, model and browser latency. Character counts are not token measurements. No human maintenance or daily productivity is measured.'}


if __name__=='__main__': print(json.dumps(measure(),indent=2))
