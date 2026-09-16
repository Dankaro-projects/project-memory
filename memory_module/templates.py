"""Project templates for a software product, a consulting engagement and a workflow automation.

A template writes short starter documents, connects the selected clients,
captures the documents as evidence and records one work item per phase. The
same planning pattern serves every project kind: goals, existing research,
research still needed, a solution design, and stories or deliverables. Nothing
in a template assumes that the work is code.
"""
import hashlib
import json
from pathlib import Path
import re
import subprocess

from .core import Conflict, InvalidRecord, _text, dumps
from .direction import PLACEHOLDER

SETTING = 'project_template'
ACTOR = 'project-memory-template'
QUESTION_ID = re.compile(r'[a-z][a-z0-9_]{0,59}')
DONE_STATES = ('done', 'cancelled')
PHASE_MOVE = ('A phase moves forward through its own work item: set its state to in_progress when its work starts, '
              'and to done when its completion criterion is met.')


def _document(title, purpose, sections):
    """A skeleton with one sentence per heading. It contains no project facts."""
    text = '# ' + title + '\n\n' + purpose + '\n'
    for heading, sentence in sections:
        text += '\n## ' + heading + '\n\n' + sentence + '\n'
    return text


def _phase(key, title, objective, criterion, next_action, scope, *, subject='general', owner='agent', paths=None):
    return {'key': key, 'title': title, 'objective': objective, 'criterion': criterion, 'next_action': next_action,
            'scope': scope, 'subject': subject, 'owner': owner, 'paths': list(paths or [])}


