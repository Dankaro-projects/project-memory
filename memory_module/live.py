"""Project-scoped, authenticated loopback viewer. SQLite remains authoritative."""
import argparse
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import TCPServer
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import time
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen
import uuid
from . import Memory
from .core import MemoryError, InvalidRecord, dumps
from . import codex_host
from .documents import document_path, PREFIX
from .health import inspect
from .install import atomic


def row(memory, rid, *, body_offset=None):
    if rid.startswith('direction_'):
        version=int(rid.split('_')[1])
        if version==0:detail={'version':0,'requirements':memory.initial_requirements,'reason':'Initial project settings.','evidence':[]}
        else:
            found=memory.db.execute('SELECT version,requirements,reason,actor,evidence,created_at FROM project_revisions WHERE version=?',(version,)).fetchone()
            detail=dict(found) if found else None
            if detail:
                for k in ('requirements','evidence'):detail[k]=json.loads(detail[k])
        if detail is None:raise InvalidRecord('Unknown project revision.')
        return {'id':rid,'kind':'project_revision','subject':'general',
                'status':memory.direction().get('status','current') if version==memory.direction()['version'] else 'historical',
                'title':f'Project requirements, version {version}','date':detail.get('created_at',''), 'episode_id':'','detail':detail}
    if rid.startswith('episode_'):
        detail=memory.episode(rid)
        return {'id':rid,'kind':'episode','subject':detail['subject'],'status':detail['status'],
                'title':detail['title'],'date':detail['created_at'],'episode_id':rid,'detail':detail}
    if rid.startswith('host_'):
        detail=codex_host.read_receipt(memory,rid)
        pending=memory.db.execute("SELECT 1 FROM host_receipts WHERE session_id=? AND tool_use_id=? AND event_name='PostToolUse' AND coalesce(json_extract(payload,'$.host'),'codex')=?",(detail['session_id'],detail['tool_use_id'],detail['payload'].get('host','codex'))).fetchone()
        reconciled=memory.db.execute("SELECT 1 FROM host_receipts WHERE event_name='Reconciled' AND json_extract(payload,'$.receipt_id')=? AND json_extract(payload,'$.resolution')!='unknown'",(rid,)).fetchone()
        status='execution_unconfirmed' if detail['event_name']=='PreToolUse' and not pending and not reconciled else 'observed'
        ep=detail['episode_id']
        return {'id':rid,'kind':'host_receipt','subject':memory.episode(ep)['subject'] if ep else 'general',
                'status':status,'title':detail['event_name']+': '+(detail['tool_name'] or 'Host session'),
                'date':detail['created_at'],'episode_id':ep or '', 'detail':detail}
    detail=memory.read(rid)
    source=detail['kind']=='source'
    if source and body_offset is not None:
        body=memory.read(rid,detail=True)['body']
        if body_offset>len(body):raise InvalidRecord('body_offset exceeds the source length.')
        detail.update(body=body[body_offset:body_offset+12000],body_offset=body_offset,
                      body_more=body_offset+12000<len(body),next_offset=min(body_offset+12000,len(body)))
    payload=detail.get('payload',{})
    title=detail['title'] if source else next((payload[k] for k in ['decision','summary','observed','question','do','text','reason','action'] if k in payload),detail['kind'])
    return {'id':rid,'kind':detail['kind'],'subject':detail['subject'],'status':detail['status'],
            'title':title,'date':detail.get('checked_at',detail.get('created_at','')),
            'episode_id':detail.get('episode_id',''),'detail':detail}


