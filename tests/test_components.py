"""Tests for authored components, their links and the authored architecture layer."""
import shutil
from pathlib import Path
import tempfile
import unittest

from memory_module import Memory, arch_authored, graph, planning
from memory_module.architecture import component, components, model, save_component
from memory_module.core import Conflict, InvalidRecord


def by_id(result):
    return {node['id']: node for node in result['nodes']}


class ComponentFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name).resolve()
        self.path = self.root / '.memory' / 'memory.sqlite'
        self.m = Memory.create(self.path, 'Engagement', ['Keep client evidence traceable.'])
        self.source = self.m.source('interview', 'Interview', 'Interview notes', 'The finance lead owns the budget.', 'user')
        self.counter = 0

    def tearDown(self):
        self.m.close()
        shutil.rmtree(self.temp.name, ignore_errors=True)

    def key(self):
        self.counter += 1
        return 'key-' + str(self.counter)

    def save(self, actor='assistant', **values):
        data = {'title': 'Finance team', 'kind': 'stakeholder', 'description': 'The finance team approves the budget.',
                'status': 'proposed', 'actor': actor, 'request_key': self.key()}
        data.update(values)
        return save_component(self.m, **data)

    def work(self, title='Budget review'):
        payload = {'state': 'ready', 'next_action': 'Interview the finance lead.', 'scope': 'Budget questions only.',
                   'autonomy': 'suggest', 'reason': 'The client asks for it.', 'item_type': 'research'}
        return planning.save(self.m, 'work_plan', payload=payload, actor='assistant',
                             evidence=[{'source_id': self.source['id'], 'reason': 'The interview defines the question.'}],
                             title=title, objective='Understand the budget process.', criterion='The budget owner is confirmed.',
                             subject='research', request_key=self.key())['episode_id']


class NamespaceTests(ComponentFixture):
    def test_authored_ids_do_not_take_over_project_folders(self):
        (self.root / 'billing').mkdir()
        (self.root / 'billing' / 'invoice.py').write_text('VALUE = 1\n')
        (self.root / 'deliverables').mkdir()
        (self.root / 'deliverables' / 'README.md').write_text('# Deliverables\n')
        report = self.save(title='Billing', kind='deliverable', path='deliverables')
        self.assertEqual(report['id'], 'component:billing-deliverable')
        stream = self.save(title='Deliverables', kind='workstream')
        self.assertEqual(stream['id'], 'component:deliverables-workstream')
        with self.assertRaisesRegex(InvalidRecord, 'already names the project folder'):
            self.save(component_id='billing', title='Billing', kind='deliverable')
        nodes = by_id(model(self.m))
        self.assertEqual(nodes['component:billing']['layer'], 'code')
        self.assertNotIn('authored', nodes['component:billing'])
        self.assertEqual(graph.node(self.m, 'component:billing')['subject'], 'code')
        folder = graph.node(self.m, 'component:deliverables')
        self.assertEqual((folder['title'], folder['status'], folder['subject']), ('deliverables', None, 'general'))
        described = self.save(title='Billing', kind='component', path='billing')
        self.assertEqual(described['id'], 'component:billing')
        self.assertEqual(by_id(model(self.m))['component:billing']['authored']['id'], 'component:billing')

    def test_a_planned_deliverable_attaches_work_before_its_file_exists(self):
        payload = {'state': 'ready', 'next_action': 'Draft the report.', 'scope': 'The pricing report only.',
                   'autonomy': 'suggest', 'reason': 'The client asks for it.', 'item_type': 'deliverable', 'paths': ['deliverables/**']}
        episode = planning.save(self.m, 'work_plan', payload=payload, actor='assistant',
                                evidence=[{'source_id': self.source['id'], 'reason': 'The interview defines the report.'}],
                                title='Pricing report', objective='Draft the pricing report.', criterion='The client accepts the report.',
                                subject='writing', request_key=self.key())['episode_id']
        self.save(title='Pricing report', kind='deliverable', path='deliverables/report.md')
        node = by_id(model(self.m))['component:pricing-report']
        self.assertEqual((node['work'], node['work_total']), ([episode], 1))


