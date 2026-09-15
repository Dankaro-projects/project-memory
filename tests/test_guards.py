"""Lesson guards, decision acknowledgement and file scope enforcement against real SQLite history."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from memory_module import Memory, InvalidRecord
from memory_module import codex_host, guards, planning
from memory_module.mcp import dispatch

REPOSITORY = Path(__file__).resolve().parent.parent


class PatternTests(unittest.TestCase):
    def test_pattern_semantics(self):
        cases = [
            ('src/app.py', ['src'], True),
            ('src', ['src'], True),
            ('srcx/app.py', ['src'], False),
            ('./src/app.py', ['src/'], True),
            ('src/a.py', ['src/*.py'], True),
            ('src/lib/a.py', ['src/*.py'], False),
            ('src/a.py', ['src/**/*.py'], True),
            ('src/lib/deep/a.py', ['src/**/*.py'], True),
            ('a.py', ['**/*.py'], True),
            ('src/x/y', ['src/**'], True),
            ('a1.py', ['a?.py'], True),
            ('a12.py', ['a?.py'], False),
            ('a/b.py', ['a?b.py'], False),
            ('anything/here.txt', ['.'], True),
            ('/etc/passwd', ['.'], False),
            ('/etc/passwd', ['src'], False),
            ('/etc/passwd', ['/etc'], True),
            ('docs/[x].md', ['docs/[x].md'], True),
        ]
        for path, patterns, expected in cases:
            with self.subTest(path=path, patterns=patterns):
                self.assertEqual(guards.match_path(path, patterns), expected)

    def test_absolute_patterns_and_paths_inside_the_root(self):
        self.assertTrue(guards.match_path('src/a.py', ['/project/src'], root='/project'))
        self.assertTrue(guards.match_path('/project/src/a.py', ['src/*.py'], root='/project'))
        self.assertFalse(guards.match_path('/other/src/a.py', ['src'], root='/project'))

    def test_relative_globs_never_match_paths_outside_the_root(self):
        for path in ['/etc/x.json', '/home/user/.claude/settings.json', '/etc/passwd']:
            for patterns in (['**/*.json'], ['**'], ['*'], ['**/passwd'], ['.']):
                with self.subTest(path=path, patterns=patterns):
                    self.assertFalse(guards.match_path(path, patterns, root='/project'))
                    self.assertFalse(guards.match_path(path, patterns))
        self.assertTrue(guards.match_path('/project/config/x.json', ['**/*.json'], root='/project'))
        self.assertTrue(guards.match_path('/etc/x.json', ['/etc/**'], root='/project'))
        self.assertFalse(guards.match_path('etc/x.json', ['/etc/**'], root='/project'))

    def test_pattern_validation(self):
        guards.validate_patterns(['src', '/abs/path', 'docs/**/*.md'])
        for bad in [[], ['../outside'], ['src/../x'], ['x' * 501], ['a\\b'], ['src', 'src'], 'src', ['p%d' % i for i in range(101)]]:
            with self.subTest(bad=str(bad)[:40]), self.assertRaises(InvalidRecord):
                guards.validate_patterns(bad)

    def test_overlap(self):
        cases = [
            ('src', 'src/lib/*.py', True),
            ('src/lib/*.py', 'src', True),
            ('src/*.py', 'docs', False),
            ('src/foo*', 'src/foobar.py', True),
            ('docs/**', 'src/a.py', False),
            ('/abs/x', 'rel/x', False),
            ('src/parser', 'src/parser/**', True),
            ('src/parser', 'src/parsers', False),
        ]
        for a, b, expected in cases:
            with self.subTest(a=a, b=b):
                self.assertEqual(guards.patterns_overlap(a, b), expected)

    def test_edit_targets(self):
        self.assertEqual(guards.edit_targets('Edit', {'file_path': '/p/a.py', 'old_string': 'x', 'new_string': 'y'}), ['/p/a.py'])
        self.assertEqual(guards.edit_targets('MultiEdit', {'file_path': 'a.py', 'edits': []}), ['a.py'])
        self.assertEqual(guards.edit_targets('NotebookEdit', {'notebook_path': 'n.ipynb'}), ['n.ipynb'])
        self.assertEqual(guards.edit_targets('Read', {'file_path': '/p/a.py'}), [])
        self.assertEqual(guards.edit_targets('Grep', {'path': 'src'}), [])
        patch = '*** Begin Patch\n*** Update File: a.py\n*** Move to: b.py\n@@\n-x\n+y\n*** Add File: c.py\n+z\n*** Delete File: d.py\n*** End Patch'
        self.assertEqual(guards.edit_targets('apply_patch', {'input': patch}), ['a.py', 'b.py', 'c.py', 'd.py'])
        heredoc = "apply_patch <<'EOF'\n" + patch + '\nEOF'
        self.assertEqual(guards.edit_targets('Bash', {'command': heredoc}), ['a.py', 'b.py', 'c.py', 'd.py'])
        self.assertEqual(guards.edit_targets('exec_command', {'cmd': ['bash', '-lc', heredoc]}), ['a.py', 'b.py', 'c.py', 'd.py'])
        self.assertEqual(guards.edit_targets('Bash', {'command': 'echo text > out.txt'}), [])

    def test_patch_markers_with_escaped_line_breaks_and_quotes(self):
        printf = "printf '*** Begin Patch\\n*** Update File: secrets.py\\n@@\\n-a\\n+b\\n*** End Patch\\n' | apply_patch"
        self.assertEqual(guards.edit_targets('Bash', {'command': printf}), ['secrets.py'])
        echo = 'echo "*** Add File: notes/new file.md" | apply_patch'
        self.assertEqual(guards.edit_targets('exec_command', {'cmd': ['bash', '-lc', echo]}), ['notes/new file.md'])
        self.assertEqual(guards.edit_targets('Bash', {'command': "grep '*** Update File: ' log.txt"}), [])
        # A patch or file content that adds marker examples is not misread as editing those files.
        body = "*** Begin Patch\n*** Add File: tests/t.py\n+sample = '*** Begin Patch\\n*** Update File: a.py\\n'\n*** End Patch"
        self.assertEqual(guards.edit_targets('apply_patch', {'input': body}), ['tests/t.py'])
        self.assertEqual(guards.edit_targets('Bash', {'command': "apply_patch <<'EOF'\n" + body + '\nEOF'}), ['tests/t.py'])
        content = "sample = '*** Begin Patch\\n*** Update File: a.py\\n'\n"
        self.assertEqual(guards.edit_targets('Write', {'file_path': 'tests/t.py', 'content': content}), ['tests/t.py'])

    def test_mcp_file_writing_tools_are_edit_tools(self):
        self.assertEqual(guards.edit_targets('mcp__filesystem__write_file', {'path': '/p/secrets.py', 'content': 'x'}), ['/p/secrets.py'])
        self.assertEqual(guards.edit_targets('mcp__filesystem__edit_file', {'path': 'a.py', 'edits': []}), ['a.py'])
        self.assertEqual(guards.edit_targets('mcp__files__create_file', {'file_path': 'b.py'}), ['b.py'])
        self.assertEqual(guards.edit_targets('mcp__filesystem__move_file', {'source': 'a.py', 'destination': 'b.py'}), ['a.py', 'b.py'])
        self.assertEqual(guards.edit_targets('mcp__filesystem__read_file', {'path': 'a.py'}), [])
        self.assertEqual(guards.edit_targets('move_file', {'source': 'a.py', 'destination': 'b.py'}), [])


class Fixture(unittest.TestCase):
    """Shared history helpers. This class defines no tests of its own."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.m = Memory.create(self.root / '.memory' / 'memory.sqlite', 'Guards', ['Preserve recorded scope.'])
        codex_host.initialize(self.m)
        source = self.m.source('user-scope', 'Scope', 'User instruction', 'Change only the parser.', 'user')
        self.evidence = [{'source_id': source['id'], 'reason': 'The user defines the scope.'}]
        self.counter = 0
        self.lessons = self.m.start('Lessons', 'Collect parser lessons.', 'learning', 'Lessons are reviewed.', subject='code')['id']

    def tearDown(self):
        self.m.close()
        self.temp.cleanup()

    def key(self):
        self.counter += 1
        return 'key-' + str(self.counter)

    def version(self, episode_id):
        return self.m.episode(episode_id)['version']

    def work(self, paths=None, state='in_progress', session='session', objective='Repair the parser.', actor='assistant'):
        payload = {'state': state, 'next_action': 'Edit the parser.', 'scope': 'Change the parser only.',
                   'autonomy': 'act', 'reason': 'The user requested the repair.'}
        if paths is not None:
            payload['paths'] = paths
        result = planning.save(self.m, 'work_plan', payload=payload, actor=actor, evidence=self.evidence,
                               title='Parser work', objective=objective, criterion='The parser fixture passes.',
                               subject='code', request_key=self.key(), session_id=session)
        return result['episode_id']

    def revise_plan(self, episode_id, actor='assistant', **changes):
        old = planning.latest(self.m, episode_id, 'work_plan')
        old.pop('id')
        return planning.save(self.m, 'work_plan', payload={**old, **changes}, actor=actor, evidence=self.evidence,
                             episode_id=episode_id, expected_version=self.version(episode_id),
                             request_key=self.key(), session_id='session')

    def lesson(self, episode_id=None, links=None, **triggers):
        episode_id = episode_id or self.lessons
        payload = {'when': 'Editing the parser.', 'do': 'Run the Unicode fixture first.',
                   'because': 'Earlier edits lost characters.', 'exceptions': 'Documentation changes.', **triggers}
        return self.m.record(episode_id, 'lesson', payload, expected_version=self.version(episode_id),
                             request_key=self.key(), actor='assistant', evidence=self.evidence, links=links)['id']

    def accept(self, lesson_id, status='accepted', **triggers):
        episode_id = self.m._event(lesson_id)['episode_id']
        payload = {'lesson_id': lesson_id, 'status': status, 'reason': 'The user reviewed the lesson.', **triggers}
        return self.m.record(episode_id, 'lesson_review', payload, expected_version=self.version(episode_id),
                             request_key=self.key(), actor='workspace-user', evidence=self.evidence,
                             links=[{'event_id': lesson_id, 'reason': 'This review assesses the lesson.'}])['id']

    def decision(self, episode_id, **extra):
        previous = planning.latest(self.m, episode_id, 'decision')
        payload = {'decision': 'Change the decoder.', 'why': 'The fixture fails.', 'expected': 'The fixture passes.',
                   'reconsider_when': 'The fixture still fails.', **extra}
        return self.m.record(episode_id, 'decision', payload, expected_version=self.version(episode_id),
                             request_key=self.key(), actor='assistant', evidence=self.evidence,
                             supersedes=previous['id'] if previous else None)

    def attempt(self, episode_id, assessment='bad', failure_type=None, completion=None):
        decision = self.decision(episode_id)
        action = self.m.record(episode_id, 'action', {'action': 'Run the fixture.'}, expected_version=decision['version'],
                               request_key=self.key(), actor='assistant', decision_id=decision['id'])
        payload = {'observed': 'The fixture ran.', 'assessment': assessment, 'assessment_reason': 'The fixture reports it.',
                   'severity': 'major' if assessment == 'bad' else 'none', 'attribution': 'The decoder change.'}
        if failure_type:
            payload['failure_type'] = failure_type
        if completion:
            payload['completion'] = completion
        return self.m.record(episode_id, 'outcome', payload, expected_version=action['version'], request_key=self.key(),
                             actor='assistant', decision_id=decision['id'], evidence=self.evidence)['id']


