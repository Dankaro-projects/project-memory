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
        result=subprocess.run([str(cli),*args],capture_output=True,text=True,cwd=project,env=env,timeout=30)
        if result.returncode:raise RuntimeError(f'{args}: {result.stderr}')
        return json.loads(result.stdout)
    shadow=project/'memory_module';shadow.mkdir()
    (shadow/'__init__.py').write_text('raise RuntimeError("Project code shadowed the installed runtime.")\n')
    (project/'VISION.md').write_text('# Vision\nKeep data local unless the customer explicitly requests an export.\n',encoding='utf-8')
    first=run('setup','--no-view','--document','VISION.md');second=run('setup','--no-view','--document','VISION.md')
    assert first['documents']==second['documents']
    report=run('doctor');assert report['mcp_process_verified'] and report['integrity']=='ok'
    runtime=subprocess.run([str(python),'-I','-c',"from pathlib import Path; import json,memory_module; from memory_module.install import launcher; p=Path(memory_module.__file__).parent; assert all((p/'agents'/(role+'.md')).is_file() for role in ('intent','outcome','recovery')); assert not any((p/(name+'.py')).exists() for name in ('maps','skills','project_dependencies','review_logs')); import memory_module.api as api; print(json.dumps({'version':memory_module.__version__,'launcher':launcher(),'endpoints':sorted(api.ENDPOINTS)}))"],check=True,capture_output=True,text=True,cwd=project,env=env)
    installed=json.loads(runtime.stdout)
    assert Path(installed['launcher'][0]).samefile(python), (installed['launcher'],str(python))
    assert installed['launcher'][1:]==['-I','-X','utf8','-m','memory_module.cli']
    assert {'health','now','work','lineage','learning','agents','kickoff','plan','architecture'}<=set(installed['endpoints']),installed['endpoints']
    child=subprocess.run(installed['launcher']+['doctor','--project',str(project)],check=True,capture_output=True,text=True,cwd=project,env=env)
    assert json.loads(child.stdout)['mcp_process_verified']
    view=run('view','--output',str(project/'view.html'),'--no-open','--include-bodies');assert Path(view['path']).is_file()
    html=Path(view['path']).read_text(encoding='utf-8')
    assert 'Keep data local unless' in html
    assert '"responses"' in html and '__WORKSPACE_JS__' not in html
    # The panel is read only, so the installed package writes the plan and its comment through the workspace actions
    # that user_action runs, and the live panel must read them and refuse every write.
    plan={'title':'Inspect installed behaviour','objective':'Verify the packaged workspace.','criterion':'The live API stores this plan.','subject':'code','payload':{'state':'ready','scope':'Inspect the installed package.','next_action':'Read the saved plan.','autonomy':'suggest','reason':'The installed smoke check requests it.'}}
    script=("import json,sys; from memory_module.core import Memory; from memory_module.workspace import action; plan=json.loads(sys.argv[2])\n"
        "with Memory(sys.argv[1]) as m:\n"
        " print(json.dumps({'episode':action(m,'plan',plan,'installed-plan')['episode_id']}))")
    episode=json.loads(subprocess.run([str(python),'-I','-c',script,first['database'],json.dumps(plan)],check=True,capture_output=True,text=True,cwd=project,env=env).stdout)['episode']
    live=run('view','--no-open')
    try:
        from urllib.request import urlopen, Request
        from urllib.error import HTTPError
        from urllib.parse import urlsplit, quote
        with urlopen(live['url']+'api/health',timeout=5) as response:health=json.load(response)
        assert health['database']==first['database'] and health['interactive'] and 'csrf' not in health
        def get(path):
            with urlopen(live['url']+path,timeout=5) as response:return json.load(response)
        now=get('api/now')
        assert any(card['id']==episode for part in now.values() if isinstance(part,list) for card in part if isinstance(card,dict)),sorted(now)
        work=get('api/work?id='+quote(episode))
        assert work['card']['id']==episode and work['history']['total']>=1
        try:
            urlopen(Request(live['url']+'api/actions',data=b'{}',headers={'Content-Type':'application/json','Origin':'http://'+urlsplit(live['url']).netloc}),timeout=5)
            raise AssertionError('The live panel accepted a write.')
        except HTTPError as error:
            assert error.code==405,error.code
        assert get('api/lineage?id='+quote(episode))['focus']==episode
        learning=get('api/learning');assert 'guards' in learning and 'proposed_lessons' in learning
        agents=get('api/agents');assert 'runs' in agents and 'hosts' in agents
    finally:
        import signal
        os.kill(live['pid'],signal.SIGTERM)
    backup=project/'backup.sqlite';run('backup',str(backup));assert backup.is_file()
    run('uninstall');assert Path(first['database']).is_file()
    print(json.dumps({'passed':True,'wheel':wheel.name,'checks':['fresh wheel installation','CLI entry point','persistent installed launcher','three bundled agent roles','retired modules absent','idempotent setup and capture','separate MCP process handshake and read','packaged offline export of API responses','packaged live HTTP viewer','workspace plan action of the installed package, read by the live panel','live panel refuses every write','packaged now, work, lineage, learning and agents endpoints','SQLite backup','uninstall preserves data'],'runtime_dependencies':0},indent=2))
