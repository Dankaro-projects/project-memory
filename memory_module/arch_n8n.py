"""Exported n8n workflows: the workflows in a project, the services they use and the nodes inside one workflow.

Only workflow names, node names, node types, connections and credential names
and ids are read. Node parameters and credential values are never kept, so no
secret from an export reaches the model. A file that is not an n8n export is
passed over without a message.
"""
import json
from pathlib import PurePosixPath
import re

from .arch_base import N8N_PREFIX, Unreadable, empty_node, readable
from .core import InvalidRecord

NOTE = (' Workflows are read from exported n8n JSON files in the project. Only workflow names, node names, node types, '
        'connections, and credential names and ids are kept. Node parameters and credential values are not kept.')
MAX_WORKFLOWS = 500

N8N_STICKY = 'n8n-nodes-base.stickyNote'
N8N_EXECUTE_TYPES = {'n8n-nodes-base.executeWorkflow', '@n8n/n8n-nodes-langchain.toolWorkflow'}
N8N_TRIGGER_TYPES = {'n8n-nodes-base.webhook', 'n8n-nodes-base.cron', 'n8n-nodes-base.interval', 'n8n-nodes-base.start',
                     'n8n-nodes-base.emailReadImap'}
# Built-in logic and generic AI building blocks. They do not name an outside system.
N8N_UTILITY = {
    'aggregate', 'agent', 'agentTool', 'chainLlm', 'chainRetrievalQa', 'chainSummarization', 'chat', 'chatTrigger', 'code',
    'compareDatasets', 'compression', 'convertToFile', 'cron', 'crypto', 'dateTime', 'debugHelper', 'documentBinaryInputLoader',
    'documentDefaultDataLoader', 'documentJsonInputLoader', 'editImage', 'errorTrigger', 'executeCommand', 'executeWorkflow',
    'executeWorkflowTrigger', 'executionData', 'extractFromFile', 'filter', 'form', 'formTrigger', 'function', 'functionItem',
    'html', 'httpRequest', 'if', 'informationExtractor', 'interval', 'itemLists', 'limit', 'localFileTrigger', 'manualChatTrigger',
    'manualTrigger', 'markdown', 'mcpClientTool', 'mcpTrigger', 'memoryBufferWindow', 'memoryManager', 'merge', 'moveBinaryData',
    'n8n', 'n8nTrigger', 'noOp', 'readBinaryFile', 'readBinaryFiles', 'readWriteFile', 'removeDuplicates', 'renameKeys',
    'respondToWebhook', 'retrieverContextualCompression', 'retrieverMultiQuery', 'retrieverVectorStore', 'retrieverWorkflow',
    'scheduleTrigger', 'sentimentAnalysis', 'set', 'simulate', 'simulateTrigger', 'sort', 'splitInBatches', 'splitOut',
    'spreadsheetFile', 'start', 'stickyNote', 'stopAndError', 'summarize', 'switch', 'textClassifier', 'toolCalculator',
    'toolCode', 'toolExecutor', 'toolHttpRequest', 'toolThink', 'toolWorkflow', 'totp', 'vectorStoreInMemory',
    'vectorStoreInMemoryInsert', 'vectorStoreInMemoryLoad', 'wait', 'webhook', 'workflowTrigger', 'writeBinaryFile', 'xml',
}
N8N_UTILITY_PREFIXES = ('outputParser', 'textSplitter')
N8N_MODEL_PREFIXES = ('lmChat', 'lm', 'embeddings', 'vectorStore', 'memory', 'tool', 'retriever')
N8N_MODEL_SUFFIXES = ('Chat', 'Insert', 'Load')
N8N_GENERIC_CREDENTIALS = {'httpHeaderAuth', 'httpBasicAuth', 'httpQueryAuth', 'httpDigestAuth', 'httpCustomAuth',
                           'httpSslAuth', 'httpBearerAuth', 'oAuth1Api', 'oAuth2Api', 'jwtAuth'}
N8N_CREDENTIAL_SUFFIXES = ('OAuth2Api', 'OAuth1Api', 'Oauth2Api', 'ServiceAccountApi', 'TokenApi', 'AppToken', 'AccessToken',
                           'ApiKey', 'Api', 'OAuth2', 'Oauth2')
SERVICE_ALIASES = (('open-ai', 'openai'), ('git-hub', 'github'), ('git-lab', 'gitlab'), ('hub-spot', 'hubspot'),
                   ('my-sql', 'mysql'), ('mongo-db', 'mongodb'), ('serp-api', 'serpapi'), ('you-tube', 'youtube'),
                   ('linked-in', 'linkedin'), ('word-press', 'wordpress'), ('drop-box', 'dropbox'),
                   ('share-point', 'sharepoint'), ('one-drive', 'onedrive'), ('send-grid', 'sendgrid'),
                   ('mail-chimp', 'mailchimp'), ('quick-books', 'quickbooks'), ('click-up', 'clickup'),
                   ('post-hog', 'posthog'), ('email-send', 'smtp'), ('email-read-imap', 'imap'))