class SaveComponentTests(ComponentFixture):
    def test_agent_proposes_and_only_the_user_confirms(self):
        created = self.save()
        self.assertEqual((created['id'], created['status'], created['version'], created['actor']),
                         ('component:finance-team', 'proposed', 1, 'assistant'))
        self.assertFalse(created['duplicate'])
        with self.assertRaises(InvalidRecord) as caught:
            self.save(component_id='component:finance-team', status='confirmed')
        self.assertIn('Only the user can confirm', str(caught.exception))
        with self.assertRaises(InvalidRecord):
            self.save(title='Procurement', status='confirmed')
        edited = self.save(component_id='finance-team', description='The finance team approves the budget and the timeline.')
        self.assertEqual((edited['version'], edited['status']), (2, 'proposed'))
        confirmed = self.save(actor='workspace-user', component_id='component:finance-team', status='confirmed',
                              description=edited['description'])
        self.assertEqual((confirmed['status'], confirmed['version'], confirmed['actor']), ('confirmed', 3, 'workspace-user'))
        for status in ('confirmed', 'proposed', 'retired'):
            with self.subTest(status=status), self.assertRaises(InvalidRecord) as caught:
                self.save(component_id='component:finance-team', status=status, description='An agent changes the text.')
            self.assertIn('only the user can change it', str(caught.exception))
        retired = self.save(actor='workspace-user', component_id='component:finance-team', status='retired',
                            description=edited['description'])
        self.assertEqual(retired['status'], 'retired')
        history = self.m.db.execute("SELECT version FROM sources WHERE source_key='component:finance-team' ORDER BY version").fetchall()
        self.assertEqual([row[0] for row in history], [1, 2, 3, 4])

    def test_request_keys_are_idempotent_and_unchanged_content_adds_no_version(self):
        data = {'title': 'Data warehouse', 'kind': 'system', 'description': 'The warehouse stores the sales data.',
                'status': 'proposed', 'actor': 'assistant', 'request_key': 'same'}
        first = save_component(self.m, **data)
        again = save_component(self.m, **data)
        self.assertTrue(again['duplicate'])
        self.assertEqual(again['version'], first['version'])
        with self.assertRaises(Conflict):
            save_component(self.m, **{**data, 'description': 'Different text.'})
        repeat = save_component(self.m, **{**data, 'component_id': first['id'], 'request_key': 'other'})
        self.assertFalse(repeat['changed'])
        self.assertEqual(repeat['version'], 1)
        self.assertEqual(repeat['revisions'], 2)

    def test_slugs_collisions_and_validation(self):
        first = self.save(title='Sales & Ops: Q3 plan!')
        self.assertEqual(first['id'], 'component:sales-ops-q3-plan')
        second = self.save(title='Sales & Ops: Q3 plan!')
        self.assertEqual(second['id'], 'component:sales-ops-q3-plan-2')
        evidence = [{'source_id': self.source['id'], 'reason': 'The interview names the owner.'}]
        with_evidence = self.save(title='Budget owner', evidence=evidence)
        self.assertEqual(with_evidence['evidence'], evidence)
        invalid = [
            {'kind': 'person'},
            {'status': 'draft'},
            {'component_id': 'component:Not Valid'},
            {'component_id': 'component:a--b'},
            {'path': '../outside'},
            {'path': '/absolute'},
            {'title': '   '},
            {'evidence': [{'source_id': 'source_missing', 'reason': 'Missing.'}]},
            {'evidence': [{'source_id': self.source['id']}]},
        ]
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(InvalidRecord):
                self.save(**values)
        with self.assertRaises(InvalidRecord):
            component(self.m, 'component:missing')


class ComponentGraphTests(ComponentFixture):
    def test_graph_node_resolves_authored_components_and_path_components(self):
        self.save(title='Billing service', kind='service', description='The service sends invoices.')
        described = graph.node(self.m, 'component:billing-service')
        self.assertEqual((described['kind'], described['title'], described['status'], described['subject']),
                         ('component', 'Billing service', 'proposed', 'general'))
        path_component = graph.node(self.m, 'component:src/billing')
        self.assertEqual((path_component['title'], path_component['status']), ('src/billing', None))

    def test_new_link_types_relate_components_and_work(self):
        team = self.save()['id']
        report = self.save(title='Budget report', kind='deliverable', description='The report summarises the budget findings.')['id']
        workstream = self.save(title='Cost workstream', kind='workstream', description='The workstream analyses costs.')['id']
        warehouse = self.save(title='Warehouse', kind='dataset', description='The dataset holds the cost data.')['id']
        episode = self.work()
        for number, (source, target, kind) in enumerate([(team, report, 'owns'), (workstream, report, 'produces'),
                                                         (report, workstream, 'part_of'), (workstream, warehouse, 'uses'),
                                                         (warehouse, report, 'informs'), (episode, report, 'implements')]):
            graph.link(self.m, from_id=source, to_id=target, type=kind, reason='The engagement plan defines this relation.',
                       actor='assistant', request_key='link-' + str(number))
        found = {(edge['from'], edge['type'], edge['to']) for edge in graph.edges(self.m, [report])}
        self.assertLessEqual({(team, 'owns', report), (workstream, 'produces', report), (report, 'part_of', workstream),
                              (warehouse, 'informs', report), (episode, 'implements', report)}, found)
        lineage = graph.lineage(self.m, episode, depth=2)
        titles = {node['title'] for node in lineage['nodes']}
        self.assertIn('Budget report', titles)
        described = component(self.m, report)
        self.assertEqual(described['links_total'], 5)
        self.assertEqual(described['work'], [episode])
        listing = components(self.m, kind='deliverable')
        self.assertEqual([item['id'] for item in listing['components']], [report])
        self.assertEqual(components(self.m, status='proposed')['total'], 4)
        self.assertEqual(components(self.m, status='confirmed')['total'], 0)
        with self.assertRaises(InvalidRecord):
            components(self.m, status='draft')


