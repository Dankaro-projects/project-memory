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
    (project/'VISION.md').write_text('# Vision\nKeep data local unless the customer explicitly requests an export.\n',encoding='utf-8')
    first=run('setup','--document','VISION.md');second=run('setup','--document','VISION.md')
    assert first['documents']==second['documents']
    report=run('doctor');assert report['mcp_process_verified'] and report['integrity']=='ok'
    view=run('view','--no-open','--include-bodies');assert Path(view['path']).is_file()
    assert 'Keep data local unless' in Path(view['path']).read_text(encoding='utf-8')
    backup=project/'backup.sqlite';run('backup',str(backup));assert backup.is_file()
    run('uninstall');assert Path(first['database']).is_file()
    print(json.dumps({'passed':True,'wheel':wheel.name,'checks':['fresh wheel installation','CLI entry point','idempotent setup and capture','separate MCP process handshake and read','packaged HTML viewer','SQLite backup','uninstall preserves data'],'runtime_dependencies':0},indent=2))
