"""The probe of the process runner (section 16.4), run against fake host programs. No real host and no model runs."""
from contextlib import redirect_stderr
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from memory_module import Memory, api, cli, codex_host, delegation, hosts, machine

# One fake program stands in for every host. FAKE_HOST names the host whose command line it reads. FAKE_SANDBOX=off
# lets a shell write outside the working directory land, FAKE_VISIBLE=1 reports a user MCP server and a skill,
# FAKE_VERSION sets the printed version and FAKE_NO_HIVE=1 skips the hive call. The hive call is a real MCP exchange
# with the restricted hive server named on the command line.
FAKE = r'''
import json, os, re, subprocess, sys
from pathlib import Path

HOST = os.environ['FAKE_HOST']
args = sys.argv[1:]
if args == ['--version']:
    print(os.environ.get('FAKE_VERSION', HOST + ' 1.0.0'))
    sys.exit(0)


def value(flag):
    for index, item in enumerate(args):
        if item == flag:
            return args[index + 1]
        if item.startswith(flag + '='):
            return item[len(flag) + 1:]
    return None


stdin = sys.stdin.read()
if HOST == 'codex':
    prompt, work = stdin, value('--sandbox') == 'workspace-write'
    cwd = value('-C')
    hive = None
    overrides = [args[index + 1] for index, item in enumerate(args) if item == '-c']
    command = [item for item in overrides if item.startswith('mcp_servers.hive.command=')]
    if command:
        arguments = [item for item in overrides if item.startswith('mcp_servers.hive.args=')][0]
        hive = [json.loads(command[0].split('=', 1)[1])] + json.loads(arguments.split('=', 1)[1])
elif HOST == 'claude':
    prompt, work, cwd = value('--system-prompt'), 'Edit' in value('--tools'), os.getcwd()
    servers = json.loads(value('--mcp-config'))['mcpServers']
    hive = [servers['hive']['command']] + servers['hive']['args'] if 'hive' in servers else None
elif HOST == 'grok':
    prompt, work, cwd, hive = value('--rules'), 'write_file' in value('--tools'), value('--cwd'), None
else:
    prompt, work, cwd, hive = stdin, False, value('--dir'), None

if work:
    for outside in re.findall(r'printf probe > (\S+)', prompt):
        if os.environ.get('FAKE_SANDBOX') == 'off' or (os.environ.get('FAKE_HOME_WRITE') == '1' and 'project-memory-probe-' in outside):
            Path(outside).write_text('probe')
    Path(cwd, 'probe-inside.txt').write_text('probe')
    logged = False
    if hive and os.environ.get('FAKE_NO_HIVE') != '1':
        call = {'move': 'question', 'request_key': 'probe',
                'fields': {'claim': 'Does the hive server accept the entry of this probe?', 'addressee': 'user'}}
        messages = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-11-25'}},
                    {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {'name': 'hive_log', 'arguments': call}}]
        result = subprocess.run(hive, input=''.join(json.dumps(item) + '\n' for item in messages), capture_output=True, text=True)
        logged = result.returncode == 0
    answer = {'outside_write': 'refused', 'inside_write': True, 'hive_logged': logged}
else:
    visible = os.environ.get('FAKE_VISIBLE') == '1'
    answer = {'answer': 'probe-ready', 'mcp_servers': ['github'] if visible else [], 'skills': ['deploy'] if visible else []}

if HOST == 'codex':
    Path(value('--output-last-message')).write_text(json.dumps(answer))
    print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 20, 'cached_input_tokens': 5, 'output_tokens': 4}}))
elif HOST == 'claude':
    print(json.dumps({'type': 'result', 'is_error': False, 'structured_output': answer,
                      'usage': {'input_tokens': 12, 'output_tokens': 3}}))
elif HOST == 'grok':
    print(json.dumps({'type': 'end', 'structuredOutput': answer, 'usage': {'inputTokens': 30, 'outputTokens': 6}}))
else:
    text = 'The probe is ready.\n```json\n' + json.dumps(answer) + '\n```'
    print(json.dumps({'type': 'text', 'part': {'type': 'text', 'text': text}}))
    print(json.dumps({'type': 'step_finish', 'part': {'reason': 'stop', 'cost': 0.002,
                      'tokens': {'total': 40, 'input': 35, 'output': 5, 'reasoning': 0, 'cache': {'read': 0, 'write': 0}}}}))
'''


