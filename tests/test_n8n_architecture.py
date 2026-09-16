"""Tests for the n8n workflow layer of the architecture model."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from memory_module import Memory, arch_n8n, architecture, graph, planning
from memory_module.architecture import credential_service, model, node_service
from memory_module.core import InvalidRecord

SECRET = 'sk-live-SECRET-VALUE-123'

LEAD_INTAKE = {
    'name': 'Lead intake',
    'id': 'wf-lead',
    'active': True,
    'nodes': [
        {'parameters': {'path': 'lead', 'httpMethod': 'POST'}, 'id': 'n1', 'name': 'Webhook',
         'type': 'n8n-nodes-base.webhook', 'typeVersion': 2, 'position': [0, 0], 'webhookId': 'hook-1'},
        {'parameters': {'rule': {'interval': [{'field': 'days'}]}}, 'id': 'n2', 'name': 'Every morning',
         'type': 'n8n-nodes-base.scheduleTrigger', 'typeVersion': 1.2, 'position': [0, 200]},
        {'parameters': {'modelId': {'__rl': True, 'value': 'gpt-4o-mini', 'mode': 'list'}, 'messages': {'values': [{'content': '=Summarise {{ $json.body }}'}]}},
         'id': 'n3', 'name': 'Summarise lead', 'type': '@n8n/n8n-nodes-langchain.openAi', 'typeVersion': 1.8,
         'position': [200, 0], 'credentials': {'openAiApi': {'id': 'cred-openai', 'name': 'OpenAI account'}}},
        {'parameters': {'model': 'gpt-4o-mini'}, 'id': 'n4', 'name': 'OpenAI Chat Model',
         'type': '@n8n/n8n-nodes-langchain.lmChatOpenAi', 'typeVersion': 1, 'position': [400, 200],
         'credentials': {'openAiApi': {'id': 'cred-openai', 'name': 'OpenAI account'}}},
        {'parameters': {'promptType': 'define', 'text': '=Qualify the lead.'}, 'id': 'n5', 'name': 'Qualify',
         'type': '@n8n/n8n-nodes-langchain.agent', 'typeVersion': 1.7, 'position': [400, 0]},
        {'parameters': {'channel': '#sales', 'text': '=New lead {{ $json.name }}'}, 'id': 'n6', 'name': 'Notify sales',
         'type': 'n8n-nodes-base.slack', 'typeVersion': 2.2, 'position': [600, 0],
         'credentials': {'slackOAuth2Api': {'id': 'cred-slack', 'name': 'Sales Slack'}}},
        {'parameters': {'url': 'https://crm.example.invalid/api/leads', 'authentication': 'genericCredentialType',
                        'genericAuthType': 'httpHeaderAuth', 'sendHeaders': True,
                        'headerParameters': {'parameters': [{'name': 'X-Api-Key', 'value': SECRET}]}},
         'id': 'n7', 'name': 'Call CRM', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [200, 200],
         'credentials': {'httpHeaderAuth': {'id': 'cred-crm', 'name': 'CRM header'}}},
        {'parameters': {'source': 'database', 'workflowId': {'__rl': True, 'value': 'wf-enrich', 'mode': 'list',
                                                            'cachedResultName': 'Enrich lead'}},
         'id': 'n8', 'name': 'Enrich', 'type': 'n8n-nodes-base.executeWorkflow', 'typeVersion': 1.1, 'position': [600, 200]},
        {'parameters': {'workflowId': '={{ $json.target }}'}, 'id': 'n9', 'name': 'Dynamic call',
         'type': 'n8n-nodes-base.executeWorkflow', 'typeVersion': 1, 'position': [800, 200], 'disabled': True},
        {'parameters': {'content': 'Remember to rotate the key.'}, 'id': 'n10', 'name': 'Sticky Note',
         'type': 'n8n-nodes-base.stickyNote', 'typeVersion': 1, 'position': [0, 400]},
    ],
    'connections': {
        'Webhook': {'main': [[{'node': 'Summarise lead', 'type': 'main', 'index': 0}]]},
        'Summarise lead': {'main': [[{'node': 'Qualify', 'type': 'main', 'index': 0}]]},
        'OpenAI Chat Model': {'ai_languageModel': [[{'node': 'Qualify', 'type': 'ai_languageModel', 'index': 0}]]},
        'Qualify': {'main': [[{'node': 'Notify sales', 'type': 'main', 'index': 0}, {'node': 'Enrich', 'type': 'main', 'index': 0}]]},
        'Every morning': {'main': [[{'node': 'Call CRM', 'type': 'main', 'index': 0}]]},
        'Enrich': {'main': [[{'node': 'Dynamic call', 'type': 'main', 'index': 0}]]},
    },
    'settings': {'executionOrder': 'v1'},
    'pinData': {},
}

ENRICH = {
    'name': 'Enrich lead',
    'id': 'wf-enrich',
    'nodes': [
        {'parameters': {}, 'name': 'When called', 'type': 'n8n-nodes-base.executeWorkflowTrigger', 'typeVersion': 1},
        {'parameters': {'operation': 'append'}, 'name': 'Log to sheet', 'type': 'n8n-nodes-base.googleSheets', 'typeVersion': 4.5,
         'credentials': {'googleSheetsOAuth2Api': {'id': 'cred-sheets', 'name': 'Operations sheets'}}},
    ],
    'connections': {'When called': {'main': [[{'node': 'Log to sheet', 'type': 'main', 'index': 0}]]}},
}

NIGHTLY = {
    'name': 'Nightly report',
    'nodes': [
        {'parameters': {}, 'name': 'Midnight', 'type': 'n8n-nodes-base.cron', 'typeVersion': 1},
        {'parameters': {'workflowId': {'__rl': True, 'value': '', 'mode': 'list', 'cachedResultName': 'Enrich lead'}},
         'name': 'Run enrichment', 'type': 'n8n-nodes-base.executeWorkflow', 'typeVersion': 1.1},
    ],
    'connections': {'Midnight': {'main': [[{'node': 'Run enrichment', 'type': 'main', 'index': 0}]]}},
}

LEAD = 'component:n8n:workflows/lead-intake.json'
ENRICH_ID = 'component:n8n:workflows/enrich.json'
NIGHTLY_ID = 'component:n8n:workflows/nightly.json'


def write(root, name, content):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content if isinstance(content, str) else json.dumps(content, indent=2), encoding='utf-8')
    return path


def by_id(result):
    return {node['id']: node for node in result['nodes']}


def edge_map(result):
    return {(edge['from'], edge['to'], edge['type']): edge for edge in result['edges']}


class N8nFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        write(self.root, 'workflows/lead-intake.json', LEAD_INTAKE)
        write(self.root, 'workflows/enrich.json', ENRICH)
        write(self.root, 'workflows/nightly.json', NIGHTLY)
        write(self.root, 'package.json', {'name': 'automation', 'dependencies': {}})
        write(self.root, 'data/nodes.json', {'nodes': [1, 2], 'other': True})
        write(self.root, 'data/broken.json', '{"nodes": [], "connections": ')
        write(self.root, 'scripts/check.py', 'import json\n')
        self.m = Memory.create(self.root / '.memory' / 'memory.sqlite', 'Automation', ['Keep client data safe.'])

    def tearDown(self):
        self.m.close()
        self.temp.cleanup()


class ServiceNameTests(unittest.TestCase):
    def test_node_types_and_credentials_become_service_names(self):
        cases = {
            'n8n-nodes-base.slack': 'slack',
            'n8n-nodes-base.slackTrigger': 'slack',
            'n8n-nodes-base.googleSheets': 'google-sheets',
            'n8n-nodes-base.hubspot': 'hubspot',
            'n8n-nodes-base.emailSend': 'smtp',
            '@n8n/n8n-nodes-langchain.openAi': 'openai',
            '@n8n/n8n-nodes-langchain.lmChatOpenAi': 'openai',
            '@n8n/n8n-nodes-langchain.lmChatAzureOpenAi': 'azure-openai',
            '@n8n/n8n-nodes-langchain.lmChatGoogleGemini': 'google-gemini',
            '@n8n/n8n-nodes-langchain.memoryPostgresChat': 'postgres',
            '@n8n/n8n-nodes-langchain.vectorStorePinecone': 'pinecone',
            '@n8n/n8n-nodes-langchain.toolSerpApi': 'serpapi',
            'n8n-nodes-base.httpRequest': None,
            'n8n-nodes-base.webhook': None,
            'n8n-nodes-base.scheduleTrigger': None,
            'n8n-nodes-base.executeWorkflowTrigger': None,
            '@n8n/n8n-nodes-langchain.agent': None,
            '@n8n/n8n-nodes-langchain.outputParserStructured': None,
            'not-a-type': None,
        }
        for node_type, expected in cases.items():
            with self.subTest(node_type=node_type):
                self.assertEqual(node_service(node_type), expected)
        self.assertEqual(credential_service('googleSheetsOAuth2Api'), 'google-sheets')
        self.assertEqual(credential_service('openAiApi'), 'openai')
        self.assertEqual(credential_service('slackOAuth2Api'), 'slack')
        self.assertIsNone(credential_service('httpHeaderAuth'))
        for service in ('slack', 'google-sheets', 'azure-openai'):
            self.assertRegex(service, graph.SERVICE_NAME)


class ComponentLevelTests(N8nFixture):
    def test_workflows_services_credentials_and_calls(self):
        result = model(self.m)
        nodes = by_id(result)
        edges = edge_map(result)
        workflows = {node_id for node_id, node in nodes.items() if node['kind'] == 'workflow'}
        self.assertEqual(workflows, {LEAD, ENRICH_ID, NIGHTLY_ID})
        lead = nodes[LEAD]
        self.assertEqual((lead['title'], lead['layer'], lead['path'], lead['files']), ('Lead intake', 'n8n', 'workflows/lead-intake.json', 1))
        self.assertEqual(lead['n8n']['workflow_id'], 'wf-lead')
        self.assertTrue(lead['n8n']['active'])
        self.assertEqual(lead['n8n']['triggers'], ['Webhook', 'Every morning'])
        self.assertEqual(lead['n8n']['nodes'], 9)
        self.assertEqual(lead['n8n']['disabled_nodes'], 1)
        self.assertEqual(lead['n8n']['unresolved_calls'], 1)
        self.assertIn('unresolved_calls', lead['flags'])
        self.assertIn({'type': 'slackOAuth2Api', 'id': 'cred-slack', 'name': 'Sales Slack'}, lead['n8n']['credentials'])
        self.assertIn({'type': 'httpHeaderAuth', 'id': 'cred-crm', 'name': 'CRM header'}, lead['n8n']['credentials'])
        self.assertEqual(nodes[NIGHTLY_ID]['title'], 'Nightly report')
        self.assertEqual(nodes[NIGHTLY_ID]['n8n']['triggers'], ['Midnight'])

        services = {node_id for node_id, node in nodes.items() if node['kind'] == 'service'}
        self.assertEqual(services, {'service:slack', 'service:openai', 'service:google-sheets', 'service:crm.example.invalid'})
        self.assertEqual(nodes['service:crm.example.invalid']['title'], 'crm.example.invalid')
        self.assertEqual(nodes['service:crm.example.invalid']['n8n']['node_types'], ['n8n-nodes-base.httpRequest'])
        self.assertIn((LEAD, 'service:crm.example.invalid', 'uses'), edges)
        self.assertEqual(nodes['service:openai']['title'], 'OpenAI')
        self.assertEqual(nodes['service:openai']['layer'], 'n8n')
        self.assertEqual(nodes['service:openai']['n8n']['credentials'], [{'type': 'openAiApi', 'id': 'cred-openai', 'name': 'OpenAI account'}])
        self.assertEqual(nodes['service:openai']['n8n']['node_types'],
                         ['@n8n/n8n-nodes-langchain.lmChatOpenAi', '@n8n/n8n-nodes-langchain.openAi'])
        self.assertEqual(edges[(LEAD, 'service:openai', 'uses')]['weight'], 2)
        self.assertEqual(edges[(LEAD, 'service:slack', 'uses')]['weight'], 1)
        self.assertIn((ENRICH_ID, 'service:google-sheets', 'uses'), edges)
        self.assertEqual(edges[(LEAD, ENRICH_ID, 'calls')]['layer'], 'n8n')
        self.assertIn((NIGHTLY_ID, ENRICH_ID, 'calls'), edges)

        # Other JSON files and the code layer are unaffected.
        self.assertNotIn('component:n8n:package.json', nodes)
        self.assertNotIn('component:n8n:data/nodes.json', nodes)
        self.assertNotIn('component:n8n:data/broken.json', nodes)
        self.assertEqual(result['issues'], [])
        self.assertEqual(nodes['component:scripts']['layer'], 'code')
        self.assertIn('credential values are not kept', result['note'])

    def test_secrets_and_parameters_never_appear_in_the_model(self):
        for level, focus in (('component', None), ('file', 'n8n:workflows/lead-intake.json')):
            text = json.dumps(model(self.m, level=level, focus=focus))
            with self.subTest(level=level):
                self.assertNotIn(SECRET, text)
                # Only the host name of a static URL is kept, never its path or query.
                self.assertNotIn('/api/leads', text)
                self.assertNotIn('rotate the key', text)
                self.assertNotIn('Qualify the lead', text)

    def test_layers_select_code_and_workflows(self):
        workflows_only = by_id(model(self.m, layers=['n8n']))
        self.assertIn(LEAD, workflows_only)
        self.assertFalse(any(node['layer'] == 'code' for node in workflows_only.values()))
        code_only = by_id(model(self.m, layers='code'))
        self.assertNotIn(LEAD, code_only)
        self.assertIn('component:scripts', code_only)
        with self.assertRaises(InvalidRecord):
            model(self.m, layers=['n8n', 'images'])
        with self.assertRaises(InvalidRecord):
            model(self.m, layers=[])
        with self.assertRaises(InvalidRecord):
            model(self.m, level='file', focus='n8n:workflows/lead-intake.json', layers=['code'])

    def test_plan_paths_attach_workflow_work_and_links_attach_services(self):
        source = self.m.source('scope', 'Scope', 'The user sets the scope.', 'Change the lead intake workflow.', 'user')
        work = planning.save(self.m, 'work_plan',
                             payload={'state': 'ready', 'next_action': 'Edit the workflow.', 'scope': 'Lead intake only.',
                                      'autonomy': 'suggest', 'reason': 'The user asks for it.', 'paths': ['workflows/lead-*.json'],
                                      'item_type': 'workflow'},
                             actor='test', evidence=[{'source_id': source['id'], 'reason': 'The user sets the scope.'}],
                             title='Lead intake', objective='Change the lead intake workflow.', criterion='The sample lead is posted.',
                             request_key='plan-lead')
        other = planning.save(self.m, 'work_plan',
                              payload={'state': 'ready', 'next_action': 'Check the Slack channel.', 'scope': 'Slack only.',
                                       'autonomy': 'suggest', 'reason': 'The user asks for it.'},
                              actor='test', evidence=[{'source_id': source['id'], 'reason': 'The user sets the scope.'}],
                              title='Slack channel', objective='Confirm the Slack channel.', criterion='The channel exists.',
                              request_key='plan-slack')
        graph.link(self.m, from_id=other['episode_id'], to_id='service:slack', type='uses', reason='The check uses Slack.',
                   actor='test', request_key='link-slack')
        self.assertEqual(graph.node(self.m, 'service:slack')['kind'], 'service')
        nodes = by_id(model(self.m, work_states={work['episode_id']: 'in_progress', other['episode_id']: 'blocked'}))
        self.assertEqual(nodes[LEAD]['work'], [work['episode_id']])
        self.assertEqual(nodes[LEAD]['status'], 'in_progress')
        self.assertEqual(nodes[ENRICH_ID]['work'], [])
        self.assertEqual(nodes['service:slack']['work'], [other['episode_id']])
        self.assertEqual(nodes['service:slack']['status'], 'blocked')
        self.assertEqual(nodes['service:slack']['links_total'], 1)

    def test_authored_workflow_component_merges_into_the_exported_workflow(self):
        architecture.save_component(self.m, title='Lead intake', kind='workflow', description='The workflow receives new leads.',
                                    status='proposed', actor='assistant', request_key='c1', path='n8n:workflows/lead-intake.json')
        nodes = by_id(model(self.m))
        self.assertNotIn('component:lead-intake', nodes)
        self.assertEqual(nodes[LEAD]['authored']['id'], 'component:lead-intake')
        self.assertEqual(nodes[LEAD]['authored_status'], 'proposed')

    def test_a_bulk_export_array_adds_each_workflow(self):
        write(self.root, 'workflows/all-workflows.json', [
            {'name': 'Invoice sync', 'nodes': [{'name': 'Xero', 'type': 'n8n-nodes-base.xero', 'parameters': {}}], 'connections': {}},
            {'name': 'Nightly cleanup', 'nodes': [{'name': 'Midnight', 'type': 'n8n-nodes-base.cron', 'parameters': {}}], 'connections': {}},
        ])
        nodes = by_id(model(self.m))
        first = 'component:n8n:workflows/all-workflows.json#1'
        self.assertEqual(nodes[first]['title'], 'Invoice sync')
        self.assertEqual(nodes[first]['path'], 'workflows/all-workflows.json')
        self.assertEqual(nodes['component:n8n:workflows/all-workflows.json#2']['title'], 'Nightly cleanup')
        self.assertIn('service:xero', nodes)
        self.assertEqual(len(model(self.m, level='file', focus='n8n:workflows/all-workflows.json#1')['nodes']), 1)
        self.assertEqual(graph.node(self.m, first)['title'], 'Invoice sync')
        self.assertEqual((graph.node(self.m, LEAD)['title'], graph.node(self.m, LEAD)['subject']), ('Lead intake', 'general'))

    def test_authored_systems_merge_into_services_from_exports(self):
        architecture.save_component(self.m, title='Slack', kind='system', description='The sales team uses Slack.',
                                    status='proposed', actor='assistant', request_key='s1')
        architecture.save_component(self.m, title='Client CRM', kind='system', description='The CRM holds the leads.',
                                    status='proposed', actor='assistant', request_key='s2', path='service:crm.example.invalid')
        nodes = by_id(model(self.m))
        self.assertNotIn('component:slack', nodes)
        self.assertNotIn('component:client-crm', nodes)
        self.assertEqual(nodes['service:slack']['authored']['id'], 'component:slack')
        self.assertEqual(nodes['service:crm.example.invalid']['authored']['id'], 'component:client-crm')

    def test_cache_reuses_unchanged_workflow_files(self):
        cache = {}
        model(self.m, cache=cache)
        with mock.patch.object(arch_n8n, '_read_workflow', side_effect=AssertionError('read again')):
            again = model(self.m, cache=cache)
        self.assertIn(LEAD, by_id(again))
        (self.root / 'workflows/nightly.json').unlink()
        nodes = by_id(model(self.m, cache=cache))
        self.assertNotIn(NIGHTLY_ID, nodes)
        self.assertFalse(any(key[1].endswith('nightly.json') for key in cache if isinstance(key, tuple) and key[0] == 'n8n'))

    def test_workflow_count_is_bounded(self):
        with mock.patch.object(arch_n8n, 'MAX_WORKFLOWS', 2):
            result = model(self.m, layers=['n8n'])
        self.assertTrue(result['truncated'])
        self.assertEqual(sum(node['kind'] == 'workflow' for node in result['nodes']), 2)


class FileLevelTests(N8nFixture):
    def test_nodes_connections_triggers_and_disabled_flags(self):
        result = model(self.m, level='file', focus='component:n8n:workflows/lead-intake.json')
        self.assertEqual(result['focus'], 'n8n:workflows/lead-intake.json')
        nodes = by_id(result)
        prefix = LEAD + '#'
        self.assertEqual(set(nodes), {prefix + name for name in ('Webhook', 'Every morning', 'Summarise lead', 'OpenAI Chat Model', 'Qualify',
                                                                   'Notify sales', 'Call CRM', 'Enrich', 'Dynamic call')})
        self.assertTrue(all(node['kind'] == 'n8n_node' and node['layer'] == 'n8n' for node in nodes.values()))
        self.assertEqual(nodes[prefix + 'Webhook']['flags'], ['trigger'])
        self.assertEqual(nodes[prefix + 'Every morning']['flags'], ['trigger'])
        self.assertEqual(nodes[prefix + 'Dynamic call']['flags'], ['disabled'])
        self.assertEqual(nodes[prefix + 'Notify sales']['n8n']['type'], 'n8n-nodes-base.slack')
        self.assertEqual(nodes[prefix + 'Notify sales']['n8n']['services'], ['slack'])
        self.assertEqual(nodes[prefix + 'Call CRM']['n8n']['services'], ['crm.example.invalid'])
        self.assertEqual(nodes[prefix + 'Enrich']['n8n']['calls'], ENRICH_ID)
        self.assertIsNone(nodes[prefix + 'Dynamic call']['n8n']['calls'])
        connections = {(edge['from'], edge['to'], edge['connection']) for edge in result['edges']}
        self.assertIn((prefix + 'Webhook', prefix + 'Summarise lead', 'main'), connections)
        self.assertIn((prefix + 'OpenAI Chat Model', prefix + 'Qualify', 'ai_languageModel'), connections)
        self.assertIn((prefix + 'Qualify', prefix + 'Enrich', 'main'), connections)
        self.assertEqual(len(connections), 7)
        self.assertTrue(all(edge['type'] == 'connects' for edge in result['edges']))

    def test_unknown_workflow_focus_is_rejected(self):
        for focus in ('n8n:workflows/missing.json', 'n8n:package.json'):
            with self.subTest(focus=focus), self.assertRaises(InvalidRecord):
                model(self.m, level='file', focus=focus)

    def test_read_only_connection_reads_the_workflow_layer(self):
        self.m.close()
        with Memory(self.root / '.memory' / 'memory.sqlite', read_only=True) as reader:
            self.assertIn(LEAD, by_id(model(reader)))
            self.assertEqual(len(model(reader, level='file', focus='n8n:workflows/enrich.json')['nodes']), 2)
        self.m = Memory(self.root / '.memory' / 'memory.sqlite')


if __name__ == '__main__':
    unittest.main()
