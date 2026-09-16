"""The machine memory: the registry, the promotion of a rule, the mechanical checks and the composed machine rules."""
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from memory_module import Memory, Conflict, InvalidRecord
from memory_module import codex_host, guards, machine, mcp, workspace
from tests.test_guards import Fixture

RULE = {
    'when': 'A delegated run has no passing review',
    'do': 'Report that the work is prepared and waiting instead of merging it',
    'because': 'An unreviewed merge hides a defect until somebody else finds it',
    'exceptions': 'The user merges it in the control panel',
    'basis': 'Every project on this computer delegates work in the same way.',
}


def strings(value):
    """Every string inside a decoded JSON value, including the keys."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for key, item in value.items() for text in [str(key)] + strings(item)]
    if isinstance(value, (list, tuple)):
        return [text for item in value for text in strings(item)]
    return []


class MachineFixture(Fixture):
    """A project, an empty machine database and the helpers that promote a rule."""

    def setUp(self):
        super().setUp()
        # The machine database lives outside every project, as it does on a real computer.
        self.machine_temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(lambda: shutil.rmtree(self.machine_temp.name, ignore_errors=True))
        self.machine_path = Path(self.machine_temp.name).resolve() / 'machine.sqlite'
        patch = mock.patch.dict(os.environ, {machine.DATABASE_VARIABLE: str(self.machine_path)})
        patch.start()
        self.addCleanup(patch.stop)

    def propose(self, *, actor='assistant', roles=('worker',), **changes):
        values = {**RULE, 'roles': list(roles), 'actor': actor, 'request_key': self.key(), **changes}
        return machine.propose(self.m, **values)

    def promote(self, *, roles=('worker',), reason='The rule holds for every project on this computer.', **changes):
        proposal = self.propose(roles=roles, **changes)
        return machine.accept(self.m, proposal['id'], actor='workspace-user', reason=reason)

    def machine_memory(self, *, read_only=False):
        found = machine.open_machine(read_only=read_only)
        self.assertIsNotNone(found)
        self.addCleanup(found.close)
        return found


class LocationTests(MachineFixture):
    def test_the_environment_variable_names_the_machine_database(self):
        self.assertEqual(machine.database_path(), self.machine_path)
        self.assertFalse(machine.exists())
        with mock.patch.dict(os.environ, {machine.DATABASE_VARIABLE: ''}):
            self.assertEqual(machine.database_path(),
                             Path.home() / machine.DEFAULT_DIRECTORY / machine.DEFAULT_FILE)
        self.assertEqual(machine.database_path(self.root / 'other.sqlite'), self.root / 'other.sqlite')

    def test_the_machine_memory_is_an_ordinary_project_memory_database(self):
        created = machine.initialize()
        self.assertTrue(created['created'])
        self.assertEqual(created['database'], str(self.machine_path))
        self.assertEqual(created['machine'], machine.machine_name())
        with Memory(self.machine_path) as opened:
            self.assertEqual(opened.project, machine.machine_name())
            self.assertEqual(opened.initial_requirements, machine.REQUIREMENTS)
        self.assertFalse(machine.initialize()['created'])
        with self.assertRaises(Conflict):
            machine.create()

    def test_every_read_answers_before_the_machine_memory_exists(self):
        overview = machine.overview()
        self.assertEqual((overview['exists'], overview['rules'], overview['projects'], overview['rules_total']),
                         (False, [], [], 0))
        self.assertIn('It is created by project-memory machine init', overview['note'])
        self.assertIsNone(machine.reader())
        self.assertEqual(machine.promotions(self.m), [])
        for role in guards.RULE_ROLES:
            composed = guards.compose(self.m, role)
            self.assertEqual((composed['machine_rule_ids'], composed['machine_used'], composed['machine_total']),
                             ([], 0, 0))
            self.assertEqual(composed['machine_budget'], guards.MACHINE_BUDGETS[role])
            # A missing machine memory is not a failed read, so no error is reported.
            self.assertNotIn('machine_error', composed)
            self.assertIsNone(guards.instructions(self.m, role)['machine_error'])
        with Memory(self.m.path, read_only=True) as reader:
            self.assertEqual(machine.promotions(reader), [])
            self.assertEqual(guards.compose(reader, 'worker')['machine_rule_ids'], [])
        self.assertFalse(self.machine_path.exists())


class ProposalTests(MachineFixture):
    def test_a_proposal_is_recorded_in_the_project_and_writes_nothing_to_the_machine(self):
        proposal = self.propose()
        self.assertEqual(proposal['state'], 'proposed')
        self.assertEqual(proposal['actor'], 'assistant')
        self.assertEqual(proposal['rule']['when'], RULE['when'])
        self.assertEqual(proposal['rule']['roles'], ['worker'])
        self.assertIsNone(proposal['machine_rule_id'])
        self.assertIn('Nothing is written to the machine memory', proposal['note'])
        self.assertFalse(machine.exists())
        self.assertEqual([item['id'] for item in machine.promotions(self.m)], [proposal['id']])
        self.assertEqual(machine.promotions(self.m, state='accepted'), [])

    def test_a_repeated_request_key_replays_and_a_changed_one_conflicts(self):
        first = machine.propose(self.m, **RULE, roles=['worker'], actor='assistant', request_key='promote-1')
        again = machine.propose(self.m, **RULE, roles=['worker'], actor='assistant', request_key='promote-1')
        self.assertEqual(first['id'], again['id'])
        self.assertEqual(len(machine.promotions(self.m)), 1)
        with self.assertRaises(Conflict):
            machine.propose(self.m, **{**RULE, 'do': 'Merge it at once'}, roles=['worker'], actor='assistant',
                            request_key='promote-1')

    def test_a_promoted_rule_carries_no_path_pattern(self):
        with self.assertRaisesRegex(InvalidRecord, 'no path pattern'):
            self.propose(paths=['src'])

    def test_the_proposal_names_an_accepted_lesson_or_omits_it(self):
        lesson = self.lesson(roles=['worker'])
        self.accept(lesson)
        self.assertEqual(self.propose(lesson_id=lesson)['lesson_id'], lesson)
        with self.assertRaises(InvalidRecord):
            self.propose(lesson_id=self.evidence[0]['source_id'])

    def test_the_fields_of_a_proposal_are_checked(self):
        for bad in [{'when': ''}, {'do': '  '}, {'because': 42}, {'exceptions': None}, {'basis': ''}]:
            with self.subTest(bad=str(bad)), self.assertRaises(InvalidRecord):
                self.propose(**bad)
        for roles in [[], ['author'], ['worker', 'worker'], 'worker']:
            with self.subTest(roles=roles), self.assertRaises(InvalidRecord):
                self.propose(roles=roles)
        with self.assertRaises(InvalidRecord):
            self.propose(pattern_type='habit')
        self.assertEqual(machine.promotions(self.m), [])


class CheckTests(MachineFixture):
    def refused(self, **changes):
        with self.assertRaises(InvalidRecord) as caught:
            self.propose(**changes)
        return caught.exception

    def test_the_checks_refuse_text_that_belongs_to_this_project_alone(self):
        cases = {
            'absolute_path': {'do': 'Read /srv/project/notes/plan.txt before editing'},
            'project_name': {'do': 'Run the Guards fixture before editing'},
            'record_identifier': {'do': 'Read event_0123456789abcdef0123456789abcdef before editing'},
            'electronic_mail': {'do': 'Ask support@example.org before editing'},
            'host_name': {'do': 'Open build.example.org before editing'},
        }
        for check, change in cases.items():
            with self.subTest(check=check):
                error = self.refused(**change)
                self.assertEqual(error.details['checks'], [check])
                self.assertEqual(error.details['matches'][0]['field'], 'do')
                self.assertIn('rewrite the text', str(error))
        self.assertEqual(machine.promotions(self.m), [])

    def test_a_refusal_reports_what_matched_without_repeating_the_value(self):
        error = self.refused(do='Ask maria@example.org before editing', basis='It applies on /var/data as well.')
        self.assertEqual(error.details['checks'], ['absolute_path', 'electronic_mail'])
        self.assertEqual({match['field'] for match in error.details['matches']}, {'do', 'basis'})
        text = json.dumps({'message': str(error), 'details': error.details})
        for value in ['maria@example.org', 'example.org', '/var/data']:
            self.assertNotIn(value, text)
        self.assertEqual(error.details['next_step']['action'], 'rewrite_promotion')

    def test_a_document_of_this_project_is_refused_by_its_file_name(self):
        document = self.root / 'delivery-plan.md'
        document.write_text('The delivery plan of this engagement.\n', encoding='utf-8')
        self.m.document(str(document))
        self.assertIn('delivery-plan.md', machine.document_names(self.m))
        error = self.refused(do='Read delivery-plan.md before editing')
        self.assertEqual(error.details['checks'], ['document_name'])

    def test_ordinary_rule_text_passes_every_check(self):
        for text in ['Run the unit tests before the review',
                     'Record the reason in the plan, for example version 1.2 of the report',
                     'Read the file that the plan names, such as the design note in docs']:
            with self.subTest(text=text[:30]):
                self.assertEqual(machine.inspect_text(self.m, {'do': text}), [])

    def test_the_check_reads_the_name_of_the_project_and_of_its_folder(self):
        terms = machine.project_terms(self.m)
        self.assertIn('guards', terms)
        self.assertIn(self.root.name.lower(), terms)


class AcceptanceTests(MachineFixture):
    def test_only_the_user_accepts_a_promotion(self):
        proposal = self.propose()
        for actor in ['assistant', 'user', 'worker']:
            with self.subTest(actor=actor), self.assertRaisesRegex(InvalidRecord, 'workspace-user'):
                machine.accept(self.m, proposal['id'], actor=actor, reason='The agent decides.')
        self.assertFalse(machine.exists())
        self.assertEqual(machine.promotion(self.m, proposal['id'])['state'], 'proposed')

    def test_acceptance_writes_the_rule_into_the_machine_memory(self):
        accepted = self.promote()
        self.assertEqual(accepted['state'], 'accepted')
        self.assertEqual(accepted['decided_by'], 'workspace-user')
        self.assertTrue(accepted['rule_is_new'])
        self.assertEqual(accepted['adopted_by'], 1)
        self.assertTrue(machine.exists())
        found = machine.rules(self.machine_memory(read_only=True))
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['rule_id'], accepted['machine_rule_id'])
        self.assertEqual(found[0]['when'], RULE['when'])
        self.assertEqual(found[0]['basis'], RULE['basis'])
        self.assertEqual(found[0]['roles'], ['worker'])
        self.assertEqual(found[0]['adopted_by'], 1)

    def test_the_project_records_the_acceptance_by_identifier_only(self):
        accepted = self.promote()
        row = dict(self.m.db.execute('SELECT * FROM rule_promotions WHERE id=?', (accepted['id'],)).fetchone())
        self.assertEqual(row['machine_rule_id'], accepted['machine_rule_id'])
        self.assertEqual(row['state'], 'accepted')
        self.assertEqual(row['decided_by'], 'workspace-user')
        self.assertNotIn(machine.machine_name(), json.dumps(row))
        self.assertNotIn(str(self.machine_path), json.dumps(row))

    def test_the_machine_memory_holds_no_identifier_or_name_of_the_project(self):
        accepted = self.promote(keywords=['parser'], failure_type='unreviewed merge')
        machine_memory = self.machine_memory(read_only=True)
        stored = '\n'.join(str(row[0]) for row in machine_memory.db.execute(
            "SELECT payload FROM events UNION ALL SELECT body FROM sources UNION ALL SELECT request_key FROM events "
            "UNION ALL SELECT source_key FROM sources")).lower()
        self.assertIn('unreviewed merge', stored)
        for value in [accepted['id'], self.m.project, str(self.m.path), str(self.root), self.root.name, self.lessons]:
            self.assertNotIn(str(value).lower(), stored)
        # The registry is a separate table of this database, and it is never part of a record.
        self.assertEqual([item['path'] for item in machine.registry(machine_memory)], [str(self.root)])

    def test_the_registry_records_the_path_the_template_and_the_phase(self):
        from memory_module import planning
        planning.set_phase(self.m, phase='production', reason='The product serves customers.', actor='workspace-user')
        self.promote()
        [entry] = machine.registry(self.machine_memory(read_only=True))
        self.assertEqual(entry['path'], str(self.root))
        self.assertEqual(entry['name'], self.root.name)
        self.assertEqual(entry['phase'], 'production')
        self.assertIsNone(entry['template'])

    def test_a_repeated_acceptance_returns_the_recorded_promotion(self):
        proposal = self.propose()
        first = machine.accept(self.m, proposal['id'], actor='workspace-user', reason='It holds everywhere.')
        again = machine.accept(self.m, proposal['id'], actor='workspace-user', reason='It holds everywhere.')
        self.assertEqual(again['machine_rule_id'], first['machine_rule_id'])
        self.assertEqual(len(machine.rules(self.machine_memory(read_only=True))), 1)

    def test_the_user_may_correct_the_text_when_accepting(self):
        proposal = self.propose()
        accepted = machine.accept(self.m, proposal['id'], actor='workspace-user', reason='The wording is clearer.',
                                  changes={'do': 'Report that the work is prepared and waiting'})
        [rule] = machine.rules(self.machine_memory(read_only=True))
        self.assertEqual(rule['do'], 'Report that the work is prepared and waiting')
        self.assertEqual(accepted['machine_rule_id'], rule['rule_id'])
        second = self.propose()
        with self.assertRaises(InvalidRecord):
            machine.accept(self.m, second['id'], actor='workspace-user', reason='It names the project.',
                           changes={'do': 'Run the Guards fixture first'})
        self.assertEqual(machine.promotion(self.m, second['id'])['state'], 'proposed')

    def test_the_user_declines_a_proposal_and_nothing_reaches_the_machine(self):
        proposal = self.propose()
        declined = machine.decline(self.m, proposal['id'], actor='workspace-user', reason='This holds for one project.')
        self.assertEqual(declined['state'], 'declined')
        self.assertEqual(declined['reason'], 'This holds for one project.')
        self.assertFalse(machine.exists())
        with self.assertRaises(InvalidRecord):
            machine.accept(self.m, proposal['id'], actor='workspace-user', reason='I changed my mind.')
        with self.assertRaisesRegex(InvalidRecord, 'workspace-user'):
            machine.decline(self.m, proposal['id'], actor='assistant', reason='The agent decides.')

    def test_a_recorded_promotion_cannot_be_deleted_or_rewritten(self):
        proposal = self.propose()
        with self.assertRaises(Exception):
            self.m.db.execute('DELETE FROM rule_promotions WHERE id=?', (proposal['id'],))
        with self.assertRaises(Exception):
            self.m.db.execute("UPDATE rule_promotions SET rule='{}' WHERE id=?", (proposal['id'],))


class AdoptionTests(MachineFixture):
    def second_project(self):
        folder = self.root / 'second'
        folder.mkdir()
        memory = Memory.create(folder / '.memory' / 'memory.sqlite', 'Second engagement', ['Preserve recorded scope.'])
        self.addCleanup(memory.close)
        codex_host.initialize(memory)
        return memory

    def test_two_projects_that_promote_the_same_rule_share_one_machine_rule(self):
        self.promote()
        other = self.second_project()
        proposal = machine.propose(other, **RULE, roles=['worker'], actor='assistant', request_key='promote-other')
        accepted = machine.accept(other, proposal['id'], actor='workspace-user', reason='It holds here as well.')
        self.assertFalse(accepted['rule_is_new'])
        self.assertEqual(accepted['adopted_by'], 2)
        found = machine.rules(self.machine_memory(read_only=True))
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['adopted_by'], 2)
        self.assertEqual(len(machine.registry(self.machine_memory(read_only=True))), 2)

    def test_the_view_states_that_outcomes_are_not_combined_across_projects(self):
        self.promote()
        overview = machine.overview()
        self.assertTrue(overview['exists'])
        self.assertEqual(overview['rules_total'], 1)
        self.assertEqual(overview['projects_total'], 1)
        self.assertIn('no outcome is combined across projects', overview['note'])
        self.assertEqual(overview['rules'][0]['adopted_by'], 1)


class CompositionTests(MachineFixture):
    def project_rule(self, *, roles=('worker',), when='Editing the parser', do='Run the Unicode fixture first',
                     exceptions='Documentation changes'):
        lesson = self.lesson(roles=list(roles), when=when + '.', do=do + '.', exceptions=exceptions + '.')
        self.accept(lesson)
        return lesson

    def test_machine_rules_are_composed_after_the_project_rules_and_are_labelled(self):
        project = self.project_rule()
        promoted = self.promote()
        composed = guards.compose(self.m, 'worker')
        self.assertEqual(composed['rule_ids'], [project])
        self.assertEqual(composed['machine_rule_ids'], [promoted['machine_rule_id']])
        lines = composed['text'].splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith('When Editing the parser.'))
        self.assertTrue(lines[1].startswith(guards.MACHINE_LABEL))
        # The first machine rule also pays for the heading of the machine rules in the instructions.
        self.assertEqual(composed['used'], len(composed['text']) - 1 + guards.MACHINE_OVERHEAD)
        self.assertEqual(composed['machine_used'], len(lines[1]) + guards.MACHINE_OVERHEAD)
        self.assertEqual(composed['machine_budget'], guards.MACHINE_BUDGETS['worker'])
        self.assertEqual(composed['omitted'], [])

    def test_the_instructions_name_the_two_groups_of_rules(self):
        self.project_rule()
        self.promote()
        composed = guards.instructions(self.m, 'worker')
        self.assertIn(guards.RULES_HEADING, composed['text'])
        self.assertIn(guards.MACHINE_HEADING, composed['text'])
        self.assertLess(composed['text'].index(guards.RULES_HEADING), composed['text'].index(guards.MACHINE_HEADING))
        self.assertEqual(composed['machine_total'], 1)
        self.assertEqual(len(composed['machine_rule_ids']), 1)

    def test_a_machine_rule_of_another_role_is_not_composed(self):
        self.promote(roles=['reviewer'])
        self.assertEqual(guards.compose(self.m, 'worker')['machine_rule_ids'], [])
        self.assertEqual(len(guards.compose(self.m, 'reviewer')['machine_rule_ids']), 1)

    def test_each_role_keeps_its_own_sub_budget(self):
        self.promote(roles=['assistant', 'worker', 'reviewer'])
        for role, expected in guards.MACHINE_BUDGETS.items():
            with self.subTest(role=role):
                self.assertEqual(guards.compose(self.m, role)['machine_budget'], expected)

    def test_a_machine_rule_beyond_the_sub_budget_is_reported_as_omitted(self):
        first = self.promote(roles=['assistant'])
        second = self.promote(roles=['assistant'], when='A run reads the project direction',
                              do='Read the requirements before proposing a change of objective',
                              because='A change of objective without the requirements repeats settled work',
                              exceptions='The user states the new objective')
        composed = guards.compose(self.m, 'assistant')
        self.assertEqual(composed['machine_total'], 2)
        self.assertEqual(composed['machine_rule_ids'], [second['machine_rule_id']])
        self.assertLessEqual(composed['machine_used'], guards.MACHINE_BUDGETS['assistant'])
        [omitted] = composed['omitted']
        self.assertEqual((omitted['lesson_id'], omitted['level']), (first['machine_rule_id'], 'machine'))
        self.assertIn('remain for machine rules in this role', omitted['reason'])

    def test_the_project_rules_take_precedence_when_the_budget_is_tight(self):
        project = self.project_rule()
        promoted = self.promote()
        block = guards.render_rule({**RULE, 'exceptions': RULE['exceptions']})
        budget = len('When Editing the parser. Do Run the Unicode fixture first. Exceptions: Documentation changes.') + 10
        composed = guards.compose(self.m, 'worker', budget=budget)
        self.assertEqual(composed['rule_ids'], [project])
        self.assertEqual(composed['machine_rule_ids'], [])
        self.assertLess(composed['machine_budget'], len(block))
        self.assertEqual([entry['lesson_id'] for entry in composed['omitted']], [promoted['machine_rule_id']])

    def test_the_machine_rules_can_be_left_out_or_given_directly(self):
        promoted = self.promote()
        self.assertEqual(guards.compose(self.m, 'worker', machine=False)['machine_rule_ids'], [])
        given = self.machine_memory(read_only=True)
        self.assertEqual(guards.compose(self.m, 'worker', machine=given)['machine_rule_ids'],
                         [promoted['machine_rule_id']])

    def test_a_retired_machine_rule_is_no_longer_composed_and_stays_readable(self):
        promoted = self.promote()
        self.assertEqual(len(guards.compose(self.m, 'worker')['machine_rule_ids']), 1)
        with self.assertRaisesRegex(InvalidRecord, 'workspace-user'):
            machine.retire(self.m, promoted['machine_rule_id'], actor='assistant', reason='The agent decides.')
        retired = machine.retire(self.m, promoted['machine_rule_id'], actor='workspace-user',
                                 reason='The practice changed, so the rule no longer holds.')
        self.assertEqual(retired['status'], 'retired')
        self.assertEqual(guards.compose(self.m, 'worker')['machine_rule_ids'], [])
        self.assertEqual(machine.rules(self.machine_memory(read_only=True)), [])
        [history] = machine.rules(self.machine_memory(read_only=True), include_retired=True)
        self.assertEqual((history['rule_id'], history['status']), (promoted['machine_rule_id'], 'retired'))
        self.assertEqual(machine.overview()['retired'][0]['rule_id'], promoted['machine_rule_id'])

    def test_a_read_only_project_composes_the_machine_rules(self):
        promoted = self.promote()
        with Memory(self.m.path, read_only=True) as reader:
            composed = guards.compose(reader, 'worker')
            self.assertEqual(composed['machine_rule_ids'], [promoted['machine_rule_id']])
            self.assertEqual(machine.promotions(reader)[0]['state'], 'accepted')


class SurfaceTests(MachineFixture):
    def test_the_panel_accepts_a_promotion_and_declines_another(self):
        first = self.propose()
        accepted = workspace.action(self.m, 'promotion',
                                    {'promotion_id': first['id'], 'status': 'accepted',
                                     'reason': 'The rule holds for every project here.'}, 'panel-accept')
        self.assertEqual(accepted['state'], 'accepted')
        self.assertEqual(accepted['decided_by'], 'workspace-user')
        self.assertEqual(len(machine.rules(self.machine_memory(read_only=True))), 1)
        again = workspace.action(self.m, 'promotion',
                                 {'promotion_id': first['id'], 'status': 'accepted',
                                  'reason': 'The rule holds for every project here.'}, 'panel-accept')
        self.assertEqual(again['machine_rule_id'], accepted['machine_rule_id'])
        second = self.propose(when='A run edits the report')
        declined = workspace.action(self.m, 'promotion',
                                    {'promotion_id': second['id'], 'status': 'declined',
                                     'reason': 'This belongs to one project.'}, 'panel-decline')
        self.assertEqual(declined['state'], 'declined')
        with self.assertRaises(InvalidRecord):
            workspace.action(self.m, 'promotion', {'promotion_id': second['id'], 'status': 'retired',
                                                   'reason': 'Neither.'}, 'panel-bad')

    def test_the_panel_retires_a_machine_rule(self):
        promoted = self.promote()
        retired = workspace.action(self.m, 'machine_rule',
                                   {'rule_id': promoted['machine_rule_id'], 'status': 'retired',
                                    'reason': 'The practice changed.'}, 'panel-retire')
        self.assertEqual(retired['status'], 'retired')
        self.assertEqual(machine.rules(self.machine_memory(read_only=True)), [])

    def test_an_agent_proposes_over_mcp_and_cannot_accept(self):
        arguments = {'operation': 'promote_rule', 'request_key': 'mcp-1',
                     'data': {**RULE, 'roles': ['worker'], 'actor': 'assistant'}}
        proposal = mcp.dispatch(self.m, 'memory_write', arguments)
        self.assertEqual(proposal['state'], 'proposed')
        self.assertFalse(machine.exists())
        with self.assertRaisesRegex(InvalidRecord, 'reserved'):
            mcp.dispatch(self.m, 'memory_write', {'operation': 'promote_rule', 'request_key': 'mcp-2',
                                                  'data': {**RULE, 'roles': ['worker'], 'actor': 'workspace-user'}})
        self.assertNotIn('promotion', mcp.OPERATIONS)
        self.assertNotIn('machine_rule', mcp.OPERATIONS)
        schema = mcp.schema('promote_rule')
        self.assertIn('nothing is written to the machine memory until the user accepts it', schema['rules'])

    def test_the_mcp_view_reports_the_rules_and_keeps_the_registry_local(self):
        self.promote()
        view = mcp.dispatch(self.m, 'memory_get', {'view': 'machine'})
        self.assertEqual(len(view['rules']), 1)
        self.assertEqual(view['rules'][0]['basis'], RULE['basis'])
        self.assertNotIn('projects', view)
        self.assertEqual(view['projects_total'], 1)
        self.assertIn('stays local', view['registry_note'])
        # Compare against the decoded strings, because JSON doubles the separators of a Windows path.
        self.assertFalse(any(str(self.root) in text or self.root.name in text for text in strings(view)))
        self.assertIn('machine', mcp.VIEWS)

    def test_the_mcp_view_answers_before_the_machine_memory_exists(self):
        view = mcp.dispatch(self.m, 'memory_get', {'view': 'machine'})
        self.assertEqual((view['exists'], view['rules'], view['rules_total']), (False, [], 0))


class NamedProjectFixture(MachineFixture):
    def named(self, name, folder):
        """A project with the given name in a folder of the given name."""
        root = self.root / 'named' / folder
        root.mkdir(parents=True)
        memory = Memory.create(root / '.memory' / 'memory.sqlite', name, ['Preserve recorded scope.'])
        self.addCleanup(memory.close)
        codex_host.initialize(memory)
        return memory

    def checks(self, memory, text, field='do'):
        return [match['check'] for match in machine.inspect_text(memory, {field: text})]


class CheckVariantTests(NamedProjectFixture):
    def test_the_failure_type_is_checked_at_proposal_and_at_acceptance(self):
        for value, check in [('Guards outage', 'project_name'), ('Outage in ' + str(self.root), 'absolute_path'),
                             ('Outage on db.northwind.example', 'host_name')]:
            with self.subTest(check=check), self.assertRaises(InvalidRecord) as refused:
                self.propose(failure_type=value)
            self.assertIn({'check': check, 'field': 'failure_type'},
                          [{'check': item['check'], 'field': item['field']} for item in refused.exception.details['matches']])
        proposal = self.propose(failure_type='unreviewed merge')
        with self.assertRaises(InvalidRecord):
            machine.accept(self.m, proposal['id'], actor='workspace-user', reason='It holds everywhere.',
                           changes={'failure_type': 'Guards outage'})
        self.assertFalse(machine.exists())

    def test_the_project_name_is_found_in_its_ordinary_variants(self):
        acme = self.named('Acme Shipping', 'acme-shipping')
        for text in ['Acme-specific release checklist', 'AcmeShipping', 'acme_shipping', 'Acmes team', "Acme's team",
                     'ACME SHIPPING rules']:
            with self.subTest(text=text):
                self.assertEqual(self.checks(acme, text), ['project_name'])
        short = self.named('LR', 'proj-lr')
        self.assertEqual(self.checks(short, 'Follow the LR release checklist'), ['project_name'])
        self.assertEqual(self.checks(short, 'Follow the release checklist of the project'), [])
        accented = self.named('Société Générale Audit', 'sg-audit')
        self.assertEqual(self.checks(accented, 'Ask Société before the audit'), ['project_name'])
        self.assertEqual(self.checks(self.m, 'Use the safeguards of the parser'), [])

    def test_ordinary_words_of_the_project_name_do_not_block_a_generic_rule(self):
        automation = self.named('Invoice Intake Automation', 'invoice-intake-automation')
        for field, text in [('basis', 'Every automation on this computer handles failures the same way.'),
                            ('when', 'An invoice workflow changes')]:
            with self.subTest(text=text):
                self.assertEqual(self.checks(automation, text, field), [])
        self.assertEqual(self.checks(automation, 'The InvoiceIntakeAutomation fails'), ['project_name'])
        engagement = self.named('Port Authority Operating Model Review', 'port-authority-review')
        self.assertEqual(self.checks(engagement, 'A model review is planned', 'when'), [])
        self.assertEqual(self.checks(engagement, 'Brief the port authority operating model review team'), ['project_name'])
        proposal = machine.propose(automation, **{**RULE, 'when': 'An invoice workflow changes',
                                                  'basis': 'Every automation on this computer handles failures the same way.'},
                                   roles=['worker'], actor='assistant', request_key='generic-automation')
        accepted = machine.accept(automation, proposal['id'], actor='workspace-user', reason='It holds everywhere.')
        self.assertEqual(accepted['state'], 'accepted')

    def test_record_identifiers_are_found_in_any_case_cut_short_or_with_another_prefix(self):
        hexadecimal = 'abab57e6' + '0123456789abcdef' * 1 + 'fedcba98'
        for text in ['EVENT_' + hexadecimal.upper(), 'ep2_' + hexadecimal, 'event_' + hexadecimal[:10],
                     '0f8fad5b-d9cb-469f-a165-70867728950e', hexadecimal]:
            with self.subTest(text=text):
                self.assertEqual(self.checks(self.m, 'Read ' + text + ' first'), ['record_identifier'])
        for text in ['Keep release 20260916 notes', 'Use version 1.2.3', 'Add a decade of cafe notes']:
            with self.subTest(text=text):
                self.assertEqual(self.checks(self.m, text), [])

    def test_network_addresses_and_host_names_are_found_in_every_form(self):
        for text in ['10.20.30.40', 'localhost:8080', 'fe80::1ff:fe23:4567:890a', 'buildhost:8443', 'portal.contoso.md',
                     'git.contoso.sh', 'ci.contoso.rs']:
            with self.subTest(text=text):
                self.assertEqual(self.checks(self.m, 'Open ' + text + ' first'), ['host_name'])
        for text in ['maria@buildhost', 'maria@exämple.de']:
            with self.subTest(text=text):
                self.assertEqual(self.checks(self.m, 'Ask ' + text + ' first'), ['electronic_mail'])
        for text in ['Update notes.md and README.md', 'Meet at 10:30:00', 'Use std::string', 'Keep the ratio 16:9',
                     'Use Python 3.12']:
            with self.subTest(text=text):
                self.assertEqual(self.checks(self.m, text), [])

    def test_home_network_and_variable_paths_are_absolute_paths(self):
        for text in ['~marius/notes', '\\\\fileserver\\share\\notes', '%USERPROFILE%\\notes',
                     'Users/someone/dev/notes', '$HOME/notes', '${HOME}/notes']:
            with self.subTest(text=text):
                self.assertEqual(self.checks(self.m, 'Read ' + text + ' first'), ['absolute_path'])
        self.assertEqual(self.checks(self.m, 'Record the reason and/or the evidence'), [])


class MachineIntegrityTests(MachineFixture):
    def test_a_rule_text_held_with_other_roles_is_refused_instead_of_dropping_them(self):
        self.promote(roles=['worker'])
        proposal = self.propose(roles=['reviewer'])
        with self.assertRaisesRegex(InvalidRecord, 'other roles or triggers') as refused:
            machine.accept(self.m, proposal['id'], actor='workspace-user', reason='It holds for reviewers.')
        self.assertEqual(refused.exception.details['machine_rule']['roles'], ['worker'])
        self.assertEqual(machine.promotion(self.m, proposal['id'])['state'], 'proposed')
        same = self.propose(roles=['worker'])
        self.assertFalse(machine.accept(self.m, same['id'], actor='workspace-user', reason='Same rule.')['rule_is_new'])

    def test_retiring_a_rule_twice_replays_the_retirement(self):
        promoted = self.promote()
        first = machine.retire(self.m, promoted['machine_rule_id'], actor='workspace-user', reason='Practice changed.')
        again = machine.retire(self.m, promoted['machine_rule_id'], actor='workspace-user', reason='Practice changed.')
        self.assertNotIn('duplicate', first)
        self.assertTrue(again['duplicate'])
        self.assertEqual(again['status'], 'retired')
        store = self.machine_memory(read_only=True)
        reviews = store.db.execute("SELECT count(*) FROM events WHERE kind='lesson_review'").fetchone()[0]
        self.assertEqual(reviews, 2)

    def test_a_lesson_written_straight_into_the_machine_memory_is_not_listed(self):
        self.promote()
        store = self.machine_memory()
        episode = machine._rules_episode(store)
        store.record(episode['id'], 'lesson', {'when': 'Copying notes', 'do': 'Copy the notes', 'because': 'Speed',
                                               'exceptions': 'None'},
                     expected_version=episode['version'], request_key='direct-lesson', actor='assistant',
                     evidence=[{'source_id': store.source('direct', 'Direct', 'Direct.', 'Direct.', 'tool')['id'],
                                'reason': 'The agent wrote it.'}])
        overview = machine.overview()
        self.assertEqual(overview['retired'], [])
        self.assertEqual(overview['rules_total'], 1)
        self.assertNotIn('Copy the notes', json.dumps(overview))

    def test_a_proposal_changes_only_to_the_decision_of_the_user(self):
        proposal = self.propose()
        for statement in ["UPDATE rule_promotions SET lesson_id='event_forged' WHERE id=?",
                          "UPDATE rule_promotions SET request_key='other', signature='x' WHERE id=?",
                          "UPDATE rule_promotions SET state='accepted', decided_by='assistant', decided_at='now', "
                          "decision_reason='r', machine_rule_id='event_fake' WHERE id=?",
                          "UPDATE rule_promotions SET state='accepted', decided_by='workspace-user', decided_at='now', "
                          "decision_reason='r' WHERE id=?"]:
            with self.subTest(statement=statement[:60]), self.assertRaises(Exception):
                self.m.db.execute(statement, (proposal['id'],))
        self.m.db.rollback()
        self.assertEqual(machine.promotion(self.m, proposal['id'])['state'], 'proposed')

    def test_adoptions_and_registered_projects_cannot_be_deleted(self):
        self.promote()
        store = self.machine_memory()
        for statement in ['DELETE FROM machine_adoptions', 'DELETE FROM machine_projects',
                          "UPDATE machine_adoptions SET basis='changed'", "UPDATE machine_projects SET path='/elsewhere'"]:
            with self.subTest(statement=statement), self.assertRaises(Exception):
                store.db.execute(statement)
        store.db.rollback()
        self.assertEqual(machine.overview()['rules'][0]['adopted_by'], 1)

    def test_a_phase_change_in_the_panel_updates_the_registry(self):
        self.promote()
        result = workspace.action(self.m, 'phase', {'phase': 'production', 'reason': 'The product serves customers.'},
                                  'phase-production')
        self.assertEqual(result['machine_registry'], 'updated')
        [entry] = machine.registry(self.machine_memory(read_only=True))
        self.assertEqual(entry['phase'], 'production')
        self.assertEqual(entry['phase_at'], result['at'])


class UnreadableMachineTests(MachineFixture):
    def check_every_read_answers(self):
        for role in guards.RULE_ROLES:
            composed = guards.compose(self.m, role)
            self.assertEqual(composed['machine_rule_ids'], [])
            self.assertIn('could not be read', composed['machine_error'])
            self.assertIn('could not be read', guards.instructions(self.m, role)['machine_error'])
        with Memory(self.m.path, read_only=True) as reader:
            self.assertIn('machine_error', guards.compose(reader, 'worker'))
        view = mcp.dispatch(self.m, 'memory_get', {'view': 'machine'})
        self.assertIn('could not be read', view['error'])
        self.assertEqual(codex_host.assistant_rules(self.m, session_id='s', targets=['src/app.py'])['text'], '')

    def test_a_file_that_is_not_a_database_does_not_stop_a_project_run(self):
        self.machine_path.write_bytes(os.urandom(4096))
        self.check_every_read_answers()

    def test_a_folder_in_place_of_the_database_does_not_stop_a_project_run(self):
        self.machine_path.mkdir()
        self.check_every_read_answers()


class AssistantHookTests(MachineFixture):
    def test_machine_rules_reach_the_assistant_without_a_project_rule(self):
        promoted = self.promote(roles=['assistant'])
        result = codex_host.assistant_rules(self.m, session_id='session-a', targets=[], text='fix the parser')
        self.assertEqual(result['machine_rule_ids'], [promoted['machine_rule_id']])
        self.assertIn(guards.MACHINE_LABEL, result['text'])
        self.assertIn(promoted['machine_rule_id'], result['text'])
        again = codex_host.assistant_rules(self.m, session_id='session-a', targets=[], text='fix the parser again')
        self.assertEqual(again['text'], '')

    def test_machine_rules_follow_a_project_rule_and_omissions_name_the_machine_view(self):
        lesson = self.lesson(roles=['assistant'])
        self.accept(lesson)
        first = self.promote(roles=['assistant'])
        second = self.promote(roles=['assistant'], when='A run reads the project direction',
                              do='Read the requirements before proposing a change of objective',
                              because='A change of objective without the requirements repeats settled work',
                              exceptions='The user states the new objective')
        result = codex_host.assistant_rules(self.m, session_id='session-b', targets=[], text='fix the parser')
        self.assertEqual(result['rule_ids'], [lesson])
        self.assertEqual(result['machine_rule_ids'], [second['machine_rule_id']])
        self.assertLess(result['text'].index(lesson), result['text'].index(second['machine_rule_id']))
        [omitted] = [entry for entry in result['omitted'] if entry.get('level') == 'machine']
        self.assertEqual(omitted['lesson_id'], first['machine_rule_id'])
        self.assertIn('Read them with memory_get machine', result['text'])
        self.assertNotIn(first['machine_rule_id'] + '. Read them with memory_get record', result['text'])


class CompositionAccountingTests(MachineFixture):
    def test_the_heading_of_the_machine_rules_is_counted_in_the_machine_budget(self):
        self.lesson_id = self.lesson(roles=['worker'])
        self.accept(self.lesson_id)
        self.promote(roles=['worker'])
        with_machine = guards.instructions(self.m, 'worker')
        without = guards.instructions(self.m, 'worker', machine=False)
        self.assertEqual(len(with_machine['machine_rule_ids']), 1)
        self.assertEqual(with_machine['characters'] - without['characters'], with_machine['machine_used'])
        self.assertLessEqual(with_machine['machine_used'], guards.MACHINE_BUDGETS['worker'])

    def test_an_omission_for_one_remaining_character_reads_as_a_sentence(self):
        promoted = self.promote(roles=['worker'])
        composed = guards.compose(self.m, 'worker', budget=1)
        [omitted] = composed['omitted']
        self.assertEqual(omitted['lesson_id'], promoted['machine_rule_id'])
        self.assertIn('within the 1 character that remains for machine rules', omitted['reason'])

    def test_the_metrics_of_a_run_name_the_machine_rules_it_carried(self):
        from memory_module import reviews
        promoted = self.promote(roles=['worker'])
        run = {'role': 'work', 'episode_id': self.lessons, 'snapshot': {'paths': ['src/app.py'], 'objective': 'Repair.'}}
        metrics = {}
        reviews.compose_instructions(self.m, run, metrics)
        self.assertEqual(metrics['machine_rule_ids'], [promoted['machine_rule_id']])
        self.assertEqual(metrics['machine_rules_total'], 1)


if __name__ == '__main__':
    unittest.main()
