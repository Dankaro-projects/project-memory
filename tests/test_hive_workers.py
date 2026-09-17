"""Workers of a swarm: the hive server on the host command line, completion without hooks, and focused problems in the hive.

This file checks sections 12.5, 12.6 and 12.9 of .memory/build/rebuild-spec.md. No real host runs. The fake child
processes of tests/test_delegation.py and tests/test_focus.py are extended with HIVE_LOGGER, which logs the moves
of the protocol in the hive before the fake worker returns its report, as a real worker does through hive_log.
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

from memory_module import InvalidRecord, delegation, dumps, hive, hosts, install, reviews
from tests.test_hosts import WORK_SETTINGS
from memory_module.hive import Hive, path_for
from tests import test_delegation, test_focus, test_focus_review

ROOT = Path(__file__).resolve().parents[1]
HIVE_TOOL_NAMES = 'mcp__hive__hive_log,mcp__hive__hive_query,mcp__hive__hive_resume'

# Runs in the fake child before the fake worker of tests/test_delegation.py. The spec names the protocol:
# complete (the default) logs the moves and names both entries, silent logs nothing and names nothing,
# and swapped names the checkpoint as the conclusion and the conclusion as the checkpoint.
HIVE_LOGGER = r'''
import json as _json, pathlib as _pathlib, sys as _sys
_spec = _json.loads(_sys.argv[1])
_binding = _spec.get('hive')
_protocol = _spec.get('protocol', 'complete')
if _binding and _protocol != 'silent':
    _sys.path.insert(0, _spec['root'])
    from memory_module import hive as _hive
    _run, _host, _agent, _swarm = _pathlib.Path(_sys.argv[2]).name, _sys.argv[3], _binding['agent_id'], _binding['swarm_id']
    _basis = [{'kind': 'file', 'value': 'src/app.py:1'}]
    with _hive.Hive(_binding['hive']) as _store:
        def _log(_move, **_fields):
            return _hive.log(_store, _swarm, _agent, move=_move, request_key=_move, fields=_fields)['id']
        _own = {row['move'] for row in _hive.query(_store, _swarm, _agent)['entries'] if row['agent'] == _agent}
        if 'orient' not in _own:
            _log('orient', claim=f'{_agent} works toward the value that the criterion names.', bases=_basis)
        if 'hypothesis' not in _own:
            _log('hypothesis', claim=f'{_agent} expects that one constant decides the value.', detail='Change the constant.')
        _seen = _log('observation', claim=f'{_agent} observed in run {_run} on host {_host} that the source file holds a value.',
                     bases=_basis)
        _conclusion = _log('conclusion', claim=f'{_agent} concludes after run {_run} that its change on host {_host} resolves '
                           'the problem.', confidence='medium', cites=[_seen])
        _checkpoint = _log('checkpoint', done=f'{_agent} wrote its change.', belief='The change meets the criterion.',
                           open_questions='None.', next_step='Return the report.')
    if _spec.get('report') is not None:
        if _protocol == 'complete':
            _spec['report'].update(hive_conclusion_id=_conclusion, hive_checkpoint_id=_checkpoint)
        elif _protocol == 'swapped':
            _spec['report'].update(hive_conclusion_id=_checkpoint, hive_checkpoint_id=_conclusion)
    _sys.argv[1] = _json.dumps(_spec)
'''


def converse(command, messages):
    """Start an MCP server command, send the messages and return the replies."""
    process = subprocess.run(command, input='\n'.join(dumps(message) for message in messages) + '\n', text=True,
                             capture_output=True, timeout=60)
    if process.returncode != 0:
        raise AssertionError(process.stderr)
    return [json.loads(line) for line in process.stdout.splitlines()]


class CommandLineTests(unittest.TestCase):
    """The exact command lines of both hosts for a run of a swarm. No host process starts."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name).resolve()
        self.codex_home = self.root / 'codex-home'
        self.codex_home.mkdir()
        (self.codex_home / 'config.toml').write_text('model = "fixture-model"\n[mcp_servers.global_one]\ncommand = "x"\n')
        self.worktree = self.root / 'project/.memory/worktrees/run1'
        self.worktree.mkdir(parents=True)
        self.folder = self.root / 'run'
        self.folder.mkdir()
        (self.folder / 'schema.json').write_text('{"type":"object"}')
        environment = patch.dict(os.environ, {'CODEX_HOME': str(self.codex_home)})
        environment.start()
        self.addCleanup(environment.stop)
        for name in hosts.ENVIRONMENT.values():
            os.environ.pop(name, None)
        self.binding = {'hive': str(self.root / 'project/.memory/hive.sqlite'), 'db': str(self.root / 'project/.memory/project.sqlite'),
                        'swarm_id': 'swarm_0123456789abcdef', 'agent_id': 'attempt-1', 'role': 'attempt'}
        launch = install.python_args('memory_module.cli')
        self.server_command = launch[0]
        self.server_args = launch[1:] + ['hive-serve', '--hive=' + self.binding['hive'], '--swarm=swarm_0123456789abcdef',
                                         '--agent=attempt-1', '--role=attempt', '--db=' + self.binding['db']]

    def tearDown(self):
        shutil.rmtree(self.temp.name, ignore_errors=True)

    def test_codex_receives_only_the_hive_server_with_hooks_disabled(self):
        with patch.object(hosts.shutil, 'which', return_value=None):
            args = hosts.work_command('codex', self.worktree, self.folder, 'prompt', hive=self.binding)
        expected = ['codex', 'exec', '--ephemeral', '--skip-git-repo-check', '--sandbox', 'workspace-write',
                    '-C', str(self.worktree), '-c', 'approval_policy="never"', '-c', 'features.hooks=false',
                    '-c', 'features.plugins=false', '-c', 'features.apps=false', '-c', 'features.multi_agent=false',
                    '-c', 'memories.use_memories=false', '-c', 'memories.generate_memories=false', '-c', 'web_search="disabled"',
                    '-c', 'mcp_servers.global_one.enabled=false',
                    '-c', 'mcp_servers.hive.command=' + json.dumps(self.server_command),
                    '-c', 'mcp_servers.hive.args=' + json.dumps(self.server_args),
                    '--output-schema', str(self.folder / 'schema.json'), '--output-last-message', str(self.folder / 'answer.json'),
                    '--json', '-']
        self.assertEqual(args, expected)
        # Codex reads each override as TOML, so the values must parse back to the server command.
        overrides = [args[index + 1] for index, value in enumerate(args) if value == '-c']
        servers = [value for value in overrides if value.startswith('mcp_servers.')]
        self.assertEqual([value for value in servers if not value.endswith('.enabled=false')],
                         ['mcp_servers.hive.command=' + json.dumps(self.server_command),
                          'mcp_servers.hive.args=' + json.dumps(self.server_args)])
        parsed = tomllib.loads('\n'.join(value.removeprefix('mcp_servers.hive.') for value in servers if '.hive.' in value))
        self.assertEqual(parsed, {'command': self.server_command, 'args': self.server_args})
        # Without a swarm the command line is the one of section 2.4.
        with patch.object(hosts.shutil, 'which', return_value=None):
            plain = hosts.work_command('codex', self.worktree, self.folder, 'prompt')
        hive_start = expected.index('mcp_servers.hive.command=' + json.dumps(self.server_command)) - 1
        self.assertEqual(plain, expected[:hive_start] + expected[hive_start + 4:])

    def test_claude_receives_only_the_hive_server_and_its_three_tools(self):
        with patch.dict(os.environ, {'PROJECT_MEMORY_CLAUDE_BIN': '/opt/fake/claude'}):
            args = hosts.work_command('claude', self.worktree, self.folder, 'Do the work.', hive=self.binding)
            plain = hosts.work_command('claude', self.worktree, self.folder, 'Do the work.')
        configured = {'mcpServers': {'hive': {'type': 'stdio', 'command': self.server_command, 'args': self.server_args}}}
        expected = ['/opt/fake/claude', '-p', '--restricted', '--no-session-persistence', '--output-format', 'stream-json',
                    '--verbose', '--permission-mode', 'dontAsk', '--setting-sources', '', '--settings', WORK_SETTINGS,
                    '--strict-mcp-config', '--mcp-config', json.dumps(configured, separators=(',', ':')), '--disable-slash-commands',
                    '--tools', 'Read,Glob,Grep,Edit,Write,Bash', '--allowedTools', 'Read,Glob,Grep,Edit,Write,Bash,' + HIVE_TOOL_NAMES,
                    '--json-schema', '{"type":"object"}', '--system-prompt', 'Do the work.']
        self.assertEqual(args, expected)
        self.assertEqual(list(json.loads(args[args.index('--mcp-config') + 1])['mcpServers']), ['hive'])
        self.assertEqual(plain[plain.index('--mcp-config') + 1], '{"mcpServers":{}}')
        self.assertEqual(plain[plain.index('--allowedTools') + 1], 'Read,Glob,Grep,Edit,Write,Bash')

    def test_a_project_path_outside_the_basic_multilingual_plane_stays_valid_toml(self):
        folder = self.root / 'Projekt \U0001F600 \u00e9'
        binding = {**self.binding, 'hive': str(folder / '.memory/hive.sqlite'), 'db': str(folder / '.memory/project.sqlite')}
        with patch.object(hosts.shutil, 'which', return_value=None):
            args = hosts.work_command('codex', self.worktree, self.folder, 'prompt', hive=binding)
        values = [value.removeprefix('mcp_servers.hive.') for value in args if value.startswith('mcp_servers.hive.')]
        parsed = tomllib.loads('\n'.join(values))
        self.assertIn('--hive=' + binding['hive'], parsed['args'])

    def test_a_codex_server_named_hive_is_refused_before_the_run(self):
        (self.codex_home / 'config.toml').write_text('[mcp_servers.hive]\ncommand = "x"\n')
        with self.assertRaises(InvalidRecord) as refused:
            hosts.work_command('codex', self.worktree, self.folder, 'prompt', hive=self.binding)
        self.assertEqual(str(refused.exception), hosts.HIVE_NAME_TAKEN)
        self.assertEqual(hosts.work_command('codex', self.worktree, self.folder, 'prompt').count('mcp_servers.hive.enabled=false'), 1)


