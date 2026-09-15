import json
from pathlib import Path
import tempfile
import os
import signal
import unittest
from urllib.request import Request, build_opener, ProxyHandler

from memory_module import Memory
from memory_module.install import setup
from memory_module.live import start
from memory_module.project_dependencies import inventory, declarations, dependency_sections
from memory_module.viewer import export_html
from memory_module.workspace import action


class ProjectDependencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.m = Memory(setup(self.root, requirements=['Keep evidence.'])['database'])

    def tearDown(self):
        self.m.close()
        self.temp.cleanup()

    def test_inventory_preserves_manifest_groups_and_declared_versions(self):
        (self.root/'package.json').write_text(json.dumps({'dependencies': {'react': '^19', 'optional': '1'},
            'devDependencies': {'vite': '~8'}, 'peerDependencies': {'react': '>=18'}, 'optionalDependencies': {'optional': '2'}}))
        (self.root/'pyproject.toml').write_text('[project]\ndependencies=["httpx[http2]>=0.28; python_version >= \'3.11\'"]\n'
            '[build-system]\nrequires=["hatchling>=1.27,<2"]\n[project.optional-dependencies]\ntest=["pytest==8"]\n')
        (self.root/'scripts').mkdir()
        (self.root/'scripts/requirements.txt').write_text('# Import utility\nopenpyxl==3.1.5 # verified pin\n')
        (self.root/'node_modules').mkdir()
        (self.root/'node_modules/package.json').write_text('{"dependencies":{"transitive":"1"}}')
        result = inventory(self.m)
        self.assertEqual(len(result['packages']), 8)
        self.assertEqual(result['issues'], [])
        self.assertEqual([(p['group'], p['requirement']) for p in result['packages'] if p['name']=='react'],
                         [('Peer', '>=18'), ('Runtime', '^19')])
        optional = next(p for p in result['packages'] if p['name']=='optional')
        self.assertEqual((optional['group'], optional['requirement']), ('Optional', '2'))
        self.assertEqual(next(p for p in result['packages'] if p['name']=='openpyxl')['manifest'], 'scripts/requirements.txt')
        self.assertIn('python_version', next(p for p in result['packages'] if p['name']=='httpx')['requirement'])

    def test_errors_and_unsupported_directives_are_visible_without_outside_reads(self):
        (self.root/'package.json').write_text('{broken')
        (self.root/'requirements.txt').write_text('-r private.in\nhttps://example.invalid/package.whl\n')
        (self.root/'pyproject.toml').write_text('[project]\ndynamic=["dependencies"]\n')
        result = inventory(self.m)
        self.assertFalse(result['packages'])
        self.assertEqual(len(result['issues']), 4)
        outside = self.root.parent / (self.root.name + '-outside.json')
        outside.write_text('{"dependencies":{"secret":"1"}}')
        try:
            (self.root/'package.json').unlink()
            try: (self.root/'package.json').symlink_to(outside)
            except OSError as exc: self.skipTest(str(exc))
            self.assertNotIn('secret', json.dumps(inventory(self.m)))
            self.assertTrue(any('outside' in i['message'] for i in inventory(self.m)['issues']))
        finally:
            outside.unlink()

    def test_recorded_usage_keeps_evidence_status_and_snapshot_scope(self):
        work = action(self.m, 'plan', {'title':'Data import','objective':'Import verified data.','criterion':'The data has evidence.',
            'payload':{'state':'ready','scope':'Use approved data.','next_action':'Inspect the source.','autonomy':'act','reason':'The user requests it.'}}, 'work')
        evidence = self.m.source('reason','Storage decision','Data stays local.','Use the local store for offline imports.','tool')
        diagram = {'episode_id':work['episode_id'],'expected_version':1,'mode':'architecture','map_version':0,'reason':'Explain storage use.',
            'nodes':[{'id':'node_app','title':'Importer','kind':'component','description':'Imports rows.','reference':'','status':'confirmed'},
                     {'id':'node_store','title':'Local storage','kind':'system','description':'Keeps imports available offline.','reference':evidence['id'],'status':'proposed'}],
            'edges':[{'id':'edge_use','from':'node_app','to':'node_store','type':'uses','reason':'The importer needs offline records.','status':'proposed'}]}
        action(self.m, 'map', diagram, 'map')
        row = inventory(self.m)['recorded'][0]
        self.assertEqual(row['used_by'][0]['name'], 'Importer')
        self.assertEqual(row['reference'], evidence['id'])
        self.assertEqual(row['status'], 'proposed')
        self.assertFalse(inventory(self.m, episode_ids=set())['recorded'])
        self.m.source('reason','Storage decision','Changed decision.','The storage boundary needs review.','tool')
        self.assertEqual(inventory(self.m)['recorded'][0]['reference_status'], 'superseded')
        output = self.root/'snapshot.html'
        export_html(self.m, output)
        self.assertIn('dependency-table', output.read_text())
        self.assertIn('Keeps imports available offline.', output.read_text())

    def test_real_http_refreshes_changed_manifests_even_with_previous_etag(self):
        manifest = self.root/'package.json'
        manifest.write_text('{"dependencies":{"react":"19"}}')
        server = start(self.m.path)
        try:
            url = server['url'] + 'api/dependencies'
            opener = build_opener(ProxyHandler({}))
            with opener.open(url) as response:
                first = json.load(response); tag = response.headers['ETag']
            self.assertEqual(first['packages'][0]['requirement'], '19')
            manifest.write_text('{"dependencies":{"react":"20"}}')
            with opener.open(Request(url, headers={'If-None-Match':tag})) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(json.load(response)['packages'][0]['requirement'], '20')
        finally:
            os.kill(server['pid'], signal.SIGTERM)

    def test_invalid_shapes_do_not_fabricate_an_empty_inventory(self):
        for name, text in [('package.json','[]'), ('package.json','{"dependencies":[]}'),
                           ('pyproject.toml','project=[]'), ('pyproject.toml','[project]\noptional-dependencies=[]')]:
            with self.subTest(text=text), self.assertRaises(ValueError): declarations(name, text)

    def test_documented_roles_are_available_without_maps_and_keep_full_exceptions(self):
        path = self.root/'architecture.md'
        section = '## Software dependencies\n\n| Dependency | Role |\n|---|---|\n| React | Browser UI. |\n\n### Exception\nThe legacy import uses Python.\n\n'
        body = '# Architecture\n\n```md\n## Fake dependencies\n```\n\n' + section + '## Other scope\nDo not include this section.\n'
        path.write_text(body)
        saved = self.m.document(str(path))
        result = inventory(self.m)
        self.assertFalse(result['recorded'])
        self.assertEqual(len(result['documentation']), 1)
        document = result['documentation'][0]
        self.assertEqual(document['body'], section)
        self.assertEqual(document['source_id'], saved['id'])
        self.assertEqual(document['status'], 'current_copy')
        path.write_text(body+'Later change.\n')
        self.assertEqual(inventory(self.m)['documentation'][0]['status'], 'file_changed')
        self.assertEqual(inventory(self.m)['documentation'][0]['body'], section)
        self.assertFalse(inventory(self.m, source_ids=set())['documentation'])
        output = self.root/'metadata.html'
        export_html(self.m, output, include_bodies=False)
        snapshot = json.loads(output.read_text().split('<script id="memory-data" type="application/json">')[1].split('</script>')[0])
        self.assertFalse(snapshot['dependencies']['documentation'])
        self.assertEqual(dependency_sections('Dependencies.md','Unheaded original text.'),
                         [{'heading':'Dependencies.md','body':'Unheaded original text.'}])