TEMPLATES = {
    'product': {
        'title': 'Software product',
        'description': 'A software product moves from goals and research to an approved requirements baseline, an architecture, user stories with acceptance criteria, and then build, review and release.',
        'subjects': ['general', 'research', 'code'],
        'layer': 'code',
        'documents': {
            'docs/brief.md': _document('Product brief', 'This document states what the product must achieve and for whom.', [
                ('Goals', 'Describe the problem the product solves and the result it must achieve.'),
                ('Users', 'Describe the main users and what they need to do.'),
                ('Constraints', 'List the budget, deadlines, technology, regulation and other limits that apply.'),
                ('Success measures', 'State how success will be measured, with a number where one is available.'),
                ('Out of scope', 'List what the first release will not include.'),
            ]),
            'docs/research.md': _document('Research', 'This document separates research that exists from research that is still needed.', [
                ('Existing research', 'Summarise research that already exists and link to each source.'),
                ('Research still needed', 'List each open question that research must answer before a decision depends on it.'),
                ('Sources', 'List the documents, interviews and data that the research uses.'),
            ]),
            'docs/architecture.md': _document('Architecture', 'This document describes how the product is structured.', [
                ('Context', 'Describe the users and outside systems that the product interacts with.'),
                ('Components', 'Describe each main component and its responsibility.'),
                ('Data', 'Describe the main data the product stores or exchanges.'),
                ('Integrations', 'Describe each outside service the product depends on.'),
                ('Open questions', 'List architecture questions that still need a decision.'),
            ]),
            'docs/stories.md': _document('User stories', 'This document lists user stories and how each one is accepted.', [
                ('Stories', 'Write each story as the user, the need and the benefit.'),
                ('Acceptance criteria', 'Write complete sentences that state when each story is accepted.'),
                ('Priorities', 'State the order in which the stories should be delivered.'),
            ]),
        },
        'phases': [
            _phase('goals', 'Goals and brief', 'Record the product goals, users, constraints and success measures in the brief.',
                   'The brief states the goals, the users, the constraints and how success is measured.',
                   'Ask the user the kickoff questions and fill in the product brief with the answers.',
                   'Draft the product brief from the answers of the user. Do not invent facts about the product.',
                   paths=['docs/brief.md']),
            _phase('research', 'Research (existing and needed)', 'Capture existing research and list the research that is still needed.',
                   'Existing research is captured as evidence, and each open question is recorded as a research work item.',
                   'Capture the existing research documents and record the questions that remain open.',
                   'Collect and summarise research. Record each gap as a research work item instead of guessing the answer.',
                   subject='research', paths=['docs/research.md']),
            _phase('requirements', 'Requirements approval', 'Obtain the approval of the user for the product requirements.',
                   'The user has approved a requirements baseline with evidence.',
                   'Present the proposed requirements to the user and ask for approval.',
                   'Propose requirements from the brief and the research. Only the user approves them.',
                   owner='human', paths=['docs/brief.md', 'docs/research.md']),
            _phase('architecture', 'Architecture', 'Describe the components, data and integrations of the product.',
                   'The architecture document and the authored components describe the product, and the user has confirmed the components.',
                   'Draft the architecture document and propose the main components.',
                   'Design the structure of the product. Proposed components stay proposed until the user confirms them.',
                   paths=['docs/architecture.md']),
            _phase('stories', 'User stories and acceptance', 'Write user stories with acceptance criteria for the approved requirements.',
                   'Each story has acceptance criteria and belongs to an epic or a phase.',
                   'Draft the stories and record each one as a story work item with acceptance criteria.',
                   'Write stories that trace to the approved requirements. Do not add scope that the user has not approved.',
                   paths=['docs/stories.md']),
            _phase('build', 'Build', 'Build the product according to the approved stories.',
                   'Each story in scope has a good and complete outcome with evidence.',
                   'Select a ready story and record its plan with the paths it may change.',
                   'Build approved stories one at a time. Each story records its own allowed paths.',
                   subject='code'),
            _phase('release', 'Review and release', 'Review the finished work with the user and release it.',
                   'The user has reviewed the result and approved the release.',
                   'Present the completed stories and the results of their checks to the user.',
                   'Prepare the release for review. Only the user approves the release.',
                   owner='human'),
        ],
        'questions': [
            {'id': 'goals', 'phase': 'goals', 'text': 'What problem does the product solve, and for whom?'},
            {'id': 'users', 'phase': 'goals', 'text': 'Who are the main users, and what do they need to do?'},
            {'id': 'success', 'phase': 'goals', 'text': 'How will success be measured?'},
            {'id': 'constraints', 'phase': 'goals', 'text': 'Which constraints apply, such as budget, deadlines, technology or regulation?'},
            {'id': 'existing_research', 'phase': 'research', 'text': 'Which research already exists, and where is it kept?'},
            {'id': 'open_questions', 'phase': 'research', 'text': 'Which questions must research still answer?'},
            {'id': 'out_of_scope', 'phase': 'goals', 'text': 'What is out of scope for the first release?'},
        ],
    },
    'engagement': {
        'title': 'Consulting engagement',
        'description': 'A consulting engagement moves from scope and stakeholders to research and evidence, analysis, recommendations and deliverables, client review and handover.',
        'subjects': ['general', 'research', 'writing'],
        'layer': 'authored',
        'documents': {
            'engagement/brief.md': _document('Engagement brief', 'This document states what the client needs from the engagement.', [
                ('Client objectives', 'Describe the decision or outcome that the client needs.'),
                ('Scope', 'State what is in scope and what is out of scope.'),
                ('Timeline', 'List the agreed milestones and dates.'),
                ('Constraints', 'List the limits on budget, access, confidentiality and time.'),
                ('Success measures', 'State how the client will judge the engagement.'),
            ]),
            'engagement/stakeholders.md': _document('Stakeholders', 'This document lists the people and groups that shape the engagement.', [
                ('Stakeholder map', 'List each stakeholder, their role and their influence on the decision.'),
                ('Interests and concerns', 'Describe what each stakeholder needs and what concerns them.'),
                ('Decision makers', 'Name who approves the recommendations and the deliverables.'),
                ('Hypotheses', 'List the hypotheses that the engagement will test with evidence.'),
            ]),
            'engagement/research.md': _document('Research and evidence', 'This document separates evidence that exists from evidence that is still needed.', [
                ('Existing evidence', 'Summarise the data, reports and interviews that already exist.'),
                ('Evidence still needed', 'List each question that needs new evidence before a finding depends on it.'),
                ('Sources', 'List each source with its date and owner.'),
            ]),
            'engagement/findings.md': _document('Findings', 'This document records what the evidence shows.', [
                ('Findings', 'State each finding as a complete sentence with its supporting evidence.'),
                ('Analysis', 'Explain how the findings relate to the hypotheses and the client objectives.'),
                ('Open questions', 'List the questions that the evidence does not yet answer.'),
            ]),
            'deliverables/README.md': _document('Deliverables', 'This folder holds the deliverables of the engagement.', [
                ('Deliverable list', 'List each deliverable with its audience and due date.'),
                ('Formats', 'State the format that the client expects for each deliverable.'),
                ('Review status', 'Record the review state of each deliverable.'),
            ]),
        },
        'phases': [
            _phase('scope', 'Scope and brief', 'Record the client objectives, scope, timeline and constraints in the engagement brief.',
                   'The engagement brief states the objectives, the scope, the timeline and how success is judged.',
                   'Ask the user the kickoff questions and fill in the engagement brief with the answers.',
                   'Draft the engagement brief from the answers of the user. Do not invent facts about the client.',
                   paths=['engagement/brief.md']),
            _phase('stakeholders', 'Stakeholders and hypotheses', 'Map the stakeholders and state the hypotheses to test.',
                   'The stakeholder map names the decision makers, and each hypothesis is recorded.',
                   'Draft the stakeholder map and propose stakeholders and workstreams as components.',
                   'Describe stakeholders and hypotheses. Proposed components stay proposed until the user confirms them.',
                   subject='research', paths=['engagement/stakeholders.md']),
            _phase('research', 'Research and evidence', 'Capture existing evidence and gather the evidence that is still needed.',
                   'Existing evidence is captured, and each open question is a research work item with a result or a stated gap.',
                   'Capture the existing evidence and record the open questions as research work items.',
                   'Collect evidence and record gaps. Do not state a finding without evidence.',
                   subject='research', paths=['engagement/research.md']),
            _phase('analysis', 'Analysis and synthesis', 'Analyse the evidence and record the findings.',
                   'Each finding is supported by recorded evidence and relates to a hypothesis or an objective.',
                   'Draft the findings from the recorded evidence.',
                   'Analyse recorded evidence only. State uncertainty and gaps explicitly.',
                   subject='research', paths=['engagement/findings.md']),
            _phase('deliverables', 'Recommendations and deliverables', 'Write the recommendations and prepare the deliverables.',
                   'Each deliverable is drafted, traces to the findings and has acceptance criteria.',
                   'Record each deliverable as a deliverable work item with acceptance criteria.',
                   'Prepare deliverables from the findings. Do not add recommendations that the evidence does not support.',
                   subject='writing', paths=['deliverables']),
            _phase('client_review', 'Client review', 'Review the deliverables with the client.',
                   'The client has reviewed each deliverable, and each requested change is recorded.',
                   'Present the deliverables to the client and record the feedback.',
                   'Collect client feedback. Only the user records the client approval.',
                   subject='writing', owner='human', paths=['deliverables']),
            _phase('handover', 'Handover', 'Hand over the final deliverables and the open questions to the client.',
                   'The client has received the final deliverables and the list of open questions.',
                   'Prepare the handover list of deliverables, evidence and open questions.',
                   'Prepare the handover. Only the user confirms that the handover is complete.',
                   owner='human', paths=['deliverables/README.md']),
        ],
        'questions': [
            {'id': 'objective', 'phase': 'scope', 'text': 'What decision or outcome does the client need from this engagement?'},
            {'id': 'scope', 'phase': 'scope', 'text': 'What is in scope, and what is out of scope?'},
            {'id': 'stakeholders', 'phase': 'stakeholders', 'text': 'Who are the stakeholders, and who makes the final decisions?'},
            {'id': 'deliverables', 'phase': 'deliverables', 'text': 'Which deliverables are expected, in which format and by when?'},
            {'id': 'existing_evidence', 'phase': 'research', 'text': 'Which evidence and data already exist?'},
            {'id': 'hypotheses', 'phase': 'stakeholders', 'text': 'Which hypotheses should the engagement test?'},
            {'id': 'client_review', 'phase': 'client_review', 'text': 'How and when will the client review the work?'},
        ],
    },
    'automation': {
        'title': 'Workflow automation',
        'description': 'A workflow automation moves from process discovery and a systems inventory to a solution design, workflow stories with test data, built n8n workflows, tests with sample data, and deployment and handover.',
        'subjects': ['general', 'research'],
        'layer': 'n8n',
        'documents': {
            'automation/process.md': _document('Process', 'This document describes the process that the automation will run.', [
                ('Current process', 'Describe each step of the process as it runs today and who performs it.'),
                ('Triggers and volumes', 'State what starts the process, how often it runs and how many items it handles.'),
                ('Pain points', 'Describe the delays, errors and manual work that the automation should remove.'),
                ('Exceptions', 'List the cases that do not follow the normal steps.'),
            ]),
            'automation/systems.md': _document('Systems and credentials', 'This document lists the systems and accounts that the workflows use. Record credential names and owners only, and never write a secret in this document.', [
                ('Systems', 'List each system, its purpose in the process and its owner.'),
                ('Credentials and access', 'List the name and owner of each credential and how access is granted.'),
                ('Data', 'Describe the data that moves between the systems and any rules that protect it.'),
            ]),
            'automation/solution-design.md': _document('Solution design', 'This document describes the workflows that will automate the process.', [
                ('Workflows', 'List each workflow, its trigger and its result.'),
                ('Data flow', 'Describe how data moves through the workflows and the systems.'),
                ('Error handling', 'Describe what happens when a step fails and who is notified.'),
                ('Test data', 'Describe the sample data that tests each workflow and the expected results.'),
            ]),
            'workflows/README.md': _document('Workflows', 'This folder holds the exported n8n workflows as JSON files.', [
                ('Exported workflows', 'Store each exported workflow as a JSON file in this folder, so that the architecture view can read it.'),
                ('Secrets and client data', 'An export keeps credential names and ids, but a node can still hold a token, a password or a key in its parameters, and pinned data can hold client records. Remove these values before an export is saved here, because delegated work stores the changes of these files in the project records.'),
                ('Naming', 'Describe how workflow files are named.'),
                ('Deployment', 'Describe where the workflows run and how a new version is deployed.'),
            ]),
        },
        'phases': [
            _phase('process', 'Process discovery', 'Describe the process that will be automated, including its triggers, volumes and exceptions.',
                   'The process document describes each step, the trigger, the volumes and the exceptions.',
                   'Ask the user the kickoff questions and fill in the process document with the answers.',
                   'Describe the current process from the answers of the user. Do not invent steps or volumes.',
                   subject='research', paths=['automation/process.md']),
            _phase('systems', 'Systems and credentials inventory', 'List the systems, credentials and data owners that the workflows need.',
                   'Each system and credential is listed with its owner, and no secret is recorded.',
                   'Fill in the systems document and propose each system as a component. When an exported workflow also uses the system, read the architecture with the n8n layer first and copy the exact service name it lists into the component path, for example service:slack, so that the architecture shows one node for it. The name of an HTTP node comes from the address it calls and the name of a mailbox node comes from its credential type, so read the listed name instead of guessing it.',
                   'Record system names, credential names and owners only. Never record a secret.',
                   paths=['automation/systems.md']),
            _phase('design', 'Solution design', 'Design the workflows, their triggers, the data flow and the error handling.',
                   'The solution design describes each workflow, its trigger, its data flow and its error handling.',
                   'Draft the solution design and propose each workflow as a component. When the workflow is exported, set the component path to n8n: followed by the file path, for example n8n:workflows/lead-intake.json.',
                   'Design the workflows from the recorded process and systems. Proposed components stay proposed until the user confirms them.',
                   paths=['automation/solution-design.md']),
            _phase('stories', 'Workflow stories and test data', 'Write a story for each workflow with acceptance criteria and sample data.',
                   'Each workflow story has acceptance criteria that name the sample data and the expected result.',
                   'Record each workflow story as a workflow work item with acceptance criteria.',
                   'Write workflow stories from the approved solution design. Do not add workflows that the user has not approved.',
                   paths=['automation/solution-design.md', 'automation/test-data']),
            _phase('build', 'Build workflows', 'Build the workflows in n8n and export them to the workflows folder.',
                   'Each workflow in scope is exported to the workflows folder and matches its story.',
                   'Select a ready workflow story and record its plan with the workflow files it may change.',
                   'Build and export workflows one story at a time. Edit only the workflow files named in the plan. A change made directly in the n8n instance through its MCP server needs the pattern mcp:<server> in the plan paths.',
                   paths=['workflows']),
            _phase('test', 'Test with sample data', 'Test each workflow with the recorded sample data.',
                   'Each workflow has a recorded test result with the sample data and the observed output.',
                   'Run each workflow with its sample data and record the outcome with evidence.',
                   'Test with sample data only. Do not run workflows against live client data without the approval of the user.',
                   paths=['workflows', 'automation/test-data']),
            _phase('deployment', 'Deployment and handover', 'Deploy the workflows and hand them over to the client.',
                   'The workflows run in the agreed environment, and the client has the handover notes.',
                   'Prepare the deployment steps and the handover notes for the user.',
                   'Prepare deployment and handover. Only the user approves deployment to the client environment.',
                   owner='human', paths=['workflows/README.md', 'automation/solution-design.md']),
        ],
        'questions': [
            {'id': 'process', 'phase': 'process', 'text': 'Which process should be automated, and how does it run today?'},
            {'id': 'trigger', 'phase': 'process', 'text': 'What starts the process, and how often does it run?'},
            {'id': 'systems', 'phase': 'systems', 'text': 'Which systems and accounts does the process use?'},
            {'id': 'credentials', 'phase': 'systems', 'text': 'Who owns the credentials, and how will access be granted?'},
            {'id': 'test_data', 'phase': 'stories', 'text': 'Which sample data can be used for testing?'},
            {'id': 'errors', 'phase': 'design', 'text': 'What should happen when a step fails?'},
            {'id': 'operations', 'phase': 'deployment', 'text': 'Where will the workflows run, and who maintains them after handover?'},
        ],
    },
}


