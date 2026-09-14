"""Build a deterministic MCPB from public runtime files, then run its entry point."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

root=Path(__file__).resolve().parents[1]
manifest=json.loads((root/'bundle/manifest.json').read_text())
output=root/'dist'/('project-memory-'+manifest['version']+'.mcpb');output.parent.mkdir(exist_ok=True)
files={p.name:p for p in (root/'bundle').iterdir() if p.is_file()}
files['LICENSE']=root/'LICENSE'
files.update({p.relative_to(root).as_posix():p for p in (root/'memory_module').iterdir() if p.suffix in {'.py','.html'}})
files.update({p.relative_to(root).as_posix():p for p in (root/'memory_module/agents').glob('*.md')})
files.update({p.relative_to(root).as_posix():p for p in (root/'memory_module/assets').glob('*.woff2')})
with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED) as archive:
    for name,path in sorted(files.items()):
        info=zipfile.ZipInfo(name,date_time=(2026,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o644<<16
        archive.writestr(info,path.read_bytes())
with tempfile.TemporaryDirectory(prefix='project-memory-bundle-') as directory:
    unpacked=Path(directory)/'bundle';project=Path(directory)/'project';project.mkdir()
    with zipfile.ZipFile(output) as archive:archive.extractall(unpacked)
    messages=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25'}},
              {'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'memory_get','arguments':{'view':'direction'}}}]
    result=subprocess.run([sys.executable,str(unpacked/'server.py'),str(project)],input=''.join(json.dumps(m)+'\n' for m in messages),text=True,capture_output=True,check=True,cwd=project,timeout=20)
    replies=[json.loads(line) for line in result.stdout.splitlines()]
    assert len(replies)==2 and not replies[-1]['result']['isError']
    assert (project/'.memory/project.sqlite').exists()
print(json.dumps({'passed':True,'bundle':output.name,'bytes':output.stat().st_size,'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),'checks':['deterministic runtime-only bundle','fresh selected project setup','actual bundled MCP process handshake and read'],'limitation':'The bundle entry point is tested; this does not verify installation in every desktop client.'},indent=2))
