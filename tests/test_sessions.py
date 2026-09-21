"""Sessions into memory: digests, flags, proposals, the handoff check, the context size hint and the start summary.

This file checks sections 17.2 to 17.10 of .memory/build/rebuild-spec.md with invented transcripts. No real host runs
and no transcript of this machine is read: every folder is a temporary one.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from memory_module import InvalidRecord, Memory, api, codex_host, sessions, workspace
from memory_module.core import Conflict

SENTINEL = 'SENTINEL-USER-TEXT-7f3a'
SECRET = 'ghp_' + 'a' * 36


def claude_line(kind, content, at, *, session='s1', cwd=None, meta=False, origin='human', extra=None):
    value = {'type': kind, 'sessionId': session, 'timestamp': at, 'cwd': cwd, 'isSidechain': False,
             'message': {'role': kind, 'content': content}}
    if kind == 'user' and isinstance(content, str) and origin:
        value['origin'] = {'kind': origin}
    if meta:
        value['isMeta'] = True
    value.update(extra or {})
    return json.dumps(value)


def usage_line(at, tokens, *, session='s1'):
    return json.dumps({'type': 'assistant', 'sessionId': session, 'timestamp': at, 'isSidechain': False,
                       'message': {'id': 'msg_' + at, 'role': 'assistant', 'content': [],
                                   'usage': {'input_tokens': 10, 'cache_creation_input_tokens': 0, 'cache_read_input_tokens': tokens - 10}}})


class Fixture(unittest.TestCase):
    """A project with its own empty transcript folders and session reading switched on."""

    def setUp(self):
        reading = patch.dict(os.environ, {'PROJECT_MEMORY_SESSIONS': 'on'})
        reading.start()
        self.addCleanup(reading.stop)
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'project'
        self.root.mkdir()
        self.m = Memory.create(self.root / '.memory' / 'project.sqlite', 'Sessions', ['Keep sessions.'])
        codex_host.initialize(self.m)
        self.claude = self.base / 'claude'
        self.codex = self.base / 'codex'
        self.own = self.claude / sessions.claude_folder_name(self.root)
        self.own.mkdir(parents=True)
        self.codex.mkdir()
        self.found = {'claude': self.claude, 'codex': self.codex}
        self.counter = 0

    def tearDown(self):
        self.m.close()
        self.temp.cleanup()

    def transcript(self, name, lines, folder=None):
        path = (folder or self.own) / (name + '.jsonl')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        return path

    def basic(self, session='s1', day='2026-09-10'):
        return [
            claude_line('user', 'Please build the parser. ' + SENTINEL + ' ' + SECRET, f'{day}T10:00:00Z', session=session, cwd=str(self.root)),
            claude_line('user', '<system-reminder>injected hook text</system-reminder>', f'{day}T10:00:01Z', session=session, meta=True),
            claude_line('user', 'Stop hook feedback: record something', f'{day}T10:00:02Z', session=session, meta=True),
            claude_line('assistant', [{'type': 'tool_use', 'id': 't1', 'name': 'Bash', 'input': {'command': 'pytest -q'}}], f'{day}T10:01:00Z', session=session),
            claude_line('user', [{'type': 'tool_result', 'tool_use_id': 't1', 'is_error': True, 'content': 'Error: Exit code 2\nfailed'}], f'{day}T10:01:05Z', session=session),
            claude_line('assistant', [{'type': 'tool_use', 'id': 't2', 'name': 'Edit', 'input': {'file_path': str(self.root / 'parser.py')}}], f'{day}T10:02:00Z', session=session),
            claude_line('user', [{'type': 'tool_result', 'tool_use_id': 't2', 'content': 'ok'}], f'{day}T10:02:01Z', session=session),
            claude_line('user', 'No, do not use regular expressions; use the tokenizer instead.', f'{day}T10:03:00Z', session=session, cwd=str(self.root)),
            claude_line('assistant', [{'type': 'tool_use', 'id': 't3', 'name': 'mcp__project_memory__memory_write', 'input': {'operation': 'source'}}], f'{day}T10:03:30Z', session=session),
            claude_line('user', [{'type': 'tool_result', 'tool_use_id': 't3', 'content': [{'type': 'text', 'text': '{"id":"source_0123456789abcdef0123","status":"current_copy"}'}]}], f'{day}T10:03:31Z', session=session),
        ]


class SessionTests(Fixture):
    def test_a_digest_is_extracted_redacted_and_searchable(self):
        self.transcript('s1', self.basic())
        result = sessions.collect(self.m, found=self.found, now='2026-09-10T12:00:00+00:00')
        self.assertEqual(result['sessions_changed'], 1)
        [row] = sessions.digests(self.m)
        self.assertEqual((row['messages'], row['files'], row['failures']), (2, 1, 1))
        body = self.m.read(row['source_id'], detail=True)['body']
        self.assertIn(SENTINEL, body)
        self.assertNotIn(SECRET, body)
        self.assertIn(sessions.REDACTED, body)
        self.assertNotIn('injected hook text', body)
        self.assertNotIn('Stop hook feedback', body)
        self.assertIn('exit code 2: pytest -q', body)
        self.assertIn('parser.py', body)
        self.assertIn('line 1', body)
        self.assertIn('source_0123456789abcdef0123 (source)', body)
        found = self.m.search('tokenizer')['records']
        self.assertIn(row['source_id'], [r['id'] for r in found])

    def test_a_long_session_is_cut_to_the_digest_limit_and_says_what_is_left_out(self):
        day = '2026-09-11'
        lines = [claude_line('user', f'Message {index:03d}. ' + 'x' * sessions.MESSAGE_CHARACTERS,
                             f'{day}T10:00:{index:02d}Z', cwd=str(self.root)) for index in range(60)]
        self.transcript('s1', lines)
        sessions.collect(self.m, found=self.found, now=f'{day}T12:00:00+00:00')
        [row] = sessions.digests(self.m)
        self.assertEqual(row['messages'], 60)
        body = self.m.read(row['source_id'], detail=True)['body']
        self.assertGreater(len(body), 10_000)
        self.assertLessEqual(len(body), sessions.DIGEST_CHARACTERS)
        self.assertIn('messages in the middle of the session are left out', body)
        self.assertIn('Message 000.', body)
        self.assertIn('Message 059.', body)
        self.assertIn('Session digest of the claude session', body)

    def test_collection_is_incremental_and_idempotent(self):
        path = self.transcript('s1', self.basic())
        sessions.collect(self.m, found=self.found)
        [first] = sessions.digests(self.m)
        again = sessions.collect(self.m, found=self.found)
        self.assertEqual(again['files_read'], 0)
        with path.open('a', encoding='utf-8') as handle:
            handle.write(claude_line('user', 'Also add a test.', '2026-09-10T10:05:00Z', cwd=str(self.root)) + '\n')
        sessions.collect(self.m, found=self.found)
        [second] = sessions.digests(self.m)
        self.assertEqual(second['messages'], 3)
        self.assertNotEqual(first['source_id'], second['source_id'])
        versions = self.m.db.execute("SELECT count(*) FROM sources WHERE source_key LIKE 'session-digest:%'").fetchone()[0]
        self.assertEqual(versions, 2)

    def test_a_session_run_from_another_folder_that_names_the_project_is_read(self):
        other = self.claude / 'Users-someone-other-folder'
        lines = [claude_line('user', 'Work on the memory project.', '2026-09-11T09:00:00Z', session='s2', cwd='/elsewhere'),
                 claude_line('assistant', [{'type': 'tool_use', 'id': 'a', 'name': 'Bash',
                              'input': {'command': 'python3 pm.py --db ' + str(self.root / '.memory/project.sqlite')}}], '2026-09-11T09:01:00Z', session='s2'),
                 claude_line('user', [{'type': 'tool_result', 'tool_use_id': 'a', 'content': 'done'}], '2026-09-11T09:01:01Z', session='s2')]
        self.transcript('s2', lines, folder=other)
        self.transcript('s3', [claude_line('user', 'Unrelated work.', '2026-09-11T09:00:00Z', session='s3', cwd='/elsewhere')], folder=other)
        sessions.collect(self.m, found=self.found)
        rows = sessions.digests(self.m)
        self.assertEqual([r['session_id'] for r in rows], ['s2'])
        self.assertEqual(rows[0]['related'], 'mentions_project')
        later = self.transcript('s3', [claude_line('user', 'Unrelated work.', '2026-09-11T09:00:00Z', session='s3', cwd='/elsewhere')], folder=other)
        with later.open('a', encoding='utf-8') as handle:
            handle.write(claude_line('assistant', [{'type': 'tool_use', 'id': 'b', 'name': 'Read', 'input': {'file_path': str(self.root / 'README.md')}}],
                                     '2026-09-11T10:00:00Z', session='s3') + '\n')
        sessions.collect(self.m, found=self.found)
        [late] = [r for r in sessions.digests(self.m) if r['session_id'] == 's3']
        self.assertEqual(late['messages'], 1, 'A session that names the project later is read from its start.')

    def test_a_codex_session_is_read_by_its_working_folder(self):
        folder = self.codex / '2026' / '09' / '12'  # Read in full collection; a session start reads only the last two days.
        lines = [json.dumps({'type': 'session_meta', 'timestamp': '2026-09-12T08:00:00Z', 'payload': {'id': 'c1', 'cwd': str(self.root)}}),
                 json.dumps({'type': 'response_item', 'timestamp': '2026-09-12T08:00:01Z', 'payload': {'type': 'message', 'role': 'user',
                             'content': [{'type': 'input_text', 'text': '<environment_context>x</environment_context>'}]}}),
                 json.dumps({'type': 'response_item', 'timestamp': '2026-09-12T08:00:02Z', 'payload': {'type': 'message', 'role': 'user',
                             'content': [{'type': 'input_text', 'text': 'Fix the importer.'}]}}),
                 json.dumps({'type': 'response_item', 'timestamp': '2026-09-12T08:01:00Z', 'payload': {'type': 'custom_tool_call', 'name': 'apply_patch',
                             'call_id': 'p1', 'input': '*** Begin Patch\n*** Update File: importer.py\n*** End Patch'}}),
                 json.dumps({'type': 'response_item', 'timestamp': '2026-09-12T08:01:01Z', 'payload': {'type': 'custom_tool_call_output', 'call_id': 'p1', 'output': 'Success'}}),
                 json.dumps({'type': 'response_item', 'timestamp': '2026-09-12T08:02:00Z', 'payload': {'type': 'custom_tool_call', 'name': 'exec',
                             'call_id': 'p2', 'input': "apply_patch <<'EOF'\n*** Begin Patch\n*** Add File: exporter.py\n+x\n*** End Patch\nEOF"}}),
                 json.dumps({'type': 'response_item', 'timestamp': '2026-09-12T08:02:01Z', 'payload': {'type': 'custom_tool_call_output', 'call_id': 'p2', 'output': 'Exit code: 0'}})]
        self.transcript('rollout-2026-09-12-c1', lines, folder=folder)
        sessions.collect(self.m, found=self.found)
        [row] = sessions.digests(self.m)
        self.assertEqual((row['host'], row['messages'], row['files']), ('codex', 1, 2))

    def test_a_direction_without_a_record_is_flagged_and_can_be_dismissed(self):
        self.transcript('s1', self.basic())
        sessions.collect(self.m, found=self.found, now='2026-09-10T10:30:00+00:00')
        self.assertEqual(sessions.flags(self.m), [], 'A window that has not closed is not judged.')
        sessions.detect_gaps(self.m, 'claude:s1', now='2026-09-10T12:00:00+00:00')
        [flag] = sessions.flags(self.m)
        self.assertEqual((flag['status'], flag['confidence'], flag['line']), ('open', 'low', 8))
        decided = sessions.decide_flag(self.m, flag['id'], 'dismissed', 'This was recorded in another session.')
        self.assertEqual(decided['status'], 'dismissed')
        self.assertEqual(sessions.precision(self.m)['dismissed'], 1)
        sessions.detect_gaps(self.m, 'claude:s1', now='2026-09-10T12:00:00+00:00')
        self.assertEqual(len(sessions.flags(self.m)), 1, 'A dismissed flag is never raised again.')
        with self.assertRaises(Conflict):
            sessions.decide_flag(self.m, flag['id'], 'confirmed', 'Changed my mind.')

    def test_the_user_decides_a_flag_without_a_reason_and_several_flags_together(self):
        self.transcript('s1', self.basic())
        self.transcript('s2', self.basic(session='s2'))
        sessions.collect(self.m, found=self.found, now='2026-09-10T12:00:00+00:00')
        first, second = sessions.flags(self.m)
        decided = workspace.action(self.m, 'session_flag', {'flag_id': first['id'], 'status': 'dismissed'}, 'panel-flag-no-reason')
        self.assertEqual((decided['status'], decided['reason']), ('dismissed', ''))
        with self.assertRaises(Conflict):
            workspace.action(self.m, 'session_flags', {'flag_ids': [second['id'], first['id']], 'status': 'confirmed'}, 'panel-flags-refused')
        self.assertEqual(sessions.precision(self.m)['open'], 1, 'One refused flag refuses the whole batch.')
        batch = workspace.action(self.m, 'session_flags', {'flag_ids': [second['id']], 'status': 'dismissed',
                                                           'reason': 'These were recorded elsewhere.'}, 'panel-flags-1')
        self.assertEqual((batch['decided'], batch['flag_ids']), (1, [second['id']]))
        self.assertEqual((sessions.precision(self.m)['dismissed'], sessions.precision(self.m)['open']), (2, 0))
        with self.assertRaises(InvalidRecord):
            workspace.action(self.m, 'session_flags', {'flag_ids': [], 'status': 'dismissed'}, 'panel-flags-empty')

    def test_a_record_written_in_the_window_prevents_a_flag(self):
        self.transcript('s1', self.basic())
        episode = self.m.start('Parser', 'Build the parser.', 'code', 'The parser works.', subject='code')
        with patch.object(self.m, 'now', lambda: '2026-09-10T10:20:00.000000+00:00'):
            self.m.record(episode['id'], 'lesson', {'when': 'Parsing', 'do': 'Use the tokenizer.', 'because': 'The user said so.', 'exceptions': 'None.'},
                          expected_version=0, request_key='k1', actor='claude-code',
                          evidence=[{'source_id': self.m.source('u', 'U', 'U', 'U', 'user')['id'], 'reason': 'User.'}])
        sessions.collect(self.m, found=self.found, now='2026-09-10T12:00:00+00:00')
        self.assertEqual(sessions.flags(self.m), [])

    def test_proposals_are_pending_until_the_user_accepts_one(self):
        self.transcript('s1', self.basic())
        sessions.collect(self.m, found=self.found)
        episode = self.m.start('Parser', 'Build the parser.', 'code', 'The parser works.', subject='code')
        answer = {'proposals': [
            {'slot': 'lesson', 'text': 'Use the tokenizer for parsing.', 'pointers': [8], 'confidence': 'high',
             'when': 'When parsing input', 'do': 'Use the tokenizer', 'because': 'The user refused regular expressions', 'exceptions': ''},
            {'slot': 'decision', 'text': 'The parser uses the tokenizer.', 'pointers': [8], 'confidence': 'medium',
             'when': '', 'do': '', 'because': '', 'exceptions': ''}]}
        seen = {}
        def runner(host, packet, timeout):
            seen['packet'] = packet
            return answer
        result = sessions.distill(self.m, host='claude', runner=runner)
        self.assertEqual(result['proposals'], 2)
        self.assertLessEqual(len(seen['packet']), sessions.DISTILL_CHARACTERS)
        events = self.m.db.execute('SELECT count(*) FROM events').fetchone()[0]
        self.assertEqual(events, 0, 'Nothing is recorded before the user accepts.')
        lesson, decision = sorted(sessions.proposals(self.m), key=lambda p: p['slot'] != 'lesson')
        accepted = sessions.decide_proposal(self.m, lesson['id'], 'accepted', 'This is right.', episode_id=episode['id'], expected_version=0)
        record = self.m.read(accepted['record_id'])
        self.assertEqual((record['kind'], record['actor']), ('lesson', 'workspace-user'))
        self.assertEqual(record['evidence'][0]['source_id'], lesson['source_id'])
        rejected = sessions.decide_proposal(self.m, decision['id'], 'rejected', 'Not a decision.')
        self.assertIsNone(rejected['record_id'])
        with self.assertRaises(InvalidRecord):
            sessions.validate_proposals({'proposals': [{'slot': 'wish', 'text': 'x', 'pointers': [1], 'confidence': 'low'}]})

    def test_the_handoff_check_lists_gaps_or_reports_ready(self):
        self.assertEqual(sessions.handoff(self.m, now='2026-09-10T12:00:00+00:00', found=self.found)['status'], 'ready')
        self.transcript('s1', self.basic())
        report = sessions.handoff(self.m, now='2026-09-10T12:00:00+00:00', found=self.found)
        self.assertEqual(report['status'], 'gaps')
        self.assertIn('possible_unrecorded_direction', {g['type'] for g in report['gaps']})

    def test_the_start_summary_lists_earlier_sessions_without_quoting_them(self):
        self.transcript('s1', self.basic('s1', '2026-09-10'))
        self.transcript('s2', self.basic('s2', '2026-09-11'))
        self.transcript('current', self.basic('current', '2026-09-12'))
        text = sessions.start_summary(self.m, 'current', room=2000, found=self.found)
        self.assertTrue(text.startswith('Earlier sessions:'))
        self.assertLessEqual(len(text), sessions.START_CHARACTERS)
        self.assertNotIn(SENTINEL, text)
        self.assertNotIn('tokenizer', text)
        rows = sessions.digests(self.m, exclude='current')
        self.assertEqual([r['session_id'] for r in rows], ['s2', 's1'])
        self.assertIn(rows[0]['source_id'], text)
        self.assertNotIn(sessions.digests(self.m)[0]['source_id'] if sessions.digests(self.m)[0]['session_id'] == 'current' else 'x', text)
        receipt = self.m.db.execute("SELECT payload FROM host_receipts WHERE event_name='ContextProvided'").fetchone()
        self.assertIn(rows[0]['source_id'], json.loads(receipt[0])['record_ids'])

    def test_the_start_summary_respects_a_small_room_and_reports_omissions(self):
        for day, name in (('10', 's1'), ('11', 's2'), ('12', 's3')):
            self.transcript(name, self.basic(name, '2026-09-' + day))
        text = sessions.start_summary(self.m, 'other', room=330, found=self.found)
        self.assertLessEqual(len(text), 330)
        self.assertIn('left out', text)

    def test_the_start_collection_honours_its_time_limit(self):
        for index in range(3):
            self.transcript(f's{index}', self.basic(f's{index}'))
        real = sessions.scan
        def slow(*args, **kwargs):
            time.sleep(0.6)
            return real(*args, **kwargs)
        with patch.object(sessions, 'scan', slow):
            started = time.monotonic()
            result = sessions.collect(self.m, found=self.found, limit=5, seconds=1.0, mentions=False)
        self.assertLess(time.monotonic() - started, 2.5)
        self.assertFalse(result['complete'])

    def test_switching_session_reading_off_removes_the_summary_and_the_collection(self):
        self.transcript('s1', self.basic())
        sessions.configure(self.m, reading=False)
        self.assertEqual(sessions.start_summary(self.m, 'current', room=2000, found=self.found), '')
        self.assertFalse(sessions.collect(self.m, found=self.found)['reading'])
        self.assertEqual(sessions.digests(self.m), [])

    def test_the_context_hint_appears_above_the_threshold_and_repeats_after_growth(self):
        path = self.base / 'live.jsonl'
        path.write_text(usage_line('2026-09-12T10:00:00Z', 260_000) + '\n', encoding='utf-8')
        first = sessions.context_hint(self.m, 'live', str(path))
        self.assertIn('260,000 tokens', first)
        self.assertIn('project-memory handoff', first)
        with path.open('a') as handle:
            handle.write(usage_line('2026-09-12T10:01:00Z', 280_000) + '\n')
        self.assertEqual(sessions.context_hint(self.m, 'live', str(path)), '')
        with path.open('a') as handle:
            handle.write(usage_line('2026-09-12T10:02:00Z', 311_000) + '\n')
        self.assertIn('311,000', sessions.context_hint(self.m, 'live', str(path)))
        path.write_text(usage_line('2026-09-12T10:00:00Z', 100_000) + '\n', encoding='utf-8')
        self.assertEqual(sessions.context_hint(self.m, 'small', str(path)), '')

    def test_the_session_start_hook_adds_the_summary_within_the_hook_budget(self):
        self.transcript('s1', self.basic('s1', '2026-09-10'))
        with patch.object(sessions, 'folders', lambda: self.found):
            output = codex_host.capture(self.m, {'hook_event_name': 'SessionStart', 'session_id': 'fresh', 'source': 'startup'}, host='claude')
        context = output['hookSpecificOutput']['additionalContext']
        self.assertIn('Earlier sessions:', context)
        self.assertNotIn(SENTINEL, context)
        self.assertLessEqual(len(context), codex_host.HOOK_CHARACTERS)

    def test_the_prompt_hook_adds_the_context_hint(self):
        path = self.base / 'live.jsonl'
        path.write_text(usage_line('2026-09-12T10:00:00Z', 400_000) + '\n', encoding='utf-8')
        output = codex_host.capture(self.m, {'hook_event_name': 'UserPromptSubmit', 'session_id': 'live', 'prompt_id': 'p1',
                                             'prompt': 'Continue.', 'transcript_path': str(path)}, host='claude')
        self.assertIn('400,000 tokens', output['hookSpecificOutput']['additionalContext'])

    def test_the_panel_reads_sessions_and_the_user_decides_flags_and_proposals(self):
        self.transcript('s1', self.basic())
        sessions.collect(self.m, found=self.found, now='2026-09-10T12:00:00+00:00')
        episode = self.m.start('Parser', 'Build the parser.', 'code', 'The parser works.', subject='code')
        sessions.distill(self.m, host='codex', found=self.found, runner=lambda host, packet, timeout: {'proposals': [
            {'slot': 'next_action', 'text': 'Add tests for the tokenizer.', 'pointers': [8], 'confidence': 'medium',
             'when': '', 'do': '', 'because': '', 'exceptions': ''}]})
        view = api.ENDPOINTS['sessions'](self.m, {})
        self.assertEqual((len(view['digests']), len(view['flags']), len(view['proposals'])), (1, 1, 1))
        # Now names both kinds with their counts, so the user finds them without opening the Sessions view.
        waiting = {item['type']: item['count'] for item in api.now(self.m, {})['attention'] if item['type'].startswith('session_')}
        self.assertEqual(waiting, {'session_flags': 1, 'session_proposals': 1})
        flag = workspace.action(self.m, 'session_flag', {'flag_id': view['flags'][0]['id'], 'status': 'confirmed',
                                                         'reason': 'The refusal was never recorded.'}, 'panel-flag-1')
        self.assertEqual((flag['status'], flag['decided_by']), ('confirmed', 'workspace-user'))
        with self.assertRaises(InvalidRecord):
            workspace.action(self.m, 'session_proposal', {'proposal_id': view['proposals'][0]['id'], 'status': 'accepted',
                                                          'reason': 'Right.'}, 'panel-proposal-0')
        accepted = workspace.action(self.m, 'session_proposal', {'proposal_id': view['proposals'][0]['id'], 'status': 'accepted',
                                    'reason': 'Right.', 'episode_id': episode['id'], 'expected_version': 0}, 'panel-proposal-1')
        note = self.m.read(accepted['record_id'])
        self.assertEqual(note['kind'], 'note')
        self.assertIn('Add tests for the tokenizer.', note['payload']['text'])
        again = workspace.action(self.m, 'session_proposal', {'proposal_id': view['proposals'][0]['id'], 'status': 'accepted',
                                 'reason': 'Right.', 'episode_id': episode['id'], 'expected_version': 0}, 'panel-proposal-1')
        self.assertEqual(again['record_id'], accepted['record_id'])
        self.assertEqual(api.ENDPOINTS['sessions'](self.m, {})['counts']['confirmed'], 1)

    def test_an_agent_cannot_write_a_session_digest_source(self):
        with self.assertRaises(InvalidRecord):
            self.m.source('session-digest:abc', 'Forged', 'Forged', 'Forged', 'tool')

    def test_a_windows_project_folder_is_found_in_json_escaped_tool_arguments(self):
        folder = 'C:\\Users\\someone\\project'
        path = self.base / 'escaped.jsonl'
        path.write_text(json.dumps({'command': 'python pm.py --db ' + folder + '\\.memory'}) + '\n', encoding='utf-8')
        self.assertTrue(sessions._mentions(path, folder))
        reader = sessions.ClaudeReader(folder, {})
        reader.mention(json.dumps({'command': 'cd ' + folder}))
        self.assertTrue(reader.data['mentions'])

    def test_redaction_covers_the_common_credential_formats(self):
        for secret in (SECRET, 'sk-ant-' + 'b' * 30, 'AKIA' + 'C' * 16, 'Bearer ' + 'd' * 30, 'api_key=' + 'e' * 20,
                       '-----BEGIN OPENSSH ' + 'PRIVATE KEY-----\nabc\n-----END OPENSSH ' + 'PRIVATE KEY-----'):
            with self.subTest(secret=secret[:12]):
                self.assertNotIn(secret, sessions.redact('value ' + secret + ' end'))


if __name__ == '__main__':
    unittest.main()


class ReconcileTests(Fixture):
    """Section 17.11: unconfirmed tool calls resolved from the panel with the transcript of their session as evidence."""

    SESSION = 'claude-session-17-11'

    def pre(self, tool, identifier, session=None):
        return codex_host.capture(self.m, {'hook_event_name': 'PreToolUse', 'session_id': session or self.SESSION, 'tool_name': tool,
                                           'tool_use_id': identifier, 'tool_input': {'x': 1}, 'cwd': str(self.root)}, host='claude')

    def fixture(self):
        at = '2026-09-13T10:00:0{}Z'
        self.transcript(self.SESSION, [
            claude_line('assistant', [{'type': 'tool_use', 'id': 'toolu_read_ok', 'name': 'Read', 'input': {'file_path': 'a'}}], at.format(1), session=self.SESSION),
            claude_line('user', [{'type': 'tool_result', 'tool_use_id': 'toolu_read_ok', 'content': 'file text ' + SECRET}], at.format(2), session=self.SESSION),
            claude_line('assistant', [{'type': 'tool_use', 'id': 'toolu_bash_bad', 'name': 'Bash', 'input': {'command': 'make'}}], at.format(3), session=self.SESSION),
            claude_line('user', [{'type': 'tool_result', 'tool_use_id': 'toolu_bash_bad', 'is_error': True, 'content': 'Exit code 2'}], at.format(4), session=self.SESSION),
            claude_line('assistant', [{'type': 'tool_use', 'id': 'toolu_read_lost', 'name': 'Read', 'input': {'file_path': 'b'}}], at.format(5), session=self.SESSION)])
        folder = self.codex / '2026' / '09' / '13'
        self.transcript('rollout-2026-09-13-02a0bbbb-0000-7000-8000-000000000001', [
            json.dumps({'type': 'event_msg', 'payload': {'type': 'item_completed', 'item': {'type': 'McpToolCall', 'id': 'exec-codex-get-1', 'tool': 'memory_get',
                        'readOnlyHint': 'True', 'status': 'failed', 'result': {'content': [{'type': 'text', 'text': 'Event was not found.'}], 'isError': True}}}})], folder=folder)
        for tool, identifier in (('Read', 'toolu_read_ok'), ('Bash', 'toolu_bash_bad'), ('Read', 'toolu_read_lost'), ('Edit', 'toolu_edit_gone')):
            self.pre(tool, identifier)
        self.pre('mcp__cua_repl__js', 'exec-codex-get-1', session='02a0bbbb-0000-7000-8000-000000000001')

    def test_each_call_carries_the_suggestion_of_its_transcript(self):
        self.fixture()
        listed = {item['tool_use_id'] if 'tool_use_id' in item else codex_host.read_receipt(self.m, item['id'])['tool_use_id']: item
                  for item in sessions.unconfirmed(self.m, found=self.found)['items']}
        self.assertEqual(listed['toolu_read_ok']['transcript']['suggested'], 'completed')
        self.assertNotIn(SECRET, listed['toolu_read_ok']['transcript']['excerpt'])
        self.assertEqual(listed['toolu_bash_bad']['transcript']['suggested'], 'failed')
        self.assertEqual(listed['toolu_read_lost']['transcript']['result'], 'no_result')
        self.assertFalse(listed['toolu_edit_gone']['transcript']['found'])
        codex = listed['exec-codex-get-1']
        self.assertEqual((codex['transcript']['suggested'], codex['read_only']), ('failed', True))

    def test_bulk_resolution_touches_only_read_only_calls_with_a_result(self):
        self.fixture()
        result = sessions.reconcile_read_only(self.m, 'Read-only calls change nothing.', 'bulk-1', found=self.found)
        self.assertEqual(result['reconciled'], 2)
        left = {codex_host.read_receipt(self.m, item['id'])['tool_use_id'] for item in sessions.unconfirmed(self.m, found=self.found)['items']}
        self.assertEqual(left, {'toolu_bash_bad', 'toolu_read_lost', 'toolu_edit_gone'})

    def test_a_single_reconciliation_clears_the_block_of_its_work_item(self):
        self.fixture()
        from memory_module import planning
        episode = self.m.start('Build', 'Build it.', 'code', 'Built.', subject='code')
        [item] = [i for i in sessions.unconfirmed(self.m, found=self.found)['items'] if codex_host.read_receipt(self.m, i['id'])['tool_use_id'] == 'toolu_edit_gone']
        with patch.object(sessions, 'folders', lambda: self.found):
            result = workspace.action(self.m, 'reconcile', {'receipt_id': item['id'], 'resolution': 'not_run', 'reason': 'The file shows no edit.'}, 'panel-reconcile-1')
        source = self.m.read(result['evidence'][0]['source_id'])
        self.assertEqual(source['origin'], 'user')
        remaining = {codex_host.read_receipt(self.m, i['id'])['tool_use_id'] for i in sessions.unconfirmed(self.m, found=self.found)['items']}
        self.assertNotIn('toolu_edit_gone', remaining)
        with patch.object(sessions, 'folders', lambda: self.found):
            bad = [i for i in sessions.unconfirmed(self.m, found=self.found)['items'] if codex_host.read_receipt(self.m, i['id'])['tool_use_id'] == 'toolu_bash_bad'][0]
            done = workspace.action(self.m, 'reconcile', {'receipt_id': bad['id'], 'resolution': 'failed', 'reason': 'make failed.'}, 'panel-reconcile-2')
        self.assertEqual(self.m.read(done['evidence'][0]['source_id'])['origin'], 'tool')

    def test_project_memory_tools_are_not_captured(self):
        before = self.m.db.execute('SELECT count(*) FROM host_receipts').fetchone()[0]
        for name in ('mcp__project_memory__memory_get', 'mcp__memory__memory_write', 'mcp__plugin_project-memory_project_memory__memory_context'):
            self.pre(name, 'toolu_' + name[-12:])
        self.assertEqual(self.m.db.execute('SELECT count(*) FROM host_receipts').fetchone()[0], before)
