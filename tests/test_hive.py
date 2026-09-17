"""The hive of section 12: storage, moves, guided validation, blind phase, tools, composition and distillation.

No real host runs. The delegated worker is the fake child process of tests/test_delegation.py,
extended so that it logs through the restricted hive server as a real worker would.
"""
import io
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from memory_module import Memory, InvalidRecord, Conflict, codex_host, delegation, dumps, hive, mcp, reviews
from memory_module.cli import main as cli_main
from memory_module.hive import Hive, path_for
from tests import test_delegation

ROOT = Path(__file__).resolve().parents[1]
DASHES = ('—', '–', ' - ')
MAIN_TOOLS_LIST_LIMIT = 7260


def git(folder, *args):
    result = subprocess.run(['git', '-C', str(folder), *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


class Fixture(unittest.TestCase):
    """A git project with a main memory, one episode and an empty hive beside the database."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name).resolve()
        git(self.root, 'init', '-q')
        git(self.root, 'config', 'user.name', 'Fixture')
        git(self.root, 'config', 'user.email', 'fixture@example.com')
        git(self.root, 'config', 'commit.gpgsign', 'false')
        (self.root / 'src').mkdir()
        (self.root / 'src/app.py').write_text('VALUE = 1\n')
        (self.root / 'src/parser.py').write_text('def parse(text):\n    return int(text)\n')
        git(self.root, 'add', '-A')
        git(self.root, 'commit', '-q', '-m', 'Initial')
        self.m = Memory.create(self.root / '.memory' / 'project.sqlite', 'Hive', ['Keep every recorded exception.'])
        codex_host.initialize(self.m)
        self.episode = self.m.start('Fix the parser', 'Find why the parser returns a wrong value.', 'code',
                                    'The parser test passes.')['id']
        self.source = self.m.source('user-request', 'Request', 'The user asks for the parser fix.',
                                    'Fix the parser so that the value test passes.', 'user')['id']
        self.clock = ['2026-09-17T10:00:00+00:00']
        self.h = Hive(path_for(self.m), clock=lambda: self.clock[0])
        self.keys = 0

    def tearDown(self):
        self.h.close()
        self.m.close()
        shutil.rmtree(self.temp.name, ignore_errors=True)

    def swarm(self, kind='focus', blind=True, purpose='Find why the parser returns a wrong value for signed numbers.', key=None):
        return hive.open_swarm(self.h, title='Parser fault', purpose=purpose, kind=kind, request_key=key or self.key(),
                               episode_id=self.episode, blind=blind, project=self.root)['id']

    def key(self):
        self.keys += 1
        return 'key-' + str(self.keys)

    def join(self, swarm, agent, host='codex', role='worker', **extra):
        return hive.join(self.h, swarm, agent_id=agent, role=role, host=host, **extra)

    def log(self, swarm, agent, move, memory=None, **fields):
        return hive.log(self.h, swarm, agent, move=move, request_key=self.key(), fields=fields, memory=memory)['id']

    def orient(self, swarm, agent, claim=None):
        return self.log(swarm, agent, 'orient', claim=claim or f'The goal of {agent} is a correct parser value.',
                        bases=[{'kind': 'file', 'value': 'src/parser.py:1-2'}])

    def team(self, *agents, blind=True, kind='focus'):
        swarm = self.swarm(kind=kind, blind=blind)
        for index, agent in enumerate(agents):
            self.join(swarm, agent, host='codex' if index % 2 == 0 else 'claude')
            self.orient(swarm, agent)
        return swarm

    def check_receipt(self, exit_code, key, swarm):
        """A check result of the swarm, recorded as focus.py records it: a start that names the swarm, then its check."""
        start = 'start-' + swarm
        codex_host.receipt(self.m, session_id='focus', event_name='FocusStarted', episode_id=self.episode,
                           payload={'episode_id': self.episode, 'start_key': start, 'swarm_id': swarm}, key='started:' + swarm)
        return codex_host.receipt(self.m, session_id='focus', event_name='FocusCheckRecorded', episode_id=self.episode,
                                  payload={'start_key': start, 'exit_code': exit_code, 'check_passed': exit_code == 0}, key=key)

    def assertRefused(self, rule, call):
        with self.assertRaises(hive.Refusal) as caught:
            call()
        error = caught.exception
        message = str(error)
        self.assertIn('rule ' + rule + '.', message)
        self.assertEqual(error.details['rule'], rule)
        self.assertEqual(error.details['execution'], 'not_started')
        self.assertEqual(error.details['next_step']['rule'], rule)
        self.assertTrue(error.details['next_step']['reason'].endswith('.'))
        for dash in DASHES:
            self.assertNotIn(dash, message)
        return error


class ValidationTests(Fixture):
    """Every rule refuses with its name and a correction, and nothing is recorded."""

    def test_each_rule_refuses_with_its_name_and_a_correction(self):
        swarm = self.swarm()
        self.join(swarm, 'codex-1')
        self.join(swarm, 'claude-2', host='claude')
        self.join(swarm, 'workspace-user', host='workspace-user', role='user')
        self.assertRefused('orient_first', lambda: self.log(swarm, 'codex-1', 'observation', claim='The parser reads text only.',
                                                            bases=[{'kind': 'file', 'value': 'src/parser.py:2'}]))
        # A question to the user is the one move accepted before orient.
        self.log(swarm, 'codex-1', 'question', claim='Which input shows the wrong value?', addressee='user')
        self.assertRefused('basis_kind', lambda: self.log(swarm, 'codex-1', 'orient', claim='The goal is a correct parser value.',
                                                          bases=[{'kind': 'url', 'value': 'https://example.com/parser'}]))
        own = self.orient(swarm, 'codex-1')
        self.orient(swarm, 'claude-2')
        observation = self.log(swarm, 'codex-1', 'observation', claim='The parser converts text with int.',
                               bases=[{'kind': 'file', 'value': 'src/parser.py:2'}])
        other = self.log(swarm, 'claude-2', 'observation', claim='The application value is one.',
                         bases=[{'kind': 'file', 'value': 'src/app.py:1'}])
        long_code = 'Code follows.\n```\n' + 'x = 1\n' * 21 + '```'
        cases = [
            ('move', 'codex-1', 'guess', {'claim': 'The parser is wrong.'}),
            ('fields', 'codex-1', 'observation', {'claim': 'The parser is wrong somewhere.', 'bases': [], 'confidence': 'low'}),
            ('required_fields', 'codex-1', 'observation', {'claim': 'The parser is wrong somewhere.'}),
            ('field_type', 'codex-1', 'hypothesis', {'claim': 'The parser is wrong somewhere.', 'detail': ['a list']}),
            ('orient_once', 'codex-1', 'orient', {'claim': 'The goal is a different parser value.',
                                                  'bases': [{'kind': 'file', 'value': 'src/app.py:1'}]}),
            ('user_moves', 'workspace-user', 'conclusion', {'claim': 'The parser is fine as it is.', 'confidence': 'high', 'cites': [own]}),
            ('claim_length', 'codex-1', 'hypothesis', {'claim': 'The parser ' + 'fails ' * 60 + '.', 'detail': 'Test it.'}),
            ('claim_sentence', 'codex-1', 'hypothesis', {'claim': 'parser broken', 'detail': 'Test it.'}),
            ('claim_sentence', 'codex-1', 'hypothesis', {'claim': 'The parser `int` call fails.', 'detail': 'Test it.'}),
            ('detail_length', 'codex-1', 'hypothesis', {'claim': 'The parser drops the sign.', 'detail': 'Long. ' * 250}),
            ('detail_code', 'codex-1', 'hypothesis', {'claim': 'The parser drops the sign.', 'detail': long_code}),
            ('checkpoint_length', 'codex-1', 'checkpoint', {'done': 'x' * 401, 'belief': 'b', 'open_questions': 'q', 'next_step': 'n'}),
            ('confidence', 'codex-1', 'conclusion', {'claim': 'The parser drops the sign.', 'confidence': 'certain', 'cites': [observation]}),
            ('addressee', 'codex-1', 'question', {'claim': 'Who wrote the parser module?', 'addressee': 'claude-2'}),
            ('blind_phase', 'codex-1', 'challenge', {'claim': 'The value in the application is not one.', 'target': other,
                                                     'bases': [{'kind': 'file', 'value': 'src/app.py:1'}]}),
            ('blind_phase', 'codex-1', 'question', {'claim': 'Which test did you run first?', 'addressee': 'agent:claude-2'}),
            ('target', 'codex-1', 'conclusion', {'claim': 'The parser drops the sign.', 'confidence': 'low', 'cites': ['e999']}),
            ('cites', 'codex-1', 'conclusion', {'claim': 'The parser drops the sign.', 'confidence': 'low', 'cites': [own, own]}),
            ('cites', 'codex-1', 'pattern', {'claim': 'Parsers of signed text drop the sign.', 'cites': [observation]}),
            ('pattern_basis', 'codex-1', 'pattern', {'claim': 'Parsers of signed text drop the sign.', 'cites': [own, observation]}),
            ('basis_shape', 'codex-1', 'observation', {'claim': 'The parser reads a file.', 'bases': [{'kind': 'file'}]}),
            ('basis_kind', 'codex-1', 'observation', {'claim': 'The parser reads a file.', 'bases': [{'kind': 'rumour', 'value': 'x'}]}),
            ('basis_count', 'codex-1', 'observation', {'claim': 'The parser reads a file.',
                                                       'bases': [{'kind': 'url', 'value': f'https://example.com/{n}'} for n in range(11)]}),
            ('basis_file', 'codex-1', 'observation', {'claim': 'The parser reads a file.', 'bases': [{'kind': 'file', 'value': 'src/parser.py'}]}),
            ('basis_file', 'codex-1', 'observation', {'claim': 'The parser reads a file.', 'bases': [{'kind': 'file', 'value': 'src/parser.py:9-2'}]}),
            ('basis_file', 'codex-1', 'observation', {'claim': 'The parser reads a file.', 'bases': [{'kind': 'file', 'value': '../secret.txt:1'}]}),
            ('basis_file', 'codex-1', 'observation', {'claim': 'The parser reads a file.', 'bases': [{'kind': 'file', 'value': 'src/missing.py:1'}]}),
            ('basis_entry', 'codex-1', 'observation', {'claim': 'The parser reads a file.', 'bases': [{'kind': 'entry', 'value': 'e999'}]}),
            ('basis_url', 'codex-1', 'observation', {'claim': 'The parser reads a file.', 'bases': [{'kind': 'url', 'value': 'parser docs'}]}),
            ('near_duplicate', 'claude-2', 'observation', {'claim': 'The parser converts text with int!',
                                                           'bases': [{'kind': 'file', 'value': 'src/app.py:1'}]}),
            ('near_duplicate', 'claude-2', 'observation', {'claim': 'The parser converts the text with int.',
                                                           'bases': [{'kind': 'file', 'value': 'src/app.py:1'}]}),
        ]
        before = self.h.db.execute('SELECT count(*) FROM entries').fetchone()[0]
        for rule, agent, move, fields in cases:
            with self.subTest(rule=rule, move=move):
                self.assertRefused(rule, lambda: hive.log(self.h, swarm, agent, move=move, request_key=self.key(), fields=fields))
        self.assertRefused('basis_source', lambda: self.log(swarm, 'codex-1', 'observation', memory=self.m,
                                                            claim='The request names the parser.',
                                                            bases=[{'kind': 'source', 'value': 'source_missing'}]))
        self.assertEqual(self.h.db.execute('SELECT count(*) FROM entries').fetchone()[0], before)
        duplicate = self.assertRefused('near_duplicate', lambda: self.log(
            swarm, 'claude-2', 'observation', claim='The parser converts text with int.', bases=[{'kind': 'file', 'value': 'src/app.py:1'}]))
        self.assertEqual(duplicate.details['next_step']['entry_id'], observation)
        self.assertEqual(duplicate.details['next_step']['suggested_moves'], ['support', 'challenge'])
        counts = hive.distill(self.h, swarm)['refusals']
        self.assertEqual(counts['basis_file'], 4)
        self.assertEqual(counts['near_duplicate'], 3)
        self.assertEqual(sum(counts.values()), len(cases) + 4)

    def test_targets_of_challenge_support_and_answer_follow_their_rules(self):
        swarm = self.team('codex-1', 'claude-2')
        mine = self.log(swarm, 'codex-1', 'observation', claim='The parser converts text with int.',
                        bases=[{'kind': 'file', 'value': 'src/parser.py:2'}])
        self.log(swarm, 'codex-1', 'hypothesis', claim='The parser drops the sign.', detail='Parse the text minus one.')
        self.log(swarm, 'claude-2', 'hypothesis', claim='The caller strips the sign.', detail='Read the caller.')
        own = self.assertRefused('own_entry', lambda: self.log(swarm, 'codex-1', 'support', claim='The conversion uses int indeed.',
                                                               target=mine, bases=[{'kind': 'file', 'value': 'src/app.py:1'}]))
        self.assertIn(mine, str(own))
        self.assertRefused('own_entry', lambda: self.log(swarm, 'codex-1', 'challenge', claim='The conversion does not use int.',
                                                         target=mine, bases=[{'kind': 'file', 'value': 'src/app.py:1'}]))
        self.assertRefused('target_move', lambda: self.log(swarm, 'claude-2', 'answer', claim='The value comes from the file.', target=mine))
        self.assertRefused('target', lambda: self.log(swarm, 'claude-2', 'answer', claim='The value comes from the file.', target='e999'))
        self.assertRefused('basis_independent', lambda: self.log(swarm, 'claude-2', 'support', claim='The parser calls int on the text.',
                                                                 target=mine, bases=[{'kind': 'file', 'value': 'src/parser.py:2'}]))
        self.assertRefused('basis_independent', lambda: self.log(swarm, 'claude-2', 'support', claim='The parser calls int on the text.',
                                                                 target=mine, bases=[{'kind': 'entry', 'value': mine}]))
        support = self.log(swarm, 'claude-2', 'support', claim='The parser calls int on the text.', target=mine,
                           bases=[{'kind': 'file', 'value': 'src/parser.py:1'}])
        question = self.log(swarm, 'claude-2', 'question', claim='Which input shows the wrong value?', addressee='role:worker')
        answer = self.log(swarm, 'codex-1', 'answer', claim='The input minus one shows the wrong value.', target=question)
        links = {tuple(row) for row in self.h.db.execute('SELECT from_entry,to_entry,relation FROM links')}
        self.assertIn((support, mine, 'supports'), links)
        self.assertIn((answer, question, 'answers'), links)

    def test_entry_limits_keep_room_for_conclusions_and_checkpoints(self):
        swarm = self.team('codex-1')
        observations = [self.log(swarm, 'codex-1', 'observation', claim=f'The parser line {n} holds statement number {n}.',
                                 bases=[{'kind': 'file', 'value': 'src/parser.py:1'}]) for n in range(10)]
        refusal = self.assertRefused('observation_limit', lambda: self.log(
            swarm, 'codex-1', 'observation', claim='The parser has one more statement.', bases=[{'kind': 'file', 'value': 'src/app.py:1'}]))
        self.assertEqual(refusal.details['next_step']['suggested_moves'], ['conclusion', 'checkpoint'])
        for n in range(25):
            self.log(swarm, 'codex-1', 'checkpoint', done=f'Step {n}.', belief='The sign is dropped.', open_questions='None.',
                     next_step='Continue.')
        self.assertEqual(self.h.db.execute("SELECT count(*) FROM entries WHERE agent_id='codex-1'").fetchone()[0], 36)
        self.assertRefused('entry_limit', lambda: self.log(swarm, 'codex-1', 'hypothesis', claim='The parser drops the sign.',
                                                           detail='Test with minus one.'))
        for n in range(4):
            self.log(swarm, 'codex-1', 'conclusion', claim=f'The parser fault number {n} is the sign handling.', confidence='low',
                     cites=[observations[n]])
        refusal = self.assertRefused('entry_limit', lambda: self.log(swarm, 'codex-1', 'checkpoint', done='All.', belief='Sign.',
                                                                     open_questions='None.', next_step='Stop.'))
        self.assertIn('final answer', str(refusal))

    def test_a_repeated_request_key_returns_the_entry_and_different_content_conflicts(self):
        swarm = self.team('codex-1')
        fields = {'claim': 'The parser drops the sign.', 'detail': 'Test with minus one.'}
        first = hive.log(self.h, swarm, 'codex-1', move='hypothesis', request_key='same', fields=fields)
        again = hive.log(self.h, swarm, 'codex-1', move='hypothesis', request_key='same', fields=fields)
        self.assertEqual((again['id'], again['duplicate']), (first['id'], True))
        with self.assertRaises(Conflict):
            hive.log(self.h, swarm, 'codex-1', move='hypothesis', request_key='same', fields={**fields, 'detail': 'Other.'})

    def test_a_file_in_the_worktree_of_the_agent_is_a_valid_basis(self):
        swarm = self.swarm()
        worktree = self.root / 'worktree'
        (worktree / 'src').mkdir(parents=True)
        (worktree / 'src/new.py').write_text('NEW = True\n')
        self.join(swarm, 'codex-1', worktree=worktree)
        self.join(swarm, 'claude-2', host='claude')
        self.log(swarm, 'codex-1', 'orient', claim='The goal is the new module.', bases=[{'kind': 'file', 'value': 'src/new.py:1'}])
        self.assertRefused('basis_file', lambda: self.log(swarm, 'claude-2', 'orient', claim='The goal is the new module.',
                                                          bases=[{'kind': 'file', 'value': 'src/new.py:1'}]))


class ReviewFindingTests(Fixture):
    """Regression tests for the findings of the independent review of the hive, 17 September 2026."""

    def test_a_command_basis_must_be_a_check_of_the_swarm(self):
        swarm = self.team('solo')
        other = self.swarm(key='other-swarm')
        user_command = codex_host.receipt(self.m, session_id='user-session', event_name='PostToolUse', tool_name='Bash',
                                          episode_id=self.episode, payload={'command': 'ls', 'exit_code': 0}, key='user-ls')
        foreign = self.check_receipt(0, 'check-of-other-swarm', other)
        for value in ('made-up-receipt', user_command, foreign):
            with self.subTest(value=value):
                self.assertRefused('basis_command', lambda: self.log(swarm, 'solo', 'observation', memory=self.m,
                                                                     claim='The unrelated command exited with code zero.',
                                                                     bases=[{'kind': 'command', 'value': value}]))
        self.assertRefused('basis_command', lambda: self.log(swarm, 'solo', 'observation', claim='The check passed without memory.',
                                                             bases=[{'kind': 'command', 'value': foreign}]))
        own = self.check_receipt(0, 'check-of-this-swarm', swarm)
        entry = self.log(swarm, 'solo', 'observation', memory=self.m, claim='The check of this swarm passed.',
                         bases=[{'kind': 'command', 'value': own}])
        self.assertEqual(tuple(self.h.db.execute('SELECT verified,exit_code FROM bases WHERE entry_id=?', (entry,)).fetchone()), (1, 0))

    def test_a_single_agent_cannot_set_off_a_lesson_without_a_check_of_the_swarm(self):
        swarm = self.team('solo')
        self.log(swarm, 'solo', 'hypothesis', claim='Rewriting the parser fixes the sign.', detail='Rewrite it.')
        seen = self.log(swarm, 'solo', 'observation', claim='The parser calls int on the raw text.',
                        bases=[{'kind': 'file', 'value': 'src/parser.py:2'}])
        conclusion = self.log(swarm, 'solo', 'conclusion', claim='Always rewrite parsers from scratch.', confidence='high', cites=[seen])
        self.assertRefused('pattern_basis', lambda: self.log(swarm, 'solo', 'pattern', claim='Rewriting parsers always fixes sign bugs.',
                                                             cites=[conclusion, seen]))
        result = hive.close(self.h, swarm, summary='Closed.', request_key='close', memory=self.m)
        self.assertEqual((result['confirmed'], result['proposals']), ({}, []))

    def test_a_confirmed_conclusion_that_is_also_disputed_proposes_no_lesson(self):
        swarm = self.team('c1', 'c2')
        for agent in ('c1', 'c2'):
            self.log(swarm, agent, 'hypothesis', claim=f'The fault seen by {agent} is the sign.', detail='Run the check.')
        passed = self.check_receipt(0, 'check-pass', swarm)
        good = self.log(swarm, 'c1', 'observation', memory=self.m, claim='The check passed after the sign fix.',
                        bases=[{'kind': 'command', 'value': passed}])
        conclusion = self.log(swarm, 'c1', 'conclusion', claim='The sign fix repairs the parser.', confidence='high', cites=[good])
        self.log(swarm, 'c2', 'challenge', claim='The sign fix breaks input with spaces.', target=conclusion,
                 bases=[{'kind': 'file', 'value': 'src/parser.py:1'}])
        other = self.log(swarm, 'c2', 'observation', claim='The caller passes the text unchanged.', bases=[{'kind': 'file', 'value': 'src/app.py:1'}])
        self.log(swarm, 'c1', 'pattern', claim='Sign handling belongs inside the parser.', cites=[conclusion, other])
        result = hive.close(self.h, swarm, summary='Closed.', request_key='close', memory=self.m)
        self.assertEqual((list(result['confirmed']), list(result['disputed'])), ([conclusion], [conclusion]))
        self.assertEqual((result['pattern_candidates'], result['proposals']), ([], []))

    def test_a_reply_without_evidence_keeps_a_verified_challenge_open(self):
        swarm = self.team('d1', 'd2')
        for agent in ('d1', 'd2'):
            self.log(swarm, agent, 'hypothesis', claim=f'The fault seen by {agent} is the sign.', detail='Read the parser.')
        seen = self.log(swarm, 'd1', 'observation', claim='The parser calls int on the raw text.', bases=[{'kind': 'file', 'value': 'src/parser.py:2'}])
        conclusion = self.log(swarm, 'd1', 'conclusion', claim='The parser is correct as it is.', confidence='medium', cites=[seen])
        challenge = self.log(swarm, 'd2', 'challenge', claim='The parser drops the sign of negative input.', target=conclusion,
                             bases=[{'kind': 'file', 'value': 'src/parser.py:1'}])
        self.log(swarm, 'd1', 'question', claim='Which negative input did you try?', addressee='agent:d2', reply_to=challenge)
        self.assertIn(conclusion, hive.distill(self.h, swarm)['disputed'])
        self.assertEqual([row['id'] for row in hive.resume(self.h, swarm, 'd1')['challenges']], [challenge])
        self.log(swarm, 'd1', 'observation', claim='Negative input keeps its sign in the parser.', reply_to=challenge,
                 bases=[{'kind': 'file', 'value': 'src/app.py:1'}])
        self.assertEqual(hive.distill(self.h, swarm)['disputed'], {})

    def test_identical_checkpoints_and_restated_claims_are_refused(self):
        swarm = self.team('alice')
        fields = {'done': 'Nothing yet.', 'belief': 'Nothing changed.', 'open_questions': 'None.', 'next_step': 'Wait.'}
        first = self.log(swarm, 'alice', 'checkpoint', **fields)
        refusal = self.assertRefused('near_duplicate', lambda: self.log(swarm, 'alice', 'checkpoint', **{**fields, 'done': 'nothing  yet!'}))
        self.assertEqual(refusal.details['next_step']['entry_id'], first)
        self.log(swarm, 'alice', 'checkpoint', **{**fields, 'done': 'Step 2 is done.'})
        seen = self.log(swarm, 'alice', 'observation', claim='The parse function calls int on the raw text.',
                        bases=[{'kind': 'file', 'value': 'src/parser.py:2'}])
        self.assertRefused('near_duplicate', lambda: self.log(swarm, 'alice', 'observation', claim='The raw text the parse function calls int on.',
                                                              bases=[{'kind': 'file', 'value': 'src/app.py:1'}]))
        restated = self.assertRefused('near_duplicate', lambda: self.log(swarm, 'alice', 'conclusion', confidence='high', cites=[seen],
                                                                         claim='The parse function calls int on the raw text.'))
        self.assertEqual(restated.details['next_step']['entry_id'], seen)
        # A conclusion may confirm a hypothesis in the same words.
        self.log(swarm, 'alice', 'hypothesis', claim='The parser drops the minus sign.', detail='Parse minus one.')
        self.log(swarm, 'alice', 'conclusion', claim='The parser drops the minus sign.', confidence='medium', cites=[seen])

    def test_a_basis_must_name_something_that_can_be_checked(self):
        swarm = self.team('codex-1', 'claude-2')
        cases = [('basis_checked', [{'kind': 'url', 'value': 'https://example.invalid/nothing'}]),
                 ('basis_file', [{'kind': 'file', 'value': 'src/parser.py:9000'}]),
                 ('basis_file', [{'kind': 'file', 'value': 'src/parser.py:2-3'}]),
                 ('basis_file', [{'kind': 'file', 'value': 'src:1'}]),
                 ('basis_source', [{'kind': 'source', 'value': self.source}])]
        for rule, bases in cases:
            with self.subTest(bases=bases):
                self.assertRefused(rule, lambda: self.log(swarm, 'codex-1', 'observation', claim='The parser has a basis here.', bases=bases))
        self.log(swarm, 'codex-1', 'observation', claim='The parser has two lines.', bases=[{'kind': 'file', 'value': 'src/parser.py:1-2'}])
        self.log(swarm, 'codex-1', 'observation', claim='A forum post describes the sign fault.',
                 bases=[{'kind': 'url', 'value': 'https://example.com/forum'}, {'kind': 'file', 'value': 'src/parser.py:1'}])

    def test_a_conclusion_cites_evidence_rather_than_the_goal(self):
        swarm = self.swarm()
        self.join(swarm, 'alice')
        goal = self.orient(swarm, 'alice')
        hypothesis = self.log(swarm, 'alice', 'hypothesis', claim='The module needs a rewrite.', detail='Read it.')
        self.assertRefused('cites_evidence', lambda: self.log(swarm, 'alice', 'conclusion', claim='The whole module needs a rewrite now.',
                                                              confidence='high', cites=[goal, hypothesis]))

    def test_an_entry_basis_names_a_visible_entry_of_the_same_swarm(self):
        swarm = self.team('a1', 'a2')
        other = self.team('b1')
        foreign = self.log(other, 'b1', 'observation', claim='The other swarm reads the app value.', bases=[{'kind': 'file', 'value': 'src/app.py:1'}])
        hidden = self.log(swarm, 'a2', 'hypothesis', claim='The caller strips the sign.', detail='Read the caller.')
        values = (foreign, hidden, 'e999')
        refusals = [self.assertRefused('basis_entry', lambda value=value: self.log(swarm, 'a1', 'observation', claim='The earlier entry holds the answer.',
                                                                                   bases=[{'kind': 'entry', 'value': value}]))
                    for value in values]
        # A hidden entry, an entry of another swarm and a missing entry get the same answer, so the refusal reveals nothing.
        self.assertEqual(len({str(refusal).replace(value, 'ID') for refusal, value in zip(refusals, values)}), 1)

    def test_a_file_basis_does_not_follow_a_link_out_of_the_worktree(self):
        outside = self.root.parent / (self.root.name + '-outside.txt')
        outside.write_text('secret\n')
        self.addCleanup(outside.unlink)
        worktree = self.root / 'worktree'
        (worktree / 'src').mkdir(parents=True)
        (worktree / 'src/link.py').symlink_to(outside)
        (worktree / 'src/real.py').write_text('REAL = True\n')
        swarm = self.swarm()
        self.join(swarm, 'a1', worktree=worktree)
        self.assertRefused('basis_file', lambda: self.log(swarm, 'a1', 'orient', claim='The goal is the linked file.',
                                                          bases=[{'kind': 'file', 'value': 'src/link.py:1'}]))
        self.log(swarm, 'a1', 'orient', claim='The goal is the real file.', bases=[{'kind': 'file', 'value': 'src/real.py:1'}])


class StorageTests(Fixture):
    def test_entries_bases_and_links_are_append_only(self):
        swarm = self.team('codex-1', 'claude-2', blind=False, kind='manual')
        observation = self.log(swarm, 'codex-1', 'observation', claim='The parser converts text with int.',
                               bases=[{'kind': 'file', 'value': 'src/parser.py:2'}])
        self.log(swarm, 'claude-2', 'support', claim='The parser calls int on the text.', target=observation,
                 bases=[{'kind': 'file', 'value': 'src/parser.py:1'}])
        statements = ["UPDATE entries SET claim='Changed.'", 'DELETE FROM entries', "UPDATE bases SET value='x'", 'DELETE FROM bases',
                      "UPDATE links SET relation='cites'", 'DELETE FROM links', 'DELETE FROM agents', 'DELETE FROM swarms',
                      "UPDATE agents SET role='reviewer'", "UPDATE swarms SET title='Changed'"]
        for statement in statements:
            with self.subTest(statement=statement):
                with self.assertRaises(sqlite3.IntegrityError):
                    self.h.db.execute(statement)
        hive.close(self.h, swarm, summary='The team closed the swarm.', request_key='close')
        for statement in ("UPDATE swarms SET state='open'", "UPDATE swarms SET summary='Rewritten.'", 'DELETE FROM entries'):
            with self.subTest(statement=statement):
                with self.assertRaises(sqlite3.IntegrityError):
                    self.h.db.execute(statement)

    def test_the_hive_lives_beside_the_project_database(self):
        self.assertEqual(self.h.path, (self.root / '.memory' / 'hive.sqlite').resolve())
        tables = {row[0] for row in self.h.db.execute("SELECT name FROM sqlite_master WHERE type IN ('table')")}
        self.assertTrue({'swarms', 'agents', 'entries', 'bases', 'links', 'entries_fts'} <= tables)
        self.assertNotIn('swarms', {row[0] for row in self.m.db.execute("SELECT name FROM sqlite_master")})

    def test_only_a_manual_swarm_opens_without_the_blind_phase(self):
        with self.assertRaisesRegex(InvalidRecord, 'Only a swarm of kind manual'):
            self.swarm(kind='focus', blind=False)
        opened = hive.open_swarm(self.h, title='Talk', purpose='Discuss the parser.', kind='manual', request_key='manual', blind=False)
        self.assertFalse(opened['blind'])
        self.assertTrue(hive.open_swarm(self.h, title='Talk', purpose='Discuss the parser.', kind='manual', request_key='manual',
                                        blind=False)['duplicate'])


class ReadWithoutHiveTests(unittest.TestCase):
    def test_reads_work_when_the_hive_file_does_not_exist(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            memory = Memory.create(Path(folder) / '.memory' / 'project.sqlite', 'Empty', ['Keep exceptions.'])
            codex_host.initialize(memory)
            try:
                path = path_for(memory)
                with Hive(path, read_only=True) as store:
                    self.assertIsNone(store.db)
                    self.assertEqual(hive.swarms(store), {'swarms': [], 'total': 0, 'max_seq': 0})
                    self.assertEqual(hive.max_seq(store), 0)
                    with self.assertRaisesRegex(InvalidRecord, 'was not found in the hive'):
                        hive.query(store, 'swarm_missing', 'codex-1')
                    with self.assertRaisesRegex(InvalidRecord, 'read only'):
                        hive.open_swarm(store, title='T', purpose='P.', kind='manual', request_key='k')
                listed = mcp.dispatch(memory, 'memory_get', {'view': 'hive'})
                self.assertEqual(listed['swarms'], [])
                with self.assertRaisesRegex(InvalidRecord, 'was not found in the hive'):
                    mcp.dispatch(memory, 'memory_get', {'view': 'hive', 'id': 'swarm_missing', 'hive': {'agent_id': 'codex-1'}})
                self.assertFalse(path.exists())
            finally:
                memory.close()


class BlindPhaseTests(Fixture):
    def test_other_hypotheses_conclusions_and_patterns_stay_hidden_until_the_agent_posts_its_hypothesis(self):
        swarm = self.team('codex-1', 'claude-2')
        verified = self.log(swarm, 'codex-1', 'observation', claim='The parser converts text with int.',
                            bases=[{'kind': 'file', 'value': 'src/parser.py:2'}])
        # A check of the swarm that could not start has no exit code, so its basis stays unverified.
        not_run = self.check_receipt(None, 'check-not-run', swarm)
        unverified = self.log(swarm, 'codex-1', 'observation', memory=self.m, claim='A forum post reports a sign problem.',
                              bases=[{'kind': 'url', 'value': 'https://example.com/forum'}, {'kind': 'command', 'value': not_run}])
        hypothesis = self.log(swarm, 'codex-1', 'hypothesis', claim='The parser drops the sign.', detail='Parse minus one.')
        conclusion = self.log(swarm, 'codex-1', 'conclusion', claim='The parser drops the sign of negative input.',
                              confidence='medium', cites=[verified])
        blind = hive.query(self.h, swarm, 'claude-2')
        seen = {row['id'] for row in blind['entries']}
        self.assertEqual(blind['phase'], 'blind')
        self.assertIn(verified, seen)
        self.assertFalse({unverified, hypothesis, conclusion} & seen)
        self.assertEqual(blind['hidden_by_blind_phase'], 3)
        self.assertNotIn('drops the sign', hive.compose(self.h, swarm, 'claude-2'))
        agent = hive.member(self.h, swarm, 'claude-2')
        self.assertIsNone(agent['revealed_at'])
        # The hidden entries cannot be cited or targeted either.
        self.assertRefused('target', lambda: self.log(swarm, 'claude-2', 'conclusion', claim='The parser is at fault.',
                                                      confidence='low', cites=[conclusion]))
        result = hive.log(self.h, swarm, 'claude-2', move='hypothesis', request_key='reveal',
                          fields={'claim': 'The caller strips the sign.', 'detail': 'Read the caller.'})
        self.assertTrue(result['revealed'])
        self.assertIsNotNone(hive.member(self.h, swarm, 'claude-2')['revealed_at'])
        opened = hive.query(self.h, swarm, 'claude-2')
        self.assertEqual(opened['phase'], 'open')
        self.assertTrue({unverified, hypothesis, conclusion} <= {row['id'] for row in opened['entries']})
        self.assertIn('drops the sign of negative input', hive.compose(self.h, swarm, 'claude-2'))

    def test_a_manual_swarm_without_blind_phase_shows_everything(self):
        swarm = self.team('codex-1', 'claude-2', blind=False, kind='manual')
        hypothesis = self.log(swarm, 'codex-1', 'hypothesis', claim='The parser drops the sign.', detail='Parse minus one.')
        self.assertIn(hypothesis, {row['id'] for row in hive.query(self.h, swarm, 'claude-2')['entries']})
        self.log(swarm, 'claude-2', 'challenge', claim='The parser keeps the sign of the text.', target=hypothesis,
                 bases=[{'kind': 'file', 'value': 'src/parser.py:2'}])

    def test_query_filters_and_pages(self):
        swarm = self.team('codex-1', 'claude-2', blind=False, kind='manual')
        self.log(swarm, 'codex-1', 'question', claim='Which input shows the wrong value?', addressee='agent:claude-2')
        self.log(swarm, 'codex-1', 'observation', claim='The parser converts signed text with int.',
                 bases=[{'kind': 'file', 'value': 'src/parser.py:2'}])
        addressed = hive.query(self.h, swarm, 'claude-2', addressed_to='agent:claude-2')
        self.assertEqual([row['move'] for row in addressed['entries']], ['question'])
        self.assertEqual([row['move'] for row in hive.query(self.h, swarm, 'claude-2', text='signed')['entries']], ['observation'])
        page = hive.query(self.h, swarm, 'claude-2', limit=2)
        self.assertTrue(page['more'])
        rest = hive.query(self.h, swarm, 'claude-2', since_seq=page['next_since_seq'])
        self.assertEqual(len(page['entries']) + len(rest['entries']), 4)
        self.assertEqual(set(page['entries'][0]), {'id', 'seq', 'agent', 'move', 'claim', 'links_in', 'links_out'})
        with self.assertRaises(InvalidRecord):
            hive.query(self.h, swarm, 'claude-2', limit=31)


class ComposeTests(Fixture):
    def build(self):
        swarm = self.team('codex-1', 'claude-2', 'codex-3')
        mine = self.log(swarm, 'codex-1', 'observation', claim='The parser converts text with int.',
                        bases=[{'kind': 'file', 'value': 'src/parser.py:2'}])
        self.log(swarm, 'codex-1', 'hypothesis', claim='The parser drops the sign.', detail='Parse minus one.')
        question = self.log(swarm, 'codex-1', 'question', claim='Which caller passes the text to the parser?', addressee='user')
        self.join(swarm, 'workspace-user', host='workspace-user', role='user')
        self.log(swarm, 'workspace-user', 'answer', claim='The import job passes the text.', target=question)
        for agent in ('claude-2', 'codex-3'):
            self.log(swarm, agent, 'hypothesis', claim=f'The caller of {agent} strips the sign.', detail='Read the caller.')
        self.log(swarm, 'claude-2', 'question', claim='Does the parser trim spaces before int?', addressee='role:worker')
        self.log(swarm, 'claude-2', 'challenge', claim='The int call keeps the minus sign of the text.', target=mine,
                 bases=[{'kind': 'file', 'value': 'src/parser.py:1'}])
        receipt = self.check_receipt(0, 'check-pass', swarm)
        checked = self.log(swarm, 'codex-3', 'observation', memory=self.m, claim='The parser check passed after the change.',
                           bases=[{'kind': 'command', 'value': receipt}])
        self.log(swarm, 'codex-3', 'conclusion', claim='The change of codex-3 fixes the parser.', confidence='high', cites=[checked])
        self.log(swarm, 'claude-2', 'conclusion', claim='The caller strips the sign before parsing.', confidence='low', cites=[mine])
        return swarm

    def test_sections_come_in_the_specified_order(self):
        swarm = self.build()
        text = hive.compose(self.h, swarm, 'codex-1', budget=2000)
        order = ['Answers to questions of this agent:', 'Open questions addressed to this agent or its role:',
                 'Unresolved challenges of entries of this agent:', 'Conclusions confirmed by a check or review:',
                 'Latest conclusions of other agents:']
        positions = [text.index(title) for title in order]
        self.assertEqual(positions, sorted(positions), text)
        self.assertIn('workspace-user answer: The import job passes the text.', text)
        self.assertIn('challenge of e', text)
        self.assertIn('(high confidence): The change of codex-3 fixes the parser.', text)
        self.assertNotIn('Omitted', text)
        self.assertEqual(text, hive.compose(self.h, swarm, 'codex-1', budget=2000))

    def test_the_budget_holds_and_omissions_are_counted(self):
        swarm = self.build()
        for budget in (400, 450, 600, 800):
            with self.subTest(budget=budget):
                value = hive.composition(self.h, swarm, 'codex-1', budget=budget)
                self.assertLessEqual(value['characters'], budget)
                self.assertEqual(value['characters'], len(value['text']))
                included = sum(value['included'].values())
                omitted = sum(value['omitted'].values())
                self.assertEqual(included + omitted, 5)
                if omitted:
                    self.assertRegex(value['text'].splitlines()[-1], rf'^Omitted to fit {budget} characters: \d+ ')
        small = hive.composition(self.h, swarm, 'codex-1', budget=400)
        self.assertGreater(sum(small['omitted'].values()), 0)
        with self.assertRaises(InvalidRecord):
            hive.compose(self.h, swarm, 'codex-1', budget=399)

    def test_a_three_agent_swarm_of_sixty_entries_composes_within_the_default_budget(self):
        swarm = self.team('codex-1', 'claude-2', 'codex-3')
        for agent in ('codex-1', 'claude-2', 'codex-3'):
            self.log(swarm, agent, 'hypothesis', claim=f'The fault seen by {agent} lies in the sign handling.', detail='Test it.')
        entries = 6
        inputs = ['signed', 'spaced', 'zero', 'huge', 'localised', 'unicode', 'prefixed', 'suffixed', 'empty', 'multiline']
        paths = ['strict', 'lenient', 'cached', 'fallback', 'default', 'legacy', 'batch', 'stream', 'retry']
        helpers = ['trim', 'split', 'lower', 'decode', 'encode', 'round', 'clamp', 'guard']
        latest = {}
        for index in range(54):
            agent = ('codex-1', 'claude-2', 'codex-3')[index % 3]
            words = (inputs[index % 10], paths[index % 9], helpers[index % 8])
            step = (index // 3) % 6
            if step == 3:
                self.log(swarm, agent, 'question', claim='Does {} input take the {} branch after {}?'.format(*words), addressee='all')
            elif step == 4:
                self.log(swarm, agent, 'conclusion', claim='The {} input fails in the {} branch because of {}.'.format(*words),
                         confidence='medium', cites=[latest[agent]])
            elif step == 5:
                self.log(swarm, agent, 'checkpoint', done=f'Step {index} is done.', belief='The sign handling is at fault.',
                         open_questions='None.', next_step='Continue with the next input.')
            else:
                latest[agent] = self.log(swarm, agent, 'observation', claim='The {} input reaches the {} branch through {}.'.format(*words),
                                         bases=[{'kind': 'file', 'value': f'src/parser.py:{index % 2 + 1}'}])
            entries += 1
        self.assertEqual(self.h.db.execute('SELECT count(*) FROM entries').fetchone()[0], entries)
        value = hive.composition(self.h, swarm, 'codex-1')
        self.assertEqual(entries, 60)
        self.assertLessEqual(value['characters'], hive.COMPOSE_BUDGET)
        # Six open questions of the other two agents and the latest conclusion of each of them.
        self.assertEqual(sum(value['included'].values()) + sum(value['omitted'].values()), 8)
        self.assertEqual(value['included'], {'questions': 6})
        self.assertEqual(value['omitted'], {'conclusions of other agents': 2})
        self.assertTrue(value['text'].endswith('Omitted to fit 800 characters: 2 conclusions of other agents.'))

    def test_resume_fits_its_limit(self):
        swarm = self.build()
        self.log(swarm, 'codex-1', 'checkpoint', done='d' * 400, belief='b' * 400, open_questions='q' * 400, next_step='n' * 400)
        value = hive.resume(self.h, swarm, 'codex-1')
        self.assertLessEqual(len(dumps(value)), hive.RESUME_LIMIT)
        self.assertEqual(value['characters'], len(dumps(value)))
        self.assertTrue(value['checkpoint']['done'].endswith('...'))
        small = hive.resume(self.h, swarm, 'claude-2')
        self.assertEqual([row['move'] for row in small['addressed']], [])
        self.assertIsNone(small['checkpoint'])


class DistillationTests(Fixture):
    def test_close_confirms_disputes_counts_and_proposes_a_lesson_only_from_a_confirmed_pattern(self):
        swarm = self.team('codex-1', 'claude-2')
        passed = self.check_receipt(0, 'check-pass', swarm)
        failed = self.check_receipt(1, 'check-fail', swarm)
        for agent in ('codex-1', 'claude-2'):
            self.log(swarm, agent, 'hypothesis', claim=f'The fault seen by {agent} is the sign.', detail='Run the check.')
        good = self.log(swarm, 'codex-1', 'observation', memory=self.m, claim='The check passed after the sign fix.',
                        bases=[{'kind': 'command', 'value': passed}])
        bad = self.log(swarm, 'claude-2', 'observation', memory=self.m, claim='The check failed after the caller change.',
                       bases=[{'kind': 'command', 'value': failed}])
        self.assertRefused('basis_command', lambda: self.log(swarm, 'claude-2', 'observation', memory=self.m,
                                                             claim='The check of an unknown run passed.',
                                                             bases=[{'kind': 'command', 'value': 'host_unknown'}]))
        confirmed = self.log(swarm, 'codex-1', 'conclusion', claim='The sign fix repairs the parser.', confidence='high', cites=[good])
        plain = self.log(swarm, 'claude-2', 'conclusion', claim='The caller change does not repair the parser.', confidence='medium',
                         cites=[bad])
        disputed = self.log(swarm, 'codex-1', 'conclusion', claim='The caller is irrelevant to the fault.', confidence='low', cites=[good])
        self.log(swarm, 'claude-2', 'challenge', claim='The caller strips spaces that the parser needs.', target=disputed,
                 bases=[{'kind': 'file', 'value': 'src/app.py:1'}])
        answered = self.log(swarm, 'claude-2', 'challenge', claim='The sign fix may break positive input.', target=confirmed,
                            bases=[{'kind': 'file', 'value': 'src/parser.py:1'}])
        self.log(swarm, 'codex-1', 'observation', claim='The positive input keeps its value after the fix.', reply_to=answered,
                 bases=[{'kind': 'file', 'value': 'src/parser.py:2'}])
        pattern = self.log(swarm, 'claude-2', 'pattern', claim='Sign handling belongs inside the parser, not its callers.',
                           detail='Both attempts point there.', cites=[confirmed, plain])
        weak = self.log(swarm, 'codex-1', 'pattern', claim='Callers of parsers rarely matter for sign faults.', cites=[plain, bad])
        hive.record_composition(self.h, swarm, 'codex-1', 640, run_id='run_a')
        result = hive.close(self.h, swarm, summary='The sign fix passed its check.', request_key='close', memory=self.m)
        self.assertEqual(set(result['confirmed']), {confirmed, disputed})
        self.assertIn('exited with code 0', result['confirmed'][confirmed])
        self.assertEqual(set(result['disputed']), {disputed})
        self.assertEqual(result['citations'][good], 2)
        self.assertEqual(result['citations'][plain], 2)
        self.assertEqual(result['pattern_candidates'], [pattern])
        self.assertNotIn(weak, [item['entry_id'] for item in result['proposals']])
        self.assertEqual(result['composed'], {'prompts': 1, 'characters': 640})
        self.assertEqual(len(result['proposals']), 1)
        lesson = self.m.read(result['proposals'][0]['lesson_id'], detail=True)
        self.assertEqual((lesson['kind'], lesson['actor']), ('lesson', hive.HIVE_ACTOR))
        self.assertEqual(lesson['payload']['do'], 'Sign handling belongs inside the parser, not its callers.')
        source = self.m.read(result['proposals'][0]['source_id'], detail=True)
        self.assertIn(pattern, source['body'])
        self.assertIn(confirmed, source['body'])
        self.assertIsNone(self.m._lesson_review(lesson['id']), 'The lesson stays proposed until the user reviews it.')
        note = self.m.read(result['summary_record'], detail=True)
        self.assertEqual(note['kind'], 'note')
        self.assertIn('2 of 3 conclusions were confirmed and 1 were disputed', note['payload']['text'])
        self.assertTrue(hive.close(self.h, swarm, summary='The sign fix passed its check.', request_key='close', memory=self.m)['duplicate'])
        with self.assertRaises(Conflict):
            hive.close(self.h, swarm, summary='Again.', request_key='close-again', memory=self.m)
        with self.assertRaisesRegex(InvalidRecord, 'is closed'):
            self.log(swarm, 'codex-1', 'checkpoint', done='d', belief='b', open_questions='q', next_step='n')
        self.assertEqual({row[0] for row in self.h.db.execute("SELECT mark FROM marks")}, {'confirmed', 'disputed'})

    def test_an_accepted_pattern_reaches_a_later_swarm(self):
        swarm = self.team('codex-1', 'claude-2')
        passed = self.check_receipt(0, 'check-pass', swarm)
        for agent in ('codex-1', 'claude-2'):
            self.log(swarm, agent, 'hypothesis', claim=f'The fault seen by {agent} is the sign.', detail='Run the check.')
        good = self.log(swarm, 'codex-1', 'observation', memory=self.m, claim='The check passed after the sign fix.',
                        bases=[{'kind': 'command', 'value': passed}])
        confirmed = self.log(swarm, 'codex-1', 'conclusion', claim='The sign fix repairs the parser.', confidence='high', cites=[good])
        other = self.log(swarm, 'claude-2', 'observation', claim='The caller passes the text unchanged.',
                         bases=[{'kind': 'file', 'value': 'src/app.py:1'}])
        self.log(swarm, 'claude-2', 'pattern', claim='Signed parser faults sit inside the parser itself.', cites=[confirmed, other])
        result = hive.close(self.h, swarm, summary='Closed.', request_key='close', memory=self.m)
        lesson_id = result['proposals'][0]['lesson_id']
        later = self.swarm(purpose='Find why the parser mishandles signed values in the import job.')
        self.join(later, 'codex-9')
        self.orient(later, 'codex-9')
        self.assertNotIn('Accepted patterns', hive.compose(self.h, later, 'codex-9', memory=self.m))
        episode = self.m.episode(self.episode)
        self.m.record(self.episode, 'lesson_review', {'lesson_id': lesson_id, 'status': 'accepted', 'reason': 'The user agrees.'},
                      expected_version=episode['version'], request_key='accept', actor='workspace-user',
                      evidence=[{'source_id': self.source, 'reason': 'The user reviewed the lesson.'}],
                      links=[{'event_id': lesson_id, 'reason': 'The lesson under review.'}])
        text = hive.compose(self.h, later, 'codex-9', memory=self.m)
        self.assertIn('Accepted patterns from earlier swarms:', text)
        self.assertIn('Signed parser faults sit inside the parser itself.', text)

    def test_check_completion_names_each_problem(self):
        swarm = self.team('codex-1', 'claude-2')
        observation = self.log(swarm, 'codex-1', 'observation', claim='The parser converts text with int.',
                               bases=[{'kind': 'file', 'value': 'src/parser.py:2'}])
        conclusion = self.log(swarm, 'codex-1', 'conclusion', claim='The parser drops the sign.', confidence='low', cites=[observation])
        checkpoint = self.log(swarm, 'codex-1', 'checkpoint', done='Read.', belief='Sign.', open_questions='None.', next_step='Fix.')
        self.assertEqual(hive.check_completion(self.h, swarm, 'codex-1', conclusion_id=conclusion, checkpoint_id=checkpoint),
                         {'complete': True, 'problems': []})
        result = hive.check_completion(self.h, swarm, 'claude-2', conclusion_id=checkpoint, checkpoint_id='e999')
        self.assertFalse(result['complete'])
        self.assertEqual(len(result['problems']), 2)
        self.assertIn('belongs to another agent or swarm', result['problems'][0])
        wrong = hive.check_completion(self.h, swarm, 'codex-1', conclusion_id=checkpoint, checkpoint_id=conclusion)
        self.assertIn('is a checkpoint, not a conclusion', wrong['problems'][0])


class PurgeTests(Fixture):
    def test_purge_is_a_user_action_that_removes_only_closed_swarms(self):
        closed = self.team('codex-1')
        self.log(closed, 'codex-1', 'hypothesis', claim='The parser drops the sign.', detail='Parse minus one.')
        still_open = self.team('claude-2')
        with self.assertRaisesRegex(InvalidRecord, 'Only the user can purge'):
            hive.purge(self.h, self.m, closed_before_days=0, actor='assistant')
        hive.close(self.h, closed, summary='Done.', request_key='close')
        self.clock[0] = '2026-09-18T10:00:00+00:00'
        kept = hive.purge(self.h, self.m, closed_before_days=2, actor='workspace-user')
        self.assertEqual(kept['swarms'], 0)
        removed = hive.purge(self.h, self.m, closed_before_days=1, actor='workspace-user')
        self.assertEqual((removed['swarms'], removed['agents'], removed['entries']), (1, 1, 2))
        self.assertEqual([row['id'] for row in hive.swarms(self.h)['swarms']], [still_open])
        self.assertEqual(self.h.db.execute('SELECT count(*) FROM entries_fts').fetchone()[0], 1)
        receipt = codex_host.read_receipt(self.m, removed['receipt_id'])
        self.assertEqual(receipt['event_name'], 'HivePurged')
        self.assertEqual(set(receipt['payload']), {'swarms', 'agents', 'entries', 'closed_before_days', 'purged_at'})
        self.assertEqual(hive.max_seq(self.h), 3, 'The sequence never goes back, so identifiers are never reused.')

    def test_the_purge_command_runs_as_the_user(self):
        self.clock[0] = '2026-01-01T10:00:00+00:00'
        swarm = self.team('codex-1')
        hive.close(self.h, swarm, summary='Done.', request_key='close')
        self.h.close()
        output = io.StringIO()
        with patch('sys.stdout', output):
            code = cli_main(['hive', 'purge', '--closed-before', '0', '--db', str(self.m.path), '--project', str(self.root)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())['swarms'], 1)
        self.h = Hive(path_for(self.m))


class SessionToolTests(Fixture):
    def call(self, name, arguments):
        return mcp.dispatch(self.m, name, arguments)

    def write(self, key, session='session-one', **data):
        return self.call('memory_write', {'operation': 'hive', 'request_key': key, 'data': data, 'session_id': session})

    def read(self, swarm, session='session-one', **options):
        return self.call('memory_get', {'view': 'hive', 'id': swarm, 'hive': options, 'session_id': session})

    def test_sessions_open_join_log_read_and_close_through_the_main_tools(self):
        swarm = self.write('open', action='open', title='Parser', purpose='Find the parser fault.', kind='workflow',
                           episode_id=self.episode)['id']
        self.assertEqual(self.write('open', action='open', title='Parser', purpose='Find the parser fault.', kind='workflow',
                                    episode_id=self.episode)['id'], swarm)
        self.write('join', action='join', swarm_id=swarm, agent_id='session-a', role='lead')
        entry = self.write('orient', action='log', swarm_id=swarm, agent_id='session-a', move='orient',
                           fields={'claim': 'The goal is the parser fault.', 'bases': [{'kind': 'source', 'value': self.source}]})
        self.assertEqual(entry['move'], 'orient')
        verified = self.h.db.execute('SELECT verified FROM bases WHERE entry_id=?', (entry['id'],)).fetchone()[0]
        self.assertEqual(verified, 1)
        listed = self.call('memory_get', {'view': 'hive'})
        self.assertEqual(listed['swarms'][0]['id'], swarm)
        rows = self.read(swarm, agent_id='session-a', moves=['orient'])
        self.assertEqual([row['id'] for row in rows['entries']], [entry['id']])
        resumed = self.read(swarm, agent_id='session-a', action='resume')
        self.assertEqual(resumed['agent_id'], 'session-a')
        closed = self.write('close', action='close', swarm_id=swarm, summary='The session closed the swarm.')
        self.assertEqual(closed['main_memory'], 'recorded')
        self.assertIn('hive', mcp.schema('hive')['operation'])

    def test_the_main_tools_refuse_reserved_names_workers_and_unknown_fields(self):
        swarm = self.swarm()
        self.join(swarm, 'codex-1')
        for name in ('workspace-user', 'User', 'focus_orchestrator', 'hive-distiller'):
            with self.subTest(name=name):
                with self.assertRaisesRegex(InvalidRecord, 'is reserved'):
                    self.write('join-' + name, action='join', swarm_id=swarm, agent_id=name, role='lead')
        with self.assertRaisesRegex(InvalidRecord, 'is not the agent that this session joined'):
            self.write('as-worker', action='log', swarm_id=swarm, agent_id='codex-1', move='orient',
                       fields={'claim': 'The goal is the parser.', 'bases': [{'kind': 'file', 'value': 'src/parser.py:1'}]})
        with self.assertRaisesRegex(InvalidRecord, 'Invalid tool arguments'):
            self.write('bad', action='log', swarm_id=swarm, agent_id='codex-1', move='guess', fields={})
        with self.assertRaisesRegex(InvalidRecord, 'needs swarm_id'):
            self.write('missing', action='close', summary='Closed.')
        with self.assertRaisesRegex(InvalidRecord, 'Invalid tool arguments'):
            self.call('memory_get', {'view': 'hive', 'id': swarm, 'hive': {'agent': 'codex-1'}})

    def test_a_refusal_over_mcp_keeps_the_error_shape(self):
        swarm = self.write('open', action='open', title='Parser', purpose='Find the parser fault.', kind='workflow')['id']
        self.write('join', action='join', swarm_id=swarm, agent_id='session-a', role='lead')
        messages = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-11-25'}},
                    {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {'name': 'memory_write', 'arguments': {
                        'operation': 'hive', 'request_key': 'early', 'session_id': 'session-one', 'data': {'action': 'log', 'swarm_id': swarm, 'agent_id': 'session-a',
                                                                              'move': 'observation', 'fields': {
                                                                                  'claim': 'The parser converts text.',
                                                                                  'bases': [{'kind': 'file', 'value': 'src/parser.py:2'}]}}}}}]
        output = io.StringIO()
        mcp.serve(self.m, io.StringIO('\n'.join(dumps(message) for message in messages)), output)
        reply = json.loads(output.getvalue().splitlines()[1])['result']
        self.assertTrue(reply['isError'])
        body = json.loads(reply['content'][0]['text'])
        self.assertEqual((body['rule'], body['execution'], body['next_step']['action']), ('orient_first', 'not_started', 'correct_entry'))

    def blind_team(self):
        """A workflow swarm where bob, a session, posted a hypothesis and a conclusion, and alice, another session, is still blind."""
        swarm = self.write('open', action='open', title='Parser', purpose='Find the parser fault.', kind='workflow', episode_id=self.episode)['id']
        for session, agent in (('session-alice', 'alice'), ('session-bob', 'bob')):
            self.write('join-' + agent, session, action='join', swarm_id=swarm, agent_id=agent, role='lead')
            self.write('orient-' + agent, session, action='log', swarm_id=swarm, agent_id=agent, move='orient',
                       fields={'claim': f'The goal of {agent} is the parser fault.', 'bases': [{'kind': 'file', 'value': 'src/parser.py:1'}]})
        seen = self.write('seen-bob', 'session-bob', action='log', swarm_id=swarm, agent_id='bob', move='observation',
                          fields={'claim': 'The parser calls int on the raw text.', 'bases': [{'kind': 'file', 'value': 'src/parser.py:2'}]})
        self.write('hypothesis-bob', 'session-bob', action='log', swarm_id=swarm, agent_id='bob', move='hypothesis',
                   fields={'claim': 'The parser ignores the minus sign in signed input.', 'detail': 'Parse minus one.'})
        self.write('conclusion-bob', 'session-bob', action='log', swarm_id=swarm, agent_id='bob', move='conclusion',
                   fields={'claim': 'The sign handling of the parser is at fault.', 'confidence': 'medium', 'cites': [seen['id']]})
        return swarm

    def test_a_blind_session_cannot_read_or_log_as_another_agent(self):
        swarm = self.blind_team()
        blind = self.read(swarm, 'session-alice', agent_id='alice')
        self.assertEqual((blind['phase'], blind['hidden_by_blind_phase']), ('blind', 2))
        for options in ({'agent_id': 'bob'}, {'agent_id': 'bob', 'action': 'resume'}):
            with self.subTest(options=options):
                with self.assertRaisesRegex(InvalidRecord, 'is not the agent that this session joined'):
                    self.read(swarm, 'session-alice', **options)
        with self.assertRaisesRegex(InvalidRecord, 'Pass session_id'):
            self.call('memory_get', {'view': 'hive', 'id': swarm, 'hive': {'agent_id': 'bob'}})
        with self.assertRaisesRegex(InvalidRecord, 'is not the agent that this session joined'):
            self.write('as-bob', 'session-alice', action='log', swarm_id=swarm, agent_id='bob', move='checkpoint',
                       fields={'done': 'Forged.', 'belief': 'Forged.', 'open_questions': 'None.', 'next_step': 'Wait.'})
        self.assertEqual(self.h.db.execute("SELECT count(*) FROM entries WHERE agent_id='bob' AND move='checkpoint'").fetchone()[0], 0)

    def test_a_session_joins_a_swarm_as_one_agent_only(self):
        swarm = self.blind_team()
        with self.assertRaisesRegex(Conflict, 'already joined the swarm .* as the agent alice'):
            self.write('join-scout', 'session-alice', action='join', swarm_id=swarm, agent_id='alice-scout', role='scout')
        with self.assertRaisesRegex(InvalidRecord, 'Pass session_id'):
            self.call('memory_write', {'operation': 'hive', 'request_key': 'join-nameless',
                                       'data': {'action': 'join', 'swarm_id': swarm, 'agent_id': 'nameless', 'role': 'lead'}})
        # Joining again with the same session and name returns the membership, and another session cannot take the name.
        self.assertTrue(self.write('join-alice-again', 'session-alice', action='join', swarm_id=swarm, agent_id='alice', role='lead')['duplicate'])
        with self.assertRaises(Conflict):
            self.write('join-alice-other', 'session-other', action='join', swarm_id=swarm, agent_id='alice', role='lead')
        self.assertEqual({row[0] for row in self.h.db.execute('SELECT agent_id FROM agents WHERE swarm_id=?', (swarm,))}, {'alice', 'bob'})

    def test_memory_context_adds_the_hive_context_of_a_session_that_joined_a_swarm(self):
        swarm = self.blind_team()
        arguments = {'query': 'parser sign hypothesis', 'subject': self.m.episode(self.episode)['subject'], 'episode_id': self.episode,
                     'max_chars': 6000}
        plain = self.call('memory_context', arguments)
        self.assertNotIn('hive', plain)
        joined = self.call('memory_context', {**arguments, 'session_id': 'session-bob'})
        [part] = joined['hive']
        self.assertEqual((part['swarm_id'], part['agent_id']), (swarm, 'bob'))
        self.assertIn(f'Hive context of agent bob in swarm {swarm}, open phase.', part['text'])
        self.assertLessEqual(mcp.size(joined), 6000)
        self.assertNotIn('hive', self.call('memory_context', {**arguments, 'session_id': 'session-nobody'}))
        self.write('close', action='close', swarm_id=swarm, summary='The sessions closed the swarm.')
        self.assertNotIn('hive', self.call('memory_context', {**arguments, 'session_id': 'session-bob'}))

    def test_the_worker_protocol_section_fits_its_limit(self):
        self.assertLessEqual(len(hive.WORKER_PROTOCOL), hive.PROTOCOL_LIMIT)
        for move in ('orient', 'hypothesis', 'observation', 'conclusion', 'checkpoint'):
            self.assertIn(move, hive.WORKER_PROTOCOL)

    def test_the_main_tool_list_stays_within_its_limit_with_the_hive(self):
        listed = {'tools': mcp.TOOLS}
        self.assertLess(len(dumps(listed)), MAIN_TOOLS_LIST_LIMIT)
        self.assertEqual([tool['name'] for tool in mcp.TOOLS], ['memory_context', 'memory_get', 'memory_write'])
        self.assertIn('hive', mcp.VIEWS)
        self.assertIn('hive', mcp.OPERATIONS)


class RestrictedServerTests(Fixture):
    def converse(self, calls, swarm, agent='codex-1', role='worker'):
        messages = [{'jsonrpc': '2.0', 'id': 0, 'method': 'initialize', 'params': {'protocolVersion': '2025-11-25'}},
                    {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}]
        messages += [{'jsonrpc': '2.0', 'id': index, 'method': 'tools/call', 'params': {'name': name, 'arguments': arguments}}
                     for index, (name, arguments) in enumerate(calls, 2)]
        output = io.StringIO()
        mcp.serve_hive(self.h.path, swarm, agent, role, io.StringIO('\n'.join(dumps(message) for message in messages)), output,
                       db=self.m.path)
        replies = [json.loads(line) for line in output.getvalue().splitlines()]
        return replies[1]['result']['tools'], [(reply['result']['isError'], json.loads(reply['result']['content'][0]['text']))
                                              for reply in replies[2:]]

    def test_the_worker_server_exposes_three_tools_bound_to_one_swarm_and_agent(self):
        swarm = self.swarm()
        other = self.swarm()
        self.join(swarm, 'codex-1')
        self.join(swarm, 'claude-2', host='claude')
        orient = {'move': 'orient', 'request_key': 'o', 'fields': {'claim': 'The goal is the parser fault.',
                                                                   'bases': [{'kind': 'file', 'value': 'src/parser.py:1'}]}}
        tools, results = self.converse([
            ('hive_log', {**orient, 'swarm_id': other}),
            ('hive_log', {**orient, 'agent_id': 'claude-2'}),
            ('hive_log', {**orient, 'swarm_id': swarm, 'agent_id': 'codex-1'}),
            ('hive_query', {'swarm_id': other}),
            ('hive_resume', {'agent_id': 'claude-2'}),
            ('hive_query', {}),
            ('hive_resume', {}),
            ('memory_get', {'view': 'hive'}),
        ], swarm)
        self.assertEqual([tool['name'] for tool in tools], ['hive_log', 'hive_query', 'hive_resume'])
        for index in (0, 1, 3, 4):
            with self.subTest(call=index):
                error, body = results[index]
                self.assertTrue(error)
                self.assertIn('This server is bound to the swarm ' + swarm + ' and the agent codex-1.', body['message'])
                self.assertEqual(body['execution'], 'not_started')
        self.assertEqual(results[2], (False, {'id': 'e1', 'seq': 1, 'move': 'orient', 'duplicate': False, 'revealed': False}))
        self.assertEqual([row['id'] for row in results[5][1]['entries']], ['e1'])
        self.assertEqual(results[6][1]['agent_id'], 'codex-1')
        self.assertTrue(results[7][0])
        self.assertIn('only hive_log, hive_query and hive_resume', results[7][1]['message'])
        self.assertEqual(self.h.db.execute("SELECT count(*) FROM entries WHERE agent_id='claude-2'").fetchone()[0], 0)
        _, mismatch = self.converse([('hive_resume', {})], swarm, role='reviewer')
        self.assertIn('with the role worker, not reviewer', mismatch[0][1]['message'])
        _, unjoined = self.converse([('hive_resume', {})], swarm, agent='codex-9')
        self.assertIn('has not joined the swarm', unjoined[0][1]['message'])

    def test_the_command_line_starts_the_restricted_server(self):
        swarm = self.swarm()
        self.join(swarm, 'codex-1')
        messages = [{'jsonrpc': '2.0', 'id': 0, 'method': 'initialize', 'params': {'protocolVersion': '2025-11-25'}},
                    {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'},
                    {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {'name': 'hive_log', 'arguments': {
                        'move': 'orient', 'request_key': 'o',
                        'fields': {'claim': 'The goal is the parser fault.', 'bases': [{'kind': 'source', 'value': self.source}]}}}}]
        process = subprocess.run([sys.executable, '-m', 'memory_module.cli', 'hive-serve', '--hive', str(self.h.path),
                                  '--swarm', swarm, '--agent', 'codex-1', '--role', 'worker'],
                                 input='\n'.join(dumps(message) for message in messages) + '\n', text=True, capture_output=True,
                                 cwd=ROOT, timeout=60)
        self.assertEqual(process.returncode, 0, process.stderr)
        replies = [json.loads(line) for line in process.stdout.splitlines()]
        self.assertEqual(replies[0]['result']['serverInfo']['name'], 'project-memory-hive')
        self.assertEqual(len(replies[1]['result']['tools']), 3)
        self.assertFalse(replies[2]['result']['isError'], replies[2])
        # The default database beside the hive verified the source basis.
        self.assertEqual(self.h.db.execute("SELECT verified FROM bases WHERE entry_id='e1'").fetchone()[0], 1)


HIVE_WORKER_PREFIX = r'''
import json as _json, subprocess as _subprocess, sys as _sys
_spec = _json.loads(_sys.argv[1])
_hive = _spec.get('hive')
if _hive:
    _calls = [
        ('orient', {'claim': 'The goal is a value of two.', 'bases': [{'kind': 'file', 'value': 'src/app.py:1'}]}),
        ('observation', {'claim': 'The application value is one today.', 'bases': [{'kind': 'file', 'value': 'src/app.py:1'}]}),
        ('hypothesis', {'claim': 'Setting the constant to two meets the criterion.', 'detail': 'Change the constant and read it back.'}),
        ('conclusion', {'claim': 'The constant change meets the criterion.', 'confidence': 'high', 'cites': ['OBSERVATION']}),
        ('checkpoint', {'done': 'Changed the constant.', 'belief': 'The value is two.', 'open_questions': 'None.', 'next_step': 'Report.'}),
    ]
    _server = _subprocess.Popen([_sys.executable, '-m', 'memory_module.cli', 'hive-serve', '--hive', _hive['path'], '--swarm',
                                 _hive['swarm'], '--agent', _hive['agent'], '--role', 'worker', '--db', _hive['db']],
                                stdin=_subprocess.PIPE, stdout=_subprocess.PIPE, text=True, cwd=_spec['root'])
    def _send(message):
        _server.stdin.write(_json.dumps(message) + '\n')
        _server.stdin.flush()
        return _json.loads(_server.stdout.readline())
    _send({'jsonrpc': '2.0', 'id': 0, 'method': 'initialize', 'params': {'protocolVersion': '2025-11-25'}})
    _ids = {}
    for _index, (_move, _fields) in enumerate(_calls, 1):
        if 'cites' in _fields:
            _fields['cites'] = [_ids['observation']]
        _reply = _send({'jsonrpc': '2.0', 'id': _index, 'method': 'tools/call',
                        'params': {'name': 'hive_log', 'arguments': {'move': _move, 'request_key': _move, 'fields': _fields}}})
        _ids[_move] = _json.loads(_reply['result']['content'][0]['text'])['id']
    _server.stdin.close()
    _server.wait(timeout=30)
    # The work report gains hive_conclusion_id and hive_checkpoint_id in section 12.6. Until then the ids travel in notes.
    _spec['report']['notes'] = _json.dumps({'hive_conclusion_id': _ids['conclusion'], 'hive_checkpoint_id': _ids['checkpoint']})
    _sys.argv[1] = _json.dumps(_spec)
'''


class FakeWorkerTests(unittest.TestCase):
    """A fake delegated worker logs through the restricted server from inside its worktree.

    The fixture of tests/test_delegation.py provides the git project, the fake hosts and the synchronous launch.
    """

    setUp = test_delegation.DelegationTests.setUp
    tearDown = test_delegation.DelegationTests.tearDown
    git = test_delegation.DelegationTests.git
    plan = test_delegation.DelegationTests.plan
    fake_reviewer = test_delegation.DelegationTests.fake_reviewer
    run_now = test_delegation.DelegationTests.run_now

    def fake_worker(self, host, worktree, folder, prompt):
        spec = self.workers[host]
        return [sys.executable, '-c', HIVE_WORKER_PREFIX + test_delegation.WORKER, json.dumps(spec), str(folder), host]

    def test_a_worker_logs_outside_its_worktree_and_a_passing_review_confirms_its_conclusion(self):
        path = path_for(self.m)
        with Hive(path) as store:
            swarm = hive.open_swarm(store, title='Value', purpose='Set the value to two.', kind='workflow', request_key='open',
                                    episode_id=self.episode, project=self.project)['id']
        run = delegation.request_work(self.m, self.episode, request_key='delegate', host='codex')
        worktree = self.project / run['workspace']
        with Hive(path) as store:
            hive.join(store, swarm, agent_id='codex-1', role='worker', host='codex', run_id=run['id'], worktree=worktree)
        self.workers['codex'] = {'write': {'src/app.py': 'VALUE = 2\n'}, 'report': test_delegation.work_report(), 'root': str(ROOT),
                                 'hive': {'path': str(path), 'swarm': swarm, 'agent': 'codex-1', 'db': str(self.m.path)}}
        delegation.launch(self.m, run)
        work = reviews.read(self.m, run['id'])
        self.assertEqual(work['state'], 'completed', work['error'])
        self.assertFalse(str(path).startswith(str(worktree)))
        ids = json.loads(work['report']['notes'])
        with Hive(path) as store:
            moves = [row[0] for row in store.db.execute("SELECT move FROM entries WHERE agent_id='codex-1' ORDER BY seq")]
            self.assertEqual(moves, ['orient', 'observation', 'hypothesis', 'conclusion', 'checkpoint'])
            completion = hive.check_completion(store, swarm, 'codex-1', conclusion_id=ids['hive_conclusion_id'],
                                               checkpoint_id=ids['hive_checkpoint_id'])
            self.assertTrue(completion['complete'], completion)
            result = hive.close(store, swarm, summary='The worker finished.', request_key='close', memory=self.m)
        self.assertEqual(list(result['confirmed']), [ids['hive_conclusion_id']])
        self.assertIn('passed cross review', result['confirmed'][ids['hive_conclusion_id']])


if __name__ == '__main__':
    unittest.main()