def page(memory, params):
    view=params.get('view','episodes');limit=int(params.get('limit','25'));offset=int(params.get('offset','0'))
    if not 1<=limit<=100 or offset<0:raise InvalidRecord('Use limit 1–100 and a nonnegative offset.')
    query=params.get('query','')
    if params.get('related'):
        rid=params['related']
        sql='''SELECT id FROM events WHERE decision_id=? OR supersedes=?
               UNION SELECT event_id FROM dependencies WHERE source_id=?
               UNION SELECT event_id FROM event_links WHERE prior_event_id=?'''
        memory.db.execute('BEGIN')
        try:
            total=memory.db.execute('SELECT count(*) FROM ('+sql+')',(rid,rid,rid,rid)).fetchone()[0]
            ids=memory.db.execute('SELECT e.id FROM events e JOIN ('+sql+') r ON e.id=r.id ORDER BY e.created_at,e.rowid LIMIT ? OFFSET ?',(rid,rid,rid,rid,limit,offset)).fetchall()
            return {'records':[row(memory,r[0]) for r in ids],'total':total,'offset':offset,'more':offset+len(ids)<total}
        finally:memory.db.rollback()
    if len(query)>500:raise InvalidRecord('Search is limited to 500 characters.')
    selections=["SELECT id,'episode' AS kind,subject,id AS episode_id,created_at AS date,title,title||' '||objective AS text FROM episodes",
        "SELECT id,kind,subject,episode_id,created_at AS date,coalesce(json_extract(payload,'$.decision'),json_extract(payload,'$.summary'),json_extract(payload,'$.observed'),json_extract(payload,'$.question'),json_extract(payload,'$.do'),json_extract(payload,'$.text'),json_extract(payload,'$.reason'),json_extract(payload,'$.action'),kind) AS title,payload AS text FROM events",
        "SELECT id,'source' AS kind,subject,'' AS episode_id,checked_at AS date,title,title||' '||summary||' '||body AS text FROM sources"]
    if codex_host.exists(memory):
        selections.append("SELECT id,'host_receipt' AS kind,coalesce((SELECT subject FROM episodes WHERE episodes.id=host_receipts.episode_id),'general') AS subject,coalesce(episode_id,'') AS episode_id,created_at AS date,event_name||': '||tool_name AS title,payload AS text FROM host_receipts")
    if view=='direction':
        initial="SELECT 'direction_0' AS id,'project_revision' AS kind,'general' AS subject,'' AS episode_id,'' AS date,'Project requirements, version 0' AS title,(SELECT value FROM settings WHERE key='requirements') AS text"
        selections=[initial]
        if memory.direction()['version']:
            selections.append("SELECT 'direction_'||version,'project_revision','general','',created_at,'Project requirements, version '||version,requirements||' '||reason FROM project_revisions")
    clauses=[];args=[]
    kinds={'episodes':['episode'],'decisions':['decision'],'sources':['source'],'documents':['source'],
           'lessons':['lesson'],'research':['research'],'corrections':['correction'],'patterns':['lesson'],'captures':['host_receipt'],'pending':['decision']}
    if view in kinds:clauses.append('kind IN ('+','.join('?' for _ in kinds[view])+')');args.extend(kinds[view])
    elif view=='events':clauses.append("kind NOT IN ('episode','source','host_receipt')")
    elif view not in {'direction','pending','drift'}:raise InvalidRecord('Unknown viewer section.')
    for column,value,op in [('subject',params.get('subject'),'='),('episode_id',params.get('episode'),'='),('date',params.get('from'),'>='),('substr(date,1,10)',params.get('to'),'<=')]:
        if value:clauses.append(column+op+'?');args.append(value)
    if query:clauses.append("(instr(lower(text),lower(?))>0 OR instr(lower(title),lower(?))>0 OR id=?)");args.extend([query,query,query])
    order={'newest':'date DESC,id','oldest':'date,id','title':'title COLLATE NOCASE,id'}.get(params.get('order','newest'))
    if not order:raise InvalidRecord('Unknown sort order.')
    where=' WHERE '+' AND '.join(clauses) if clauses else ''
    sql='SELECT id FROM ('+' UNION ALL '.join(selections)+')'+where+' ORDER BY '+order
    memory.db.execute('BEGIN')
    try:
        pending={}
        derived=view in {'pending','drift','documents','patterns'} or bool(params.get('status'))
        if not derived:
            total=memory.db.execute('SELECT count(*) FROM ('+' UNION ALL '.join(selections)+')'+where,args).fetchone()[0]
            records=[row(memory,r[0]) for r in memory.db.execute(sql+' LIMIT ? OFFSET ?',(*args,limit,offset)).fetchall()]
        else:
            # Derived status uses the same domain rules as MCP; bodies are never sent in list pages.
            total=0;records=[]
            for found in memory.db.execute(sql,args):
                r=row(memory,found[0]);detail=r['detail']
                if view=='pending':
                    values=memory.pending(decision_id=r['id'])['decisions']
                    if not values:continue
                    pending[r['id']]=values[0]
                if view=='drift' and r['status'] not in {'needs_review','review_due','superseded','file_changed','file_missing','file_unreadable'}:continue
                if view=='documents' and detail.get('origin')!='document':continue
                if view=='patterns' and detail.get('payload',{}).get('pattern_type') not in {'anti_pattern','practice','recovery'}:continue
                state=pending[r['id']]['state'] if view=='pending' else r['status']
                if params.get('status') and state!=params['status']:continue
                if offset<=total<offset+limit:records.append(r)
                elif view=='pending':pending.pop(r['id'],None)
                total+=1
        return {'records':records,'pending':[pending[r['id']] for r in records if r['id'] in pending],'total':total,'offset':offset,'limit':limit,
                'more':offset+len(records)<total,'source_bodies_included':False}
    finally:memory.db.rollback()


