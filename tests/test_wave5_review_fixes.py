"""Regression tests for the wave 5 review findings.

Each test names the behaviour that was wrong before the fix, so a change that
brings the old behaviour back fails here rather than in a manual session.
"""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

from memory_module import Memory, architecture, cli, graph, mcp, planning, templates
from memory_module.arch_authored import save_component
from memory_module.core import InvalidRecord

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = json.dumps({
    'name': 'Enquiry intake', 'id': 'enquiry-intake', 'active': False,
    'nodes': [
        {'id': '1', 'name': 'New enquiry', 'type': 'n8n-nodes-base.webhook', 'typeVersion': 1, 'position': [0, 0],
         'parameters': {'path': 'enquiry', 'httpMethod': 'POST'}},
        {'id': '2', 'name': 'Notify sales', 'type': 'n8n-nodes-base.slack', 'typeVersion': 1, 'position': [240, 0],
         'parameters': {'channel': '#sales'},
         'credentials': {'slackApi': {'id': 'cred-slack', 'name': 'Sales Slack'}}}],
    'connections': {'New enquiry': {'main': [[{'node': 'Notify sales', 'type': 'main', 'index': 0}]]}}}, indent=2) + '\n'


class ProjectFixture(unittest.TestCase):
    """A project folder with its own database, without a template."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.m = Memory.create(self.root / '.memory' / 'memory.sqlite', 'Fixes', ['Keep the recorded exception.'])
        self.addCleanup(self.m.close)
        self.counter = 0

    def key(self):
        self.counter += 1
        return 'fix-' + str(self.counter)

    def user_source(self):
        source = self.m.source('user:' + self.key(), 'User request', 'The user states the scope.',
                               'Deliver the work that the user asked for.', 'user')
        return [{'source_id': source['id'], 'reason': 'The user asks for this work.'}]

    def item(self, title, state, **extra):
        payload = {'state': state, 'next_action': 'Continue ' + title + '.', 'scope': 'The ' + title + ' only.',
                   'autonomy': 'suggest', 'reason': 'The user plans it.', **extra}
        return planning.save(self.m, 'work_plan', payload=payload, actor='assistant', evidence=self.user_source(),
                             title=title, objective='Deliver ' + title + '.', criterion=title + ' is accepted.',
                             request_key=self.key())['episode_id']


class NextWorkSelectionTests(ProjectFixture):
    """Asking what to do next must show the work that can start, not only the first page of the board."""

    def test_selection_names_the_ready_work_and_honours_the_state_filter(self):
        # The backlog items are created first, so they fill the first page of the board by age.
        for number in range(6):
            self.item('Phase ' + str(number + 1), 'backlog', item_type='phase')
        ready = self.item('Confirm the filing deadlines', 'ready')
        running = self.item('Show the late filings', 'in_progress', owner='human')

        result = planning.next_work(self.m, limit=4)
        self.assertTrue(result['selection_required'])
        # The board page still holds the backlog phases, which is what hid the ready work before.
        self.assertNotIn(ready, [card['id'] for card in result['board']['cards']])
        self.assertEqual(result['ready_total'], 2)
        self.assertEqual({card['id'] for card in result['ready']}, {ready, running})
        self.assertIn('can start now', result['next_step'])

        filtered = planning.next_work(self.m, state='ready')
        self.assertEqual([card['id'] for card in filtered['board']['cards']], [ready])
        self.assertNotIn('ready', filtered)

    def test_the_next_view_passes_the_state_filter_through_mcp(self):
        ready = self.item('Confirm the filing deadlines', 'ready')
        self.item('Goals and brief', 'backlog', item_type='phase')
        result = mcp.dispatch(self.m, 'memory_get', {'view': 'next', 'state': 'ready', 'max_chars': 20000})
        self.assertEqual([card['id'] for card in result['board']['cards']], [ready])
        without = mcp.dispatch(self.m, 'memory_get', {'view': 'next', 'max_chars': 20000})
        self.assertEqual([card['id'] for card in without['ready']], [ready])


class RequirementApprovalTests(ProjectFixture):
    """Only the user approves the requirements baseline, so an agent cannot cite its own writing as the approval."""

    def test_approval_needs_user_origin_evidence(self):
        written = self.m.source('docs/brief.md', 'Product brief', 'The assistant drafted the brief.',
                                'The brief states the goals of the product.', 'tool')
        with self.assertRaises(InvalidRecord) as caught:
            self.m.approve_requirements(requirements=['Import every client file without lost characters.'],
                                        reason='The assistant approves its own brief.', actor='assistant',
                                        evidence=[{'source_id': written['id'], 'reason': 'The brief states the requirement.'}],
                                        expected_version=0, request_key=self.key())
        self.assertIn('user-origin evidence', str(caught.exception))
        self.assertEqual(caught.exception.details['next_step']['action'], 'ask_user')
        self.assertEqual(self.m.direction()['version'], 0)

        spoken = self.m.source('user:approval', 'The user approves the requirements', 'The user approved the baseline.',
                               'The user said that every client file must import without lost characters.', 'user')
        approved = self.m.approve_requirements(requirements=['Import every client file without lost characters.'],
                                               reason='The user approved the requirements.', actor='assistant',
                                               evidence=[{'source_id': spoken['id'], 'reason': 'The user states the approval.'},
                                                         {'source_id': written['id'], 'reason': 'The brief states the detail.'}],
                                               expected_version=0, request_key=self.key())
        self.assertEqual(approved['version'], 1)


class ComponentPathTests(ProjectFixture):
    """A revision that does not repeat the path must not drop the link between a component and the project."""

    def save(self, **values):
        data = {'title': 'Filing tracker service', 'kind': 'service', 'description': 'The service tracks the filings.',
                'status': 'proposed', 'actor': 'assistant', 'request_key': self.key()}
        return save_component(self.m, **{**data, **values})

    def test_an_omitted_path_is_kept_and_an_explicit_null_clears_it(self):
        created = self.save(path='src/tracker')
        self.assertEqual(created['path'], 'src/tracker')
        revised = self.save(component_id=created['id'], description='The service tracks and reports the filings.')
        self.assertEqual((revised['path'], revised['version']), ('src/tracker', 2))
        cleared = self.save(component_id=created['id'], description='The service tracks and reports the filings.', path=None)
        self.assertEqual((cleared['path'], cleared['version']), (None, 3))

    def test_the_component_operation_still_accepts_a_null_path(self):
        rules, hidden = mcp.operation_rules('component')
        self.assertEqual(rules['path'], {'type': ['string', 'null']})
        self.assertEqual(hidden, set())


class ArchitectureEdgeTests(ProjectFixture):
    """A recorded link that repeats an extracted relation is one relation, so it is drawn once."""

    def test_an_authored_link_merges_into_the_edge_of_the_workflow_export(self):
        workflow = self.root / 'workflows' / 'enquiry-intake.json'
        workflow.parent.mkdir(parents=True)
        workflow.write_text(WORKFLOW, encoding='utf-8')
        workflow_id = 'component:n8n:workflows/enquiry-intake.json'
        slack = save_component(self.m, title='Slack workspace', kind='system', description='The sales team uses Slack.',
                               status='proposed', actor='assistant', request_key=self.key(), path='service:slack')['id']
        nodes = {node['id']: node for node in architecture.model(self.m)['nodes']}
        self.assertEqual(nodes['service:slack']['authored']['id'], slack)

        graph.link(self.m, from_id=workflow_id, to_id=slack, type='uses',
                   reason='The phase guidance asks the user to relate the workflow to the system.',
                   actor='workspace-user', request_key=self.key())
        edges = [edge for edge in architecture.model(self.m)['edges']
                 if (edge['from'], edge['to'], edge['type']) == (workflow_id, 'service:slack', 'uses')]
        self.assertEqual(len(edges), 1, 'The recorded link repeated the edge of the export instead of merging into it.')
        self.assertTrue(edges[0]['link_id'], 'The merged edge does not name the recorded link.')


class KickoffProgressTests(unittest.TestCase):
    """Kickoff must reach the research and phase steps instead of waiting for documents of later phases."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name).resolve() / 'engagement'
        self.result = templates.scaffold(self.project, 'engagement', git=False)
        self.m = Memory(self.result['database'])
        self.addCleanup(self.m.close)
        self.counter = 0

    def key(self):
        self.counter += 1
        return 'kickoff-' + str(self.counter)

    def answer_everything(self):
        ids = [question['id'] for question in templates.TEMPLATES['engagement']['questions']]
        templates.answer_kickoff(self.m, question_ids=ids, text='The client answered every question in the workshop.',
                                 actor='assistant', request_key=self.key())

    def test_the_next_step_reaches_research_and_the_phase_after_the_first_documents(self):
        self.answer_everything()
        step = templates.kickoff(self.m)['next_step']
        # Only the brief belongs to the first phase. The findings and the deliverable list cannot exist in the first hour.
        self.assertEqual(step['paths'], ['engagement/brief.md'])
        (self.project / 'engagement' / 'brief.md').write_text('# Brief\n\nThe client needs a cost reduction plan.\n',
                                                              encoding='utf-8')
        self.assertEqual(templates.kickoff(self.m)['next_step']['action'], 'approve_requirements')

        source = self.m.source('user:approval', 'Client approval', 'The client approved the scope.',
                               'The client approved a cost reduction plan for two regions.', 'user')
        self.m.approve_requirements(requirements=['Reduce costs in two regions without closing sites.'],
                                    reason='The client approved the scope.', actor='workspace-user',
                                    evidence=[{'source_id': source['id'], 'reason': 'The client approval.'}],
                                    expected_version=0, request_key=self.key())
        # With no open research the step names the phase, and it states how a phase moves forward.
        step = templates.kickoff(self.m)['next_step']
        self.assertEqual(step['action'], 'continue_phase')
        self.assertEqual(step['episode_id'], self.result['phases'][0]['episode_id'])
        self.assertIn('in_progress', step['reason'])

        planning.save(self.m, 'work_plan',
                      payload={'state': 'ready', 'next_action': 'Collect the regional cost data.',
                               'scope': 'Cost data only.', 'autonomy': 'suggest', 'reason': 'The findings need the data.',
                               'item_type': 'research'},
                      actor='assistant', evidence=[{'source_id': source['id'], 'reason': 'The scope needs cost data.'}],
                      title='Test whether consolidation keeps the service level',
                      objective='Test whether consolidation keeps the service level.',
                      criterion='The service level of the consolidated sites is measured.', subject='research',
                      request_key=self.key())
        self.assertEqual(templates.kickoff(self.m)['next_step']['action'], 'research')

    def test_the_result_states_how_a_phase_moves_forward(self):
        state = templates.kickoff(self.m)
        self.assertIn('in_progress', state['note'])
        self.assertEqual(state['current_phase'], 'scope')


