"""End to end scenarios for the three project kinds, from kickoff to delivery.

Each scenario scaffolds a real project from its template, connects the clients,
drives the assistant steps through a real MCP server process over standard input
and output, and records the human steps through the control panel actions. Agent
hosts are fake programs that never contact a model: they read the report schema
that Project Memory prepared for the run and answer with a valid report, or they
report a usage limit. Git is real, so delegated work runs in its own worktree and
a merge changes the project files.

After each stage the scenarios read the api.py responses that the control panel
shows, so a change that breaks the panel breaks these tests.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from memory_module import Memory, api, graph, guards, reviews, templates
from memory_module.workspace import action

ROOT = Path(__file__).resolve().parents[1]
BROWSER = Path(__file__).resolve().parent / 'browser'
if str(BROWSER) not in sys.path:
    sys.path.insert(0, str(BROWSER))
import fixture  # noqa: E402

LAUNCHER = [sys.executable, '-m', 'memory_module.cli']
RUN_SECONDS = 120
# Write operations that record who wrote the record. The other operations do not accept an author.
ACTOR_OPERATIONS = ('plan', 'sprint', 'record', 'progress', 'component', 'answer_kickoff', 'approve_requirements')

WORKFLOW = json.dumps({
    'name': 'Lead intake', 'id': 'lead-intake', 'active': False,
    'nodes': [
        {'id': '1', 'name': 'New lead webhook', 'type': 'n8n-nodes-base.webhook', 'typeVersion': 1, 'position': [0, 0],
         'parameters': {'path': 'lead-intake', 'httpMethod': 'POST'}},
        {'id': '2', 'name': 'Notify sales', 'type': 'n8n-nodes-base.slack', 'typeVersion': 1, 'position': [240, 0],
         'parameters': {'channel': '#sales'},
         'credentials': {'slackApi': {'id': 'cred-slack', 'name': 'Sales Slack'}}}],
    'connections': {'New lead webhook': {'main': [[{'node': 'Notify sales', 'type': 'main', 'index': 0}]]}},
    'settings': {'executionOrder': 'v1'}}, indent=2) + '\n'


class ToolError(Exception):
    """An error result returned by an MCP tool call, with the details the assistant receives."""

    def __init__(self, value):
        super().__init__(value.get('message', 'The tool call failed.'))
        self.value = value


class Server:
    """A real MCP server process for one project database, driven over standard input and output."""

    def __init__(self, database):
        self.process = subprocess.Popen(LAUNCHER + ['serve', '--db', str(database)], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                        env={**os.environ, 'PYTHONPATH': str(ROOT)}, cwd=str(ROOT))
        self.next_id = 0
        self.send('initialize', {'protocolVersion': '2025-11-25', 'capabilities': {},
                                 'clientInfo': {'name': 'project-memory-scenarios', 'version': '1'}})

    def send(self, method, params):
        self.next_id += 1
        request = {'jsonrpc': '2.0', 'id': self.next_id, 'method': method, 'params': params}
        self.process.stdin.write(json.dumps(request) + '\n')
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            raise AssertionError('The MCP server stopped. ' + self.process.stderr.read())
        reply = json.loads(line)
        if 'error' in reply:
            raise AssertionError(reply['error']['message'])
        return reply['result']

    def call(self, name, arguments):
        result = self.send('tools/call', {'name': name, 'arguments': arguments})
        value = json.loads(result['content'][0]['text'])
        if result.get('isError'):
            raise ToolError(value)
        return value

    def close(self):
        if self.process.poll() is None:
            self.process.stdin.close()
            self.process.wait(timeout=30)
        for stream in (self.process.stdout, self.process.stderr):
            stream.close()


class Scenario(unittest.TestCase):
    """One scaffolded project with fake agent hosts, a live MCP server and the panel read functions."""

    template = 'product'
    project_name = 'Scenario project'
    clients = ('codex', 'claude')

    def host_plan(self):
        """What each fake host call does. Every scenario states its own plan."""
        return {}

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.project = self.base / self.template
        environment = patch.dict(os.environ, {**fixture.fake_hosts(self.base / 'hosts', self.host_plan()),
                                              'PYTHONPATH': str(ROOT)})
        environment.start()
        self.addCleanup(environment.stop)
        self.scaffold = templates.scaffold(self.project, self.template, name=self.project_name,
                                           clients=list(self.clients), _launcher=LAUNCHER)
        self.database = self.scaffold['database']
        self.phases = {phase['key']: phase['episode_id'] for phase in self.scaffold['phases']}
        self.m = Memory(self.database)
        self.addCleanup(self.m.close)
        self.server = Server(self.database)
        self.addCleanup(self.server.close)

    # Helpers.

    def git(self, *args):
        result = subprocess.run(['git', '-C', str(self.project), *args], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def commit(self, message, *paths):
        self.git('add', '--', *paths)
        self.git('-c', 'user.name=Scenario', '-c', 'user.email=scenario@localhost', '-c', 'commit.gpgsign=false',
                 'commit', '-q', '--no-verify', '-m', message, '--', *paths)

    def write_file(self, relative, text):
        path = self.project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def read(self, name, **params):
        """One control panel response, read exactly as the server reads it."""
        return api.ENDPOINTS[name](self.m, params)

    def assistant(self, operation, key, data, session_id=None):
        """One assistant write through the MCP server. Operations that record an author name the assistant."""
        if operation in ACTOR_OPERATIONS:
            data = {'actor': 'assistant', **data}
        arguments = {'operation': operation, 'request_key': key, 'data': data}
        if session_id:
            arguments['session_id'] = session_id
        return self.server.call('memory_write', arguments)

    def view(self, name, **arguments):
        """One assistant read through the MCP server."""
        return self.server.call('memory_get', {'view': name, **arguments})

    def user(self, operation, data, key):
        """One human action through the control panel."""
        return action(self.m, operation, data, key)

    def version(self, episode_id):
        return self.m.episode(episode_id)['version']

    def source(self, key, title, summary, body, subject='general'):
        return self.m.source(key, title, summary, body, 'user', subject=subject)['id']

    def wait_for(self, run_id, seconds=RUN_SECONDS):
        end = time.time() + seconds
        while time.time() < end:
            run = reviews.read(self.m, run_id)
            if run['state'] not in reviews.ACTIVE:
                return run
            time.sleep(0.2)
        raise AssertionError('The agent run did not finish within ' + str(seconds) + ' seconds.')

    def child_run(self, parent_id, role):
        """The run that a finished run started, once it exists."""
        end = time.time() + RUN_SECONDS
        while time.time() < end:
            for item in self.m.db.execute('SELECT id FROM review_runs WHERE parent_run=? AND role=? ORDER BY rowid DESC',
                                          (parent_id, role)).fetchall():
                return self.wait_for(item[0])
            time.sleep(0.2)
        raise AssertionError('No ' + role + ' run started for ' + parent_id + '.')

    def delegate(self, episode_id, key, host=None):
        data = {'episode_id': episode_id}
        if host:
            data['host'] = host
        run = self.user('delegate', data, key)
        return self.wait_for(run['id'])

    def hook(self, event, host='claude'):
        """Run the installed hook command in its own process, as the client does."""
        return subprocess.run(LAUNCHER + ['hook', '--db', str(self.database), '--host', host],
                              input=json.dumps(event), capture_output=True, text=True,
                              env={**os.environ, 'PYTHONPATH': str(ROOT)}, cwd=str(ROOT))

    def attention(self, kind):
        return [item for item in self.read('now')['attention'] if item['type'] == kind]

    def hierarchy_nodes(self, response):
        found = {}

        def walk(node):
            found[node['id']] = node
            for child in node['children']:
                walk(child)
        for root in response['roots']:
            walk(root)
        return found


class ProductScenario(Scenario):
    """A software product from kickoff to a delivered story, with a guard, a scope block and delegated work."""

    template = 'product'
    project_name = 'Client file importer'

    def host_plan(self):
        return {'codex:work': [{'unavailable': 'You have hit your usage limit. Try again in 20 minutes.'}],
                'claude:work': [{'write': {'src/app/parser.py': 'VALUE = 2\n'}}]}

    def test_a_product_runs_from_kickoff_to_a_delivered_story(self):
        # Kickoff. The template asks its questions before any work is planned.
        kickoff = self.view('kickoff')
        self.assertEqual(kickoff['template'], 'product')
        self.assertEqual(kickoff['next_step']['action'], 'answer_kickoff')
        self.assertFalse(any(question['answered'] for question in kickoff['questions']))
        self.assertEqual(self.read('now')['kickoff'], {'template': 'product', 'baseline': 'not_established',
                                                       'read_with': 'api/kickoff'})
        self.assistant('answer_kickoff', 'kickoff-1',
                       {'question_ids': [question['id'] for question in kickoff['questions']],
                        'text': 'The operations team imports client files, and no character may be lost. '
                                'Success means that every sample file imports. The first release excludes exports.'})
        answered = self.view('kickoff')
        self.assertTrue(all(question['answered'] for question in answered['questions']))
        self.assertEqual(answered['next_step']['action'], 'fill_documents')

        # Existing research is captured as evidence, and missing research becomes its own work item.
        self.write_file('docs/research.md', '# Research\n\nThe operations team counted 4,200 client files in 2025.\n')
        captured = self.assistant('document', 'document-1', {'path': str(self.project / 'docs/research.md')})
        self.assertTrue(captured['id'].startswith('source_'))
        documents = self.read('records', view='documents')
        self.assertIn('docs/research.md', json.dumps(documents['records']))
        request = self.assistant('source', 'source-1', {
            'source_key': 'user:import-request', 'title': 'User request', 'summary': 'The user describes the import work.',
            'body': 'Import every client file without losing characters.', 'origin': 'user', 'subject': 'code'})
        evidence = [{'source_id': request['id'], 'reason': 'The user asks for this work.'}]
        research = self.assistant('plan', 'plan-research', {
            'title': 'Measure the encodings of the client files', 'objective': 'Measure which encodings client files use.',
            'criterion': 'The share of each encoding in the sample is recorded.', 'subject': 'research',
            'evidence': evidence,
            'payload': {'state': 'ready', 'next_action': 'Measure the sample files.', 'item_type': 'research',
                        'scope': 'Measure the sample only.', 'autonomy': 'suggest',
                        'reason': 'A decision on decoding depends on this answer.',
                        'parent_id': self.phases['research']}})
        self.assertEqual(self.view('kickoff')['research'][0]['episode_id'], research['episode_id'])

        # Only the user approves the requirements baseline.
        approved = self.user('requirements', {
            'requirements': ['Every client file imports without lost characters.',
                             'A file in an unsupported encoding is rejected with a clear message.'],
            'reason': 'The user approved the requirements after the kickoff answers.',
            'expected_version': self.m.direction()['version']}, 'requirements-1')
        self.assertEqual(approved['version'], 1)
        self.assertEqual(self.read('requirements')['current']['version'], 1)
        self.assertEqual(self.read('now')['kickoff']['baseline'], 'current')

        # The architecture is authored, and an assistant proposal stays proposed until the user confirms it.
        proposed = self.assistant('component', 'component-1', {
            'title': 'Client file importer', 'kind': 'component', 'description': 'Reads client files and converts them to records.',
            'status': 'proposed', 'path': 'src/app'})
        self.assertEqual((proposed['status'], proposed['actor']), ('proposed', 'assistant'))
        with self.assertRaises(ToolError) as caught:
            self.assistant('component', 'component-2', {
                'title': 'Export service', 'kind': 'service', 'description': 'Publishes the export.', 'status': 'confirmed'})
        self.assertIn('Only the user can confirm a component', str(caught.exception))
        confirmed = self.user('component', {'component_id': proposed['id'], 'title': 'Client file importer',
                                            'kind': 'component', 'description': 'Reads client files and converts them to records.',
                                            'status': 'confirmed', 'path': 'src/app'}, 'component-confirm')
        self.assertEqual(confirmed['status'], 'confirmed')
        self.assertEqual([item['status'] for item in self.read('components')['components']], ['confirmed'])

        # Stories carry acceptance criteria and belong to an epic.
        epic = self.assistant('plan', 'plan-epic', {
            'title': 'Import client files', 'objective': 'Import client files without losing characters.',
            'criterion': 'Every story of this epic is done.', 'subject': 'code', 'evidence': evidence,
            'payload': {'state': 'backlog', 'next_action': 'Plan the stories.', 'item_type': 'epic',
                        'scope': 'Import work only.', 'autonomy': 'suggest', 'reason': 'The user asks for the import.',
                        'parent_id': self.phases['stories']}})
        story = self.user('plan', {
            'title': 'Parse client files', 'objective': 'Import every client file without lost characters.',
            'criterion': 'Every sample client file imports without lost characters.', 'subject': 'code',
            'payload': {'state': 'ready', 'next_action': 'Change the decoder.', 'item_type': 'story',
                        'scope': 'Change the importer only.', 'autonomy': 'act', 'parent_id': epic['episode_id'],
                        'reason': 'The user requests the import work.',
                        'acceptance': ['Every sample client file imports without lost characters.',
                                       'A file in an unsupported encoding is rejected with a clear message.'],
                        'paths': ['src/app/**']}}, 'plan-story')
        episode = story['episode_id']
        self.user('link', {'from_id': episode, 'to_id': confirmed['id'], 'type': 'implements',
                           'reason': 'The story changes the importer.'}, 'link-story')
        hierarchy = self.hierarchy_nodes(self.read('plan'))
        self.assertEqual(hierarchy[episode]['parent_id'], epic['episode_id'])
        self.assertEqual(hierarchy[episode]['acceptance_total'], 2)
        self.assertEqual(hierarchy[self.phases['stories']]['rollup']['ready'], 1)

        # The first attempt fails, and the failure names its type.
        first = self.assistant('record', 'decision-1', {
            'episode_id': episode, 'kind': 'decision', 'expected_version': self.version(episode), 'evidence': evidence,
            'payload': {'decision': 'Decode every client file as strict UTF-8.',
                        'why': 'Strict decoding exposes invalid input early.', 'expected': 'Every sample file imports.',
                        'reconsider_when': 'A supported sample file fails to import.',
                        'uncertainty': 'The files tagged as Latin-1 have not been checked.',
                        'alternatives': ['Keep a separate path for tagged Latin-1 files.']}}, session_id='session-a')
        self.assistant('record', 'action-1', {
            'episode_id': episode, 'kind': 'action', 'expected_version': self.version(episode),
            'decision_id': first['id'], 'payload': {'action': 'Decode the sample files strictly.'}})
        failed = self.assistant('record', 'outcome-1', {
            'episode_id': episode, 'kind': 'outcome', 'expected_version': self.version(episode),
            'decision_id': first['id'], 'evidence': evidence,
            'payload': {'observed': 'The sample file tagged as Latin-1 failed to import.', 'assessment': 'bad',
                        'assessment_reason': 'A supported sample file did not import.', 'severity': 'major',
                        'attribution': 'The strict decoding caused the failure.', 'completion': 'partial',
                        'failure_type': 'lost_text'}})
        self.assertEqual([item['id'] for item in self.attention('failure_without_lesson')], [failed['id']])

        # The lesson is proposed by the assistant and accepted by the user with its triggers.
        learning = self.assistant('start', 'start-lessons', {
            'title': 'Lessons of the import work', 'objective': 'Collect the lessons of this project.',
            'task_type': 'learning', 'criterion': 'Each lesson is accepted or rejected by the user.', 'subject': 'code'})
        lesson = self.assistant('record', 'lesson-1', {
            'episode_id': learning['id'], 'kind': 'lesson', 'expected_version': self.version(learning['id']),
            'evidence': evidence, 'links': [{'event_id': failed['id'], 'reason': 'The failed outcome teaches this lesson.'}],
            'payload': {'when': 'Client text is decoded.', 'do': 'Run the tagged sample before changing the decoder.',
                        'because': 'The tagged sample failed after a strict decoding change.',
                        'exceptions': 'Files without an encoding tag.', 'pattern_type': 'recovery'}})
        pending = self.read('learning')
        self.assertEqual([item['id'] for item in pending['proposed_lessons']['lessons']], [lesson['id']])
        self.assertEqual(pending['guards'], [])
        self.assertEqual(self.attention('failure_without_lesson'), [])
        self.assertEqual([item['count'] for item in self.attention('lessons_to_accept')], [1])
        self.user('lesson_review', {'lesson_id': lesson['id'], 'expected_version': self.version(learning['id']),
                                    'status': 'accepted', 'reason': 'The user accepts the lesson with its triggers.',
                                    'paths': ['src/app/**'], 'failure_type': 'lost_text'}, 'accept-lesson')
        guard = self.read('learning')['guards'][0]
        self.assertEqual((guard['lesson_id'], guard['paths'], guard['failure_type'], guard['recurrences']),
                         (lesson['id'], ['src/app/**'], 'lost_text', 0))

        # A decision in the guarded paths must acknowledge the guard.
        revised = {'episode_id': episode, 'kind': 'decision', 'expected_version': self.version(episode),
                   'evidence': evidence, 'supersedes': first['id'],
                   'payload': {'decision': 'Decode strict UTF-8 and keep the tagged Latin-1 path.',
                               'why': 'The tagged files must keep their characters.',
                               'expected': 'Both sample files import.', 'reconsider_when': 'A sample file fails to import.',
                               'uncertainty': 'Files without a tag are still untested.',
                               'alternatives': ['Reject every tagged file.']}}
        with self.assertRaises(ToolError) as caught:
            self.assistant('record', 'decision-2', revised, session_id='session-a')
        details = caught.exception.value
        self.assertEqual(details['next_step']['action'], 'acknowledge_lessons')
        self.assertEqual([item['lesson_id'] for item in details['matched_lessons']], [lesson['id']])
        revised['payload'] = {**revised['payload'], 'lessons_considered': [
            {'lesson_id': lesson['id'], 'applies': 'yes', 'reason': 'The tagged sample was run before the change.'}]}
        second = self.assistant('record', 'decision-3', revised, session_id='session-a')
        self.assertFalse(second['duplicate'])

        # An edit outside the recorded paths is blocked by the hook, and the panel shows it.
        blocked = self.hook({'hook_event_name': 'PreToolUse', 'session_id': 'session-a', 'tool_name': 'Edit',
                             'tool_use_id': 'call-1', 'cwd': str(self.project),
                             'tool_input': {'file_path': str(self.project / 'docs/brief.md'),
                                            'old_string': 'Goals', 'new_string': 'Aims'}})
        self.assertEqual(blocked.returncode, 2, blocked.stdout + blocked.stderr)
        self.assertIn('docs/brief.md', blocked.stderr)
        self.assertIn('src/app/**', blocked.stderr)
        self.assertEqual(self.m.db.execute("SELECT count(*) FROM host_receipts WHERE event_name='PreToolUse'").fetchone()[0], 0)
        block = self.read('now')['scope_blocks'][0]
        self.assertEqual((block['tool_name'], block['blocked'], block['still_outside']),
                         ('Edit', ['docs/brief.md'], ['docs/brief.md']))
        self.assertEqual([item['episode_id'] for item in self.attention('scope_block')], [episode])

        # The user extends the scope in the control panel, so the same edit is no longer outside it.
        allowed = self.user('allow_paths', {'episode_id': episode, 'expected_version': self.version(episode),
                                            'paths': ['docs/brief.md'],
                                            'reason': 'The brief must record the tagged file decision.'}, 'allow-1')
        self.assertEqual(allowed['added'], ['docs/brief.md'])
        self.assertEqual(self.read('now')['scope_blocks'][0]['still_outside'], [])
        self.assertEqual(self.attention('scope_block'), [])

        # Delegated work: Codex reports a usage limit, Claude finishes the work and the other host reviews it.
        self.write_file('src/app/parser.py', 'VALUE = 1\n')
        self.commit('Add the parser', 'src/app/parser.py')
        stopped = self.delegate(episode, 'delegate-1', host='codex')
        self.assertEqual((stopped['host'], stopped['state']), ('codex', 'host_unavailable'))
        self.assertEqual(stopped['metrics']['host_unavailable']['reason'], 'usage_limit')
        rerouted = self.child_run(stopped['id'], 'work')
        self.assertEqual((rerouted['host'], rerouted['state']), ('claude', 'completed'))
        self.assertEqual(rerouted['metrics']['changed_files'], ['src/app/parser.py'])
        self.assertEqual(rerouted['snapshot']['rerouted_from'], stopped['id'])
        review = self.child_run(rerouted['id'], 'work_review')
        self.assertEqual(review['state'], 'pass')
        agents = self.read('agents')
        availability = {host['host']: host for host in agents['hosts']}
        self.assertFalse(availability['codex']['available'])
        self.assertIsNotNone(availability['codex']['until'])
        self.assertTrue(availability['claude']['available'])
        self.assertEqual({run['role'] for run in agents['runs']['runs']}, {'work', 'work_review'})
        waiting = [item for item in self.read('now')['attention'] if item['type'] == 'awaiting_merge']
        self.assertEqual([item['id'] for item in waiting], [rerouted['id']])
        merged = self.user('merge', {'run_id': rerouted['id']}, 'merge-1')
        self.assertTrue(merged['merged'])
        self.assertEqual((self.project / 'src/app/parser.py').read_text(), 'VALUE = 2\n')
        summary = next(run for run in self.read('work', id=episode)['runs']['runs'] if run['id'] == rerouted['id'])
        self.assertEqual(summary['merge']['state'], 'merged')

        # The same failure type recurs in other work, although the guard was acknowledged.
        other = self.user('plan', {
            'title': 'Import the archived client files', 'objective': 'Import the archived files without lost characters.',
            'criterion': 'Every archived sample file imports.', 'subject': 'code',
            'payload': {'state': 'ready', 'next_action': 'Import the archive.', 'item_type': 'task',
                        'scope': 'Import the archive only.', 'autonomy': 'act',
                        'reason': 'The user asks for the archived files.', 'paths': ['src/app/**']}}, 'plan-archive')
        repeat = self.assistant('record', 'decision-4', {
            'episode_id': other['episode_id'], 'kind': 'decision', 'expected_version': self.version(other['episode_id']),
            'evidence': evidence,
            'payload': {'decision': 'Import the archive in one pass.', 'why': 'One pass is fast enough for the archive.',
                        'expected': 'Every archived file imports.', 'reconsider_when': 'An archived file fails.',
                        'uncertainty': 'The archive encodings are unknown.', 'alternatives': ['Import the archive in batches.'],
                        'lessons_considered': [{'lesson_id': lesson['id'], 'applies': 'yes',
                                                'reason': 'The tagged sample was run before the import.'}]}})
        self.assistant('record', 'action-2', {
            'episode_id': other['episode_id'], 'kind': 'action', 'expected_version': self.version(other['episode_id']),
            'decision_id': repeat['id'], 'payload': {'action': 'Import the archived files.'}})
        self.assistant('record', 'outcome-2', {
            'episode_id': other['episode_id'], 'kind': 'outcome', 'expected_version': self.version(other['episode_id']),
            'decision_id': repeat['id'], 'evidence': evidence,
            'payload': {'observed': 'Characters were lost in the archived files.', 'assessment': 'bad',
                        'assessment_reason': 'The archived files lost characters.', 'severity': 'major',
                        'attribution': 'The single pass caused the loss.', 'completion': 'partial',
                        'failure_type': 'lost_text'}})
        recurrence = self.read('learning')
        self.assertEqual(recurrence['guards'][0]['recurrences'], 1)
        self.assertEqual([item['lesson_id'] for item in recurrence['recurrences']], [lesson['id']])
        self.assertEqual([item['id'] for item in self.attention('guard_recurrence')], [lesson['id']])

        # Done is gated by the outcome check, which the rejection starts.
        self.assistant('record', 'action-3', {
            'episode_id': episode, 'kind': 'action', 'expected_version': self.version(episode),
            'decision_id': second['id'], 'payload': {'action': 'Import both sample files with the merged parser.'}})
        self.assistant('record', 'outcome-3', {
            'episode_id': episode, 'kind': 'outcome', 'expected_version': self.version(episode),
            'decision_id': second['id'], 'evidence': evidence,
            'payload': {'observed': 'Both sample files imported without lost characters.', 'assessment': 'good',
                        'assessment_reason': 'The observed result meets the expectation.', 'severity': 'none',
                        'attribution': 'The merged parser produced this result.', 'completion': 'complete'}})
        work = self.read('work', id=episode)
        self.assertEqual(work['next']['action'], 'request_review')
        with self.assertRaises(ToolError) as caught:
            self.assistant('progress', 'done-1', {'episode_id': episode, 'expected_version': self.version(episode),
                                                  'payload': {'state': 'done', 'reason': 'Both sample files import.'}})
        check = caught.exception.value['agent_check']
        self.assertTrue(check['requested'])
        self.assertEqual(self.wait_for(check['id'])['state'], 'pass')
        self.assistant('progress', 'done-2', {'episode_id': episode, 'expected_version': self.version(episode),
                                              'payload': {'state': 'done', 'reason': 'Both sample files import and the check passed.'}})
        done = self.read('work', id=episode)
        self.assertEqual(done['card']['state'], 'done')
        self.assertEqual(done['card']['issues'], [])
        self.assertEqual(self.read('now')['counts']['done'], 1)

        # The lineage of the story keeps its requirement, plan, decisions, runs and component.
        lineage = self.read('lineage', id=episode, depth='3', limit='120')
        kinds = {node['kind'] for node in lineage['nodes']}
        self.assertTrue({'episode', 'work_plan', 'decision', 'outcome', 'check', 'component'} <= kinds, kinds)
        self.assertIn('direction_1', [node['id'] for node in lineage['nodes']])


class EngagementScenario(Scenario):
    """A consulting engagement from scope to a deliverable that the client reviews."""

    template = 'engagement'
    project_name = 'Supplier cost review'
    clients = ('claude',)

    def host_plan(self):
        return {'claude:review': [{'verdict': 'pass'}]}

    def test_an_engagement_runs_from_scope_to_a_reviewed_deliverable(self):
        kickoff = self.view('kickoff')
        self.assertEqual(kickoff['template'], 'engagement')
        self.assistant('answer_kickoff', 'kickoff-1', {
            'question_ids': [question['id'] for question in kickoff['questions']],
            'text': 'The client needs to know whether supplier costs can fall by ten percent within a year. '
                    'The chief financial officer approves the board report by the end of the quarter.'})

        # Stakeholders and workstreams are authored components, and only the user confirms them.
        procurement = self.assistant('component', 'component-1', {
            'title': 'Procurement team', 'kind': 'stakeholder', 'description': 'Owns the supplier contracts and the cost data.',
            'status': 'proposed'})
        self.assertEqual(procurement['status'], 'proposed')
        workstream = self.user('component', {
            'title': 'Supplier cost review', 'kind': 'workstream',
            'description': 'Collects and analyses the supplier cost evidence.', 'status': 'confirmed'}, 'component-2')
        deliverable_component = self.user('component', {
            'title': 'Board report', 'kind': 'deliverable', 'description': 'Presents the recommendations to the board.',
            'status': 'confirmed', 'path': 'deliverables'}, 'component-3')
        self.user('link', {'from_id': procurement['id'], 'to_id': workstream['id'], 'type': 'informs',
                           'reason': 'Procurement provides the contract data.'}, 'link-1')
        self.user('link', {'from_id': workstream['id'], 'to_id': deliverable_component['id'], 'type': 'produces',
                           'reason': 'The review produces the evidence for the report.'}, 'link-2')
        components = {item['id']: item for item in self.read('components')['components']}
        self.assertEqual({item['status'] for item in components.values()}, {'proposed', 'confirmed'})
        self.assertEqual(components[workstream['id']]['links_total'], 2)
        authored = {node['id']: node for node in self.read('architecture', layers='authored')['nodes']}
        self.assertEqual(authored[procurement['id']]['layer'], 'authored')
        self.assertEqual(authored[deliverable_component['id']]['title'], 'Board report')

        # Hypotheses are research work items.
        request = self.assistant('source', 'source-1', {
            'source_key': 'user:engagement-request', 'title': 'Client request',
            'summary': 'The client describes the engagement.',
            'body': 'Reduce supplier costs by ten percent within a year, with a board report that shows the evidence.',
            'origin': 'user', 'subject': 'research'})
        evidence = [{'source_id': request['id'], 'reason': 'The client defines this engagement.'}]
        hypothesis = self.assistant('plan', 'plan-hypothesis', {
            'title': 'Test the hypothesis that freight costs drive the increase',
            'objective': 'Test whether freight costs drive the cost increase.',
            'criterion': 'The freight share of the increase is measured and recorded.', 'subject': 'research',
            'evidence': evidence,
            'payload': {'state': 'ready', 'next_action': 'Collect the freight invoices.', 'item_type': 'research',
                        'scope': 'Analyse the recorded invoices only.', 'autonomy': 'suggest',
                        'reason': 'A recommendation depends on this answer.', 'parent_id': self.phases['stakeholders']}})
        self.assertEqual([item['episode_id'] for item in self.view('kickoff')['research']], [hypothesis['episode_id']])

        # Findings are written and captured as evidence.
        self.write_file('engagement/findings.md',
                        '# Findings\n\nFreight costs explain 6 of the 9 percentage points of the increase.\n')
        finding = self.assistant('document', 'document-1', {'path': str(self.project / 'engagement/findings.md')})
        self.assertIn('engagement/findings.md', json.dumps(self.read('records', view='documents')['records']))

        # The user approves the requirements, and the deliverable story traces to them.
        self.user('requirements', {
            'requirements': ['The board report states each recommendation with its supporting evidence.',
                             'Each cost estimate states its margin of error.'],
            'reason': 'The client approved the scope of the report.',
            'expected_version': self.m.direction()['version']}, 'requirements-1')
        deliverable = self.user('plan', {
            'title': 'Draft the board report', 'objective': 'Present the supplier cost recommendations to the board.',
            'criterion': 'The client accepts the board report.', 'subject': 'writing',
            'payload': {'state': 'ready', 'next_action': 'Draft the report from the findings.', 'item_type': 'deliverable',
                        'scope': 'Write the board report only.', 'autonomy': 'act',
                        'reason': 'The client asks for the board report.',
                        'acceptance': ['The report states each recommendation with its supporting evidence.',
                                       'The report states the margin of error of each cost estimate.'],
                        'paths': ['deliverables/**']}}, 'plan-deliverable')
        episode = deliverable['episode_id']
        self.user('link', {'from_id': episode, 'to_id': deliverable_component['id'], 'type': 'implements',
                           'reason': 'The work item drafts the board report.'}, 'link-3')
        hierarchy = self.hierarchy_nodes(self.read('plan'))
        self.assertEqual(hierarchy[episode]['item_type'], 'deliverable')
        self.assertEqual(hierarchy[self.phases['client_review']]['owner'], 'human')

        # The recommendation is decided, delivered and reviewed with the client.
        decision = self.assistant('record', 'decision-1', {
            'episode_id': episode, 'kind': 'decision', 'expected_version': self.version(episode),
            'evidence': evidence + [{'source_id': finding['id'], 'reason': 'The findings support this recommendation.'}],
            'payload': {'decision': 'Recommend renegotiating the five largest freight contracts.',
                        'why': 'Freight costs explain most of the increase.',
                        'expected': 'The board accepts the recommendation.',
                        'reconsider_when': 'The freight share falls below three percentage points.',
                        'uncertainty': 'The contract renewal dates are not confirmed.',
                        'alternatives': ['Recommend a single tender for all freight.']}})
        self.write_file('deliverables/board-report.md',
                        '# Board report\n\nRenegotiate the five largest freight contracts.\n')
        self.assistant('record', 'action-1', {
            'episode_id': episode, 'kind': 'action', 'expected_version': self.version(episode),
            'decision_id': decision['id'], 'payload': {'action': 'Draft the board report with the recommendation.'}})
        self.assistant('record', 'outcome-1', {
            'episode_id': episode, 'kind': 'outcome', 'expected_version': self.version(episode),
            'decision_id': decision['id'], 'evidence': evidence,
            'payload': {'observed': 'The client accepted the report with two wording changes.', 'assessment': 'good',
                        'assessment_reason': 'The client accepted the recommendation and its evidence.',
                        'severity': 'none', 'attribution': 'The drafted report produced this result.',
                        'completion': 'complete'}})
        self.user('comment', {'episode_id': episode, 'expected_version': self.version(episode),
                              'text': 'The client asked for the margin of error next to each estimate.'}, 'comment-1')

        # The lineage of the deliverable decision reaches the approved requirements and the component.
        lineage = self.read('lineage', id=decision['id'], depth='3', limit='120')
        ids = [node['id'] for node in lineage['nodes']]
        self.assertIn('direction_1', ids)
        self.assertIn(episode, ids)
        self.assertIn(deliverable_component['id'], ids)
        self.assertIn(finding['id'], ids)

        # The panel shows the engagement without any assumption that the work is code.
        now = self.read('now')
        self.assertEqual(now['project'], self.project_name)
        self.assertEqual(now['counts']['ready'], 1)
        self.assertEqual([item['id'] for item in now['review']], [episode])
        self.assertEqual([item['id'] for item in self.attention('work_to_review')], [episode])
        self.assertEqual({item['id'] for item in now['latest_decisions']}, {decision['id']})
        self.assertEqual(self.read('agents')['hosts'][0]['host'], 'claude')
        self.assertEqual(self.read('learning')['guards'], [])


class AutomationScenario(Scenario):
    """A workflow automation from process discovery to a delegated workflow edit."""

    template = 'automation'
    project_name = 'Lead intake automation'

    def host_plan(self):
        return {'claude:work': [{'write': {'workflows/lead-intake.json': WORKFLOW.replace('#sales', '#sales-leads')}},
                                {'write': {'workflows/lead-intake.json': WORKFLOW,
                                           'automation/systems.md': '# Systems\n\nThe worker changed this file.\n'}}]}

    def test_an_automation_runs_from_process_discovery_to_a_delegated_workflow_edit(self):
        kickoff = self.view('kickoff')
        self.assertEqual(kickoff['template'], 'automation')
        self.assistant('answer_kickoff', 'kickoff-1', {
            'question_ids': [question['id'] for question in kickoff['questions']],
            'text': 'New leads arrive through the website form, about 60 each day, and a sales assistant copies each one '
                    'into HubSpot by hand. Slack receives the notification, and the client owns both credentials.'})

        # The systems inventory records the systems without any secret.
        slack = self.assistant('component', 'component-1', {
            'title': 'Slack workspace', 'kind': 'system', 'description': 'Receives the lead notifications for the sales team.',
            'status': 'proposed', 'path': 'service:slack'})
        confirmed = self.user('component', {'component_id': slack['id'], 'title': 'Slack workspace', 'kind': 'system',
                                            'description': 'Receives the lead notifications for the sales team.',
                                            'status': 'confirmed', 'path': 'service:slack'}, 'component-confirm')
        self.assertEqual(confirmed['status'], 'confirmed')

        # The exported workflow produces the architecture layer of the automation.
        self.write_file('workflows/lead-intake.json', WORKFLOW)
        structure = self.read('architecture')
        nodes = {node['id']: node for node in structure['nodes']}
        workflow_id = 'component:n8n:workflows/lead-intake.json'
        self.assertEqual(nodes[workflow_id]['title'], 'Lead intake')
        self.assertEqual(nodes[workflow_id]['layer'], 'n8n')
        self.assertEqual(nodes['service:slack']['authored_status'], 'confirmed')
        self.assertIn((workflow_id, 'service:slack', 'uses'),
                      [(edge['from'], edge['to'], edge['type']) for edge in structure['edges']])
        self.assertEqual(graph.node(self.m, workflow_id)['title'], 'Lead intake')
        detail = self.read('architecture', level='file', focus='n8n:workflows/lead-intake.json')
        self.assertEqual(sorted(node['id'] for node in detail['nodes']),
                         [workflow_id + '#New lead webhook', workflow_id + '#Notify sales'])

        # The workflow story states its sample data in the acceptance criteria.
        request = self.assistant('source', 'source-1', {
            'source_key': 'user:automation-request', 'title': 'Client request',
            'summary': 'The client describes the automation.',
            'body': 'Route new leads from the website form to the sales team within five minutes.',
            'origin': 'user', 'subject': 'general'})
        evidence = [{'source_id': request['id'], 'reason': 'The client asks for this automation.'}]
        self.write_file('automation/test-data/sample-lead.json',
                        json.dumps({'email': 'lead@example.invalid', 'message': 'We need a quote.'}, indent=2) + '\n')
        story = self.user('plan', {
            'title': 'Build the lead intake workflow', 'objective': 'Route new leads to the sales team within five minutes.',
            'criterion': 'The sample lead reaches the sales channel within five minutes.', 'subject': 'general',
            'payload': {'state': 'ready', 'next_action': 'Change the exported workflow.', 'item_type': 'workflow',
                        'scope': 'Change the exported workflows only.', 'autonomy': 'act',
                        'reason': 'The client asks for the lead routing.',
                        'acceptance': ['The sample lead in automation/test-data reaches the sales channel within five minutes.',
                                       'A lead without an email address goes to the review queue.'],
                        'paths': ['workflows/**']}}, 'plan-workflow')
        episode = story['episode_id']
        self.user('link', {'from_id': episode, 'to_id': workflow_id, 'type': 'implements',
                           'reason': 'The work item builds this workflow.'}, 'link-workflow')
        self.assertEqual(guards.plan_paths(self.m, episode), ['workflows/**'])
        attached = {node['id']: node for node in self.read('architecture')['nodes']}
        self.assertIn(episode, attached[workflow_id]['work'])
        self.assertEqual(attached[workflow_id]['links_total'], 1)

        # Delegated work may change the workflow exports only.
        self.commit('Add the exported workflow and its sample data', 'workflows/lead-intake.json',
                    'automation/test-data/sample-lead.json')
        run = self.delegate(episode, 'delegate-1', host='claude')
        self.assertEqual((run['host'], run['state']), ('claude', 'completed'))
        self.assertEqual(run['metrics']['changed_files'], ['workflows/lead-intake.json'])
        review = self.child_run(run['id'], 'work_review')
        self.assertEqual(review['state'], 'pass')
        self.user('merge', {'run_id': run['id']}, 'merge-1')
        self.assertIn('#sales-leads', (self.project / 'workflows/lead-intake.json').read_text())

        # A change outside the workflow folder is a scope violation, and nothing is merged.
        second = self.delegate(episode, 'delegate-2', host='claude')
        self.assertEqual(second['state'], 'scope_violation')
        self.assertEqual(second['metrics']['outside_paths'], ['automation/systems.md'])
        self.assertIn('automation/systems.md', second['error'])
        self.user('discard', {'run_id': second['id'], 'reason': 'The change left the workflow folder.'}, 'discard-1')
        runs = {item['id']: item for item in self.read('agents')['runs']['runs']}
        self.assertEqual(runs[run['id']]['merge']['state'], 'merged')
        self.assertEqual(runs[second['id']]['merge']['state'], 'discarded')
        self.assertTrue((self.project / 'automation/systems.md').read_text().startswith('# Systems and credentials'))

        # The panel describes the automation in its own terms.
        now = self.read('now')
        self.assertEqual(now['project'], self.project_name)
        self.assertEqual([item['item_type'] for item in now['in_progress'] + now['ready'] if item['id'] == episode], ['workflow'])
        hierarchy = self.hierarchy_nodes(self.read('plan'))
        self.assertEqual(hierarchy[self.phases['build']]['item_type'], 'phase')
        lineage = self.read('lineage', id=episode, depth='2', limit='80')
        self.assertIn(workflow_id, [node['id'] for node in lineage['nodes']])


if __name__ == '__main__':
    unittest.main()