def html():
    template=Path(__file__).with_name('viewer.html').read_text(encoding='utf-8')
    data={'live':True,'project':'Project Memory','exported_at':'','requirements':[], 'records':[],'pending':[],'scope':{},'source_bodies_included':False}
    content=template.replace('__MEMORY_DATA__',dumps(data)).replace("base-uri 'none'", "connect-src 'self'; base-uri 'none'")
    for tag in ['style','script']:
        body=template.split('<'+tag+'>',1)[1].split('</'+tag+'>',1)[0]
        digest=base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()
        content=content.replace('__'+tag.upper()+'_HASH__',digest)
    return content.encode()


class Viewer(HTTPServer):
    allow_reuse_address=True
    def __init__(self, path, token, port=0):
        self.memory=Memory(path,read_only=True);self.token=token;self.boot=uuid.uuid4().hex
        self.last_access=time.monotonic();self.version=None;self.paths=[];self.next_review=None;self.due_count=0;self.db_identity=Path(path).stat().st_ino
        super().__init__(('127.0.0.1',port),Handler)
    def server_bind(self):
        # HTTPServer resolves a hostname here; this fixed loopback service needs no DNS.
        TCPServer.server_bind(self)
        self.server_name='127.0.0.1';self.server_port=self.server_address[1]
    def revision(self):
        if self.memory.path.stat().st_ino!=self.db_identity:
            self.last_access=0
            raise InvalidRecord('The database file was replaced. Restart the viewer with project-memory view.')
        version=self.memory.db.execute('PRAGMA data_version').fetchone()[0]
        now=self.memory.now()
        if version!=self.version or self.next_review and now>=self.next_review:
            self.due_count,self.next_review=self.memory.db.execute('SELECT count(CASE WHEN review_after<=? THEN 1 END),min(CASE WHEN review_after>? THEN review_after END) FROM sources',(now,now)).fetchone()
            self.paths=[path for r in self.memory.db.execute('SELECT DISTINCT source_key FROM sources WHERE source_key LIKE ?',(PREFIX+'%',)) if (path:=document_path(r[0])) is not None]
            self.version=version
        stats=[]
        for p in self.paths:
            try:st=p.stat();stats.append((str(p),st.st_mtime_ns,st.st_size,st.st_ino))
            except OSError:stats.append((str(p),None))
        return self.boot+':'+str(version)+':'+str(self.due_count)+':'+hashlib.sha256(dumps(stats).encode()).hexdigest()[:16]
    def server_close(self):
        super().server_close();self.memory.close()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):pass
    def setup(self):
        super().setup();self.connection.settimeout(5)
    def do_GET(self):
        origin=f'http://127.0.0.1:{self.server.server_port}'
        prefix='/'+self.server.token+'/'
        target=urlsplit(self.path)
        if (self.headers.get('Host')!=origin[7:] or self.headers.get('Origin',origin)!=origin
                or not target.path.startswith(prefix)):
            self.send_error(403);return
        self.server.last_access=time.monotonic()
        if len(target.query)>4096:self.send_error(414);return
        params={k:v[-1] for k,v in parse_qs(target.query).items()}
        endpoint=target.path[len(prefix):]
        try:
            if endpoint=='':body=html();mime='text/html; charset=utf-8';etag=None
            elif endpoint in {'api/health','api/records','api/record'}:
                revision=self.server.revision();etag='"'+hashlib.sha256((revision+target.path+target.query).encode()).hexdigest()+'"'
                if self.headers.get('If-None-Match')==etag:
                    self.send_response(304);self.send_header('ETag',etag);self.end_headers();return
                memory=self.server.memory
                if endpoint=='api/health':
                    value={'revision':revision,**inspect(memory),'requirements':memory.requirements,
                           'episodes':[dict(r) for r in memory.db.execute('SELECT id,title FROM episodes ORDER BY created_at DESC LIMIT 1000')],
                           'episodes_more':memory.db.execute('SELECT count(*) FROM episodes').fetchone()[0]>1000}
                elif endpoint=='api/records':value={'revision':revision,**page(memory,params)}
                else:
                    rid=params.get('id','');offset=int(params.get('body_offset','0'))
                    if offset<0:raise InvalidRecord('body_offset must be nonnegative.')
                    memory.db.execute('BEGIN')
                    try:value={'revision':revision,'record':row(memory,rid,body_offset=offset)}
                    finally:memory.db.rollback()
                body=dumps(value).encode();mime='application/json; charset=utf-8'
            else:self.send_error(404);return
        except (MemoryError,ValueError,sqlite3.Error,OSError) as exc:
            body=dumps({'error':type(exc).__name__,'message':str(exc)}).encode();mime='application/json';etag=None
            self.send_response(400)
        else:self.send_response(200)
        self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-cache, private');self.send_header('Referrer-Policy','no-referrer')
        self.send_header('X-Content-Type-Options','nosniff');self.send_header('X-Frame-Options','DENY')
        if etag:self.send_header('ETag',etag)
        self.end_headers()
        try:self.wfile.write(body)
        except (BrokenPipeError,ConnectionResetError):pass


