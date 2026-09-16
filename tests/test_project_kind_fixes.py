"""Regression tests for review findings that span documents, guards and templates for non-code projects."""
import shutil
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile

from memory_module import Memory, codex_host, guards, planning, templates
from memory_module.core import InvalidRecord


def office_file(path, parts):
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('[Content_Types].xml', '<Types/>')
        for name, text in parts.items():
            archive.writestr(name, text)


class DocumentCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name).resolve()
        self.m = Memory.create(self.root / '.memory' / 'memory.sqlite', 'Engagement', ['Keep client evidence traceable.'])

    def tearDown(self):
        self.m.close()
        shutil.rmtree(self.temp.name, ignore_errors=True)

    def test_office_and_text_files_are_captured_as_text(self):
        office_file(self.root / 'deck.pptx', {'ppt/slides/slide1.xml':
            '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            '<p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>Prices rose by 4 percent.</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>'})
        office_file(self.root / 'memo.docx', {'word/document.xml':
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
            '<w:p><w:r><w:t>The finance lead owns the budget.</w:t></w:r></w:p></w:body></w:document>'})
        sheet = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
        office_file(self.root / 'model.xlsx', {
            'xl/workbook.xml': f'<workbook xmlns="{sheet}"><sheets><sheet name="Prices"/></sheets></workbook>',
            'xl/sharedStrings.xml': f'<sst xmlns="{sheet}"><si><t>Option</t></si><si><t>Premium</t></si></sst>',
            'xl/worksheets/sheet1.xml': f'<worksheet xmlns="{sheet}"><sheetData><row><c t="s"><v>0</v></c><c><v>120</v></c></row>'
                                         '<row><c t="s"><v>1</v></c><c><v>180</v></c></row></sheetData></worksheet>'})
        (self.root / 'interviews.csv').write_text('name,role\nAnna,Finance lead\n')
        (self.root / 'lead-intake.json').write_text(json.dumps({'name': 'Lead intake', 'nodes': [], 'connections': {}}))
        expected = {'deck.pptx': 'Prices rose by 4 percent.', 'memo.docx': 'The finance lead owns the budget.',
                    'model.xlsx': 'Premium\t180', 'interviews.csv': 'Anna,Finance lead', 'lead-intake.json': 'Lead intake'}
        for name, text in expected.items():
            with self.subTest(name=name):
                captured = self.m.document(str(self.root / name))
                source = self.m.read(captured['id'], detail=True)
                self.assertIn(text, source['body'])
                self.assertTrue(source['source_key'].startswith('local-file:'))
        (self.root / 'notes.md').write_text('# Notes\n')
        self.assertTrue(self.m.read(self.m.document(str(self.root / 'notes.md'))['id'])['source_key'].startswith('local-markdown:'))
        (self.root / 'image.png').write_bytes(b'\x89PNG')
        with self.assertRaisesRegex(InvalidRecord, 'Word, PowerPoint or Excel'):
            self.m.document(str(self.root / 'image.png'))
        (self.root / 'interviews.csv').write_text('name,role\nAnna,Finance lead\nBen,Buyer\n')
        from memory_module.documents import sync
        self.assertEqual(sync(self.m)['created'], 1)


class ReviewSubjectTests(unittest.TestCase):
    def test_a_review_of_a_deliverable_is_recorded_in_a_writing_episode(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            with Memory.create(Path(folder) / 'memory.sqlite', 'Engagement', ['Keep client evidence traceable.']) as memory:
                episode = memory.start('Pricing report', 'Draft the report.', 'writing', 'The client accepts it.', subject='writing')
                source = memory.source('client-feedback', 'Client feedback', 'The client commented on draft 2.', 'Section 3 needs a source.',
                                       'user', subject='writing')
                review = memory.record(episode['id'], 'review',
                                       {'target': 'deliverables/report.md', 'revision': 'Draft 2 sent on 12 September',
                                        'summary': 'The client asked for one change.',
                                        'findings': [{'location': 'Section 3', 'issue': 'The price table has no source.', 'severity': 'minor'}]},
                                       expected_version=episode['version'], request_key='review', actor='assistant',
                                       evidence=[{'source_id': source['id'], 'reason': 'The client feedback states the finding.'}])
                self.assertEqual(memory.read(review['id'])['kind'], 'review')


class McpWriteScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name).resolve()
        self.m = Memory.create(self.root / '.memory' / 'memory.sqlite', 'Automation', ['Keep client data safe.'])
        codex_host.initialize(self.m)
        self.source = self.m.source('user', 'Scope', 'The user sets the scope.', 'Change the lead intake workflow only.', 'user')
        payload = {'state': 'in_progress', 'next_action': 'Edit the workflow.', 'scope': 'The lead intake workflow only.',
                   'autonomy': 'act', 'reason': 'The user asks for it.', 'paths': ['workflows/lead-intake.json'], 'item_type': 'workflow'}
        self.episode = planning.save(self.m, 'work_plan', payload=payload, actor='assistant', evidence=self.evidence(),
                                     title='Lead intake', objective='Change the lead intake workflow.', criterion='The sample lead is posted.',
                                     request_key='plan', session_id='session')['episode_id']

    def tearDown(self):
        self.m.close()
        shutil.rmtree(self.temp.name, ignore_errors=True)

    def evidence(self):
        return [{'source_id': self.source['id'], 'reason': 'The user sets the scope.'}]

    def event(self, tool, tool_input, call):
        return {'hook_event_name': 'PreToolUse', 'session_id': 'session', 'turn_id': 'turn-1', 'tool_name': tool,
                'tool_use_id': call, 'tool_input': tool_input, 'cwd': str(self.root)}

    def test_write_tools_of_mcp_servers_are_scope_targets(self):
        self.assertEqual(guards.edit_targets('mcp__n8n-mcp__update_workflow', {'workflowId': 'Zp8Error00001'}), ['mcp:n8n-mcp/update_workflow'])
        self.assertEqual(guards.edit_targets('mcp__n8n-mcp__publish_workflow', {}), ['mcp:n8n-mcp/publish_workflow'])
        self.assertEqual(guards.edit_targets('mcp__claude_ai_Google_Drive__update_file', {'fileId': 'x'}),
                         ['mcp:claude_ai_Google_Drive/update_file'])
        for tool in ('mcp__n8n-mcp__get_workflow_details', 'mcp__n8n-mcp__search_workflows', 'mcp__project_memory__memory_write',
                     'mcp__plugin_project-memory_project_memory__memory_write', 'mcp__n8n-mcp__validate_workflow'):
            with self.subTest(tool=tool):
                self.assertEqual(guards.edit_targets(tool, {'workflowId': 'x'}), [])

    def test_a_live_workflow_change_is_blocked_until_the_plan_allows_the_server(self):
        with self.assertRaises(guards.ScopeBlocked) as caught:
            codex_host.capture(self.m, self.event('mcp__n8n-mcp__update_workflow', {'workflowId': 'Zp8Error00001'}, 'call-1'), host='claude')
        self.assertIn('mcp:n8n-mcp/update_workflow', str(caught.exception))
        self.assertIn('mcp:<server>', str(caught.exception))
        codex_host.capture(self.m, self.event('mcp__n8n-mcp__get_workflow_details', {'workflowId': 'Zp8Error00001'}, 'call-2'), host='claude')
        plan = planning.latest(self.m, self.episode, 'work_plan')
        plan.pop('id')
        planning.save(self.m, 'work_plan', payload={**plan, 'paths': ['workflows/lead-intake.json', 'mcp:n8n-mcp']}, actor='workspace-user',
                      evidence=self.evidence(), episode_id=self.episode, expected_version=self.m.episode(self.episode)['version'],
                      request_key='allow-n8n', session_id='session')
        codex_host.capture(self.m, self.event('mcp__n8n-mcp__update_workflow', {'workflowId': 'Zp8Error00001'}, 'call-3'), host='claude')
        blocked = self.m.db.execute("SELECT count(*) FROM host_receipts WHERE event_name='ScopeBlocked'").fetchone()[0]
        self.assertEqual(blocked, 1)


class TemplateGuidanceTests(unittest.TestCase):
    def test_the_workflow_readme_warns_about_inline_secrets_and_pinned_data(self):
        text = templates.TEMPLATES['automation']['documents']['workflows/README.md']
        self.assertNotIn('not secrets', text)
        self.assertIn('pinned data', text)
        self.assertIn('token', text)

    def test_scaffold_commits_only_the_starter_documents_in_a_new_repository(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            project = Path(folder) / 'engagement'
            result = templates.scaffold(project, 'engagement')
            self.assertTrue(result['git']['initialized'])
            self.assertTrue(result['git']['committed'])
            tracked = subprocess.run(['git', '-C', str(project), 'ls-files'], capture_output=True, text=True).stdout.split()
            self.assertEqual(sorted(tracked), sorted(templates.TEMPLATES['engagement']['documents']))
            again = templates.scaffold(project, 'engagement')
            self.assertFalse(again['git']['initialized'])
            count = subprocess.run(['git', '-C', str(project), 'rev-list', '--count', 'HEAD'], capture_output=True, text=True).stdout.strip()
            self.assertEqual(count, '1')
            self.assertIn('commit', result['next_step'])


if __name__ == '__main__':
    unittest.main()