class AuthoredLayerTests(ComponentFixture):
    def test_authored_nodes_links_status_and_merge_with_code_components(self):
        (self.root / 'billing').mkdir()
        (self.root / 'billing' / 'invoice.py').write_text('import json\n', encoding='utf-8')
        team = self.save()['id']
        report = self.save(title='Budget report', kind='deliverable', description='The report summarises the findings.',
                           path='deliverables')['id']
        billing = self.save(title='Billing service', kind='service', description='The service sends invoices.', path='billing')['id']
        retired = self.save(actor='workspace-user', title='Old portal', kind='system', description='The portal was replaced.',
                            status='retired')['id']
        episode = self.work()
        graph.link(self.m, from_id=team, to_id=report, type='owns', reason='The team owns the report.', actor='assistant', request_key='l1')
        graph.link(self.m, from_id=report, to_id=billing, type='uses', reason='The report uses billing data.', actor='assistant', request_key='l2')
        graph.link(self.m, from_id=episode, to_id=team, type='relates_to', reason='The review interviews the team.', actor='assistant', request_key='l3')
        result = model(self.m, work_states={episode: 'in_progress'})
        nodes = by_id(result)
        self.assertEqual(result['layers'], ['code', 'n8n', 'authored'])
        self.assertEqual((nodes[team]['kind'], nodes[team]['layer'], nodes[team]['flags']), ('stakeholder', 'authored', ['proposed']))
        self.assertEqual(nodes[team]['work'], [episode])
        self.assertEqual(nodes[team]['status'], 'in_progress')
        self.assertEqual(nodes[retired]['flags'], ['retired'])
        self.assertNotIn(billing, nodes)
        self.assertEqual(nodes['component:billing']['authored']['id'], billing)
        self.assertEqual(nodes['component:billing']['layer'], 'code')
        edges = {(edge['from'], edge['to'], edge['type']): edge for edge in result['edges']}
        self.assertEqual(edges[(team, report, 'owns')]['layer'], 'authored')
        self.assertIn((report, 'component:billing', 'uses'), edges)
        self.assertFalse(any(edge['to'] == team and edge['from'] == episode for edge in result['edges']))

        authored_only = by_id(model(self.m, layers=['authored']))
        self.assertEqual(set(authored_only), {team, report, billing, retired})
        self.assertEqual(authored_only[billing]['layer'], 'authored')
        code_only = by_id(model(self.m, layers=['code']))
        self.assertNotIn(team, code_only)
        self.assertNotIn('authored', code_only['component:billing'])

    def test_plan_paths_attach_work_to_authored_components_with_paths(self):
        (self.root / 'deliverables').mkdir()
        (self.root / 'deliverables' / 'report.md').write_text('# Report\n', encoding='utf-8')
        report = self.save(title='Budget report', kind='deliverable', description='The report summarises the findings.',
                           path='deliverables')['id']
        payload = {'state': 'ready', 'next_action': 'Draft the report.', 'scope': 'The report only.', 'autonomy': 'suggest',
                   'reason': 'The client needs the report.', 'paths': ['deliverables/**'], 'item_type': 'deliverable'}
        episode = planning.save(self.m, 'work_plan', payload=payload, actor='assistant',
                                evidence=[{'source_id': self.source['id'], 'reason': 'The client asks for the report.'}],
                                title='Write report', objective='Write the budget report.', criterion='The client accepts it.',
                                subject='writing', request_key=self.key())['episode_id']
        nodes = by_id(model(self.m, layers=['authored']))
        self.assertEqual(nodes[report]['work'], [episode])

    def test_read_only_connection_lists_components_and_the_model(self):
        team = self.save()['id']
        self.m.close()
        with Memory(self.path, read_only=True) as reader:
            self.assertEqual(components(reader)['total'], 1)
            self.assertIn(team, by_id(model(reader)))
            self.assertEqual(graph.node(reader, team)['title'], 'Finance team')
        self.m = Memory(self.path)
        with Memory(self.path, read_only=True) as reader:
            self.assertEqual(arch_authored._revision_details(reader, 'component:missing')['revisions'], 0)


if __name__ == '__main__':
    unittest.main()
