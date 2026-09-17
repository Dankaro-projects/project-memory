"""The usage ledger of section 16.2: collectors, idempotent collection, windows, headroom and the usage command.

The fixtures under tests/fixtures/usage were written by hand from the structure of
the logs of each host, with invented values. They are stored with the ending
.fixture, because the repository ignores .jsonl files, and are copied into a
temporary folder with their real ending before each test.
"""
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import unittest
from unittest import mock

from memory_module import Memory, codex_host, hosts, machine, reviews, usage
from memory_module.cli import main as cli_main

FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'usage'
NOON = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
SENTINEL = 'sentinel'
PROJECT_NAME = 'Sentinel Harbour'
CODEX_RUN_METRICS = {'provider_usage': [{'input_tokens': 300, 'cached_input_tokens': 100, 'output_tokens': 50},
                                        {'input_tokens': 200, 'cached_input_tokens': 0, 'output_tokens': 30}],
                     'duration_ms': 600000}
CLAUDE_RUN_METRICS = {'provider_usage': {'input_tokens': 5, 'cache_creation_input_tokens': 10,
                                         'cache_read_input_tokens': 20, 'output_tokens': 7}, 'duration_ms': 42000}
DIGEST = re.compile(r'[0-9a-f]{32}')
STAMP = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}\+00:00')
ENUMS = set(usage.HOSTS) | set(usage.KINDS) | set(usage.SOURCES.values()) | set(usage.LIMIT_WINDOWS) | {usage.CURRENCY}


def copy_fixtures(target):
    """Copy the fixture tree and give every log its real ending."""
    for source in FIXTURES.rglob('*.fixture'):
        destination = target / source.relative_to(FIXTURES).with_suffix('.jsonl')
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)


