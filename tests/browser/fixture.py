"""Build fixture projects for the control panel browser tests and for reviewers.

    python3 tests/browser/fixture.py --kind product|engagement|automation --output <dir> [--serve]

The project is scaffolded from its template with no hook clients, then filled
through the real modules: work items with types, parents, acceptance criteria and
a blocked dependency chain; a decision with a failed outcome and a successful
revision; a document with an exception; an accepted lesson with path triggers and
a recurrence; a rule accepted for every agent role and a reviewer base text saved
by the user; a proposed lesson; a scope block receipt; a completed delegated run
with a passing work review that awaits a merge; authored components with links;
one rule accepted into the machine memory and one promotion that awaits the user;
a usage ledger in the machine memory with Codex at 91 percent of its window, a
Claude limit hit that has not reset and a passing Grok probe, and a routing
decision on the delegated run and on its work review;
and project files for the template (a small source tree, engagement documents or
exported n8n workflows).

The machine memory of the fixture is created inside the fixture folder, because
PROJECT_MEMORY_MACHINE_DB is set before the records are written, so no check reads
or writes the machine memory of this computer.

Agent runs are inserted as finished rows. No host process runs, and the project is
not a git repository, so delegation from the panel stops with a message. With
--serve, the host programs point at a fixture program that refuses to run, so a
check requested from the panel cannot start a real agent either.

The script prints JSON with the project, the database path, the record ids and,
with --serve, the URL of the live control panel.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memory_module import Memory, architecture, codex_host, focus, graph, hive, hosts, machine, reviews, sessions, templates  # noqa: E402
from memory_module.planning import latest, save  # noqa: E402
from memory_module.workspace import action  # noqa: E402

LAUNCHER = [sys.executable, '-m', 'memory_module.cli']
# The project version of the reviewer base text, saved through the workspace action as the user.
BASE_TEXT = ('You are the reviewer in this project. Assess the supplied work against the recorded objective, the completion '
             'criterion and every constraint. State the evidence for every judgement and report missing evidence as unknown. '
             'Give one verdict of pass, changes required or uncertain. Do not edit files, repeat the work or merge.')
USER = 'workspace-user'
AGENT = 'assistant'
# Two rules for the machine memory. Their text names no project, path, record, document, address or host, because the
# mechanical checks of a promotion refuse text that belongs to one project alone.
ACCEPTED_RULE = {'when': 'work is delegated to an agent',
                 'do': 'Name the files the agent may change before the run starts.',
                 'because': 'A run without a named boundary changes work that nobody reviewed.',
                 'exceptions': 'A run that only reads and reports.'}
PROPOSED_RULE = {'when': 'a change is ready to be merged',
                 'do': 'State which checks ran and what they reported before the merge.',
                 'because': 'A merge without a recorded check result hides a failure until a user meets it.',
                 'exceptions': 'A change that only corrects wording in a comment.'}


HOST_PROGRAM = '''import json, os, pathlib, sys

argv = sys.argv[1:]


def option(name):
    return argv[argv.index(name) + 1] if name in argv else None


codex = '--output-last-message' in argv
folder = pathlib.Path(option('--output-last-message')).parent if codex else None
schema = json.loads((folder / 'schema.json').read_text()) if codex else json.loads(option('--json-schema'))
properties = schema['properties']
host = 'codex' if codex else 'claude'
mode = 'review' if 'verdict' in properties else 'work'
plan_path = pathlib.Path(os.environ['MEMORY_FAKE_HOST_PLAN'])
plan = json.loads(plan_path.read_text())
key = host + ':' + mode
calls = plan.setdefault('calls', {})
index = calls.get(key, 0)
calls[key] = index + 1
plan_path.write_text(json.dumps(plan))
specs = plan.get(key) or []
spec = specs[index] if index < len(specs) else (specs[-1] if specs else {})
for name, text in (spec.get('write') or {}).items():
    path = pathlib.Path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
if spec.get('unavailable'):
    event = {'type': 'error', 'message': spec['unavailable']} if codex else {'type': 'result', 'is_error': True, 'result': spec['unavailable']}
    print(json.dumps(event), flush=True)
    sys.exit(1)
if mode == 'review':
    verdict = spec.get('verdict', 'pass')
    outcome = 'met' if verdict == 'pass' else 'unmet'
    names = properties['checks']['items']['properties']['criterion'].get('enum', ['C001'])
    report = {'verdict': verdict, 'summary': spec.get('summary', 'The reviewer inspected the recorded work.'),
              'checks': [{'criterion': name, 'evidence': 'The recorded work shows this result.', 'result': outcome}
                         for name in names],
              'findings': [], 'lesson_proposals': spec.get('lessons', [])}
    if 'constraint_checks' in properties:
        report['constraint_checks'] = [
            {'constraint': name, 'applicability': 'applies', 'reason': 'The constraint applies to this work.',
             'evidence': 'The recorded work.', 'result': outcome}
            for name in properties['constraint_checks']['items']['properties']['constraint']['enum']]
else:
    report = {'summary': spec.get('summary', 'The worker changed the files the plan allows.'), 'result': 'complete',
              'changed_files': sorted(spec.get('write') or {}),
              'checks_run': [{'command': 'Compare the result with the sample data.', 'outcome': 'The sample data matches.'}],
              'notes': '', 'lesson_proposals': spec.get('lessons', [])}
if spec.get('report'):
    report = spec['report']
if codex:
    (folder / 'answer.json').write_text(json.dumps(report))
    print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 10}}), flush=True)
else:
    print(json.dumps({'type': 'result', 'structured_output': report, 'usage': {'input_tokens': 10}}), flush=True)
'''


def fake_hosts(directory, plan=None):
    """Write a host program that stands in for Codex and Claude, and return the environment that selects it.

    The program never contacts a model. It reads the report schema that Project
    Memory prepared for the run, so it answers a delegated work run and an agent
    check with a valid report. The plan file selects what each call does: files to
    write inside the worktree, a verdict, lesson proposals, a replacement report or
    a usage limit message that makes the host report itself as unavailable. Its
    keys are `<host>:work` and `<host>:review`, and each key holds one entry per
    call, so the first call of a host can fail and the next one can succeed.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    program = directory / 'fake-host.py'
    program.write_text(HOST_PROGRAM, encoding='utf-8')
    program.chmod(0o755)
    # Windows ignores a shebang line and the executable bit, so the launcher is a
    # small program that runs this file with the interpreter that wrote it.
    if os.name == 'nt':
        launcher = directory / 'fake-host.cmd'
        launcher.write_text('@echo off\r\n"' + sys.executable + '" "' + str(program) + '" %*\r\n', encoding='utf-8')
    else:
        launcher = directory / 'fake-host'
        launcher.write_text('#!/bin/sh\nexec ' + sys.executable + ' ' + str(program) + ' "$@"\n', encoding='utf-8')
        launcher.chmod(0o755)
    program = launcher
    plan_path = directory / 'host-plan.json'
    plan_path.write_text(json.dumps(plan or {}), encoding='utf-8')
    return {'PROJECT_MEMORY_CODEX_BIN': str(program), 'PROJECT_MEMORY_CLAUDE_BIN': str(program),
            'MEMORY_FAKE_HOST_PLAN': str(plan_path), 'CODEX_HOME': str(directory / 'codex-home')}


