"""Host profiles: command lines for every host and role, event parsers, usage, unavailability and structured answers.

This file checks section 16.1 of .memory/build/rebuild-spec.md. No host process starts and no model runs. The golden
command lines of Codex and Claude were captured from hosts.py before it read profiles, so the refactor is proven to
change no argument for any role.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from memory_module import InvalidRecord, Memory, host_profiles, hosts, install, reviews
from memory_module.install import setup
from memory_module.workspace import action

PROMPT = 'Review the work. Ünïcode stays literal: 𝔘.'
DASH_PROMPT = '-starts with a dash'


def _normalise(value, root):
    """Replace the machine specific paths of a command line with stable markers."""
    text = json.dumps(value, ensure_ascii=False)
    # The interpreter goes first: it can lie inside the source folder, as .venv/bin/python does.
    replacements = ((sys.executable, '<python>'), (str(install.SOURCE_ROOT), '<source>'), (str(root), '<root>'))
    for old, new in replacements:
        text = text.replace(json.dumps(old, ensure_ascii=False)[1:-1], new)
    return json.loads(text)


def build_codex_and_claude(root):
    """Build every Codex and Claude command line of every role, in the situations the suite knows."""
    home = root / 'codex-home'
    home.mkdir()
    (home / 'config.toml').write_text('model = "fixture-model"\n[mcp_servers.global_one]\ncommand = "x"\n')
    project = root / 'project'
    (project / '.codex').mkdir(parents=True)
    (project / '.codex/config.toml').write_text('[mcp_servers.local-two]\ncommand = "y"\n')
    worktree = project / '.memory/worktrees/run1'
    worktree.mkdir(parents=True)
    folder = root / 'run'
    folder.mkdir()
    (folder / 'schema.json').write_text('{"type":"object","properties":{"verdict":{"type":"string"}}}')
    binding = {'hive': str(root / '.memory/hive.sqlite'), 'db': str(root / '.memory/project.sqlite'),
               'swarm_id': 'swarm_1', 'agent_id': '-agent', 'role': 'worker'}
    built = {}
    clean = {name: '' for name in hosts.ENVIRONMENT.values()}
    with patch.dict(os.environ, {'CODEX_HOME': str(home), **clean}), patch.object(hosts.shutil, 'which', return_value=None):
        for host in ('codex', 'claude'):
            built[host + ' review'] = hosts.review_command(host, str(project), folder, PROMPT)
            built[host + ' review dash prompt'] = hosts.review_command(host, str(project), folder, DASH_PROMPT)
            built[host + ' work'] = hosts.work_command(host, worktree, folder, PROMPT)
            built[host + ' work in project'] = hosts.work_command(host, str(project), folder, PROMPT)
            built[host + ' work with hive'] = hosts.work_command(host, worktree, folder, PROMPT, hive=binding)
        with patch.dict(os.environ, {'PROJECT_MEMORY_CODEX_BIN': '/opt/fake/codex', 'PROJECT_MEMORY_CLAUDE_BIN': '/opt/fake/claude'}):
            for host in ('codex', 'claude'):
                built[host + ' review with override'] = hosts.review_command(host, str(project), folder, PROMPT)
                built[host + ' work with override'] = hosts.work_command(host, worktree, folder, PROMPT)
    with patch.dict(os.environ, {'CODEX_HOME': str(root / 'missing-home'), **clean}), \
            patch.object(hosts.shutil, 'which', side_effect=lambda name: '/usr/bin/' + name):
        built['codex review without configuration'] = hosts.review_command('codex', str(root), folder, PROMPT)
        built['codex work found on the search path'] = hosts.work_command('codex', worktree, folder, PROMPT)
        built['claude work found on the search path'] = hosts.work_command('claude', worktree, folder, PROMPT)
    return _normalise(built, root)


# Captured from hosts.py at commit 9f711d2, before hosts.py read profiles. Markers replace machine specific paths.
GOLDEN = {
    'codex review': [
        'codex', 'exec', '--ephemeral', '--skip-git-repo-check', '--sandbox', 'read-only', '-C', '<root>/project',
        '-c', 'approval_policy="never"', '-c', 'features.hooks=false', '-c', 'features.plugins=false', '-c',
        'features.apps=false', '-c', 'features.multi_agent=false', '-c', 'project_doc_max_bytes=0', '-c',
        'memories.use_memories=false', '-c', 'memories.generate_memories=false', '-c',
        'skills.include_instructions=false', '-c', 'web_search="disabled"', '-m', 'fixture-model', '-c',
        'mcp_servers.global_one.enabled=false', '-c', 'mcp_servers.local-two.enabled=false', '--output-schema',
        '<root>/run/schema.json', '--output-last-message', '<root>/run/answer.json', '--json', '-'
    ],
    'codex review dash prompt': [
        'codex', 'exec', '--ephemeral', '--skip-git-repo-check', '--sandbox', 'read-only', '-C', '<root>/project',
        '-c', 'approval_policy="never"', '-c', 'features.hooks=false', '-c', 'features.plugins=false', '-c',
        'features.apps=false', '-c', 'features.multi_agent=false', '-c', 'project_doc_max_bytes=0', '-c',
        'memories.use_memories=false', '-c', 'memories.generate_memories=false', '-c',
        'skills.include_instructions=false', '-c', 'web_search="disabled"', '-m', 'fixture-model', '-c',
        'mcp_servers.global_one.enabled=false', '-c', 'mcp_servers.local-two.enabled=false', '--output-schema',
        '<root>/run/schema.json', '--output-last-message', '<root>/run/answer.json', '--json', '-'
    ],
    'codex work': [
        'codex', 'exec', '--ephemeral', '--skip-git-repo-check', '--sandbox', 'workspace-write', '-C',
        '<root>/project/.memory/worktrees/run1', '-c', 'approval_policy="never"', '-c', 'features.hooks=false', '-c',
        'features.plugins=false', '-c', 'features.apps=false', '-c', 'features.multi_agent=false', '-c',
        'memories.use_memories=false', '-c', 'memories.generate_memories=false', '-c', 'web_search="disabled"', '-c',
        'mcp_servers.global_one.enabled=false', '--output-schema', '<root>/run/schema.json', '--output-last-message',
        '<root>/run/answer.json', '--json', '-'
    ],
    'codex work in project': [
        'codex', 'exec', '--ephemeral', '--skip-git-repo-check', '--sandbox', 'workspace-write', '-C',
        '<root>/project', '-c', 'approval_policy="never"', '-c', 'features.hooks=false', '-c',
        'features.plugins=false', '-c', 'features.apps=false', '-c', 'features.multi_agent=false', '-c',
        'memories.use_memories=false', '-c', 'memories.generate_memories=false', '-c', 'web_search="disabled"', '-c',
        'mcp_servers.global_one.enabled=false', '-c', 'mcp_servers.local-two.enabled=false', '--output-schema',
        '<root>/run/schema.json', '--output-last-message', '<root>/run/answer.json', '--json', '-'
    ],
    'codex work with hive': [
        'codex', 'exec', '--ephemeral', '--skip-git-repo-check', '--sandbox', 'workspace-write', '-C',
        '<root>/project/.memory/worktrees/run1', '-c', 'approval_policy="never"', '-c', 'features.hooks=false', '-c',
        'features.plugins=false', '-c', 'features.apps=false', '-c', 'features.multi_agent=false', '-c',
        'memories.use_memories=false', '-c', 'memories.generate_memories=false', '-c', 'web_search="disabled"', '-c',
        'mcp_servers.global_one.enabled=false', '-c', 'mcp_servers.hive.command="<python>"', '-c',
        'mcp_servers.hive.args=["-X", "utf8", "-c", "import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); runpy.run_module(sys.argv.pop(1),run_name=\\"__main__\\")", "<source>", "memory_module.cli", "hive-serve", "--hive=<root>/.memory/hive.sqlite", "--swarm=swarm_1", "--agent=-agent", "--role=worker", "--db=<root>/.memory/project.sqlite"]',
        '--output-schema', '<root>/run/schema.json', '--output-last-message', '<root>/run/answer.json', '--json',
        '-'
    ],
    'claude review': [
        'claude', '-p', '--restricted', '--no-session-persistence', '--output-format', 'stream-json', '--verbose',
        '--permission-mode', 'dontAsk', '--setting-sources', '', '--settings', '{"disableAllHooks":true}',
        '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}', '--disable-slash-commands', '--tools',
        'Read,Glob,Grep', '--allowedTools', 'Read,Glob,Grep', '--json-schema',
        '{"type":"object","properties":{"verdict":{"type":"string"}}}', '--system-prompt',
        'Review the work. Ünïcode stays literal: 𝔘.'
    ],
    'claude review dash prompt': [
        'claude', '-p', '--restricted', '--no-session-persistence', '--output-format', 'stream-json', '--verbose',
        '--permission-mode', 'dontAsk', '--setting-sources', '', '--settings', '{"disableAllHooks":true}',
        '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}', '--disable-slash-commands', '--tools',
        'Read,Glob,Grep', '--allowedTools', 'Read,Glob,Grep', '--json-schema',
        '{"type":"object","properties":{"verdict":{"type":"string"}}}', '--system-prompt', '-starts with a dash'
    ],
    'claude work': [
        'claude', '-p', '--restricted', '--no-session-persistence', '--output-format', 'stream-json', '--verbose',
        '--permission-mode', 'dontAsk', '--setting-sources', '', '--settings',
        '{"disableAllHooks":true,"sandbox":{"enabled":true,"failIfUnavailable":true,"autoAllowBashIfSandboxed":true,"allowUnsandboxedCommands":false}}',
        '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}', '--disable-slash-commands', '--tools',
        'Read,Glob,Grep,Edit,Write,Bash', '--allowedTools', 'Read,Glob,Grep,Edit,Write,Bash', '--json-schema',
        '{"type":"object","properties":{"verdict":{"type":"string"}}}', '--system-prompt',
        'Review the work. Ünïcode stays literal: 𝔘.'
    ],
    'claude work in project': [
        'claude', '-p', '--restricted', '--no-session-persistence', '--output-format', 'stream-json', '--verbose',
        '--permission-mode', 'dontAsk', '--setting-sources', '', '--settings',
        '{"disableAllHooks":true,"sandbox":{"enabled":true,"failIfUnavailable":true,"autoAllowBashIfSandboxed":true,"allowUnsandboxedCommands":false}}',
        '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}', '--disable-slash-commands', '--tools',
        'Read,Glob,Grep,Edit,Write,Bash', '--allowedTools', 'Read,Glob,Grep,Edit,Write,Bash', '--json-schema',
        '{"type":"object","properties":{"verdict":{"type":"string"}}}', '--system-prompt',
        'Review the work. Ünïcode stays literal: 𝔘.'
    ],
    'claude work with hive': [
        'claude', '-p', '--restricted', '--no-session-persistence', '--output-format', 'stream-json', '--verbose',
        '--permission-mode', 'dontAsk', '--setting-sources', '', '--settings',
        '{"disableAllHooks":true,"sandbox":{"enabled":true,"failIfUnavailable":true,"autoAllowBashIfSandboxed":true,"allowUnsandboxedCommands":false}}',
        '--strict-mcp-config', '--mcp-config',
        '{"mcpServers":{"hive":{"type":"stdio","command":"<python>","args":["-X","utf8","-c","import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); runpy.run_module(sys.argv.pop(1),run_name=\\"__main__\\")","<source>","memory_module.cli","hive-serve","--hive=<root>/.memory/hive.sqlite","--swarm=swarm_1","--agent=-agent","--role=worker","--db=<root>/.memory/project.sqlite"]}}}',
        '--disable-slash-commands', '--tools', 'Read,Glob,Grep,Edit,Write,Bash', '--allowedTools',
        'Read,Glob,Grep,Edit,Write,Bash,mcp__hive__hive_log,mcp__hive__hive_query,mcp__hive__hive_resume',
        '--json-schema', '{"type":"object","properties":{"verdict":{"type":"string"}}}', '--system-prompt',
        'Review the work. Ünïcode stays literal: 𝔘.'
    ],
    'codex review with override': [
        '/opt/fake/codex', 'exec', '--ephemeral', '--skip-git-repo-check', '--sandbox', 'read-only', '-C',
        '<root>/project', '-c', 'approval_policy="never"', '-c', 'features.hooks=false', '-c',
        'features.plugins=false', '-c', 'features.apps=false', '-c', 'features.multi_agent=false', '-c',
        'project_doc_max_bytes=0', '-c', 'memories.use_memories=false', '-c', 'memories.generate_memories=false',
        '-c', 'skills.include_instructions=false', '-c', 'web_search="disabled"', '-m', 'fixture-model', '-c',
        'mcp_servers.global_one.enabled=false', '-c', 'mcp_servers.local-two.enabled=false', '--output-schema',
        '<root>/run/schema.json', '--output-last-message', '<root>/run/answer.json', '--json', '-'
    ],
    'codex work with override': [
        '/opt/fake/codex', 'exec', '--ephemeral', '--skip-git-repo-check', '--sandbox', 'workspace-write', '-C',
        '<root>/project/.memory/worktrees/run1', '-c', 'approval_policy="never"', '-c', 'features.hooks=false', '-c',
        'features.plugins=false', '-c', 'features.apps=false', '-c', 'features.multi_agent=false', '-c',
        'memories.use_memories=false', '-c', 'memories.generate_memories=false', '-c', 'web_search="disabled"', '-c',
        'mcp_servers.global_one.enabled=false', '--output-schema', '<root>/run/schema.json', '--output-last-message',
        '<root>/run/answer.json', '--json', '-'
    ],
    'claude review with override': [
        '/opt/fake/claude', '-p', '--restricted', '--no-session-persistence', '--output-format', 'stream-json',
        '--verbose', '--permission-mode', 'dontAsk', '--setting-sources', '', '--settings',
        '{"disableAllHooks":true}', '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
        '--disable-slash-commands', '--tools', 'Read,Glob,Grep', '--allowedTools', 'Read,Glob,Grep', '--json-schema',
        '{"type":"object","properties":{"verdict":{"type":"string"}}}', '--system-prompt',
        'Review the work. Ünïcode stays literal: 𝔘.'
    ],
    'claude work with override': [
        '/opt/fake/claude', '-p', '--restricted', '--no-session-persistence', '--output-format', 'stream-json',
        '--verbose', '--permission-mode', 'dontAsk', '--setting-sources', '', '--settings',
        '{"disableAllHooks":true,"sandbox":{"enabled":true,"failIfUnavailable":true,"autoAllowBashIfSandboxed":true,"allowUnsandboxedCommands":false}}',
        '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}', '--disable-slash-commands', '--tools',
        'Read,Glob,Grep,Edit,Write,Bash', '--allowedTools', 'Read,Glob,Grep,Edit,Write,Bash', '--json-schema',
        '{"type":"object","properties":{"verdict":{"type":"string"}}}', '--system-prompt',
        'Review the work. Ünïcode stays literal: 𝔘.'
    ],
    'codex review without configuration': [
        'codex', 'exec', '--ephemeral', '--skip-git-repo-check', '--sandbox', 'read-only', '-C', '<root>', '-c',
        'approval_policy="never"', '-c', 'features.hooks=false', '-c', 'features.plugins=false', '-c',
        'features.apps=false', '-c', 'features.multi_agent=false', '-c', 'project_doc_max_bytes=0', '-c',
        'memories.use_memories=false', '-c', 'memories.generate_memories=false', '-c',
        'skills.include_instructions=false', '-c', 'web_search="disabled"', '--output-schema',
        '<root>/run/schema.json', '--output-last-message', '<root>/run/answer.json', '--json', '-'
    ],
    'codex work found on the search path': [
        '/usr/bin/codex', 'exec', '--ephemeral', '--skip-git-repo-check', '--sandbox', 'workspace-write', '-C',
        '<root>/project/.memory/worktrees/run1', '-c', 'approval_policy="never"', '-c', 'features.hooks=false', '-c',
        'features.plugins=false', '-c', 'features.apps=false', '-c', 'features.multi_agent=false', '-c',
        'memories.use_memories=false', '-c', 'memories.generate_memories=false', '-c', 'web_search="disabled"',
        '--output-schema', '<root>/run/schema.json', '--output-last-message', '<root>/run/answer.json', '--json',
        '-'
    ],
    'claude work found on the search path': [
        '/usr/bin/claude', '-p', '--restricted', '--no-session-persistence', '--output-format', 'stream-json',
        '--verbose', '--permission-mode', 'dontAsk', '--setting-sources', '', '--settings',
        '{"disableAllHooks":true,"sandbox":{"enabled":true,"failIfUnavailable":true,"autoAllowBashIfSandboxed":true,"allowUnsandboxedCommands":false}}',
        '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}', '--disable-slash-commands', '--tools',
        'Read,Glob,Grep,Edit,Write,Bash', '--allowedTools', 'Read,Glob,Grep,Edit,Write,Bash', '--json-schema',
        '{"type":"object","properties":{"verdict":{"type":"string"}}}', '--system-prompt',
        'Review the work. Ünïcode stays literal: 𝔘.'
    ],
}


@unittest.skipIf(os.name == 'nt', 'The golden command lines hold POSIX path separators.')
class GoldenCommandLineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name).resolve()

    def tearDown(self):
        shutil.rmtree(self.temp.name, ignore_errors=True)

    def test_codex_and_claude_command_lines_are_unchanged_for_every_role(self):
        built = build_codex_and_claude(self.root)
        self.assertEqual(sorted(built), sorted(GOLDEN))
        for name, args in GOLDEN.items():
            with self.subTest(name=name):
                self.assertEqual(built[name], args)


SCHEMA_TEXT = '{"type":"object","properties":{"verdict":{"type":"string"}}}'
GROK_ENVIRONMENT = {
    'GROK_CLAUDE_SKILLS_ENABLED': 'false', 'GROK_CLAUDE_RULES_ENABLED': 'false', 'GROK_CLAUDE_AGENTS_ENABLED': 'false',
    'GROK_CLAUDE_MCPS_ENABLED': 'false', 'GROK_CLAUDE_HOOKS_ENABLED': 'false', 'GROK_CURSOR_SKILLS_ENABLED': 'false',
    'GROK_CURSOR_RULES_ENABLED': 'false', 'GROK_CURSOR_AGENTS_ENABLED': 'false', 'GROK_CURSOR_MCPS_ENABLED': 'false',
    'GROK_CURSOR_HOOKS_ENABLED': 'false', 'GROK_MEMORY': '0'}


class NewHostCommandTests(unittest.TestCase):
    """The exact command lines, environments and roles of the Grok and OpenCode profiles."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / 'project'
        self.project.mkdir()
        self.folder = self.root / 'run'
        self.folder.mkdir()
        (self.folder / 'schema.json').write_text(SCHEMA_TEXT)
        self.xdg = self.root / 'xdg'
        (self.xdg / 'opencode').mkdir(parents=True)
        clean = {name: '' for name in hosts.ENVIRONMENT.values()}
        self.grok_home = self.root / 'grok-user-home'
        self.grok_home.mkdir()
        (self.grok_home / 'auth.json').write_text('{"invented": "sign in"}')
        self.env = patch.dict(os.environ, {'XDG_CONFIG_HOME': str(self.xdg), 'GROK_HOME': str(self.grok_home), **clean})
        self.env.start()
        self.which = patch.object(hosts.shutil, 'which', side_effect=lambda name: '/usr/bin/' + name)
        self.which.start()

    def tearDown(self):
        self.which.stop()
        self.env.stop()
        shutil.rmtree(self.temp.name, ignore_errors=True)

    def test_grok_review_command_line_and_environment(self):
        args = hosts.review_command('grok', str(self.project), self.folder, DASH_PROMPT)
        self.assertEqual(args, [
            'grok', '--prompt-file', str(self.folder / 'prompt.txt'), '--output-format', 'streaming-json',
            '--json-schema', SCHEMA_TEXT, '--cwd', str(self.project), '--sandbox', 'read-only',
            '--permission-mode', 'dontAsk', '--tools', 'read_file,grep,list_dir', '--deny', 'MCPTool', '--deny', 'Bash',
            '--deny', 'Edit', '--deny', 'Write', '--deny', 'WebFetch', '--no-subagents',
            '--disable-web-search', '--max-turns', '50', '--verbatim', '--rules=-starts with a dash'])
        self.assertEqual(hosts.review_environment('grok', str(self.project), self.folder, PROMPT),
                         {**GROK_ENVIRONMENT, 'GROK_HOME': str(self.folder / 'grok-home')})
        with patch.dict(os.environ, {'PROJECT_MEMORY_GROK_BIN': '/opt/fake/grok'}):
            self.assertEqual(hosts.review_command('grok', str(self.project), self.folder, PROMPT)[0], '/opt/fake/grok')

    def test_grok_work_command_line_refuses_the_hive(self):
        worktree = self.project / '.memory/worktrees/run1'
        args = hosts.work_command('grok', worktree, self.folder, PROMPT)
        self.assertEqual(args, [
            '/usr/bin/grok', '--prompt-file', str(self.folder / 'prompt.txt'), '--output-format', 'streaming-json',
            '--json-schema', SCHEMA_TEXT, '--cwd', str(worktree), '--sandbox', 'workspace',
            '--permission-mode', 'dontAsk', '--tools', 'read_file,grep,list_dir,search_replace,write_file,run_terminal_cmd',
            '--allow', 'Read', '--allow', 'Grep', '--allow', 'Edit', '--allow', 'Write', '--allow', 'Bash',
            '--deny', 'MCPTool', '--deny', 'WebFetch', '--no-subagents', '--disable-web-search',
            '--max-turns', '200', '--verbatim', '--rules=' + PROMPT])
        self.assertEqual(hosts.work_environment('grok', str(worktree), self.folder, PROMPT),
                         {**GROK_ENVIRONMENT, 'GROK_HOME': str(self.folder / 'grok-home')})
        binding = {'hive': 'h', 'db': 'd', 'swarm_id': 's', 'agent_id': 'a', 'role': 'worker'}
        with self.assertRaises(InvalidRecord) as caught:
            hosts.work_command('grok', worktree, self.folder, PROMPT, hive=binding)
        self.assertEqual(str(caught.exception), 'The grok host cannot receive the hive server on its command line. '
                                                'Delegate work of a swarm to Codex or Claude.')

    def test_a_grok_run_home_links_the_sign_in_and_ignores_the_skills_of_the_user(self):
        # Grok 1.0 has no switch for user skills and removed --no-memory, so each run gets its own home (Grok 1.0.34).
        environment = hosts.review_environment('grok', str(self.project), self.folder, PROMPT)
        home = Path(environment['GROK_HOME'])
        self.assertTrue((home / 'auth.json').is_symlink(), 'The sign in is linked, never copied.')
        self.assertEqual((home / 'auth.json').resolve(), (self.grok_home / 'auth.json').resolve())
        configuration = tomllib.loads((home / 'config.toml').read_text(encoding='utf-8'))
        self.assertEqual(configuration, {'skills': {'ignore': [str(Path.home())]}, 'memory': {'enabled': False}})
        self.assertEqual(environment['GROK_MEMORY'], '0')
        self.assertEqual(hosts.review_environment('grok', str(self.project), self.folder, PROMPT), environment)

    def test_a_grok_run_keeps_the_default_home_when_no_link_can_be_made(self):
        with patch.object(Path, 'symlink_to', side_effect=OSError('no right to create links')):
            self.assertEqual(hosts.review_environment('grok', str(self.project), self.root / 'other-run', PROMPT), GROK_ENVIRONMENT)

    def test_grok_1_usage_reads_uncached_input_cache_reads_and_cache_writes(self):
        usage = {'input_tokens': 7210, 'cache_read_input_tokens': 41000, 'cache_creation_input_tokens': 5,
                 'output_tokens': 1893, 'reasoning_tokens': 412, 'total_tokens': 50108}
        totals = host_profiles.grok_usage(usage)
        self.assertEqual((totals['input'], totals['cache_read'], totals['cache_write'], totals['output'], totals['reasoning']),
                         (7210, 41000, 5, 1893, 412))

    def test_a_refused_option_is_named_without_storing_the_output_of_the_host(self):
        self.assertEqual(hosts._refused_option("error: unexpected argument '--no-memory' found\n\nUsage: grok"), '--no-memory')
        self.assertIsNone(hosts._refused_option('Some other failure with a secret value'))

    def test_grok_refuses_a_folder_whose_repository_holds_grok_configuration(self):
        worktree = self.project / '.memory/worktrees/run1'
        worktree.mkdir(parents=True)
        (worktree / '.git').write_text('gitdir: elsewhere\n')
        self.assertEqual(hosts.work_command('grok', worktree, self.folder, PROMPT)[0], '/usr/bin/grok')
        (worktree / '.agents' / 'skills' / 'repo-skill').mkdir(parents=True)
        with self.assertRaises(InvalidRecord) as caught:
            hosts.work_command('grok', worktree, self.folder, PROMPT)
        self.assertEqual(str(caught.exception), 'The grok host would load the repository configuration in .agents/skills, and '
                                                'no Grok setting switches that off. Remove that configuration from the '
                                                'repository or use another host.')
        # Configuration at the git root applies to a folder inside the repository.
        (self.project / '.git').mkdir()
        (self.project / '.grok').mkdir()
        (self.project / '.grok' / 'config.toml').write_text('[mcp_servers.repo_server]\ncommand = "true"\n')
        subfolder = self.project / 'src'
        subfolder.mkdir()
        with self.assertRaisesRegex(InvalidRecord, 'repository configuration in .grok, and no Grok setting'):
            hosts.review_command('grok', str(subfolder), self.folder, PROMPT)
        # Outside a git repository only the folder itself counts, and the Grok home folder is never repository configuration.
        outside = self.root / 'plain' / 'inner'
        outside.mkdir(parents=True)
        (self.root / 'plain' / '.grok').mkdir()
        self.assertEqual(hosts.review_command('grok', str(outside), self.folder, PROMPT)[0], 'grok')
        with patch.dict(os.environ, {'GROK_HOME': str(self.project / '.grok')}):
            self.assertEqual(hosts.review_command('grok', str(self.project), self.folder, PROMPT)[0], 'grok')
        # Codex and Claude are not affected.
        self.assertEqual(hosts.review_command('claude', str(self.project), self.folder, PROMPT)[0], 'claude')

    def test_the_mcp_delegate_host_rule_names_every_known_host(self):
        from memory_module import mcp
        rules, _ = mcp.operation_rules('delegate')
        self.assertEqual(rules['host']['enum'], ['codex', 'claude', 'grok', 'opencode', None])

    def test_an_unreadable_version_is_remembered_so_identical_calls_agree(self):
        hosts._VERSIONS.clear()
        self.addCleanup(hosts._VERSIONS.clear)
        answers = [subprocess.TimeoutExpired(['grok', '--version'], 15),
                   subprocess.CompletedProcess(['grok', '--version'], 0, 'grok 0.2.93\n', '')]

        def run(*args, **options):
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer
        with patch.object(hosts.subprocess, 'run', side_effect=run) as called, \
                patch.object(hosts.time, 'monotonic', return_value=1000.0):
            self.assertIsNone(hosts.installed_version('grok'))
            self.assertIsNone(hosts.installed_version('grok'))
            self.assertEqual(called.call_count, 1)
        with patch.object(hosts.subprocess, 'run', side_effect=run), \
                patch.object(hosts.time, 'monotonic', return_value=1000.0 + hosts.VERSION_RETRY_SECONDS):
            self.assertEqual(hosts.installed_version('grok'), 'grok 0.2.93')
            self.assertEqual(hosts.installed_version('grok'), 'grok 0.2.93')

    def test_opencode_review_command_line_configuration_and_environment(self):
        (self.xdg / 'opencode/opencode.jsonc').write_text(
            '{\n  // The user model.\n  "$schema": "https://opencode.ai/config.json",\n'
            '  "model": "opencode-go/fixture-model", /* trailing comma follows */\n'
            '  "mcp": {"shelf": {"type": "local", "command": ["x"]}},\n}\n')
        args = hosts.review_command('opencode', str(self.project), self.folder, PROMPT)
        self.assertEqual(args, ['opencode', 'run', '--format', 'json', '--pure', '--dir', str(self.project),
                                '--agent', 'project-memory-review', '--model', 'opencode-go/fixture-model'])
        environment = hosts.review_environment('opencode', str(self.project), self.folder, PROMPT)
        self.assertEqual(environment, {
            'OPENCODE_CONFIG': str(self.folder / 'opencode.json'), 'XDG_CONFIG_HOME': str(self.folder / 'opencode-config-home'),
            'OPENCODE_DISABLE_PROJECT_CONFIG': '1', 'OPENCODE_DISABLE_CLAUDE_CODE': '1', 'OPENCODE_DISABLE_EXTERNAL_SKILLS': '1',
            'OPENCODE_DISABLE_DEFAULT_PLUGINS': '1', 'OPENCODE_DISABLE_AUTOUPDATE': '1', 'OPENCODE_DISABLE_SHARE': '1'})
        self.assertEqual(list((self.folder / 'opencode-config-home').iterdir()), [])
        config = json.loads((self.folder / 'opencode.json').read_text(encoding='utf-8'))
        denied = {name: 'deny' for name in ('edit', 'bash', 'webfetch', 'websearch', 'task', 'skill', 'todowrite',
                                            'question', 'external_directory')}
        self.assertEqual(config['mcp'], {})
        self.assertEqual(config['permission'], denied)
        agent = config['agent']['project-memory-review']
        self.assertEqual((agent['mode'], agent['permission']), ('primary', denied))
        self.assertTrue(agent['prompt'].startswith(PROMPT))
        self.assertTrue(agent['prompt'].endswith(SCHEMA_TEXT))
        self.assertIn('```json', agent['prompt'])
        (self.xdg / 'opencode/opencode.jsonc').unlink()
        self.assertEqual(hosts.review_command('opencode', str(self.project), self.folder, PROMPT)[-2:],
                         ['--agent', 'project-memory-review'])

    def test_opencode_takes_no_work_and_unknown_hosts_are_refused(self):
        with self.assertRaises(InvalidRecord) as caught:
            hosts.work_command('opencode', self.project, self.folder, PROMPT)
        self.assertEqual(str(caught.exception),
                         'The opencode host does not take delegated work. Select a host whose profile allows work.')
        with self.assertRaises(InvalidRecord) as caught:
            hosts.review_command('gemini', str(self.project), self.folder, PROMPT)
        self.assertEqual(str(caught.exception), 'The agent host must be codex, claude, grok or opencode.')

    def test_roles_before_and_after_a_probe(self):
        expected = {'codex': (['review', 'work'], ['review', 'work']), 'claude': (['review', 'work'], ['review', 'work']),
                    'grok': (['review'], ['review', 'work']), 'opencode': (['review'], ['review'])}
        self.assertEqual({row['host']: (row['roles_before_probe'], row['roles_after_probe']) for row in host_profiles.summary()},
                         expected)
        self.assertEqual(hosts.HOSTS, ('codex', 'claude'))
        self.assertEqual(hosts.KNOWN_HOSTS, ('codex', 'claude', 'grok', 'opencode'))
        self.assertTrue(hosts.allows('grok', 'outcome'))
        self.assertFalse(hosts.allows('grok', 'work'))
        self.assertTrue(hosts.allows('grok', 'work', probed=True))
        self.assertFalse(hosts.allows('opencode', 'work', probed=True))
        self.assertEqual([hosts.profile(name).packet_in_input for name in hosts.KNOWN_HOSTS], [True, False, False, False])

    def test_install_location_is_used_when_the_program_is_not_on_the_search_path(self):
        home = self.root / 'home'
        (home / '.grok/bin').mkdir(parents=True)
        (home / '.grok/bin/grok').write_text('')
        with patch.object(hosts.shutil, 'which', return_value=None), patch.object(hosts.Path, 'home', return_value=home):
            self.assertEqual(hosts.executable('grok'), str(home / '.grok/bin/grok'))
            self.assertEqual(hosts.review_command('grok', str(self.project), self.folder, PROMPT)[0], str(home / '.grok/bin/grok'))
            self.assertIsNone(hosts.executable('opencode'))
            self.assertEqual(hosts.review_command('opencode', str(self.project), self.folder, PROMPT)[0], 'opencode')
            self.assertIsNone(hosts.executable('codex'))


