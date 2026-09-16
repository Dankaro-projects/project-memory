"""Tests for typed links, derived edges, lineage and the work dependency graph."""
import shutil
from pathlib import Path
import sqlite3
import tempfile
import unittest

from memory_module import Memory, Conflict, InvalidRecord
from memory_module import codex_host, graph, planning, reviews


def plan_payload(**changes):
    payload = {'state': 'ready', 'next_action': 'Inspect the parser.', 'scope': 'Parser module only.',
               'autonomy': 'act', 'reason': 'The user requests the repair.'}
    payload.update(changes)
    return payload


class GraphFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)
        self.path = self.root / 'memory.sqlite'
        self.m = Memory.create(self.path, 'Graph', ['Keep the parser strict.'])
        self.source = self.m.source('scope', 'Scope', 'User instruction', 'Repair the parser.', 'user', subject='code')
        self.evidence = [{'source_id': self.source['id'], 'reason': 'The user defines the work.'}]
        self.counter = 0

    def tearDown(self):
        self.m.close()
        shutil.rmtree(self.tmp.name, ignore_errors=True)

    def key(self):
        self.counter += 1
        return 'key-' + str(self.counter)

    def work(self, title='Repair parser', **changes):
        result = planning.save(self.m, 'work_plan', payload=plan_payload(**changes), actor='assistant',
                               evidence=self.evidence, title=title, objective='Repair the parser.',
                               criterion='The parser tests pass.', subject='code', request_key=self.key())
        return result['episode_id']

    def version(self, episode_id):
        return self.m.episode(episode_id)['version']

    def record(self, episode_id, kind, payload, **options):
        return self.m.record(episode_id, kind, payload, expected_version=self.version(episode_id),
                             request_key=self.key(), actor='assistant', **options)

    def decision(self, episode_id, text='Use the strict parser.', supersedes=None):
        return self.record(episode_id, 'decision', {'decision': text, 'why': 'The user asks for it.',
                           'expected': 'Parser tests pass.', 'reconsider_when': 'Tests fail.'},
                           evidence=self.evidence, supersedes=supersedes)


