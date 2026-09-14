"""Verify real compaction, retained conditions, and orderly session shutdown."""
import argparse
import json
from pathlib import Path
import queue
import time
from tests.integration.codex_app_client import CodexClient, project_database
from tests.integration.codex_cases import final_json
from memory_module import Memory


def run(project, output):
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    client=CodexClient(project,output/'events.jsonl')
    try:
        thread=client.start()
        turn=client.turn(thread,'For this local continuity test, retain these exact conditions: new uploads use strict UTF-8; invalid bytes raise UnicodeDecodeError; only the separately tagged legacy endpoint uses Latin-1. The production outcome is unmeasured. Do not perform research or modify files. Acknowledge these conditions in one sentence.')
        client.complete(turn)
        client.request('thread/compact/start',{'threadId':thread})
        deadline=time.monotonic()+180;compacted=False
        while time.monotonic()<deadline:
            try:event=client.receive(timeout=10)
            except queue.Empty:continue
            if event.get('method')=='thread/compacted' or (event.get('method')=='item/completed' and event['params']['item'].get('type')=='contextCompaction'):
                compact_turn=event['params']['turnId']
                if not any(e.get('method')=='turn/completed' and e['params']['turn']['id']==compact_turn for e in client.events):
                    client.complete(compact_turn)
                compacted=True;break
        if not compacted:raise TimeoutError('The host did not report completed compaction.')
        turn=client.turn(thread,'Using the earlier conditions, return only JSON with encoding, invalid_bytes, legacy_exception, and production_status. Preserve every condition and uncertainty. Do not reread sources or modify files.')
        end=client.complete(turn);answer,raw=final_json(client.events,turn)
        checks={'encoding':'utf' in str(answer.get('encoding','')).lower(),
            'invalid_bytes':'UnicodeDecodeError' in str(answer.get('invalid_bytes','')),
            'legacy_exception':'latin' in str(answer.get('legacy_exception','')).lower() and 'legacy' in str(answer.get('legacy_exception','')).lower(),
            'production_unmeasured':'unmeasured' in str(answer.get('production_status','')).lower()}
        shutdown=client.request('thread/unsubscribe',{'threadId':thread})
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            try:client.receive(timeout=.5)
            except queue.Empty:continue
        with Memory(project_database(project)) as m:
            counts=dict(m.db.execute('SELECT event_name,count(*) FROM host_receipts WHERE session_id=? GROUP BY event_name',(thread,)).fetchall())
        result={'thread_id':thread,'compaction_completed':compacted,'completion':end['status'],'output':answer,
            'checks':checks,'hook_counts':counts,'shutdown_response':shutdown,
            'limits':'One short continuity case checks four explicit facts. It does not establish lossless long-session compaction or a full input below 10,000 tokens.'}
        (output/'answer.txt').write_text(raw);(output/'result.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result,indent=2))
    finally:client.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--project',required=True);p.add_argument('--output',required=True)
    a=p.parse_args();run(a.project,a.output)
