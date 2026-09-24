"""Data contracts of the read API, the live server over it and the offline export."""
import os
import shutil
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import queue
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import uuid

from memory_module import Memory, InvalidRecord, api, architecture, codex_host, hive, hosts, live, machine, planning, reviews, workspace
from memory_module.install import setup
from memory_module.live import Viewer
from memory_module.workspace import action

LAUNCHER = [sys.executable, '-m', 'memory_module.cli']
MACHINE = {}
RULE = {'when': 'a release is prepared', 'do': 'Run the whole test suite before the release is tagged.',
        'because': 'A release that skips the suite hides a failure until users meet it.',
        'exceptions': 'A documentation change that touches no code.'}


def setUpModule():
    """No test reads the machine memory of this computer. Every machine path stays in a temporary folder."""
    MACHINE['folder'] = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    MACHINE['previous'] = os.environ.get(machine.DATABASE_VARIABLE)
    MACHINE['default'] = str(Path(MACHINE['folder'].name) / 'machine.sqlite')
    os.environ[machine.DATABASE_VARIABLE] = MACHINE['default']


def tearDownModule():
    if MACHINE['previous'] is None:
        os.environ.pop(machine.DATABASE_VARIABLE, None)
    else:
        os.environ[machine.DATABASE_VARIABLE] = MACHINE['previous']
    MACHINE['folder'].cleanup()