def _hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def definition(template):
    """Return a template definition, or raise InvalidRecord for an unknown template."""
    if template not in TEMPLATES:
        raise InvalidRecord('template must be one of ' + ', '.join(sorted(TEMPLATES)) + '.')
    return TEMPLATES[template]


def request_key(template, phase_key):
    return 'template:' + template + ':' + phase_key


def setting(memory):
    """The recorded project template setting, or None."""
    row = memory.db.execute('SELECT value FROM settings WHERE key=?', (SETTING,)).fetchone()
    if not row:
        return None
    try:
        value = json.loads(row[0])
    except ValueError:
        return None
    if not isinstance(value, dict) or value.get('template') not in TEMPLATES:
        return None
    return value


def validate_answers(value):
    """Check the `kickoff_answers` list of a note payload."""
    if not isinstance(value, list) or not 1 <= len(value) <= 30:
        raise InvalidRecord('kickoff_answers must be a list of 1 to 30 kickoff question ids.')
    for item in value:
        if not isinstance(item, str) or not QUESTION_ID.fullmatch(item):
            raise InvalidRecord('Each kickoff answer must be a question id of lowercase letters, digits and underscores.')
    if len(set(value)) != len(value):
        raise InvalidRecord('Each kickoff question id appears at most once in kickoff_answers.')
    return value


