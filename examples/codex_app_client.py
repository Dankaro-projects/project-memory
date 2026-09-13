"""Development-only client for actual installed Codex app-server verification."""
import json
from pathlib import Path
import queue
import subprocess
import threading
import time
import tomllib


class CodexClient:
    def __init__(self, project, log):
        self.project=Path(project).resolve();self.log=Path(log);self.events=[];self.next_id=0
        config_path=Path.home()/'.codex/config.toml'
        config=tomllib.loads(config_path.read_text())
        self.model=config.get('model')
        cmd=['codex','app-server','--stdio','-c','projects={'+json.dumps(str(self.project))+'={trust_level="trusted"}}',
             '-c','features.plugins=false','-c','features.apps=false','-c','skills.include_instructions=false',
             '-c','project_doc_max_bytes=0','-c','memories.use_memories=false','-c','memories.generate_memories=false']
        # Keep account authentication; disable unrelated connectors only for this
        # test process. Do not copy secrets into test logs or a second CODEX_HOME.
        for key in config.get('mcp_servers',{}):
            if key!='project_memory':cmd+=['-c',f'mcp_servers.{key}.enabled=false']
        self.err=self.log.with_suffix('.stderr').open('w')
        self.out=self.log.open('w')
        self.process=subprocess.Popen(cmd,cwd=self.project,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.err,text=True,bufsize=1)
        self.queue=queue.Queue()
        def read():
            for line in self.process.stdout:
                try:self.queue.put(json.loads(line))
                except ValueError:self.queue.put({'malformed_output':line})
            self.queue.put({'process_exited':True})
        threading.Thread(target=read,daemon=True).start()
        self.request('initialize',{'clientInfo':{'name':'memory-verification','version':'0.3'},'capabilities':{'experimentalApi':True}})
        self.send({'method':'initialized','params':{}})
    def send(self,value):
        self.process.stdin.write(json.dumps(value)+'\n');self.process.stdin.flush()
    def receive(self,timeout=30):
        value=self.queue.get(timeout=timeout)
        self.out.write(json.dumps(value)+'\n');self.out.flush();self.events.append(value)
        if 'process_exited' in value:raise RuntimeError('Codex app-server exited; inspect stderr.')
        # Never auto-approve an unexpected external operation.
        if 'id' in value and 'method' in value:
            self.send({'id':value['id'],'error':{'code':-32601,'message':'Verification client does not approve external actions.'}})
        return value
    def request(self,method,params,timeout=30):
        self.next_id+=1;rid=self.next_id;self.send({'id':rid,'method':method,'params':params})
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            value=self.receive(max(.1,deadline-time.monotonic()))
            if value.get('id')==rid:
                if 'error' in value:raise RuntimeError(value['error'])
                return value.get('result',{})
        raise TimeoutError(method)
    def start(self):
        return self.request('thread/start',{'cwd':str(self.project),'model':self.model,'approvalPolicy':'never','sandbox':'workspace-write',
            'developerInstructions':'Run only the requested evaluation in this directory. Do not delegate, inspect other projects or make external requests. Use the available memory tools when the task requests them. Keep responses concise.'})['thread']['id']
    def turn(self,thread,text):
        return self.request('turn/start',{'threadId':thread,'input':[{'type':'text','text':text}]})['turn']['id']
    def complete(self,turn,timeout=240):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            try:v=self.receive(min(30,max(.1,deadline-time.monotonic())))
            except queue.Empty:continue
            if v.get('method')=='turn/completed' and v['params']['turn']['id']==turn:return v['params']['turn']
        raise TimeoutError('Turn did not complete.')
    def close(self):
        self.process.terminate()
        try:self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:self.process.kill();self.process.wait()
        self.out.close();self.err.close()
