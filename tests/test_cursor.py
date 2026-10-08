"""Cursor as a session host: hooks, a receipt stamp and a transcript digest.

Cursor is not a check runner and not a delegated worker. Transcripts here are invented.
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from memory_module import InvalidRecord, Memory, codex_host, hosts, install, sessions, usage


class CursorCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name)
        self.m = Memory.create(self.root / 'memory.sqlite', 'Tests', ['Preserve exceptions.'])
        codex_host.initialize(self.m)

    def tearDown(self):
        self.m.close()
        shutil.rmtree(self.temp.name, ignore_errors=True)

    def receipt(self):
        return codex_host.read_receipt(self.m, codex_host.status(self.m)['recent'][0]['id'])

    def test_session_start_stamps_cursor_and_returns_context(self):
        event = {'hook_event_name': 'SessionStart', 'session_id': 'cursor-session',
                 'transcript_path': '/tmp/t.jsonl', 'cwd': str(self.root), 'source': 'startup'}
        result = codex_host.capture(self.m, event, host='cursor')
        context = result['hookSpecificOutput']['additionalContext']
        self.assertIn('cursor-session', context)
        self.assertEqual(result['additional_context'], context)
        row = self.receipt()
        self.assertEqual((row['event_name'], row['payload']['host']), ('SessionStart', 'cursor'))

    def test_native_names_use_the_conversation_and_keep_a_failure(self):
        codex_host.capture(self.m, {'hook_event_name': 'sessionStart', 'conversation_id': 'conv-1', 'source': 'startup'}, host='cursor')
        self.assertEqual(self.receipt()['session_id'], 'conv-1')
        codex_host.capture(self.m, {'hook_event_name': 'beforeSubmitPrompt', 'session_id': 'conv-1', 'prompt': 'next step'}, host='cursor')
        codex_host.capture(self.m, {'hook_event_name': 'preToolUse', 'session_id': 'conv-1', 'tool_name': 'Shell',
                                    'tool_use_id': 'toolu_1', 'tool_input': {'command': 'pytest'}}, host='cursor')
        codex_host.capture(self.m, {'hook_event_name': 'postToolUseFailure', 'session_id': 'conv-1', 'tool_name': 'Shell',
                                    'tool_use_id': 'toolu_1', 'error': 'Command failed'}, host='cursor')
        counts = {row[0]: row[1] for row in self.m.db.execute('SELECT event_name,count(*) FROM host_receipts GROUP BY event_name')}
        self.assertEqual(counts['UserPromptSubmit'], 1)
        self.assertEqual(counts['PostToolUse'], 1)
        self.assertEqual(self.receipt()['payload']['host'], 'cursor')
        self.assertTrue(self.receipt()['payload']['failed'])


class CursorInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.project = Path(self.temp.name)

    def tearDown(self):
        shutil.rmtree(self.temp.name, ignore_errors=True)

    def test_setup_writes_hooks_without_enabling_checks(self):
        result = install.setup(self.project, client='cursor', trust=True, _launcher=['project-memory'])
        hooks = json.loads((self.project / '.cursor' / 'hooks.json').read_text())
        self.assertEqual(hooks['version'], 1)
        command = hooks['hooks']['sessionStart'][0]['command']
        self.assertTrue(command.endswith('--host cursor'))
        self.assertEqual(set(hooks['hooks']), set(codex_host.CURSOR_HOOK_EVENTS))
        servers = json.loads((self.project / '.cursor' / 'mcp.json').read_text())['mcpServers']
        self.assertEqual(servers['project_memory']['args'][-3:], ['serve', '--db', result['database']])
        with Memory(result['database']) as memory:
            self.assertIsNone(memory.db.execute("SELECT value FROM settings WHERE key='review_host'").fetchone())
        install.setup(self.project, client='cursor', _launcher=['project-memory'])
        hooks = json.loads((self.project / '.cursor' / 'hooks.json').read_text())
        self.assertEqual(len(hooks['hooks']['sessionStart']), 1)
        removed = install.uninstall(self.project, 'cursor')
        self.assertEqual((removed['removed'], removed['agent_hosts_removed']), (True, []))
        self.assertEqual(json.loads((self.project / '.cursor' / 'hooks.json').read_text())['hooks'], {})

    def test_existing_cursor_entries_are_kept(self):
        folder = self.project / '.cursor'
        folder.mkdir()
        (folder / 'hooks.json').write_text(json.dumps({'version': 1, 'hooks': {'stop': [{'command': 'other'}]}}))
        (folder / 'mcp.json').write_text(json.dumps({'mcpServers': {'other': {'command': 'x'}}}))
        install.setup(self.project, client='cursor', _launcher=['project-memory'])
        commands = [item['command'] for item in json.loads((folder / 'hooks.json').read_text())['hooks']['stop']]
        self.assertEqual(commands[0], 'other')
        self.assertTrue(commands[-1].endswith('--host cursor'))
        install.uninstall(self.project, 'cursor')
        self.assertEqual(json.loads((folder / 'hooks.json').read_text())['hooks'], {'stop': [{'command': 'other'}]})
        self.assertEqual(json.loads((folder / 'mcp.json').read_text())['mcpServers'], {'other': {'command': 'x'}})

    def test_cursor_is_outside_checks_and_headroom(self):
        self.assertNotIn('cursor', hosts.KNOWN_HOSTS)
        self.assertNotIn('cursor', usage.HOSTS)
        self.assertEqual(hosts.HOSTS, ('codex', 'claude'))
        with self.assertRaises(InvalidRecord):
            hosts.may_work('cursor')


class CursorReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'project'
        self.root.mkdir()
        self.m = Memory.create(self.root / '.memory' / 'project.sqlite', 'Sessions', ['Keep sessions.'])
        codex_host.initialize(self.m)
        self.cursor = self.base / 'cursor-projects'
        self.session = '11111111-2222-4333-8444-555555555555'
        self.folder = self.cursor / sessions.cursor_project_slug(self.root) / 'agent-transcripts' / self.session
        self.folder.mkdir(parents=True)

    def tearDown(self):
        self.m.close()
        shutil.rmtree(self.temp.name, ignore_errors=True)

    def test_the_documented_folder_is_under_cursor_projects(self):
        self.assertEqual(sessions.cursor_project_slug(Path('/home/dankaro')), 'home-dankaro')
        self.assertTrue(str(usage.default_folders()['cursor']).endswith('.cursor/projects'))

    def test_reader_keeps_user_text_and_edit_paths(self):
        secret = 'ghp_' + 'a' * 36
        lines = [
            {'role': 'user', 'message': {'content': [{'type': 'text', 'text': 'Please build the parser. SENTINEL-USER-TEXT ' + secret}]}},
            {'role': 'assistant', 'message': {'content': [{'type': 'tool_use', 'name': 'Write', 'input': {'path': str(self.root / 'parser.py'), 'contents': 'x'}}]}},
            {'role': 'assistant', 'message': {'content': [{'type': 'tool_use', 'name': 'Shell', 'input': {'command': 'pytest -q'}}]}},
            {'type': 'turn_ended', 'status': 'success'},
        ]
        path = self.folder / (self.session + '.jsonl')
        path.write_text('\n'.join(json.dumps(line) for line in lines) + '\n', encoding='utf-8')
        found = {'claude': self.base / 'missing-claude', 'codex': self.base / 'missing-codex', 'cursor': self.cursor}
        listed = [(item[1], item[2]) for item in sessions.candidates(self.m, found=found, mentions=False)]
        self.assertEqual(listed, [('cursor', 'working_folder')])
        sessions.ensure(self.m)
        key = sessions.scan(self.m, path, 'cursor', 'working_folder')
        self.assertEqual(key, 'cursor:' + self.session)
        data = json.loads(self.m.db.execute('SELECT data FROM session_digests WHERE session_key=?', (key,)).fetchone()[0])
        self.assertIn('SENTINEL-USER-TEXT', data['messages'][0]['text'])
        self.assertNotIn(secret, json.dumps(data))
        self.assertEqual(data['files'], ['parser.py'])
        self.assertEqual(data['failures'], [])