@unittest.skipIf(os.name == 'nt', 'The fake host programs start through a POSIX interpreter line.')
class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.machine_path = self.root / 'machine' / 'machine.sqlite'
        self.programs = {}
        for host in hosts.KNOWN_HOSTS:
            program = self.root / 'bin' / host
            program.parent.mkdir(exist_ok=True)
            program.write_text('#!/bin/sh\nFAKE_HOST=' + host + ' exec ' + json.dumps(sys.executable) + ' '
                               + json.dumps(str(self.root / 'bin' / 'fake.py')) + ' "$@"\n')
            program.chmod(0o755)
            self.programs[host] = str(program)
        (self.root / 'bin' / 'fake.py').write_text(FAKE)
        environment = {machine.DATABASE_VARIABLE: str(self.machine_path), 'CODEX_HOME': str(self.root / 'codex-home'),
                       'XDG_CONFIG_HOME': str(self.root / 'xdg'), 'FAKE_SANDBOX': 'on', 'FAKE_VISIBLE': '0',
                       'FAKE_NO_HIVE': '0', 'FAKE_VERSION': '', 'FAKE_HOME_WRITE': '0',
                       'GROK_HOME': str(self.root / 'grok-home')}
        environment.update({hosts.ENVIRONMENT[host]: program for host, program in self.programs.items()})
        patcher = patch.dict(os.environ, environment)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop('FAKE_VERSION')
        hosts._VERSIONS.clear()
        self.addCleanup(hosts._VERSIONS.clear)

    def statuses(self, result):
        return {check['check']: check['status'] for check in result['checks']}

    def test_a_claude_probe_passes_every_check_and_is_recorded_in_the_machine_memory(self):
        result = hosts.probe('claude', timeout=60)
        self.assertEqual(self.statuses(result), {'limit_message': 'passed', 'structured_answer': 'passed', 'usage': 'passed',
                                                 'shell_write_outside_refused': 'passed', 'hive_write_accepted': 'passed'})
        self.assertEqual((result['host'], result['version'], result['runner'], result['passed']), ('claude', 'claude 1.0.0', 'process', True))
        self.assertEqual(result['roles'], ['review', 'work'])
        with Memory(self.machine_path, read_only=True) as store:
            rows = store.db.execute('SELECT session_id,event_name,payload FROM host_receipts').fetchall()
        self.assertEqual([(row['session_id'], row['event_name']) for row in rows], [('probe:claude', 'HostProbeFinished')])
        stored = json.loads(rows[0]['payload'])
        self.assertEqual(stored['checks'], result['checks'])
        # The receipt holds check names, states and sentences only: no temporary path and no host output.
        self.assertNotIn(str(self.root), rows[0]['payload'])
        self.assertNotIn(tempfile.gettempdir(), rows[0]['payload'])
        self.assertNotIn('probe-ready', rows[0]['payload'])
        self.assertEqual(hosts.probe_results()['claude']['version'], 'claude 1.0.0')
        self.assertEqual([path.name for path in self.machine_path.parent.iterdir() if path.name.startswith('probe-')], [])

    def test_a_codex_probe_reaches_the_hive_through_its_command_line_overrides(self):
        result = hosts.probe('codex', timeout=60)
        self.assertTrue(result['passed'], result['checks'])
        self.assertEqual(self.statuses(result)['hive_write_accepted'], 'passed')

    def test_a_write_outside_the_worktree_or_a_missing_hive_write_fails_the_probe(self):
        with patch.dict(os.environ, {'FAKE_SANDBOX': 'off'}):
            result = hosts.probe('claude', timeout=60)
        self.assertFalse(result['passed'])
        self.assertEqual(self.statuses(result)['shell_write_outside_refused'], 'failed')
        self.assertIn('A shell write outside the worktree succeeded', result['checks'][3]['detail'])
        with patch.dict(os.environ, {'FAKE_NO_HIVE': '1'}):
            result = hosts.probe('claude', timeout=60)
        self.assertEqual(self.statuses(result)['hive_write_accepted'], 'failed')
        self.assertFalse(result['passed'])

    def test_grok_works_only_after_a_passing_probe_of_its_installed_version(self):
        config = {'hosts': ['claude', 'grok']}
        self.assertFalse(hosts.probed('grok'))
        self.assertEqual(delegation.work_hosts(config), ['claude'])
        result = hosts.probe('grok', timeout=60)
        self.assertEqual(self.statuses(result), {'limit_message': 'passed', 'structured_answer': 'passed', 'usage': 'passed',
                                                 'no_user_configuration': 'passed', 'shell_write_outside_refused': 'passed',
                                                 'configuration_home_write_refused': 'passed',
                                                 'hive_write_accepted': 'not_applicable'})
        self.assertEqual(result['roles'], ['review', 'work'])
        self.assertTrue(hosts.probed('grok'))
        self.assertTrue(hosts.may_work('grok'))
        self.assertEqual(delegation.work_hosts(config), ['claude', 'grok'])
        # A new installed version needs its own probe.
        hosts._VERSIONS.clear()
        with patch.dict(os.environ, {'FAKE_VERSION': 'grok 1.1.0'}):
            self.assertFalse(hosts.probed('grok'))
            self.assertEqual(delegation.work_hosts(config), ['claude'])
        # A later failing probe of the same version withdraws the work role.
        hosts._VERSIONS.clear()
        with patch.dict(os.environ, {'FAKE_VISIBLE': '1'}):
            failed = hosts.probe('grok', timeout=60)
        self.assertEqual(self.statuses(failed)['no_user_configuration'], 'failed')
        self.assertIn('1 MCP servers and 1 skills', [check for check in failed['checks'] if check['check'] == 'no_user_configuration'][0]['detail'])
        self.assertEqual(failed['roles'], ['review'])
        self.assertFalse(hosts.probed('grok'))
        # Codex and Claude need no probe to work, so they never read one.
        self.assertFalse(hosts.probed('codex'))
        self.assertTrue(hosts.may_work('codex'))

    def test_a_grok_worker_that_can_write_into_the_grok_configuration_folder_fails_the_probe(self):
        # Every built-in Grok sandbox allows writes to ~/.grok, where later runs load hooks and configuration.
        with patch.dict(os.environ, {'FAKE_HOME_WRITE': '1'}):
            result = hosts.probe('grok', timeout=60)
        self.assertEqual(self.statuses(result)['shell_write_outside_refused'], 'passed')
        self.assertEqual(self.statuses(result)['configuration_home_write_refused'], 'failed')
        self.assertFalse(result['passed'])
        self.assertEqual(result['roles'], ['review'])
        self.assertFalse(hosts.may_work('grok'))
        # The probe file is removed from the configuration folder afterwards.
        self.assertEqual(list((self.root / 'grok-home').iterdir()), [])
        # Codex and Claude name no configuration folder, so their probes keep their checks.
        self.assertNotIn('configuration_home_write_refused', self.statuses(hosts.probe('claude', timeout=60)))

    def test_an_opencode_probe_checks_the_review_role_only(self):
        result = hosts.probe('opencode', timeout=60)
        self.assertEqual(self.statuses(result), {'limit_message': 'passed', 'structured_answer': 'passed', 'usage': 'passed'})
        self.assertEqual((result['passed'], result['roles']), (True, ['review']))
        self.assertFalse(hosts.may_work('opencode'))

    def test_every_canned_limit_message_is_classified_offline_with_its_reset_time(self):
        for host in hosts.KNOWN_HOSTS:
            with self.subTest(host=host):
                self.assertEqual(hosts.probe_limit_message(host)['status'], 'passed')
        with patch.dict(hosts.PROBE_LIMIT_MESSAGES, {'grok': ('The request failed.', 'rate_limit', hosts.timedelta(minutes=5))}):
            self.assertEqual(hosts.probe_limit_message('grok')['status'], 'failed')

    def test_a_missing_program_or_an_unreadable_version_cannot_unlock_a_role(self):
        with patch.dict(os.environ, {'PROJECT_MEMORY_GROK_BIN': ''}), \
                patch.object(hosts.shutil, 'which', return_value=None), patch.object(hosts.Path, 'home', return_value=self.root / 'home'):
            with self.assertRaisesRegex(hosts.InvalidRecord, 'The grok host is not installed, so it cannot be probed.'):
                hosts.probe('grok')
        with patch.object(hosts, 'installed_version', return_value=None):
            result = hosts.probe('grok', timeout=60)
        self.assertFalse(result['passed'])
        self.assertEqual(result['note'], 'The installed version could not be read, so the probe cannot unlock a role.')

    def test_the_command_refuses_to_run_inside_an_assistant_session(self):
        errors = io.StringIO()
        with patch.dict(os.environ, {'CLAUDECODE': '1'}), redirect_stderr(errors):
            self.assertEqual(cli.main(['host', 'probe', 'grok']), 1)
        self.assertIn('does not run from inside an assistant session', errors.getvalue())
        self.assertFalse(self.machine_path.exists())

    def test_the_command_prints_the_probe_in_a_terminal_of_the_user(self):
        from memory_module import live
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(live, 'assistant_session', return_value=[]), redirect_stderr(errors), \
                patch('sys.stdout', output):
            self.assertEqual(cli.main(['host', 'probe', 'opencode', '--timeout', '60']), 0)
        self.assertIn('spends tokens', errors.getvalue())
        self.assertTrue(json.loads(output.getvalue())['passed'])

    def test_the_usage_endpoint_lists_the_latest_probe_of_each_host(self):
        hosts.probe('opencode', timeout=60)
        project = self.root / 'project' / '.memory' / 'project.sqlite'
        with Memory.create(project, 'Probe listing', ['Keep history.']) as memory:
            listed = api.usage(memory, {})
        self.assertEqual(list(listed['probes']), ['opencode'])
        self.assertTrue(listed['probes']['opencode']['passed'])
        self.assertIn('probed_at', listed['probes']['opencode'])
        with Memory(project, read_only=True) as reader:
            self.assertFalse(codex_host.exists(reader))


if __name__ == '__main__':
    unittest.main()