def start(path):
    path=Path(path).resolve()
    state=path.parent/('viewer.json' if path.name=='project.sqlite' else path.name+'.viewer.json')
    with state.with_suffix('.lock').open('a+b') as lock:
        if os.name=='nt':
            import msvcrt
            lock.write(b'0');lock.flush();lock.seek(0)
            msvcrt.locking(lock.fileno(),msvcrt.LK_LOCK,1)
        else:
            import fcntl
            fcntl.flock(lock,fcntl.LOCK_EX)
        return _start(path,state)


def _start(path,state):
    previous=json.loads(state.read_text()) if state.exists() else {}
    if previous.get('database')!=str(path):previous={}
    port=previous.get('port',0);token=previous.get('token')
    valid=type(port) is int and 0<port<65536 and isinstance(token,str) and len(token)>=24 and all(c.isalnum() or c in '-_' for c in token)
    expected=f'http://127.0.0.1:{port}/{token}/' if valid else None
    if previous.get('database')==str(path) and previous.get('url')==expected and expected:
        try:
            with urlopen(previous['url']+'api/health',timeout=1) as response:
                if json.load(response).get('database')==str(path):return {**{k:v for k,v in previous.items() if k!='token'},'reused':True}
        except (OSError,ValueError):pass
    # The credential stays in a private state file, not the process arguments.
    token=token if valid else secrets.token_urlsafe(24)
    info={'database':str(path),'token':token,'port':port if valid else 0,'phase':'starting'}
    atomic(state,dumps(info));state.chmod(0o600)
    log=state.with_suffix('.log')
    with log.open('w') as out:
        process=subprocess.Popen([sys.executable,'-m','memory_module.live','--db',str(path),'--state',str(state)],
            stdin=subprocess.DEVNULL,stdout=out,stderr=out,start_new_session=os.name!='nt')
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        value=json.loads(state.read_text())
        if value.get('phase')=='ready':
            import threading
            threading.Thread(target=process.wait,daemon=True).start()
            return {k:v for k,v in value.items() if k!='token'}
        if process.poll() is not None:raise RuntimeError('The viewer did not start. '+log.read_text()[-4000:])
        time.sleep(.05)
    process.terminate()
    try:process.wait(timeout=2)
    except subprocess.TimeoutExpired:process.kill();process.wait()
    raise RuntimeError('Viewer startup timed out. '+log.read_text()[-4000:])


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--db',required=True);parser.add_argument('--state',required=True)
    args=parser.parse_args();state=Path(args.state);info=json.loads(state.read_text())
    with Viewer(args.db,info['token'],info.get('port',0)) as server:
        info.update(port=server.server_port,url=f'http://127.0.0.1:{server.server_port}/{info["token"]}/',phase='ready',pid=os.getpid())
        atomic(state,dumps(info));state.chmod(0o600);server.timeout=1
        while time.monotonic()-server.last_access<600:server.handle_request()


if __name__=='__main__':main()
