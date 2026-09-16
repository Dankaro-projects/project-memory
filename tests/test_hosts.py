"""Host command lines, run log detection and availability. No host process is started."""
import shutil
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from memory_module import Memory, InvalidRecord, hosts, reviews


NOW = datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self):
        self.value = NOW

    def __call__(self):
        return self.value.isoformat()


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name).resolve()
        self.codex_home = self.root / 'codex-home'
        self.codex_home.mkdir()
        (self.codex_home / 'config.toml').write_text('model = "fixture-model"\n[mcp_servers.global_one]\ncommand = "x"\n')
        self.project = self.root / 'project'
        (self.project / '.codex').mkdir(parents=True)
        (self.project / '.codex/config.toml').write_text('[mcp_servers.local-two]\ncommand = "y"\n')
        self.worktree = self.project / '.memory/worktrees/run1'
        self.worktree.mkdir(parents=True)
        self.folder = self.root / 'run'
        self.folder.mkdir()
        (self.folder / 'schema.json').write_text('{"type":"object"}')
        environment = {'CODEX_HOME': str(self.codex_home)}
        self.env = patch.dict(os.environ, environment)
        self.env.start()
        for name in hosts.ENVIRONMENT.values():
            os.environ.pop(name, None)

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.temp.name, ignore_errors=True)

    def test_executable_prefers_environment_override(self):
        with patch.object(hosts.shutil, 'which', return_value='/usr/local/bin/codex') as which:
            self.assertEqual(hosts.executable('codex'), '/usr/local/bin/codex')
            which.assert_called_with('codex')
        with patch.dict(os.environ, {'PROJECT_MEMORY_CLAUDE_BIN': '/opt/fake/claude'}):
            self.assertEqual(hosts.executable('claude'), '/opt/fake/claude')
        with patch.object(hosts.shutil, 'which', return_value=None):
            self.assertIsNone(hosts.executable('claude'))
        with self.assertRaises(InvalidRecord):
            hosts.executable('gemini')

    def test_review_command_is_unchanged_and_reviews_command_delegates(self):
        args = hosts.review_command('codex', str(self.project), self.folder, 'prompt')
        self.assertEqual(args[:6], ['codex', 'exec', '--ephemeral', '--skip-git-repo-check', '--sandbox', 'read-only'])
        self.assertIn('project_doc_max_bytes=0', args)
        self.assertEqual(args[args.index('-m') + 1], 'fixture-model')
        self.assertIn('mcp_servers.global_one.enabled=false', args)
        self.assertIn('mcp_servers.local-two.enabled=false', args)
        self.assertEqual(args[-6:], ['--output-schema', str(self.folder / 'schema.json'),
                                     '--output-last-message', str(self.folder / 'answer.json'), '--json', '-'])
        self.assertEqual(args[args.index('-C') + 1], str(self.project))
        self.assertEqual(reviews.command('codex', str(self.project), self.folder, 'prompt'), args)
        claude = hosts.review_command('claude', str(self.project), self.folder, 'the prompt')
        self.assertEqual(claude[0], 'claude')
        self.assertEqual(claude[claude.index('--tools') + 1], 'Read,Glob,Grep')
        self.assertEqual(claude[claude.index('--json-schema') + 1], '{"type":"object"}')
        self.assertEqual(claude[-1], 'the prompt')
        self.assertEqual(reviews.command('claude', str(self.project), self.folder, 'the prompt'), claude)

    def test_review_command_uses_environment_override(self):
        with patch.dict(os.environ, {'PROJECT_MEMORY_CLAUDE_BIN': '/opt/custom/claude',
                                     'PROJECT_MEMORY_CODEX_BIN': '/opt/custom/codex'}):
            self.assertEqual(reviews.command('claude', str(self.project), self.folder, 'p')[0], '/opt/custom/claude')
            codex = reviews.command('codex', str(self.project), self.folder, 'p')
        self.assertEqual(codex[0], '/opt/custom/codex')
        self.assertEqual(codex[1:6], ['exec', '--ephemeral', '--skip-git-repo-check', '--sandbox', 'read-only'])

    def test_review_command_rejects_unsafe_server_names(self):
        (self.project / '.codex/config.toml').write_text('[mcp_servers."bad name"]\ncommand = "y"\n')
        with self.assertRaises(InvalidRecord):
            hosts.review_command('codex', str(self.project), self.folder, 'prompt')

    def test_codex_work_command(self):
        with patch.object(hosts.shutil, 'which', return_value=None):
            args = hosts.work_command('codex', self.worktree, self.folder, 'prompt')
        expected = ['codex', 'exec', '--ephemeral', '--skip-git-repo-check', '--sandbox', 'workspace-write',
                    '-C', str(self.worktree), '-c', 'approval_policy="never"', '-c', 'features.hooks=false',
                    '-c', 'features.plugins=false', '-c', 'features.apps=false', '-c', 'features.multi_agent=false',
                    '-c', 'memories.use_memories=false', '-c', 'memories.generate_memories=false', '-c', 'web_search="disabled"',
                    '-c', 'mcp_servers.global_one.enabled=false',
                    '--output-schema', str(self.folder / 'schema.json'), '--output-last-message', str(self.folder / 'answer.json'),
                    '--json', '-']
        self.assertEqual(args, expected)
        # The worktree lies under .memory, where Codex does not read the project's own
        # configuration. Naming that server would create an entry with no command and no
        # address, and Codex refuses to start with an invalid transport.
        self.assertNotIn('mcp_servers.local-two.enabled=false', args)
        self.assertNotIn('read-only', args)
        self.assertNotIn('-m', args)

    def test_claude_work_command_and_program_override(self):
        with patch.dict(os.environ, {'PROJECT_MEMORY_CLAUDE_BIN': '/opt/fake/claude'}):
            args = hosts.work_command('claude', self.worktree, self.folder, 'Do the work.')
        expected = ['/opt/fake/claude', '-p', '--restricted', '--no-session-persistence', '--output-format', 'stream-json',
                    '--verbose', '--permission-mode', 'dontAsk', '--setting-sources', '', '--settings', '{"disableAllHooks":true}',
                    '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}', '--disable-slash-commands',
                    '--tools', 'Read,Glob,Grep,Edit,Write,Bash', '--allowedTools', 'Read,Glob,Grep,Edit,Write,Bash',
                    '--json-schema', '{"type":"object"}', '--system-prompt', 'Do the work.']
        self.assertEqual(args, expected)
        with patch.dict(os.environ, {'PROJECT_MEMORY_CODEX_BIN': '/opt/fake/codex'}):
            self.assertEqual(hosts.work_command('codex', self.worktree, self.folder, 'p')[0], '/opt/fake/codex')


class RunLogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.folder = Path(self.temp.name)
        self.output = self.folder / 'output.jsonl'

    def tearDown(self):
        shutil.rmtree(self.temp.name, ignore_errors=True)

    def test_codex_usage_limit_across_split_lines(self):
        event = json.dumps({'type': 'error', 'message': "You've hit your usage limit. Try again in 2 hours."}) + '\n'
        self.output.write_text(event[:25])
        log = hosts.RunLog(self.folder)
        self.assertNotIn('host_unavailable', log.read())
        with self.output.open('a') as stream:
            stream.write(event[25:])
        before = datetime.now(timezone.utc)
        metrics = log.read()
        found = metrics['host_unavailable']
        self.assertEqual(found['reason'], 'usage_limit')
        until = datetime.fromisoformat(found['until'])
        self.assertLessEqual(abs((until - before - timedelta(hours=2)).total_seconds()), 60)
        self.assertEqual(metrics['host_error_events'], 1)
        self.assertNotIn('usage limit', json.dumps(metrics))

    def test_codex_turn_failed_rate_limit(self):
        event = {'type': 'turn.failed', 'error': {'message': 'stream error: unexpected status 429 Too Many Requests'}}
        self.output.write_text(json.dumps(event) + '\n')
        metrics = hosts.RunLog(self.folder).read(final=True)
        self.assertEqual(metrics['host_unavailable'], {'reason': 'rate_limit', 'until': None})

    def test_claude_result_with_epoch_reset(self):
        events = [{'type': 'system', 'subtype': 'init', 'model': 'fixture'},
                  {'type': 'result', 'is_error': True, 'result': 'Claude AI usage limit reached|1789473600'}]
        text = '\n'.join(json.dumps(e) for e in events)
        self.output.write_text(text[:40])
        log = hosts.RunLog(self.folder)
        log.read()
        with self.output.open('a') as stream:
            stream.write(text[40:])
        metrics = log.read(final=True)
        self.assertEqual(metrics['host_unavailable']['reason'], 'usage_limit')
        self.assertEqual(metrics['host_unavailable']['until'], datetime.fromtimestamp(1789473600, timezone.utc).isoformat())

    def test_claude_successful_result_is_not_scanned(self):
        event = {'type': 'result', 'is_error': False, 'result': 'The report discusses a rate limit in the parser.'}
        self.output.write_text(json.dumps(event) + '\n')
        self.assertNotIn('host_unavailable', hosts.RunLog(self.folder).read(final=True))

    def test_transient_error_before_successful_completion_is_not_unavailability(self):
        events = [{'type': 'thread.started'},
                  {'type': 'error', 'message': 'Reconnecting... 1/5 (stream error: last status: 429 Too Many Requests)'},
                  {'type': 'turn.completed', 'usage': {'input_tokens': 1}}]
        self.output.write_text('\n'.join(json.dumps(e) for e in events[:2]) + '\n')
        log = hosts.RunLog(self.folder)
        self.assertEqual(log.read()['host_unavailable']['reason'], 'rate_limit')
        with self.output.open('a') as stream:
            stream.write(json.dumps(events[2]) + '\n')
        metrics = log.read(final=True)
        self.assertEqual(metrics['phase'], 'report_received')
        self.assertNotIn('host_unavailable', metrics)
        self.assertEqual(metrics['host_error_events'], 1)

    def test_retry_notice_in_standard_error_of_successful_run_is_ignored(self):
        self.output.write_text(json.dumps({'type': 'turn.completed'}) + '\n')
        (self.folder / 'stderr.log').write_text('WARN retrying request after rate limit (attempt 1)\n')
        metrics = hosts.RunLog(self.folder).read(final=True)
        self.assertEqual(metrics['phase'], 'report_received')
        self.assertNotIn('host_unavailable', metrics)
        claude = hosts.RunLog(self.folder)
        self.output.write_text(json.dumps({'type': 'result', 'is_error': False, 'result': 'Done.'}) + '\n')
        self.assertNotIn('host_unavailable', claude.read(final=True))

    def test_failure_after_completion_is_still_detected(self):
        events = [{'type': 'turn.completed'}, {'type': 'turn.failed', 'error': {'message': 'usage limit reached'}}]
        self.output.write_text('\n'.join(json.dumps(e) for e in events) + '\n')
        self.assertEqual(hosts.RunLog(self.folder).read(final=True)['host_unavailable']['reason'], 'usage_limit')

    def test_unrepresentable_reset_times_do_not_raise(self):
        texts = ['Usage limit reached. Try again in 99999999 hours.',
                 'Usage limit reached. Try again in ' + '9' * 400 + ' seconds.',
                 'Usage limit reached. Resets at 9999-12-31T23:00:00-14:00']
        for text in texts:
            with self.subTest(text=text[:60]):
                self.assertIsNone(hosts.parse_until(text, NOW))
                self.assertEqual(hosts.unavailable(text, NOW), {'reason': 'usage_limit', 'until': None})
        self.output.write_text(json.dumps({'type': 'turn.failed', 'error': {'message': texts[0]}}) + '\n')
        (self.folder / 'stderr.log').write_text(texts[2] + '\n')
        self.assertEqual(hosts.RunLog(self.folder).read(final=True)['host_unavailable'],
                         {'reason': 'usage_limit', 'until': None})

    def test_final_standard_error_authentication_and_reset_time(self):
        self.output.write_text('')
        (self.folder / 'stderr.log').write_text('noise\n' * 5000 + 'Error: Not logged in. Please run /login.\n')
        log = hosts.RunLog(self.folder)
        self.assertNotIn('host_unavailable', log.read())
        self.assertEqual(log.read(final=True)['host_unavailable'], {'reason': 'authentication', 'until': None})
        (self.folder / 'stderr.log').write_text('quota exceeded; limit resets at 2026-09-15T12:30:00Z\n')
        self.assertEqual(hosts.RunLog(self.folder).read(final=True)['host_unavailable'],
                         {'reason': 'quota', 'until': '2026-09-15T12:30:00+00:00'})

    def test_ordinary_errors_are_not_unavailability(self):
        self.output.write_text(json.dumps({'type': 'error', 'message': 'The tool call failed with exit code 1.'}) + '\n')
        (self.folder / 'stderr.log').write_text('warning: 4290 files scanned\n')
        metrics = hosts.RunLog(self.folder).read(final=True)
        self.assertEqual(metrics['host_error_events'], 1)
        self.assertNotIn('host_unavailable', metrics)

    def test_detection_wording(self):
        cases = {
            'Usage limit reached for this plan.': 'usage_limit',
            'rate_limit_error: slow down': 'rate_limit',
            'HTTP 429': 'rate_limit',
            'Too many requests': 'rate_limit',
            'insufficient_quota': 'quota',
            'Not logged in': 'authentication',
            'authentication_error: invalid x-api-key': 'authentication',
            'HTTP 401 Unauthorized': 'authentication',
        }
        for text, reason in cases.items():
            with self.subTest(text=text):
                self.assertEqual(hosts.unavailable(text)['reason'], reason)
        self.assertIsNone(hosts.unavailable('Everything worked.'))
        self.assertIsNone(hosts.unavailable(''))

    def test_until_parsing(self):
        self.assertEqual(hosts.parse_until('try again in 30 seconds', NOW), (NOW + timedelta(seconds=30)).isoformat())
        self.assertEqual(hosts.parse_until('Please try again in 5 minutes.', NOW), (NOW + timedelta(minutes=5)).isoformat())
        self.assertEqual(hosts.parse_until('Try again in 1 hour', NOW), (NOW + timedelta(hours=1)).isoformat())
        self.assertEqual(hosts.parse_until('Your limit will reset at 2026-09-16 08:00', NOW), '2026-09-16T08:00:00+00:00')
        self.assertEqual(hosts.parse_until('resets 2026-09-16T08:00:00+02:00', NOW), '2026-09-16T06:00:00+00:00')
        self.assertEqual(hosts.parse_until('limit reached|1789473600', NOW), '2026-09-15T12:00:00+00:00')
        self.assertIsNone(hosts.parse_until('limit reached', NOW))


class AvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.path = Path(self.temp.name) / '.memory/memory.db'
        self.clock = Clock()
        self.memory = Memory.create(self.path, 'Fixture', ['Keep history.'], clock=self.clock)
        self.which = patch.object(hosts.shutil, 'which', side_effect=lambda name: '/bin/' + name)
        self.which.start()
        self.env = patch.dict(os.environ, {})
        self.env.start()
        for name in hosts.ENVIRONMENT.values():
            os.environ.pop(name, None)

    def tearDown(self):
        self.env.stop()
        self.which.stop()
        self.memory.close()
        shutil.rmtree(self.temp.name, ignore_errors=True)

    def test_read_only_connection_without_receipts_table(self):
        with Memory(self.path, read_only=True) as reader:
            self.assertEqual(hosts.availability(reader, 'codex'),
                             {'host': 'codex', 'installed': True, 'available': True, 'until': None, 'reason': None})
            self.assertEqual(hosts.choose(reader, 'claude'), 'claude')

    def test_until_expiry_and_mark_available(self):
        hosts.mark_unavailable(self.memory, 'codex', 'usage_limit', (NOW + timedelta(minutes=90)).isoformat())
        state = hosts.availability(self.memory, 'codex')
        self.assertFalse(state['available'])
        self.assertEqual(state['reason'], 'usage_limit')
        self.assertEqual(state['until'], (NOW + timedelta(minutes=90)).isoformat(timespec='microseconds'))
        self.clock.value = NOW + timedelta(minutes=80)
        self.assertFalse(hosts.availability(self.memory, 'codex')['available'])
        self.clock.value = NOW + timedelta(minutes=91)
        self.assertTrue(hosts.availability(self.memory, 'codex')['available'])
        self.clock.value = NOW
        self.assertFalse(hosts.availability(self.memory, 'codex')['available'])
        hosts.mark_available(self.memory, 'codex')
        self.assertTrue(hosts.availability(self.memory, 'codex')['available'])
        self.assertTrue(hosts.availability(self.memory, 'claude')['available'])
        rows = self.memory.db.execute("SELECT session_id,event_name FROM host_receipts ORDER BY rowid").fetchall()
        self.assertEqual([tuple(r) for r in rows], [('host:codex', 'HostUnavailable'), ('host:codex', 'HostAvailable')])

    def test_unavailability_without_until_lasts_sixty_minutes(self):
        hosts.mark_unavailable(self.memory, 'claude', 'authentication')
        hosts.mark_unavailable(self.memory, 'claude', 'authentication')
        self.clock.value = NOW + timedelta(minutes=59)
        self.assertFalse(hosts.availability(self.memory, 'claude')['available'])
        self.clock.value = NOW + timedelta(minutes=61)
        self.assertTrue(hosts.availability(self.memory, 'claude')['available'])

    def test_mark_unavailable_validates_input(self):
        with self.assertRaises(InvalidRecord):
            hosts.mark_unavailable(self.memory, 'other', 'usage_limit')
        with self.assertRaises(InvalidRecord):
            hosts.mark_unavailable(self.memory, 'codex', ' ')
        with self.assertRaises(InvalidRecord):
            hosts.mark_unavailable(self.memory, 'codex', 'usage_limit', 'tomorrow')

    def test_choose_fallback_and_failure(self):
        self.assertEqual(hosts.choose(self.memory, 'codex'), 'codex')
        hosts.mark_unavailable(self.memory, 'codex', 'rate_limit')
        self.assertEqual(hosts.choose(self.memory, 'codex'), 'claude')
        self.assertEqual(hosts.choose(self.memory, 'claude'), 'claude')
        with self.assertRaises(InvalidRecord) as caught:
            hosts.choose(self.memory, 'codex', allowed=['codex'])
        self.assertTrue(str(caught.exception).startswith('No configured agent host is available.'))
        self.assertEqual([r['host'] for r in caught.exception.details['availability']], ['codex'])
        self.assertFalse(caught.exception.details['availability'][0]['available'])
        with self.assertRaises(InvalidRecord):
            hosts.choose(self.memory, 'codex', exclude=('claude',))
        hosts.mark_available(self.memory, 'codex')
        self.assertEqual(hosts.choose(self.memory, 'claude', exclude=('claude',)), 'codex')

    def test_choose_skips_host_that_is_not_installed(self):
        with patch.object(hosts.shutil, 'which', side_effect=lambda name: None if name == 'codex' else '/bin/' + name):
            self.assertEqual(hosts.choose(self.memory, 'codex'), 'claude')
            with patch.dict(os.environ, {'PROJECT_MEMORY_CODEX_BIN': '/opt/fake/codex'}):
                self.assertEqual(hosts.choose(self.memory, 'codex'), 'codex')
        with patch.object(hosts.shutil, 'which', return_value=None):
            with self.assertRaises(InvalidRecord) as caught:
                hosts.choose(self.memory, 'claude')
            self.assertEqual([r['host'] for r in caught.exception.details['availability']], ['claude', 'codex'])


