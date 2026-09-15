"""Trust the exact project hooks that the installer wrote, using the hashes that Codex reports."""
import json
from pathlib import Path
import shlex
import sys


def trust_project_hooks(project, database, expected_command=None):
    """Trust only this installer's exact command, using Codex's reported hashes."""
    import queue
    import subprocess
    import threading
    import time
    project=Path(project).resolve();database=Path(database).resolve()
    root=Path(__file__).resolve().parent.parent
    expected='env '+shlex.quote('PYTHONPATH='+str(root))+' '+shlex.join([sys.executable,'-m','memory_module.codex_host','--db',str(database)])
    expected=expected_command or expected
    cmd=['codex','app-server','--stdio']
    process=subprocess.Popen(cmd,cwd=project,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
    messages=queue.Queue()
    def read():
        for line in process.stdout:
            try:messages.put(json.loads(line))
            except ValueError:continue
        messages.put({'error':{'message':'Codex exited before answering.'},'id':-1})
    threading.Thread(target=read,daemon=True).start()
    def request(i,method,params):
        process.stdin.write(json.dumps({'id':i,'method':method,'params':params})+'\n');process.stdin.flush()
        end=time.monotonic()+20
        while time.monotonic()<end:
            item=messages.get(timeout=max(.1,end-time.monotonic()))
            if item.get('id') in {i,-1}:
                if 'error' in item:raise RuntimeError(item['error']['message'])
                return item['result']
        raise TimeoutError('Codex did not answer '+method)
    try:
        request(1,'initialize',{'clientInfo':{'name':'memory-setup','version':'0.3'},'capabilities':{'experimentalApi':True}})
        request(2,'config/batchWrite',{'edits':[{'keyPath':'projects','value':{str(project):{'trust_level':'trusted'}},'mergeStrategy':'upsert'}], 'reloadUserConfig':True})
        hooks=request(3,'hooks/list',{'cwds':[str(project)]})['data'][0]
        if hooks['errors']:raise RuntimeError('Codex reported invalid project hooks.')
        from .health import diagnose_hooks
        diagnosis=diagnose_hooks(hooks)
        if diagnosis['duplicates'] or diagnosis['wrong_host']:
            raise RuntimeError('Codex discovered duplicate or Claude Project Memory hooks. Update the plugin before trusting this installation.')
        selected=[h for h in hooks['hooks'] if h['sourcePath']==str(project/'.codex/hooks.json') and h.get('command')==expected]
        if len(selected)!=9:raise RuntimeError('Expected exactly nine matching memory hooks. No hook trust settings were changed.')
        changes={h['key']:{'enabled':True,'trusted_hash':h['currentHash']} for h in selected}
        # The official host stores hook trust in user config, not project config.
        result=request(4,'config/batchWrite',{'edits':[
            {'keyPath':'hooks.state','value':changes,'mergeStrategy':'upsert'}], 'reloadUserConfig':False})
        import tomllib
        saved=tomllib.loads(Path(result['filePath']).read_text(encoding='utf-8'))
        if saved.get('projects',{}).get(str(project),{}).get('trust_level')!='trusted':
            raise RuntimeError('Codex did not persist project trust. Inspect the host settings before proceeding.')
        return {'trusted_hooks':len(selected),'project':str(project),'host_result':result,
                'note':'Trust permits execution; verify actual receipts with the live checks.'}
    finally:
        process.terminate()
        try:process.wait(timeout=5)
        except subprocess.TimeoutExpired:process.kill();process.wait()
