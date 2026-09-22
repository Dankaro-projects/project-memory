"""Section 17.1: fresh work apart from cache reads, routing on fresh work, session statistics, one count per Claude
message, and the modes of the machine memory files.

Every log line here is invented. The machine memory lives in a temporary folder.
"""
from contextlib import redirect_stdout
from datetime import timedelta
import io
import json
import os
from pathlib import Path
import stat
import sys
import unittest
from unittest import mock

from memory_module import Memory, hosts, install, machine, usage
from memory_module.cli import doctor, main as cli_main
from tests.test_usage import DIGEST, NOON, UsageFixture, stamp_text

POSIX = os.name != 'nt'


def claude_block(message, at, usage_value, *, request='req_sample_repeat', session='session-sample-one', block='text'):
    """One line of an assistant message. Claude Code writes one line for each block of content and repeats the usage."""
    line = {'type': 'assistant', 'timestamp': at, 'sessionId': session, 'uuid': 'uuid-' + block + '-' + at,
            'message': {'id': message, 'model': 'sample', 'content': [{'type': block}], 'usage': usage_value}}
    if request is not None:
        line['requestId'] = request
    return json.dumps(line) + '\n'


def codex_meta(identifier):
    return json.dumps({'timestamp': '2026-09-16T08:00:00Z', 'type': 'session_meta', 'payload': {'id': identifier}}) + '\n'


def codex_count(at, total, last):
    return json.dumps({'timestamp': at, 'type': 'event_msg', 'payload': {
        'type': 'token_count', 'info': {'total_token_usage': total, 'last_token_usage': last}}}) + '\n'


def codex_usage(input_tokens, cached, output, reasoning=0):
    return {'input_tokens': input_tokens, 'cached_input_tokens': cached, 'output_tokens': output,
            'reasoning_output_tokens': reasoning, 'total_tokens': input_tokens + output}


REPEATED = {'input_tokens': 4, 'cache_creation_input_tokens': 300, 'cache_read_input_tokens': 90000, 'output_tokens': 60}
# The usage ledger as section 16.2 created it, before the session columns existed.
OLD_SCHEMA = machine.USAGE_SCHEMA.replace(',\n session TEXT, context_tokens INTEGER\n', '\n')


class FreshFixture(UsageFixture):
    """Empty log folders for invented lines. This class defines no tests of its own."""

    def setUp(self):
        super().setUp()
        self.folders = {'codex': self.root / 'fresh' / 'codex', 'claude': self.root / 'fresh' / 'claude'}
        for folder in self.folders.values():
            folder.mkdir(parents=True)

    def write(self, host, name, *lines):
        path = self.folders[host] / name
        path.write_text(''.join(lines), encoding='utf-8')
        return path