def _service_slug(name):
    text = re.sub(r'(?<=[a-z0-9])(?=[A-Z])', '-', name).lower()
    text = re.sub(r'[^a-z0-9._-]+', '-', text).strip('-._')
    for old, new in SERVICE_ALIASES:
        text = re.sub(r'(?<![a-z0-9])' + re.escape(old) + r'(?![a-z0-9])', new, text)
    return text[:100].strip('-._') or None


def node_service(node_type):
    """Service name for an n8n node type, or None for built-in logic.

    `n8n-nodes-base.slack` and `n8n-nodes-base.slackTrigger` become `slack`;
    `@n8n/n8n-nodes-langchain.lmChatOpenAi` becomes `openai`.
    """
    if not isinstance(node_type, str) or '.' not in node_type:
        return None
    package, _, name = node_type.rpartition('.')
    if not name or name in N8N_UTILITY or name.startswith(N8N_UTILITY_PREFIXES):
        return None
    if 'langchain' in package:
        for prefix in N8N_MODEL_PREFIXES:
            if name.startswith(prefix) and len(name) > len(prefix) and name[len(prefix)].isupper():
                name = name[len(prefix):]
                break
        for suffix in N8N_MODEL_SUFFIXES:
            if name.endswith(suffix) and len(name) > len(suffix):
                name = name[:-len(suffix)]
    for suffix in ('Trigger', 'Tool'):
        if name.endswith(suffix) and len(name) > len(suffix):
            name = name[:-len(suffix)]
            if name in N8N_UTILITY:
                return None
    return _service_slug(name)


def credential_service(credential_type):
    """Service name for an n8n credential type, or None for generic authentication."""
    if not isinstance(credential_type, str) or not credential_type or credential_type in N8N_GENERIC_CREDENTIALS:
        return None
    name = credential_type
    for suffix in N8N_CREDENTIAL_SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix):
            name = name[:-len(suffix)]
            break
    return _service_slug(name)


N8N_HTTP_TYPES = {'n8n-nodes-base.httpRequest', '@n8n/n8n-nodes-langchain.toolHttpRequest'}
HOST_SERVICE = re.compile(r'[a-z0-9][a-z0-9.-]{0,99}')


def request_host(item):
    """The host name of a static http or https URL in an HTTP Request node, or None.

    Only the host name is kept. The path, the query, user information and every
    other parameter are ignored, and URLs built from expressions are skipped.
    """
    from urllib.parse import urlsplit
    parameters = item.get('parameters')
    if not isinstance(parameters, dict):
        return None
    url = parameters.get('url')
    if not isinstance(url, str) or url.startswith('=') or '{{' in url:
        return None
    try:
        parts = urlsplit(url.strip())
        host = parts.hostname
    except ValueError:
        return None
    if parts.scheme not in ('http', 'https') or not host:
        return None
    host = host.rstrip('.')
    if host.startswith('www.'):
        host = host[len('www.'):]
    return host if HOST_SERVICE.fullmatch(host) else None


def _is_trigger(node_type):
    name = node_type.rpartition('.')[2]
    return node_type in N8N_TRIGGER_TYPES or (name.endswith('Trigger') and len(name) > len('Trigger'))


def _short(value, limit=300):
    return value[:limit] if isinstance(value, str) else None


def _call_target(item):
    """The workflow an Execute Workflow node calls: an id, a cached name or a file path. Expressions are ignored."""
    parameters = item.get('parameters')
    if not isinstance(parameters, dict):
        return {}
    target = {}
    source = parameters.get('source', 'database')
    if source == 'localFile' and isinstance(parameters.get('workflowPath'), str):
        target['path'] = parameters['workflowPath'][:1000]
    value = parameters.get('workflowId')
    if isinstance(value, dict):
        if isinstance(value.get('cachedResultName'), str):
            target['name'] = value['cachedResultName'][:300]
        value = value.get('value')
    if isinstance(value, (str, int)) and not isinstance(value, bool) and not str(value).startswith('='):
        target['id'] = str(value)[:200]
    return target


