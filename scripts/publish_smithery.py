"""Publish a validated MCPB using its actual MCP tool schemas and existing CLI login."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
import zipfile


def publish(bundle, name):
    bundle=Path(bundle).resolve()
    with tempfile.TemporaryDirectory(prefix='project-memory-publish-') as directory:
        root=Path(directory);server=root/'bundle';project=root/'project';project.mkdir()
        with zipfile.ZipFile(bundle) as archive:
            archive.extractall(server)
            manifest=json.loads(archive.read('manifest.json'))
        requests=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25'}},
                  {'jsonrpc':'2.0','id':2,'method':'tools/list'}]
        run=subprocess.run([sys.executable,str(server/'server.py'),str(project)],
            input=''.join(json.dumps(r)+'\n' for r in requests),text=True,capture_output=True,check=True,timeout=20)
        tools=json.loads(run.stdout.splitlines()[-1])['result']['tools']
    payload={'type':'stdio','runtime':'python',
        'serverCard':{'serverInfo':{'name':manifest['name'],'version':manifest['version']},'tools':tools},
        'configSchema':{'type':'object','properties':{'project':{'type':'string','title':'Project directory',
            'description':manifest['user_config']['project']['description']}},'required':['project']}}
    # Smithery CLI 1.2.0 copies MCPB tool summaries into serverCard, whose API
    # requires inputSchema. Read the actual tools instead; keep the MCPB unchanged.
    login=subprocess.run(['npm','exec','--yes','--package','smithery@1.2.0','--','smithery','auth','whoami','--full'],
                         text=True,capture_output=True,check=True,timeout=30)
    prefix='SMITHERY_API_KEY='
    if not login.stdout.strip().startswith(prefix):raise RuntimeError('Smithery did not return an existing login.')
    token=login.stdout.strip()[len(prefix):]
    boundary='project-memory-'+uuid.uuid4().hex
    parts=[f'--{boundary}\r\nContent-Disposition: form-data; name="payload"\r\n\r\n'.encode()+json.dumps(payload).encode(),
           f'--{boundary}\r\nContent-Disposition: form-data; name="bundle"; filename="server.mcpb"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()+bundle.read_bytes()]
    body=b'\r\n'.join(parts)+f'\r\n--{boundary}--\r\n'.encode()
    request=urllib.request.Request('https://api.smithery.ai/servers/'+name+'/releases',data=body,method='PUT',
        headers={'Authorization':'Bearer '+token,'Content-Type':'multipart/form-data; boundary='+boundary,
                 'User-Agent':'Project-Memory-Publisher/'+manifest['version']})
    try:
        with urllib.request.urlopen(request,timeout=60) as response:return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code==404:
            create=urllib.request.Request('https://api.smithery.ai/servers/'+name,data=b'{}',method='PUT',
                headers={'Authorization':'Bearer '+token,'Content-Type':'application/json',
                         'User-Agent':'Project-Memory-Publisher/'+manifest['version']})
            with urllib.request.urlopen(create,timeout=30) as response:json.load(response)
            with urllib.request.urlopen(request,timeout=60) as response:return json.load(response)
        raise RuntimeError(f'Smithery returned HTTP {error.code}: '+error.read().decode()) from None


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle',type=Path);parser.add_argument('--name',required=True)
    args=parser.parse_args()
    if len(args.name.split('/'))!=2 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_/' for c in args.name):
        parser.error('Use namespace/server for --name.')
    print(json.dumps(publish(args.bundle,args.name),indent=2))