class FreshWorkTests(FreshFixture):
    def test_repeated_usage_lines_of_one_claude_message_count_once(self):
        # Three blocks of one message repeat its usage; one line lacks the request identifier.
        self.write('claude', 'repeat.jsonl',
                   claude_block('msg_sample_repeat', '2026-09-16T10:00:00Z', REPEATED, block='thinking'),
                   claude_block('msg_sample_repeat', '2026-09-16T10:00:01Z', REPEATED, block='text'),
                   claude_block('msg_sample_repeat', '2026-09-16T10:00:02Z', REPEATED, request=None, block='tool_use'))
        stats = self.collect(projects=False)
        self.assertEqual(stats['records_added'], 1)
        window = self.summary()['claude']['windows']['last_5_hours']
        self.assertEqual({name: window[name] for name in ('fresh_input_tokens', 'cache_write_tokens', 'cache_read_tokens',
                                                           'output_tokens', 'fresh_work_tokens', 'records')},
                         {'fresh_input_tokens': 4, 'cache_write_tokens': 300, 'cache_read_tokens': 90000,
                          'output_tokens': 60, 'fresh_work_tokens': 364, 'records': 1})

    def test_a_ledger_keyed_by_message_and_request_is_migrated_and_counts_a_message_once(self):
        with Memory(self.machine_path) as store, store._write():
            for statement in machine._statements(OLD_SCHEMA):
                store.db.execute(statement)
            store.db.execute('INSERT INTO usage_records (entry,host,kind,started_at,ended_at,input_tokens,cached_input_tokens,'
                             'cache_write_tokens,output_tokens,total_tokens,recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                             (usage._digest('claude:msg_sample_repeat:req_sample_repeat'), 'claude', 'session',
                              stamp_text(NOON - timedelta(hours=2)), stamp_text(NOON - timedelta(hours=2)),
                              4, 90000, 300, 60, 90364, stamp_text(NOON)))
        with Memory(self.machine_path, read_only=True) as store:
            self.assertFalse(machine.has_usage_sessions(store))
            # A read only connection reads an old ledger without the migration.
            self.assertEqual(usage.sessions(store, 'claude', NOON - timedelta(days=7), NOON)['total'], 0)
        for _ in range(2):
            with Memory(self.machine_path) as store:
                machine.ensure_usage(store)
        columns = [row[1] for row in self.rows('PRAGMA table_info(usage_records)')]
        self.assertEqual((columns.count('session'), columns.count('context_tokens')), (1, 1))
        self.write('claude', 'resumed.jsonl',
                   claude_block('msg_sample_repeat', '2026-09-16T10:00:00Z', REPEATED),
                   claude_block('msg_sample_repeat', '2026-09-16T10:00:01Z', REPEATED, request=None, block='tool_use'))
        self.assertEqual(self.collect(projects=False)['records_added'], 0)
        rows = self.rows("SELECT total_tokens,session,context_tokens FROM usage_records WHERE host='claude'")
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0][0], rows[0][2]), (90364, 90304))
        self.assertTrue(DIGEST.fullmatch(rows[0][1]))
        self.assertEqual(self.summary()['claude']['sessions']['shown'][0]['turns'], 1)

    def test_codex_fresh_input_leaves_out_the_cached_input_counted_inside_its_input(self):
        self.write('codex', 'cached.jsonl', codex_meta('codex-session-sample'),
                   codex_count('2026-09-16T10:00:00Z', codex_usage(50000, 48000, 700, 200), codex_usage(50000, 48000, 700, 200)),
                   codex_count('2026-09-16T10:05:00Z', codex_usage(110000, 105000, 1000, 300),
                               codex_usage(60000, 57000, 300, 100)))
        self.collect(projects=False)
        window = self.summary()['codex']['windows']['last_5_hours']
        self.assertEqual({name: window[name] for name in ('fresh_input_tokens', 'cache_write_tokens', 'cache_read_tokens',
                                                           'output_tokens', 'reasoning_tokens', 'fresh_work_tokens')},
                         {'fresh_input_tokens': 5000, 'cache_write_tokens': 0, 'cache_read_tokens': 105000,
                          'output_tokens': 1000, 'reasoning_tokens': 300, 'fresh_work_tokens': 6000})
        found = self.summary()['codex']['sessions']
        self.assertEqual(found['total'], 1)
        session = found['shown'][0]
        self.assertEqual((session['turns'], session['largest_context_tokens'], session['cache_read_share']),
                         (2, 60000, round(105000 / 110000, 4)))
        self.assertEqual(session['session'], usage._digest('session:codex:codex-session-sample'))
        # The session digest is kept between collections, so a later report joins the same session.
        with (self.folders['codex'] / 'cached.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(codex_count('2026-09-16T10:10:00Z', codex_usage(170000, 160000, 1100, 300),
                                     codex_usage(60000, 55000, 100, 0)))
        self.collect(projects=False)
        self.assertEqual(self.summary()['codex']['sessions']['shown'][0]['turns'], 3)

    def test_routing_load_compares_fresh_work_and_leaves_out_cache_reads(self):
        def add(host, hours, fresh, reads, entry):
            with Memory(self.machine_path) as store:
                machine.ensure_usage(store)
                batch = usage.Batch(store, usage.new_stats())
                at = NOON - timedelta(hours=hours)
                batch.record(entry, host, 'session', at, at, usage.tokens(
                    {'input_tokens': fresh, 'cache_read_input_tokens': reads, 'output_tokens': 0}))
                with store._write():
                    batch.write()
        # Claude did the same fresh work as in earlier windows but read five times as much from the cache.
        for index, hours in enumerate((6, 12, 18)):
            add('claude', hours, 1000, 200000, 'claude-%d' % index)
        add('claude', 1, 1000, 1000000, 'claude-now')
        room = self.headroom('claude')
        self.assertEqual((room['fresh_work_5_hours'], room['median_fresh_work_5_hours'], room['relative_load']),
                         (1000, 1000, 1.0))
        rooms = hosts.ledger_headroom(['claude'], NOON)
        self.assertEqual(rooms['claude']['relative_load'], 1.0)

    def test_session_statistics_of_the_fixtures_are_counts_with_a_digest(self):
        self.folders = {'codex': self.root / 'logs' / 'codex' / 'sessions', 'claude': self.root / 'logs' / 'claude' / 'projects'}
        self.collect(projects=False)
        found = self.summary()['claude']['sessions']
        self.assertEqual(found['total'], 2)
        first, second = found['shown']
        # The first session holds two messages and the reply of its subagent; the second repeats one of them.
        self.assertEqual((first['turns'], first['largest_context_tokens'], first['cache_read_share']),
                         (3, 6020, round(11000 / 12031, 4)))
        self.assertEqual((second['turns'], second['largest_context_tokens'], second['cache_read_share']),
                         (1, 105, round(100 / 105, 4)))
        for item in found['shown']:
            self.assertTrue(DIGEST.fullmatch(item['session']))
            self.assertEqual(item['fresh_work_tokens'],
                             item['fresh_input_tokens'] + item['cache_write_tokens'] + item['output_tokens'])
        codex = self.summary()['codex']['sessions']['shown']
        self.assertEqual([(item['turns'], item['largest_context_tokens']) for item in codex], [(1, 4500), (2, 1000)])


