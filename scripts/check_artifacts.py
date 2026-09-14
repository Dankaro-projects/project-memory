"""Reject operational data and unexpected members in public distributions."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
import tarfile
import zipfile

root=Path(sys.argv[1] if len(sys.argv)>1 else 'dist')
artifacts=sorted(root.glob('*.whl'))+sorted(root.glob('*.tar.gz'))
if len(artifacts)!=2:raise SystemExit('Expected exactly one wheel and one source distribution.')
report=[]
for path in artifacts:
    if path.suffix=='.whl':
        with zipfile.ZipFile(path) as archive:members={i.filename:archive.read(i) for i in archive.infolist() if not i.is_dir()}
    else:
        with tarfile.open(path) as archive:members={'/'.join(PurePosixPath(i.name).parts[1:]):archive.extractfile(i).read() for i in archive.getmembers() if i.isfile()}
    for name,body in members.items():
        parts=PurePosixPath(name).parts
        if any(p in {'.memory','.codex','results','__pycache__','.env'} for p in parts) or re.search(r'\.(sqlite\w*|db\w*|jsonl|pyc|zip)$',name):
            raise SystemExit('Private or generated member: '+name)
        if name.startswith('memory_module/') and PurePosixPath(name).suffix not in {'.py','.html'} and name not in {'memory_module/agents/outcome.md','memory_module/agents/intent.md','memory_module/agents/recovery.md','memory_module/assets/manrope-latin-400.woff2','memory_module/assets/manrope-latin-700.woff2'}:
            raise SystemExit('Unexpected runtime member: '+name)
        if path.suffix=='.whl' and not (name.startswith('memory_module/') or parts[0].endswith('.dist-info')):
            raise SystemExit('Unexpected wheel member: '+name)
        if re.search(rb'(?:ghp_[A-Za-z0-9]{30,}|sk-proj-[A-Za-z0-9_-]{30,}|-----BEGIN (?:RSA |OPENSSH )?PRIVATE KEY-----)',body):
            raise SystemExit('Possible credential in '+name)
    if 'memory_module/viewer.html' not in members:raise SystemExit('The packaged viewer is missing.')
    for weight in (400,700):
        if f'memory_module/assets/manrope-latin-{weight}.woff2' not in members:raise SystemExit('A packaged viewer font is missing.')
    report.append({'file':path.name,'bytes':path.stat().st_size,'members':len(members),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
print(json.dumps({'passed':True,'artifacts':report},indent=2))