def _summarize_workflow(data):
    """Keep node names, types, flags, connections and credential names and ids. Parameters and secrets are not kept."""
    nodes = []
    for item in data['nodes']:
        if not isinstance(item, dict):
            continue
        name = _short(item.get('name'))
        node_type = _short(item.get('type'))
        if not name or not node_type or node_type == N8N_STICKY:
            continue
        credentials = []
        raw = item.get('credentials')
        if isinstance(raw, dict):
            for credential_type, value in raw.items():
                if not isinstance(credential_type, str):
                    continue
                entry = {'type': credential_type[:200]}
                if isinstance(value, dict):
                    for key in ('id', 'name'):
                        if isinstance(value.get(key), (str, int)) and not isinstance(value.get(key), bool):
                            entry[key] = str(value[key])[:200]
                elif isinstance(value, str):
                    # Older exports store only the credential name.
                    entry['name'] = value[:200]
                credentials.append(entry)
        host = request_host(item) if node_type in N8N_HTTP_TYPES else None
        services = []
        for service in [node_service(node_type)] + [credential_service(entry['type']) for entry in credentials] + [host]:
            if service and service not in services:
                services.append(service)
        nodes.append({'name': name, 'type': node_type, 'disabled': item.get('disabled') is True,
                      'trigger': _is_trigger(node_type), 'services': services, 'credentials': credentials, 'host': host,
                      'call': _call_target(item) if node_type in N8N_EXECUTE_TYPES else None})
    connections = []
    for source, outputs in data['connections'].items():
        if not isinstance(source, str) or not isinstance(outputs, dict):
            continue
        for connection, groups in outputs.items():
            if not isinstance(groups, list):
                continue
            for group in groups:
                if not isinstance(group, list):
                    continue
                for target in group:
                    if isinstance(target, dict) and isinstance(target.get('node'), str):
                        connections.append([source, target['node'], str(connection)[:100]])
    workflow_id = data.get('id')
    return {'name': _short(data.get('name')), 'active': data.get('active') is True, 'nodes': nodes, 'connections': connections,
            'id': str(workflow_id)[:200] if isinstance(workflow_id, (str, int)) and not isinstance(workflow_id, bool) else None}


def _is_workflow(value):
    return isinstance(value, dict) and isinstance(value.get('nodes'), list) and isinstance(value.get('connections'), dict)