def _git(root):
    try:
        inside = subprocess.run(['git', '-C', str(root), 'rev-parse', '--is-inside-work-tree'],
                                capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        return {'requested': True, 'initialized': False, 'reason': 'Git is not installed, so no repository was created.'}
    if inside.returncode == 0 and inside.stdout.strip() == 'true':
        return {'requested': True, 'initialized': False, 'reason': 'The folder is already inside a Git repository.'}
    run = subprocess.run(['git', 'init', '-q', str(root)], capture_output=True, text=True, timeout=30)
    if run.returncode != 0:
        raise InvalidRecord('Git could not create a repository: ' + (run.stderr.strip() or 'no message') + '.')
    return {'requested': True, 'initialized': True}


COMMIT_IDENTITY = ['-c', 'user.name=Project Memory', '-c', 'user.email=project-memory@localhost', '-c', 'commit.gpgsign=false']


def _first_commit(root, template, paths):
    """Commit only the starter documents in a repository that scaffold created.

    Client configuration such as .mcp.json and .codex stays uncommitted, so the
    user decides whether it belongs in the repository. Delegated work needs this
    first commit as its base.
    """
    def git(*args):
        return subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True, timeout=60)
    if git('rev-parse', '--verify', '--quiet', 'HEAD').returncode == 0:
        return {'committed': False, 'reason': 'The repository already has a commit.'}
    if not paths:
        return {'committed': False, 'reason': 'No starter document was written, so nothing was committed.'}
    added = git('add', '--', *paths)
    identity = [] if git('config', 'user.email').stdout.strip() else COMMIT_IDENTITY
    done = added.returncode == 0 and git(*identity, 'commit', '-q', '--no-verify', '-m',
                                         f'Add the starter documents of the {template} template', '--', *paths).returncode == 0
    if not done:
        return {'committed': False, 'reason': 'Git could not commit the starter documents. Commit them before delegating work.'}
    return {'committed': True, 'paths': list(paths)}


