"""Several clients in one project: setup, uninstall, legacy upgrade and doctor."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from memory_module import Memory, codex_host, install, reviews
from memory_module import cli
from memory_module.health import expected_hook_events

CLAUDE_EVENTS = codex_host.HOST_EVENTS['claude'] | {'PostToolUseFailure'}


class InstallClientsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.project = Path(self.temp.name)
        self.launch = [sys.executable, '-m', 'memory_module.cli']

    def tearDown(self):
        self.temp.cleanup()

    def setup(self, **kwargs):
        kwargs.setdefault('_launcher', self.launch)
        return install.setup(self.project, **kwargs)

    def state(self):
        return json.loads((self.project / '.memory/install.json').read_text())

    def codex_config(self):
        return (self.project / '.codex/config.toml').read_text()

    def codex_hooks(self):
        return json.loads((self.project / '.codex/hooks.json').read_text())['hooks']

    def claude_servers(self):
        return json.loads((self.project / '.mcp.json').read_text())['mcpServers']

    def claude_settings(self):
        return json.loads((self.project / '.claude/settings.local.json').read_text())

    def commands(self, hooks):
        return {event: [h['command'] for group in groups for h in group['hooks']] for event, groups in hooks.items()}

    def assert_codex_installed(self):
        hooks = self.codex_hooks()
        self.assertEqual(set(hooks), codex_host.EVENTS)
        self.assertEqual(len(hooks), 9)
        command = self.state()['clients']['codex']['hook_command']
        for event, commands in self.commands(hooks).items():
            self.assertEqual(commands.count(command), 1, event)
        config = self.codex_config()
        self.assertEqual(config.count(install.BEGIN), 1)
        self.assertIn('[mcp_servers.project_memory]', config)
        self.assertIn(self.state()['clients']['codex']['block'], config)

    def assert_claude_installed(self):
        entry = self.state()['clients']['claude']
        self.assertEqual(self.claude_servers()['project_memory'], entry['server_entry'])
        hooks = self.claude_settings()['hooks']
        self.assertEqual(set(hooks), CLAUDE_EVENTS)
        for event, commands in self.commands(hooks).items():
            self.assertEqual(commands.count(entry['hook_command']), 1, event)

    def test_codex_then_claude_keeps_both_clients(self):
        codex = self.setup(client='codex')
        self.assertEqual(codex['clients'], ['codex'])
        claude = self.setup(client='claude')
        self.assertEqual(claude['client'], 'claude')
        self.assertEqual(claude['clients'], ['claude', 'codex'])
        self.assert_codex_installed()
        self.assert_claude_installed()
        state = self.state()
        self.assertEqual(set(state), {'version', 'phase', 'database', 'clients', 'client', 'backups'})
        self.assertEqual(state['phase'], 'installed')
        self.assertEqual(state['client'], 'claude')
        self.assertEqual(set(state['clients']), {'codex', 'claude'})
        self.assertEqual(set(state['clients']['codex']), {'block', 'hook_command'})
        self.assertEqual(set(state['clients']['claude']), {'server_entry', 'hook_command'})
        self.assertTrue(state['clients']['claude']['hook_command'].endswith('--host claude'))
        self.assertEqual(len(state['backups']), 2)
        mcp = self.setup(client='mcp')
        self.assertEqual(mcp['clients'], ['claude', 'codex', 'mcp'])
        self.assert_codex_installed()
        self.assert_claude_installed()
        self.assertEqual(self.state()['clients']['mcp'], {})

    def test_claude_then_codex_keeps_claude(self):
        self.setup(client='claude', trust=True)
        self.setup(client='codex')
        self.assert_codex_installed()
        self.assert_claude_installed()
        self.assertEqual(self.claude_settings()['enabledMcpjsonServers'], ['project_memory'])
        self.assertEqual(self.state()['client'], 'codex')

    def test_repeated_setup_does_not_duplicate(self):
        for client in ('codex', 'claude', 'codex', 'claude', 'codex'):
            self.setup(client=client)
        self.assert_codex_installed()
        self.assert_claude_installed()
        self.assertTrue(all(len(groups) == 1 for groups in self.codex_hooks().values()))
        self.assertTrue(all(len(groups) == 1 for groups in self.claude_settings()['hooks'].values()))
        self.assertEqual(len(self.state()['backups']), 2)
        self.setup(client='codex', _launcher=['new-launcher'])
        self.assertIn('new-launcher', self.codex_config())
        self.assertTrue(all(len(groups) == 1 for groups in self.codex_hooks().values()))
        self.assert_claude_installed()

    def test_uninstall_claude_keeps_codex(self):
        (self.project / '.mcp.json').write_text(json.dumps({'mcpServers': {'other': {'command': 'x'}}}))
        self.setup(client='codex')
        self.setup(client='claude', trust=True)
        result = install.uninstall(self.project, 'claude')
        self.assertTrue(result['removed'])
        self.assertEqual(result['clients_removed'], ['claude'])
        self.assertEqual(result['clients'], ['codex'])
        self.assertTrue(result['installation_file_kept'])
        self.assertEqual(self.claude_servers(), {'other': {'command': 'x'}})
        self.assertEqual(self.claude_settings(), {'enabledMcpjsonServers': []})
        state = self.state()
        self.assertEqual(set(state['clients']), {'codex'})
        self.assertEqual(state['client'], 'codex')
        self.assert_codex_installed()
        again = install.uninstall(self.project, 'claude')
        self.assertFalse(again['removed'])
        self.assertEqual(again['clients'], ['codex'])
        install.uninstall(self.project, 'codex')
        self.assertFalse((self.project / '.memory/install.json').exists())
        self.assertEqual(self.codex_config(), '')
        self.assertEqual(self.codex_hooks(), {})

    def test_uninstall_codex_keeps_claude(self):
        local = self.project / '.codex'
        local.mkdir()
        (local / 'config.toml').write_text('model = "other"\n')
        self.setup(client='claude')
        self.setup(client='codex')
        install.uninstall(self.project, 'codex')
        self.assertEqual(self.codex_config(), 'model = "other"\n')
        self.assertEqual(self.codex_hooks(), {})
        self.assert_claude_installed()
        self.assertEqual(self.state()['client'], 'claude')

    def test_uninstall_all_clients(self):
        local = self.project / '.codex'
        local.mkdir()
        (local / 'config.toml').write_text('model = "other"\n')
        (local / 'hooks.json').write_text(json.dumps({'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'other'}]}]}}))
        first = self.setup(client='codex', requirements=['Keep data locally.'])
        self.setup(client='claude')
        self.setup(client='mcp')
        result = install.uninstall(self.project)
        self.assertEqual(result['clients_removed'], ['claude', 'codex', 'mcp'])
        self.assertEqual(result['clients'], [])
        self.assertFalse((self.project / '.memory/install.json').exists())
        self.assertEqual(self.codex_config(), 'model = "other"\n')
        self.assertEqual(self.commands(self.codex_hooks()), {'Stop': ['other']})
        self.assertEqual(self.claude_servers(), {})
        self.assertEqual(self.claude_settings(), {})
        with Memory(first['database']) as memory:
            self.assertEqual(memory.requirements, ['Keep data locally.'])
        self.assertEqual(install.uninstall(self.project)['removed'], False)
        with self.assertRaises(ValueError):
            install.uninstall(self.project, 'cursor')

    def test_legacy_install_file_is_upgraded(self):
        self.setup(client='codex')
        modern = self.state()
        legacy = {'version': modern['version'], 'phase': 'installed', 'database': modern['database'], 'client': 'codex',
                  'block': modern['clients']['codex']['block'], 'hook_command': modern['clients']['codex']['hook_command'],
                  'server_entry': None, 'backups': modern['backups']}
        (self.project / '.memory/install.json').write_text(json.dumps(legacy))
        read = install.project_state(self.project)
        self.assertEqual(read['clients'], modern['clients'])
        self.assertEqual(read['client'], 'codex')
        result = self.setup(client='claude')
        self.assertEqual(result['clients'], ['claude', 'codex'])
        self.assert_codex_installed()
        self.assert_claude_installed()
        self.assertNotIn('block', self.state())
        install.uninstall(self.project, 'claude')
        self.assert_codex_installed()

    def test_legacy_upgrade_covers_claude_mcp_and_interrupted_switch(self):
        entry = {'command': 'x', 'args': ['serve']}
        claude = install.upgrade_state({'database': 'd', 'client': 'claude', 'block': '', 'hook_command': 'h',
                                        'server_entry': entry, 'backups': ['b']})
        self.assertEqual(claude['clients'], {'claude': {'server_entry': entry, 'hook_command': 'h'}})
        self.assertEqual(claude['backups'], ['b'])
        mcp = install.upgrade_state({'database': 'd', 'client': 'mcp', 'block': '', 'hook_command': '', 'server_entry': None})
        self.assertEqual(mcp['clients'], {'mcp': {}})
        switch = install.upgrade_state({'database': 'd', 'phase': 'pending', 'client': 'claude', 'block': '', 'hook_command': 'new',
                                        'server_entry': entry, 'previous_block': 'old block', 'previous_hook_command': 'old',
                                        'previous_server_entry': None})
        self.assertEqual(switch['clients']['codex'], {'block': 'old block', 'hook_command': 'old'})
        self.assertEqual(switch['clients']['claude'], {'server_entry': entry, 'hook_command': 'new'})
        upgrade = install.upgrade_state({'database': 'd', 'phase': 'pending', 'client': 'codex', 'block': 'new block', 'hook_command': 'new',
                                         'previous_block': 'old block', 'previous_hook_command': 'old'})
        self.assertEqual(upgrade['clients'], {'codex': {'block': 'new block', 'hook_command': 'new',
                                                        'previous_block': 'old block', 'previous_hook_command': 'old'}})

    def test_interrupted_claude_setup_recovers_and_keeps_codex(self):
        self.setup(client='codex')
        codex_before = (self.codex_config(), self.codex_hooks())
        for fail_at in [2, 3]:
            self.setup(client='claude')
            real = install.atomic
            calls = []

            def fail(path, content):
                calls.append(path)
                if len(calls) == fail_at:
                    raise OSError('Injected interruption')
                return real(path, content)

            with patch.object(install, 'atomic', side_effect=fail):
                with self.assertRaises(OSError):
                    install.setup(self.project, client='claude', _launcher=['new-launcher'])
            self.assertEqual(self.state()['phase'], 'pending')
            install.setup(self.project, client='claude', _launcher=['new-launcher'])
            self.assertTrue(all(len(groups) == 1 for groups in self.claude_settings()['hooks'].values()))
            self.assertEqual(self.claude_servers()['project_memory']['command'], 'new-launcher')
            self.assertEqual((self.codex_config(), self.codex_hooks()), codex_before)
            self.assertEqual(set(self.state()['clients']['claude']), {'server_entry', 'hook_command'})
            install.uninstall(self.project, 'claude')
            self.assertNotIn('project_memory', self.claude_servers())

    def test_review_host_union(self):
        info = self.setup(client='codex')
        with Memory(info['database']) as memory:
            self.assertEqual(reviews.configured(memory)['hosts'], ['codex'])
        self.setup(client='claude')
        self.setup(client='mcp')
        with Memory(info['database']) as memory:
            config = reviews.configured(memory)
            self.assertEqual(config['host'], 'claude')
            self.assertEqual(config['hosts'], ['claude', 'codex'])
            self.assertEqual(config['project'], str(self.project.resolve()))
            enabled = config['enabled_at']
        self.setup(client='codex')
        with Memory(info['database']) as memory:
            config = reviews.configured(memory)
            self.assertEqual((config['host'], config['hosts'], config['enabled_at']), ('codex', ['claude', 'codex'], enabled))

    def test_legacy_review_host_gains_hosts(self):
        info = self.setup()
        with Memory(info['database']) as memory:
            self.assertIsNone(reviews.configured(memory))
            legacy = {'project': str(self.project), 'host': 'codex', 'enabled_at': memory.now()}
            with memory._write():
                memory.db.execute("INSERT OR REPLACE INTO settings VALUES ('review_host',?)", (json.dumps(legacy),))
            self.assertEqual(reviews.configured(memory), {**legacy, 'hosts': ['codex']})
            reviews.configure(memory, self.project, 'claude')
            config = reviews.configured(memory)
            self.assertEqual((config['host'], config['hosts'], config['enabled_at']), ('claude', ['claude', 'codex'], legacy['enabled_at']))

    def test_setup_initializes_the_graph_when_available(self):
        if importlib.util.find_spec('memory_module.graph'):
            info = self.setup()
            with Memory(info['database']) as memory:
                table = memory.db.execute("SELECT 1 FROM sqlite_master WHERE name='links'").fetchone()
            self.assertTrue(table)
            return
        calls = []
        fake = types.ModuleType('memory_module.graph')
        fake.initialize = calls.append
        with patch.dict(sys.modules, {'memory_module.graph': fake}):
            self.setup()
        self.assertEqual(len(calls), 1)
        if 'graph' in vars(sys.modules['memory_module']):
            delattr(sys.modules['memory_module'], 'graph')
        with Memory(self.setup()['database']) as memory:
            self.assertFalse(install.initialize_graph(memory))

    def test_expected_hook_events_are_the_union_of_clients(self):
        self.assertEqual(expected_hook_events(['claude', 'codex']), codex_host.EVENTS)
        self.assertEqual(expected_hook_events(['claude']), codex_host.HOST_EVENTS['claude'])
        self.assertEqual(expected_hook_events(['claude', 'mcp']), codex_host.HOST_EVENTS['claude'])
        self.assertEqual(expected_hook_events(['mcp']), set())
        self.assertEqual(expected_hook_events(None, 'claude'), codex_host.HOST_EVENTS['claude'])
        self.assertEqual(expected_hook_events(), codex_host.EVENTS)

    def run_cli(self, *argv):
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_cli_doctor_reports_clients_and_uninstall_selects_a_client(self):
        self.setup(client='claude')
        self.setup(client='codex')
        discovery = {'status': 'ready_for_new_session'}
        with patch('memory_module.health.codex_hooks', return_value=discovery) as hooks:
            code, out, err = self.run_cli('doctor', '--project', str(self.project))
        self.assertEqual(code, 0, err)
        report = json.loads(out)
        hooks.assert_called_once()
        self.assertEqual(report['clients'], ['claude', 'codex'])
        self.assertEqual(report['client'], 'codex')
        self.assertEqual(report['host_discovery'], discovery)
        self.assertEqual(set(report['expected_hook_events']), codex_host.EVENTS)
        self.assertIn('Interrupt', report['missing_hook_events'])
        code, out, err = self.run_cli('hook', '--host', 'claude', '--if-unmanaged', '--project', str(self.project))
        self.assertEqual((code, out.strip()), (0, '{}'), err)
        code, out, err = self.run_cli('uninstall', '--client', 'codex', '--project', str(self.project))
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)['clients'], ['claude'])
        code, out, err = self.run_cli('doctor', '--project', str(self.project))
        self.assertEqual(code, 0, err)
        report = json.loads(out)
        self.assertEqual(report['clients'], ['claude'])
        self.assertNotIn('host_discovery', report)
        self.assertNotIn('Interrupt', report['missing_hook_events'])
        code, out, err = self.run_cli('uninstall', '--project', str(self.project))
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)['clients_removed'], ['claude'])
        self.assertFalse((self.project / '.memory/install.json').exists())


if __name__ == '__main__':
    unittest.main()