class ReportTests(FreshFixture):
    def run_command(self, *arguments):
        environment = {'CODEX_HOME': str(self.root / 'logs' / 'codex'), 'CLAUDE_CONFIG_DIR': str(self.root / 'logs' / 'claude')}
        output = io.StringIO()
        # The sample logs are dated around NOON, so the report reads them at that fixed time and not at the real clock;
        # with the real clock the sessions left the seven day window on 22 September 2026 and the report counted one.
        with mock.patch.dict(os.environ, environment), mock.patch.object(usage, '_now', lambda now: NOON), redirect_stdout(output):
            self.assertEqual(cli_main(['usage', *arguments]), 0)
        return output.getvalue()

    def test_the_json_report_separates_every_figure_and_names_no_single_total(self):
        value = json.loads(self.run_command('--json'))
        claude = {item['host']: item for item in value['hosts']}['claude']
        window = claude['windows']['last_7_days']
        for name in ('fresh_input_tokens', 'cache_write_tokens', 'cache_read_tokens', 'output_tokens', 'reasoning_tokens',
                     'fresh_work_tokens', 'unseparated_tokens'):
            self.assertIsInstance(window[name], int, name)
        self.assertFalse({'total_tokens', 'input_tokens', 'cached_input_tokens', 'load_tokens'} & set(window))
        self.assertEqual(claude['sessions']['total'], 2)
        self.assertIn('fresh_work_5_hours', claude['headroom'])
        self.assertIn('Fresh work is fresh input plus cache writes plus output.', value['note'])

    def test_the_table_labels_fresh_work_and_shows_cache_reads_apart(self):
        text = self.run_command()
        header = next(line for line in text.splitlines() if line.startswith('Host') and 'Window' in line)
        for label in ('Fresh input', 'Cache writes', 'Output', 'Fresh work', 'Cache reads', 'Reasoning'):
            self.assertIn(label, header)
        self.assertIn('Fresh work is fresh input plus cache writes plus output.', text)
        self.assertIn('Cache read share', text)
        self.assertRegex(text, r'(?m)^claude\s+5 hours\s+')
        self.assertNotRegex(text, r' [–—-] ')