class NewHostEventTests(unittest.TestCase):
    """Recorded event shapes of Grok streaming-json and OpenCode json, including malformed lines."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.folder = Path(self.temp.name)
        self.output = self.folder / 'output.jsonl'

    def tearDown(self):
        shutil.rmtree(self.temp.name, ignore_errors=True)

    def read(self, host, lines):
        self.output.write_text('\n'.join(line if isinstance(line, str) else json.dumps(line) for line in lines) + '\n')
        log = hosts.RunLog(self.folder, host)
        metrics = log.read(final=True)
        return log, metrics

    def finish(self, host, log, metrics):
        metrics['termination_reason'] = 'completed'
        try:
            report = hosts.host_answer(host, self.folder, log)
        except (ValueError, InvalidRecord) as exc:
            metrics['report_error'] = str(exc)
            report = None
        return report, hosts.run_state(host, metrics, report, missing_report='missing', exit_message='exit')

    def test_grok_stream_with_structured_output_and_usage(self):
        answer = {'verdict': 'pass'}
        log, metrics = self.read('grok', [
            {'type': 'thought', 'data': 'Reading the files.'}, '{"type": "text", "data": ', {'type': 'text', 'data': '{"verdict":'},
            {'type': 'text', 'data': ' "pass"}'}, {'type': 'auto_compact_started', 'percentage': 85},
            {'type': 'end', 'stopReason': 'EndTurn', 'sessionId': 's1', 'requestId': 'r1', 'structuredOutput': answer,
             'usage': {'inputTokens': 1200, 'outputTokens': 80, 'reasoningTokens': 40, 'totalTokens': 1320}}])
        self.assertEqual(metrics['unparsed_events'], 1)
        self.assertEqual(metrics['phase'], 'report_received')
        self.assertEqual(hosts.usage('grok', metrics['provider_usage']),
                         {'input': 1200, 'output': 80, 'reasoning': 40, 'cache_read': None, 'cache_write': None,
                          'total': 1320, 'cost': None})
        self.assertNotIn('verdict', json.dumps(metrics))
        report, state = self.finish('grok', log, metrics)
        self.assertEqual((report, state), (answer, ('completed', '', None)))

    def test_grok_answer_from_text_without_usage(self):
        log, metrics = self.read('grok', [{'type': 'text', 'data': '{"verdict": "uncertain"}'},
                                          {'type': 'end', 'stopReason': 'EndTurn', 'sessionId': 's', 'requestId': 'r'}])
        self.assertIsNone(metrics['provider_usage'])
        self.assertIsNone(hosts.usage('grok', metrics['provider_usage'])['total'])
        self.assertEqual(self.finish('grok', log, metrics)[0], {'verdict': 'uncertain'})

    def test_grok_structured_output_error_and_max_turns_fail_the_run(self):
        log, metrics = self.read('grok', [{'type': 'text', 'data': 'Not JSON.'},
                                          {'type': 'end', 'stopReason': 'EndTurn', 'structuredOutputError': 'invalid'}])
        report, state = self.finish('grok', log, metrics)
        self.assertEqual(state, ('failed', 'The grok host did not produce the structured answer that the schema requires.',
                                 'invalid_report'))
        log, metrics = self.read('grok', [{'type': 'text', 'data': 'Partial.'}, {'type': 'max_turns_reached'}])
        self.assertEqual((metrics['phase'], metrics['unrecovered_error_events']), ('host_error', 1))
        self.assertEqual(self.finish('grok', log, metrics)[1][0], 'failed')

    def test_grok_limits_and_sign_in(self):
        cases = [("You've hit the rate limit for your plan. Upgrade your account or try again later.", 'rate_limit'),
                 ("You've reached your free Grok Build usage limit for now. Try again in 3 hours.", 'usage_limit'),
                 ('Not signed in. Run `grok login` to authenticate.', 'authentication')]
        for text, reason in cases:
            with self.subTest(reason=reason):
                log, metrics = self.read('grok', [{'type': 'error', 'message': text}])
                self.assertEqual(metrics['host_unavailable']['reason'], reason)
                self.assertNotIn(text[:20], json.dumps(metrics))
                metrics['termination_reason'] = 'host_exit'
                self.assertEqual(hosts.run_state('grok', metrics, None, missing_report='m', exit_message='e')[0],
                                 'host_unavailable')
        self.assertIsNone(hosts.unavailable('Not signed in. Run `grok login` to authenticate.'))
        self.assertEqual(hosts.unavailable('Not signed in.', host='grok'), {'reason': 'authentication', 'until': None})

    def opencode_events(self, final_text, reason='stop'):
        return [
            {'type': 'step_start', 'timestamp': 1, 'sessionID': 'ses', 'part': {'type': 'step-start'}},
            {'type': 'tool_use', 'timestamp': 2, 'sessionID': 'ses',
             'part': {'type': 'tool', 'tool': 'read', 'state': {'status': 'completed'}}},
            {'type': 'tool_use', 'timestamp': 3, 'sessionID': 'ses',
             'part': {'type': 'tool', 'tool': 'bash', 'state': {'status': 'error'}}},
            {'type': 'step_finish', 'timestamp': 4, 'sessionID': 'ses', 'part': {
                'type': 'step-finish', 'reason': 'tool-calls', 'cost': 0.08,
                'tokens': {'total': 28000, 'input': 27900, 'output': 60, 'reasoning': 40, 'cache': {'read': 0, 'write': 0}}}},
            'not json at all',
            {'type': 'text', 'timestamp': 5, 'sessionID': 'ses', 'part': {'type': 'text', 'text': final_text}},
            {'type': 'step_finish', 'timestamp': 6, 'sessionID': 'ses', 'part': {
                'type': 'step-finish', 'reason': reason, 'cost': 0.005,
                'tokens': {'total': 500, 'input': 300, 'output': 150, 'reasoning': 50, 'cache': {'read': 1000, 'write': 20}}}}]

    def test_opencode_final_json_block_usage_and_inspections(self):
        text = 'The review is done.\n```json\n{"verdict": "draft"}\n```\nThen the final report.\n```json\n{"verdict": "pass"}\n```'
        log, metrics = self.read('opencode', self.opencode_events(text))
        self.assertEqual((metrics['completed_inspections'], metrics['failed_inspections'], metrics['unparsed_events']), (2, 1, 1))
        self.assertEqual(metrics['phase'], 'report_received')
        totals = hosts.usage('opencode', metrics['provider_usage'])
        self.assertEqual({key: value for key, value in totals.items() if key != 'cost'},
                         {'input': 28200, 'output': 210, 'reasoning': 90, 'cache_read': 1000, 'cache_write': 20, 'total': 28500})
        self.assertAlmostEqual(totals['cost'], 0.085)
        self.assertNotIn('verdict', json.dumps(metrics))
        self.assertEqual(self.finish('opencode', log, metrics), ({'verdict': 'pass'}, ('completed', '', None)))

    def test_opencode_missing_or_invalid_json_block_fails_the_review(self):
        cases = (('The work meets the criterion. {"verdict": "pass"}',
                  'The opencode host did not end its answer with a JSON block, so the run has no report.'),
                 ('```json\n{"verdict": pass}\n```',
                  'The final JSON block of the opencode answer is not valid JSON, so the run has no report.'))
        for text, error in cases:
            with self.subTest(error=error[:40]):
                log, metrics = self.read('opencode', self.opencode_events(text))
                self.assertEqual(self.finish('opencode', log, metrics), (None, ('failed', error, 'invalid_report')))

    def test_opencode_errors_limits_and_reset_times(self):
        now = hosts.datetime(2026, 9, 17, 12, 0, tzinfo=hosts.timezone.utc)
        cases = [({'name': 'ProviderAuthError', 'data': {'providerID': 'opencode-go', 'message': 'The key was refused.'}},
                  {'reason': 'authentication', 'until': None}),
                 ({'name': 'APIError', 'data': {'message': 'Rate limit exceeded, retry in 30 seconds', 'statusCode': 429}},
                  {'reason': 'rate_limit', 'until': (now + hosts.timedelta(seconds=30)).isoformat()}),
                 ({'name': 'GoUsageLimitError', 'data': {'message': 'It will reset in 2 hours.'}},
                  {'reason': 'usage_limit', 'until': (now + hosts.timedelta(hours=2)).isoformat()})]
        for error, expected in cases:
            with self.subTest(name=error['name']):
                text = ' '.join(hosts._strings({'type': 'error', 'error': error}))
                self.assertEqual(hosts.unavailable(text, now, host='opencode'), expected)
                log, metrics = self.read('opencode', [{'type': 'error', 'timestamp': 1, 'sessionID': 'ses', 'error': error}])
                self.assertEqual(metrics['host_unavailable']['reason'], expected['reason'])
        self.assertIsNone(hosts.parse_until('It will reset in 2 hours.', now))
        self.assertIsNone(hosts.unavailable('ProviderAuthError'))

    def test_codex_and_claude_logs_ignore_the_new_event_shapes(self):
        lines = [{'type': 'text', 'data': '{}'}, {'type': 'end', 'structuredOutput': {}},
                 {'type': 'step_finish', 'part': {'reason': 'stop', 'tokens': {'total': 1}}}]
        for host in (None, 'codex', 'claude'):
            with self.subTest(host=host):
                log, metrics = self.read(host, lines)
                self.assertEqual((metrics['phase'], metrics['provider_usage'], log.text(), log.result),
                                 ('starting', None, '', None))

    def test_answer_text_is_bounded(self):
        log, _ = self.read('grok', [{'type': 'text', 'data': 'x' * 100_000}] * 4)
        self.assertEqual(len(log.text()), host_profiles.TEXT_LIMIT)


class ProfileUsageTests(unittest.TestCase):
    def test_codex_and_claude_usage(self):
        codex = hosts.usage('codex', [{'input_tokens': 100, 'cached_input_tokens': 60, 'output_tokens': 10,
                                       'reasoning_output_tokens': 4}, {'input_tokens': 50, 'output_tokens': 5}, 'malformed'])
        self.assertEqual(codex, {'input': 150, 'output': 15, 'reasoning': 4, 'cache_read': 60, 'cache_write': None,
                                 'total': None, 'cost': None})
        claude = hosts.usage('claude', {'input_tokens': 7, 'output_tokens': 3, 'cache_read_input_tokens': 900,
                                        'cache_creation_input_tokens': 40})
        self.assertEqual(claude, {'input': 7, 'output': 3, 'reasoning': None, 'cache_read': 900, 'cache_write': 40,
                                  'total': None, 'cost': None})
        empty = {'input': None, 'output': None, 'reasoning': None, 'cache_read': None, 'cache_write': None,
                 'total': None, 'cost': None}
        self.assertEqual(hosts.usage('claude', None), empty)
        self.assertEqual(hosts.usage('opencode', [{'tokens': {'input': True}, 'cost': 'free'}]), empty)


class ConfigurationTests(unittest.TestCase):
    """A project may list Grok and OpenCode; a host that is not installed is reported unavailable and never stops the others."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name).resolve()
        info = setup(self.root, requirements=['Keep history.'])
        self.memory = Memory(info['database'])
        self.work = action(self.memory, 'plan', {
            'title': 'Repair the parser', 'objective': 'Preserve both decoding paths.', 'criterion': 'Both paths decode.',
            'subject': 'code', 'payload': {'state': 'ready', 'next_action': 'Inspect the parser.', 'scope': 'Change decoding only.',
                                           'autonomy': 'act', 'reason': 'The user requests the repair.'}}, 'fixture')
        clean = {name: '' for name in hosts.ENVIRONMENT.values()}
        self.env = patch.dict(os.environ, {'XDG_CONFIG_HOME': str(self.root / 'xdg'), **clean})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.memory.close()
        shutil.rmtree(self.temp.name, ignore_errors=True)

    def test_new_hosts_join_the_host_list_and_missing_programs_do_not_stop_others(self):
        for host in ('grok', 'opencode', 'claude'):
            reviews.configure(self.memory, self.root, host)
        self.assertEqual(reviews.configured(self.memory)['hosts'], ['claude', 'grok', 'opencode'])
        with self.assertRaises(InvalidRecord):
            reviews.configure(self.memory, self.root, 'gemini')
        missing_grok = patch.object(hosts.shutil, 'which', side_effect=lambda name: None if name == 'grok' else '/usr/bin/' + name)
        with missing_grok, patch.object(hosts.Path, 'home', return_value=self.root / 'empty-home'):
            self.assertEqual(hosts.availability(self.memory, 'grok'),
                             {'host': 'grok', 'installed': False, 'available': True, 'until': None, 'reason': None})
            self.assertEqual(hosts.choose(self.memory, 'grok', allowed=['grok', 'opencode']), 'opencode')
            hosts.mark_unavailable(self.memory, 'opencode', 'authentication')
            with self.assertRaises(InvalidRecord) as caught:
                hosts.choose(self.memory, 'grok', allowed=['grok', 'opencode'])
            self.assertEqual([row['host'] for row in caught.exception.details['availability']], ['grok', 'opencode'])
            self.assertEqual(reviews.review_host(self.memory, reviews.configured(self.memory), 'grok'), 'claude')

    def test_an_opencode_review_runs_isolated_and_fails_without_a_json_block(self):
        reviews.configure(self.memory, self.root, 'opencode')
        with patch.object(hosts.shutil, 'which', side_effect=lambda name: '/usr/bin/' + name):
            run = reviews.request(self.memory, self.work['episode_id'], request_key='check', retry=True)
        self.assertEqual(run['host'], 'opencode')
        folder = self.memory.path.parent / 'agent-runs' / run['id']
        # The fake host records which isolation variables it received, then answers without the JSON block.
        program = ('import json, os, pathlib, sys\n'
                   'names = ["OPENCODE_CONFIG", "XDG_CONFIG_HOME", "OPENCODE_DISABLE_PROJECT_CONFIG"]\n'
                   f'pathlib.Path({str(folder / "seen.json")!r}).write_text(json.dumps({{n: os.environ.get(n) for n in names}}))\n'
                   'sys.stdin.read()\n'
                   'print(json.dumps({"type": "text", "sessionID": "s", "part": {"type": "text", "text": "The work passes."}}))\n'
                   'print(json.dumps({"type": "step_finish", "sessionID": "s", "part": {"reason": "stop", '
                   '"tokens": {"total": 9, "input": 7, "output": 2, "reasoning": 0, "cache": {"read": 0, "write": 0}}, "cost": 0.001}}))\n')
        with patch.object(reviews, 'command', return_value=[sys.executable, '-c', program]):
            reviews.execute(self.memory, run['id'], timeout=20)
        finished = reviews.read(self.memory, run['id'])
        self.assertEqual(finished['state'], 'failed')
        self.assertEqual(finished['error'], 'The opencode host did not end its answer with a JSON block, so the run has no report.')
        self.assertEqual(finished['metrics']['termination_reason'], 'invalid_report')
        self.assertEqual(hosts.usage('opencode', finished['metrics']['provider_usage'])['total'], 9)
        seen = json.loads((folder / 'seen.json').read_text())
        self.assertEqual(seen, {'OPENCODE_CONFIG': str(folder / 'opencode.json'),
                                'XDG_CONFIG_HOME': str(folder / 'opencode-config-home'), 'OPENCODE_DISABLE_PROJECT_CONFIG': '1'})
        self.assertNotIn('OPENCODE_CONFIG', os.environ)

if __name__ == '__main__':
    unittest.main()