def _phase_episode(memory, template, phase_key):
    row = memory.db.execute('SELECT episode_id FROM events WHERE request_key=?', (request_key(template, phase_key),)).fetchone()
    return row[0] if row else None


def _template_source(memory, template, spec):
    key = 'template:' + template
    row = memory.db.execute('SELECT id FROM sources WHERE source_key=? ORDER BY version DESC LIMIT 1', (key,)).fetchone()
    if row:
        # A later package version can change the template text. The first capture stays the evidence of existing phases.
        return row[0]
    body = dumps({'template': template, 'description': spec['description'], 'documents': sorted(spec['documents']),
                  'phases': spec['phases'], 'questions': spec['questions']})
    return memory.source(key, spec['title'] + ' template', 'This source records the phases and starter documents that the '
                         + template + ' template defines. It describes a planning pattern and states no project facts.',
                         body, 'tool')['id']


def _phase_payload(phase, previous):
    payload = {'state': 'backlog', 'next_action': phase['next_action'], 'scope': phase['scope'], 'autonomy': 'suggest',
               'owner': phase['owner'], 'reason': 'The project template defines this phase.', 'item_type': 'phase'}
    if previous:
        payload['depends_on'] = [{'episode_id': previous, 'reason': 'The previous phase provides the input for this phase.'}]
    if phase['paths']:
        payload['paths'] = list(phase['paths'])
    return payload


