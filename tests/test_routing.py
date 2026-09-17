"""Routing by headroom (section 16.3). No host process runs, and the machine memory stays in a temporary folder."""
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from memory_module import Memory, InvalidRecord, api, delegation, hosts, machine, reviews
from memory_module.install import setup
from memory_module.workspace import action

NOW = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self):
        self.value = NOW

    def __call__(self):
        return self.value.isoformat()


def stamp(value):
    return value.astimezone(timezone.utc).isoformat(timespec='microseconds')


class LedgerFixture(unittest.TestCase):
    """A project memory with a fixed clock, every host installed, and an empty usage ledger in a temporary machine memory."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.machine_path = self.root / 'machine' / 'machine.sqlite'
        clean = {name: '' for name in hosts.ENVIRONMENT.values()}
        environment = patch.dict(os.environ, {machine.DATABASE_VARIABLE: str(self.machine_path), **clean})
        environment.start()
        self.addCleanup(environment.stop)
        which = patch.object(hosts.shutil, 'which', side_effect=lambda name: '/usr/bin/' + name)
        which.start()
        self.addCleanup(which.stop)
        machine.initialize(self.machine_path, name='Test machine')
        with Memory(self.machine_path) as store:
            machine.ensure_usage(store)
        self.clock = Clock()
        self.memory = Memory.create(self.root / 'project' / '.memory' / 'project.sqlite', 'Routing', ['Keep history.'],
                                    clock=self.clock)
        self.addCleanup(self.memory.close)

    def add_limit(self, host, percent, resets_at, limit_id='codex', window='primary'):
        with Memory(self.machine_path) as store, store._write():
            store.db.execute('INSERT OR REPLACE INTO usage_limits (host,limit_id,window,used_percent,window_minutes,resets_at,observed_at)'
                             ' VALUES (?,?,?,?,?,?,?)', (host, limit_id, window, percent, 300, stamp(resets_at), stamp(NOW)))

    def add_hit(self, host, hit_at, until=None, entry=None):
        with Memory(self.machine_path) as store, store._write():
            store.db.execute('INSERT INTO usage_limit_hits (entry,host,reason,hit_at,until) VALUES (?,?,?,?,?)',
                             (entry or host + stamp(hit_at), host, 'usage_limit', stamp(hit_at), stamp(until) if until else None))

    def add_record(self, host, ended_at, total, entry):
        with Memory(self.machine_path) as store, store._write():
            store.db.execute('INSERT INTO usage_records (entry,host,kind,started_at,ended_at,total_tokens,recorded_at)'
                             " VALUES (?,?,'session',?,?,?,?)", (entry, host, stamp(ended_at), stamp(ended_at), total, stamp(NOW)))


class RouteTests(LedgerFixture):
    def test_the_preferred_host_is_kept_when_it_is_not_constrained(self):
        self.add_limit('codex', 84.5, NOW + timedelta(hours=2))
        decision = hosts.route(self.memory, 'codex', allowed=['codex', 'claude'])
        self.assertEqual((decision['host'], decision['reason']), ('codex', 'preferred'))
        self.assertTrue(decision['ledger_read'])
        self.assertEqual(decision['sentence'], 'The preferred host codex was chosen because it is not constrained.')
        self.assertEqual(decision['decided_at'], stamp(NOW))
        self.assertEqual([option['host'] for option in decision['hosts']], ['codex', 'claude'])
        self.assertEqual(hosts.choose(self.memory, 'codex', allowed=['codex', 'claude']), 'codex')

    def test_a_host_at_85_percent_in_an_unexpired_window_is_constrained(self):
        self.add_limit('codex', 85, NOW + timedelta(hours=2))
        decision = hosts.route(self.memory, 'codex', allowed=['codex', 'claude'])
        self.assertEqual((decision['host'], decision['reason']), ('claude', 'headroom'))
        self.assertEqual(decision['sentence'],
                         'The preferred host codex was not chosen because its reported use is 85 percent of a window that '
                         'resets at 2026-09-17 14:00 UTC. The claude host was chosen because it has the most headroom among '
                         'the hosts that are not constrained.')
        codex = decision['hosts'][0]
        self.assertEqual((codex['constrained'], codex['reasons'], codex['used_percent']), (True, ['used_percent'], 85))

    def test_an_expired_window_and_a_passed_limit_hit_do_not_constrain(self):
        self.add_limit('codex', 99, NOW - timedelta(seconds=1))
        self.add_hit('codex', NOW - timedelta(hours=3), until=NOW - timedelta(minutes=1))
        self.assertEqual(hosts.route(self.memory, 'codex', allowed=['codex', 'claude'])['reason'], 'preferred')
        # A window whose reset time is exactly now has reset.
        self.add_limit('codex', 99, NOW)
        self.assertEqual(hosts.route(self.memory, 'codex', allowed=['codex', 'claude'])['reason'], 'preferred')

    def test_a_limit_hit_constrains_until_its_reset_or_for_60_minutes_without_one(self):
        self.add_hit('claude', NOW - timedelta(minutes=10), until=NOW + timedelta(minutes=30), entry='first')
        decision = hosts.route(self.memory, 'claude', allowed=['codex', 'claude'])
        self.assertEqual((decision['host'], decision['reason']), ('codex', 'headroom'))
        self.assertIn('it hit a usage limit that resets at 2026-09-17 12:30 UTC', decision['sentence'])
        self.clock.value = NOW + timedelta(minutes=31)
        self.assertEqual(hosts.route(self.memory, 'claude', allowed=['codex', 'claude'])['host'], 'claude')
        self.add_hit('claude', NOW + timedelta(minutes=20), entry='second')
        self.clock.value = NOW + timedelta(minutes=79)
        self.assertEqual(hosts.route(self.memory, 'claude', allowed=['codex', 'claude'])['host'], 'codex')
        self.clock.value = NOW + timedelta(minutes=81)
        self.assertEqual(hosts.route(self.memory, 'claude', allowed=['codex', 'claude'])['host'], 'claude')

    def test_a_reported_percentage_decides_before_load_and_load_before_configured_order(self):
        allowed = ['codex', 'claude', 'grok', 'opencode']
        self.add_limit('codex', 90, NOW + timedelta(hours=1))
        self.add_limit('grok', 40, NOW + timedelta(hours=1), limit_id='grok')
        self.add_limit('opencode', 20, NOW + timedelta(hours=1), limit_id='opencode')
        decision = hosts.route(self.memory, 'codex', allowed=allowed)
        self.assertEqual((decision['host'], decision['reason']), ('opencode', 'headroom'))
        self.assertTrue(decision['sentence'].endswith('not constrained, with a reported use of 20 percent.'))
        # Without any reported percentage, the lowest load against the host's own 7 day median wins.
        rooms = {'codex': {'reasons': ['used_percent'], 'used_percent': 90},
                 'claude': {'reasons': [], 'relative_load': 2.0}, 'grok': {'reasons': [], 'relative_load': 0.5}, 'opencode': {'reasons': []}}
        self.assertEqual(hosts.route(self.memory, 'codex', allowed=allowed, headroom=rooms)['host'], 'grok')
        # A tie keeps the configured order.
        rooms = {'codex': {'reasons': ['used_percent'], 'used_percent': 90}, 'claude': {'reasons': []}, 'grok': {'reasons': []},
                 'opencode': {'reasons': []}}
        self.assertEqual(hosts.route(self.memory, 'codex', allowed=allowed, headroom=rooms)['host'], 'claude')
        self.assertEqual(hosts.route(self.memory, 'codex', allowed=['codex', 'opencode', 'grok', 'claude'], headroom=rooms)['host'],
                         'opencode')

    def test_relative_load_is_read_from_the_ledger(self):
        # Codex used 1,000 tokens in each of three earlier windows and 3,000 in the last 5 hours; claude used its median.
        for index, hours in enumerate((6, 12, 18)):
            self.add_record('codex', NOW - timedelta(hours=hours), 1000, 'codex-%d' % index)
            self.add_record('claude', NOW - timedelta(hours=hours), 1000, 'claude-%d' % index)
        self.add_record('codex', NOW - timedelta(hours=1), 3000, 'codex-now')
        self.add_record('claude', NOW - timedelta(hours=1), 1000, 'claude-now')
        rooms = {name: {**room, 'reasons': []} for name, room in hosts.ledger_headroom(['codex', 'claude'], NOW).items()}
        self.assertEqual((rooms['codex']['relative_load'], rooms['claude']['relative_load']), (3.0, 1.0))
        self.add_limit('grok', 95, NOW + timedelta(hours=1), limit_id='grok')
        decision = hosts.route(self.memory, 'grok', allowed=['grok', 'codex', 'claude'])
        self.assertEqual(decision['host'], 'claude')
        self.assertTrue(decision['sentence'].endswith('with a load in the last 5 hours of 1 times its own median.'))

    def test_when_every_host_is_constrained_the_most_headroom_still_runs(self):
        self.add_limit('codex', 97, NOW + timedelta(hours=1))
        self.add_limit('claude', 88, NOW + timedelta(hours=1), limit_id='claude')
        decision = hosts.route(self.memory, 'codex', allowed=['codex', 'claude'])
        self.assertEqual((decision['host'], decision['reason']), ('claude', 'all_constrained'))
        self.assertEqual(decision['sentence'],
                         'The preferred host codex was not chosen because its reported use is 97 percent of a window that '
                         'resets at 2026-09-17 13:00 UTC. Every other allowed host is constrained too, so the claude host was '
                         'chosen because it has the most headroom, although its reported use is 88 percent of a window that '
                         'resets at 2026-09-17 13:00 UTC, with a reported use of 88 percent.')
        # A limit hit ranks after a high percentage.
        self.add_hit('claude', NOW, until=NOW + timedelta(hours=1))
        self.assertEqual(hosts.route(self.memory, 'claude', allowed=['codex', 'claude'])['host'], 'codex')

    def test_a_host_marked_unavailable_or_not_installed_is_never_chosen(self):
        hosts.mark_unavailable(self.memory, 'codex', 'usage_limit', (NOW + timedelta(hours=1)).isoformat())
        self.add_limit('claude', 92, NOW + timedelta(hours=1), limit_id='claude')
        decision = hosts.route(self.memory, 'codex', allowed=['codex', 'claude'])
        self.assertEqual((decision['host'], decision['reason']), ('claude', 'all_constrained'))
        self.assertIn('it is marked unavailable until 2026-09-17 13:00 UTC', decision['sentence'])
        hosts.mark_unavailable(self.memory, 'claude', 'rate_limit')
        with self.assertRaises(InvalidRecord) as caught:
            hosts.route(self.memory, 'codex', allowed=['codex', 'claude'])
        self.assertEqual([row['host'] for row in caught.exception.details['availability']], ['codex', 'claude'])
        with patch.object(hosts.shutil, 'which', side_effect=lambda name: None if name == 'grok' else '/usr/bin/' + name), \
                patch.object(hosts.Path, 'home', return_value=self.root / 'empty-home'):
            decision = hosts.route(self.memory, 'grok', allowed=['grok', 'opencode'])
        self.assertEqual((decision['host'], decision['reason']), ('opencode', 'headroom'))
        self.assertIn('because it is not installed.', decision['sentence'])

    def test_an_excluded_preferred_host_and_an_unreadable_ledger(self):
        decision = hosts.route(self.memory, 'codex', allowed=['codex', 'claude'], exclude=('codex',))
        self.assertEqual((decision['host'], decision['reason']), ('claude', 'headroom'))
        self.assertTrue(decision['sentence'].startswith('The preferred host codex is excluded from this choice.'))
        broken = self.root / 'broken.sqlite'
        broken.write_bytes(b'This file is not a database.' * 10)
        with patch.dict(os.environ, {machine.DATABASE_VARIABLE: str(broken)}):
            decision = hosts.route(self.memory, 'codex', allowed=['codex', 'claude'])
        self.assertEqual((decision['host'], decision['reason'], decision['ledger_read']), ('codex', 'preferred', False))

    def test_routing_is_deterministic(self):
        self.add_limit('codex', 90, NOW + timedelta(hours=1))
        first = hosts.route(self.memory, 'codex', allowed=['codex', 'claude', 'grok'])
        for _ in range(5):
            self.assertEqual(hosts.route(self.memory, 'codex', allowed=['codex', 'claude', 'grok']), first)


class HeadroomRankingTests(LedgerFixture):
    """An idle host, a host that cannot report a percentage and a model limit are ranked by the headroom they have."""

    def test_an_idle_host_ranks_before_a_loaded_host(self):
        for index, hours in enumerate((6, 12, 18)):
            self.add_record('claude', NOW - timedelta(hours=hours), 1000, 'claude-%d' % index)
            self.add_record('grok', NOW - timedelta(hours=hours), 1000, 'grok-%d' % index)
        self.add_record('grok', NOW - timedelta(hours=1), 10000, 'grok-now')
        self.add_limit('codex', 95, NOW + timedelta(hours=1))
        decision = hosts.route(self.memory, 'codex', allowed=['codex', 'grok', 'claude'])
        self.assertEqual((decision['host'], decision['reason']), ('claude', 'headroom'))
        # A host that was never used ranks before a loaded host as well.
        rooms = {'codex': {'reasons': ['used_percent'], 'used_percent': 95}, 'claude': {'reasons': [], 'relative_load': 50.0}}
        ledger = hosts.ledger_headroom(['opencode'], NOW)
        rooms['opencode'] = ledger['opencode']
        self.assertEqual(hosts.route(self.memory, 'codex', allowed=['codex', 'claude', 'opencode'], headroom=rooms)['host'],
                         'opencode')

    def test_a_high_reported_percentage_does_not_win_over_a_host_that_cannot_report_one(self):
        self.add_hit('opencode', NOW, until=NOW + timedelta(hours=1))
        self.add_limit('codex', 84.9, NOW + timedelta(days=6))
        decision = hosts.route(self.memory, 'opencode', allowed=['opencode', 'codex', 'claude'])
        self.assertEqual(decision['host'], 'claude')
        # A low reported percentage still ranks first.
        self.add_limit('codex', 20, NOW + timedelta(days=6))
        self.assertEqual(hosts.route(self.memory, 'opencode', allowed=['opencode', 'codex', 'claude'])['host'], 'codex')

    def test_a_model_limit_does_not_constrain_the_whole_host(self):
        self.add_limit('codex', 10, NOW + timedelta(hours=4))
        self.add_limit('codex', 95, NOW + timedelta(hours=4), limit_id='codex_model')
        decision = hosts.route(self.memory, 'codex', allowed=['codex', 'claude'])
        self.assertEqual((decision['host'], decision['reason']), ('codex', 'preferred'))


class CrossReviewTests(LedgerFixture):
    def config(self, *names):
        return {'project': str(self.root), 'host': names[-1], 'hosts': list(names)}

    def test_the_reviewer_differs_from_the_worker_host_even_when_the_other_host_is_constrained(self):
        config = self.config('claude', 'codex')
        self.add_limit('claude', 96, NOW + timedelta(hours=1), limit_id='claude')
        decisions = []
        self.assertEqual(reviews.review_host(self.memory, config, 'codex', decisions), 'claude')
        self.assertEqual(decisions[0]['reason'], 'all_constrained')

    def test_headroom_chooses_among_the_other_hosts_before_the_worker_host(self):
        config = self.config('claude', 'codex', 'grok')
        self.add_limit('claude', 90, NOW + timedelta(hours=1), limit_id='claude')
        decisions = []
        self.assertEqual(reviews.review_host(self.memory, config, 'codex', decisions), 'grok')
        self.assertEqual(decisions[0]['reason'], 'headroom')
        # Before routing by headroom, an unavailable first choice fell back to the worker host although grok could review.
        hosts.mark_unavailable(self.memory, 'claude', 'usage_limit')
        self.assertEqual(reviews.review_host(self.memory, config, 'codex'), 'grok')

    def test_the_worker_host_reviews_only_when_no_other_host_can_run(self):
        config = self.config('claude', 'codex')
        hosts.mark_unavailable(self.memory, 'claude', 'usage_limit')
        self.assertEqual(reviews.review_host(self.memory, config, 'codex'), 'codex')
        self.assertEqual(reviews.review_host(self.memory, self.config('codex'), 'codex'), 'codex')

    def test_a_worker_host_that_reviews_its_own_work_is_recorded_as_such(self):
        hosts.mark_unavailable(self.memory, 'claude', 'usage_limit')
        decisions = []
        self.assertEqual(reviews.review_host(self.memory, self.config('claude', 'codex'), 'codex', decisions), 'codex')
        self.assertEqual(decisions[0]['reason'], 'same_host')
        self.assertTrue(decisions[0]['sentence'].startswith('No host other than the worker host codex is installed and '
                                                            'available, so the codex host reviews its own work. '))
        with patch.object(hosts.shutil, 'which', side_effect=lambda name: None if name == 'claude' else '/usr/bin/' + name):
            decisions = []
            reviews.review_host(self.memory, self.config('claude', 'codex'), 'codex', decisions)
        self.assertEqual((decisions[0]['host'], decisions[0]['reason']), ('codex', 'same_host'))


class RecordedDecisionTests(unittest.TestCase):
    """The decision and its reason are recorded on the run and listed by the usage endpoint."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.machine_path = self.root / 'machine' / 'machine.sqlite'
        clean = {name: '' for name in hosts.ENVIRONMENT.values()}
        environment = patch.dict(os.environ, {machine.DATABASE_VARIABLE: str(self.machine_path), 'XDG_CONFIG_HOME': str(self.root / 'xdg'),
                                              'CODEX_HOME': str(self.root / 'codex-home'), **clean})
        environment.start()
        self.addCleanup(environment.stop)
        which = patch.object(hosts.shutil, 'which', side_effect=lambda name: '/usr/bin/' + name)
        which.start()
        self.addCleanup(which.stop)
        machine.initialize(self.machine_path, name='Test machine')
        with Memory(self.machine_path) as store, store._write():
            machine.ensure_usage(store)
            observed = datetime.now(timezone.utc)
            resets = (observed + timedelta(hours=2)).isoformat(timespec='microseconds')
            store.db.execute("INSERT INTO usage_limits VALUES ('claude','claude','primary',91,300,?,?)",
                             (resets, observed.isoformat(timespec='microseconds')))
        project = self.root / 'project'
        project.mkdir()
        (project / 'src').mkdir()
        (project / 'src' / 'app.py').write_text('VALUE = 1\n')
        for args in (('init', '-q'), ('config', 'user.name', 'Fixture'), ('config', 'user.email', 'fixture@example.com'),
                     ('config', 'commit.gpgsign', 'false'), ('add', '-A'), ('commit', '-q', '-m', 'Initial')):
            subprocess.run(['git', '-C', str(project), *args], check=True, capture_output=True)
        info = setup(project, requirements=['Keep history.'])
        self.memory = Memory(info['database'])
        self.addCleanup(self.memory.close)
        for name in ('codex', 'claude', 'grok'):
            reviews.configure(self.memory, project, name)
        self.work = action(self.memory, 'plan', {
            'title': 'Repair the parser', 'objective': 'Preserve both decoding paths.', 'criterion': 'Both paths decode.',
            'subject': 'code', 'payload': {'state': 'ready', 'next_action': 'Inspect the parser.', 'scope': 'Change decoding only.',
                                           'autonomy': 'act', 'paths': ['src/**'], 'reason': 'The user requests the repair.'}},
            'fixture')

    def test_a_check_and_delegated_work_record_the_decision_on_the_run(self):
        check = reviews.request(self.memory, self.work['episode_id'], request_key='check', retry=True)
        # The implementer defaults to codex, claude is at 91 percent, so grok reviews and the reason is recorded.
        self.assertEqual(check['host'], 'grok')
        self.assertEqual((check['routing']['preferred'], check['routing']['reason']), ('claude', 'headroom'))
        self.assertNotIn('routing', check['snapshot'])
        self.assertEqual(reviews.read(self.memory, check['id'])['routing'], check['routing'])
        reviews.cancel(self.memory, check['id'])
        work = delegation.request_work(self.memory, self.work['episode_id'], request_key='work', host='claude')
        self.assertEqual(work['host'], 'codex')
        self.assertEqual((work['routing']['preferred'], work['routing']['reason']), ('claude', 'headroom'))
        listed = api.usage(self.memory, {})['routing']
        self.assertEqual([(item['id'], item['reason']) for item in listed], [(work['id'], 'headroom'), (check['id'], 'headroom')])
        self.assertEqual(listed[0]['sentence'], work['routing']['sentence'])
        reviews.cancel(self.memory, work['id'])

    def test_work_of_a_swarm_is_never_routed_to_a_host_that_cannot_join_the_hive(self):
        from memory_module import hive
        with hive.Hive(hive.path_for(self.memory)) as store:
            swarm = hive.open_swarm(store, title='Parser fault', purpose='Find why the parser loses characters.',
                                    kind='manual', request_key='swarm', blind=False)
        binding = {'swarm_id': swarm['id'], 'agent_id': 'w1', 'role': 'worker'}
        hosts.mark_unavailable(self.memory, 'codex', 'usage_limit')
        # Grok takes work here, as after a passing probe, but it cannot receive the hive server.
        with patch.object(hosts, 'may_work', return_value=True):
            self.assertEqual(delegation.work_hosts(reviews.configured(self.memory)), ['claude', 'codex', 'grok'])
            work = delegation.request_work(self.memory, self.work['episode_id'], request_key='swarm-work', host='claude',
                                           hive=binding)
            self.assertEqual(work['host'], 'claude')
            self.assertEqual([option['host'] for option in work['routing']['hosts']], ['claude', 'codex'])
            reviews.cancel(self.memory, work['id'])
            with self.assertRaises(InvalidRecord) as caught:
                delegation.request_work(self.memory, self.work['episode_id'], request_key='swarm-grok', host='grok', hive=binding)
            self.assertEqual(str(caught.exception), 'The grok host cannot receive the hive server, so it cannot join a swarm. '
                                                    'Delegate work of a swarm to Codex or Claude.')

    def test_a_legacy_run_without_a_decision_reads_as_none(self):
        reviews.ensure_run_columns(self.memory)
        with self.memory._write():
            self.memory.db.execute("INSERT INTO review_runs (id,episode_id,role,signature,host,session_id,state,created_at,updated_at,"
                                   "request_key,snapshot) VALUES ('check_legacy',?,'outcome','s','codex','','pass',?,?,'legacy','{}')",
                                   (self.work['episode_id'], self.memory.now(), self.memory.now()))
        self.assertIsNone(reviews.read(self.memory, 'check_legacy')['routing'])
        self.assertEqual(api.usage(self.memory, {})['routing'], [])


if __name__ == '__main__':
    unittest.main()