class LinkTableTests(GraphFixture):
    def test_initialize_is_idempotent_and_safe_inside_a_write(self):
        with self.m._write():
            graph.initialize(self.m)
            graph.initialize(self.m)
        graph.initialize(self.m)
        self.assertTrue(graph._table_exists(self.m, 'links'))

    def test_link_idempotency_conflict_and_validation(self):
        first = self.work('First')
        second = self.work('Second')
        args = {'from_id': first, 'to_id': second, 'type': 'blocks', 'reason': 'The first work item blocks the second.', 'actor': 'workspace-user'}
        created = graph.link(self.m, request_key='link-1', **args)
        self.assertFalse(created['duplicate'])
        self.assertEqual(graph.link(self.m, request_key='link-1', **args), {'id': created['id'], 'duplicate': True})
        with self.assertRaises(Conflict):
            graph.link(self.m, request_key='link-1', **{**args, 'reason': 'Different content.'})
        with self.assertRaises(Conflict):
            graph.link(self.m, request_key='link-2', **args)
        with self.assertRaises(InvalidRecord):
            graph.link(self.m, request_key='link-3', **{**args, 'type': 'contains'})
        with self.assertRaises(InvalidRecord):
            graph.link(self.m, request_key='link-4', **{**args, 'reason': '   '})
        with self.assertRaises(InvalidRecord):
            graph.link(self.m, request_key='link-5', **{**args, 'to_id': 'event_missing'})
        with self.assertRaises(InvalidRecord):
            graph.link(self.m, request_key='link-6', **{**args, 'to_id': first})
        self.assertEqual(self.m.db.execute('SELECT count(*) FROM links').fetchone()[0], 1)

    def test_retirement_keeps_both_rows_and_removes_the_active_edge(self):
        first = self.work('First')
        second = self.work('Second')
        args = {'from_id': first, 'to_id': second, 'type': 'depends_on', 'actor': 'workspace-user'}
        created = graph.link(self.m, reason='The second item needs the first.', request_key='a', **args)
        active = [edge for edge in graph.edges(self.m, [first]) if edge['origin'] == 'link']
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]['link_id'], created['id'])
        with self.assertRaises(InvalidRecord):
            graph.link(self.m, from_id=first, to_id=second, type='blocks', reason='Wrong type.', actor='user', request_key='b', retire=created['id'])
        retired = graph.link(self.m, reason='The dependency no longer applies.', request_key='c', retire=created['id'], **args)
        self.assertEqual(self.m.db.execute('SELECT count(*) FROM links').fetchone()[0], 2)
        self.assertEqual([edge for edge in graph.edges(self.m, [first]) if edge['origin'] == 'link'], [])
        with self.assertRaises(Conflict):
            graph.link(self.m, reason='Retire twice.', request_key='d', retire=created['id'], **args)
        with self.assertRaises(InvalidRecord):
            graph.link(self.m, reason='Retire a retirement.', request_key='e', retire=retired['id'], **args)
        again = graph.link(self.m, reason='The dependency applies again.', request_key='f', **args)
        self.assertEqual([edge['link_id'] for edge in graph.edges(self.m, [second]) if edge['origin'] == 'link'], [again['id']])

    def test_links_are_immutable(self):
        first = self.work('First')
        second = self.work('Second')
        graph.link(self.m, from_id=first, to_id=second, type='relates_to', reason='Shared parser.', actor='user', request_key='a')
        with self.assertRaises(sqlite3.DatabaseError):
            self.m.db.execute("UPDATE links SET reason='changed'")
        with self.assertRaises(sqlite3.DatabaseError):
            self.m.db.execute('DELETE FROM links')

    def test_component_and_package_endpoints(self):
        episode = self.work()
        component = graph.node(self.m, 'component:memory_module/core')
        self.assertEqual((component['kind'], component['title']), ('component', 'memory_module/core'))
        self.assertEqual(graph.node(self.m, 'component:.')['title'], 'Graph')
        package = graph.node(self.m, 'package:maven:org.example:parser')
        self.assertEqual((package['kind'], package['title']), ('package', 'org.example:parser'))
        for bad in ('component:/abs', 'component:a/../b', 'component:./a', 'component:a/', 'component:', 'component:a\\b',
                    'package:pypi:', 'package::x', 'package:pypi:two words', 'thing_1', 'direction_x', 'direction_4'):
            with self.subTest(bad=bad), self.assertRaises(InvalidRecord):
                graph.node(self.m, bad)
        graph.link(self.m, from_id=episode, to_id='component:memory_module', type='affects_component',
                   reason='The work changes this component.', actor='assistant', request_key='c')
        graph.link(self.m, from_id='component:memory_module', to_id='package:pypi:requests', type='depends_on',
                   reason='The component imports this package.', actor='assistant', request_key='p')
        found = {(edge['from'], edge['type'], edge['to']) for edge in graph.edges(self.m, ['component:memory_module'])}
        self.assertEqual(found, {(episode, 'affects_component', 'component:memory_module'),
                                 ('component:memory_module', 'depends_on', 'package:pypi:requests')})
        reached = graph.lineage(self.m, 'package:pypi:requests', depth=2)
        self.assertIn(episode, {item['id'] for item in reached['nodes']})
        with self.assertRaises(InvalidRecord):
            graph.link(self.m, from_id=episode, to_id='component:../outside', type='affects_component',
                       reason='Outside the project.', actor='assistant', request_key='x')