def scaffold(project, template, *, name=None, clients=(), requirements=None, git=True, _launcher=None):
    """Create or complete a project from a template. Repeating it creates nothing new.

    Existing documents are never overwritten. Phase work items use the request keys
    `template:<template>:<phase key>`, so a repeated run finds them instead of
    creating them again.
    """
    from . import Memory
    from . import install, planning
    spec = definition(template)
    if name is not None:
        _text(name, 'name', 200)
    if isinstance(clients, str):
        clients = [clients]
    selected = list(dict.fromkeys(clients)) or ['mcp']
    for client in selected:
        if client not in install.CLIENTS:
            raise InvalidRecord('Each client must be mcp, codex or claude.')
    if requirements is not None:
        if not isinstance(requirements, list) or not requirements:
            raise InvalidRecord('requirements must be a list of at least one agreed requirement.')
        for item in requirements:
            _text(item, 'requirement', 2000)
    root = Path(project).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    state = install.project_state(root)
    database = Path(state['database']) if state else root / '.memory' / 'project.sqlite'
    if database.exists():
        with Memory(database, read_only=True) as memory:
            existing = setting(memory)
            if existing and existing['template'] != template:
                raise Conflict('This project already uses the ' + existing['template'] + ' template. Keep that template or create a new project.')
            if requirements is not None and memory.requirements != requirements:
                raise Conflict('Existing requirements differ. Approve an explicit direction revision instead of changing the template requirements.')
    git_result = _git(root) if git else {'requested': False, 'initialized': False}
    documents = []
    for relative, text in spec['documents'].items():
        path = root / relative
        written = not path.exists()
        if written:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('x', encoding='utf-8', newline='\n') as stream:
                stream.write(text)
        documents.append({'path': relative, 'written': written})
    if git_result.get('initialized'):
        git_result.update(_first_commit(root, template, [entry['path'] for entry in documents if entry['written']]))
    if not database.exists():
        database.parent.mkdir(parents=True, exist_ok=True)
        with Memory.create(database, name or root.name, requirements or [PLACEHOLDER]):
            pass
    setups = [install.setup(root, client=client, database=str(database), _launcher=_launcher) for client in selected]
    phases = []
    with Memory(database) as memory:
        for entry in documents:
            try:
                captured = memory.document(str(root / entry['path']))
                entry.update(source_id=captured['id'], captured=captured['created'])
            except (InvalidRecord, OSError, UnicodeError) as exc:
                entry.update(source_id=None, captured=False, issue=str(exc))
        with memory._write():
            source_id = _template_source(memory, template, spec)
        evidence = [{'source_id': source_id, 'reason': 'The project template defines this phase and its starter documents.'}]
        previous = None
        for phase in spec['phases']:
            key = request_key(template, phase['key'])
            episode_id = _phase_episode(memory, template, phase['key'])
            created = episode_id is None
            if created:
                with memory._write():
                    result = planning.save(memory, 'work_plan', payload=_phase_payload(phase, previous), actor=ACTOR,
                                           evidence=evidence, title=phase['title'], objective=phase['objective'],
                                           criterion=phase['criterion'], subject=phase['subject'], request_key=key)
                episode_id = result['episode_id']
            phases.append({'key': phase['key'], 'title': phase['title'], 'episode_id': episode_id, 'created': created})
            previous = episode_id
        value = {'template': template, 'version': 1, 'created_at': memory.now(),
                 'documents': {relative: _hash(text) for relative, text in spec['documents'].items()}}
        with memory._write():
            memory.db.execute('INSERT OR IGNORE INTO settings VALUES (?,?)', (SETTING, dumps(value)))
    return {'project': str(root), 'template': template, 'database': str(database), 'git': git_result,
            'clients': setups[-1]['clients'], 'documents': documents, 'phases': phases,
            'created': {'documents': sum(entry['written'] for entry in documents),
                        'captures': sum(bool(entry.get('captured')) for entry in documents),
                        'phases': sum(phase['created'] for phase in phases)},
            'next_step': 'Open the project in a connected client and use memory_get kickoff to answer the kickoff questions before planning work. '
                         'Delegated work starts from the latest git commit, so commit changed documents before delegating work that depends on them.'}