class GuardHistoryTests(Fixture):
    # Record fields and acknowledgement.

    def test_plan_and_lesson_trigger_fields_are_validated(self):
        for paths in [[], ['../x'], 'src']:
            with self.subTest(paths=paths), self.assertRaises(InvalidRecord):
                self.work(paths=paths)
        episode = self.work(paths=['src/parser'])
        self.assertEqual(guards.plan_paths(self.m, episode), ['src/parser'])
        for triggers in [{'keywords': ['x' * 101]}, {'keywords': ['k'] * 31}, {'paths': ['../x']}, {'failure_type': ''}]:
            with self.subTest(triggers=triggers), self.assertRaises(InvalidRecord):
                self.lesson(**triggers)
        lesson = self.lesson()
        with self.assertRaises(InvalidRecord):
            self.accept(lesson, keywords='parser')
        with self.assertRaises(InvalidRecord):
            self.decision(episode, lessons_considered=[{'lesson_id': lesson, 'applies': 'maybe', 'reason': 'Unclear.'}])

    def test_decision_must_acknowledge_matching_guards(self):
        lesson = self.lesson(paths=['src/parser/**'])
        self.accept(lesson)
        unrelated = self.lesson(keywords=['billing'])
        self.accept(unrelated)
        episode = self.work(paths=['src/parser'])
        before = self.version(episode)
        with self.assertRaises(InvalidRecord) as caught:
            self.decision(episode)
        self.assertIn(lesson, str(caught.exception))
        details = caught.exception.details
        self.assertEqual([item['lesson_id'] for item in details['matched_lessons']], [lesson])
        self.assertEqual(set(details['matched_lessons'][0]), {'lesson_id', 'when', 'do', 'exceptions'})
        self.assertEqual(details['next_step'], {'action': 'acknowledge_lessons', 'read_with': {'view': 'record', 'id': lesson}})
        self.assertEqual(self.version(episode), before)
        with self.assertRaises(InvalidRecord):
            self.decision(episode, lessons_considered=[{'lesson_id': episode_note(self), 'applies': 'no', 'reason': 'Not a lesson.'}])
        considered = [{'lesson_id': lesson, 'applies': 'yes', 'reason': 'This work edits the parser.'}]
        recorded = self.decision(episode, lessons_considered=considered)
        self.assertEqual(self.m.read(recorded['id'])['payload']['lessons_considered'], considered)
        self.assertIn(lesson + ' (applies: yes)', self.m.history(episode))

    def test_absolute_and_relative_guard_paths_match_each_other(self):
        relative_lesson = self.lesson(paths=['src/parser'])
        self.accept(relative_lesson)
        episode = self.work(paths=[str(self.root / 'src' / 'parser')])
        with self.assertRaises(InvalidRecord) as caught:
            self.decision(episode)
        self.assertIn(relative_lesson, str(caught.exception))
        absolute_lesson = self.lesson(paths=[str(self.root / 'src' / 'lexer.py')])
        self.accept(absolute_lesson)
        self.assertEqual([guard['lesson_id'] for guard in guards.matching_guards(self.m, paths=['src/lexer.py'])],
                         [absolute_lesson])
        self.assertEqual([guard['lesson_id'] for guard in guards.matching_guards(self.m, paths=['src/parser/io.py'])],
                         [relative_lesson])
        lexer = self.work(paths=['src/lexer.py'], session='lexer')
        with self.assertRaises(InvalidRecord) as caught:
            self.decision(lexer)
        self.assertIn(absolute_lesson, str(caught.exception))
        self.decision(lexer, lessons_considered=[{'lesson_id': absolute_lesson, 'applies': 'yes', 'reason': 'The lexer changes.'}])
        self.assertEqual(guards.matching_guards(self.m, paths=['/elsewhere/src/lexer.py']), [])

    def test_keywords_match_whole_words_without_case(self):
        lesson = self.lesson(keywords=['Unicode'])
        self.accept(lesson)
        other = self.work(objective='Handle unicodes in names.', session='other')
        self.decision(other)
        episode = self.work(objective='Preserve UNICODE input.')
        with self.assertRaises(InvalidRecord):
            self.decision(episode)
        self.decision(episode, lessons_considered=[{'lesson_id': lesson, 'applies': 'no', 'reason': 'Only names change.'}])

    def test_proposed_rejected_and_untriggered_lessons_are_not_guards(self):
        proposed = self.lesson(paths=['src'])
        rejected = self.lesson(paths=['src'])
        self.accept(rejected, status='rejected')
        plain = self.lesson()
        self.accept(plain)
        self.assertEqual(guards.active_guards(self.m), [])
        episode = self.work(paths=['src'])
        self.decision(episode)
        self.assertTrue(proposed)

    def test_accepted_review_triggers_replace_lesson_triggers(self):
        lesson = self.lesson(paths=['docs'], failure_type='lost_text')
        review = self.accept(lesson, keywords=['encoding'])
        [guard] = guards.active_guards(self.m)
        self.assertEqual((guard['lesson_id'], guard['review_id']), (lesson, review))
        self.assertEqual((guard['paths'], guard['keywords'], guard['failure_type']), ([], ['encoding'], None))
        self.assertEqual(guards.matching_guards(self.m, paths=['docs/a.md']), [])
        [matched] = guards.matching_guards(self.m, text='An Encoding problem.')
        self.assertEqual(matched['matched_on'], ['keywords'])
        with self.assertRaises(InvalidRecord):
            self.accept(lesson, paths=['../outside'])

    # Learning signals.

    def test_recurrence_counts_only_failures_after_acceptance(self):
        first = self.work(session='first')
        self.attempt(first, failure_type='lost_text')
        lesson = self.lesson(failure_type='lost_text')
        self.accept(lesson)
        self.assertEqual(guards.recurrences(self.m), [])
        second = self.work(session='second')
        self.attempt(second, failure_type='other_failure')
        recurring = self.attempt(second, failure_type='lost_text')
        [entry] = guards.recurrences(self.m)
        self.assertEqual((entry['lesson_id'], entry['total']), (lesson, 1))
        self.assertEqual(entry['outcomes'][0]['id'], recurring)

    def test_failures_without_lesson(self):
        unaddressed = self.work(session='a')
        unaddressed_outcome = self.attempt(unaddressed)
        learned = self.work(session='b')
        self.attempt(learned)
        self.lesson(episode_id=learned)
        recovered = self.work(session='c')
        self.attempt(recovered)
        self.attempt(recovered, assessment='good', completion='complete')
        linked = self.work(session='d')
        linked_outcome = self.attempt(linked)
        self.lesson(links=[{'event_id': linked_outcome, 'reason': 'This failure prompted the lesson.'}])
        result = guards.failures_without_lesson(self.m)
        self.assertEqual([item['outcome_id'] for item in result], [unaddressed_outcome])
        self.assertEqual(result[0]['episode_id'], unaddressed)

    def test_scope_changes_report_added_patterns_by_agents(self):
        episode = self.work(paths=['src'])
        self.revise_plan(episode, paths=['src', 'docs'])
        self.revise_plan(episode, actor='workspace-user', paths=['src', 'docs', 'tests'])
        self.revise_plan(episode, next_action='Run the tests.')
        [change] = guards.scope_changes(self.m)
        self.assertEqual((change['episode_id'], change['added'], change['actor']), (episode, ['docs'], 'assistant'))

    def test_read_functions_work_on_read_only_connections(self):
        lesson = self.lesson(paths=['src'], failure_type='lost_text')
        self.accept(lesson)
        episode = self.work(paths=['src'])
        path = self.m.path
        plain = Memory.create(self.root / 'plain.sqlite', 'Plain', ['Nothing.'])
        plain.close()
        for database in (path, self.root / 'plain.sqlite'):
            with Memory(database, read_only=True) as memory:
                guards.active_guards(memory)
                guards.matching_guards(memory, paths=['src/a.py'], text='text', failure_type='lost_text')
                guards.recurrences(memory)
                guards.failures_without_lesson(memory)
                guards.scope_changes(memory)
                guards.session_work(memory, 'session')
                guards.scope_sentence(memory, 'session')
        with Memory(path, read_only=True) as memory:
            self.assertEqual(guards.plan_paths(memory, episode), ['src'])
            self.assertEqual(guards.session_work(memory, 'session')['episode_id'], episode)

    def test_mcp_accepts_new_fields(self):
        lesson = self.lesson()
        review = dispatch(self.m, 'memory_write', {'operation': 'record', 'request_key': 'mcp-review', 'data': {
            'episode_id': self.lessons, 'kind': 'lesson_review', 'expected_version': self.version(self.lessons),
            'actor': 'assistant', 'evidence': self.evidence,
            'links': [{'event_id': lesson, 'reason': 'This review assesses the lesson.'}],
            'payload': {'lesson_id': lesson, 'status': 'accepted', 'reason': 'Reviewed.', 'paths': ['src'], 'keywords': []}}})
        self.assertFalse(review['duplicate'])
        result = dispatch(self.m, 'memory_write', {'operation': 'plan', 'request_key': 'mcp-plan', 'data': {
            'title': 'Parser', 'objective': 'Repair the parser.', 'criterion': 'The fixture passes.', 'subject': 'code',
            'actor': 'assistant', 'evidence': self.evidence,
            'payload': {'state': 'ready', 'next_action': 'Edit.', 'scope': 'Parser.', 'autonomy': 'act',
                        'reason': 'Requested.', 'paths': ['src/parser']}}})
        episode = result['episode_id']
        decision = {'decision': 'Change the decoder.', 'why': 'The fixture fails.', 'expected': 'It passes.',
                    'reconsider_when': 'It fails.', 'uncertainty': 'Untested.', 'alternatives': []}
        arguments = {'operation': 'record', 'request_key': 'mcp-decision', 'data': {
            'episode_id': episode, 'kind': 'decision', 'expected_version': self.version(episode), 'actor': 'assistant',
            'evidence': self.evidence, 'payload': decision}}
        with self.assertRaises(InvalidRecord) as caught:
            dispatch(self.m, 'memory_write', arguments)
        self.assertEqual(caught.exception.details['next_step']['action'], 'acknowledge_lessons')
        arguments['data']['payload'] = {**decision, 'lessons_considered': [
            {'lesson_id': lesson, 'applies': 'yes', 'reason': 'The decoder is in the parser.'}]}
        arguments['request_key'] = 'mcp-decision-acknowledged'
        self.assertFalse(dispatch(self.m, 'memory_write', arguments)['duplicate'])