def policy(title, purpose, rows, exception):
    """A short Markdown document with a table, an exception, a list, a quote and a link."""
    table = '\n'.join('| ' + first + ' | ' + second + ' |' for first, second in rows)
    return (f'# {title}\n\n{purpose}\n\n## Rule\n\n| Case | Required behaviour |\n| --- | --- |\n{table}\n\n'
            f'## Exception\n\n{exception}\n\n- Keep **the exception** until the user retires it.\n'
            '- Record each rejected case with `its identifier` and the reason.\n\n'
            '> Evidence for each exception stays with the project records.\n\n'
            '```text\n<img src=x onerror="window.injected=true">\n```\n\n'
            '[Unsafe link](javascript:window.injected=true) and [reference](https://www.unicode.org/faq/utf_bom.html)\n')


def workflow(name, identifier, nodes, connections):
    return json.dumps({'name': name, 'id': identifier, 'active': False, 'nodes': nodes, 'connections': connections,
                       'settings': {'executionOrder': 'v1'}}, indent=2) + '\n'


def n8n_node(identifier, name, node_type, x, parameters=None, credentials=None):
    node = {'id': identifier, 'name': name, 'type': node_type, 'typeVersion': 1, 'position': [x, 0],
            'parameters': parameters or {}}
    if credentials:
        node['credentials'] = credentials
    return node


def chain(*names):
    return {first: {'main': [[{'node': second, 'type': 'main', 'index': 0}]]} for first, second in zip(names, names[1:])}


LEAD_INTAKE = workflow('Lead intake', 'lead-intake', [
    n8n_node('1', 'New lead webhook', 'n8n-nodes-base.webhook', 0, {'path': 'lead-intake', 'httpMethod': 'POST'}),
    n8n_node('2', 'Summarise lead', '@n8n/n8n-nodes-langchain.openAi', 240, {'modelId': 'gpt-4o-mini'},
             {'openAiApi': {'id': 'cred-openai', 'name': 'OpenAI account'}}),
    n8n_node('3', 'Enrich lead', 'n8n-nodes-base.executeWorkflow', 480, {'workflowId': 'enrich-lead'}),
    n8n_node('4', 'Notify sales', 'n8n-nodes-base.slack', 720, {'channel': '#sales'},
             {'slackApi': {'id': 'cred-slack', 'name': 'Slack bot'}}),
], chain('New lead webhook', 'Summarise lead', 'Enrich lead', 'Notify sales'))

ENRICH_LEAD = workflow('Enrich lead', 'enrich-lead', [
    n8n_node('1', 'When called by another workflow', 'n8n-nodes-base.executeWorkflowTrigger', 0),
    n8n_node('2', 'Update HubSpot contact', 'n8n-nodes-base.hubspot', 240, {'resource': 'contact', 'operation': 'upsert'},
             {'hubspotApi': {'id': 'cred-hubspot', 'name': 'HubSpot private app'}}),
], chain('When called by another workflow', 'Update HubSpot contact'))

# The focused problem of the --focus option. The check file is written into the fixture project.
FOCUS_PROBLEM = 'The export drops the time zone of every date, so the sales team reads the wrong delivery day.'
FOCUS_HYPOTHESES = [
    ('The date formatter ignores the time zone of the parsed value.', 'Format every date with its own offset.'),
    ('The parser converts every date to local time before the export reads it.', 'Keep the offset when the parser reads a date.'),
    ('The export template truncates the date to its first ten characters.', 'Write the full date string in the template.'),
]
FOCUS_CHECK_TEXT = ('import unittest\n\n\nclass ExportFormatTests(unittest.TestCase):\n'
                    '    def test_dates_keep_the_time_zone(self):\n        self.assertTrue(True)\n')

