"""Data contracts of the read API, the live server over it and the offline export."""
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

from memory_module import Memory, InvalidRecord, api, architecture, codex_host, hosts, planning, reviews
from memory_module.install import setup
from memory_module.live import Viewer
from memory_module.workspace import action

LAUNCHER = [sys.executable, '-m', 'memory_module.cli']


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
        self.m, self.ids = build(self.root)

    def tearDown(self):
        self.m.close()
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
            params = {'work': {'id': episode}, 'lineage': {'id': episode}, 'record': {'id': source}}
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
                  'run': '?id=' + self.ids['run']}
        for name in api.ENDPOINTS:
            with self.subTest(endpoint=name):
                status, etag, value = self.get('api/' + name + params.get(name, ''))
                self.assertEqual(status, 200)
                self.assertIn('revision', value)
                self.assertEqual(self.status(Request(self.base + 'api/' + name + params.get(name, ''), headers={'If-None-Match': etag})), 304)
        _, _, health = self.get('api/health')
        self.assertTrue(health['csrf'])
        _, _, invalid = None, None, None
        self.assertEqual(self.status(Request(self.base + 'api/work?id=episode_missing')), 400)

    def test_token_unknown_endpoints_and_removed_review_route_are_refused(self):
        self.assertEqual(self.status(Request(self.origin + '/live-api-test-tokex/api/health')), 403)
        self.assertEqual(self.status(Request(self.origin + '/api/health')), 403)
        self.assertEqual(self.status(Request(self.base + 'api/skills')), 404)
        _, _, health = self.get('api/health')
        headers = {'Content-Type': 'application/json', 'Origin': self.origin, 'X-Project-Memory': health['csrf']}
        self.assertEqual(self.status(Request(self.base + 'api/reviews', data=b'{}', headers=headers)), 404)
        self.assertEqual(self.status(Request(self.base + 'api/actions', data=b'{}', headers={**headers, 'X-Project-Memory': 'wrong'})), 403)
        self.assertEqual(self.status(Request(self.base + 'api/actions', data=b'{}', headers={**headers, 'Content-Type': 'text/plain'})), 403)
        self.assertEqual(self.status(Request(self.base + 'api/actions', data=b'[]', headers=headers)), 400)

    def test_expensive_results_are_cached_per_revision(self):
        with patch('memory_module.architecture.model', wraps=architecture.model) as model:
            self.get('api/architecture')
            self.get('api/architecture')
            self.assertEqual(model.call_count, 1)
            _, _, health = self.get('api/health')
            headers = {'Content-Type': 'application/json', 'Origin': self.origin, 'X-Project-Memory': health['csrf']}
            body = {'operation': 'comment', 'request_key': 'live-comment',
                    'data': {'episode_id': self.ids['design'], 'expected_version': self.m.episode(self.ids['design'])['version'],
                             'text': 'The design needs one more example.'}}
            self.assertEqual(self.status(Request(self.base + 'api/actions', data=json.dumps(body).encode(), headers=headers)), 200)
            self.get('api/architecture')
            self.assertEqual(model.call_count, 2)

    def test_deeply_nested_action_json_returns_an_error_response(self):
        _, _, health = self.get('api/health')
        headers = {'Content-Type': 'application/json', 'Origin': self.origin, 'X-Project-Memory': health['csrf']}
        body = b'[' * 200000 + b']' * 200000
        with self.assertRaises(HTTPError) as caught:
            urlopen(Request(self.base + 'api/actions', data=body, headers=headers), timeout=20)
        self.assertEqual(caught.exception.code, 400)
        self.assertEqual(json.load(caught.exception)['error'], 'RecursionError')
        caught.exception.close()
        self.assertEqual(self.get('api/health')[0], 200)

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
        self.assertEqual(data['omitted'], [])
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
        self.assertEqual([item['key'] for item in data['omitted']], list(PROJECT_VIEWS))
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


if __name__ == '__main__':
    unittest.main()
