"""Build fixture projects for the control panel browser tests and for reviewers.

    python3 tests/browser/fixture.py --kind product|engagement|automation --output <dir> [--serve]

The project is scaffolded from its template with no hook clients, then filled
through the real modules: work items with types, parents, acceptance criteria and
a blocked dependency chain; a decision with a failed outcome and a successful
revision; a document with an exception; an accepted lesson with path triggers and
a recurrence; a proposed lesson; a scope block receipt; a completed delegated run
with a passing work review that awaits a merge; authored components with links;
and project files for the template (a small source tree, engagement documents or
exported n8n workflows).

Agent runs are inserted as finished rows. No host process runs, and the project is
not a git repository, so delegation from the panel stops with a message. With
--serve, the host programs point at a fixture program that refuses to run, so a
check requested from the panel cannot start a real agent either.

The script prints JSON with the project, the database path, the record ids and,
with --serve, the URL of the live control panel.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memory_module import Memory, architecture, codex_host, graph, hosts, reviews, templates  # noqa: E402
from memory_module.planning import latest, save  # noqa: E402
from memory_module.workspace import action  # noqa: E402

LAUNCHER = [sys.executable, '-m', 'memory_module.cli']
USER = 'workspace-user'
AGENT = 'assistant'


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
                                         'paths': spec['lesson']['paths'], 'failure_type': spec['failure_type']},
               'fixture:accept-lesson')
        self.ids['guard'] = accepted['id']
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

    def run_row(self, role, host, state, *, parent=None, report, metrics, snapshot_extra=None, run_id=None):
        """Insert one finished run. Finished rows are immutable, so every value is final at insert."""
        run_id = run_id or 'check_' + uuid.uuid4().hex
        story = self.ids['story']
        snapshot = {'role': role, 'project': str(self.root), 'paths': self.spec['story']['paths'],
                    'independence': 'other_host', **(snapshot_extra or {})}
        now = self.m.now()
        with self.m._write():
            self.m.db.execute('''INSERT INTO review_runs (id,episode_id,role,signature,host,session_id,state,created_at,updated_at,
                request_key,snapshot,report,metrics,error,parent_run,workspace,branch) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                              (run_id, story, role, 'fixture', host, '', state, now, now, 'fixture:run:' + run_id,
                               json.dumps(snapshot), json.dumps(report), json.dumps(metrics), '', parent,
                               '.memory/worktrees/' + run_id if role == 'work' else None,
                               'pm/' + story[8:16] + '-' + run_id[6:14] if role == 'work' else None))
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
        self.run_row('work', 'codex', 'completed', run_id=work, report=report,
                     metrics={'changed_files': changed, 'commit': 'f1x7ure0', 'diff_source': source['id']})
        review = {'verdict': 'pass', 'summary': 'The work review found that the change meets the acceptance criteria.',
                  'checks': [{'criterion': 'C001', 'evidence': 'The diff changes only the allowed paths.', 'result': 'met'}],
                  'findings': [], 'lesson_proposals': []}
        review_id = self.run_row('work_review', 'claude', 'pass', parent=work, report=review, metrics={'input_tokens': 10})
        hosts.mark_available(self.m, 'codex')
        hosts.mark_unavailable(self.m, 'claude', 'The fixture records a usage limit for this host.')
        self.ids.update(work_run=work, work_review=review_id, diff_source=source['id'])

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


def build(kind, output):
    """Create the fixture project in an empty or missing folder and return its description."""
    spec = KINDS[kind]
    root = Path(output).expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        raise SystemExit('The output folder must be empty or missing, because the fixture records cannot be created twice.')
    root.mkdir(parents=True, exist_ok=True)
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
    parser.add_argument('--kind', choices=sorted(KINDS), required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--serve', action='store_true', help='Start the live control panel and print its URL.')
    parser.add_argument('--export', dest='snapshot', help='Write the offline snapshot to this file and name it in the printed JSON.')
    args = parser.parse_args()
    description = build(args.kind, args.output)
    if args.snapshot:
        description['snapshot'] = export(description, args.snapshot)
    if args.serve:
        description = serve(description)
    print(json.dumps(description, indent=2))


if __name__ == '__main__':
    main()