def _baseline(memory):
    from .direction import current
    revision = current(memory)
    if revision['requirements'] == [PLACEHOLDER]:
        return {'status': 'not_established', 'version': revision['version']}
    return {'status': revision.get('status', 'current'), 'version': revision['version'], 'requirements': len(revision['requirements'])}


def kickoff_hint(memory):
    """One sentence for host context while a templated project has no requirements baseline, otherwise an empty string."""
    value = setting(memory)
    if not value or _baseline(memory)['status'] != 'not_established':
        return ''
    return ('This project uses the ' + value['template'] + ' template and its requirements baseline is not established, '
            'so use memory_get kickoff before planning work.')


def answers(memory):
    """Map each answered kickoff question id to the note that records the answer."""
    result = {}
    rows = memory.db.execute("""SELECT id, payload FROM events WHERE kind='note'
        AND json_extract(payload,'$.kickoff_answers') IS NOT NULL ORDER BY rowid""")
    for row in rows:
        for question_id in json.loads(row['payload']).get('kickoff_answers', []):
            result.setdefault(question_id, row['id'])
    return result


def _question_phase(spec, question_ids):
    """The earliest template phase that the answered questions belong to."""
    order = {phase['key']: index for index, phase in enumerate(spec['phases'])}
    selected = set(question_ids)
    keys = [question['phase'] for question in spec['questions']
            if question['id'] in selected and question.get('phase') in order]
    return min(keys, key=lambda key: order[key]) if keys else spec['phases'][0]['key']


def answer_kickoff(memory, *, question_ids, text, actor, request_key, evidence=None, episode_id=None):
    """Record a note that answers kickoff questions.

    The note belongs to the earliest phase that the answered questions cover, so
    an answer about credentials is recorded on the phase that inventories them,
    unless the caller names another work item.
    """
    value = setting(memory)
    if not value:
        raise InvalidRecord('This project has no template, so it has no kickoff questions.')
    spec = TEMPLATES[value['template']]
    validate_answers(question_ids)
    known = {question['id'] for question in spec['questions']}
    unknown = [question_id for question_id in question_ids if question_id not in known]
    if unknown:
        raise InvalidRecord('These kickoff question ids are not part of the template: ' + ', '.join(unknown) + '.',
                            known=sorted(known))
    if episode_id is None:
        episode_id = _phase_episode(memory, value['template'], _question_phase(spec, question_ids))
        if episode_id is None:
            episode_id = _phase_episode(memory, value['template'], spec['phases'][0]['key'])
        if episode_id is None:
            raise InvalidRecord('The first template phase was not found. Run the template setup again.')
    return memory.record(episode_id, 'note', {'text': text, 'kickoff_answers': list(question_ids)},
                         expected_version=memory.episode(episode_id)['version'], request_key=request_key,
                         actor=actor, evidence=evidence)


def _phase_documents(spec, phase_key):
    """The starter documents that one phase is responsible for filling in."""
    phase = next((item for item in spec['phases'] if item['key'] == phase_key), None)
    if phase is None:
        return []
    return [relative for relative in spec['documents']
            if any(relative == pattern or relative.startswith(pattern.rstrip('/') + '/') for pattern in phase['paths'])]


def _document_status(root, relative, expected):
    path = root / relative
    try:
        if not path.exists():
            return 'missing'
        data = path.read_bytes()
    except OSError:
        return 'unreadable'
    if expected and hashlib.sha256(data).hexdigest() == expected:
        return 'unchanged'
    return 'changed'


