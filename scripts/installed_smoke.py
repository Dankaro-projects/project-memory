"""Install the wheel in a fresh environment and exercise it outside the checkout."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

wheel=next(Path(sys.argv[1] if len(sys.argv)>1 else 'dist').resolve().glob('*.whl'))
with tempfile.TemporaryDirectory(prefix='project-memory-installed-') as directory:
    root=Path(directory);venv=root/'venv';project=root/'project';project.mkdir()
    subprocess.run([sys.executable,'-m','venv',str(venv)],check=True,capture_output=True)
    python=venv/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    cli=venv/('Scripts/project-memory.exe' if os.name=='nt' else 'bin/project-memory')
    env={k:v for k,v in os.environ.items() if k not in {'PYTHONPATH','PYTHONHOME','PROJECT_MEMORY_PROJECT'}}
    env['PYTHONUTF8']='1'
    subprocess.run([str(python),'-m','pip','install','--no-deps',str(wheel)],check=True,capture_output=True,cwd=project,env=env)
    def run(*args):
        result=subprocess.run([str(cli),*args],check=True,capture_output=True,text=True,cwd=project,env=env,timeout=30)
        return json.loads(result.stdout)
    shadow=project/'memory_module';shadow.mkdir()
    (shadow/'__init__.py').write_text('raise RuntimeError("Project code shadowed the installed runtime.")\n')
    (project/'VISION.md').write_text('# Vision\nKeep data local unless the customer explicitly requests an export.\n',encoding='utf-8')
    first=run('setup','--no-view','--document','VISION.md');second=run('setup','--no-view','--document','VISION.md')
    assert first['documents']==second['documents']
    report=run('doctor');assert report['mcp_process_verified'] and report['integrity']=='ok'
    runtime=subprocess.run([str(python),'-I','-c',"from pathlib import Path; import json,memory_module; from memory_module.install import launcher; p=Path(memory_module.__file__).parent; assert all((p/'agents'/(role+'.md')).is_file() for role in ('intent','outcome','recovery')); print(json.dumps({'version':memory_module.__version__,'launcher':launcher()}))"],check=True,capture_output=True,text=True,cwd=project,env=env)
    installed=json.loads(runtime.stdout)
    assert Path(installed['launcher'][0]).samefile(python), (installed['launcher'],str(python))
    assert installed['launcher'][1:]==['-I','-m','memory_module.cli']
    child=subprocess.run(installed['launcher']+['doctor','--project',str(project)],check=True,capture_output=True,text=True,cwd=project,env=env)
    assert json.loads(child.stdout)['mcp_process_verified']
    view=run('view','--output',str(project/'view.html'),'--no-open','--include-bodies');assert Path(view['path']).is_file()
    assert 'Keep data local unless' in Path(view['path']).read_text(encoding='utf-8')
    live=run('view','--no-open')
    try:
        from urllib.request import urlopen
        with urlopen(live['url']+'api/health',timeout=5) as response:health=json.load(response)
        assert health['database']==first['database'] and health['interactive']
        from urllib.request import Request
        from urllib.parse import urlsplit
        body={'operation':'plan','request_key':'installed-plan','data':{'title':'Inspect installed behaviour','objective':'Verify the packaged workspace.','criterion':'The live API stores this plan.','subject':'code','payload':{'state':'ready','scope':'Inspect the installed package.','next_action':'Read the saved plan.','autonomy':'suggest','reason':'The installed smoke check requests it.'}}}
        headers={'Content-Type':'application/json','Origin':'http://'+urlsplit(live['url']).netloc,'X-Project-Memory':health['csrf']}
        with urlopen(Request(live['url']+'api/actions',data=json.dumps(body).encode(),headers=headers),timeout=5) as response:assert json.load(response)['episode_id']
    finally:
        import signal
        os.kill(live['pid'],signal.SIGTERM)
    backup=project/'backup.sqlite';run('backup',str(backup));assert backup.is_file()
    run('uninstall');assert Path(first['database']).is_file()
    print(json.dumps({'passed':True,'wheel':wheel.name,'checks':['fresh wheel installation','CLI entry point','persistent installed launcher','three bundled agent roles','idempotent setup and capture','separate MCP process handshake and read','packaged offline HTML viewer','packaged live HTTP viewer','authenticated workspace write','SQLite backup','uninstall preserves data'],'runtime_dependencies':0},indent=2))
