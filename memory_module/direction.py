"""Explicit, append-only project requirement revisions; original settings remain intact."""
import json
from .core import InvalidRecord, Conflict, _text, _digest, dumps

SCHEMA = ['''
CREATE TABLE IF NOT EXISTS project_revisions (
 version INTEGER PRIMARY KEY, requirements TEXT NOT NULL, reason TEXT NOT NULL,
 actor TEXT NOT NULL, evidence TEXT NOT NULL, created_at TEXT NOT NULL,
 request_key TEXT NOT NULL UNIQUE, signature TEXT NOT NULL
);''' , '''CREATE TRIGGER IF NOT EXISTS immutable_project_revisions_update BEFORE UPDATE ON project_revisions BEGIN
 SELECT RAISE(ABORT,'Append a new project revision.'); END;''' , '''CREATE TRIGGER IF NOT EXISTS immutable_project_revisions_delete BEFORE DELETE ON project_revisions BEGIN
 SELECT RAISE(ABORT,'Project revisions cannot be deleted.'); END;
''']


def current(memory):
    if memory.db.execute("SELECT 1 FROM sqlite_master WHERE name='project_revisions'").fetchone():
        row=memory.db.execute('SELECT * FROM project_revisions ORDER BY version DESC LIMIT 1').fetchone()
        if row:
            value=dict(row)
            for key in ['requirements','evidence']: value[key]=json.loads(value[key])
            value.pop('request_key');value.pop('signature')
            value['status']='current' if all(memory.source_status(r['source_id'])=='current_copy' for r in value['evidence']) else 'needs_review'
            return value
    return {'version':0,'requirements':memory.initial_requirements,'reason':'Initial project settings.','evidence':[]}


def approve(memory, requirements, reason, actor, evidence, expected_version, request_key):
    if not isinstance(requirements,list) or not 1<=len(requirements)<=100:
        raise InvalidRecord('Provide 1–100 explicitly approved requirements.')
    for item in requirements:_text(item,'requirement',2000)
    _text(reason,'reason',2000);_text(actor,'actor',200);_text(request_key,'request_key',180)
    if type(expected_version) is not int or expected_version<0:raise InvalidRecord('expected_version must be nonnegative.')
    if not isinstance(evidence,list) or not 1<=len(evidence)<=20:raise InvalidRecord('Approval requires evidence references.')
    for ref in evidence:
        if not isinstance(ref,dict) or set(ref)!={'source_id','reason'}:raise InvalidRecord('Evidence needs source_id and reason.')
        _text(ref['source_id'],'source_id',200)
        _text(ref['reason'],'evidence reason',2000)
        if memory.source_status(ref['source_id'])!='current_copy':raise InvalidRecord('Approval evidence needs review.')
    signature=_digest(dumps([requirements,reason,actor,evidence]))
    with memory._write():
        for statement in SCHEMA: memory.db.execute(statement)
        prior=memory.db.execute('SELECT * FROM project_revisions WHERE request_key=?',(request_key,)).fetchone()
        if prior:
            if prior['signature']!=signature:raise Conflict('Request key was used for another project revision.')
            return {'version':prior['version'],'duplicate':True}
        version=current(memory)['version']
        if expected_version!=version:raise Conflict(f'Project direction is version {version}; read it before approving a revision.')
        memory.db.execute('INSERT INTO project_revisions VALUES (?,?,?,?,?,?,?,?)',
            (version+1,dumps(requirements),reason,actor,dumps(evidence),memory.now(),request_key,signature))
    return {'version':version+1,'duplicate':False}


def history(memory, limit=10, offset=0):
    initial={'version':0,'requirements':memory.initial_requirements,'reason':'Initial project settings.','evidence':[]}
    if not memory.db.execute("SELECT 1 FROM sqlite_master WHERE name='project_revisions'").fetchone():
        return {'revisions':[initial] if offset==0 else [],'more':False}
    rows=memory.db.execute('SELECT version,requirements,reason,actor,evidence,created_at FROM project_revisions ORDER BY version DESC LIMIT ? OFFSET ?', (limit+1,offset)).fetchall()
    values=[]
    for row in rows:
        value=dict(row)
        for key in ['requirements','evidence']:value[key]=json.loads(value[key])
        values.append(value)
    count=memory.db.execute('SELECT count(*) FROM project_revisions').fetchone()[0]
    if offset<=count and len(values)<limit+1:values.append(initial)
    return {'revisions':values[:limit],'more':len(values)>limit,'next_offset':offset+min(limit,len(values))}
