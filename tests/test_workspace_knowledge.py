import base64
import io
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
import zipfile

from memory_module import Memory, Conflict, InvalidRecord, dumps
from memory_module import skills, maps
from memory_module.install import setup
from memory_module.live import Viewer
from memory_module.mcp import dispatch, write, tool_result
from memory_module.workspace import action
from memory_module.viewer import export_html, html_template


def files(name='inspect-evidence', body='Read the original evidence. Preserve its exceptions.'):
    manifest = f'---\nname: {name}\ndescription: Inspect evidence before making a recommendation.\n---\n{body}'
    return [{'path': 'SKILL.md', 'content': base64.b64encode(manifest.encode()).decode()},
            {'path': 'references/check.md', 'content': base64.b64encode(b'The exception applies only to tagged records.').decode()},
            {'path': 'scripts/check.py', 'content': base64.b64encode(b'raise RuntimeError("Never execute an import")').decode()}]


class WorkspaceKnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.m = Memory(setup(self.root, requirements=['Preserve conditions and exceptions.'])['database'])
        self.work = action(self.m, 'plan', {'title': 'Evaluate the evidence', 'objective': 'Make an informed recommendation.',
            'criterion': 'The recommendation links to original evidence.', 'subject': 'research',
            'payload': {'state': 'ready', 'next_action': 'Inspect the evidence.', 'scope': 'Preserve conditions and exceptions.',
                        'autonomy': 'act', 'reason': 'The user requests this evaluation.', 'owner': 'agent', 'priority': 'normal', 'sprint_id': None, 'depends_on': []}}, 'work')
        self.ep = self.work['episode_id']

    def tearDown(self):
        self.m.close()
        self.temp.cleanup()

    def version(self):
        return self.m.episode(self.ep)['version']

    def import_skill(self, name='inspect-evidence'):
        return action(self.m, 'skill_import', {'files': files(name), 'expected_version': 0}, 'import:' + name)['skill']

    def selection(self, skill, state='selected'):
        return {'episode_id': self.ep, 'expected_version': self.version(), 'skill_id': skill['id'],
                'revision': skill['revision'], 'state': state, 'reason': 'This method preserves the required evidence and exceptions.'}

    def diagram(self, status='proposed'):
        return {'episode_id': self.ep, 'expected_version': self.version(), 'mode': 'architecture', 'map_version': 0,
                'reason': 'Show how the recommendation depends on evidence.',
                'nodes': [{'id': 'node_evidence', 'title': 'Evidence review', 'kind': 'process',
                           'description': 'The reviewer checks the original evidence.', 'reference': self.work['id'], 'status': status},
                          {'id': 'node_report', 'title': 'Recommendation', 'kind': 'deliverable',
                           'description': 'The report preserves conditions and exceptions.', 'reference': '', 'status': status}],
                'edges': [{'id': 'edge_dependency', 'from': 'node_report', 'to': 'node_evidence', 'type': 'depends_on',
                           'reason': 'The recommendation requires the completed evidence review.', 'status': status}]}

    def counts(self):
        return [self.m.db.execute('SELECT count(*) FROM ' + name).fetchone()[0] for name in ('sources', 'events')]

    def test_import_retry_exact_replacement_and_original_files(self):
        payload = {'files': files(), 'expected_version': 0}
        first = action(self.m, 'skill_import', payload, 'import-once')
        before = self.counts()
        self.assertEqual(action(self.m, 'skill_import', payload, 'import-once'), first)
        self.assertEqual(before, self.counts())
        sid = first['skill']['id']
        self.assertIn('Never execute', skills.read(self.m, sid, 'scripts/check.py')['text'])
        self.assertFalse((self.root / '.agents/skills/inspect-evidence').exists())
        with self.assertRaises(Conflict):
            action(self.m, 'skill_import', {'files': files(body='New instructions.'), 'expected_version': 1}, 'wrong-replacement')
        second = action(self.m, 'skill_import', {'files': files(body='New instructions.'), 'expected_version': 1, 'replace_skill_id': sid}, 'replace')
        self.assertEqual(second['skill']['version'], 2)
        self.assertTrue(any(e['from'] == sid and e['to'] == second['skill']['id'] and e['type'] == 'revised_by' for e in maps.relationships(self.m, sid)['edges']))
        self.assertIn('Preserve its exceptions', skills.read(self.m, sid, 'SKILL.md')['text'])
        self.assertEqual(skills.read(self.m, sid)['status'], 'superseded')

    def test_zip_import_supports_one_enclosing_folder(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            for f in files(): archive.writestr('method/' + f['path'], base64.b64decode(f['content']))
        result = action(self.m, 'skill_import', {'archive': base64.b64encode(stream.getvalue()).decode(), 'expected_version': 0}, 'zip')
        self.assertEqual(len(result['skill']['files']), 3)

    def test_unsafe_packages_are_rejected_without_writes(self):
        before = self.counts()
        for name in ('../SKILL.md', '/SKILL.md', './SKILL.md', 'x/../../SKILL.md', '.hidden/SKILL.md', 'x\\SKILL.md'):
            with self.subTest(name=name), self.assertRaises(InvalidRecord):
                action(self.m, 'skill_import', {'files': [{**files()[0], 'path': name}], 'expected_version': 0}, name)
        for name in ('../SKILL.md', '/SKILL.md', 'x/../../SKILL.md'):
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, 'w') as archive: archive.writestr(name, base64.b64decode(files()[0]['content']))
            with self.assertRaises(InvalidRecord): skills.unpack(base64.b64encode(stream.getvalue()).decode())
        with self.assertRaises(InvalidRecord): skills.package(files() + [files()[0]])
        with self.assertRaises(InvalidRecord): skills.package(files() + [{'path': 'large.txt', 'content': base64.b64encode(b'x' * 1_000_001).decode()}])
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            info = zipfile.ZipInfo('SKILL.md'); info.external_attr = 0o120777 << 16
            archive.writestr(info, '/outside')
        with self.assertRaises(InvalidRecord): skills.unpack(base64.b64encode(stream.getvalue()).decode())
        self.assertEqual(before, self.counts())

    def test_metadata_handles_common_scalars_and_preserves_original(self):
        text = '---\nname: inspect-evidence\ndescription: >-\n  Inspect the original evidence.\n  Preserve its exceptions.\nlicense: "MIT"\n---\nDo the work.'
        self.assertEqual(skills.metadata(text)['description'], 'Inspect the original evidence. Preserve its exceptions.')
        for text in ('No frontmatter', '---\nname: Bad Name\ndescription: example\n---', '---\nname: valid\ndescription: *anchor\n---'):
            with self.assertRaises(InvalidRecord): skills.metadata(text)

    def test_local_changes_preserve_snapshot_and_can_release_missing_skill(self):
        folder = self.root / '.agents/skills/inspect-evidence'; folder.mkdir(parents=True)
        for item in files():
            target = folder / item['path']; target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(base64.b64decode(item['content']))
        skill = skills.read(self.m, skills.catalog(self.m)['skills'][0]['id'])
        action(self.m, 'skill_selection', self.selection(skill), 'select-local')
        selected = skills.selections(self.m, self.ep)[0]
        original = skills.read(self.m, selected['snapshot_source_id'], 'references/check.md')['text']
        with Viewer(self.m.path, 'fixture-token') as server:
            before = server.revision()
            (folder / 'references/check.md').write_text('Changed condition.')
            self.assertNotEqual(server.revision(), before)
        self.assertEqual(skills.selections(self.m, self.ep)[0]['current_status'], 'changed')
        self.assertIn('tagged records', original)
        self.assertEqual(skills.read(self.m, selected['snapshot_source_id'], 'references/check.md')['text'], original)
        (folder / 'SKILL.md').unlink()
        self.assertEqual(skills.selections(self.m, self.ep)[0]['current_status'], 'missing')
        result = action(self.m, 'skill_selection', self.selection(skill, 'released'), 'release-missing')
        self.assertEqual(result['selections'][0]['state'], 'released')

    def test_discovery_does_not_follow_outside_links(self):
        folder = self.root / '.agents/skills'; folder.mkdir(parents=True)
        outside = self.root / 'outside'; outside.mkdir()
        (outside / 'SKILL.md').write_bytes(base64.b64decode(files()[0]['content']))
        try:
            (folder / 'linked').symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            self.skipTest('This platform does not permit test symlinks: ' + str(exc))
        self.assertEqual(skills.catalog(self.m)['skills'], [])
        local = folder / 'local'; local.mkdir()
        (local / 'SKILL.md').write_bytes(base64.b64decode(files()[0]['content']))
        (local / 'linked.md').symlink_to(outside / 'SKILL.md')
        with self.assertRaises(InvalidRecord): skills.read(self.m, skills.catalog(self.m)['skills'][0]['id'])

    def test_selection_is_explicit_and_agent_use_needs_evidence(self):
        skill = self.import_skill()
        before = self.counts()
        with self.assertRaises(InvalidRecord): write(self.m, 'skill_selection', 'unsupported-use', self.selection(skill, 'reported_use'))
        self.assertEqual(before, self.counts())
        result = write(self.m, 'skill_selection', 'use-report', {**self.selection(skill, 'reported_use'), 'actor': 'assistant',
            'evidence': [{'source_id': self.m.read(self.work['id'])['evidence'][0]['source_id'], 'reason': 'This fixture links the explicit use report to its requested work.'}]})
        self.assertEqual(result['selections'][0]['state'], 'reported_use')
        self.assertTrue(any('not independent execution verification' in item['reason'] for item in self.m.read(result['event']['id'])['evidence']))

    def test_map_versions_conflicts_and_history_survive_reopening(self):
        data = self.diagram(); result = action(self.m, 'map', data, 'map-first')
        before = self.counts()
        self.assertEqual(action(self.m, 'map', data, 'map-first'), result)
        with self.assertRaises(Conflict): action(self.m, 'map', data, 'stale-map')
        self.assertEqual(before, self.counts())
        revised = {**data, 'expected_version': self.version(), 'map_version': 1, 'reason': 'The user confirms the dependency.'}
        revised['edges'] = [{**data['edges'][0], 'status': 'confirmed'}]
        second = action(self.m, 'map', revised, 'confirm-map')
        self.assertEqual(second['map']['version'], 2)
        original = self.m.read(result['map']['source_id'], detail=True)
        self.assertEqual(json.loads(original['body'])['edges'][0]['status'], 'proposed')
        with Memory(self.m.path) as reopened:
            self.assertEqual(maps.model(reopened, self.ep, 'architecture')['edges'][0]['status'], 'confirmed')

    def test_map_rejects_bad_links_and_unapproved_agent_confirmation(self):
        data = self.diagram(); before = self.counts()
        with self.assertRaises(InvalidRecord): action(self.m, 'map', {**data, 'edges': [{**data['edges'][0], 'to': 'missing'}]}, 'bad-link')
        with self.assertRaises(InvalidRecord): write(self.m, 'map', 'false-confirm', {**self.diagram('confirmed'), 'actor': 'assistant',
            'evidence': [{'source_id': self.m.read(self.work['id'])['evidence'][0]['source_id'], 'reason': 'The fixture supports a proposal, not human confirmation.'}]})
        self.assertEqual(before, self.counts())

    def test_relationship_direction_and_bounded_retrieval(self):
        skill = self.import_skill(); result = action(self.m, 'skill_selection', self.selection(skill), 'selection')
        graph = maps.relationships(self.m, result['event']['id'], 10)
        self.assertTrue(any(e['from'] == skill['id'] and e['to'] == result['event']['id'] for e in graph['edges']))
        tiny = maps.relationships(self.m, self.ep, 1)
        self.assertEqual(len(tiny['nodes']), 1); self.assertTrue(tiny['truncated'])
        result = dispatch(self.m, 'memory_get', {'view': 'skills', 'max_chars': 2000})
        self.assertLessEqual(len(dumps(tool_result(result))), 2000)
        self.assertNotIn('files', result['skills'][0])
        detail = dispatch(self.m, 'memory_get', {'view': 'skill', 'id': skill['id'], 'file': 'SKILL.md', 'max_chars': 2000})
        self.assertIn('exceptions', detail['text'])
        action(self.m, 'map', self.diagram(), 'diagram')
        first = dispatch(self.m, 'memory_get', {'view': 'map', 'id': self.ep, 'mode': 'architecture', 'limit': 1})
        self.assertTrue(first['more']); self.assertEqual(first['node_count'], 2)
        second = dispatch(self.m, 'memory_get', {'view': 'map', 'id': self.ep, 'mode': 'architecture', 'limit': 1, 'offset': first['next_offset']})
        self.assertFalse(second['more']); self.assertNotEqual(first['nodes'][0]['id'], second['nodes'][0]['id'])

    def test_human_requirement_and_lesson_reviews_preserve_exceptions(self):
        action(self.m, 'requirements', {'requirements': ['Preserve tagged exceptions.'], 'reason': 'The user clarifies the scope.', 'expected_version': self.m.direction()['version']}, 'requirements')
        self.assertEqual(self.m.requirements, ['Preserve tagged exceptions.'])
        source = self.m.source('fixture-result', 'Observed evidence', 'The fixture preserves exceptions.', 'The tagged record passes.', 'tool')
        lesson = self.m.record(self.ep, 'lesson', {'when': 'The reviewer evaluates tagged records.', 'do': 'Preserve the explicit exception.',
            'because': 'The evidence requires this condition.', 'exceptions': 'This does not apply to untagged records.'},
            expected_version=self.version(), request_key='lesson', actor='test', evidence=[{'source_id': source['id'], 'reason': 'This fixture establishes the condition.'}])
        action(self.m, 'lesson_review', {'lesson_id': lesson['id'], 'expected_version': self.version(), 'status': 'accepted', 'reason': 'The user checks the complete condition and exception.'}, 'accept')
        self.assertEqual(self.m.read(lesson['id'])['status'], 'accepted')
        self.assertIn('untagged', self.m.read(lesson['id'])['payload']['exceptions'])
        self.m.source('fixture-result', 'Changed evidence', 'The condition changes.', 'The tagged record fails.', 'tool')
        before = self.counts()
        with self.assertRaises(InvalidRecord): action(self.m, 'lesson_review', {'lesson_id': lesson['id'], 'expected_version': self.version(), 'status': 'accepted', 'reason': 'Attempt to accept stale evidence.'}, 'stale-accept')
        self.assertEqual(before, self.counts())

    def test_map_reference_drift_and_plan_dependency_direction(self):
        source = self.m.source('interview', 'Client interview', 'The client explains the exception.', 'Preserve the tagged case.', 'tool', subject='research')
        data = self.diagram()
        data['nodes'][0]['reference'] = source['id']
        first = action(self.m, 'map', data, 'linked-map')
        self.m.source('interview', 'Revised interview', 'The client changes the exception.', 'The tagged case now requires review.', 'tool', subject='research')
        self.assertEqual(maps.model(self.m, self.ep, 'architecture')['reference_status'][source['id']], 'superseded')
        self.assertTrue(any(e['from'] == source['id'] and e['to'] == first['event']['id'] for e in maps.relationships(self.m, source['id'])['edges']))
        prerequisite = self.m.start('Review inputs', 'Inspect the original input.', 'review', 'The original input is checked.', subject='research')
        payload = self.m.read(self.work['id'])['payload']
        action(self.m, 'plan', {'episode_id': self.ep, 'expected_version': self.version(), 'payload': {**payload,
            'depends_on': [{'episode_id': prerequisite['id'], 'reason': 'The recommendation requires the input review.'}]}}, 'dependency')
        for focus in (self.ep, prerequisite['id']):
            self.assertTrue(any(e['type'] == 'depends_on' and e['from'] == self.ep and e['to'] == prerequisite['id'] for e in maps.relationships(self.m, focus)['edges']))

    def test_real_mcp_process_import_retry_and_lazy_reads(self):
        initialize = {'jsonrpc': '2.0', 'id': 0, 'method': 'initialize', 'params': {'protocolVersion': '2025-11-25'}}
        request = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': 'memory_write',
            'arguments': {'operation': 'skill_import', 'request_key': 'process-import', 'data': {'files': files(), 'expected_version': 0}}}}
        command = [sys.executable, '-m', 'memory_module.mcp', '--db', str(self.m.path)]
        results = []
        for _ in range(2):
            run = subprocess.run(command, input=dumps(initialize) + '\n' + dumps(request) + '\n', text=True, encoding='utf-8', capture_output=True, check=True, timeout=10)
            reply = json.loads(run.stdout.splitlines()[-1])['result']
            self.assertFalse(reply['isError']); results.append(json.loads(reply['content'][0]['text']))
        self.assertEqual(results[0]['skill']['id'], results[1]['skill']['id'])
        read = {**request, 'params': {'name': 'memory_get', 'arguments': {'view': 'skill', 'id': results[0]['skill']['id'], 'file': 'references/check.md', 'max_chars': 2000}}}
        run = subprocess.run(command, input=dumps(initialize) + '\n' + dumps(read) + '\n', text=True, encoding='utf-8', capture_output=True, check=True, timeout=10)
        reply = json.loads(run.stdout.splitlines()[-1])['result']
        self.assertFalse(reply['isError']); self.assertLessEqual(len(dumps(reply)), 2000)
        self.assertIn('tagged records', json.loads(reply['content'][0]['text'])['text'])

    def test_export_embeds_assets_and_maps_without_package_bodies(self):
        skill = self.import_skill(); action(self.m, 'skill_selection', self.selection(skill), 'select')
        action(self.m, 'map', self.diagram(), 'map')
        destination = self.root / 'snapshot.html'; export_html(self.m, destination, include_bodies=True)
        html = destination.read_text()
        self.assertIn('node_evidence', html)
        self.assertNotIn(files()[2]['content'], html)
        self.assertNotIn('__WORKSPACE_JS__', html)
        self.assertIn('function renderSkills()', html_template())
        self.assertIn('data:font/woff2;base64', html)


if __name__ == '__main__': unittest.main()