def episode_note(test):
    """Record a note and return its id, so a decision can reference a record that is not a lesson."""
    episode = test.lessons
    return test.m.record(episode, 'note', {'text': 'A note.'}, expected_version=test.version(episode),
                         request_key=test.key(), actor='assistant')['id']


class ScopeHookTests(Fixture):
    def hook(self, tool_name, tool_input, host='claude', session='session', tool_use_id='call-1'):
        event = {'hook_event_name': 'PreToolUse', 'session_id': session, 'tool_name': tool_name,
                 'tool_use_id': tool_use_id, 'tool_input': tool_input, 'cwd': str(self.root)}
        if host == 'codex':
            event['turn_id'] = 'turn-1'
        return event

    def receipts(self, name):
        return self.m.db.execute('SELECT * FROM host_receipts WHERE event_name=?', (name,)).fetchall()

    def test_claude_edit_outside_scope_is_blocked_without_a_pretool_receipt(self):
        episode = self.work(paths=['src/parser'])
        event = self.hook('Edit', {'file_path': str(self.root / 'docs' / 'guide.md'), 'old_string': 'a', 'new_string': 'b'})
        with self.assertRaises(guards.ScopeBlocked) as caught:
            codex_host.capture(self.m, event, host='claude')
        message = str(caught.exception)
        self.assertIn('docs/guide.md', message)
        self.assertIn('src/parser', message)
        self.assertIn(episode, message)
        self.assertEqual(self.receipts('PreToolUse'), [])
        self.assertEqual(codex_host.status(self.m)['unconfirmed_total'], 0)
        [blocked] = self.receipts('ScopeBlocked')
        payload = json.loads(blocked['payload'])
        self.assertEqual(payload['blocked'], ['docs/guide.md'])
        self.assertEqual(payload['allowed_patterns'], ['src/parser'])
        self.assertEqual((payload['episode_id'], payload['host']), (episode, 'claude'))
        inside = self.hook('Edit', {'file_path': str(self.root / 'src' / 'parser' / 'io.py')}, tool_use_id='call-2')
        codex_host.capture(self.m, inside, host='claude')
        self.assertEqual(len(self.receipts('PreToolUse')), 1)

    def test_codex_apply_patch_and_bash_heredoc_are_blocked(self):
        self.work(paths=['src/parser'])
        patch = '*** Begin Patch\n*** Update File: src/parser/io.py\n@@\n-a\n+b\n*** Add File: docs/new.md\n+text\n*** End Patch'
        with self.assertRaises(guards.ScopeBlocked) as caught:
            codex_host.capture(self.m, self.hook('apply_patch', {'input': patch}, host='codex'), host='codex')
        self.assertIn('docs/new.md', str(caught.exception))
        self.assertNotIn('src/parser/io.py is', str(caught.exception))
        heredoc = {'command': "apply_patch <<'EOF'\n" + patch.replace('docs/new.md', 'tests/new.py') + '\nEOF'}
        with self.assertRaises(guards.ScopeBlocked) as caught:
            codex_host.capture(self.m, self.hook('Bash', heredoc, host='codex', tool_use_id='call-2'), host='codex')
        self.assertIn('tests/new.py', str(caught.exception))
        self.assertEqual(self.receipts('PreToolUse'), [])
        self.assertEqual(len(self.receipts('ScopeBlocked')), 2)

    def test_read_tools_and_work_without_paths_are_not_blocked(self):
        self.work(paths=['src/parser'])
        codex_host.capture(self.m, self.hook('Read', {'file_path': str(self.root / 'docs' / 'guide.md')}), host='claude')
        codex_host.capture(self.m, self.hook('Bash', {'command': 'echo text > docs/out.txt'}, tool_use_id='call-2'), host='claude')
        self.assertEqual(len(self.receipts('PreToolUse')), 2)
        other = self.hook('Edit', {'file_path': str(self.root / 'docs' / 'guide.md')}, session='free', tool_use_id='call-3')
        self.work(session='free')
        codex_host.capture(self.m, other, host='claude')
        self.assertEqual(self.receipts('ScopeBlocked'), [])

    def test_bound_decision_scope_applies_when_no_work_is_in_progress(self):
        episode = self.work(paths=['src'], state='ready')
        decision = self.decision(episode)
        codex_host.bind(self.m, 'bound', decision['id'], 'bind-key')
        event = self.hook('Write', {'file_path': 'README.md', 'content': 'x'}, session='bound')
        with self.assertRaises(guards.ScopeBlocked):
            codex_host.capture(self.m, event, host='claude')
        self.assertEqual(self.m.db.execute("SELECT count(*) FROM events WHERE kind='action'").fetchone()[0], 0)

    def test_hook_process_returns_exit_code_two_with_the_message(self):
        self.work(paths=['src/parser'])
        event = self.hook('Edit', {'file_path': str(self.root / 'docs' / 'guide.md')})
        run = subprocess.run([sys.executable, '-m', 'memory_module.codex_host', '--db', str(self.m.path), '--host', 'claude'],
                             input=json.dumps(event), text=True, capture_output=True, timeout=60, cwd=REPOSITORY)
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertIn('Project Memory blocked this edit because docs/guide.md is outside the recorded scope', run.stderr)
        self.assertNotIn('Memory capture failed', run.stderr)
        self.assertEqual(run.stdout, '')
        self.assertFalse(self.m.path.with_suffix('.capture-errors').exists())
        self.assertEqual(len(self.receipts('ScopeBlocked')), 1)
        self.assertEqual(self.receipts('PreToolUse'), [])

    def test_guard_reminders_are_shown_once_per_session(self):
        lesson = self.lesson(paths=['src/parser/**'])
        self.accept(lesson)
        target = str(self.root / 'src' / 'parser' / 'io.py')
        first = codex_host.capture(self.m, self.hook('Edit', {'file_path': target}), host='claude')
        context = first['hookSpecificOutput']['additionalContext']
        self.assertIn(lesson, context)
        self.assertIn('Run the Unicode fixture first.', context)
        self.assertIn('Documentation changes.', context)
        second = codex_host.capture(self.m, self.hook('Edit', {'file_path': target}, tool_use_id='call-2'), host='claude')
        self.assertNotIn(lesson, json.dumps(second))
        other = codex_host.capture(self.m, self.hook('Edit', {'file_path': target}, session='other', tool_use_id='call-3'), host='claude')
        self.assertIn(lesson, other['hookSpecificOutput']['additionalContext'])
        outside = codex_host.capture(self.m, self.hook('Edit', {'file_path': str(self.root / 'docs' / 'a.md')}, session='third', tool_use_id='call-4'), host='claude')
        self.assertEqual(outside, {})
        self.assertEqual(len(self.receipts('GuardShown')), 2)

    def test_globs_do_not_allow_edits_outside_the_project(self):
        self.work(paths=['**/*.json'])
        outside = [str(Path.home() / '.claude' / 'settings.json'), '/etc/x.json']
        for index, target in enumerate(outside):
            with self.subTest(target=target), self.assertRaises(guards.ScopeBlocked):
                codex_host.capture(self.m, self.hook('Write', {'file_path': target, 'content': '{}'}, tool_use_id=f'out-{index}'), host='claude')
        codex_host.capture(self.m, self.hook('Write', {'file_path': str(self.root / 'config' / 'a.json'), 'content': '{}'},
                                             tool_use_id='inside'), host='claude')
        self.revise_plan(self.m.db.execute("SELECT episode_id FROM events WHERE kind='work_plan'").fetchone()[0], paths=['**'])
        with self.assertRaises(guards.ScopeBlocked):
            codex_host.capture(self.m, self.hook('Write', {'file_path': '/etc/passwd', 'content': 'x'}, tool_use_id='out-all'), host='claude')
        self.assertEqual(len(self.receipts('ScopeBlocked')), 3)

    def test_escaped_patch_and_mcp_write_outside_scope_are_blocked(self):
        self.work(paths=['src'])
        printf = "printf '*** Begin Patch\\n*** Update File: secrets.py\\n@@\\n-a\\n+b\\n*** End Patch\\n' | apply_patch"
        with self.assertRaises(guards.ScopeBlocked) as caught:
            codex_host.capture(self.m, self.hook('Bash', {'command': printf}, host='codex'), host='codex')
        self.assertIn('secrets.py', str(caught.exception))
        for index, tool in enumerate(['mcp__filesystem__write_file', 'mcp__filesystem__edit_file']):
            with self.subTest(tool=tool), self.assertRaises(guards.ScopeBlocked):
                codex_host.capture(self.m, self.hook(tool, {'path': str(self.root / 'secrets.py')}, tool_use_id=f'mcp-{index}'), host='claude')
        self.assertEqual(self.receipts('PreToolUse'), [])

    def test_reminders_match_absolute_guard_paths_and_absolute_targets(self):
        absolute = self.lesson(paths=[str(self.root / 'src' / 'parser')])
        self.accept(absolute)
        relative = self.lesson(paths=['src/lexer.py'])
        self.accept(relative)
        first = codex_host.capture(self.m, self.hook('Edit', {'file_path': str(self.root / 'src' / 'parser' / 'io.py')}), host='claude')
        self.assertIn(absolute, first['hookSpecificOutput']['additionalContext'])
        second = codex_host.capture(self.m, self.hook('Edit', {'file_path': 'src/lexer.py'}, tool_use_id='call-2'), host='claude')
        self.assertIn(relative, second['hookSpecificOutput']['additionalContext'])
        shown = guards.guard_reminders(self.m, session_id='direct', targets=['src/parser/x.py'])
        self.assertEqual([guard['lesson_id'] for guard in shown], [absolute])

    def test_session_context_names_allowed_patterns_and_guards(self):
        lesson = self.lesson(paths=['src/parser/io.py'])
        self.accept(lesson)
        episode = self.work(paths=['src/parser'])
        start = codex_host.capture(self.m, {'hook_event_name': 'SessionStart', 'session_id': 'session', 'source': 'startup'}, host='claude')
        context = start['hookSpecificOutput']['additionalContext']
        self.assertIn(f'Work {episode} allows edits only in src/parser, and lessons {lesson} apply to these paths.', context)
        prompt = codex_host.capture(self.m, {'hook_event_name': 'UserPromptSubmit', 'session_id': 'session',
                                             'prompt_id': 'p1', 'prompt': 'Continue.'}, host='claude')
        self.assertIn('allows edits only in src/parser', prompt['hookSpecificOutput']['additionalContext'])
        free = codex_host.capture(self.m, {'hook_event_name': 'SessionStart', 'session_id': 'free', 'source': 'startup'}, host='claude')
        self.assertNotIn('allows edits only', free['hookSpecificOutput']['additionalContext'])


if __name__ == '__main__':
    unittest.main()