@unittest.skipUnless(POSIX, 'File modes are not used on Windows.')
class PermissionTests(FreshFixture):
    def setUp(self):
        super().setUp()
        self.secure_path = self.root / 'home' / machine.DEFAULT_DIRECTORY / machine.DEFAULT_FILE

    def mode(self, path):
        return stat.S_IMODE(path.stat().st_mode)

    def test_the_machine_folder_and_database_are_created_for_the_owner_only(self):
        machine.initialize(self.secure_path, name='Test machine')
        self.assertEqual(self.mode(self.secure_path.parent), 0o700)
        self.assertEqual(self.mode(self.secure_path), 0o600)
        with machine.writer(self.secure_path) as store:
            machine.ensure_usage(store)
            for suffix in ('-wal', '-shm'):
                companion = self.secure_path.with_name(self.secure_path.name + suffix)
                if companion.exists():
                    self.assertEqual(self.mode(companion), 0o600, suffix)
        self.assertEqual(machine.permissions(self.secure_path)['status'], 'owner_only')

    def test_wider_modes_are_reported_and_corrected_on_the_next_write(self):
        machine.initialize(self.secure_path, name='Test machine')
        self.secure_path.parent.chmod(0o755)
        self.secure_path.chmod(0o644)
        found = machine.permissions(self.secure_path)
        self.assertEqual(found['status'], 'too_wide')
        self.assertEqual(found['wider'], [{'item': 'folder', 'mode': '0755', 'expected': '0700'},
                                          {'item': 'database', 'mode': '0644', 'expected': '0600'}])
        usage.collect_machine(self.secure_path, folders=self.folders, projects=False)
        self.assertEqual((self.mode(self.secure_path.parent), self.mode(self.secure_path)), (0o700, 0o600))
        self.assertEqual(machine.permissions(self.secure_path), {'status': 'owner_only', 'wider': [],
                                                                 'note': 'Only the owner can read the machine memory.'})

    def test_a_database_outside_the_default_folder_keeps_the_mode_of_its_folder(self):
        self.machine_path.parent.chmod(0o755)
        self.machine_path.chmod(0o644)
        with machine.writer(self.machine_path):
            pass
        self.assertEqual((self.mode(self.machine_path.parent), self.mode(self.machine_path)), (0o755, 0o600))

    def test_doctor_reports_a_machine_memory_readable_by_other_users(self):
        machine.initialize(self.secure_path, name='Test machine')
        self.secure_path.chmod(0o644)
        folder = self.root / 'project'
        folder.mkdir()
        with mock.patch.dict(os.environ, {machine.DATABASE_VARIABLE: str(self.secure_path)}):
            info = install.setup(folder, _launcher=[sys.executable, '-m', 'memory_module.cli'],
                                 requirements=['Keep the invented notes local.'])
            self.secure_path.chmod(0o644)
            report = doctor(Path(info['database']))
        self.assertEqual(report['machine_permissions']['status'], 'too_wide')
        self.assertEqual(report['machine_permissions']['wider'], [{'item': 'database', 'mode': '0644', 'expected': '0600'}])


class WindowsPermissionTests(unittest.TestCase):
    def test_modes_are_not_checked_on_windows(self):
        target = Path('absent') / machine.DEFAULT_DIRECTORY / machine.DEFAULT_FILE
        with mock.patch.object(machine.os, 'name', 'nt'):
            self.assertEqual(machine.permissions(target)['status'], 'not_checked')
            machine.secure(target)


if __name__ == '__main__':
    unittest.main()