def _read_workflow(path):
    """The summary of an exported workflow, a list of summaries for a bulk export array, or None for other JSON."""
    data = path.read_bytes()
    if b'"nodes"' not in data or b'"connections"' not in data:
        return None
    try:
        value = json.loads(data.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return None
    if _is_workflow(value):
        return _summarize_workflow(value)
    if isinstance(value, list):
        found = [_summarize_workflow(item) for item in value if _is_workflow(item)]
        return found or None
    return None


def workflow_entries(name, summary):
    """(key, summary) pairs for one file. A bulk export array gives `<file>#<position>` keys, starting at 1."""
    if summary is None:
        return []
    if isinstance(summary, dict):
        return [(name, summary)]
    return [(f'{name}#{index + 1}', item) for index, item in enumerate(summary)]


def _workflow_files(root, names, cache):
    """Exported workflows as (key, file name, summary). A file holding an array of workflows yields one entry per workflow."""
    workflows = []
    truncated = False
    seen = set()
    for name in names:
        if truncated:
            break
        if PurePosixPath(name).suffix.lower() != '.json':
            continue
        try:
            path, stat = readable(root, name)
            if stat is None:
                continue
            key = ('n8n', str(path), stat.st_size, stat.st_mtime_ns)
            seen.add(key)
            if cache is not None and key in cache:
                summary = cache[key]
            else:
                summary = _read_workflow(path)
                if cache is not None:
                    cache[key] = summary
        except (Unreadable, OSError):
            continue
        for key, item in workflow_entries(name, summary):
            if len(workflows) >= MAX_WORKFLOWS:
                truncated = True
                break
            workflows.append((key, name, item))
    if cache is not None and not truncated:
        for key in [key for key in cache if isinstance(key, tuple) and len(key) == 4 and key[0] == 'n8n' and key not in seen]:
            del cache[key]
    return workflows, truncated


SERVICE_TITLES = {'openai': 'OpenAI', 'github': 'GitHub', 'gitlab': 'GitLab', 'hubspot': 'HubSpot', 'mysql': 'MySQL',
                  'mongodb': 'MongoDB', 'smtp': 'SMTP', 'imap': 'IMAP', 'serpapi': 'SerpApi', 'youtube': 'YouTube',
                  'linkedin': 'LinkedIn'}


def _service_title(service):
    if '.' in service:
        # A host name from an HTTP Request node is shown as it is.
        return service
    return SERVICE_TITLES.get(service) or ' '.join(part.capitalize() for part in service.split('-'))


def _unique_credentials(entries):
    result = []
    for entry in entries:
        if entry not in result:
            result.append(entry)
    return result


def layer(root, names, cache, focus_file=None):
    """Workflow and service nodes at component level, or the nodes of one workflow at file level."""
    workflows, truncated = _workflow_files(root, names, cache)
    by_file = {key: 'component:' + N8N_PREFIX + key for key, _, _ in workflows}
    by_workflow_id = {}
    by_name = {}
    by_basename = {}
    for key, name, summary in workflows:
        if summary['id']:
            by_workflow_id.setdefault(summary['id'], []).append(by_file[key])
        if summary['name']:
            by_name.setdefault(summary['name'], []).append(by_file[key])
        by_basename.setdefault(PurePosixPath(name).name, []).append(by_file[key])

    def resolve(call):
        if not call:
            return None
        if call.get('path'):
            found = by_basename.get(PurePosixPath(call['path'].replace('\\', '/')).name, [])
            if len(found) == 1:
                return found[0]
        for key, index in (('id', by_workflow_id), ('name', by_name)):
            found = index.get(call.get(key), [])
            if len(found) == 1:
                return found[0]
        return None

    nodes = {}
    members = {}
    edges = {}
    if focus_file is not None:
        workflow_id = by_file.get(focus_file)
        if workflow_id is None:
            raise InvalidRecord('The focus is not an exported n8n workflow in this project.', focus=N8N_PREFIX + focus_file)
        summary = {key: value for key, _, value in workflows}[focus_file]
        file_name = {key: name for key, name, _ in workflows}[focus_file]
        present = set()
        for item in summary['nodes']:
            node_id = workflow_id + '#' + item['name']
            node = empty_node(node_id, 'n8n_node', item['name'], file_name, layer='n8n')
            node['n8n'] = {'type': item['type'], 'disabled': item['disabled'], 'trigger': item['trigger'],
                           'services': item['services'], 'credentials': item['credentials']}
            if item['call'] is not None:
                node['n8n']['calls'] = resolve(item['call'])
            if item['trigger']:
                node['flags'].append('trigger')
            if item['disabled']:
                node['flags'].append('disabled')
            nodes[node_id] = node
            members[node_id] = []
            present.add(item['name'])
        for source, target, connection in summary['connections']:
            if source in present and target in present:
                key = (workflow_id + '#' + source, workflow_id + '#' + target, 'connects', connection)
                edges[key] = edges.get(key, 0) + 1
        edge_list = [{'from': a, 'to': b, 'type': kind, 'connection': connection, 'weight': weight, 'layer': 'n8n'}
                     for (a, b, kind, connection), weight in sorted(edges.items())]
        return {'nodes': nodes, 'members': members, 'edges': edge_list, 'truncated': truncated}

    services = {}
    for key, name, summary in workflows:
        workflow_id = by_file[key]
        node = empty_node(workflow_id, 'workflow', summary['name'] or PurePosixPath(name).stem, name, layer='n8n')
        node['files'] = 1
        node['languages'] = ['n8n']
        unresolved = 0
        for item in summary['nodes']:
            for service in item['services']:
                key = (workflow_id, 'service:' + service, 'uses')
                edges[key] = edges.get(key, 0) + 1
                entry = services.setdefault(service, {'types': set(), 'credentials': [], 'workflows': set()})
                entry['workflows'].add(workflow_id)
                if node_service(item['type']) == service or item.get('host') == service:
                    entry['types'].add(item['type'])
                entry['credentials'].extend(c for c in item['credentials'] if credential_service(c['type']) == service)
            if item['call'] is not None:
                target = resolve(item['call'])
                if target and target != workflow_id:
                    key = (workflow_id, target, 'calls')
                    edges[key] = edges.get(key, 0) + 1
                elif target is None:
                    unresolved += 1
        node['n8n'] = {'workflow_id': summary['id'], 'active': summary['active'], 'nodes': len(summary['nodes']),
                       'triggers': [item['name'] for item in summary['nodes'] if item['trigger']],
                       'disabled_nodes': sum(item['disabled'] for item in summary['nodes']),
                       'credentials': _unique_credentials(c for item in summary['nodes'] for c in item['credentials']),
                       'unresolved_calls': unresolved}
        if unresolved:
            node['flags'].append('unresolved_calls')
        nodes[workflow_id] = node
        members[workflow_id] = [name]
    for service, entry in sorted(services.items()):
        node_id = 'service:' + service
        node = empty_node(node_id, 'service', _service_title(service), None, layer='n8n')
        node['n8n'] = {'node_types': sorted(entry['types']), 'credentials': _unique_credentials(entry['credentials']),
                       'workflows': len(entry['workflows'])}
        nodes[node_id] = node
        members[node_id] = []
    edge_list = [{'from': a, 'to': b, 'type': kind, 'weight': weight, 'layer': 'n8n'} for (a, b, kind), weight in sorted(edges.items())]
    return {'nodes': nodes, 'members': members, 'edges': edge_list, 'truncated': truncated}
