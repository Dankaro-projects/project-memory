"""Typed human actions over the same append-only plans used by MCP."""
import json
from .core import InvalidRecord, Conflict, dumps, _text, _digest
from .mcp import write
from .planning import latest


def action(memory, operation, data, request_key):
    if operation not in {'plan','sprint','comment'}:
        raise InvalidRecord('Unknown workspace action.')
    if not isinstance(data,dict): raise InvalidRecord('Action data must be an object.')
    _text(request_key,'request_key',180)
    signature=_digest(dumps([operation,data]))
    with memory._write():
        prior=memory.db.execute('SELECT signature,result FROM adapter_requests WHERE request_key=?',(request_key,)).fetchone()
        if prior:
            if prior['signature']!=signature: raise Conflict('This action key was already used for different changes.')
            return json.loads(prior['result'])
        allowed={'episode_id','expected_version','title','objective','criterion','subject','payload'}
        if operation=='comment': allowed={'episode_id','expected_version','text'}
        if set(data)-allowed: raise InvalidRecord('The workspace action contains unsupported fields.')
        data=dict(data);episode_id=data.get('episode_id')
        if episode_id:
            ep=memory.episode(episode_id)
            if data.get('expected_version')!=ep['version']:
                raise Conflict('This work changed while you were editing. Your draft is retained; reload its current version before saving.')
        title=ep['title'] if episode_id else data.get('title','Work')
        sentences=['The user saves this '+operation+' for '+title+'.']
        fields={'objective':'The intended result is','criterion':'Completion requires','scope':'The scope is',
                'next_action':'The next action is','reason':'The reason is','state':'The selected work state is',
                'owner':'The responsible party is','priority':'The priority is','autonomy':'The recorded autonomy is',
                'starts_on':'The sprint starts on','ends_on':'The sprint ends on','status':'The sprint status is','text':'The comment says'}
        values={**data,**data.get('payload',{})}
        for key,label in fields.items():
            if key in values:sentences.append(label+': '+values[key])
        for ref in values.get('depends_on',[]):
            sentences.append('This work depends on '+memory.episode(ref['episode_id'])['title']+' because '+ref['reason'])
        if values.get('sprint_id'):sentences.append('The assigned sprint is '+memory.episode(values['sprint_id'])['title']+'.')
        source=memory.source('workspace:'+request_key,operation.capitalize()+' for '+title,
                             sentences[0],'\n'.join(sentences),'user',subject=data.get('subject',ep['subject'] if episode_id else 'general'))
        evidence=[{'source_id':source['id'],'reason':'The user submits this change through the local workspace.'}]
        if operation=='comment':
            result=memory.record(episode_id,'note',{'text':data['text']},expected_version=data['expected_version'],
                                 actor='workspace-user',evidence=evidence,request_key=request_key+':record')
        else:
            if episode_id:
                old=latest(memory,episode_id,'work_plan' if operation=='plan' else 'sprint')
                if old:
                    # Keep the original intent evidence when the user changes only scheduling or progress.
                    governed={'scope','autonomy','depends_on'} if operation=='plan' else {'starts_on','ends_on'}
                    if all(old.get(k,[] if k=='depends_on' else None)==data['payload'].get(k,[] if k=='depends_on' else None) for k in governed):
                        evidence=[dict(r) for r in memory.db.execute('SELECT source_id,reason FROM dependencies WHERE event_id=?',(old['id'],))]
                if operation=='plan' and data['payload'].get('owner','agent')=='agent' and data['payload'].get('state')=='in_progress' and (not old or old.get('state')!='in_progress' or data['payload'].get('session_id')!=old.get('session_id')):
                    raise InvalidRecord('Only an active agent session can claim work in progress. Select Ready to queue agent work.')
            result=write(memory,operation,request_key+':record',{**data,'actor':'workspace-user','evidence':evidence},session_id=data['payload'].get('session_id'))
        memory.db.execute('INSERT INTO adapter_requests VALUES (?,?,?)',(request_key,signature,dumps(result)))
        return result
