"""Tests for project templates, kickoff, the work item hierarchy and the related wave 1 follow ups."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

from memory_module import Memory, cli, codex_host, graph, guards, planning, templates
from memory_module.core import Conflict, InvalidRecord

LAUNCHER = [sys.executable, '-m', 'memory_module.cli']


def counts(path):
    with Memory(path, read_only=True) as memory:
        return {table: memory.db.execute('SELECT count(*) FROM ' + table).fetchone()[0]
                for table in ('episodes', 'events', 'sources', 'settings')}


class ScaffoldTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def test_each_template_is_idempotent_with_phases_dependencies_and_documents(self):
        for template, clients in (('product', ['codex', 'claude']), ('engagement', []), ('automation', ['claude'])):
            with self.subTest(template=template):
                project = self.base / template
                spec = templates.TEMPLATES[template]
                first = templates.scaffold(project, template, name='Client ' + template, clients=clients, _launcher=LAUNCHER)
                self.assertTrue(first['git']['initialized'])
                self.assertTrue((project / '.git').is_dir())
                self.assertEqual(first['clients'], sorted(clients or ['mcp']))
                self.assertEqual(first['created'], {'documents': len(spec['documents']), 'captures': len(spec['documents']),
                                                    'phases': 7})
                for relative, text in spec['documents'].items():
                    self.assertEqual((project / relative).read_text(encoding='utf-8'), text)
                database = Path(first['database'])
                before = counts(database)
                with Memory(database, read_only=True) as memory:
                    self.assertEqual(memory.project, 'Client ' + template)
                    self.assertEqual(templates.setting(memory)['template'], template)
                    previous = None
                    for phase, recorded in zip(spec['phases'], first['phases']):
                        plan = planning.latest(memory, recorded['episode_id'], 'work_plan')
                        episode = memory.episode(recorded['episode_id'])
                        self.assertEqual(episode['title'], phase['title'])
                        self.assertEqual((plan['item_type'], plan['state'], plan['autonomy'], plan['owner']),
                                         ('phase', 'backlog', 'suggest', phase['owner']))
                        self.assertEqual(plan.get('paths', []), phase['paths'])
                        if previous:
                            self.assertEqual([ref['episode_id'] for ref in plan['depends_on']], [previous])
                        else:
                            self.assertNotIn('depends_on', plan)
                        evidence = memory.read(plan['id'])['evidence']
                        self.assertEqual(evidence[0]['origin'], 'tool')
                        previous = recorded['episode_id']
                    owners = {phase['owner'] for phase in spec['phases']}
                    self.assertEqual(owners, {'agent', 'human'})
                    captured = memory.db.execute("SELECT count(*) FROM sources WHERE source_key LIKE 'local-markdown:%'").fetchone()[0]
                    self.assertEqual(captured, len(spec['documents']))
                second = templates.scaffold(project, template, clients=clients, _launcher=LAUNCHER)
                self.assertEqual(second['created'], {'documents': 0, 'captures': 0, 'phases': 0})
                self.assertFalse(second['git']['initialized'])
                self.assertEqual([phase['episode_id'] for phase in second['phases']], [phase['episode_id'] for phase in first['phases']])
                self.assertEqual(counts(database), before)

    def test_existing_documents_are_not_overwritten(self):
        project = self.base / 'product'
        brief = project / 'docs' / 'brief.md'
        brief.parent.mkdir(parents=True)
        brief.write_text('# Our brief\n\nThe user wrote this brief.\n', encoding='utf-8')
        result = templates.scaffold(project, 'product', git=False)
        self.assertFalse((project / '.git').exists())
        self.assertEqual({entry['path']: entry['written'] for entry in result['documents']}['docs/brief.md'], False)
        self.assertEqual(brief.read_text(encoding='utf-8'), '# Our brief\n\nThe user wrote this brief.\n')
        (project / 'docs' / 'research.md').write_text('# Research\n\nThe user replaced the skeleton.\n', encoding='utf-8')
        templates.scaffold(project, 'product', git=False)
        self.assertIn('The user replaced the skeleton.', (project / 'docs' / 'research.md').read_text(encoding='utf-8'))
        with self.assertRaises(Conflict):
            templates.scaffold(project, 'engagement', git=False)
        with self.assertRaises(Conflict):
            templates.scaffold(project, 'product', git=False, requirements=['A different requirement applies.'])
        with self.assertRaises(InvalidRecord):
            templates.scaffold(self.base / 'other', 'website', git=False)
        with self.assertRaises(InvalidRecord):
            templates.scaffold(self.base / 'other', 'product', clients=['cursor'], git=False)

    def test_cli_init_creates_a_project(self):
        project = self.base / 'automation'
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli.main(['init', str(project), '--template', 'automation', '--name', 'Invoice automation', '--no-git', '--no-view'])
        self.assertEqual(code, 0)
        result = json.loads(output.getvalue())
        self.assertEqual((result['template'], result['clients']), ('automation', ['mcp']))
        self.assertTrue((project / 'workflows' / 'README.md').exists())
        self.assertFalse((project / '.git').exists())
        self.assertNotIn('viewer', result)


class KickoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name).resolve() / 'engagement'
        self.result = templates.scaffold(self.project, 'engagement', git=False)
        self.m = Memory(self.result['database'])
        self.counter = 0

    def tearDown(self):
        self.m.close()
        self.temp.cleanup()

    def key(self):
        self.counter += 1
        return 'kickoff-' + str(self.counter)

    def test_kickoff_reports_unanswered_questions_and_changes_after_answers(self):
        state = templates.kickoff(self.m)
        spec = templates.TEMPLATES['engagement']
        self.assertEqual(state['template'], 'engagement')
        self.assertEqual(state['baseline']['status'], 'not_established')
        # Each later phase depends on the phase before it, but backlog that waits for a prerequisite is not blocked.
        self.assertEqual([phase['state'] for phase in state['phases']], ['backlog'] * 7)
        self.assertEqual({entry['status'] for entry in state['documents']}, {'unchanged'})
        self.assertEqual([question['id'] for question in state['questions'] if not question['answered']],
                         [question['id'] for question in spec['questions']])
        self.assertEqual(state['next_step']['action'], 'answer_kickoff')

        note = templates.answer_kickoff(self.m, question_ids=['objective', 'scope'], text='The client needs a cost reduction plan for two regions.',
                                        actor='assistant', request_key=self.key())
        state = templates.kickoff(self.m)
        answered = {question['id']: question['answered_by'] for question in state['questions'] if question['answered']}
        self.assertEqual(answered, {'objective': note['id'], 'scope': note['id']})
        self.assertNotIn('objective', state['next_step']['question_ids'])
        self.assertEqual(self.m.read(note['id'])['episode_id'], self.result['phases'][0]['episode_id'])

        with self.assertRaises(InvalidRecord):
            templates.answer_kickoff(self.m, question_ids=['budget'], text='Unknown question.', actor='assistant', request_key=self.key())
        with self.assertRaises(InvalidRecord):
            templates.answer_kickoff(self.m, question_ids=['Scope!'], text='Invalid id.', actor='assistant', request_key=self.key())
        remaining = [question['id'] for question in spec['questions'] if question['id'] not in answered]
        templates.answer_kickoff(self.m, question_ids=remaining, text='The client answered the remaining questions in the workshop.',
                                 actor='assistant', request_key=self.key())
        state = templates.kickoff(self.m)
        self.assertTrue(all(question['answered'] for question in state['questions']))
        self.assertEqual(state['next_step']['action'], 'fill_documents')
        self.assertEqual(len(state['next_step']['paths']), 5)

        for relative in templates.TEMPLATES['engagement']['documents']:
            (self.project / relative).write_text('# Filled\n\nThe team recorded the client answers.\n', encoding='utf-8')
        (self.project / 'deliverables' / 'README.md').unlink()
        state = templates.kickoff(self.m)
        self.assertEqual({entry['path']: entry['status'] for entry in state['documents']}['deliverables/README.md'], 'missing')
        (self.project / 'deliverables' / 'README.md').write_text('# Deliverables\n\nThe report is due in October.\n', encoding='utf-8')
        state = templates.kickoff(self.m)
        self.assertEqual(state['next_step']['action'], 'approve_requirements')
        self.assertIn('memory_get kickoff', templates.kickoff_hint(self.m))

        source = self.m.source('client-approval', 'Client approval', 'The client approved the scope.', 'Reduce costs in two regions.', 'user')
        self.m.approve_requirements(requirements=['Reduce costs in two regions without closing sites.'], reason='The client approved the scope.',
                                    actor='workspace-user', evidence=[{'source_id': source['id'], 'reason': 'The client approval.'}],
                                    expected_version=0, request_key=self.key())
        self.assertEqual(templates.kickoff_hint(self.m), '')
        research_payload = {'state': 'ready', 'next_action': 'Collect the regional cost data.', 'scope': 'Cost data only.',
                            'autonomy': 'suggest', 'reason': 'The findings need the data.', 'item_type': 'research',
                            'parent_id': self.result['phases'][2]['episode_id']}
        research = planning.save(self.m, 'work_plan', payload=research_payload, actor='assistant',
                                 evidence=[{'source_id': source['id'], 'reason': 'The scope needs cost data.'}],
                                 title='Regional cost data', objective='Collect the cost data of both regions.',
                                 criterion='The cost data of both regions is captured.', subject='research', request_key=self.key())
        state = templates.kickoff(self.m)
        self.assertEqual(state['research'][0]['episode_id'], research['episode_id'])
        self.assertEqual(state['next_step']['action'], 'research')

    def test_kickoff_reads_on_a_read_only_connection_and_without_a_template(self):
        with Memory(self.result['database'], read_only=True) as reader:
            self.assertEqual(templates.kickoff(reader)['next_step']['action'], 'answer_kickoff')
        with tempfile.TemporaryDirectory() as folder:
            with Memory.create(Path(folder) / 'plain.sqlite', 'Plain', ['Keep evidence.']) as plain:
                state = templates.kickoff(plain)
                self.assertIsNone(state['template'])
                self.assertEqual(templates.kickoff_hint(plain), '')
                with self.assertRaises(InvalidRecord):
                    templates.answer_kickoff(plain, question_ids=['goals'], text='No template.', actor='assistant', request_key='x')

    def test_session_context_asks_for_kickoff_until_the_baseline_exists(self):
        start = codex_host.capture(self.m, {'hook_event_name': 'SessionStart', 'session_id': 'session-1', 'source': 'startup'}, host='claude')
        self.assertIn('memory_get kickoff', start['hookSpecificOutput']['additionalContext'])
        prompt = codex_host.capture(self.m, {'hook_event_name': 'UserPromptSubmit', 'session_id': 'session-1', 'prompt_id': 'p1',
                                             'prompt': 'Plan the engagement.'}, host='claude')
        self.assertIn('engagement template', prompt['hookSpecificOutput']['additionalContext'])
        codex = codex_host.capture(self.m, {'hook_event_name': 'UserPromptSubmit', 'session_id': 'session-2', 'turn_id': 't1',
                                            'prompt': 'Plan the engagement.'}, host='codex')
        self.assertEqual(codex['hookSpecificOutput']['additionalContext'].count('memory_get kickoff'), 1)


class HierarchyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.m = Memory.create(Path(self.temp.name) / 'memory.sqlite', 'Hierarchy', ['Deliver the stories.'])
        codex_host.initialize(self.m)
        source = self.m.source('user', 'Scope', 'The user sets the scope.', 'Deliver the checkout epic.', 'user')
        self.evidence = [{'source_id': source['id'], 'reason': 'The user sets the scope.'}]
        self.counter = 0

    def tearDown(self):
        self.m.close()
        self.temp.cleanup()

    def key(self):
        self.counter += 1
        return 'hierarchy-' + str(self.counter)

    def item(self, title, item_type='story', state='ready', **extra):
        payload = {'state': state, 'next_action': 'Work on ' + title + '.', 'scope': 'The ' + title + ' only.',
                   'autonomy': 'suggest', 'reason': 'The user plans it.', 'item_type': item_type, **extra}
        return planning.save(self.m, 'work_plan', payload=payload, actor='assistant', evidence=self.evidence, title=title,
                             objective='Deliver ' + title + '.', criterion=title + ' is accepted.', request_key=self.key())['episode_id']

    def revise(self, episode_id, **changes):
        plan = planning.latest(self.m, episode_id, 'work_plan')
        plan.pop('id')
        return planning.save(self.m, 'work_plan', payload={**plan, **changes}, actor='assistant', evidence=self.evidence,
                             episode_id=episode_id, expected_version=self.m.episode(episode_id)['version'], request_key=self.key())

    def test_hierarchy_rolls_up_states_and_graph_derives_part_of(self):
        phase = self.item('Build', item_type='phase', state='in_progress', owner='human')
        epic = self.item('Checkout', item_type='epic', parent_id=phase)
        stories = [self.item('Cart', parent_id=epic, acceptance=['The cart keeps items after a reload.']),
                   self.item('Payment', parent_id=epic), self.item('Receipt', parent_id=epic, state='cancelled')]
        task = self.item('Card form', item_type='task', parent_id=stories[1])
        loose = self.item('Research tax rules', item_type='research')
        states = {stories[0]: 'done', stories[1]: 'in_progress', task: 'blocked'}
        tree = planning.hierarchy(self.m, states=states)
        self.assertEqual([root['id'] for root in tree['roots']], [phase, loose])
        self.assertFalse(tree['truncated'])
        self.assertEqual(tree['total'], 7)
        root = tree['roots'][0]
        self.assertEqual(root['descendants'], 5)
        self.assertEqual(root['rollup']['done'], 1)
        self.assertEqual(root['rollup']['blocked'], 1)
        self.assertEqual(root['rollup']['cancelled'], 1)
        self.assertEqual(root['progress'], 0.25)
        checkout = root['children'][0]
        self.assertEqual((checkout['id'], checkout['item_type']), (epic, 'epic'))
        self.assertEqual([child['id'] for child in checkout['children']], stories)
        self.assertEqual(checkout['children'][0]['acceptance_total'], 1)
        self.assertEqual(checkout['children'][1]['children'][0]['id'], task)
        self.assertEqual(checkout['children'][1]['rollup']['blocked'], 1)
        self.assertIsNone(tree['roots'][1]['progress'])
        self.assertEqual(tree['roots'][1]['item_type'], 'research')
        subtree = planning.hierarchy(self.m, root=epic, states=states)
        self.assertEqual([item['id'] for item in subtree['roots']], [epic])
        self.assertEqual(subtree['total'], 5)
        limited = planning.hierarchy(self.m, limit=3, states=states)
        self.assertTrue(limited['truncated'])
        self.assertEqual(limited['total'], 3)
        computed = planning.hierarchy(self.m)
        self.assertEqual(computed['roots'][0]['state'], 'in_progress')
        edges = {(edge['from'], edge['type'], edge['to']) for edge in graph.edges(self.m, [epic])}
        self.assertIn((epic, 'part_of', phase), edges)
        self.assertIn((stories[0], 'part_of', epic), edges)
        nodes = {node['id']: node for node in graph.work_graph(self.m)['nodes']}
        self.assertEqual((nodes[epic]['item_type'], nodes[epic]['parent_id']), ('epic', phase))

    def test_parent_cycles_and_invalid_fields_are_rejected(self):
        phase = self.item('Discovery', item_type='phase')
        epic = self.item('Interviews', item_type='epic', parent_id=phase)
        story = self.item('Interview finance', parent_id=epic)
        with self.assertRaises(InvalidRecord) as caught:
            self.revise(phase, parent_id=story)
        self.assertIn('cycle', str(caught.exception))
        with self.assertRaises(InvalidRecord):
            self.revise(epic, parent_id=epic)
        sprint = planning.save(self.m, 'sprint', payload={'starts_on': '2026-09-01', 'ends_on': '2026-09-14', 'status': 'active',
                                                          'reason': 'The team plans two weeks.'},
                               actor='assistant', evidence=self.evidence, title='Sprint', objective='Plan.', criterion='Done.',
                               request_key=self.key())['episode_id']
        invalid = [{'parent_id': sprint}, {'parent_id': 'episode_missing'}, {'item_type': 'feature'},
                   {'acceptance': ['the cart works']}, {'acceptance': []}, {'acceptance': ['Same.', 'Same.']},
                   {'acceptance': 'The cart works.'}]
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(InvalidRecord):
                self.item('Invalid', **values)
        self.revise(story, parent_id=phase)
        self.assertEqual(planning.latest(self.m, story, 'work_plan')['parent_id'], phase)
        with self.assertRaises(InvalidRecord):
            planning.hierarchy(self.m, root=sprint)


class PlanPathScopeTests(unittest.TestCase):
    """A change to plan paths alone is not a change of scope, so widening the allowed paths lets work continue."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.m = Memory.create(self.root / '.memory' / 'memory.sqlite', 'Scope', ['Keep the parser strict.'])
        codex_host.initialize(self.m)
        source = self.m.source('user', 'Scope', 'The user sets the scope.', 'Repair the parser.', 'user')
        self.evidence = [{'source_id': source['id'], 'reason': 'The user sets the scope.'}]
        self.counter = 0

    def tearDown(self):
        self.m.close()
        self.temp.cleanup()

    def key(self):
        self.counter += 1
        return 'scope-' + str(self.counter)

    def version(self, episode_id):
        return self.m.episode(episode_id)['version']

    def revise(self, episode_id, **changes):
        plan = planning.latest(self.m, episode_id, 'work_plan')
        plan.pop('id')
        return planning.save(self.m, 'work_plan', payload={**plan, **changes}, actor='assistant', evidence=self.evidence,
                             episode_id=episode_id, expected_version=self.version(episode_id), request_key=self.key())

    def work_with_outcome(self):
        payload = {'state': 'ready', 'next_action': 'Repair the parser.', 'scope': 'The parser only.', 'autonomy': 'act',
                   'reason': 'The user asks for it.', 'paths': ['src/parser']}
        episode = planning.save(self.m, 'work_plan', payload=payload, actor='assistant', evidence=self.evidence, title='Parser',
                                objective='Repair the parser.', criterion='The parser tests pass.', subject='code',
                                request_key=self.key())['episode_id']
        decision = self.m.record(episode, 'decision', {'decision': 'Change the decoder.', 'why': 'The test fails.',
                                                       'expected': 'The test passes.', 'reconsider_when': 'The test fails again.'},
                                 expected_version=self.version(episode), request_key=self.key(), actor='assistant', evidence=self.evidence)
        action = self.m.record(episode, 'action', {'action': 'Run the tests.'}, expected_version=decision['version'],
                               request_key=self.key(), actor='assistant', decision_id=decision['id'])
        self.m.record(episode, 'outcome', {'observed': 'The tests pass.', 'assessment': 'good', 'assessment_reason': 'All tests pass.',
                                           'severity': 'none', 'attribution': 'The decoder change.', 'completion': 'complete'},
                      expected_version=action['version'], request_key=self.key(), actor='assistant', decision_id=decision['id'],
                      evidence=self.evidence)
        return episode, decision['id']

    def test_widened_paths_keep_the_decision_current_and_allow_done(self):
        episode, decision = self.work_with_outcome()
        self.assertEqual(self.m.review_reasons(decision), [])
        self.revise(episode, paths=['src/parser', 'docs'])
        self.assertEqual(self.m.review_reasons(decision), [])
        self.assertNotEqual(planning.card(self.m, episode)['state'], 'review')
        self.revise(episode, state='done', paths=['src/parser', 'docs', 'tests'])
        self.assertEqual(planning.latest(self.m, episode, 'work_plan')['state'], 'done')

    def test_changed_scope_still_blocks_done_and_marks_the_decision_for_review(self):
        episode, decision = self.work_with_outcome()
        with self.assertRaises(InvalidRecord) as caught:
            self.revise(episode, state='done', scope='The parser and the lexer.')
        self.assertIn('scope', str(caught.exception))
        self.revise(episode, scope='The parser and the lexer.')
        self.assertEqual(len(self.m.review_reasons(decision)), 1)
        self.assertFalse(planning.scope_changed({'scope': 'a'}, {'scope': 'a', 'paths': ['x']}))
        self.assertFalse(planning.scope_changed({'scope': 'a', 'depends_on': []}, {'scope': 'a'}))
        self.assertTrue(planning.scope_changed({'scope': 'a'}, {'scope': 'b'}))


class ScopeBlockRedeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.m = Memory.create(self.root / '.memory' / 'memory.sqlite', 'Redelivery', ['Keep scope.'])
        codex_host.initialize(self.m)
        source = self.m.source('user', 'Scope', 'The user sets the scope.', 'Change the parser only.', 'user')
        payload = {'state': 'in_progress', 'next_action': 'Edit the parser.', 'scope': 'The parser only.', 'autonomy': 'act',
                   'reason': 'The user asks for it.', 'paths': ['src/parser']}
        self.episode = planning.save(self.m, 'work_plan', payload=payload, actor='assistant',
                                     evidence=[{'source_id': source['id'], 'reason': 'The user sets the scope.'}],
                                     title='Parser', objective='Repair the parser.', criterion='The tests pass.', subject='code',
                                     request_key='plan', session_id='session')['episode_id']

    def tearDown(self):
        self.m.close()
        self.temp.cleanup()

    def event(self, target):
        return {'hook_event_name': 'PreToolUse', 'session_id': 'session', 'turn_id': 'turn-1', 'tool_name': 'Edit',
                'tool_use_id': 'call-1', 'tool_input': {'file_path': str(self.root / target)}, 'cwd': str(self.root)}

    def test_redelivered_call_with_other_targets_reports_the_scope_message(self):
        for target in ('docs/a.md', 'docs/b.md', 'docs/b.md'):
            with self.subTest(target=target), self.assertRaises(guards.ScopeBlocked) as caught:
                codex_host.capture(self.m, self.event(target), host='codex')
            self.assertIn(target, str(caught.exception))
        rows = self.m.db.execute("SELECT payload FROM host_receipts WHERE event_name='ScopeBlocked' ORDER BY rowid").fetchall()
        self.assertEqual([json.loads(row[0])['blocked'] for row in rows], [['docs/a.md'], ['docs/b.md']])
        self.assertEqual(self.m.db.execute("SELECT count(*) FROM host_receipts WHERE event_name='PreToolUse'").fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