class ServerCommandTests(unittest.TestCase):
    """The server in the command line of each host starts and offers exactly the three hive tools."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name).resolve()
        (self.root / 'worktree/src').mkdir(parents=True)
        (self.root / 'worktree/src/app.py').write_text('VALUE = 1\n')
        self.folder = self.root / 'run'
        self.folder.mkdir()
        (self.folder / 'schema.json').write_text('{"type":"object"}')
        self.path = self.root / '.memory/hive.sqlite'
        with Hive(self.path) as store:
            self.swarm = hive.open_swarm(store, title='Value', purpose='Set the value.', kind='workflow', request_key='open')['id']
            hive.join(store, self.swarm, agent_id='attempt-1', role='attempt', host='codex', run_id='run_1',
                      worktree=self.root / 'worktree')
        self.binding = {'hive': str(self.path), 'db': str(self.root / '.memory/project.sqlite'), 'swarm_id': self.swarm,
                        'agent_id': 'attempt-1', 'role': 'attempt'}
        environment = patch.dict(os.environ, {'CODEX_HOME': str(self.root / 'codex-home')})
        environment.start()
        self.addCleanup(environment.stop)

    def tearDown(self):
        shutil.rmtree(self.temp.name, ignore_errors=True)

    def messages(self, key):
        return [{'jsonrpc': '2.0', 'id': 0, 'method': 'initialize', 'params': {'protocolVersion': '2025-11-25'}},
                {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'},
                {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {'name': 'hive_log', 'arguments': {
                    'move': 'orient', 'request_key': key,
                    'fields': {'claim': 'The goal is a value of two.', 'bases': [{'kind': 'file', 'value': 'src/app.py:1'}]}}}}]

    def test_an_agent_name_that_begins_with_a_dash_stays_the_value_of_its_option(self):
        with Hive(self.path) as store:
            hive.join(store, self.swarm, agent_id='-w1', role='--role=x', host='codex', run_id='run_2', worktree=self.root / 'worktree')
        server = hosts.hive_server({**self.binding, 'agent_id': '-w1', 'role': '--role=x'})
        replies = converse([server['command'], *server['args']], self.messages('orient-dash'))
        self.assertFalse(replies[2]['result']['isError'], replies[2])
        with Hive(self.path, read_only=True) as store:
            self.assertEqual(store.db.execute("SELECT agent_id FROM entries WHERE agent_id='-w1'").fetchone()[0], '-w1')

    def test_the_server_of_each_command_line_logs_as_the_bound_agent(self):
        codex = hosts.work_command('codex', self.root / 'worktree', self.folder, 'prompt', hive=self.binding)
        values = [value.removeprefix('mcp_servers.hive.') for value in codex if value.startswith('mcp_servers.hive.')]
        server = tomllib.loads('\n'.join(values))
        claude = hosts.work_command('claude', self.root / 'worktree', self.folder, 'prompt', hive=self.binding)
        entry = json.loads(claude[claude.index('--mcp-config') + 1])['mcpServers']['hive']
        self.assertEqual((entry['command'], entry['args']), (server['command'], server['args']))
        replies = converse([server['command'], *server['args']], self.messages('orient'))
        self.assertEqual(replies[0]['result']['serverInfo']['name'], 'project-memory-hive')
        self.assertEqual([tool['name'] for tool in replies[1]['result']['tools']], list(hosts.HIVE_TOOLS))
        self.assertEqual(['mcp__hive__' + name for name in hosts.HIVE_TOOLS], HIVE_TOOL_NAMES.split(','))
        self.assertFalse(replies[2]['result']['isError'], replies[2])
        with Hive(self.path, read_only=True) as store:
            rows = store.db.execute('SELECT agent_id,move FROM entries').fetchall()
        self.assertEqual([tuple(row) for row in rows], [('attempt-1', 'orient')])


class WorkerFixture:
    """The fixture of tests/test_delegation.py with a fake worker that follows the hive protocol."""

    setUp = test_delegation.DelegationTests.setUp
    tearDown = test_delegation.DelegationTests.tearDown
    git = test_delegation.DelegationTests.git
    plan = test_delegation.DelegationTests.plan
    fake_reviewer = test_delegation.DelegationTests.fake_reviewer
    run_now = test_delegation.DelegationTests.run_now

    def fake_worker(self, host, worktree, folder, prompt, hive=None):
        self.calls = getattr(self, 'calls', [])
        self.calls.append({'host': host, 'hive': hive, 'prompt': prompt, 'folder': Path(folder)})
        spec = {**self.workers.get(host, {'write': {'src/app.py': 'VALUE = 2\n'}, 'report': test_delegation.work_report()}),
                'hive': hive, 'root': str(ROOT)}
        return [sys.executable, '-c', HIVE_LOGGER + test_delegation.WORKER, json.dumps(spec), str(folder), host]

    def open_swarm(self, key='open'):
        with Hive(path_for(self.m)) as store:
            return hive.open_swarm(store, title='Value', purpose='Set the value to two.', kind='workflow', request_key=key,
                                   episode_id=self.episode, project=self.project)['id']

    def delegate_in(self, swarm, key='delegate', agent='worker-1'):
        run = delegation.request_work(self.m, self.episode, request_key=key, host='codex',
                                      hive={'swarm_id': swarm, 'agent_id': agent, 'role': 'worker'})
        delegation.launch(self.m, run)
        return reviews.read(self.m, run['id'])


class CompletionTests(WorkerFixture, unittest.TestCase):
    def test_a_run_that_names_its_own_conclusion_and_checkpoint_completes(self):
        swarm = self.open_swarm()
        run = self.delegate_in(swarm)
        self.assertEqual(run['state'], 'completed', run['error'])
        self.assertEqual(run['metrics']['hive_protocol'], {'complete': True, 'problems': []})
        call = self.calls[0]
        self.assertEqual(call['hive'], {'hive': str(path_for(self.m)), 'db': str(self.m.path), 'swarm_id': swarm,
                                        'agent_id': 'worker-1', 'role': 'worker'})
        schema = json.loads((call['folder'] / 'schema.json').read_text())
        self.assertIn('hive_conclusion_id', schema['required'])
        self.assertIn('hive_checkpoint_id', schema['required'])
        prompt = call['prompt']
        self.assertIn(hive.WORKER_PROTOCOL + '\n', prompt)
        self.assertLessEqual(len(hive.WORKER_PROTOCOL), hive.PROTOCOL_LIMIT)
        self.assertIn('Hive context of agent worker-1 in swarm ' + swarm + ', blind phase.', prompt)
        self.assertIn('Name your own hive conclusion entry in hive_conclusion_id', prompt)
        with Hive(path_for(self.m), read_only=True) as store:
            agent = hive.member(store, swarm, 'worker-1')
            composed = store.db.execute('SELECT run_id,characters FROM compositions').fetchall()
        self.assertEqual((agent['host'], agent['run_id'], agent['worktree']), ('codex', run['id'], str(self.project / run['workspace'])))
        self.assertEqual([tuple(row) for row in composed], [(run['id'], run['metrics']['hive']['composed_characters'])])
        self.assertEqual((run['report']['hive_conclusion_id'], run['report']['hive_checkpoint_id']), ('e4', 'e5'))
        review = next(item for item in delegation.runs(self.m, episode_id=self.episode)['runs'] if item['role'] == 'work_review')
        self.assertEqual(review['state'], 'pass')

    def test_a_run_without_its_hive_entries_ends_protocol_incomplete_and_keeps_its_diff(self):
        swarm = self.open_swarm()
        self.workers['codex'] = {'write': {'src/app.py': 'VALUE = 2\n'}, 'report': test_delegation.work_report(), 'protocol': 'silent'}
        run = self.delegate_in(swarm)
        self.assertEqual(run['state'], 'protocol_incomplete')
        self.assertEqual(run['error'], 'The worker did not complete the hive protocol. The work report does not name '
                                       'hive_conclusion_id and hive_checkpoint_id. The changes are kept and can be reviewed, '
                                       'but a focused problem does not select this run.')
        self.assertEqual(run['metrics']['changed_files'], ['src/app.py'])
        self.assertIn('+VALUE = 2', self.m.read(run['metrics']['diff_source'], detail=True)['body'])
        self.assertEqual(self.git('rev-parse', run['branch']), run['metrics']['commit'])
        review = next(item for item in delegation.runs(self.m, episode_id=self.episode)['runs'] if item['role'] == 'work_review')
        self.assertEqual((review['state'], review['parent_run']), ('pass', run['id']))

    def test_entries_of_the_wrong_move_leave_the_protocol_incomplete(self):
        swarm = self.open_swarm()
        self.workers['codex'] = {'write': {'src/app.py': 'VALUE = 2\n'}, 'report': test_delegation.work_report(), 'protocol': 'swapped'}
        run = self.delegate_in(swarm)
        self.assertEqual(run['state'], 'protocol_incomplete')
        self.assertEqual(run['metrics']['hive_protocol']['problems'],
                         ['The hive_conclusion_id e5 is a checkpoint, not a conclusion.',
                          'The hive_checkpoint_id e4 is a conclusion, not a checkpoint.'])

    def test_a_run_without_a_swarm_keeps_the_plain_command_and_schema(self):
        run = delegation.request_work(self.m, self.episode, request_key='plain', host='codex')
        delegation.launch(self.m, run)
        run = reviews.read(self.m, run['id'])
        self.assertEqual(run['state'], 'completed', run['error'])
        self.assertIsNone(self.calls[0]['hive'])
        self.assertNotIn('hive_conclusion_id', json.loads((self.calls[0]['folder'] / 'schema.json').read_text())['properties'])
        self.assertNotIn('Hive protocol', self.calls[0]['prompt'])
        self.assertNotIn('hive', run['metrics'])

    def test_a_binding_needs_an_open_swarm_and_its_fields(self):
        with self.assertRaises(InvalidRecord) as missing:
            delegation.request_work(self.m, self.episode, request_key='missing', hive={'swarm_id': 'swarm_none', 'agent_id': 'a',
                                                                                      'role': 'worker'})
        self.assertEqual(str(missing.exception), 'The swarm swarm_none was not found in the hive of this project.')
        swarm = self.open_swarm()
        with self.assertRaises(InvalidRecord) as shape:
            delegation.request_work(self.m, self.episode, request_key='shape', hive={'swarm_id': swarm, 'agent_id': 'a'})
        self.assertEqual(str(shape.exception), delegation.HIVE_BINDING_REFUSED)
        with Hive(path_for(self.m)) as store:
            hive.close(store, swarm, summary='Closed early.', request_key='close')
        with self.assertRaises(InvalidRecord) as closed:
            delegation.request_work(self.m, self.episode, request_key='closed', hive={'swarm_id': swarm, 'agent_id': 'a',
                                                                                     'role': 'worker'})
        self.assertEqual(str(closed.exception), f'The swarm {swarm} is closed, so a new run cannot join it. Open a new swarm for new work.')
        self.assertEqual(self.m.db.execute("SELECT count(*) FROM review_runs WHERE role='work'").fetchone()[0], 0)


class HiveFocusWorker:
    """The focus fixture with a fake worker that receives the hive binding and follows the protocol."""

    def fake_worker(self, host, worktree, folder, prompt, hive=None):
        given = json.loads((Path(folder) / 'input.json').read_text())
        attempt = (given.get('focus') or {}).get('attempt')
        self.prompts.append({'host': host, 'attempt': attempt, 'prompt': prompt, 'hive': hive})
        if attempt in self.before:
            self.before[attempt]()
        spec = self.attempts.get((attempt, host)) or self.attempts.get(attempt) or self.workers.get(host) \
            or test_focus.change('VALUE = 2\n')
        spec = {**spec, 'hive': hive, 'root': str(ROOT)}
        return [sys.executable, '-c', HIVE_LOGGER + test_focus.FOCUS_WORKER, json.dumps(spec), str(folder), host]


class FocusHiveTests(HiveFocusWorker, test_focus.FocusFixture):
    def swarm_of(self, episode):
        started = self.receipts('FocusStarted')[-1]
        self.assertEqual(started['episode_id'], episode)
        return started['swarm_id']

    def test_a_relay_hands_over_through_the_hive_and_records_check_results_as_command_bases(self):
        episode = self.prepared()
        self.attempts = {1: test_focus.change('VALUE = 3\n'), 2: test_focus.change('VALUE = 2\n')}
        self.start(episode)
        view = self.focus.view(self.m, episode)
        self.assertEqual(view['state'], 'merged')
        first, second = view['attempts']
        swarm = self.swarm_of(episode)
        with Hive(path_for(self.m), read_only=True) as store:
            listed = hive.swarms(store)['swarms'][0]
            agents = {row['agent_id']: dict(row) for row in store.db.execute('SELECT * FROM agents WHERE swarm_id=?', (swarm,))}
            entries = {row['id']: dict(row) for row in store.db.execute('SELECT * FROM entries WHERE swarm_id=?', (swarm,))}
            bases = [dict(row) for row in store.db.execute('SELECT * FROM bases')]
            links = [tuple(row) for row in store.db.execute('SELECT * FROM links')]
        self.assertEqual((listed['id'], listed['kind'], listed['episode_id'], listed['state']), (swarm, 'focus', episode, 'closed'))
        self.assertEqual({name: (row['host'], row['role'], row['run_id']) for name, row in agents.items()},
                         {'focus-orchestrator': ('session', 'supervisor', None),
                          'attempt-1': (self.work_host, 'attempt', first['run_id']),
                          'attempt-2': (self.other_host, 'attempt', second['run_id'])})

        # The hypothesis of each attempt is its hive hypothesis, and the main memory result names that entry.
        records = [json.loads(row[0]) for row in self.m.db.execute(
            "SELECT payload FROM events WHERE episode_id=? AND kind='hypothesis' AND supersedes IS NOT NULL ORDER BY seq", (episode,))]
        opened = [reviews.read(self.m, item['run_id'])['metrics']['hive']['opening'] for item in (first, second)]
        self.assertEqual([record['hive_entry_id'] for record in records], [item['hypothesis_id'] for item in opened])
        for item, statement in zip(opened, (test_focus.H1, test_focus.H2)):
            self.assertEqual((entries[item['hypothesis_id']]['move'], entries[item['hypothesis_id']]['claim']),
                             ('hypothesis', statement['statement']))
            self.assertEqual(entries[item['orient_id']]['move'], 'orient')

        # Each check result is a command basis, verified with its exit code, that challenges or supports the hypothesis.
        checks = {item['run_id']: item for item in self.receipts('FocusHiveRecorded')}
        challenge = entries[checks[first['run_id']]['entry_id']]
        support = entries[checks[second['run_id']]['entry_id']]
        self.assertEqual((challenge['agent_id'], challenge['move'], challenge['claim']),
                         ('focus-orchestrator', 'challenge', f'The check failed for attempt-1, so its hypothesis '
                                                             f'{opened[0]["hypothesis_id"]} is ruled out.'))
        self.assertEqual((support['move'], json.loads(support['data'])['target']), ('support', opened[1]['hypothesis_id']))
        command = {row['entry_id']: (row['verified'], row['exit_code']) for row in bases if row['kind'] == 'command'}
        self.assertEqual(command, {challenge['id']: (1, 1), support['id']: (1, 0)})
        self.assertIn((challenge['id'], opened[0]['hypothesis_id'], 'challenges'), links)

        # The relay hands over the checkpoint, the check result and the conclusion of attempt 1 through the hive.
        prompts = {item['attempt']: item['prompt'] for item in self.prompts}
        self.assertNotIn('Handover from', prompts[1])
        checkpoint = next(entry for entry in entries.values() if entry['agent_id'] == 'attempt-1' and entry['move'] == 'checkpoint')
        conclusion = next(entry for entry in entries.values() if entry['agent_id'] == 'attempt-1' and entry['move'] == 'conclusion')
        self.assertIn(f'Handover from attempt-1: checkpoint {checkpoint["id"]}, done: attempt-1 wrote its change. Belief: The '
                      f'change meets the criterion.', prompts[2])
        self.assertIn(f'Challenge {challenge["id"]} by focus-orchestrator: {challenge["claim"]}', prompts[2])
        self.assertIn('Latest conclusions of other agents:\n' + conclusion['id'] + ' attempt-1 conclusion (medium confidence): '
                      + conclusion['claim'], prompts[2])
        self.assertIn(delegation.HIVE_OPENED.format(**opened[1]), prompts[2])
        self.assertEqual(test_focus.section(prompts[2])[0], 'Focused problem: ' + test_focus.PROBLEM)
        handover = reviews.read(self.m, second['run_id'])['metrics']['hive']['handover_characters']
        self.assertTrue(0 < handover <= delegation.HANDOVER_LIMIT)

        # Closing distils the swarm: the selected attempt passed cross review, so its conclusion is confirmed.
        with Hive(path_for(self.m), read_only=True) as store:
            distillation = hive._swarm_row(store.db.execute('SELECT * FROM swarms WHERE id=?', (swarm,)).fetchone())['distillation']
        confirmed = next(entry['id'] for entry in entries.values() if entry['agent_id'] == 'attempt-2' and entry['move'] == 'conclusion')
        self.assertEqual(list(distillation['confirmed']), [confirmed])
        self.assertEqual(distillation['composed']['prompts'], 2)
        self.assertTrue(self.m.db.execute('SELECT 1 FROM events WHERE request_key=?', ('hive-summary:' + swarm,)).fetchone())

    def test_an_attempt_that_passes_its_check_without_the_protocol_is_not_selected_or_handed_over(self):
        episode = self.prepared()
        self.attempts = {1: {**test_focus.change('VALUE = 2\n'), 'protocol': 'silent'}, 2: test_focus.change('VALUE = 2\n')}
        self.start(episode)
        view = self.focus.view(self.m, episode)
        first, second = view['attempts']
        self.assertEqual((first['run_state'], first['check_passed'], first['exit_code'], first['selected']),
                         ('protocol_incomplete', False, None, False))
        self.assertEqual((second['run_state'], second['check_passed'], second['selected']), ('completed', True, True))
        self.assertEqual(view['state'], 'merged')
        self.assertTrue(view['hypotheses'][0]['evidence_summary'].startswith('The worker did not complete the hive protocol.'))
        prompts = {item['attempt']: item['prompt'] for item in self.prompts}
        self.assertNotIn('Handover from', prompts[2])
        with Hive(path_for(self.m), read_only=True) as store:
            moves = [row[0] for row in store.db.execute("SELECT move FROM entries WHERE agent_id='attempt-1' ORDER BY seq")]
        self.assertEqual(moves, ['orient', 'hypothesis'])

    def test_a_start_that_cannot_request_its_attempts_closes_its_empty_swarm(self):
        episode = self.prepared()
        (self.project / 'src/app.py').write_text('VALUE = 9\n')
        with self.assertRaises(InvalidRecord):
            self.start(episode)
        self.assertEqual(self.receipts('FocusStarted'), [])
        with Hive(path_for(self.m), read_only=True) as store:
            states = [row[0] for row in store.db.execute("SELECT state FROM swarms WHERE kind='focus'")]
            agents = [row[0] for row in store.db.execute('SELECT agent_id FROM agents')]
        self.assertEqual((states, agents), (['closed'], ['focus-orchestrator']))


# Section 11 still holds when every attempt works in the hive: each test of tests/test_focus.py and tests/test_focus_review.py runs
# again here with a worker that follows the protocol. The classes are built by name, so the loader does not
# collect the original classes a second time from this module.
REPLAYED = {test_focus: ('EligibilityTests', 'CheckTests', 'HypothesisTests', 'RelayTests', 'ParallelAndRankingTests', 'LearningTests'),
            test_focus_review: ('MergeFailureTests', 'CancelledAttemptTests', 'CheckFileTests', 'CheckIsolationTests', 'PanelTests',
                                'KeyAndTextTests')}
for _module, _names in REPLAYED.items():
    for _name in _names:
        globals()['Hive' + _name] = type('Hive' + _name, (HiveFocusWorker, getattr(_module, _name)), {'__module__': __name__})


if __name__ == '__main__':
    unittest.main()