if __name__ == '__main__':
    unittest.main()


class CodexServerOverrideTests(unittest.TestCase):
    """Only a server that the loaded configuration defines can be disabled.

    A delegated worker runs inside a worktree under .memory, where Codex does not
    read the project's own .codex/config.toml. Naming that server anyway created
    an entry with no command and no address, and Codex refused to start with
    "invalid transport" before any work began.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name)
        home = self.root / 'codex-home'
        home.mkdir()
        (home / 'config.toml').write_text('model = "test-model"\n\n[mcp_servers.shared]\ncommand = "shared"\n')
        self.project = self.root / 'project'
        (self.project / '.codex').mkdir(parents=True)
        (self.project / '.codex/config.toml').write_text('[mcp_servers.project_memory]\ncommand = "memory"\n')
        self.worktree = self.project / '.memory/worktrees/run'
        self.worktree.mkdir(parents=True)
        self.environment = patch.dict(os.environ, {'CODEX_HOME': str(home)})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def tearDown(self):
        shutil.rmtree(self.temp.name, ignore_errors=True)

    def test_a_worktree_run_does_not_name_the_project_server(self):
        command = hosts.work_command('codex', str(self.worktree), self.root, 'prompt')
        self.assertIn('mcp_servers.shared.enabled=false', command)
        self.assertNotIn('mcp_servers.project_memory.enabled=false', command)

    def test_a_run_in_the_project_disables_its_own_server_once(self):
        command = hosts.review_command('codex', str(self.project), self.root, 'prompt')
        self.assertEqual(command.count('mcp_servers.project_memory.enabled=false'), 1)
        self.assertEqual(command.count('mcp_servers.shared.enabled=false'), 1)