def fake_run(memory, episode_id, *, role='work', state='completed', parent=None, changed=('src/app.py',), project=None, request_key=None):
    """Insert a finished agent run directly. No host process runs."""
    reviews.ensure_run_columns(memory)
    run_id = 'check_' + uuid.uuid4().hex
    snapshot = {'role': role, 'project': str(project or memory.path.parent.parent), 'paths': ['src/**']}
    report = {'summary': 'The fixture run finished.', 'result': 'complete'} if role == 'work' else {'verdict': state, 'summary': 'The fixture review finished.'}
    with memory._write():
        memory.db.execute('''INSERT INTO review_runs (id,episode_id,role,signature,host,session_id,state,created_at,updated_at,
            request_key,snapshot,report,metrics,error,parent_run,workspace,branch) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                          (run_id, episode_id, role, 'fixture', 'codex', '', state, memory.now(), memory.now(), request_key or 'fixture:' + run_id,
                           json.dumps(snapshot), json.dumps(report), json.dumps({'changed_files': list(changed), 'commit': 'abc123'}),
                           '', parent, '.memory/worktrees/' + run_id, 'pm/' + run_id[6:14]))
    return run_id


def build(root):
    """A project with work items, a blocked chain, decisions with failure and recovery, an accepted guard with a
    recurrence, a proposed lesson, a failure without a lesson, a scope block, a delegated run awaiting merge and a
    small source tree."""
    info = setup(root, requirements=['Keep the tagged exception.'])
    m = Memory(info['database'])
    (root / 'src').mkdir()
    (root / 'src/__init__.py').write_text('')
    (root / 'src/app.py').write_text('import json\nfrom src import util\n')
    (root / 'src/util.py').write_text('VALUE = 1\n')
    source = m.source('user:scope', 'User request', 'The user asks for the parser work.', 'Change the parser within src.', 'user', subject='code')
    evidence = [{'source_id': source['id'], 'reason': 'The user defines the work.'}]
    ids = {'source': source['id']}

    def work(key, title, state='ready', **extra):
        payload = {'state': state, 'next_action': 'Continue the work.', 'scope': 'Change the parser only.',
                   'autonomy': 'act', 'reason': 'The user requests this work.', **extra}
        result = planning.save(m, 'work_plan', payload=payload, actor='workspace-user', evidence=evidence, title=title,
                               objective='Deliver ' + title.lower() + '.', criterion='The fixture passes.', subject='code',
                               request_key='plan:' + key)
        ids[key] = result['episode_id']
        return result['episode_id']

    work('parser', 'Prepare the parser', paths=['src/**'])
    work('design', 'Design the import')
    work('build', 'Build the import', depends_on=[{'episode_id': ids['design'], 'reason': 'The design comes first.'}])
    work('publish', 'Publish the import', depends_on=[{'episode_id': ids['build'], 'reason': 'The build comes first.'}])
    work('wording', 'Review the wording', state='review')

    def record(episode, kind, payload, **kwargs):
        return m.record(episode, kind, payload, expected_version=m.episode(episode)['version'], actor='fixture',
                        request_key=kind + ':' + episode + ':' + str(m.episode(episode)['version']), evidence=evidence, **kwargs)

    decision = {'decision': 'Decode every input as strict UTF-8.', 'why': 'Strict decoding exposes invalid input.',
                'expected': 'Every fixture imports.', 'uncertainty': 'The tagged path is unchecked.',
                'alternatives': ['Keep a tagged path.'], 'reconsider_when': 'A supported input fails.'}
    old = record(ids['parser'], 'decision', decision)
    record(ids['parser'], 'action', {'action': 'Run the fixtures.'}, decision_id=old['id'])
    record(ids['parser'], 'outcome', {'observed': 'The tagged fixture failed.', 'assessment': 'bad', 'assessment_reason': 'The exception was omitted.',
                                      'severity': 'major', 'attribution': 'Every file used UTF-8.', 'completion': 'partial',
                                      'failure_type': 'lost_text'}, decision_id=old['id'])
    revised = record(ids['parser'], 'decision', {**decision, 'decision': 'Keep strict UTF-8 and the tagged path.'}, supersedes=old['id'])
    record(ids['parser'], 'action', {'action': 'Repair the decoder.'}, decision_id=revised['id'])
    good = record(ids['parser'], 'outcome', {'observed': 'Both fixtures passed.', 'assessment': 'good', 'assessment_reason': 'Both paths pass.',
                                             'severity': 'none', 'attribution': 'The repair restores the branch.', 'completion': 'complete'},
                  decision_id=revised['id'])
    ids.update(old=old['id'], revised=revised['id'], good=good['id'])
    lessons = m.start('Parser lessons', 'Collect parser lessons.', 'learning', 'Lessons are reviewed.', subject='code')['id']
    ids['lessons'] = lessons
    accepted = record(lessons, 'lesson', {'when': 'Text is decoded.', 'do': 'Check the tagged path.', 'because': 'The tagged fixture failed.',
                                          'exceptions': 'Undocumented encodings.', 'pattern_type': 'recovery'})
    action(m, 'lesson_review', {'lesson_id': accepted['id'], 'expected_version': m.episode(lessons)['version'], 'status': 'accepted',
                                'reason': 'The user accepts the lesson.', 'paths': ['src/**'], 'failure_type': 'lost_text'}, 'accept-lesson')
    proposed = record(lessons, 'lesson', {'when': 'A release is prepared.', 'do': 'Read the notes aloud.', 'because': 'Wording errors reached users.',
                                          'exceptions': 'Internal releases.', 'pattern_type': 'practice'})
    ids.update(guard=accepted['id'], proposed=proposed['id'])
    imports = m.start('Import files', 'Import the client files.', 'action', 'Files import without loss.', subject='code')['id']
    ids['imports'] = imports
    failed_decision = record(imports, 'decision', {**decision, 'decision': 'Import the files in one pass.'})
    record(imports, 'action', {'action': 'Import the client files.'}, decision_id=failed_decision['id'])
    ids['failure'] = record(imports, 'outcome', {'observed': 'Text was lost again.', 'assessment': 'bad', 'assessment_reason': 'Text is missing.',
                                                 'severity': 'major', 'attribution': 'The encoding was wrong.', 'completion': 'partial',
                                                 'failure_type': 'lost_text'}, decision_id=failed_decision['id'])['id']
    # An agent widens the paths of a work item, which the learning view lists.
    design = m.read(planning.latest(m, ids['design'], 'work_plan')['id'])
    planning.save(m, 'work_plan', payload={**design['payload'], 'paths': ['docs/**'], 'reason': 'The design needs the documents.'},
                  actor='assistant', evidence=evidence, episode_id=ids['design'], expected_version=m.episode(ids['design'])['version'],
                  request_key='agent-widen')
    plan_id = planning.latest(m, ids['parser'], 'work_plan')['id']
    with m._write():
        codex_host.receipt(m, session_id='session-1', event_name='ScopeBlocked', episode_id=ids['parser'], key='scope-block',
                           payload={'episode_id': ids['parser'], 'plan_id': plan_id, 'tool_name': 'Edit', 'blocked': ['docs/readme.md'],
                                    'allowed_patterns': ['src/**'], 'host': 'codex'})
    reviews.configure(m, root, 'codex')
    ids['run'] = fake_run(m, ids['parser'], project=root)
    with m._write():
        codex_host.receipt(m, session_id='fixture', event_name='DelegationFollowUpNotStarted', episode_id=ids['parser'], key='follow-up',
                           payload={'run_id': ids['run'], 'state': 'completed', 'error': 'Two agent runs were active.'})
        codex_host.receipt(m, session_id='fixture', event_name='LessonProposalsNotRecorded', episode_id=ids['parser'], key='lessons-missing',
                           payload={'run_id': 'check_missing', 'error': 'The episode changed.'})
        codex_host.receipt(m, session_id='host:codex', event_name='HostAvailabilityNotRecorded', key='availability-missing',
                           payload={'host': 'codex', 'error': 'The database was busy.'})
    hosts.mark_available(m, 'codex')
    return m, ids


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name).resolve()
        # The machine memory of this test lives beside the project, so no test reads or writes the one of this computer.
        os.environ[machine.DATABASE_VARIABLE] = str(self.root / 'machine' / 'machine.sqlite')
        self.m, self.ids = build(self.root)

    def tearDown(self):
        self.m.close()
        os.environ[machine.DATABASE_VARIABLE] = MACHINE['default']
        shutil.rmtree(self.temp.name, ignore_errors=True)

    def test_now_reports_work_attention_agents_decisions_and_learning_counts(self):
        value = api.now(self.m, {})
        self.assertEqual(value['counts']['blocked'], 2)
        self.assertEqual(value['counts']['review'], 1)
        self.assertEqual({item['id'] for item in value['blocked']}, {self.ids['build'], self.ids['publish']})
        self.assertEqual(set(value['blocked'][0]), {'id', 'title', 'subject', 'state', 'priority', 'next_action', 'issues', 'owner', 'item_type'})
        kinds = [item['type'] for item in value['attention']]
        for expected in ('scope_block', 'blocked_work', 'awaiting_merge', 'agent_follow_up', 'guard_recurrence',
                         'failure_without_lesson', 'work_to_review', 'lessons_to_accept'):
            self.assertIn(expected, kinds)
        self.assertLessEqual(len(value['attention']), 20)
        # Now leads with one entry per kind, and the list narrows to one kind.
        by_kind = {item['type']: item for item in value['attention_kinds']}
        self.assertEqual((by_kind['blocked_work']['count'], by_kind['blocked_work']['rows'], 'entry' in by_kind['blocked_work']), (2, 2, False))
        self.assertEqual(by_kind['lessons_to_accept']['entry']['type'], 'lessons_to_accept')
        self.assertEqual(value['attention_count'], sum(item['count'] for item in value['attention_kinds']))
        # Each kind carries its first rows for its tab, and the row of a work item names the item and why it waits.
        self.assertEqual([len(by_kind['blocked_work']['entries']), len(by_kind['lessons_to_accept']['entries'])], [2, 1])
        row = by_kind['blocked_work']['entries'][0]
        self.assertTrue(row['title'] and row['detail'] and row['reason'].startswith(row['title']))
        blocked = api.now(self.m, {'attention_type': 'blocked_work'})
        self.assertEqual(([item['type'] for item in blocked['attention']], blocked['attention_total']), (['blocked_work'] * 2, 2))
        self.assertEqual(value['lessons_to_accept'], 1)
        self.assertEqual(value['failures_without_lesson'], 1)
        self.assertEqual(value['recurrences'], 1)
        self.assertEqual(value['scope_blocks'][0]['still_outside'], ['docs/readme.md'])
        self.assertEqual(value['scope_changes'][0]['added'], ['docs/**'])
        self.assertEqual(value['agents']['recent'][0]['id'], self.ids['run'])
        decisions = {item['id']: item for item in value['latest_decisions']}
        self.assertEqual(decisions[self.ids['revised']]['outcome']['assessment'], 'good')
        self.assertNotIn(self.ids['old'], decisions)

    def test_follow_up_receipts_are_attention_until_a_later_record_resolves_them(self):
        items = api.agents(self.m, {})['attention']
        self.assertEqual(sorted(item['receipt'] for item in items), ['DelegationFollowUpNotStarted', 'LessonProposalsNotRecorded'])
        fake_run(self.m, self.ids['parser'], role='work_review', state='pass', parent=self.ids['run'], project=self.root)
        self.assertEqual([item['receipt'] for item in api.agents(self.m, {})['attention']], ['LessonProposalsNotRecorded'])

    def test_work_combines_card_next_step_lineage_runs_checks_and_history_pages(self):
        value = api.work(self.m, {'id': self.ids['parser'], 'limit': '2', 'offset': '1'})
        self.assertEqual(value['card']['id'], self.ids['parser'])
        self.assertIn('action', value['next'])
        self.assertEqual(value['lineage']['focus'], self.ids['parser'])
        self.assertEqual([run['id'] for run in value['runs']['runs']], [self.ids['run']])
        self.assertEqual(value['runs']['runs'][0]['merge'], None)
        total = self.m.db.execute('SELECT count(*) FROM events WHERE episode_id=?', (self.ids['parser'],)).fetchone()[0]
        self.assertEqual((value['history']['total'], value['history']['offset'], len(value['history']['records'])), (total, 1, 2))
        self.assertTrue(value['history']['more'])
        with self.assertRaises(InvalidRecord):
            api.work(self.m, {})
        with self.assertRaises(InvalidRecord):
            api.work(self.m, {'id': self.ids['parser'], 'limit': 'many'})

    def test_records_record_and_lineage_keep_bounded_pages(self):
        page = api.page(self.m, {'view': 'decisions', 'limit': '1'})
        self.assertEqual((page['total'], len(page['records']), page['more']), (3, 1, True))
        decision = api.record(self.m, {'id': self.ids['old']})['record']
        self.assertEqual(decision['outcome']['detail']['payload']['assessment'], 'bad')
        source = api.record(self.m, {'id': self.ids['source']})['record']['detail']
        self.assertEqual(source['body'], 'Change the parser within src.')
        graph = api.lineage(self.m, {'id': self.ids['old'], 'depth': '1'})
        self.assertIn(self.ids['revised'], {node['id'] for node in graph['nodes']})
        for bad in ({'view': 'unknown'}, {'view': 'events', 'limit': '0'}, {'view': 'events', 'order': 'random'}):
            with self.assertRaises(InvalidRecord):
                api.page(self.m, bad)

    def test_learning_lists_guards_with_recurrences_proposed_lessons_and_failures(self):
        value = api.learning(self.m, {})
        [guard] = value['guards']
        self.assertEqual((guard['lesson_id'], guard['recurrences'], guard['paths']), (self.ids['guard'], 1, ['src/**']))
        self.assertEqual(value['recurrences'][0]['outcomes'][0]['id'], self.ids['failure'])
        [lesson] = value['proposed_lessons']['lessons']
        self.assertEqual(lesson['id'], self.ids['proposed'])
        self.assertEqual(lesson['episode_version'], self.m.episode(self.ids['lessons'])['version'])
        self.assertEqual(lesson['evidence'][0]['origin'], 'user')
        self.assertEqual([item['outcome_id'] for item in value['failures_without_lesson']], [self.ids['failure']])
        self.assertIn('signals', value)
        self.assertEqual((value['guards_total'], value['guards_more']), (1, False))
        with patch('memory_module.guards.active_guards', return_value=[{**guard, 'lesson_id': 'event_' + str(i)} for i in range(api.GUARD_LIMIT + 5)]):
            bounded = api.learning(self.m, {})
        self.assertEqual((len(bounded['guards']), bounded['guards_total'], bounded['guards_more']), (api.GUARD_LIMIT, api.GUARD_LIMIT + 5, True))

    def rule(self, key, roles, **triggers):
        """Record a lesson that names roles and let the user accept it with its triggers."""
        lessons = self.ids['lessons']
        payload = {'when': 'A delegated change is prepared.', 'do': 'Run the tagged fixture first.',
                   'because': 'The tagged fixture failed before.', 'exceptions': 'Documentation changes.',
                   'pattern_type': 'practice'}
        lesson = self.m.record(lessons, 'lesson', {**payload, 'do': 'Run the ' + key + ' fixture first.'},
                               expected_version=self.m.episode(lessons)['version'], actor='assistant', request_key='lesson:' + key,
                               evidence=[{'source_id': self.ids['source'], 'reason': 'The user defines the work this rule guards.'}])
        action(self.m, 'lesson_review', {'lesson_id': lesson['id'], 'expected_version': self.m.episode(lessons)['version'],
                                         'status': 'accepted', 'reason': 'The user accepts the rule.', 'roles': list(roles),
                                         **triggers}, 'accept:' + key)
        return lesson['id']

    def test_learning_reports_the_instructions_per_role_and_the_effectiveness_of_each_rule(self):
        from memory_module import guards
        before = api.learning(self.m, {})['instructions']
        self.assertEqual(sorted(before['roles']), sorted(guards.RULE_ROLES))
        self.assertEqual(before['counts'], {'assistant': 0, 'worker': 0, 'reviewer': 0})
        self.assertEqual(before['max_rules'], guards.MAX_ACTIVE_RULES)
        worker = before['roles']['worker']
        self.assertEqual((worker['base_source'], worker['rule_ids'], worker['used']), ('agents/worker.md', [], 0))
        self.assertEqual((worker['budget'], worker['text']), (guards.ROLE_BUDGETS['worker'], worker['base']))
        self.assertEqual(api.learning(self.m, {})['effectiveness'], [])
        always = self.rule('always', ['worker'])
        guarded = self.rule('guarded', ['worker'], failure_type='lost_text')
        value = api.learning(self.m, {})
        worker = value['instructions']['roles']['worker']
        # Every run of the role carries the rule without other triggers; the guarded rule joins the run that matches it.
        self.assertEqual((worker['rule_ids'], value['instructions']['counts']['worker']), ([always], 2))
        self.assertIn(guards.RULES_HEADING, worker['text'])
        self.assertIn('Run the always fixture first', worker['text'])
        self.assertEqual((worker['omitted'], worker['accepted_total']), ([], 2))
        self.assertEqual(value['instructions']['roles']['assistant']['rule_ids'], [])
        self.assertEqual(guards.instructions(self.m, 'worker', failure_types=['lost_text'])['rule_ids'], [guarded, always])
        entries = {entry['lesson_id']: entry for entry in value['effectiveness']}
        self.assertEqual(sorted(entries), sorted([always, guarded]))
        self.assertEqual((entries[always]['roles'], entries[always]['runs'], entries[always]['state']), (['worker'], 0, 'unproven'))
        self.assertEqual(entries[always]['verdicts'], {'pass': 0, 'changes_required': 0, 'uncertain': 0, 'pending': 0})
        # The fixture recorded this failure type before the acceptance and none after it.
        self.assertEqual((entries[guarded]['recurrences_before'], entries[guarded]['recurrences_after'],
                          entries[guarded]['state']), (2, 0, 'effective'))
        self.assertTrue(entries[guarded]['note'].endswith('.'))
        with Memory(self.m.path, read_only=True) as reader:
            self.assertEqual(api.learning(reader, {})['instructions']['counts'], {'assistant': 0, 'worker': 2, 'reviewer': 0})

    def run_with_rules(self, episode_id, rule_ids, verdict):
        """Insert a finished reviewer run that recorded the rules it composed."""
        reviews.ensure_run_columns(self.m)
        run_id = 'check_' + uuid.uuid4().hex
        report = json.dumps({'verdict': verdict, 'summary': 'The fixture review finished.'})
        metrics = json.dumps({'rule_ids': list(rule_ids), 'instruction_source': 'instructions:reviewer'})
        with self.m._write():
            self.m.db.execute('INSERT INTO review_runs (id,episode_id,role,signature,host,session_id,state,created_at,'
                              'updated_at,request_key,snapshot,report,metrics,error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                              (run_id, episode_id, 'work_review', 'fixture', 'codex', '', verdict, self.m.now(),
                               self.m.now(), run_id, '{}', report, metrics, ''))
        return run_id

    def test_the_now_view_reports_rules_over_the_cap_and_a_rule_that_does_not_help(self):
        from memory_module import guards
        for index in range(guards.MAX_ACTIVE_RULES + 1):
            self.rule('cap-%d' % index, ['worker'])
        now = api.now(self.m, {})
        over = [item for item in now['attention'] if item['type'] == 'rules_over_cap']
        self.assertEqual([item['role'] for item in over], ['worker'])
        self.assertIn('at most ' + str(guards.MAX_ACTIVE_RULES), over[0]['reason'])
        self.assertEqual(guards.rule_counts(self.m)['worker'], guards.MAX_ACTIVE_RULES + 1)
        # A rule judged ineffective by the verdicts of its runs alone also reaches the attention list.
        rule = self.rule('weak', ['reviewer'])
        episode = self.ids['parser']
        for _ in range(3):
            self.run_with_rules(episode, [rule], 'changes_required')
        entry = {item['lesson_id']: item for item in guards.effectiveness(self.m)}[rule]
        self.assertEqual((entry['state'], entry['failure_type']), ('ineffective', None))
        items = [item for item in api.now(self.m, {})['attention'] if item['type'] == 'rule_ineffective']
        self.assertEqual([item['id'] for item in items], [rule])
        self.assertIn('do not show that it helps', items[0]['reason'])

    def test_the_instructions_command_writes_one_file_per_role(self):
        from memory_module import cli, guards
        output = self.root / 'instructions'
        stream = io.StringIO()
        with redirect_stdout(stream):
            self.assertEqual(cli.main(['instructions', '--db', str(self.m.path), '--output', str(output)]), 0)
        result = json.loads(stream.getvalue())
        self.assertEqual([entry['role'] for entry in result['roles']], list(guards.RULE_ROLES))
        for entry in result['roles']:
            with self.subTest(role=entry['role']):
                written = (output / (entry['role'] + '.md')).read_text()
                self.assertEqual(written.strip(), guards.instructions(self.m, entry['role'])['text'].strip())
                self.assertEqual(entry['base_source'], 'agents/' + entry['role'] + '.md')
                self.assertEqual((entry['rule_ids'], entry['omitted'], entry['accepted_rules']), ([], [], 0))
        self.assertTrue(result['note'].endswith('.'))

    def test_run_endpoint_returns_the_report_metrics_reviews_and_merge_state(self):
        review = fake_run(self.m, self.ids['parser'], role='work_review', state='changes_required', parent=self.ids['run'], project=self.root)
        value = api.run(self.m, {'id': self.ids['run']})['run']
        self.assertEqual((value['report']['result'], value['metrics']['commit'], value['paths']), ('complete', 'abc123', ['src/**']))
        self.assertEqual((value['review']['id'], value['merge'], value['changed_files']), (review, None, 1))
        self.assertEqual([item['id'] for item in value['reviews']], [review])
        self.assertIn('diff_source', value)
        self.assertNotIn('snapshot', value)
        self.assertEqual(api.run(self.m, {'id': review})['run']['report']['verdict'], 'changes_required')
        with self.assertRaises(InvalidRecord):
            api.run(self.m, {})

    def test_scope_block_reason_names_several_paths_with_a_plural_verb(self):
        plan_id = planning.latest(self.m, self.ids['parser'], 'work_plan')['id']
        with self.m._write():
            codex_host.receipt(self.m, session_id='session-2', event_name='ScopeBlocked', episode_id=self.ids['parser'], key='scope-block-two',
                               payload={'episode_id': self.ids['parser'], 'plan_id': plan_id, 'tool_name': 'Edit', 'host': 'codex',
                                        'blocked': ['deliverables/report.md', 'deliverables/appendix.md'], 'allowed_patterns': ['src/**']})
        reasons = [item['reason'] for item in api.now(self.m, {})['attention'] if item['type'] == 'scope_block']
        self.assertIn('deliverables/report.md, deliverables/appendix.md are outside', reasons[0])
        self.assertIn('docs/readme.md is outside', reasons[1])

    def test_a_new_template_project_shows_no_blocked_phases(self):
        from memory_module import templates
        for name in ('engagement', 'automation', 'product'):
            with self.subTest(template=name):
                result = templates.scaffold(self.root / name, name, git=False, _launcher=LAUNCHER)
                with Memory(result['database'], read_only=True) as reader:
                    now = api.now(reader, {})
                    self.assertEqual(now['counts']['blocked'], 0)
                    self.assertEqual(now['counts']['backlog'], now['total'])
                    self.assertNotIn('blocked_work', [item['type'] for item in now['attention']])
                    self.assertEqual(api.plan(reader, {})['counts']['blocked'], 0)

    def test_graphs_plan_and_architecture_use_card_states_computed_once(self):
        self.m._api_cache = {}
        with patch('memory_module.planning.card', wraps=planning.card) as card:
            graph = api.work_graph(self.m, {})
            tree = api.plan(self.m, {})
            model = api.architecture(self.m, {})
            api.now(self.m, {})
            episodes = self.m.db.execute("SELECT count(*) FROM episodes WHERE task_type!='sprint'").fetchone()[0]
            self.assertEqual(card.call_count, episodes)
        states = {node['id']: node['state'] for node in graph['nodes']}
        self.assertEqual(states[self.ids['build']], 'blocked')
        self.assertEqual(len([edge for edge in graph['edges'] if edge['type'] == 'depends_on']), 2)
        self.assertEqual(tree['counts']['blocked'], 2)
        source = next(node for node in model['nodes'] if node['id'] == 'component:src')
        self.assertIn(self.ids['parser'], source['work'])
        self.assertEqual(source['status'], 'guarded')
        with patch.object(architecture, 'model', side_effect=AssertionError('The model should come from the request cache.')):
            self.assertIs(api.architecture(self.m, {}), model)

    def test_the_panel_records_the_phase_of_the_project_and_health_reports_it(self):
        self.assertEqual(api.health(self.m, {})['phase']['phase'], 'development')
        result = action(self.m, 'phase', {'phase': 'production', 'reason': 'The parser serves users now.'}, 'phase-1')
        self.assertEqual((result['phase'], result['version'], result['actor']), ('production', 1, 'workspace-user'))
        phase = api.health(self.m, {})['phase']
        self.assertEqual((phase['phase'], phase['reason']), ('production', 'The parser serves users now.'))
        self.assertIn('only the user brings delegated work into the project', phase['meaning'])
        action(self.m, 'phase', {'phase': 'development', 'reason': 'The next version is being built.'}, 'phase-2')
        self.assertEqual([item['phase'] for item in planning.phase_history(self.m)], ['development', 'production'])
        with self.assertRaises(InvalidRecord):
            action(self.m, 'phase', {'phase': 'development', 'reason': 'The phase does not change.'}, 'phase-3')

    def propose(self, key='promote-1'):
        return machine.propose(self.m, **RULE, basis='Two releases failed after the suite was skipped.',
                               roles=['worker'], actor='assistant', request_key=key)

    def test_machine_endpoint_reports_the_proposals_before_the_machine_memory_exists(self):
        value = api.machine(self.m, {})
        self.assertEqual((value['exists'], value['rules'], value['projects'], value['promotions']), (False, [], [], []))
        self.assertIn('No machine memory exists on this computer yet', value['note'])
        proposal = self.propose()
        value = api.machine(self.m, {})
        self.assertEqual([item['id'] for item in value['promotions']], [proposal['id']])
        self.assertEqual((value['proposed_total'], value['exists']), (1, False))
        self.assertEqual(value['promotions'][0]['rule']['do'], RULE['do'])
        self.assertEqual([item['count'] for item in api.now(self.m, {})['attention'] if item['type'] == 'machine_rules'], [1])
        self.assertFalse(Path(os.environ[machine.DATABASE_VARIABLE]).exists())

    def test_usage_endpoint_reads_the_ledger_read_only_and_the_revision_follows_it(self):
        from datetime import datetime, timedelta, timezone
        from memory_module import usage
        database = Path(os.environ[machine.DATABASE_VARIABLE])
        value = api.usage(self.m, {})
        self.assertEqual((value['ledger'], value['probes'], value['routing']), (False, {}, []))
        self.assertEqual([item['host'] for item in value['hosts']], ['codex', 'claude', 'grok', 'opencode'])
        self.assertEqual(value['note'], usage.NO_LEDGER_NOTE)
        self.assertEqual(value['configured_hosts'], ['codex'])
        self.assertFalse(database.exists())
        with Viewer(self.m.path, 'usage-revision-token') as server:
            before = server.revision()
            empty = self.root / 'no-logs'
            usage.collect_machine(folders={'codex': empty, 'claude': empty}, projects=False)
            collected = server.revision()
            self.assertNotEqual(collected, before)
            resets = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(timespec='microseconds')
            with Memory(database) as store, store._write():
                store.db.execute("INSERT INTO usage_limits VALUES ('codex','codex','primary',93,300,?,?)", (resets, resets))
            self.assertNotEqual(server.revision(), collected)
        value = api.usage(self.m, {})
        codex = value['hosts'][0]
        self.assertTrue(value['ledger'])
        self.assertEqual((codex['headroom']['constrained'], codex['headroom']['used_percent']), (True, 93))
        with self.assertRaises(InvalidRecord):
            api.usage(self.m, {'limit': '0'})

    def test_the_panel_accepts_a_promotion_retires_the_rule_and_the_endpoint_reports_both(self):
        proposal = self.propose()
        accepted = action(self.m, 'promotion', {'promotion_id': proposal['id'], 'status': 'accepted',
                                                'reason': 'The rule holds for every project on this computer.'}, 'accept-1')
        value = api.machine(self.m, {})
        self.assertTrue(value['exists'])
        [rule] = value['rules']
        self.assertEqual((rule['rule_id'], rule['adopted_by'], rule['status']), (accepted['machine_rule_id'], 1, 'accepted'))
        self.assertEqual(rule['basis'], 'Two releases failed after the suite was skipped.')
        self.assertEqual([item['path'] for item in value['projects']], [value['project_path']])
        self.assertEqual(value['projects'][0]['phase'], 'development')
        self.assertIn('no outcome is combined across projects', value['note'])
        action(self.m, 'machine_rule', {'rule_id': rule['rule_id'], 'status': 'retired',
                                        'reason': 'The suite now runs in the release command.'}, 'retire-1')
        value = api.machine(self.m, {})
        self.assertEqual((value['rules'], value['rules_total']), ([], 0))
        self.assertEqual([item['status'] for item in value['retired']], ['retired'])

    def test_the_panel_corrects_the_text_of_a_proposed_rule_before_it_accepts_it(self):
        proposal = self.propose()
        changed = {**RULE, 'do': 'Run the whole test suite and record its result before a release is tagged.',
                   'roles': ['worker', 'reviewer'], 'keywords': ['release']}
        action(self.m, 'promotion', {'promotion_id': proposal['id'], 'status': 'accepted', 'rule': changed,
                                     'basis': 'Two releases failed without a recorded test result.',
                                     'reason': 'The wording now asks for the recorded result.'}, 'accept-1')
        [rule] = api.machine(self.m, {})['rules']
        self.assertEqual(rule['do'], changed['do'])
        self.assertEqual((rule['roles'], rule['keywords']), (['worker', 'reviewer'], ['release']))
        self.assertEqual(rule['basis'], 'Two releases failed without a recorded test result.')

    def test_a_declined_promotion_writes_nothing_to_the_machine_memory(self):
        proposal = self.propose()
        action(self.m, 'promotion', {'promotion_id': proposal['id'], 'status': 'declined',
                                     'reason': 'The rule belongs to this project alone.'}, 'decline-1')
        value = api.machine(self.m, {})
        self.assertEqual([item['state'] for item in value['promotions']], ['declined'])
        self.assertEqual((value['exists'], value['proposed_total']), (False, 0))

    def test_agents_requirements_coverage_kickoff_plan_and_components(self):
        agents = api.agents(self.m, {})
        self.assertTrue(agents['configured'])
        self.assertEqual([host['host'] for host in agents['hosts']], ['codex'])
        self.assertTrue(agents['hosts'][0]['available'])
        requirements = api.requirements(self.m, {})
        self.assertEqual(requirements['items']['items'][0]['text'], 'Keep the tagged exception.')
        self.assertEqual(api.coverage(self.m, {})['sessions'], [])
        self.assertIsNone(api.kickoff(self.m, {})['template'])
        self.assertEqual(api.components(self.m, {})['total'], 0)
        action(self.m, 'component', {'title': 'Client CRM', 'kind': 'system', 'description': 'The CRM holds client records.',
                                     'status': 'confirmed'}, 'crm')
        [item] = api.components(self.m, {'status': 'confirmed'})['components']
        self.assertEqual((item['title'], item['actor']), ('Client CRM', 'workspace-user'))
        health = api.health(self.m, {})
        self.assertEqual(health['clients'], ['mcp'])
        self.assertEqual(health['review_host']['hosts'], ['codex'])
        self.assertNotIn('csrf', health)

    def test_kickoff_endpoint_reports_template_questions(self):
        from memory_module import templates
        project = self.root / 'engagement'
        result = templates.scaffold(project, 'engagement', git=False, _launcher=LAUNCHER)
        with Memory(result['database'], read_only=True) as reader:
            value = api.kickoff(reader, {})
            self.assertEqual(value['template'], 'engagement')
            self.assertTrue(value['questions'])
            now = api.now(reader, {})
            self.assertEqual(now['kickoff']['template'], 'engagement')
            self.assertEqual(api.plan(reader, {})['total'], 7)


    def test_focus_endpoint_adds_the_check_output_to_each_attempt_and_reports_totals_without_an_id(self):
        from memory_module import focus
        self.assertEqual(api.focus(self.m, {'id': self.ids['design']})['state'], 'none')
        focus.propose(self.m, self.ids['parser'], problem='The parser loses the tagged characters.', actor='assistant',
                      request_key='api-focus', hypotheses=[{'statement': 'The decoder ignores the tag.', 'approach': 'Read the tag first.'}])
        [hypothesis] = api.focus(self.m, {'id': self.ids['parser']})['hypotheses']
        base = {'episode_id': self.ids['parser'], 'start_key': 'api-start'}
        check = {**base, 'attempt': 1, 'run_id': self.ids['run'], 'host': 'codex', 'hypothesis_id': hypothesis['id'], 'command': ['python3', 'check.py'],
                 'check_passed': False, 'exit_code': 1, 'duration_ms': 900, 'output_tail': 'FAILED (failures=1)\n', 'timed_out': False,
                 'changed_lines': 3, 'evidence_summary': 'The check failed with exit code 1. Last output line: FAILED (failures=1).'}
        receipts = [('FocusStarted', {**base, 'mode': 'relay', 'attempts': 1, 'check': {}, 'hypothesis_ids': [hypothesis['id']]}),
                    ('FocusAttemptRequested', {**base, 'attempt': 1, 'run_id': self.ids['run'], 'host': 'codex', 'hypothesis_id': hypothesis['id']}),
                    ('FocusCheckRecorded', check)]
        with self.m._write():
            for name, payload in receipts:
                codex_host.receipt(self.m, session_id='api-focus', event_name=name, episode_id=self.ids['parser'], key='api-focus:' + name, payload=payload)
        value = api.focus(self.m, {'id': self.ids['parser']})
        self.assertEqual((value['state'], value['eligible'], value['focus']['problem']), ('running', True, 'The parser loses the tagged characters.'))
        [attempt] = value['attempts']
        self.assertEqual((attempt['exit_code'], attempt['check_passed'], attempt['changed_lines']), (1, False, 3))
        self.assertEqual((attempt['output_tail'], attempt['timed_out'], attempt['evidence_summary']),
                         ('FAILED (failures=1)\n', False, check['evidence_summary']))
        self.assertEqual(value['report']['checks'], {'passed': 0, 'failed': 1, 'not_run': 0})
        totals = api.focus(self.m, {})
        self.assertEqual((totals['problems'], totals['attempts']), (1, 1))
        self.assertNotIn('state', totals)
        with self.assertRaises(InvalidRecord):
            api.focus(self.m, {'id': 'x' * 201})


class HiveTests(unittest.TestCase):
    """The Hive view of the control panel: its read endpoint, the actions of the user and the live revision."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name).resolve()
        os.environ[machine.DATABASE_VARIABLE] = str(self.root / 'machine' / 'machine.sqlite')
        self.m, self.ids = build(self.root)
        self.keys = 0

    def tearDown(self):
        self.m.close()
        os.environ[machine.DATABASE_VARIABLE] = MACHINE['default']
        shutil.rmtree(self.temp.name, ignore_errors=True)

    def key(self):
        self.keys += 1
        return 'hive-key-' + str(self.keys)

    def swarm(self):
        """A blind swarm with a Codex agent in the open phase and a Claude agent still in the blind phase."""
        with hive.Hive(hive.path_for(self.m)) as store:
            swarm = hive.open_swarm(store, title='Parser fault', purpose='Find why the parser loses the tagged characters.',
                                    kind='manual', request_key='hive-open', episode_id=self.ids['parser'])['id']
            # A command basis is verified only for a check of the swarm, which a start that names the swarm links to its check.
            with self.m._write():
                start = {'episode_id': self.ids['parser'], 'start_key': 'hive-start'}
                codex_host.receipt(self.m, session_id='hive-check', event_name='FocusStarted', episode_id=self.ids['parser'],
                                   payload={**start, 'swarm_id': swarm}, key='hive-started')
                receipt = codex_host.receipt(self.m, session_id='hive-check', event_name='FocusCheckRecorded', episode_id=self.ids['parser'],
                                             payload={**start, 'exit_code': 0, 'check_passed': True}, key='hive-check')
            for agent, host in (('codex-1', 'codex'), ('claude-1', 'claude')):
                hive.join(store, swarm, agent_id=agent, role='worker', host=host, worktree=self.root)
            log = lambda agent, move, **fields: hive.log(store, swarm, agent, move=move, request_key=self.key(), fields=fields, memory=self.m)['id']
            ids = {'orient': log('codex-1', 'orient', claim='The goal is a parser that keeps every tagged character.',
                                 bases=[{'kind': 'file', 'value': 'src/app.py:1'}])}
            log('claude-1', 'orient', claim='The goal of this agent is a decoder that keeps the tag.', bases=[{'kind': 'file', 'value': 'src/util.py:1'}])
            ids['hypothesis'] = log('codex-1', 'hypothesis', claim='The decoder drops the tag before it reads the text.',
                                    detail='Run the fixture with a tagged file and compare the output.')
            ids['observation'] = log('codex-1', 'observation', claim='The tagged fixture passes after the decoder reads the tag first.',
                                     bases=[{'kind': 'command', 'value': receipt}])
            ids['conclusion'] = log('codex-1', 'conclusion', claim='Reading the tag first keeps every tagged character.',
                                    confidence='high', cites=[ids['observation']])
            ids['question'] = log('claude-1', 'question', claim='Should the decoder accept files without a tag?', addressee='user')
        return swarm, ids

    def test_the_endpoint_lists_swarms_and_shows_one_swarm_as_a_timeline(self):
        self.assertEqual(api.hive(self.m, {}), {'swarms': [], 'total': 0, 'max_seq': 0, 'exists': False})
        self.assertFalse(hive.path_for(self.m).exists(), 'a read created the hive file')
        swarm, ids = self.swarm()
        listing = api.hive(self.m, {})
        self.assertEqual((listing['total'], listing['max_seq'], listing['exists']), (1, 6, True))
        [row] = listing['swarms']
        self.assertEqual((row['title'], row['state'], row['kind'], row['entries']), ('Parser fault', 'open', 'manual', 6))
        self.assertEqual([(agent['agent_id'], agent['host'], agent['revealed']) for agent in row['agents']],
                         [('codex-1', 'codex', True), ('claude-1', 'claude', False)])
        value = api.hive(self.m, {'id': swarm})
        self.assertEqual((value['swarm']['id'], value['swarm']['blind'], value['total'], value['more']), (swarm, True, 6, False))
        self.assertNotIn('request_key', value['swarm'])
        self.assertEqual({agent['agent_id']: (agent['phase'], agent['entries']) for agent in value['agents']},
                         {'codex-1': ('open', 4), 'claude-1': ('blind', 2)})
        entries = {entry['id']: entry for entry in value['entries']}
        observation = entries[ids['observation']]
        self.assertEqual((observation['agent'], observation['host'], observation['move']), ('codex-1', 'codex', 'observation'))
        self.assertEqual([(basis['kind'], basis['verified'], basis['exit_code']) for basis in observation['bases']], [('command', True, 0)])
        self.assertEqual(observation['links_in'], [{'from': ids['conclusion'], 'relation': 'cites'}])
        conclusion = entries[ids['conclusion']]
        self.assertEqual((conclusion['confidence'], conclusion['links_out']), ('high', [{'to': ids['observation'], 'relation': 'cites'}]))
        self.assertIn('exited with code 0', conclusion['confirmed'])
        self.assertIsNone(conclusion['disputed'])
        self.assertEqual(entries[ids['question']]['addressed_to'], 'user')
        # The observer of the control panel sees the hypothesis that the blind agent does not see.
        self.assertIn(ids['hypothesis'], entries)
        with self.assertRaises(InvalidRecord) as caught:
            api.hive(self.m, {'id': 'swarm_missing'})
        self.assertIn('was not found in the hive', str(caught.exception))
        with Memory(self.m.path, read_only=True) as reader:
            self.assertEqual(api.hive(reader, {'id': swarm})['total'], 6)

    def test_the_user_posts_answers_observes_closes_and_purges_as_workspace_user(self):
        with self.assertRaises(InvalidRecord) as caught:
            action(self.m, 'hive_post', {'swarm_id': 'swarm_missing', 'move': 'question', 'claim': 'Is there a swarm?', 'addressee': 'all'}, 'post-none')
        self.assertEqual(str(caught.exception), workspace.HIVE_MISSING)
        self.assertFalse(hive.path_for(self.m).exists())
        swarm, ids = self.swarm()
        answer = action(self.m, 'hive_post', {'swarm_id': swarm, 'move': 'answer', 'target': ids['question'],
                                              'claim': 'The decoder refuses a file without a tag.'}, 'post-answer')
        self.assertEqual((answer['move'], answer['agent_id'], answer['duplicate']), ('answer', 'workspace-user', False))
        repeated = action(self.m, 'hive_post', {'swarm_id': swarm, 'move': 'answer', 'target': ids['question'],
                                                'claim': 'The decoder refuses a file without a tag.'}, 'post-answer')
        self.assertEqual((repeated['id'], repeated['duplicate']), (answer['id'], True))
        question = action(self.m, 'hive_post', {'swarm_id': swarm, 'move': 'question', 'addressee': 'agent:codex-1',
                                                'claim': 'Does the fix also cover the archive import?'}, 'post-question')
        observation = action(self.m, 'hive_post', {'swarm_id': swarm, 'move': 'observation', 'claim': 'The utility module holds the only constant.',
                                                   'bases': [{'kind': 'file', 'value': 'src/util.py:1'}], 'detail': ''}, 'post-observation')
        value = api.hive(self.m, {'id': swarm})
        entries = {entry['id']: entry for entry in value['entries']}
        self.assertEqual(entries[answer['id']]['links_out'], [{'to': ids['question'], 'relation': 'answers'}])
        self.assertEqual((entries[question['id']]['host'], entries[question['id']]['addressed_to']), ('workspace-user', 'agent:codex-1'))
        self.assertEqual(entries[observation['id']]['bases'], [{'kind': 'file', 'value': 'src/util.py:1', 'verified': True, 'exit_code': None}])
        user = next(agent for agent in value['agents'] if agent['agent_id'] == 'workspace-user')
        self.assertEqual((user['host'], user['role'], user['phase']), ('workspace-user', 'user', 'open'))
        # The user posts only question, answer and observation, and a refusal of the hive guides the correction.
        with self.assertRaises(InvalidRecord) as caught:
            action(self.m, 'hive_post', {'swarm_id': swarm, 'move': 'conclusion', 'claim': 'The fix is complete.'}, 'post-conclusion')
        self.assertIn('question, an answer or an observation', str(caught.exception))
        with self.assertRaises(hive.Refusal) as caught:
            action(self.m, 'hive_post', {'swarm_id': swarm, 'move': 'observation', 'claim': 'no full stop here',
                                         'bases': [{'kind': 'url', 'value': 'https://example.com'}]}, 'post-bad')
        self.assertEqual(caught.exception.details['rule'], 'claim_sentence')
        closed = action(self.m, 'hive_close', {'swarm_id': swarm, 'summary': 'The decoder reads the tag first.'}, 'close-swarm')
        self.assertEqual((closed['main_memory'], list(closed['confirmed']), closed['duplicate']), ('recorded', [ids['conclusion']], False))
        self.assertEqual(api.hive(self.m, {'id': swarm})['swarm']['state'], 'closed')
        with self.assertRaises(InvalidRecord):
            action(self.m, 'hive_post', {'swarm_id': swarm, 'move': 'question', 'claim': 'Is the swarm still open?', 'addressee': 'all'}, 'post-late')
        with self.assertRaises(InvalidRecord):
            action(self.m, 'hive_purge', {'closed_before_days': -1}, 'purge-negative')
        purged = action(self.m, 'hive_purge', {'closed_before_days': 0}, 'purge-now')
        self.assertEqual((purged['swarms'], purged['agents'], purged['entries']), (1, 3, 9))
        self.assertEqual(api.hive(self.m, {})['total'], 0)
        receipt = codex_host.read_receipt(self.m, purged['receipt_id'])
        self.assertEqual((receipt['event_name'], receipt['payload']['entries']), ('HivePurged', 9))

    def test_the_live_revision_follows_new_entries_joins_and_closes_of_the_hive(self):
        with Viewer(self.m.path, 'hive-revision-token') as server:
            empty = server.revision()
            swarm, ids = self.swarm()
            opened = server.revision()
            self.assertNotEqual(opened, empty)
            self.assertEqual(live.hive_state(self.m), [6, 1, 0, 2])
            with hive.Hive(hive.path_for(self.m)) as store:
                hive.log(store, swarm, 'claude-1', move='hypothesis', request_key='late', fields={
                    'claim': 'The archive import uses another decoder.', 'detail': 'Import a tagged archive and compare it.'})
            logged = server.revision()
            self.assertNotEqual(logged, opened)
            self.assertEqual(live.hive_state(self.m)[0], 7)
            self.assertEqual(server.revision(), logged)
            with hive.Hive(hive.path_for(self.m)) as store:
                hive.close(store, swarm, summary='The swarm ends.', request_key='close')
            self.assertNotEqual(server.revision(), logged)


class ReadOnlyTests(unittest.TestCase):
    def test_every_endpoint_reads_a_database_without_optional_tables(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            root = Path(folder).resolve()
            path = root / '.memory' / 'project.sqlite'
            path.parent.mkdir()
            with Memory.create(path, 'Bare', ['Keep the exception.']) as memory:
                episode = memory.start('Bare work', 'Check read paths.', 'action', 'Every read succeeds.')['id']
                source = memory.source('bare', 'Bare source', 'A summary.', 'A body.', 'user')['id']
                tables = memory.db.execute("SELECT name FROM sqlite_master ORDER BY name").fetchall()
            params = {'work': {'id': episode}, 'lineage': {'id': episode}, 'record': {'id': source}, 'focus': {'id': episode}}
            with Memory(path, read_only=True) as reader:
                for name, endpoint in api.ENDPOINTS.items():
                    with self.subTest(endpoint=name):
                        if name == 'run':
                            with self.assertRaises(InvalidRecord):
                                endpoint(reader, {'id': 'check_missing'})
                            continue
                        self.assertIsInstance(endpoint(reader, params.get(name, {})), dict)
                self.assertEqual((api.agents(reader, {})['configured'], api.agents(reader, {})['hosts']), (False, []))
            with Memory(path, read_only=True) as reader:
                self.assertEqual(reader.db.execute("SELECT name FROM sqlite_master ORDER BY name").fetchall(), tables)
            self.assertFalse((path.parent / 'hive.sqlite').exists(), 'a read created the hive file')


class LiveApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name).resolve()
        self.m, self.ids = build(self.root)
        ready = queue.Queue()

        def serve():
            with Viewer(self.m.path, 'live-api-test-token') as server:
                ready.put(server)
                server.serve_forever()
        self.thread = threading.Thread(target=serve, daemon=True)
        self.thread.start()
        self.server = ready.get(timeout=3)
        self.origin = f'http://127.0.0.1:{self.server.server_port}'
        self.base = self.origin + '/live-api-test-token/'

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.m.close()
        shutil.rmtree(self.temp.name, ignore_errors=True)

    def get(self, route, headers=None):
        with urlopen(Request(self.base + route, headers=headers or {}), timeout=10) as response:
            return response.status, response.headers['ETag'], json.load(response)

    def status(self, request):
        try:
            with urlopen(request, timeout=10) as response:
                return response.status
        except HTTPError as error:
            error.close()
            return error.code

    def test_every_endpoint_is_served_with_a_revision_and_an_etag(self):
        params = {'work': '?id=' + self.ids['parser'], 'lineage': '?id=' + self.ids['old'], 'record': '?id=' + self.ids['source'],
                  'run': '?id=' + self.ids['run'], 'focus': '?id=' + self.ids['parser']}
        for name in api.ENDPOINTS:
            with self.subTest(endpoint=name):
                status, etag, value = self.get('api/' + name + params.get(name, ''))
                self.assertEqual(status, 200)
                self.assertIn('revision', value)
                self.assertEqual(self.status(Request(self.base + 'api/' + name + params.get(name, ''), headers={'If-None-Match': etag})), 304)
        _, _, health = self.get('api/health')
        # The panel is read only, so the health response hands out no token for writes.
        self.assertNotIn('csrf', health)
        self.assertEqual(self.status(Request(self.base + 'api/work?id=episode_missing')), 400)

    def test_token_unknown_endpoints_and_removed_review_route_are_refused(self):
        self.assertEqual(self.status(Request(self.origin + '/live-api-test-tokex/api/health')), 403)
        self.assertEqual(self.status(Request(self.origin + '/api/health')), 403)
        self.assertEqual(self.status(Request(self.base + 'api/skills')), 404)
        # The panel is read only: every write is refused, whatever its route, headers or body.
        headers = {'Content-Type': 'application/json', 'Origin': self.origin}
        for route, body in (('api/actions', b'{}'), ('api/reviews', b'{}'), ('api/actions', b'[' * 2000 + b']' * 2000)):
            with self.assertRaises(HTTPError) as caught:
                urlopen(Request(self.base + route, data=body, headers=headers), timeout=20)
            self.assertEqual(caught.exception.code, 405)
            self.assertEqual(json.load(caught.exception)['error'], 'ReadOnly')
            caught.exception.close()

    def test_expensive_results_are_cached_per_revision(self):
        with patch('memory_module.architecture.model', wraps=architecture.model) as model:
            self.get('api/architecture')
            self.get('api/architecture')
            self.assertEqual(model.call_count, 1)
            # A write from the chat changes the revision, so the next read computes the model again.
            action(self.m, 'comment', {'episode_id': self.ids['design'], 'expected_version': self.m.episode(self.ids['design'])['version'],
                                       'text': 'The design needs one more example.'}, 'live-comment')
            self.get('api/architecture')
            self.assertEqual(model.call_count, 2)

    def test_a_partial_request_from_another_client_does_not_stall_the_panel(self):
        import socket
        import time
        self.get('api/health')
        with socket.create_connection(('127.0.0.1', self.server.server_port)) as slow:
            slow.sendall(b'GET /')
            time.sleep(0.2)
            started = time.monotonic()
            status, _, _ = self.get('api/health')
            elapsed = time.monotonic() - started
        self.assertEqual(status, 200)
        self.assertLess(elapsed, 2.5)

    def test_project_files_are_checked_at_most_once_per_interval(self):
        # A server that is not serving can be used from this thread; SQLite connections stay in their own thread.
        # The fixture has agent runs, so the content signature of the project is part of the revision.
        with Viewer(self.m.path, 'interval-test-token') as server:
            before = server.revision()
            (self.root / 'src/util.py').write_text('VALUE = 2\n')
            self.assertEqual(server.revision(), before)
            server.tree_interval = 0
            self.assertNotEqual(server.revision(), before)


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name).resolve()
        self.m, self.ids = build(self.root)

    def tearDown(self):
        self.m.close()
        shutil.rmtree(self.temp.name, ignore_errors=True)

    def data(self, path):
        text = path.read_text().split('<script id="memory-data" type="application/json">')[1].split('</script>')[0]
        return json.loads(text)

    def test_full_export_embeds_every_view_under_its_request_key(self):
        target = self.root / 'full.html'
        result = self.m.export_html(target, include_bodies=True)
        data = self.data(target)
        self.assertFalse(data['live'])
        responses = data['responses']
        self.assertEqual(result['responses'], len(responses))
        for key in ('health', 'now', 'board?limit=100', 'records?limit=100&view=decisions', 'work?id=' + self.ids['parser'],
                    'record?id=' + self.ids['old'], 'lineage?id=' + self.ids['old'], 'work_graph', 'architecture', 'learning',
                    'agents', 'requirements', 'kickoff', 'plan', 'components', 'record?body_offset=0&id=' + self.ids['source']):
            self.assertIn(key, responses)
        self.assertEqual([item['key'] for item in data['omitted']], ['machine'])
        # The usage ledger belongs to the machine, so a snapshot carries none and the Usage view says so.
        self.assertFalse([key for key in responses if key.startswith('usage')])
        self.assertNotIn('csrf', responses['health'])
        # A record key holds the live response, so a panel that requests record?id= finds the body offline as well.
        live = json.loads(json.dumps(api.record(self.m, {'id': self.ids['source']})))
        self.assertEqual(responses['record?id=' + self.ids['source']], live)
        self.assertEqual(responses['record?body_offset=0&id=' + self.ids['source']]['record']['detail']['body'], 'Change the parser within src.')
        self.assertEqual([item['id'] for item in responses['sprints?limit=100']['sprints']], [])
        self.assertEqual(responses['run?id=' + self.ids['run']]['run']['report']['result'], 'complete')
        self.assertEqual(responses['records?limit=100&view=decisions']['total'], 3)

    def test_scoped_export_omits_project_wide_views_and_escapes_markup(self):
        self.m.record(self.ids['design'], 'note', {'text': '</script><script>window.injected=true</script>'},
                      expected_version=self.m.episode(self.ids['design'])['version'], actor='fixture', request_key='injection')
        target = self.root / 'scoped.html'
        self.m.export_html(target, episode_id=self.ids['parser'])
        html = target.read_text()
        data = self.data(target)
        responses = data['responses']
        for key in ('now', 'learning', 'agents', 'architecture', 'record?body_offset=0&id=' + self.ids['source']):
            self.assertNotIn(key, responses)
        from memory_module.viewer import PROJECT_VIEWS
        self.assertEqual([item['key'] for item in data['omitted']], list(PROJECT_VIEWS) + ['machine'])
        self.assertTrue(all(item['reason'].endswith('.') for item in data['omitted']))
        self.assertNotIn('body', responses['record?id=' + self.ids['source']]['record']['detail'])
        self.assertIn('sprints?limit=100', responses)
        self.assertEqual([card['id'] for card in responses['board?limit=100']['cards']], [self.ids['parser']])
        self.assertEqual(responses['records?limit=100&view=decisions']['total'], 2)
        self.assertEqual([item['id'] for item in responses['health']['episodes']], [self.ids['parser']])
        self.assertNotIn('work?id=' + self.ids['design'], responses)
        self.assertNotIn('window.injected', html)
        design = self.root / 'design.html'
        self.m.export_html(design, episode_id=self.ids['design'])
        self.assertNotIn('<script>window.injected', design.read_text())
        self.assertIn('\\u003c/script', design.read_text())
        with self.assertRaises(InvalidRecord):
            self.m.export_html(self.root / 'small.html', max_records=1)
        self.assertFalse((self.root / 'small.html').exists())

    def test_record_text_with_policy_placeholders_is_exported_unchanged(self):
        text = '__SCRIPT_HASH__ __STYLE_HASH__ __MEMORY_DATA__ stay as written.'
        note = self.m.record(self.ids['design'], 'note', {'text': text}, expected_version=self.m.episode(self.ids['design'])['version'],
                             actor='fixture', request_key='placeholders')
        target = self.root / 'placeholders.html'
        self.m.export_html(target)
        data = self.data(target)
        self.assertEqual(data['responses']['record?id=' + note['id']]['record']['detail']['payload']['text'], text)


class MachineCommandTests(unittest.TestCase):
    """The command line reads the machine memory and creates it once."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name).resolve()
        self.database = self.root / 'machine' / 'machine.sqlite'
        os.environ[machine.DATABASE_VARIABLE] = str(self.database)
        self.m, self.ids = build(self.root)

    def tearDown(self):
        self.m.close()
        os.environ[machine.DATABASE_VARIABLE] = MACHINE['default']
        shutil.rmtree(self.temp.name, ignore_errors=True)

    def run_command(self, *arguments):
        from memory_module import cli
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = cli.main(['machine', *arguments, '--project', str(self.root)])
        self.assertEqual(code, 0)
        return json.loads(stream.getvalue())

    def test_machine_init_list_and_rules_report_the_memory_of_this_computer(self):
        empty = self.run_command('list')
        self.assertEqual((empty['exists'], empty['projects'], empty['rules']), (False, [], []))
        self.assertIn('project-memory machine init', empty['note'])
        created = self.run_command('init')
        self.assertEqual((created['created'], created['database']), (True, str(self.database)))
        self.assertTrue(self.database.exists())
        listed = self.run_command('list')
        self.assertEqual([item['path'] for item in listed['projects']], [str(self.root)])
        self.assertEqual(listed['projects'][0]['phase'], 'development')
        self.assertEqual(self.run_command('rules')['rules'], [])
        proposal = machine.propose(self.m, **RULE, basis='Two releases failed after the suite was skipped.',
                                   roles=['worker'], actor='assistant', request_key='promote-cli')
        action(self.m, 'promotion', {'promotion_id': proposal['id'], 'status': 'accepted',
                                     'reason': 'The rule holds for every project on this computer.'}, 'accept-cli')
        rules = self.run_command('rules')
        self.assertEqual([rule['do'] for rule in rules['rules']], [RULE['do']])
        self.assertEqual(rules['rules'][0]['adopted_by'], 1)
        self.assertEqual(self.run_command('rules', '--role', 'worker')['rules_total'], 1)
        self.assertEqual(self.run_command('rules', '--role', 'assistant')['rules_total'], 0)
        self.assertEqual(self.run_command('init')['created'], False)


if __name__ == '__main__':
    unittest.main()