KINDS = {
    'product': {
        'name': 'Fixture product', 'subject': 'code',
        'request': 'The user asks for an import of client files that keeps every character, and an export that the sales team can publish.',
        'epic': ('Import and export client files', 'build'),
        'story': {'title': 'Parse client files', 'item_type': 'story', 'paths': ['src/app/**'],
                  'acceptance': ['Every sample client file imports without lost characters.',
                                 'A file in an unsupported encoding is rejected with a clear message.']},
        'chain': ['Design the export format', 'Build the export', 'Publish the export'],
        'research': ('Measure the size of client files', 'research'),
        'review': 'Review the import messages',
        'decision': {'decision': 'Decode every client file as strict UTF-8.', 'why': 'Strict decoding exposes invalid input early.',
                     'expected': 'Every sample file imports.', 'uncertainty': 'The files tagged as Latin-1 have not been checked.',
                     'alternatives': ['Keep a separate path for tagged Latin-1 files.'],
                     'reconsider_when': 'A supported sample file fails to import.'},
        'failed': 'The sample file tagged as Latin-1 failed to import.',
        'revised': 'Decode strict UTF-8 and keep the tagged Latin-1 path.',
        'succeeded': 'Both sample files imported without lost characters.',
        'failure_type': 'lost_text',
        'lesson': {'when': 'Client text is decoded.', 'do': 'Run the tagged Latin-1 sample before changing the decoder.',
                   'because': 'The tagged sample failed after a strict decoding change.', 'exceptions': 'Files without an encoding tag.',
                   'pattern_type': 'recovery', 'paths': ['src/app/**']},
        'rule': {'when': 'A change to the importer is proposed.', 'do': 'Name the sample file that proves the change.',
                 'because': 'A change without a named sample could not be checked.', 'exceptions': 'Changes that only rename a symbol.',
                 'pattern_type': 'practice', 'roles': ['assistant', 'worker', 'reviewer']},
        'proposed': {'when': 'A release note is prepared.', 'do': 'Read the release note aloud before publishing it.',
                     'because': 'Unclear wording reached users in the last release.', 'exceptions': 'Internal releases.',
                     'pattern_type': 'practice'},
        'recurrence': ('Import archived client files', 'Import the archive in one pass.', 'Characters were lost in the archived files.'),
        'document': ('docs/import-policy.md', policy('Import policy', 'This policy states how client files are imported.',
                                                     [('UTF-8', 'Import the file and reject invalid bytes.'),
                                                      ('Tagged Latin-1', 'Import the file through the tagged path.')],
                                                     'Files tagged as Latin-1 keep their separate import path, because strict decoding lost characters in the sample file.')),
        'blocked': 'docs/brief.md', 'changed': ['src/app/parser.py'],
        'components': [
            ('importer', 'Client file importer', 'component', 'Reads client files and converts them to records.', 'confirmed', 'src/app', USER),
            ('exporter', 'Export service', 'service', 'Publishes the export for the sales team.', 'proposed', None, AGENT),
            ('web-client', 'Web client', 'component', 'Shows the imported records.', 'proposed', 'web', AGENT),
        ],
        'links': [('story', 'importer', 'implements', 'The story changes the importer.'),
                  ('web-client', 'importer', 'uses', 'The web client reads the imported records.'),
                  ('exporter', 'importer', 'uses', 'The export reads the imported records.')],
        'files': {
            'src/app/__init__.py': '',
            'src/app/main.py': 'from . import parser\nfrom . import export\n\n\ndef run(path):\n    return export.publish(parser.parse(path))\n',
            'src/app/parser.py': 'import json\n\n\ndef parse(path):\n    with open(path, encoding="utf-8") as stream:\n        return json.load(stream)\n',
            'src/app/export.py': 'from . import parser\n\n\ndef publish(records):\n    return list(records)\n',
            'web/index.ts': "import React from 'react';\nimport { load } from './api';\n\nexport const App = () => load();\n",
            'web/api.ts': "export function load() {\n  return fetch('/records');\n}\n",
            'package.json': json.dumps({'name': 'fixture-web', 'private': True, 'dependencies': {'react': '18.3.1'}}, indent=2) + '\n',
            'requirements.txt': 'requests==2.32.3\n',
        },
        'question': ('goals', 'The product imports client files without losing characters, for the operations team.'),
    },
    'engagement': {
        'name': 'Fixture engagement', 'subject': 'writing',
        'request': 'The client asks whether supplier costs can fall by ten percent within a year, and needs a board report with the evidence.',
        'epic': ('Board report on supplier costs', 'deliverables'),
        'story': {'title': 'Draft the board report', 'item_type': 'deliverable', 'paths': ['deliverables/**'],
                  'acceptance': ['The report states each recommendation with its supporting evidence.',
                                 'The report states the confidence interval of each cost estimate.']},
        'chain': ['Interview the main suppliers', 'Analyse the supplier costs', 'Prepare the recommendations'],
        'research': ('Test the hypothesis that freight costs drive the increase', 'research'),
        'review': 'Review the executive summary with the partner',
        'decision': {'decision': 'Collect supplier cost data through a written survey.', 'why': 'A survey reaches every supplier within two weeks.',
                     'expected': 'At least 30 of the 40 suppliers respond.', 'uncertainty': 'The response rate of this supplier group is unknown.',
                     'alternatives': ['Interview the ten largest suppliers.'],
                     'reconsider_when': 'Fewer than half of the suppliers respond after one week.'},
        'failed': 'Only 9 of the 40 suppliers responded to the survey.',
        'revised': 'Interview the ten largest suppliers and keep the survey for the others.',
        'succeeded': 'The interviews covered 72 percent of the supplier spend.',
        'failure_type': 'low_response',
        'lesson': {'when': 'Evidence is collected from suppliers.', 'do': 'Interview the largest suppliers before relying on a survey.',
                   'because': 'The survey response rate was too low to support a finding.',
                   'exceptions': 'Engagements where the client already holds audited cost data.', 'pattern_type': 'recovery',
                   'paths': ['engagement/**']},
        'rule': {'when': 'A recommendation is written for the client.', 'do': 'Name the source of every figure in the same sentence.',
                 'because': 'The partner could not trace two figures in the last report.', 'exceptions': 'Figures the client supplied in writing.',
                 'pattern_type': 'practice', 'roles': ['assistant', 'worker', 'reviewer']},
        'proposed': {'when': 'A board report is drafted.', 'do': 'State the confidence interval next to each estimate.',
                     'because': 'The partner asked for the strength of evidence behind each number.',
                     'exceptions': 'Figures taken directly from audited accounts.', 'pattern_type': 'practice'},
        'recurrence': ('Collect logistics cost data', 'Collect logistics cost data through a written survey.',
                       'Only 4 of the 25 logistics providers responded.'),
        'document': ('engagement/interview-policy.md', policy('Interview policy', 'This policy states how supplier evidence is collected.',
                                                              [('Ten largest suppliers', 'Interview each supplier and record the notes.'),
                                                               ('Other suppliers', 'Send the written survey.')],
                                                              'Suppliers under a confidentiality dispute are not contacted, because the client legal team must approve each contact.')),
        'blocked': 'engagement/brief.md', 'changed': ['deliverables/board-report.md'],
        'components': [
            ('chief-financial-officer', 'Chief financial officer', 'stakeholder', 'Approves the recommendations and the board report.', 'confirmed', None, USER),
            ('procurement-team', 'Procurement team', 'stakeholder', 'Owns the supplier contracts and the cost data.', 'proposed', None, AGENT),
            ('supplier-cost-review', 'Supplier cost review', 'workstream', 'Collects and analyses the supplier cost evidence.', 'confirmed', None, USER),
            ('supplier-interviews', 'Supplier interviews', 'workstream', 'Interviews the ten largest suppliers.', 'proposed', None, AGENT),
            ('board-report', 'Board report', 'deliverable', 'Presents the recommendations to the board.', 'proposed', 'deliverables', AGENT),
        ],
        'links': [('procurement-team', 'supplier-cost-review', 'informs', 'Procurement provides the contract data.'),
                  ('supplier-interviews', 'supplier-cost-review', 'part_of', 'The interviews are part of the cost review.'),
                  ('supplier-cost-review', 'board-report', 'produces', 'The review produces the evidence for the report.'),
                  ('chief-financial-officer', 'board-report', 'owns', 'The chief financial officer approves the report.'),
                  ('story', 'board-report', 'implements', 'The work item drafts the report.')],
        'files': {'deliverables/board-report.md': '# Board report\n\nThis draft presents the recommendations on supplier costs.\n'},
        'question': ('objective', 'The client needs to know whether supplier costs can fall by ten percent within a year.'),
    },
    'automation': {
        'name': 'Fixture automation', 'subject': 'general',
        'request': 'The client asks to route new leads from the website form to the sales team within five minutes, with each lead enriched in HubSpot.',
        'epic': ('Lead intake automation', 'build'),
        'story': {'title': 'Build the lead intake workflow', 'item_type': 'workflow', 'paths': ['workflows/lead-intake.json'],
                  'acceptance': ['The sample lead in automation/test-data reaches the sales channel within five minutes.',
                                 'A lead without an email address goes to the review queue.']},
        'chain': ['Confirm the HubSpot field mapping', 'Build the enrichment workflow', 'Test the workflows with sample leads'],
        'research': ('Confirm the daily lead volume', 'process'),
        'review': 'Review the wording of the Slack message',
        'decision': {'decision': 'Call the OpenAI node for every lead before enrichment.', 'why': 'A summary helps the sales team respond faster.',
                     'expected': 'Each lead reaches Slack within five minutes.', 'uncertainty': 'The rate limit of the OpenAI account is unknown.',
                     'alternatives': ['Summarise only leads with a message longer than 200 characters.'],
                     'reconsider_when': 'A lead takes longer than five minutes to reach Slack.'},
        'failed': 'Leads waited 14 minutes during a burst of 60 leads because the OpenAI node was rate limited.',
        'revised': 'Summarise only long messages and send the other leads directly.',
        'succeeded': 'The sample burst of 60 leads reached Slack within 3 minutes.',
        'failure_type': 'rate_limited',
        'lesson': {'when': 'A workflow calls an outside model for every item.', 'do': 'Test the workflow with a burst of sample items before deployment.',
                   'because': 'A rate limit delayed leads during a burst.', 'exceptions': 'Workflows that run once a day.',
                   'pattern_type': 'recovery', 'paths': ['workflows/**']},
        'rule': {'when': 'A workflow is changed.', 'do': 'State which trigger and which credential the change affects.',
                 'because': 'A changed trigger stopped the lead intake without anyone noticing.', 'exceptions': 'Changes to a disabled workflow.',
                 'pattern_type': 'practice', 'roles': ['assistant', 'worker', 'reviewer']},
        'proposed': {'when': 'A credential is added to a workflow.', 'do': 'Record the credential name and owner in the systems document.',
                     'because': 'A handover missed the owner of the Slack credential.', 'exceptions': 'Credentials that the client manages directly.',
                     'pattern_type': 'practice'},
        'recurrence': ('Route support requests', 'Summarise every support request with the OpenAI node.',
                       'Support requests waited 9 minutes during a burst.'),
        'document': ('automation/data-policy.md', policy('Data policy', 'This policy states which lead data the workflows may move.',
                                                         [('Contact details', 'Move them to HubSpot only.'),
                                                          ('Free text messages', 'Summarise them without storing the original.')],
                                                         'Leads from the partner portal keep the original message, because the partner contract requires it.')),
        'blocked': 'automation/systems.md', 'changed': ['workflows/lead-intake.json'],
        'components': [
            ('hubspot-crm', 'HubSpot CRM', 'system', 'Stores the client contacts and the enriched lead data.', 'confirmed', 'service:hubspot', USER),
            ('slack-workspace', 'Slack workspace', 'system', 'Receives the lead notifications for the sales team.', 'proposed', 'service:slack', AGENT),
            ('lead-intake-workflow', 'Lead intake workflow', 'workflow', 'Receives new leads and notifies the sales team.', 'proposed',
             'n8n:workflows/lead-intake.json', AGENT),
        ],
        'links': [('lead-intake-workflow', 'hubspot-crm', 'uses', 'The workflow enriches each lead in HubSpot.'),
                  ('lead-intake-workflow', 'slack-workspace', 'uses', 'The workflow notifies the sales team in Slack.'),
                  ('story', 'lead-intake-workflow', 'implements', 'The work item builds the workflow.')],
        'files': {'workflows/lead-intake.json': LEAD_INTAKE, 'workflows/enrich-lead.json': ENRICH_LEAD,
                  'automation/test-data/sample-lead.json': json.dumps({'email': 'lead@example.com', 'message': 'We need a quote.'}, indent=2) + '\n'},
        'question': ('process', 'New leads arrive through the website form, and a sales assistant copies each one into HubSpot by hand.'),
    },
}