class UsageFixture(unittest.TestCase):
    """A temporary machine memory and a copy of the log fixtures. This class defines no tests of its own."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        copy_fixtures(self.root / 'logs')
        self.folders = {'codex': self.root / 'logs' / 'codex' / 'sessions', 'claude': self.root / 'logs' / 'claude' / 'projects'}
        self.machine_path = self.root / 'machine' / 'machine.sqlite'
        patch = mock.patch.dict(os.environ, {machine.DATABASE_VARIABLE: str(self.machine_path)})
        patch.start()
        self.addCleanup(patch.stop)
        machine.initialize(self.machine_path, name='Test machine')

    def collect(self, **options):
        with Memory(self.machine_path) as store:
            return usage.collect(store, folders=self.folders, **options)

    def summary(self, now=NOON):
        with Memory(self.machine_path, read_only=True) as store:
            return {item['host']: item for item in usage.summary(store, now=now, zone=timezone.utc)['hosts']}

    def headroom(self, host, now=NOON):
        with Memory(self.machine_path, read_only=True) as store:
            return usage.host_headroom(store, host, now)

    def rows(self, sql, parameters=()):
        with Memory(self.machine_path, read_only=True) as store:
            return [tuple(row) for row in store.db.execute(sql, parameters).fetchall()]

    def codex_log(self):
        return self.folders['codex'] / '2026' / '09' / '16' / 'rollout-2026-09-16T08-00-00-sample.jsonl'

    def add_limit(self, host, percent, resets_at, observed_at=NOON):
        with Memory(self.machine_path) as store:
            machine.ensure_usage(store)
            batch = usage.Batch(store, usage.new_stats())
            batch.limit(host, {'limit_id': 'sample', 'primary': {'used_percent': percent, 'window_minutes': 300,
                                                                  'resets_at': resets_at}}, observed_at)
            with store._write():
                batch.write()

    def add_record(self, host, at, total, kind='session'):
        with Memory(self.machine_path) as store:
            machine.ensure_usage(store)
            batch = usage.Batch(store, usage.new_stats())
            batch.record(usage._digest(host + stamp_text(at) + str(total)), host, kind, at, at,
                         {'input_tokens': total, 'total_tokens': total})
            with store._write():
                batch.write()

    def add_hit(self, host, hit_at, until, reason='usage_limit'):
        with Memory(self.machine_path) as store:
            machine.ensure_usage(store)
            with store._write():
                store.db.execute('INSERT INTO usage_limit_hits (entry,host,reason,hit_at,until) VALUES (?,?,?,?,?)',
                                 (usage._digest(host + stamp_text(hit_at)), host, reason, stamp_text(hit_at),
                                  stamp_text(until) if until else None))


def stamp_text(value):
    return usage.stamp(value)


class ProjectFixture(UsageFixture):
    """A registered project with finished, running and unavailable runs. This class defines no tests of its own."""

    def setUp(self):
        super().setUp()
        self.project = self.root / 'harbour-work'
        database = self.project / '.memory' / 'project.sqlite'
        clock = lambda: '2026-09-16T11:00:00+00:00'
        memory = Memory.create(database, PROJECT_NAME, ['Keep the invented contract private.'], clock=clock)
        self.addCleanup(memory.close)
        codex_host.initialize(memory)
        with memory._write():
            for statement in machine._statements(reviews.SCHEMA):
                memory.db.execute(statement)
        episode = memory.start('Sentinel Harbour contract', 'Repair the invented parser.', 'code', 'The parser passes.')
        self.runs = {
            'run_codex_sample': ('codex', 'completed', '2026-09-16T10:30:00+00:00', '2026-09-16T10:40:00+00:00',
                                 CODEX_RUN_METRICS),
            'run_opencode_sample': ('opencode', 'completed', '2026-09-16T09:00:00+00:00', '2026-09-16T09:10:00+00:00',
                                    {'provider_usage': None, 'duration_ms': 600000}),
            'run_claude_sample': ('claude', 'failed', '2026-09-16T11:00:00+00:00', '2026-09-16T11:05:00+00:00',
                                  CLAUDE_RUN_METRICS),
            'run_running_sample': ('claude', 'running', '2026-09-16T11:50:00+00:00', '2026-09-16T11:55:00+00:00',
                                   CLAUDE_RUN_METRICS),
        }
        with memory._write():
            for run_id, (host, state, created, updated, metrics) in self.runs.items():
                memory.db.execute(
                    'INSERT INTO review_runs (id,episode_id,role,signature,host,session_id,state,created_at,updated_at,'
                    'request_key,snapshot,report,metrics,error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (run_id, episode['id'], 'outcome', 'signature', host, 'session', state, created, updated,
                     'key-' + run_id, json.dumps({'text': 'SENTINEL-CONTENT-7431 ' + PROJECT_NAME}),
                     json.dumps({'summary': 'SENTINEL-CONTENT-7431'}), json.dumps(metrics),
                     'SENTINEL-CONTENT-7431 error text of ' + str(self.project)))
        events = database.parent / 'agent-runs' / 'run_opencode_sample' / 'output.jsonl'
        events.parent.mkdir(parents=True)
        shutil.copyfile(FIXTURES / 'opencode' / 'output.fixture', events)
        hosts.mark_unavailable(memory, 'claude', 'usage_limit', '2026-09-16T14:00:00+00:00')
        memory.close()
        with Memory(self.machine_path) as store:
            machine.register(store, path=str(self.project), template=None, phase={'phase': 'development', 'at': None})


class CollectorTests(UsageFixture):
    """Each log format is read into numbers and times, incrementally and once."""

    def test_codex_token_counts_add_the_growth_of_the_session_total_and_skip_repeated_reports(self):
        stats = self.collect(projects=False)
        rows = self.rows("SELECT ended_at,input_tokens,cached_input_tokens,output_tokens,reasoning_tokens,total_tokens"
                         " FROM usage_records WHERE host='codex' ORDER BY ended_at")
        self.assertEqual([row[5] for row in rows], [5000, 1100, 500])
        self.assertEqual(rows[2][1:], (400, 100, 100, 20, 500))
        self.assertEqual(stats['malformed_lines'], 2)
        windows = self.summary()['codex']['windows']
        self.assertEqual(windows['last_5_hours']['total_tokens'], 1600)
        self.assertEqual(windows['today']['total_tokens'], 1600)
        self.assertEqual(windows['last_7_days']['total_tokens'], 6600)

    def test_codex_limits_keep_the_latest_observation_of_each_window(self):
        self.collect(projects=False)
        limits = {item['window']: item for item in self.summary()['codex']['limits']}
        self.assertEqual(limits['primary']['used_percent'], 45.0)
        self.assertEqual(limits['primary']['window_minutes'], 300)
        self.assertEqual(limits['primary']['resets_at'], '2026-09-16T13:00:00.000000+00:00')
        self.assertEqual(limits['secondary']['used_percent'], 14.0)
        self.assertEqual(limits['secondary']['window_minutes'], 10080)
        self.assertEqual(limits['primary']['limit_id'], 'codex_sample')
        self.assertFalse(limits['primary']['expired'])

    def test_a_partial_last_line_waits_until_it_is_complete(self):
        self.collect(projects=False)
        self.assertEqual(self.rows("SELECT count(*) FROM usage_records WHERE host='codex'"), [(3,)])
        with self.codex_log().open('ab') as stream:
            stream.write(b'\n')
        stats = self.collect(projects=False)
        self.assertEqual(stats['files_read'], 1)
        self.assertEqual(stats['records_added'], 1)
        self.assertLess(stats['bytes_read'], 1000)
        self.assertEqual(self.summary()['codex']['windows']['last_5_hours']['total_tokens'], 2200)
        # The completed report carries no secondary window, so the secondary window of the earlier report is dropped.
        self.assertEqual({item['window']: item['used_percent'] for item in self.summary()['codex']['limits']},
                         {'primary': 50.0})

    def test_claude_keeps_the_largest_report_of_a_message_and_records_a_repeated_message_once(self):
        self.collect(projects=False)
        rows = self.rows("SELECT ended_at,input_tokens,cached_input_tokens,cache_write_tokens,output_tokens,"
                         "reasoning_tokens,total_tokens FROM usage_records WHERE host='claude' ORDER BY ended_at")
        self.assertEqual([row[6] for row in rows], [120, 100, 6260, 6100])
        self.assertEqual(rows[2][1:], (10, 5000, 1000, 250, 30, 6260))
        windows = self.summary()['claude']['windows']
        self.assertEqual(windows['last_5_hours']['total_tokens'], 12360)
        self.assertEqual(windows['today']['total_tokens'], 12460)
        self.assertEqual(windows['last_7_days']['total_tokens'], 12580)
        self.assertEqual(self.summary()['claude']['limits'], [])

    def test_a_second_collection_changes_nothing_and_opens_no_unchanged_file(self):
        first = self.collect(projects=False)
        before = self.rows('SELECT * FROM usage_records ORDER BY entry')
        second = self.collect(projects=False)
        self.assertEqual(first['files_seen'], 5)
        self.assertEqual(second['files_unchanged'], 5)
        self.assertEqual((second['files_read'], second['bytes_read'], second['records_added'],
                          second['records_written'], second['limits_written']), (0, 0, 0, 0, 0))
        self.assertEqual(self.rows('SELECT * FROM usage_records ORDER BY entry'), before)

    def test_a_copied_or_rewritten_log_is_read_again_without_counting_its_entries_twice(self):
        self.collect(projects=False)
        before = self.rows('SELECT entry,total_tokens FROM usage_records ORDER BY entry')
        session = self.folders['claude'] / '-srv-example-sentinel-harbour' / 'session-two.jsonl'
        shutil.copyfile(session, session.with_name('session-three.jsonl'))
        shutil.copyfile(self.codex_log(), self.codex_log().with_name('rollout-copy.jsonl'))
        # Rewriting a file in place keeps its identity but changes its first bytes, so it is read from the start.
        exec_log = self.codex_log().with_name('rollout-z-exec-sample.jsonl')
        exec_log.write_bytes(b'{"type":"compacted"}\n' + exec_log.read_bytes())
        stats = self.collect(projects=False)
        self.assertEqual(stats['files_read'], 3)
        self.assertEqual(stats['files_restarted'], 1)
        self.assertEqual(stats['records_added'], 0)
        self.assertEqual(self.rows('SELECT entry,total_tokens FROM usage_records ORDER BY entry'), before)

    def test_a_missing_folder_is_reported_and_does_not_stop_the_other_host(self):
        self.folders['codex'] = self.root / 'absent'
        stats = self.collect(projects=False)
        self.assertEqual(stats['folders'], {'codex': 'missing', 'claude': 'read'})
        self.assertEqual(self.rows("SELECT count(*) FROM usage_records WHERE host='claude'"), [(4,)])

    def test_opencode_events_sum_tokens_and_cost_and_skip_malformed_lines(self):
        with (FIXTURES / 'opencode' / 'output.fixture').open('rb') as stream:
            found = usage.opencode_usage(stream)
        self.assertEqual(found['steps'], 2)
        self.assertEqual(found['malformed_lines'], 1)
        self.assertAlmostEqual(found['cost'], 0.0163)
        self.assertEqual(found['tokens'], {'input_tokens': 1700, 'cached_input_tokens': 150, 'cache_write_tokens': 0,
                                           'output_tokens': 110, 'reasoning_tokens': 40, 'total_tokens': 2000})
        top_level = ['{"type":"step_finish","tokens":{"input":3,"output":4},"cost":0.5}', 'not json with step', '']
        self.assertEqual(usage.opencode_usage(top_level)['tokens']['total_tokens'], 7)
        self.assertEqual(usage.opencode_usage(['{"type":"text","part":{"text":"step"}}'])['tokens'], None)

    def test_token_shapes_of_each_host(self):
        self.assertEqual(usage.tokens({'input_tokens': 10, 'cached_input_tokens': 4, 'output_tokens': 2})['total_tokens'], 12)
        self.assertEqual(usage.tokens({'input_tokens': 10, 'cache_read_input_tokens': 4, 'output_tokens': 2})['total_tokens'], 16)
        self.assertEqual(usage.tokens({'input': 1, 'output': 2, 'cache': {'read': 3, 'write': 4}})['total_tokens'], 10)
        self.assertEqual(usage.tokens([{'input_tokens': 1, 'output_tokens': 1}, None, 'text'])['total_tokens'], 2)
        self.assertIsNone(usage.tokens({'input_tokens': True, 'output_tokens': -3}))
        self.assertIsNone(usage.tokens('text'))


class RunCollectionTests(ProjectFixture):
    """Runs of registered projects and their limit hits enter the ledger once."""

    def test_finished_runs_record_tokens_cost_and_duration_and_a_running_run_waits(self):
        stats = self.collect()
        self.assertEqual((stats['projects_seen'], stats['runs_recorded'], stats['limit_hits_recorded']), (1, 3, 1))
        rows = {row[0]: row[1:] for row in self.rows(
            "SELECT host,started_at,ended_at,total_tokens,cost,currency,duration_ms FROM usage_records WHERE kind='run'")}
        self.assertEqual(rows['codex'], ('2026-09-16T10:30:00.000000+00:00', '2026-09-16T10:40:00.000000+00:00', 580,
                                         None, None, 600000))
        self.assertEqual(rows['opencode'][2:], (2000, 0.0163, 'USD', 600000))
        self.assertEqual(rows['claude'][2:], (42, None, None, 42000))
        summary = self.summary()
        self.assertEqual(summary['opencode']['windows']['last_7_days']['cost'], {'USD': 0.0163})
        self.assertEqual(summary['codex']['windows']['last_5_hours']['run_records'], 1)
        self.assertTrue(summary['opencode']['measured']['cost'])
        self.assertEqual(summary['claude']['limit_hit'], {'reason': 'usage_limit', 'hit_at': '2026-09-16T11:00:00.000000+00:00',
                                                          'until': '2026-09-16T14:00:00.000000+00:00', 'active': True})
        again = self.collect()
        self.assertEqual((again['runs_recorded'], again['limit_hits_recorded'], again['records_added']), (0, 0, 0))

    def test_a_run_that_finishes_later_is_recorded_on_the_next_collection(self):
        self.collect()
        with Memory(self.project / '.memory' / 'project.sqlite') as memory:
            with memory._write():
                memory.db.execute("UPDATE review_runs SET state='completed' WHERE id='run_running_sample'")
        self.assertEqual(self.collect()['runs_recorded'], 1)
        self.assertEqual(self.rows("SELECT count(*) FROM usage_records WHERE kind='run' AND host='claude'"), [(2,)])

    def test_run_metrics_without_an_event_log_supply_tokens_and_cost(self):
        steps = [{'tokens': {'total': 90, 'input': 80, 'output': 10, 'reasoning': 0, 'cache': {'read': 0, 'write': 0}},
                  'cost': 0.25}, {'tokens': None, 'cost': 0.5}]
        with Memory(self.project / '.memory' / 'project.sqlite') as memory:
            with memory._write():
                memory.db.execute(
                    "INSERT INTO review_runs (id,episode_id,role,signature,host,session_id,state,created_at,updated_at,"
                    "request_key,snapshot,metrics) SELECT 'run_metrics_sample',episode_id,role,signature,'opencode',session_id,"
                    "'completed',created_at,updated_at,'key-metrics',snapshot,? FROM review_runs WHERE id='run_codex_sample'",
                    (json.dumps({'provider_usage': steps, 'duration_ms': 5}),))
        self.collect()
        self.assertEqual(self.rows("SELECT total_tokens,cost,currency FROM usage_records WHERE kind='run' AND duration_ms=5"),
                         [(90, 0.75, 'USD')])
        self.assertEqual(usage.tokens({'inputTokens': 3, 'outputTokens': 4})['total_tokens'], 7)

    def test_a_registered_project_without_a_database_is_counted_and_skipped(self):
        with Memory(self.machine_path) as store:
            machine.register(store, path=str(self.root / 'gone'), template=None, phase={'phase': 'development', 'at': None})
        stats = self.collect()
        self.assertEqual((stats['projects_seen'], stats['projects_without_database'], stats['runs_recorded']), (2, 1, 3))

    def test_the_machine_database_holds_no_content_path_project_name_or_session_title(self):
        other_tables = self.non_ledger_rows()
        self.collect()
        with self.codex_log().open('ab') as stream:
            stream.write(b'\n')
        self.collect()
        self.assertEqual(self.non_ledger_rows(), other_tables, 'Collection changed a table outside the usage ledger.')
        with Memory(self.machine_path, read_only=True) as store:
            tables = [row[0] for row in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            checked = 0
            for table in tables:
                columns = [row[1] for row in store.db.execute('PRAGMA table_info(' + table + ')')]
                for row in store.db.execute('SELECT * FROM ' + table):
                    for column, value in zip(columns, row):
                        if isinstance(value, bytes):
                            value = value.decode('utf-8', errors='replace')
                        if isinstance(value, str):
                            # No table of the machine memory carries text from the logs or the project.
                            self.assertNotIn(SENTINEL, value.lower(), (table, column))
                        if table in machine.USAGE_TABLES:
                            self.assert_ledger_value(table, column, value)
                            checked += 1
            self.assertGreater(checked, 150)

    def assert_ledger_value(self, table, column, value):
        where = (table, column)
        if value is None or isinstance(value, (int, float)):
            return
        self.assertIsInstance(value, str, where)
        self.assertLessEqual(len(value), 64, where)
        for mark in ('/', '\\', ' ', '@', '~', 'harbour', 'example', 'msg_', 'req_'):
            self.assertNotIn(mark, value.lower(), where)
        if column == 'state':
            state = json.loads(value)
            self.assertLessEqual(set(state), {'total'}, where)
            self.assertTrue(all(isinstance(item, int) for item in state.get('total', [])), where)
            return
        allowed = (DIGEST.fullmatch(value) or STAMP.fullmatch(value) or value in ENUMS
                   or (column == 'limit_id' and usage.LIMIT_IDENTIFIER.fullmatch(value))
                   or (column == 'reason' and usage.REASON.fullmatch(value)))
        self.assertTrue(allowed, where)

    def non_ledger_rows(self):
        with Memory(self.machine_path, read_only=True) as store:
            tables = [row[0] for row in store.db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            return {table: sorted(map(repr, map(tuple, store.db.execute('SELECT * FROM ' + table))))
                    for table in tables if table not in machine.USAGE_TABLES}


class WindowTests(UsageFixture):
    """Totals per window at their boundaries, and headroom for routing."""

    def test_windows_count_a_record_after_the_start_and_up_to_now(self):
        self.add_record('grok', NOON - timedelta(hours=5), 7)
        self.add_record('grok', NOON - timedelta(hours=5) + timedelta(microseconds=1), 11)
        self.add_record('grok', NOON, 13)
        self.add_record('grok', NOON + timedelta(microseconds=1), 17)
        self.add_record('grok', datetime(2026, 9, 16, 0, 0, tzinfo=timezone.utc), 19)
        self.add_record('grok', NOON - timedelta(days=7), 23)
        self.add_record('grok', NOON - timedelta(days=7) + timedelta(seconds=1), 29)
        windows = self.summary()['grok']['windows']
        self.assertEqual(windows['last_5_hours']['total_tokens'], 24)
        self.assertEqual(windows['today']['total_tokens'], 50)
        self.assertEqual(windows['last_7_days']['total_tokens'], 79)
        self.assertEqual(windows['last_7_days']['session_records'], 5)

    def test_the_day_starts_at_local_midnight_of_the_given_zone(self):
        self.add_record('grok', datetime(2026, 9, 15, 23, 30, tzinfo=timezone.utc), 5)
        zone = timezone(timedelta(hours=2))
        with Memory(self.machine_path, read_only=True) as store:
            hosts_found = {item['host']: item for item in usage.summary(store, now=NOON, zone=zone)['hosts']}
        self.assertEqual(hosts_found['grok']['windows']['today']['total_tokens'], 5)
        self.assertEqual(hosts_found['grok']['windows']['today']['from'], '2026-09-15T22:00:00.000000+00:00')
        self.assertEqual(self.summary()['grok']['windows']['today']['total_tokens'], 0)

    def test_a_host_is_constrained_at_85_percent_until_its_window_resets(self):
        self.add_limit('codex', 84.9, int((NOON + timedelta(hours=1)).timestamp()))
        self.assertFalse(self.headroom('codex')['constrained'])
        self.add_limit('codex', 85.0, int((NOON + timedelta(hours=1)).timestamp()), observed_at=NOON + timedelta(seconds=1))
        found = self.headroom('codex')
        self.assertTrue(found['constrained'])
        self.assertEqual((found['reasons'], found['used_percent']), (['used_percent'], 85.0))
        expired = self.headroom('codex', now=NOON + timedelta(hours=1))
        self.assertFalse(expired['constrained'])
        self.assertIsNone(expired['used_percent'])

    def test_an_older_limit_report_does_not_replace_a_newer_one(self):
        self.add_limit('codex', 40.0, int((NOON + timedelta(hours=1)).timestamp()))
        self.add_limit('codex', 95.0, int((NOON + timedelta(hours=1)).timestamp()), observed_at=NOON - timedelta(hours=1))
        self.assertEqual(self.headroom('codex')['used_percent'], 40.0)

    def test_a_limit_hit_constrains_until_its_reset_time_or_for_an_hour_without_one(self):
        self.add_hit('claude', NOON - timedelta(minutes=10), NOON + timedelta(minutes=5))
        self.assertEqual(self.headroom('claude')['reasons'], ['limit_hit'])
        self.assertFalse(self.headroom('claude', now=NOON + timedelta(minutes=5))['constrained'])
        self.add_hit('grok', NOON - timedelta(minutes=59), None)
        self.assertTrue(self.headroom('grok')['constrained'])
        self.assertFalse(self.headroom('grok', now=NOON + timedelta(minutes=1))['constrained'])

    def test_relative_load_compares_the_last_5_hours_with_the_median_of_the_week(self):
        self.add_record('codex', NOON - timedelta(hours=1), 300)
        self.add_record('codex', NOON - timedelta(hours=6), 100)
        self.add_record('codex', NOON - timedelta(hours=26), 200)
        self.add_record('codex', NOON - timedelta(hours=51), 400)
        found = self.headroom('codex')
        self.assertEqual((found['tokens_5_hours'], found['median_5_hours'], found['relative_load']), (300, 250, 1.2))
        self.assertIsNone(self.headroom('claude')['tokens_5_hours'])
        # A host with no recorded use in the last 5 hours has a load of zero, so it ranks before a loaded host.
        self.assertEqual(self.headroom('claude')['relative_load'], 0.0)

    def test_reads_work_without_a_ledger_and_without_a_machine_memory(self):
        found = self.summary()
        self.assertFalse(found['codex']['measured']['tokens'])
        self.assertEqual(self.rows("SELECT count(*) FROM sqlite_master WHERE name='usage_records'"), [(0,)])
        for path in self.machine_path.parent.glob('machine.sqlite*'):
            path.unlink()
        values = usage.headroom(now=NOON)
        self.assertEqual(set(values), set(usage.HOSTS))
        self.assertFalse(any(item['constrained'] for item in values.values()))


def codex_line(at, total, last=None, rate_limits=None):
    """One Codex token_count event with invented values."""
    usage_of = lambda value: {'input_tokens': value, 'cached_input_tokens': 0, 'output_tokens': 0,
                              'reasoning_output_tokens': 0, 'total_tokens': value}
    return json.dumps({'timestamp': at, 'type': 'event_msg',
                       'payload': {'type': 'token_count', 'info': {'total_token_usage': usage_of(total),
                                                                   'last_token_usage': usage_of(last or total)},
                                   'rate_limits': rate_limits}}) + '\n'


def claude_line(identifier, at, usage_value):
    """One Claude Code assistant message with invented values."""
    return json.dumps({'type': 'assistant', 'timestamp': at, 'requestId': 'req-' + identifier,
                       'message': {'id': identifier, 'model': 'sample', 'usage': usage_value}}) + '\n'


class LedgerRegressionTests(UsageFixture):
    """Malformed values, forked sessions, limit windows and limit hits that once stopped or misled the ledger."""

    def setUp(self):
        super().setUp()
        self.folders = {'codex': self.root / 'fresh' / 'codex', 'claude': self.root / 'fresh' / 'claude'}
        for folder in self.folders.values():
            folder.mkdir(parents=True)

    def write(self, host, name, *lines):
        path = self.folders[host] / name
        path.write_text(''.join(lines), encoding='utf-8')
        return path

    def test_a_token_count_too_large_for_sqlite_is_skipped_and_collection_continues(self):
        self.write('claude', 'huge.jsonl', claude_line('msg_huge', '2026-09-16T10:00:00Z', {'input_tokens': 10 ** 20}),
                   claude_line('msg_fine', '2026-09-16T10:01:00Z', {'input_tokens': 40, 'output_tokens': 2}))
        self.write('codex', 'float.jsonl', codex_line('2026-09-16T10:00:00Z', 1e300))
        for _ in range(2):
            stats = self.collect(projects=False)
            self.assertEqual(stats['files_unreadable'], 0)
        self.assertEqual(self.rows('SELECT host,total_tokens FROM usage_records ORDER BY host'), [('claude', 42)])
        self.assertEqual(self.rows('SELECT count(*) FROM usage_files'), [(2,)])
        with mock.patch.object(usage, 'default_folders', return_value=self.folders):
            self.assertIsNotNone(usage.collect_after_run())
            with redirect_stdout(io.StringIO()):
                self.assertEqual(cli_main(['usage', '--json']), 0)

    def test_totals_that_exceed_the_integers_of_sqlite_are_still_summed(self):
        with Memory(self.machine_path) as store, store._write():
            machine.ensure_usage(store)
            for index in range(2):
                store.db.execute("INSERT INTO usage_records (entry,host,kind,started_at,ended_at,total_tokens,recorded_at)"
                                 " VALUES (?,'claude','session',?,?,?,?)", ('large-%d' % index, stamp_text(NOON), stamp_text(NOON),
                                                                            2 ** 62, stamp_text(NOON)))
        self.assertEqual(self.summary()['claude']['windows']['last_5_hours']['total_tokens'], 2 ** 63)
        self.assertIsNotNone(hosts.ledger_headroom(['claude', 'codex'], NOON))

    def test_a_time_at_the_edge_of_the_date_range_is_treated_as_missing(self):
        self.assertIsNone(usage.moment('0001-01-01T00:00:00+05:00'))
        self.assertIsNone(usage.moment('9999-12-31T23:59:59-05:00'))
        self.write('claude', 'edge.jsonl', claude_line('msg_edge', '0001-01-01T00:00:00+05:00', {'input_tokens': 5}))
        limits = {'limit_id': 'codex', 'primary': {'used_percent': 10.0, 'window_minutes': 300,
                                                   'resets_at': '9999-12-31T23:59:59-05:00'}}
        self.write('codex', 'edge.jsonl', codex_line('2026-09-16T10:00:00Z', 70, rate_limits=limits))
        for _ in range(2):
            self.assertEqual(self.collect(projects=False)['files_unreadable'], 0)
        self.assertEqual(self.rows('SELECT host,total_tokens FROM usage_records'), [('codex', 70)])
        self.assertEqual(self.rows('SELECT used_percent,resets_at FROM usage_limits'), [(10.0, None)])

    def test_a_forked_codex_session_that_replays_its_parent_is_counted_once(self):
        self.write('codex', 'parent.jsonl', codex_line('2026-09-16T08:00:00Z', 1100),
                   codex_line('2026-09-16T08:05:00Z', 3300, last=2200))
        self.write('codex', 'z-fork.jsonl', codex_line('2026-09-16T09:00:00Z', 1100),
                   codex_line('2026-09-16T09:00:00Z', 3300, last=2200), codex_line('2026-09-16T09:10:00Z', 3850, last=550))
        self.collect(projects=False)
        self.assertEqual(self.rows("SELECT sum(total_tokens),count(*) FROM usage_records WHERE host='codex'"), [(3850, 3)])

    def test_a_limit_observation_from_a_wrong_clock_does_not_lock_the_limit(self):
        with Memory(self.machine_path) as store, store._write():
            machine.ensure_usage(store)
            store.db.execute("INSERT INTO usage_limits VALUES ('codex','sample','primary',99,300,NULL,"
                             "'2099-01-01T00:00:00.000000+00:00')")
        self.assertFalse(self.headroom('codex')['constrained'])
        self.add_limit('codex', 3.0, int((NOON + timedelta(hours=2)).timestamp()), observed_at=NOON - timedelta(minutes=1))
        self.assertEqual(self.rows('SELECT used_percent FROM usage_limits'), [(3.0,)])
        # A new report from a wrong clock is not stored at all.
        self.add_limit('codex', 97.0, None, observed_at=datetime(2099, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(self.rows('SELECT used_percent FROM usage_limits'), [(3.0,)])

    def test_a_window_without_a_reset_time_expires_after_its_length(self):
        self.add_limit('codex', 95.0, None, observed_at=NOON - timedelta(days=15))
        self.assertFalse(self.headroom('codex')['constrained'])
        self.add_limit('codex', 95.0, None, observed_at=NOON - timedelta(hours=4))
        self.assertTrue(self.headroom('codex')['constrained'])
        self.assertFalse(self.headroom('codex', now=NOON + timedelta(hours=1))['constrained'])

    def test_a_window_that_the_latest_report_no_longer_carries_does_not_constrain(self):
        early = {'limit_id': 'codex', 'primary': {'used_percent': 20.0, 'window_minutes': 300, 'resets_at': None},
                 'secondary': {'used_percent': 90.0, 'window_minutes': 10080,
                               'resets_at': int((NOON + timedelta(days=5)).timestamp())}}
        later = {'limit_id': 'codex', 'primary': {'used_percent': 30.0, 'window_minutes': 10080, 'resets_at': None},
                 'secondary': None}
        self.write('codex', 'shape.jsonl', codex_line('2026-09-16T09:00:00Z', 10, rate_limits=early),
                   codex_line('2026-09-16T11:00:00Z', 20, last=10, rate_limits=later))
        self.collect(projects=False)
        found = self.headroom('codex')
        self.assertEqual((found['constrained'], found['used_percent']), (False, 30.0))

    def test_a_model_limit_does_not_decide_while_the_general_limit_is_reported(self):
        self.add_limit('codex', 10.0, int((NOON + timedelta(hours=2)).timestamp()))
        with Memory(self.machine_path) as store, store._write():
            store.db.execute("UPDATE usage_limits SET limit_id='codex'")
        self.add_limit('codex', 95.0, int((NOON + timedelta(hours=2)).timestamp()))
        found = self.headroom('codex')
        self.assertEqual((found['constrained'], found['used_percent']), (False, 10.0))
        # Without the general limit, the model limit still constrains.
        with Memory(self.machine_path) as store, store._write():
            store.db.execute("DELETE FROM usage_limits WHERE limit_id='codex'")
        self.assertTrue(self.headroom('codex')['constrained'])

    def test_an_expired_later_hit_does_not_hide_an_earlier_hit_that_has_not_reset(self):
        self.add_hit('codex', NOON - timedelta(hours=4), datetime(2026, 9, 20, tzinfo=timezone.utc))
        self.add_hit('codex', NOON - timedelta(hours=2), None)
        found = self.headroom('codex')
        self.assertTrue(found['constrained'])
        self.assertEqual(found['limit_hit']['until'], '2026-09-20T00:00:00.000000+00:00')

    def test_an_idle_host_has_a_load_of_zero(self):
        for hours in (6, 12, 18):
            self.add_record('grok', NOON - timedelta(hours=hours), 1000)
        self.assertEqual(self.headroom('grok')['relative_load'], 0.0)
        self.assertEqual(self.headroom('opencode')['relative_load'], 0.0)
        self.add_record('grok', NOON - timedelta(hours=1), 10000)
        self.assertEqual(self.headroom('grok')['relative_load'], 10.0)


class HostAvailableTests(ProjectFixture):
    """A host that a project marks available again is no longer constrained by its earlier limit hit."""

    def test_marking_a_host_available_ends_its_limit_hit_in_the_ledger(self):
        self.collect()
        self.assertEqual(self.headroom('claude')['reasons'], ['limit_hit'])
        database = self.project / '.memory' / 'project.sqlite'
        with Memory(database, clock=lambda: '2026-09-16T11:30:00+00:00') as memory:
            hosts.mark_available(memory, 'claude')
            self.assertTrue(hosts.availability(memory, 'claude')['available'])
        self.collect()
        found = self.headroom('claude')
        self.assertFalse(found['constrained'])
        self.assertEqual(self.summary()['claude']['limit_hit']['active'], False)
        # A later hit constrains again.
        with Memory(database, clock=lambda: '2026-09-16T11:45:00+00:00') as memory:
            hosts.mark_unavailable(memory, 'claude', 'usage_limit', '2026-09-16T15:00:00+00:00')
        self.collect()
        self.assertEqual(self.headroom('claude')['reasons'], ['limit_hit'])


class CommandTests(UsageFixture):
    """project-memory usage collects first and prints a table or JSON."""

    def run_command(self, *arguments):
        environment = {'CODEX_HOME': str(self.root / 'logs' / 'codex'), 'CLAUDE_CONFIG_DIR': str(self.root / 'logs' / 'claude')}
        output = io.StringIO()
        with mock.patch.dict(os.environ, environment), redirect_stdout(output):
            code = cli_main(['usage', *arguments])
        self.assertEqual(code, 0)
        return output.getvalue()

    def test_json_output_reports_every_host_with_measured_and_unavailable_data(self):
        value = json.loads(self.run_command('--json'))
        self.assertEqual([item['host'] for item in value['hosts']], list(usage.HOSTS))
        self.assertEqual(value['collection']['files_read'], 5)
        found = {item['host']: item for item in value['hosts']}
        self.assertTrue(found['codex']['measured']['limit_state'])
        self.assertFalse(found['grok']['measured']['tokens'])
        self.assertIn('No tokens were recorded for this host on this machine.', found['grok']['unavailable'])
        self.assertEqual(json.loads(self.run_command('--json'))['collection']['records_added'], 0)

    def test_the_table_names_each_host_and_uses_plain_sentences(self):
        text = self.run_command()
        for host in usage.HOSTS:
            self.assertRegex(text, r'(?m)^' + host + r'\s')
        self.assertIn('unavailable', text)
        self.assertNotRegex(text, r' [–—-] ')
        self.assertNotIn(SENTINEL, text.lower())

    def test_the_command_creates_the_machine_memory_when_it_is_missing(self):
        for path in self.machine_path.parent.glob('machine.sqlite*'):
            path.unlink()
        value = json.loads(self.run_command('--json'))
        self.assertTrue(value['collection']['machine_memory_created'])
        self.assertTrue(value['ledger'])


if __name__ == '__main__':
    unittest.main()