def kickoff(memory):
    """Report the kickoff state of a templated project and the next step, in plain sentences."""
    from .architecture import project_root
    from .planning import card
    baseline = _baseline(memory)
    value = setting(memory)
    if not value:
        return {'template': None, 'baseline': baseline, 'phases': [], 'research': [], 'documents': [], 'questions': [],
                'next_step': {'action': 'plan', 'reason': 'This project was not created from a template. Plan work items directly, or create a new project with project-memory init and a template.'}}
    template = value['template']
    spec = TEMPLATES[template]
    phases = []
    for phase in spec['phases']:
        episode_id = _phase_episode(memory, template, phase['key'])
        entry = {'key': phase['key'], 'title': phase['title'], 'episode_id': episode_id, 'owner': phase['owner']}
        if episode_id:
            item = card(memory, episode_id)
            entry.update(state=item['state'], next_action=(item['plan'] or {}).get('next_action'))
        else:
            entry.update(state='missing', next_action=None)
        phases.append(entry)
    rows = memory.db.execute("""SELECT e.episode_id, ep.title, json_extract(e.payload,'$.state') AS state FROM events e
        JOIN episodes ep ON ep.id=e.episode_id WHERE e.kind='work_plan'
          AND e.seq=(SELECT max(n.seq) FROM events n WHERE n.episode_id=e.episode_id AND n.kind='work_plan')
          AND json_extract(e.payload,'$.item_type')='research'
        ORDER BY ep.created_at, ep.id LIMIT 200""").fetchall()
    research = [{'episode_id': row['episode_id'], 'title': row['title'], 'state': row['state']}
                for row in rows if row['state'] not in DONE_STATES]
    root = project_root(memory)
    hashes = value.get('documents') if isinstance(value.get('documents'), dict) else {}
    documents = []
    for relative, text in spec['documents'].items():
        documents.append({'path': relative, 'status': _document_status(root, relative, hashes.get(relative) or _hash(text))})
    answered = answers(memory)
    questions = [{'id': question['id'], 'text': question['text'], 'phase': question.get('phase'),
                  'answered': question['id'] in answered, 'answered_by': answered.get(question['id'])}
                 for question in spec['questions']]
    open_questions = [question['id'] for question in questions if not question['answered']]
    unfilled = [entry['path'] for entry in documents if entry['status'] in ('missing', 'unchanged')]
    pending = [phase for phase in phases if phase['state'] not in DONE_STATES]
    current_phase = pending[0] if pending else None
    # Only the documents of the phase that is running can be filled in now. A later phase records findings and a
    # deliverable list from work that has not happened yet, so demanding every document would stop kickoff here.
    due = [path for path in unfilled
           if path in _phase_documents(spec, current_phase['key'] if current_phase else None)]
    if open_questions:
        step = {'action': 'answer_kickoff', 'question_ids': open_questions,
                'reason': 'Ask the user the open kickoff questions. Record the answers in a note whose kickoff_answers lists the question ids. '
                          'Each question names the phase it belongs to, so record answers about different phases in separate notes.'}
    elif due:
        step = {'action': 'fill_documents', 'paths': due, 'phase': current_phase['key'], 'episode_id': current_phase['episode_id'],
                'reason': 'Fill in the starter documents of the phase ' + current_phase['title'] + ' with the answers and the '
                          'evidence that the user provides, and capture each document after it changes.'}
    elif baseline['status'] == 'not_established':
        step = {'action': 'approve_requirements',
                'reason': 'Propose requirements from the recorded answers and documents, and ask the user to approve the requirements baseline.'}
    elif research:
        step = {'action': 'research', 'episode_ids': [item['episode_id'] for item in research],
                'reason': 'Complete or explicitly defer the open research items before a decision depends on their answers.'}
    elif current_phase:
        step = {'action': 'continue_phase', 'episode_id': current_phase['episode_id'], 'phase': current_phase['key'],
                'reason': 'Continue with the phase ' + current_phase['title'] + '. ' + PHASE_MOVE}
    else:
        step = {'action': 'kickoff_complete', 'reason': 'Every kickoff step and every template phase is recorded as finished.'}
    return {'template': template, 'title': spec['title'], 'description': spec['description'], 'layer': spec['layer'],
            'subjects': spec['subjects'], 'baseline': baseline, 'phases': phases, 'research': research,
            'documents': documents, 'questions': questions, 'next_step': step,
            'current_phase': current_phase['key'] if current_phase else None,
            'note': 'Kickoff status comes from recorded notes, documents and plans. It does not approve requirements '
                    'or grant permission. ' + PHASE_MOVE}
