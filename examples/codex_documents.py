"""Exercise document capture and discovery through the installed Codex host."""
import argparse
import json
from pathlib import Path

from examples.codex_app_client import CodexClient
from examples.codex_cases import final_json
from memory_module import Memory


def call_failed(item):
    if item.get('status')=='failed' or item.get('error') or (item.get('result') or {}).get('isError'):
        return True
    # The app-server may omit isError while retaining the adapter's JSON error.
    for content in (item.get('result') or {}).get('content', []):
        if content.get('type') != 'text':
            continue
        try:
            value=json.loads(content.get('text',''))
        except (ValueError,TypeError):
            continue
        if isinstance(value,dict) and 'error' in value and 'message' in value:
            return True
    return False


def run(project, output):
    project=Path(project).resolve();output=Path(output).resolve();output.mkdir(parents=True,exist_ok=False)
    path=project/'AmberlakeDocumentPilot.md'
    with path.open('x') as stream:
        stream.write('# AmberlakeDocumentPilot vision\nKeep customer data locally. A reviewed export is allowed only after an explicit customer request.\n')
    with Memory(project/'memory.sqlite') as m:
        source=m.document(str(path),subject='general')
        ep=m.start('AmberlakeDocumentPilot rollout','Review the export choice after a vision change.','document integration','The changed evidence and exception are explicit.',subject='code')
        refs=[{'source_id':source['id'],'reason':'The selected vision defines the export conditions.'}]
        decision=m.record(ep['id'],'decision',{'decision':'Keep AmberlakeDocumentPilot data local with an explicitly requested export.',
            'why':'The captured vision requires this scope.','expected':'Customers control when data leaves the local store.',
            'uncertainty':'Customer acceptance remains unmeasured.','alternatives':['Export automatically.'],
            'reconsider_when':'The selected vision changes.'},expected_version=0,request_key='document-pilot-choice',actor='fixture',evidence=refs)
        lesson=m.record(ep['id'],'lesson',{'when':'The AmberlakeDocumentPilot project keeps customer data locally.','do':'Preserve the export exception.',
            'because':'The decision must preserve the original conditions when evidence is reused. '*70,
            'exceptions':'Do not apply this lesson when a customer explicitly requests a reviewed export.'},expected_version=1,request_key='document-pilot-lesson',actor='fixture',evidence=refs)
    path.write_text('# AmberlakeDocumentPilot vision\nCustomers may request scheduled exports after review. Local storage remains the default.\n')
    prompt=f'''[memory:code] AmberlakeDocumentPilot
Use memory_get search with subject code to discover the pilot records, then read the decision and full proposed lesson in one records batch. Check their status, uncertainty and exception. Capture the current {path.name} through memory_write document with subject general and a fresh request key. Read its captured body through memory_get record. Do not edit files or decisions, accept lessons, or read SQLite directly. Return JSON with needs_review (boolean), lesson_status (text), exception (text), current_export_policy (text), uncertainty (text).'''
    (output/'prompt.txt').write_text(prompt)
    client=CodexClient(project,output/'events.jsonl')
    try:
        thread=client.start();turn=client.turn(thread,prompt);end=client.complete(turn,timeout=240)
        answer,raw=final_json(client.events,turn)
        completed=[e['params']['item'] for e in client.events if e.get('method')=='item/completed' and e['params']['item'].get('type')=='mcpToolCall']
        calls=[{'tool':i.get('tool'),'arguments':i.get('arguments'),'result':i.get('result'),'error':i.get('error')} for i in completed]
        successful=[i for i in completed if not call_failed(i)]
        def args(i):
            value=i.get('arguments',{});return json.loads(value) if isinstance(value,str) else value
        checks={'turn_completed':end.get('status')=='completed',
            'actual_index_call':any(args(i).get('view')=='search' for i in successful),
            'actual_batch_call':any(args(i).get('view')=='records' and {decision['id'],lesson['id']}<=set(args(i).get('ids',[])) for i in successful),
            'actual_document_capture':any(args(i).get('operation')=='document' for i in successful),
            'actual_source_body_read':any('body_offset' in args(i) for i in successful),
            'recognises_changed_evidence':answer.get('needs_review') is True,
            'preserves_proposed_status':answer.get('lesson_status')=='proposed',
            'preserves_exception':'explicitly requests a reviewed export' in answer.get('exception',''),
            'reads_current_policy':'scheduled' in answer.get('current_export_policy','').lower(),
            'retains_uncertainty':'unmeasured' in answer.get('uncertainty','').lower()}
        with Memory(project/'memory.sqlite') as m:
            checks['two_source_versions']=m.db.execute('SELECT count(*) FROM sources WHERE source_key=(SELECT source_key FROM sources WHERE id=?)',(source['id'],)).fetchone()[0]==2
            checks['no_accepted_lesson']=m.read(lesson['id'])['lesson_status']=='proposed'
            m.export_html(output/'viewer.html',episode_id=ep['id'],include_bodies=True)
        usage=[e['params']['tokenUsage'] for e in client.events if e.get('method')=='thread/tokenUsage/updated']
        result={'checks':checks,'answer':answer,'raw_answer':raw,'model':client.model,'usage':usage[-1] if usage else None,
            'mcp_calls':len(calls),'mcp_errors':sum(call_failed(i) for i in completed),
            'meaning':'One live host integration case; this is not a paired token-saving or daily-use study.'}
        (output/'calls.json').write_text(json.dumps(calls,indent=2)+'\n')
        (output/'result.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result,indent=2))
        if not all(checks.values()):raise RuntimeError('A live document check failed; inspect preserved output.')
    finally:client.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--project',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();run(args.project,args.output)
