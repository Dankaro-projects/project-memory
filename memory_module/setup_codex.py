"""Install the local MCP server and command hooks in one project, preserving data."""
import argparse
import json
from pathlib import Path
import shlex
import sys
from .core import Memory, Conflict
from .codex_host import initialize


def setup(project, database):
    project=Path(project).resolve();database=Path(database).resolve()
    if not project.is_dir(): raise ValueError('Project directory does not exist.')
    root=Path(__file__).resolve().parent.parent
    local=project/'.codex';local.mkdir(exist_ok=True)
    config=local/'config.toml';hooks_file=local/'hooks.json'
    original=config.read_text() if config.exists() else ''
    if '[mcp_servers.memory]' in original:
        raise Conflict('This project already has a memory server. Inspect its existing setup before changing it.')
    if '[hooks' in original:
        raise Conflict('This project has inline hooks. Merge the documented memory entries with that layer to preserve precedence.')
    existing=json.loads(hooks_file.read_text()) if hooks_file.exists() else {'hooks':{}}
    if not isinstance(existing.get('hooks'),dict): raise ValueError('Existing hooks file is invalid.')
    with Memory(database) as memory:
        backup=database.with_name(database.name+'.before-codex.sqlite')
        memory.backup(backup)
        initialize(memory)
    command=shlex.join([sys.executable,'-m','memory_module.codex_host','--db',str(database)])
    # Set only the module search path for the hook process, without changing the shell cwd.
    command='env '+shlex.quote('PYTHONPATH='+str(root))+' '+command
    for event in ['SessionStart','UserPromptSubmit','PreToolUse','PostToolUse','Stop','Interrupt','SessionEnd','PreCompact','PostCompact']:
        entry={'hooks':[{'type':'command','command':command,'timeout':3 if event in {'Interrupt','SessionEnd'} else 10}]}
        if event in {'PreToolUse','PostToolUse'}: entry['matcher']='.*'
        existing['hooks'].setdefault(event,[]).append(entry)
    addition='\n[mcp_servers.memory]\ncommand = '+json.dumps(sys.executable)+'\nargs = '+json.dumps(['-m','memory_module.mcp','--db',str(database)])+'\ncwd = '+json.dumps(str(root))+'\ndefault_tools_approval_mode = "approve"\n'
    for p in [config,hooks_file]:
        if p.exists():
            with p.with_name(p.name+'.before-memory').open('x') as f: f.write(p.read_text())
    hooks_file.write_text(json.dumps(existing,indent=2)+'\n');config.write_text(original+addition)
    return {'database':str(database),'backup':str(backup),'config':str(config),'hooks':str(hooks_file),
      'next_step':'Open a new Codex session in this project, review these exact command hooks in /hooks and enable them. Verify receipts with memory_get status; configuration alone is not evidence.'}


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


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--project',required=True);p.add_argument('--db',required=True)
    p.add_argument('--trust',action='store_true',help="Enable only this installer's exact project hooks through Codex.")
    p.add_argument('--trust-only',action='store_true',help='Trust an existing installation without rewriting project files.')
    a=p.parse_args()
    result={} if a.trust_only else setup(a.project,a.db)
    if a.trust or a.trust_only:result['trust']=trust_project_hooks(a.project,a.db)
    print(json.dumps(result,indent=2))
