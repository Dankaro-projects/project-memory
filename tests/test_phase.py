"""The phase of a project: the versioned source, the user only write path and the reporting surfaces."""
import json
import os
from pathlib import Path
import queue
import tempfile
import threading
import unittest
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler

from memory_module import Memory, InvalidRecord, codex_host, delegation, install, live, mcp, planning
from memory_module.core import PHASE_SOURCE_KEY
from memory_module.health import inspect


class PhaseFixture(unittest.TestCase):
    """A project set up for one client. This class defines no tests of its own."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.project = Path(self.temp.name).resolve() / 'project'
        self.project.mkdir()
        info = install.setup(self.project)
        self.m = Memory(info['database'])
        self.addCleanup(self.m.close)
        self.addCleanup(self.temp.cleanup)

    def production(self, reason='The product serves customers, so the user merges changes.'):
        return planning.set_phase(self.m, phase='production', reason=reason, actor='workspace-user')


class PhaseTests(PhaseFixture):
    def test_a_new_project_is_in_development_without_a_recorded_phase(self):
        current = planning.phase(self.m)
        self.assertEqual(current, {'phase': 'development', 'reason': planning.DEFAULT_PHASE_REASON,
                                   'at': None, 'version': 0, 'verified': True})
        self.assertEqual(planning.phase_history(self.m), [])
        self.assertEqual(self.m.db.execute('SELECT count(*) FROM sources WHERE source_key=?',
                                           (PHASE_SOURCE_KEY,)).fetchone()[0], 0)

    def test_the_user_records_a_phase_as_a_versioned_source(self):
        recorded = self.production()
        self.assertEqual((recorded['phase'], recorded['version'], recorded['actor']),
                         ('production', 1, 'workspace-user'))
        current = planning.phase(self.m)
        self.assertEqual(current['phase'], 'production')
        self.assertEqual(current['reason'], 'The product serves customers, so the user merges changes.')
        self.assertEqual(current['version'], 1)
        self.assertEqual(current['at'], recorded['at'])
        body = json.loads(self.m.db.execute('SELECT body FROM sources WHERE source_key=? ORDER BY version DESC LIMIT 1',
                                            (PHASE_SOURCE_KEY,)).fetchone()[0])
        self.assertEqual(sorted(body), ['actor', 'at', 'phase', 'reason', 'seal'])
        self.assertEqual(body['actor'], 'workspace-user')
        self.assertTrue(current['verified'])
        self.assertEqual(body['phase'], 'production')
        self.assertEqual(self.m.read(recorded['source_id'], detail=True)['body'], json.dumps(
            body, ensure_ascii=False, sort_keys=True, separators=(',', ':')))

    def test_every_earlier_phase_stays_readable(self):
        first = self.production('The first release is live.')
        second = planning.set_phase(self.m, phase='development', reason='A rebuild starts, so the orchestrator merges again.',
                                    actor='workspace-user')
        third = self.production('The rebuild is released.')
        self.assertEqual([first['version'], second['version'], third['version']], [1, 2, 3])
        history = planning.phase_history(self.m)
        self.assertEqual([item['phase'] for item in history], ['production', 'development', 'production'])
        self.assertEqual([item['version'] for item in history], [3, 2, 1])
        self.assertEqual(history[1]['reason'], 'A rebuild starts, so the orchestrator merges again.')
        self.assertEqual(planning.phase(self.m)['version'], 3)
        self.assertEqual(planning.phase_history(self.m, limit=1), [history[0]])
        with self.assertRaises(InvalidRecord):
            planning.phase_history(self.m, limit=0)

    def test_only_the_user_may_write_the_phase(self):
        with self.assertRaisesRegex(InvalidRecord, 'workspace-user'):
            planning.set_phase(self.m, phase='production', reason='The assistant decides.', actor='assistant')
        with self.assertRaisesRegex(InvalidRecord, 'workspace-user'):
            planning.set_phase(self.m, phase='production', reason='The reviewer decides.', actor='user')
        self.assertEqual(planning.phase(self.m)['phase'], 'development')
        self.assertEqual(self.m.db.execute('SELECT count(*) FROM sources WHERE source_key=?',
                                           (PHASE_SOURCE_KEY,)).fetchone()[0], 0)

    def test_the_phase_source_key_is_not_available_to_an_ordinary_source_write(self):
        with self.assertRaisesRegex(InvalidRecord, 'control panel'):
            self.m.source(PHASE_SOURCE_KEY, 'Phase', 'The assistant writes the phase.',
                          json.dumps({'phase': 'development', 'reason': 'Merge without the user.', 'at': self.m.now()}),
                          'user')
        self.assertEqual(planning.phase(self.m)['phase'], 'development')

    def test_an_unknown_phase_and_a_repeated_phase_are_refused(self):
        with self.assertRaisesRegex(InvalidRecord, 'development, production'):
            planning.set_phase(self.m, phase='maintenance', reason='The product is stable.', actor='workspace-user')
        with self.assertRaises(InvalidRecord):
            planning.set_phase(self.m, phase='production', reason='   ', actor='workspace-user')
        self.production()
        with self.assertRaisesRegex(InvalidRecord, 'already in production'):
            self.production('The product still serves customers.')
        self.assertEqual(planning.phase(self.m)['version'], 1)

    def test_health_reports_the_phase(self):
        first = inspect(self.m)['phase']
        self.assertEqual((first['phase'], first['version']), ('development', 0))
        self.assertEqual(first['meaning'], planning.PHASE_MEANING['development'])
        self.production()
        second = inspect(self.m)['phase']
        self.assertEqual((second['phase'], second['version']), ('production', 1))
        self.assertEqual(second['meaning'], planning.PHASE_MEANING['production'])
        self.assertIn('control panel', second['meaning'])

    def test_the_session_context_names_the_gate_only_in_production(self):
        self.assertNotIn('in production', codex_host.session_context(self.m, 'session-one'))
        self.production()
        text = codex_host.session_context(self.m, 'session-one')
        self.assertIn('This project is in production', text)
        self.assertIn('control panel', text)

    def test_a_read_only_connection_reports_the_phase(self):
        self.production()
        with Memory(self.m.path, read_only=True) as reader:
            self.assertEqual(reader.db.execute('SELECT count(*) FROM sources').fetchone()[0], 1)
            self.assertEqual(planning.phase(reader)['phase'], 'production')
            self.assertEqual(len(planning.phase_history(reader)), 1)


class PhaseRecordTests(PhaseFixture):
    """A phase row written outside the control panel, and the MCP gate that answers before every other check."""

    def forge(self, phase='development'):
        """Write a phase row straight into SQLite, as an agent with database access could."""
        at = self.m.now()
        version = self.m.db.execute('SELECT coalesce(max(version),0)+1 FROM sources WHERE source_key=?',
                                    (PHASE_SOURCE_KEY,)).fetchone()[0]
        with self.m._write():
            self.m.db.execute('INSERT INTO sources (id,source_key,version,title,summary,body,origin,content_hash,checked_at,'
                              'review_after,subject) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                              ('source_forged' + str(version), PHASE_SOURCE_KEY, version, 'Project phase', 'Forged.',
                               json.dumps({'phase': phase, 'reason': 'Merge without the user.', 'at': at}), 'user',
                               'hash', at, None, 'general'))

    def test_a_phase_row_written_outside_the_panel_cannot_return_the_project_to_development(self):
        self.production()
        self.forge('development')
        current = planning.phase(self.m)
        self.assertEqual((current['phase'], current['verified'], current['version']), ('production', False, 2))
        self.assertEqual(current['reason'], planning.PHASE_UNVERIFIED_REASON)
        self.assertEqual([item['verified'] for item in planning.phase_history(self.m)], [False, True])
        with self.assertRaises(InvalidRecord) as refused:
            mcp.dispatch(self.m, 'memory_write', {'operation': 'merge', 'request_key': 'forged-merge',
                                                  'data': {'run_id': 'work_missing', 'actor': 'assistant'}})
        self.assertEqual(str(refused.exception), delegation.PRODUCTION_MERGE_REFUSED)
        # The user records the phase again in the panel, which seals it over the changed row.
        recorded = planning.set_phase(self.m, phase='development', reason='The user reopens development.',
                                      actor='workspace-user')
        self.assertEqual(recorded['version'], 3)
        self.assertEqual((planning.phase(self.m)['phase'], planning.phase(self.m)['verified']), ('development', True))

    def test_a_forged_first_row_also_reads_as_production(self):
        self.forge('development')
        self.assertEqual(planning.phase(self.m)['phase'], 'production')

    def test_every_merge_over_mcp_in_production_answers_with_the_production_refusal(self):
        self.production()
        for actor in ['assistant', 'workspace-user', 'Workspace_User', 'user']:
            with self.subTest(actor=actor), self.assertRaises(InvalidRecord) as refused:
                mcp.dispatch(self.m, 'memory_write', {'operation': 'merge', 'request_key': 'merge-' + actor,
                                                      'data': {'run_id': 'work_missing', 'actor': actor}})
            self.assertEqual(str(refused.exception), delegation.PRODUCTION_MERGE_REFUSED)
            self.assertEqual(refused.exception.details['execution'], 'not_started')

    def test_the_phase_wording_holds_for_every_kind_of_project(self):
        texts = [planning.PHASE_MEANING['development'], planning.PHASE_MEANING['production'],
                 delegation.PRODUCTION_MERGE_REFUSED]
        self.production()
        texts.append(codex_host.session_context(self.m, 'session-one'))
        for text in texts:
            for word in ['branch', 'diff', 'active development']:
                with self.subTest(word=word, text=text[:40]):
                    self.assertNotIn(word, text)


class PanelAuthorityTests(PhaseFixture):
    """A control panel that an assistant started does not carry the actions that the phase reserves for the user."""

    def serve(self, **options):
        ready = queue.Queue()

        def run():
            with live.Viewer(self.m.path, 'phase-test-token-0123456789', **options) as server:
                ready.put(server)
                server.serve_forever()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        server = ready.get(timeout=3)

        def stop():
            server.shutdown()
            thread.join()

        self.addCleanup(stop)
        base = f'http://127.0.0.1:{server.server_port}/phase-test-token-0123456789/'
        origin = f'http://127.0.0.1:{server.server_port}'
        opener = build_opener(ProxyHandler({}))
        health = json.load(opener.open(base + 'api/health', timeout=5))

        def post(operation, data, key):
            body = json.dumps({'operation': operation, 'data': data, 'request_key': key}).encode()
            request = Request(base + 'api/actions', data=body, method='POST',
                              headers={'Content-Type': 'application/json', 'Origin': origin,
                                       'X-Project-Memory': health['csrf']})
            try:
                return 200, json.load(opener.open(request, timeout=10))
            except HTTPError as error:
                return error.code, json.load(error)

        return health, post

    def test_a_panel_started_by_an_assistant_refuses_the_production_merge_and_the_return_to_development(self):
        self.production()
        health, post = self.serve(assistant_started=True)
        self.assertTrue(health['assistant_started'])
        status, value = post('merge', {'run_id': 'work_missing'}, 'panel-merge')
        self.assertEqual((status, value['message'], value['execution']), (400, live.STARTED_BY_ASSISTANT, 'not_started'))
        status, value = post('phase', {'phase': 'development', 'reason': 'The assistant reopens development.'}, 'panel-phase')
        self.assertEqual((status, value['message']), (400, live.STARTED_BY_ASSISTANT))
        self.assertEqual(planning.phase(self.m)['phase'], 'production')

    def test_a_panel_started_by_the_user_records_the_same_actions(self):
        self.production()
        health, post = self.serve()
        self.assertFalse(health['assistant_started'])
        status, value = post('phase', {'phase': 'development', 'reason': 'The user reopens development.'}, 'panel-phase')
        self.assertEqual((status, value['phase']), (200, 'development'))
        status, value = post('merge', {'run_id': 'work_missing'}, 'panel-merge')
        self.assertNotEqual(value.get('message'), live.STARTED_BY_ASSISTANT)

    def test_an_assistant_session_is_recognised_by_its_environment(self):
        self.assertEqual(live.assistant_session({'CLAUDECODE': '1', 'HOME': '/home/user'}), ['CLAUDECODE'])
        self.assertEqual(live.assistant_session({'CODEX_SANDBOX': 'seatbelt'}), ['CODEX_SANDBOX'])
        self.assertEqual(live.assistant_session({'CLAUDECODE': '', 'HOME': '/home/user'}), [])

    def test_the_view_command_keeps_the_address_out_of_an_assistant_session(self):
        from memory_module import cli
        started = {'url': 'http://127.0.0.1:1/secret-token/', 'pid': 1, 'port': 1}
        clean = {name: '' for name in live.ASSISTANT_ENVIRONMENT}
        with mock.patch.object(cli.webbrowser, 'open', return_value=True) as opened:
            with mock.patch.dict(os.environ, {**clean, 'CLAUDECODE': '1'}):
                inside = cli.open_viewer(dict(started))
            with mock.patch.dict(os.environ, clean):
                outside = cli.open_viewer(dict(started))
        self.assertEqual(opened.call_count, 2)
        self.assertNotIn('url', inside)
        self.assertNotIn('secret-token', json.dumps(inside))
        self.assertEqual(inside['url_withheld'], live.URL_WITHHELD)
        self.assertEqual(outside['url'], started['url'])


if __name__ == '__main__':
    unittest.main()