class DerivedEdgeTests(GraphFixture):
    def test_every_derived_edge_type_from_a_real_history(self):
        episode = self.work()
        plan_id = planning.latest(self.m, episode, 'work_plan')['id']
        first = self.decision(episode)
        action = self.record(episode, 'action', {'action': 'Run the parser tests.'}, decision_id=first['id'])
        result = self.record(episode, 'action_result', {'execution_status': 'completed', 'summary': 'Tests ran.'}, decision_id=first['id'])
        outcome = self.record(episode, 'outcome', {'observed': 'Tests pass.', 'assessment': 'good', 'assessment_reason': 'All pass.',
                              'severity': 'none', 'attribution': 'The parser change.'}, decision_id=first['id'], evidence=self.evidence)
        follow = self.record(episode, 'follow_up', {'review_after': '2030-01-01T00:00:00+00:00', 'owner': 'user', 'reason': 'Check again.'}, decision_id=first['id'])
        second = self.decision(episode, 'Keep the strict parser.', supersedes=first['id'])
        note = self.record(episode, 'note', {'text': 'The decision informs this note.'}, links=[{'event_id': second['id'], 'reason': 'The note follows the decision.'}])
        lesson = self.record(episode, 'lesson', {'when': 'Parsing input.', 'do': 'Stay strict.', 'because': 'Tests pass.', 'exceptions': 'Legacy endpoint.'}, evidence=self.evidence)
        review = self.record(episode, 'lesson_review', {'lesson_id': lesson['id'], 'status': 'accepted', 'reason': 'The user agrees.'},
                             evidence=self.evidence, links=[{'event_id': lesson['id'], 'reason': 'This review accepts the lesson.'}])
        dependent = self.work('Dependent', depends_on=[{'episode_id': episode, 'reason': 'The parser must be repaired first.'}])
        reviews.configure(self.m, self.root, 'codex')
        runs = {}
        for name, role, parent in (('outcome', 'outcome', None), ('work', 'work', None), ('rerouted', 'work', 'work'), ('work_review', 'work_review', 'rerouted')):
            runs[name] = 'check_' + name
            self.m.db.execute('INSERT INTO review_runs (id,episode_id,role,signature,host,session_id,state,created_at,updated_at,request_key,snapshot,parent_run) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                              (runs[name], episode, role, 'sig', 'codex', 's', 'passed', self.m.now(), self.m.now(), 'run-' + name, '{}', runs.get(parent)))
        codex_host.initialize(self.m)
        with self.m._write():
            blocked = codex_host.receipt(self.m, session_id='session', event_name='ScopeBlocked', tool_name='Edit', episode_id=episode,
                                         payload={'episode_id': episode, 'plan_id': plan_id, 'blocked': ['README.md']}, key='blocked-1')
        explicit = graph.link(self.m, from_id=note['id'], to_id='component:memory_module', type='annotates', reason='The note describes the module.', actor='user', request_key='l')

        found = graph.edges(self.m, [episode], limit=500)
        found += graph.edges(self.m, [first['id'], second['id'], lesson['id'], note['id'], runs['rerouted'], runs['work_review']], limit=500)
        observed = {(edge['from'], edge['type'], edge['to'], edge['origin']) for edge in found}
        expected = {
            (self.source['id'], 'supports', first['id'], 'evidence'),
            (second['id'], 'informs', note['id'], 'event_link'),
            (first['id'], 'action', action['id'], 'decision'),
            (first['id'], 'action_result', result['id'], 'decision'),
            (first['id'], 'outcome', outcome['id'], 'decision'),
            (first['id'], 'follow_up', follow['id'], 'decision'),
            (first['id'], 'revised_by', second['id'], 'revision'),
            (dependent, 'depends_on', episode, 'plan'),
            (episode, 'contains', first['id'], 'episode'),
            (episode, 'contains', plan_id, 'episode'),
            ('direction_0', 'governs', first['id'], 'direction'),
            (plan_id, 'plans', first['id'], 'plan'),
            (review['id'], 'reviews', lesson['id'], 'review'),
            (episode, 'checked_by', runs['outcome'], 'run'),
            (episode, 'checked_by', runs['work_review'], 'run'),
            (episode, 'delegated_to', runs['work'], 'run'),
            (runs['work'], 'rerouted_to', runs['rerouted'], 'run'),
            (runs['rerouted'], 'reviewed_by', runs['work_review'], 'run'),
            (episode, 'blocked_edit', blocked, 'scope'),
            (note['id'], 'annotates', 'component:memory_module', 'link'),
        }
        self.assertLessEqual(expected, observed)
        self.assertNotIn((episode, 'contains', action['id'], 'episode'), observed)
        self.assertEqual({edge[3] for edge in observed}, {'evidence', 'event_link', 'decision', 'revision', 'plan', 'episode', 'direction', 'review', 'run', 'scope', 'link'})
        single = graph.edges(self.m, [episode])
        self.assertEqual(len({edge['id'] for edge in single}), len(single))
        self.assertEqual(single, graph.edges(self.m, [episode]))
        self.assertEqual(len(graph.edges(self.m, [episode], limit=2)), 2)
        self.assertIn(explicit['id'], {edge.get('link_id') for edge in graph.edges(self.m, [note['id']])})
        for endpoint in (episode, first['id'], self.source['id'], blocked, runs['work'], 'direction_0'):
            described = graph.node(self.m, endpoint)
            self.assertEqual(set(described), {'id', 'kind', 'title', 'status', 'subject', 'date', 'episode_id'})
        self.assertEqual(graph.node(self.m, first['id'])['title'], 'Use the strict parser.')
        self.assertEqual(graph.node(self.m, plan_id)['title'], 'Inspect the parser.')
        self.assertEqual(graph.node(self.m, runs['work'])['kind'], 'check')
        self.assertEqual(graph.node(self.m, blocked)['episode_id'], episode)

    def test_invalid_arguments(self):
        with self.assertRaises(InvalidRecord):
            graph.edges(self.m, 'episode_x')
        with self.assertRaises(InvalidRecord):
            graph.edges(self.m, ['episode_x'], limit=0)
        self.assertEqual(graph.edges(self.m, []), [])
        with self.assertRaises(InvalidRecord):
            graph.lineage(self.m, 'episode_missing')
        with self.assertRaises(InvalidRecord):
            graph.work_graph(self.m, states=['ready'])


class LineageTests(GraphFixture):
    def test_lineage_is_bounded_by_node_limit_and_reports_truncation(self):
        episode = self.work()
        for number in range(8):
            self.record(episode, 'note', {'text': 'Note ' + str(number)})
        small = graph.lineage(self.m, episode, depth=1, limit=5)
        self.assertTrue(small['truncated'])
        self.assertEqual(len(small['nodes']), 5)
        ids = {item['id'] for item in small['nodes']}
        self.assertTrue(all(edge['from'] in ids and edge['to'] in ids for edge in small['edges']))
        self.assertEqual(small['nodes'][0]['id'], episode)
        full = graph.lineage(self.m, episode, depth=1, limit=80)
        self.assertFalse(full['truncated'])
        # The episode, its plan and eight notes. The plan evidence source is at depth 2.
        self.assertEqual(len(full['nodes']), 10)

    def test_lineage_follows_depth_in_both_directions(self):
        episode = self.work()
        decision = self.decision(episode)
        action = self.record(episode, 'action', {'action': 'Run the tests.'}, decision_id=decision['id'])
        one = {item['id'] for item in graph.lineage(self.m, self.source['id'], depth=1)['nodes']}
        self.assertIn(decision['id'], one)
        self.assertNotIn(action['id'], one)
        two = graph.lineage(self.m, self.source['id'], depth=2)
        self.assertIn(action['id'], {item['id'] for item in two['nodes']})
        self.assertIn(episode, {item['id'] for item in two['nodes']})
        backwards = {item['id'] for item in graph.lineage(self.m, action['id'], depth=2)['nodes']}
        self.assertIn(self.source['id'], backwards)
        self.assertEqual(set(two), {'focus', 'nodes', 'edges', 'truncated'})


class WorkGraphTests(GraphFixture):
    def test_work_graph_on_a_dependency_chain(self):
        first = self.work('First')
        second = self.work('Second', depends_on=[{'episode_id': first, 'reason': 'Second needs first.'}])
        third = self.work('Third', priority='high', depends_on=[{'episode_id': second, 'reason': 'Third needs second.'}])
        loose = self.m.start('Loose', 'Collect notes.', 'action', 'Notes exist.', 'code')['id']
        planning.save(self.m, 'sprint', payload={'starts_on': '2026-09-01', 'ends_on': '2026-09-14', 'status': 'active', 'reason': 'Sprint.'},
                      actor='user', evidence=self.evidence, title='Sprint', objective='Plan.', criterion='Done.', request_key=self.key())
        graph.link(self.m, from_id=loose, to_id=first, type='blocks', reason='Notes are needed first.', actor='user', request_key='blocks')
        graph.link(self.m, from_id=loose, to_id='component:.', type='affects_component', reason='Not a work edge.', actor='user', request_key='component')
        result = graph.work_graph(self.m, states={second: 'blocked'})
        self.assertFalse(result['truncated'])
        nodes = {item['id']: item for item in result['nodes']}
        self.assertEqual(set(nodes), {first, second, third, loose})
        self.assertEqual(nodes[first]['state'], 'ready')
        self.assertEqual(nodes[second]['state'], 'blocked')
        self.assertEqual(nodes[loose]['state'], 'backlog')
        self.assertEqual(nodes[third]['priority'], 'high')
        self.assertEqual(nodes[loose]['priority'], 'normal')
        self.assertEqual({(edge['from'], edge['type'], edge['to'], edge['origin']) for edge in result['edges']},
                         {(second, 'depends_on', first, 'plan'), (third, 'depends_on', second, 'plan'), (loose, 'blocks', first, 'link')})
        limited = graph.work_graph(self.m, limit=2)
        self.assertTrue(limited['truncated'])
        self.assertEqual(len(limited['nodes']), 2)
        kept = {item['id'] for item in limited['nodes']}
        self.assertTrue(all(edge['from'] in kept and edge['to'] in kept for edge in limited['edges']))


class ReadOnlyTests(GraphFixture):
    def test_read_functions_work_on_a_read_only_database_without_links(self):
        episode = self.work()
        decision = self.decision(episode)
        dependent = self.work('Dependent', depends_on=[{'episode_id': episode, 'reason': 'Needs the parser.'}])
        writer_edges = graph.edges(self.m, [episode])
        writer_lineage = graph.lineage(self.m, decision['id'])
        writer_work = graph.work_graph(self.m)
        self.assertFalse(graph._table_exists(self.m, 'links'))
        self.m.close()
        with Memory(self.path, read_only=True) as reader:
            self.assertEqual(graph.node(reader, decision['id'])['kind'], 'decision')
            self.assertEqual(graph.node(reader, 'component:.')['kind'], 'component')
            self.assertEqual(graph.node(reader, 'direction_0')['status'], 'current')
            with self.assertRaises(InvalidRecord):
                graph.node(reader, 'host_missing')
            with self.assertRaises(InvalidRecord):
                graph.node(reader, 'check_missing')
            self.assertEqual(graph.edges(reader, [episode]), writer_edges)
            self.assertEqual(graph.lineage(reader, decision['id']), writer_lineage)
            self.assertEqual(graph.work_graph(reader), writer_work)
            self.assertIn((dependent, episode), {(edge['from'], edge['to']) for edge in writer_work['edges']})
            self.assertFalse(graph._table_exists(reader, 'links'))
            with self.assertRaises((sqlite3.DatabaseError, InvalidRecord)):
                graph.link(reader, from_id=episode, to_id=dependent, type='blocks', reason='Read only.', actor='user', request_key='ro')
        self.m = Memory(self.path)


if __name__ == '__main__':
    unittest.main()