class KickoffAnswerPhaseTests(unittest.TestCase):
    """An answer is recorded on the phase it belongs to, not always on the first phase."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        project = Path(self.temp.name).resolve() / 'automation'
        self.result = templates.scaffold(project, 'automation', git=False)
        self.m = Memory(self.result['database'])
        self.addCleanup(self.m.close)
        self.phases = {phase['key']: phase['episode_id'] for phase in self.result['phases']}

    def test_an_answer_about_credentials_belongs_to_the_systems_phase(self):
        note = templates.answer_kickoff(self.m, question_ids=['systems', 'credentials'],
                                        text='The lead system is HubSpot and the client owns its credential.',
                                        actor='assistant', request_key='answer-systems')
        self.assertEqual(self.m.read(note['id'])['episode_id'], self.phases['systems'])
        first = templates.answer_kickoff(self.m, question_ids=['process'], text='Leads arrive through the website form.',
                                         actor='assistant', request_key='answer-process')
        self.assertEqual(self.m.read(first['id'])['episode_id'], self.phases['process'])
        # Every question states its phase, so a caller can see where its answer is recorded.
        questions = {item['id']: item['phase'] for item in templates.kickoff(self.m)['questions']}
        self.assertEqual(questions['credentials'], 'systems')


class GuidanceTests(unittest.TestCase):
    """Interface text that the findings named as misleading."""

    def test_the_systems_phase_sends_the_user_to_the_derived_service_name(self):
        phase = next(item for item in templates.TEMPLATES['automation']['phases'] if item['key'] == 'systems')
        self.assertIn('n8n layer', phase['next_action'])
        self.assertIn('instead of guessing it', phase['next_action'])

    def test_a_component_without_a_path_is_told_that_a_link_attaches_its_work(self):
        form = (ROOT / 'memory_module' / 'ui' / 'forms.js').read_text(encoding='utf-8')
        self.assertIn('only when a link records the relation', form)
        self.assertIn('a stakeholder or a workstream, shows its work only through a recorded link',
                      mcp.schema('component')['rules'])

    def test_the_kickoff_checklist_is_not_hidden_by_an_approved_baseline(self):
        panel = (ROOT / 'memory_module' / 'ui' / 'views_work.js').read_text(encoding='utf-8')
        self.assertNotIn('now.kickoff.baseline !== "not_established"', panel)
        self.assertIn('kickoff_complete', panel)


class ReleaseGateTests(unittest.TestCase):
    """The artifact check must require every runtime module, as the release documentation states."""

    def test_every_runtime_module_is_required_in_the_built_artifacts(self):
        sys.path.insert(0, str(ROOT / 'scripts'))
        try:
            from check_artifacts import REQUIRED_MODULES, RETIRED_MODULES
        finally:
            sys.path.remove(str(ROOT / 'scripts'))
        shipped = {path.stem for path in (ROOT / 'memory_module').glob('*.py')} - {'__init__', '__main__'}
        self.assertEqual(shipped - set(REQUIRED_MODULES), set())
        self.assertEqual(set(REQUIRED_MODULES) & set(RETIRED_MODULES), set())


class MissingDatabaseTests(unittest.TestCase):
    """A command that needs a project explains itself instead of reporting an operating system error."""

    def run_command(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(argv))
        return code, err.getvalue()

    def test_view_doctor_and_serve_refuse_a_missing_project_in_plain_words(self):
        with tempfile.TemporaryDirectory() as folder:
            empty = Path(folder).resolve()
            for command in ('view', 'doctor', 'serve'):
                with self.subTest(command=command):
                    argv = [command, '--project', str(empty)] + (['--no-open'] if command == 'view' else [])
                    code, message = self.run_command(*argv)
                    self.assertEqual(code, 1)
                    self.assertIn('No project records were found in this folder.', message)
                    self.assertIn('project-memory setup', message)
                    # The message names no internal file, and nothing is created in the folder.
                    self.assertNotIn('.memory', message)
                    self.assertNotIn('Errno', message)
            self.assertEqual(list(empty.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