class Builder:
    """Records the fixture through the real modules and keeps the ids it creates."""

    def __init__(self, memory, root, spec, phases):
        self.m = memory
        self.root = root
        self.spec = spec
        self.phases = phases
        self.ids = {'phases': phases}
        source = memory.source('user:fixture-request', 'User request', 'The user describes the work of this fixture.',
                               spec['request'], 'user', subject=spec['subject'])
        self.evidence = [{'source_id': source['id'], 'reason': 'The user defines this work.'}]
        self.ids['source'] = source['id']

    def work(self, key, title, *, item_type='task', state='ready', parent=None, paths=None, acceptance=None, depends=None, owner='agent'):
        payload = {'state': state, 'next_action': 'Continue with ' + title[0].lower() + title[1:] + '.',
                   'scope': 'The work stays within the request of the user.', 'autonomy': 'act', 'owner': owner,
                   'reason': 'The user requests this work.', 'item_type': item_type}
        if parent:
            payload['parent_id'] = parent
        if paths:
            payload['paths'] = list(paths)
        if acceptance:
            payload['acceptance'] = list(acceptance)
        if depends:
            payload['depends_on'] = [{'episode_id': depends, 'reason': 'The earlier work item provides the input.'}]
        result = save(self.m, 'work_plan', payload=payload, actor=USER, evidence=self.evidence, title=title,
                      objective=title + '.', criterion='The user accepts the result of this work item.',
                      subject=self.spec['subject'], request_key='fixture:plan:' + key)
        self.ids[key] = result['episode_id']
        return result['episode_id']

    def record(self, episode_id, kind, payload, **extra):
        version = self.m.episode(episode_id)['version']
        return self.m.record(episode_id, kind, payload, expected_version=version, actor=AGENT, evidence=self.evidence,
                             request_key='fixture:' + kind + ':' + episode_id + ':' + str(version), **extra)

    def outcome(self, episode_id, decision_id, observed, good):
        payload = {'observed': observed, 'assessment': 'good' if good else 'bad',
                   'assessment_reason': 'The observed result ' + ('meets' if good else 'does not meet') + ' the expectation.',
                   'severity': 'none' if good else 'major', 'attribution': 'The recorded action produced this result.',
                   'completion': 'complete' if good else 'partial'}
        if not good:
            payload['failure_type'] = self.spec['failure_type']
        return self.record(episode_id, 'outcome', payload, decision_id=decision_id)['id']

    def work_items(self):
        spec = self.spec
        epic_title, epic_phase = spec['epic']
        epic = self.work('epic', epic_title, item_type='epic', parent=self.phases[epic_phase])
        story = spec['story']
        self.work('story', story['title'], item_type=story['item_type'], state='in_progress', owner='human', parent=epic,
                  paths=story['paths'], acceptance=story['acceptance'])
        previous = None
        for index, title in enumerate(spec['chain']):
            previous = self.work('chain_' + str(index), title, parent=epic, depends=previous, paths=story['paths'])
        research_title, research_phase = spec['research']
        self.work('research', research_title, item_type='research', parent=self.phases[research_phase])
        review = self.work('review', spec['review'], state='review', parent=epic)
        self.record(review, 'note', {'text': '</script><script>window.injected=true</script> The wording stays as the user approved it.'})

    def decisions(self):
        spec = self.spec
        story = self.ids['story']
        first = self.record(story, 'decision', spec['decision'])
        self.record(story, 'action', {'action': 'Apply the decision to the sample data.'}, decision_id=first['id'])
        self.ids['failed_outcome'] = self.outcome(story, first['id'], spec['failed'], good=False)
        revised = self.record(story, 'decision', {**spec['decision'], 'decision': spec['revised']}, supersedes=first['id'])
        self.record(story, 'action', {'action': 'Apply the revised decision to the sample data.'}, decision_id=revised['id'])
        self.ids['good_outcome'] = self.outcome(story, revised['id'], spec['succeeded'], good=True)
        self.ids.update(decision=first['id'], revised_decision=revised['id'])

    def document(self):
        relative, text = self.spec['document']
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        self.ids['document'] = self.m.document(str(path))['id']

    def learning(self):
        spec = self.spec
        lessons = self.m.start('Lessons from this project', 'Collect the lessons of this project.', 'learning',
                               'Each lesson is accepted or rejected by the user.', subject=spec['subject'])['id']
        self.ids['lessons'] = lessons
        lesson = {key: value for key, value in spec['lesson'].items() if key != 'paths'}
        accepted = self.record(lessons, 'lesson', lesson, links=[{'event_id': self.ids['failed_outcome'],
                                                                   'reason': 'The failed outcome teaches this lesson.'}])
        action(self.m, 'lesson_review', {'lesson_id': accepted['id'], 'expected_version': self.m.episode(lessons)['version'],
                                         'status': 'accepted', 'reason': 'The user accepts the lesson with its triggers.',
                                         'paths': spec['lesson']['paths'], 'failure_type': spec['failure_type'],
                                         'roles': ['worker']},
               'fixture:accept-lesson')
        self.ids['guard'] = accepted['id']
        # A rule with a role and no other trigger is composed into every prompt of that role.
        rule = self.record(lessons, 'lesson', spec['rule'])
        action(self.m, 'lesson_review', {'lesson_id': rule['id'], 'expected_version': self.m.episode(lessons)['version'],
                                         'status': 'accepted', 'reason': 'The user accepts the lesson as a rule for every role.',
                                         'roles': spec['rule']['roles']},
               'fixture:accept-rule')
        self.ids['rule'] = rule['id']
        action(self.m, 'instructions', {'role': 'reviewer', 'text': BASE_TEXT,
                                        'reason': 'The user adjusts the base text of the reviewer.'},
               'fixture:instructions-reviewer')
        self.ids['proposed_lesson'] = self.record(lessons, 'lesson', spec['proposed'])['id']
        title, decision, observed = spec['recurrence']
        episode = self.m.start(title, title + '.', 'action', 'The work finishes without the known failure.', subject=spec['subject'])['id']
        self.ids['recurrence_work'] = episode
        chosen = self.record(episode, 'decision', {**spec['decision'], 'decision': decision})
        self.record(episode, 'action', {'action': 'Apply the decision.'}, decision_id=chosen['id'])
        self.ids['recurrence_outcome'] = self.outcome(episode, chosen['id'], observed, good=False)

    def kickoff(self):
        question, text = self.spec['question']
        templates.answer_kickoff(self.m, question_ids=[question], text=text, actor=USER, request_key='fixture:kickoff',
                                 evidence=self.evidence)

    def scope_block(self):
        story = self.ids['story']
        plan = latest(self.m, story, 'work_plan')
        if not codex_host.exists(self.m):
            codex_host.initialize(self.m)
        with self.m._write():
            receipt = codex_host.receipt(self.m, session_id='fixture-session', event_name='ScopeBlocked', episode_id=story,
                                         key='fixture:scope-block',
                                         payload={'episode_id': story, 'plan_id': plan['id'], 'tool_name': 'Edit',
                                                  'blocked': [self.spec['blocked']], 'allowed_patterns': self.spec['story']['paths'],
                                                  'host': 'codex'})
        self.ids['scope_block'] = receipt['id'] if isinstance(receipt, dict) else receipt

    def run_row(self, role, host, state, *, parent=None, report, metrics, snapshot_extra=None, run_id=None, episode_id=None, routing=None):
        """Insert one finished run. Finished rows are immutable, so every value is final at insert."""
        run_id = run_id or 'check_' + uuid.uuid4().hex
        story = episode_id or self.ids['story']
        snapshot = {'role': role, 'project': str(self.root), 'paths': self.spec['story']['paths'],
                    'independence': 'other_host', **(snapshot_extra or {})}
        now = self.m.now()
        with self.m._write():
            self.m.db.execute('''INSERT INTO review_runs (id,episode_id,role,signature,host,session_id,state,created_at,updated_at,
                request_key,snapshot,report,metrics,error,parent_run,workspace,branch,routing) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                              (run_id, story, role, 'fixture', host, '', state, now, now, 'fixture:run:' + run_id,
                               json.dumps(snapshot), json.dumps(report), json.dumps(metrics), '', parent,
                               '.memory/worktrees/' + run_id if role == 'work' else None,
                               'pm/' + story[8:16] + '-' + run_id[6:14] if role == 'work' else None,
                               json.dumps(routing) if routing else None))
            name = 'DelegationRequested' if role == 'work' else 'WorkReviewRequested'
            codex_host.receipt(self.m, session_id=run_id, event_name=name, episode_id=story, key=run_id + ':requested',
                               payload={'run_id': run_id, 'role': role, 'host': host, 'parent_run': parent})
        return run_id

    def delegated_run(self):
        spec = self.spec
        reviews.configure(self.m, self.root, 'codex')
        reviews.configure(self.m, self.root, 'claude')
        changed = spec['changed']
        diff = ''.join(f'diff --git a/{name} b/{name}\n--- a/{name}\n+++ b/{name}\n@@ -1 +1 @@\n-previous line\n+changed line\n' for name in changed)
        report = {'summary': 'The delegated work changed ' + ', '.join(changed) + '.', 'result': 'complete', 'changed_files': changed,
                  'checks_run': [{'command': 'Compare the result with the sample data.', 'outcome': 'The sample data matches.'}],
                  'notes': 'The fixture inserted this run. No agent ran.', 'lesson_proposals': []}
        work = 'check_' + uuid.uuid4().hex
        source = self.m.source('delegation:' + work, 'Changes of the delegated work', 'The delegated work changed ' + str(len(changed)) + ' files.',
                               diff, 'tool', subject=spec['subject'])
        # Both runs name the rules they composed, so the Learning view can count the runs of a rule.
        worker_rules = [self.ids['rule'], self.ids['guard']]
        routing = {'preferred': 'claude', 'reason': 'headroom', 'decided_at': self.m.now(),
                   'sentence': 'The preferred host claude was not chosen because it hit a usage limit that resets in one hour. '
                               'The codex host was chosen because it has the most headroom among the hosts that are not constrained.'}
        self.run_row('work', 'codex', 'completed', run_id=work, report=report, routing={**routing, 'host': 'codex'},
                     metrics={'changed_files': changed, 'commit': 'f1x7ure0', 'diff_source': source['id'],
                              'instruction_role': 'worker', 'instruction_source': 'instructions:worker',
                              'rule_ids': worker_rules, 'rules_omitted': []})
        review = {'verdict': 'pass', 'summary': 'The work review found that the change meets the acceptance criteria.',
                  'checks': [{'criterion': 'C001', 'evidence': 'The diff changes only the allowed paths.', 'result': 'met'}],
                  'findings': [], 'lesson_proposals': []}
        review_id = self.run_row('work_review', 'claude', 'pass', parent=work, report=review,
                                 routing={'host': 'claude', 'preferred': 'claude', 'reason': 'preferred', 'decided_at': self.m.now(),
                                          'sentence': 'The preferred host claude was chosen because it is not constrained.'},
                                 metrics={'input_tokens': 10, 'instruction_role': 'reviewer',
                                          'instruction_source': 'instructions:reviewer',
                                          'rule_ids': [self.ids['rule']], 'rules_omitted': []})
        hosts.mark_available(self.m, 'codex')
        hosts.mark_unavailable(self.m, 'claude', 'The fixture records a usage limit for this host.')
        self.ids.update(work_run=work, work_review=review_id, diff_source=source['id'])

    def focused_problem(self):
        """A focused problem whose relay start failed its check in attempt 1 and passed in attempt 2.

        The check is written as the user would set it in the panel. Check results, hypothesis results and
        receipts are written under the reserved actor, as focus.judge writes them after it runs the check.
        """
        episode = self.ids['chain_0']
        check_path = 'tests/test_export_format.py'
        (self.root / 'tests').mkdir(exist_ok=True)
        (self.root / check_path).write_text(FOCUS_CHECK_TEXT, encoding='utf-8')
        proposed = focus.propose(self.m, episode, problem=FOCUS_PROBLEM, actor=AGENT, request_key='fixture:focus:propose',
                                 hypotheses=[dict(statement=statement, approach=approach) for statement, approach in FOCUS_HYPOTHESES])
        plan = self.m.read(latest(self.m, episode, 'work_plan')['id'])
        check = {'command': ['python3', '-m', 'unittest', 'tests.test_export_format'], 'timeout_seconds': 120,
                 'files': [{'path': check_path, 'sha256': hashlib.sha256(FOCUS_CHECK_TEXT.encode()).hexdigest()}],
                 'set_at': self.m.now()}
        save(self.m, 'work_plan', episode_id=episode, expected_version=self.m.episode(episode)['version'],
             payload={**plan['payload'], 'focus': {**proposed['focus'], 'check': check}}, actor=USER,
             evidence=[{'source_id': item['source_id'], 'reason': item['reason']} for item in plan['evidence']],
             links=plan['links'], request_key='fixture:focus:check')
        first, second, third = proposed['hypothesis_ids']
        runs = {}
        for attempt, host, hypothesis_id, lines in ((1, 'codex', first, 14), (2, 'claude', second, 6)):
            usage = [{'input_tokens': 1800, 'output_tokens': 420}] if host == 'codex' else {'input_tokens': 2100, 'output_tokens': 380}
            report = {'summary': f'Attempt {attempt} changed the export format.', 'result': 'complete',
                      'changed_files': ['src/app/export.py'], 'checks_run': [], 'notes': '', 'lesson_proposals': []}
            runs[attempt] = self.run_row('work', host, 'completed', episode_id=episode, report=report,
                                         metrics={'changed_files': ['src/app/export.py'], 'commit': 'f0c05' + str(attempt),
                                                  'duration_ms': 64000 + attempt * 1000, 'provider_usage': usage},
                                         snapshot_extra={'focus': {'problem': FOCUS_PROBLEM, 'attempt': attempt, 'start_key': 'fixture-start'}})
        review = {'verdict': 'pass', 'summary': 'The work review found that attempt 2 meets the acceptance criteria.',
                  'checks': [], 'findings': [], 'lesson_proposals': []}
        review_id = self.run_row('work_review', 'codex', 'pass', parent=runs[2], episode_id=episode, report=review, metrics={})
        evidence = {1: 'The check failed with exit code 1. Last output line: FAILED (failures=1).', 2: 'The check passed with exit code 0.'}
        output = {1: 'F.\nFAIL: test_dates_keep_the_time_zone\nAssertionError: the export dropped the time zone\nFAILED (failures=1)\n',
                  2: '..\nRan 2 tests in 0.004s\n\nOK\n'}
        for hypothesis_id, attempt, state in ((first, 1, 'ruled_out'), (second, 2, 'confirmed')):
            record = self.m.read(hypothesis_id)
            self.m.record(episode, 'hypothesis', {**record['payload'], 'state': state, 'run_id': runs[attempt], 'attempt': attempt,
                                                  'evidence_summary': evidence[attempt]},
                          expected_version=self.m.episode(episode)['version'], supersedes=hypothesis_id, actor=focus.FOCUS_ACTOR,
                          request_key='fixture:focus:result:' + str(attempt))
        base = {'episode_id': episode, 'start_key': 'fixture-start'}
        # A start names its swarm, which makes its check results verified command bases in that swarm (section 12.9).
        swarm = {'swarm_id': self.swarm_id} if getattr(self, 'swarm_id', None) else {}
        receipts = [('FocusStarted', {**base, 'mode': 'relay', 'attempts': 2, 'check': check, 'hypothesis_ids': [first, second, third], **swarm})]
        for attempt, host, hypothesis_id in ((1, 'codex', first), (2, 'claude', second)):
            receipts.append(('FocusAttemptRequested', {**base, 'attempt': attempt, 'run_id': runs[attempt], 'host': host, 'hypothesis_id': hypothesis_id}))
            receipts.append(('FocusCheckRecorded', {**base, 'attempt': attempt, 'run_id': runs[attempt], 'host': host,
                                                    'hypothesis_id': hypothesis_id, 'command': check['command'],
                                                    'check_passed': attempt == 2, 'exit_code': 0 if attempt == 2 else 1,
                                                    'duration_ms': 1200 + attempt * 100, 'output_tail': output[attempt], 'timed_out': False,
                                                    'changed_lines': 14 if attempt == 1 else 6, 'evidence_summary': evidence[attempt]}))
        receipts += [('FocusSelected', {**base, 'run_id': runs[2], 'attempt': 2, 'reason': 'The check passed for this attempt.'}),
                     ('FocusCompleted', {**base, 'run_id': runs[2], 'review_state': 'pass', 'merge': 'merged'})]
        checks = {}
        with self.m._write():
            for name, payload in receipts:
                receipt = codex_host.receipt(self.m, session_id=episode, event_name=name, episode_id=episode, payload=payload,
                                             key='focus:' + episode + ':fixture-start:' + name + ':' + payload.get('run_id', ''))
                if name == 'FocusCheckRecorded':
                    checks[payload['attempt']] = receipt
            codex_host.receipt(self.m, session_id=runs[1], event_name='DelegationDiscarded', episode_id=episode, key='fixture:focus:discard',
                               payload={'run_id': runs[1], 'reason': 'The check of the focused problem failed for this attempt.',
                                        'actor': focus.FOCUS_ACTOR})
            codex_host.receipt(self.m, session_id=runs[2], event_name='DelegationMerged', episode_id=episode, key='fixture:focus:merge',
                               payload={'run_id': runs[2], 'commit': 'f0c0mm1', 'branch_commit': 'f0c052', 'override_reason': None,
                                        'actor': focus.FOCUS_ACTOR, 'review_id': review_id, 'review_state': 'pass'})
        self.ids.update(focus_item=episode, focus_runs=[runs[1], runs[2]], focus_hypotheses=[first, second, third], focus_checks=checks)

    def open_hive_swarm(self):
        """Open the swarm of the hive fixture on the focused work item, before the focused start that names it is recorded."""
        with hive.Hive(hive.path_for(self.m)) as store:
            self.swarm_id = hive.open_swarm(store, title=HIVE_TITLE, purpose='Find why the export shows the wrong delivery day for the sales team.',
                                            kind='manual', request_key='fixture:hive:open', episode_id=self.ids['chain_0'])['id']

    def hive_swarm(self):
        """A blind manual swarm on the focused work item of the product fixture with three agents and the user.

        codex-1 posts its hypothesis, the passing check of the focused start as a verified command basis, a confirmed
        conclusion and answers the user. claude-1 supports and challenges the work of codex-1, codex-1 replies to the
        challenge, and claude-1 asks the user a question that is still open.
        codex-2 has only oriented, so it is still in the blind phase. HIVE_LATE is appended later by
        append_hive_entry while the page is open, and it ends the blind phase of codex-2.
        """
        swarm, receipt = self.swarm_id, self.ids['focus_checks'][2]
        with hive.Hive(hive.path_for(self.m)) as store:
            for agent, host in (('codex-1', 'codex'), ('claude-1', 'claude'), ('codex-2', 'codex')):
                hive.join(store, swarm, agent_id=agent, role='worker', host=host, worktree=self.root)
            hive.join(store, swarm, agent_id=USER, role='user', host=USER, worktree=self.root)
            count = [0]

            def log(agent, move, **fields):
                count[0] += 1
                return hive.log(store, swarm, agent, move=move, request_key='fixture:hive:' + str(count[0]), fields=fields, memory=self.m)['id']
            ids = {}
            log('codex-1', 'orient', claim='The goal is an export that keeps the delivery day of every order.',
                bases=[{'kind': 'file', 'value': 'src/app/export.py:1-4'}])
            log('claude-1', 'orient', claim='The goal of this agent is a parser that keeps the time zone.',
                bases=[{'kind': 'file', 'value': 'src/app/parser.py:1'}])
            log('codex-2', 'orient', claim='The goal here is a date format that the sales team reads correctly.',
                bases=[{'kind': 'file', 'value': 'src/app/main.py:1'}])
            ids['hypothesis'] = log('codex-1', 'hypothesis', claim='The export drops the offset when it formats each date.',
                                    detail='Format a date with an offset and compare the exported text.')
            ids['observation'] = log('codex-1', 'observation', claim='The export test passes once the offset is kept.',
                                     bases=[{'kind': 'command', 'value': receipt}])
            ids['conclusion'] = log('codex-1', 'conclusion', claim='Keeping the offset in the export fixes the delivery day.',
                                    confidence='high', cites=[ids['observation']])
            ids['question'] = log(USER, 'question', claim='Does the fix also change the dates in the archive export?',
                                  addressee='agent:codex-1')
            ids['answer'] = log('codex-1', 'answer', claim='The archive export reads the same formatter, so it changes too.',
                                target=ids['question'])
            log('claude-1', 'hypothesis', claim='The parser converts each date to local time too early.',
                detail='Parse a date with an offset and read the stored value.')
            ids['support'] = log('claude-1', 'support', claim='The parser keeps the offset, which agrees with the conclusion.',
                                 target=ids['conclusion'], bases=[{'kind': 'file', 'value': 'src/app/parser.py:4-6'}])
            ids['challenge'] = log('claude-1', 'challenge', claim='The main module may still format dates without the offset.',
                                   target=ids['conclusion'], bases=[{'kind': 'file', 'value': 'src/app/main.py:5-6'}])
            ids['reply'] = log('codex-1', 'observation', claim='The main module passes the records to the export unchanged.',
                               bases=[{'kind': 'file', 'value': 'src/app/main.py:5-6'}], reply_to=ids['challenge'])
            ids['user_question'] = log('claude-1', 'question', claim='Should the export show each day in the time zone of the customer?',
                                       addressee='user')
        self.ids['hive'] = {'swarm': swarm, 'entries': ids}

    def session_flags(self):
        """Four open flags of one finished session, so Now decides one in its pane and Sessions offers the row actions and the batch decision."""
        sessions.ensure(self.m)
        with self.m._write():
            for turn, excerpt in enumerate(('No, keep the offset of the delivery day.', 'Do not change the export format.', 'Use the second file instead.',
                                            'Wait for the review before the release.'), start=1):
                self.m.db.execute('INSERT INTO session_flags VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                                  (f'flag_fixture_{turn}', 'claude:fixture', turn, turn * 4, '2026-09-10T10:0%d:00+00:00' % turn,
                                   'correction', excerpt, 'open', '2026-09-10T12:00:00+00:00', None, None, None))

    def machine_memory(self):
        """One rule already accepted into the machine memory and one proposal that awaits the user."""
        accepted = machine.propose(self.m, **ACCEPTED_RULE, basis='Two delegated runs changed work that nobody had named.',
                                   roles=['worker'], actor=AGENT, request_key='fixture:promote:accepted')
        result = action(self.m, 'promotion', {'promotion_id': accepted['id'], 'status': 'accepted',
                                              'reason': 'The rule holds for every project on this computer.'},
                        'fixture:promotion:accepted')
        waiting = machine.propose(self.m, **PROPOSED_RULE, basis='A merge without a recorded check result was corrected twice.',
                                  roles=['reviewer', 'worker'], actor=AGENT, request_key='fixture:promote:proposed')
        self.ids.update(machine_rule=result['machine_rule_id'], accepted_promotion=accepted['id'],
                        proposed_promotion=waiting['id'])

    def usage_ledger(self):
        """Usage of this machine: Codex at 91 percent of its 5 hour window, a Claude limit hit that has not reset,
        OpenCode runs with a cost, and a passing probe of Grok. Only counts, times and states are written."""
        now = datetime.now(timezone.utc)
        stamp = lambda value: value.isoformat(timespec='microseconds')
        path = machine.database_path()
        machine.initialize(path)
        with Memory(path) as store:
            machine.ensure_usage(store)
            if not codex_host.exists(store):
                codex_host.initialize(store)
            with store._write():
                for index, (host, hours, total, cost) in enumerate((('codex', 1, 48000, None), ('codex', 30, 120000, None),
                                                                    ('claude', 2, 36000, None), ('opencode', 3, 28291, 0.085))):
                    ended = stamp(now - timedelta(hours=hours))
                    store.db.execute('INSERT INTO usage_records (entry,host,kind,started_at,ended_at,input_tokens,output_tokens,total_tokens,'
                                     'cost,currency,recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                                     ('fixture-' + str(index), host, 'session' if cost is None else 'run', ended, ended,
                                      total - 1000, 1000, total, cost, 'USD' if cost is not None else None, stamp(now)))
                store.db.execute("INSERT INTO usage_limits VALUES ('codex','codex','primary',91,300,?,?)",
                                 (stamp(now + timedelta(hours=2)), stamp(now)))
                store.db.execute("INSERT INTO usage_limit_hits VALUES ('fixture-hit','claude','usage_limit',?,?)",
                                 (stamp(now - timedelta(minutes=5)), stamp(now + timedelta(hours=1))))
                codex_host.receipt(store, session_id='probe:grok', event_name=hosts.PROBE_EVENT, key='fixture:probe:grok',
                                   payload={'host': 'grok', 'version': 'grok 0.2.93', 'runner': 'process', 'passed': True,
                                            'roles': ['review', 'work'], 'checks': [], 'measured': {}, 'seconds': 41.0})

    def components(self):
        created = {}
        for key, title, kind, description, status, path, actor in self.spec['components']:
            result = architecture.save_component(self.m, component_id=key, title=title, kind=kind, description=description,
                                                 status=status, actor=actor, request_key='fixture:component:' + key,
                                                 evidence=self.evidence, path=path)
            created[key] = result['id']
        for index, (first, second, link_type, reason) in enumerate(self.spec['links']):
            graph.link(self.m, from_id=self.ids.get(first) or created[first], to_id=created.get(second) or self.ids[second],
                       type=link_type, reason=reason, actor=USER, request_key='fixture:link:' + str(index))
        self.ids['components'] = created


HIVE_TITLE = 'Wrong delivery day in the export'
HIVE_LATE = 'The template of the export cuts the offset from the date text.'


def append_hive_entry(database):
    """Append the hypothesis of codex-2 to the fixture swarm, as a worker does while the panel is open."""
    with Memory(database) as memory, hive.Hive(hive.path_for(memory)) as store:
        swarm = next(row['id'] for row in hive.swarms(store)['swarms'] if row['title'] == HIVE_TITLE)
        return hive.log(store, swarm, 'codex-2', move='hypothesis', request_key='fixture:hive:late', memory=memory,
                        fields={'claim': HIVE_LATE, 'detail': 'Export one order and read the date text.'})


def build(kind, output, with_focus=False, with_hive=False):
    """Create the fixture project in an empty or missing folder and return its description."""
    spec = KINDS[kind]
    root = Path(output).expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        raise SystemExit('The output folder must be empty or missing, because the fixture records cannot be created twice.')
    root.mkdir(parents=True, exist_ok=True)
    # The machine memory of the fixture stays inside the fixture folder, so no check reads the one of this computer.
    os.environ[machine.DATABASE_VARIABLE] = str(root / '.memory' / 'machine.sqlite')
    for relative, text in spec['files'].items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
    result = templates.scaffold(root, kind, name=spec['name'], clients=(), git=False, _launcher=LAUNCHER)
    phases = {phase['key']: phase['episode_id'] for phase in result['phases']}
    with Memory(result['database']) as memory:
        builder = Builder(memory, root, spec, phases)
        builder.work_items()
        builder.decisions()
        builder.document()
        builder.learning()
        builder.kickoff()
        builder.scope_block()
        builder.delegated_run()
        builder.components()
        builder.machine_memory()
        builder.usage_ledger()
        if with_hive:
            builder.session_flags()
            builder.open_hive_swarm()
        if with_focus:
            builder.focused_problem()
        if with_hive:
            builder.hive_swarm()
        ids = builder.ids
    return {'kind': kind, 'project': str(root), 'database': result['database'], 'ids': ids}


def serve(description):
    """Start the live control panel with host programs that refuse to run."""
    from memory_module import live
    root = Path(description['project'])
    fake = root / '.memory' / 'fixture-host'
    fake.write_text('#!' + sys.executable + '\nimport sys\nsys.stderr.write("The fixture host does not run agents.\\n")\nsys.exit(1)\n',
                    encoding='utf-8')
    fake.chmod(0o755)
    os.environ[machine.DATABASE_VARIABLE] = str(root / '.memory' / 'machine.sqlite')
    os.environ['PROJECT_MEMORY_CODEX_BIN'] = str(fake)
    os.environ['PROJECT_MEMORY_CLAUDE_BIN'] = str(fake)
    os.environ['PYTHONPATH'] = str(ROOT) + (os.pathsep + os.environ['PYTHONPATH'] if os.environ.get('PYTHONPATH') else '')
    info = live.start(description['database'])
    return {**description, 'url': info['url']}


def export(description, destination):
    """Write the offline snapshot of the fixture and return its path."""
    from memory_module import viewer
    with Memory(description['database']) as memory:
        viewer.export_html(memory, destination, include_bodies=True)
    return str(Path(destination).resolve())


def main():
    parser = argparse.ArgumentParser(description='Build a control panel fixture project.')
    parser.add_argument('--kind', choices=sorted(KINDS))
    parser.add_argument('--output')
    parser.add_argument('--serve', action='store_true', help='Start the live control panel and print its URL.')
    parser.add_argument('--export', dest='snapshot', help='Write the offline snapshot to this file and name it in the printed JSON.')
    parser.add_argument('--focus', action='store_true', help='Add a focused problem with a finished relay start of two attempts.')
    parser.add_argument('--hive', action='store_true', help='Add a hive swarm with three agents and the user (product with --focus only).')
    parser.add_argument('--append-hive-entry', dest='append', metavar='DATABASE',
                        help='Append the late hypothesis of codex-2 to the swarm of this fixture database and print the entry.')
    args = parser.parse_args()
    if args.append:
        print(json.dumps(append_hive_entry(args.append)))
        return
    if not args.kind or not args.output:
        parser.error('--kind and --output are required to build a fixture.')
    if args.hive and (args.kind != 'product' or not args.focus):
        parser.error('--hive needs the product fixture with --focus, because the hive entries cite its source files and its check.')
    description = build(args.kind, args.output, with_focus=args.focus, with_hive=args.hive)
    if args.snapshot:
        description['snapshot'] = export(description, args.snapshot)
    if args.serve:
        description = serve(description)
    print(json.dumps(description, indent=2))


if __name__ == '__main__':
    main()
