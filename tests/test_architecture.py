"""Tests for static code structure, package manifests and attachments of work to components."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from memory_module import Memory, arch_code, architecture, guards, planning
from memory_module.architecture import declarations, dependency_sections, model, project_root, pubspec
from memory_module.core import InvalidRecord


TRIGGER_FIELDS = ('paths', 'keywords', 'failure_type')


@contextmanager
def trigger_fields_accepted():
    """Accept plan and lesson trigger fields even before core.py and planning.py validate them.

    The real validation runs first. The fields are removed only when that validation
    rejects the payload, so these tests work before and after the guards change lands.
    """
    original_plan = planning.validate_payload
    original_payload = Memory._validate_payload

    def plan(kind, payload):
        try:
            return original_plan(kind, payload)
        except InvalidRecord:
            if kind != 'work_plan' or 'paths' not in payload:
                raise
            return original_plan(kind, {k: v for k, v in payload.items() if k != 'paths'})

    def other(self, kind, payload):
        try:
            return original_payload(self, kind, payload)
        except InvalidRecord:
            if kind not in {'lesson', 'lesson_review'} or not set(TRIGGER_FIELDS) & set(payload):
                raise
            return original_payload(self, kind, {k: v for k, v in payload.items() if k not in TRIGGER_FIELDS})

    with mock.patch.object(planning, 'validate_payload', plan), mock.patch.object(Memory, '_validate_payload', other):
        yield


def write(root, name, text):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    return path


def python_project(root):
    write(root, 'main.py', 'import app.core\nimport os\n')
    write(root, 'app/__init__.py', '')
    write(root, 'app/core.py', 'from .util import helper\nfrom . import models\nimport requests\n')
    write(root, 'app/util.py', 'import json\n\ndef helper():\n    return 1\n')
    write(root, 'app/models/__init__.py', '')
    write(root, 'app/models/user.py', 'import yaml\n')
    write(root, 'app/api/__init__.py', '')
    write(root, 'app/api/handlers.py', 'from ..core import helper\nfrom app.models import user\nfrom app import util\n')
    write(root, 'broken.py', 'def broken(:\n    pass\n')
    write(root, 'pyproject.toml', '[project]\ndependencies=["requests>=2", "rich"]\n'
                                  '[build-system]\nrequires=["hatchling"]\n')


def typescript_project(root):
    write(root, 'web/package.json', json.dumps({
        'dependencies': {'react': '^19', '@scope/ui': '1.0.0'},
        'devDependencies': {'left-pad': '1.3.0'}}))
    write(root, 'web/src/index.ts', "import React from 'react';\n"
                                    "import { Button } from '@scope/ui/button';\n"
                                    "import { readFile } from 'node:fs';\n"
                                    "import path from 'path';\n"
                                    "import { list } from './components';\n"
                                    "import { format } from './util.js';\n"
                                    "export * from './types';\n"
                                    "// import ghost from 'ghost-package';\n"
                                    "/* import other from 'other-ghost'; */\n"
                                    "const url = 'https://example.invalid/x';\n"
                                    "const fp = require('lodash/fp');\n"
                                    "const lazy = () => import('./lazy');\n")
    write(root, 'web/src/components/index.ts', "import {\n  format,\n} from '../util';\nexport const list = [];\n")
    write(root, 'web/src/util.ts', 'export const format = (x) => x;\n')
    write(root, 'web/src/types.ts', 'export type Id = string;\n')
    write(root, 'web/src/lazy.tsx', "import './styles.css';\nexport default 1;\n")


def dart_project(root):
    write(root, 'mobile/pubspec.yaml', 'name: shop\n'
                                       'dependencies:\n'
                                       '  flutter:\n'
                                       '    sdk: flutter\n'
                                       '  http: ^1.0.0\n'
                                       'dev_dependencies:\n'
                                       '  test: any\n')
    write(root, 'mobile/lib/main.dart', "import 'dart:async';\n"
                                        "import 'package:shop/src/cart.dart';\n"
                                        "import 'package:http/http.dart' as http;\n"
                                        "import 'widgets/button.dart';\n")
    write(root, 'mobile/lib/src/cart.dart', 'class Cart {}\n')
    write(root, 'mobile/lib/widgets/button.dart', "import '../src/cart.dart';\n")


def by_id(result):
    return {node['id']: node for node in result['nodes']}


def edge_set(result):
    return {(edge['from'], edge['to'], edge['type']): edge['weight'] for edge in result['edges']}


class ArchitectureTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name).resolve()
        self.m = Memory.create(self.root / '.memory' / 'memory.sqlite', 'Architecture', ['Keep evidence.'])

    def tearDown(self):
        self.m.close()
        self.temp.cleanup()


class ProjectRootTests(ArchitectureTestCase):
    def test_root_prefers_workspace_setting_then_review_host_then_database_location(self):
        self.assertEqual(project_root(self.m), self.root)
        self.m.db.execute("INSERT INTO settings VALUES ('review_host',?)",
                          (json.dumps({'project': '/review/project', 'host': 'codex', 'enabled_at': '2026-01-01T00:00:00+00:00'}),))
        self.assertEqual(project_root(self.m), Path('/review/project'))
        self.m.db.execute("INSERT INTO settings VALUES ('workspace_project',?)", (json.dumps('/workspace/project'),))
        self.assertEqual(project_root(self.m), Path('/workspace/project'))


class PythonStructureTests(ArchitectureTestCase):
    def test_components_imports_packages_and_syntax_errors(self):
        python_project(self.root)
        result = model(self.m)
        nodes = by_id(result)
        self.assertEqual(result['root'], str(self.root))
        self.assertEqual(result['level'], 'component')
        self.assertFalse(result['truncated'])
        self.assertIn('dynamic loading', result['note'].lower())
        self.assertEqual({n for n, v in nodes.items() if v['kind'] == 'component'},
                         {'component:.', 'component:app', 'component:app/models', 'component:app/api'})
        root_node = nodes['component:.']
        self.assertEqual((root_node['files'], root_node['languages']), (2, ['python']))
        self.assertEqual(nodes['component:app']['lines'], 3 + 4)
        edges = edge_set(result)
        self.assertEqual(edges[('component:.', 'component:app', 'imports')], 1)
        self.assertEqual(edges[('component:app', 'component:app/models', 'imports')], 1)
        self.assertEqual(edges[('component:app/api', 'component:app', 'imports')], 2)
        self.assertEqual(edges[('component:app/api', 'component:app/models', 'imports')], 1)
        self.assertNotIn('package:python:os', nodes)
        self.assertNotIn('package:python:json', nodes)
        self.assertIn(('component:app', 'package:python:requests', 'uses_package'), edges)
        self.assertIn(('component:app/models', 'package:python:yaml', 'uses_package'), edges)
        requests = nodes['package:python:requests']
        self.assertEqual(requests['declared'], [{'requirement': '>=2', 'group': 'Runtime', 'manifest': 'pyproject.toml'}])
        self.assertEqual(requests['flags'], [])
        self.assertEqual(nodes['package:python:rich']['flags'], ['declared_not_imported'])
        self.assertEqual(nodes['package:python:hatchling']['flags'], [])
        self.assertEqual(nodes['package:python:yaml']['flags'], ['imported_not_declared'])
        self.assertEqual(result['languages'], {'python': {'files': 9, 'lines': 15}})
        self.assertEqual(len(result['issues']), 1)
        self.assertEqual((result['issues'][0]['path'], result['issues'][0]['line']), ('broken.py', 1))
        self.assertIn('syntax error', result['issues'][0]['message'])

    def test_file_level_shows_focus_files_and_connected_components(self):
        python_project(self.root)
        result = model(self.m, level='file', focus='app')
        nodes = by_id(result)
        self.assertEqual(result['focus'], 'app')
        self.assertEqual({n for n, v in nodes.items() if v['kind'] == 'file'},
                         {'component:app/__init__.py', 'component:app/core.py', 'component:app/util.py'})
        self.assertEqual({n for n, v in nodes.items() if v['kind'] == 'component'},
                         {'component:.', 'component:app/models', 'component:app/api'})
        self.assertEqual(set(n for n, v in nodes.items() if v['kind'] == 'package'), {'package:python:requests'})
        edges = edge_set(result)
        self.assertIn(('component:app/core.py', 'component:app/util.py', 'imports'), edges)
        self.assertIn(('component:app/core.py', 'component:app/models', 'imports'), edges)
        self.assertIn(('component:.', 'component:app/core.py', 'imports'), edges)
        self.assertEqual(edges[('component:app/api', 'component:app/core.py', 'imports')], 1)
        self.assertEqual(model(self.m, level='file', focus='component:app')['focus'], 'app')
        with self.assertRaises(InvalidRecord):
            model(self.m, level='file')
        with self.assertRaises(InvalidRecord):
            model(self.m, level='file', focus='missing')
        with self.assertRaises(InvalidRecord):
            model(self.m, level='file', focus='../outside')
        with self.assertRaises(InvalidRecord):
            model(self.m, level='graph')


class ScriptAndDartStructureTests(ArchitectureTestCase):
    def test_typescript_resolution_scoped_packages_and_builtins(self):
        typescript_project(self.root)
        result = model(self.m)
        nodes = by_id(result)
        edges = edge_set(result)
        self.assertIn(('component:web/src', 'component:web/src/components', 'imports'), edges)
        self.assertIn(('component:web/src/components', 'component:web/src', 'imports'), edges)
        packages = {n for n, v in nodes.items() if v['kind'] == 'package'}
        self.assertEqual(packages, {'package:npm:react', 'package:npm:@scope/ui', 'package:npm:left-pad', 'package:npm:lodash'})
        self.assertIn(('component:web/src', 'package:npm:@scope/ui', 'uses_package'), edges)
        self.assertEqual(nodes['package:npm:left-pad']['flags'], ['declared_not_imported'])
        self.assertEqual(nodes['package:npm:lodash']['flags'], ['imported_not_declared'])
        self.assertEqual(nodes['package:npm:react']['groups'], ['Runtime'])
        self.assertEqual(set(nodes['component:web/src']['languages']), {'typescript'})
        self.assertEqual(result['languages']['typescript']['files'], 5)
        self.assertEqual(result['issues'], [])

    def test_resolved_typescript_file_edges_at_file_level(self):
        typescript_project(self.root)
        edges = edge_set(model(self.m, level='file', focus='web/src'))
        for target in ('util.ts', 'types.ts', 'lazy.tsx'):
            self.assertIn(('component:web/src/index.ts', 'component:web/src/' + target, 'imports'), edges)
        self.assertIn(('component:web/src/index.ts', 'component:web/src/components', 'imports'), edges)

    def test_dart_internal_package_imports_and_pubspec_dependencies(self):
        dart_project(self.root)
        result = model(self.m)
        nodes = by_id(result)
        edges = edge_set(result)
        self.assertIn(('component:mobile/lib', 'component:mobile/lib/src', 'imports'), edges)
        self.assertIn(('component:mobile/lib/widgets', 'component:mobile/lib/src', 'imports'), edges)
        self.assertNotIn('package:pub:shop', nodes)
        self.assertIn(('component:mobile/lib', 'package:pub:http', 'uses_package'), edges)
        self.assertEqual(nodes['package:pub:http']['declared'][0]['requirement'], '^1.0.0')
        self.assertEqual(nodes['package:pub:test']['flags'], ['declared_not_imported'])
        self.assertEqual(nodes['package:pub:flutter']['declared'][0]['requirement'], 'sdk: flutter')
        self.assertEqual(pubspec('name: "shop"\n')[0], 'shop')


class LimitsAndCacheTests(ArchitectureTestCase):
    def test_large_files_are_skipped_and_source_count_truncates(self):
        write(self.root, 'a.py', 'import b\n')
        write(self.root, 'b.py', '')
        write(self.root, 'c.py', '')
        write(self.root, 'large.py', '#' * 1_000_001)
        result = model(self.m)
        self.assertTrue(any(i['path'] == 'large.py' and '1 MB' in i['message'] for i in result['issues']))
        self.assertEqual(result['languages']['python']['files'], 3)
        with mock.patch.object(arch_code, 'MAX_SOURCE_FILES', 2):
            limited = model(self.m)
        self.assertTrue(limited['truncated'])
        self.assertEqual(limited['languages']['python']['files'], 2)

    def test_cache_reuses_unchanged_files_and_rereads_changed_files(self):
        python_project(self.root)
        cache = {}
        model(self.m, cache=cache)
        with mock.patch.object(arch_code, '_parse_python', side_effect=AssertionError('parsed again')):
            again = model(self.m, cache=cache)
        self.assertEqual(len(again['issues']), 1)
        path = self.root / 'app/util.py'
        path.write_text('import requests\nimport rich\n')
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
        calls = []
        original = arch_code._parse_python

        def counted(data):
            calls.append(data)
            return original(data)

        with mock.patch.object(arch_code, '_parse_python', side_effect=counted):
            changed = model(self.m, cache=cache)
        self.assertEqual(len(calls), 1)
        self.assertEqual(by_id(changed)['package:python:rich']['flags'], [])
        self.assertEqual(sum(1 for key in cache if key[0] == str(path)), 1)


class AttachmentTests(ArchitectureTestCase):
    def setUp(self):
        super().setUp()
        python_project(self.root)
        self.evidence = self.m.source('architecture-test', 'Scope', 'The user sets the scope.', 'Work on the API handlers.', 'user')

    def plan(self, title, paths, state='ready', key=None):
        payload = {'state': state, 'next_action': 'Inspect the files.', 'scope': 'Use the listed paths.',
                   'autonomy': 'suggest', 'reason': 'The user asks for it.', 'paths': paths}
        if state == 'in_progress':
            payload['session_id'] = 'session-1'
        with trigger_fields_accepted():
            return planning.save(self.m, 'work_plan', payload=payload, actor='test',
                                 evidence=[{'source_id': self.evidence['id'], 'reason': 'The user sets the scope.'}],
                                 title=title, objective='Change ' + title + '.', criterion='The change is checked.',
                                 request_key=key or 'plan-' + title, session_id=payload.get('session_id'))

    def test_plan_paths_attach_work_and_work_states_set_status(self):
        first = self.plan('api', ['app/api/**'])
        second = self.plan('models', ['/' + (self.root / 'app/models').as_posix().lstrip('/')], state='in_progress')
        self.plan('outside', ['/elsewhere/project/**'])
        result = model(self.m)
        nodes = by_id(result)
        self.assertEqual(nodes['component:app/api']['work'], [first['episode_id']])
        self.assertEqual(nodes['component:app/api']['work_total'], 1)
        self.assertEqual(nodes['component:app']['work'], [])
        self.assertEqual(nodes['component:app/models']['work'], [second['episode_id']])
        self.assertEqual(nodes['component:app/models']['status'], 'in_progress')
        self.assertEqual(nodes['component:app/api']['status'], 'idle')
        self.assertEqual(nodes['component:.']['work_total'], 0)
        states = {first['episode_id']: 'blocked', second['episode_id']: 'review'}
        nodes = by_id(model(self.m, work_states=states))
        self.assertEqual(nodes['component:app/api']['status'], 'blocked')
        self.assertEqual(nodes['component:app/models']['status'], 'review')
        files = by_id(model(self.m, level='file', focus='app/api'))
        self.assertEqual(files['component:app/api/handlers.py']['work'], [first['episode_id']])

    def test_attached_lists_are_limited_with_totals(self):
        for number in range(21):
            self.plan('bulk-' + str(number), ['app/util.py'])
        node = by_id(model(self.m))['component:app']
        self.assertEqual(len(node['work']), 20)
        self.assertEqual(node['work_total'], 21)

    def test_accepted_lessons_mark_components_guarded_and_links_attach(self):
        work = self.plan('lesson-host', ['app/api'])
        episode = self.m.episode(work['episode_id'])
        with trigger_fields_accepted():
            lesson = self.m.record(work['episode_id'], 'lesson',
                                   {'when': 'Editing handlers.', 'do': 'Keep validation.', 'because': 'It failed before.',
                                    'exceptions': 'None.', 'paths': ['app/models/**']},
                                   expected_version=episode['version'], request_key='lesson-1', actor='test',
                                   evidence=[{'source_id': self.evidence['id'], 'reason': 'Observed failure.'}])
            nodes = by_id(model(self.m))
            self.assertEqual(nodes['component:app/models']['lessons'], [lesson['id']])
            self.assertEqual(nodes['component:app/models']['status'], 'idle')
            self.m.record(work['episode_id'], 'lesson_review',
                          {'lesson_id': lesson['id'], 'status': 'accepted', 'reason': 'The user accepts it.'},
                          expected_version=lesson['version'], request_key='review-1', actor='workspace-user',
                          evidence=[{'source_id': self.evidence['id'], 'reason': 'User review.'}],
                          links=[{'event_id': lesson['id'], 'reason': 'The lesson under review.'}])
        nodes = by_id(model(self.m))
        self.assertEqual(nodes['component:app/models']['status'], 'guarded')
        self.assertEqual(nodes['component:app/api']['status'], 'idle')
        self.m.db.executescript('''CREATE TABLE links (id TEXT PRIMARY KEY, from_id TEXT NOT NULL, to_id TEXT NOT NULL,
            type TEXT NOT NULL, reason TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL,
            request_key TEXT NOT NULL UNIQUE, signature TEXT NOT NULL, retires TEXT REFERENCES links(id));''')
        rows = [('link_1', work['episode_id'], 'component:app/api', 'affects_component', 'k1', None),
                ('link_2', 'component:app', 'package:python:requests', 'relates_to', 'k2', None),
                ('link_3', work['episode_id'], 'component:app', 'affects_component', 'k3', None),
                ('link_4', work['episode_id'], 'component:app', 'affects_component', 'k4', 'link_3')]
        for link_id, source, target, kind, key, retires in rows:
            self.m.db.execute('INSERT INTO links VALUES (?,?,?,?,?,?,?,?,?,?)',
                              (link_id, source, target, kind, 'Reason.', 'test', '2026-01-01T00:00:00+00:00', key, 'sig', retires))
        nodes = by_id(model(self.m))
        self.assertEqual(nodes['component:app/api']['links'], ['link_1'])
        self.assertEqual(nodes['component:app']['links'], ['link_2'])
        self.assertEqual(nodes['package:python:requests']['links'], ['link_2'])
        self.assertEqual(nodes['package:python:requests']['links_total'], 1)

    def test_read_only_connection_reads_the_model_without_links_table(self):
        self.plan('api', ['app/api/**'])
        reader = Memory(self.m.path, read_only=True)
        try:
            result = model(reader, work_states={})
            self.assertEqual(by_id(result)['component:app/api']['work_total'], 1)
            self.assertEqual(model(reader, level='file', focus='app')['level'], 'file')
        finally:
            reader.close()
        self.assertFalse(self.m.db.execute("SELECT 1 FROM sqlite_master WHERE name='links'").fetchone())


class PathMatchingTests(ArchitectureTestCase):
    """The model compares paths with the one matcher in guards, and with no copy of its own."""

    def test_path_patterns_follow_the_documented_semantics(self):
        cases = [
            ('app/api/handlers.py', ['app/api'], True),
            ('app/apiary/x.py', ['app/api'], False),
            ('app/api/handlers.py', ['app/**'], True),
            ('app/api/handlers.py', ['app/*.py'], False),
            ('app/core.py', ['app/*.py'], True),
            ('app/core.py', ['app/cor?.py'], True),
            ('main.py', ['**/*.py'], True),
            ('main.py', ['.'], True),
        ]
        for path, patterns, expected in cases:
            with self.subTest(path=path, patterns=patterns):
                self.assertEqual(guards.match_path(path, patterns), expected)

    def test_the_model_attaches_work_through_the_guards_matcher(self):
        python_project(self.root)
        evidence = self.m.source('matcher-test', 'Scope', 'The user sets the scope.', 'Work on the API handlers.', 'user')
        with trigger_fields_accepted():
            planning.save(self.m, 'work_plan',
                          payload={'state': 'ready', 'next_action': 'Inspect the files.', 'scope': 'Use the listed paths.',
                                   'autonomy': 'suggest', 'reason': 'The user asks for it.', 'paths': ['app/api/**']},
                          actor='test', evidence=[{'source_id': evidence['id'], 'reason': 'The user sets the scope.'}],
                          title='api', objective='Change the handlers.', criterion='The change is checked.',
                          request_key='plan-matcher')
        calls = []
        original = guards.match_path

        def counted(path, patterns, **options):
            calls.append(path)
            return original(path, patterns, **options)

        with mock.patch.object(guards, 'match_path', counted):
            nodes = by_id(model(self.m))
        self.assertTrue(calls)
        self.assertEqual(nodes['component:app/api']['work_total'], 1)


class ManifestTests(ArchitectureTestCase):
    """Package manifest declarations, grouped and versioned as each manifest states them."""

    def test_packages_preserve_manifest_groups_and_declared_versions(self):
        (self.root / 'package.json').write_text(json.dumps({'dependencies': {'react': '^19', 'optional': '1'},
            'devDependencies': {'vite': '~8'}, 'peerDependencies': {'react': '>=18'}, 'optionalDependencies': {'optional': '2'}}))
        (self.root / 'pyproject.toml').write_text('[project]\ndependencies=["httpx[http2]>=0.28; python_version >= \'3.11\'"]\n'
            '[build-system]\nrequires=["hatchling>=1.27,<2"]\n[project.optional-dependencies]\ntest=["pytest==8"]\n')
        (self.root / 'scripts').mkdir()
        (self.root / 'scripts/requirements.txt').write_text('# Import utility\nopenpyxl==3.1.5 # verified pin\n')
        (self.root / 'node_modules').mkdir()
        (self.root / 'node_modules/package.json').write_text('{"dependencies":{"transitive":"1"}}')
        result = model(self.m)['packages']
        self.assertEqual(len(result['declared']), 8)
        self.assertEqual(result['issues'], [])
        self.assertEqual([(p['group'], p['requirement']) for p in result['declared'] if p['name'] == 'react'],
                         [('Peer', '>=18'), ('Runtime', '^19')])
        optional = next(p for p in result['declared'] if p['name'] == 'optional')
        self.assertEqual((optional['group'], optional['requirement']), ('Optional', '2'))
        self.assertEqual(next(p for p in result['declared'] if p['name'] == 'openpyxl')['manifest'], 'scripts/requirements.txt')
        self.assertIn('python_version', next(p for p in result['declared'] if p['name'] == 'httpx')['requirement'])
        react = by_id(model(self.m))['package:npm:react']
        self.assertEqual(react['groups'], ['Peer', 'Runtime'])
        self.assertEqual(react['manifests'], ['package.json'])
        self.assertEqual(react['flags'], [])

    def test_errors_and_unsupported_directives_are_visible_without_outside_reads(self):
        (self.root / 'package.json').write_text('{broken')
        (self.root / 'requirements.txt').write_text('-r private.in\nhttps://example.invalid/package.whl\n')
        (self.root / 'pyproject.toml').write_text('[project]\ndynamic=["dependencies"]\n')
        result = model(self.m)['packages']
        self.assertFalse(result['declared'])
        self.assertEqual(len(result['issues']), 4)
        outside = self.root.parent / (self.root.name + '-outside.json')
        outside.write_text('{"dependencies":{"secret":"1"}}')
        try:
            (self.root / 'package.json').unlink()
            try:
                (self.root / 'package.json').symlink_to(outside)
            except OSError as exc:
                self.skipTest(str(exc))
            self.assertNotIn('secret', json.dumps(model(self.m)))
            self.assertTrue(any('outside' in i['message'] for i in model(self.m)['packages']['issues']))
        finally:
            outside.unlink()

    def test_invalid_shapes_do_not_fabricate_an_empty_inventory(self):
        for name, text in [('package.json', '[]'), ('package.json', '{"dependencies":[]}'),
                           ('pyproject.toml', 'project=[]'), ('pyproject.toml', '[project]\noptional-dependencies=[]')]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                declarations(name, text)

    def test_dependency_sections_keep_full_exceptions(self):
        section = '## Software dependencies\n\n| Dependency | Role |\n|---|---|\n| React | Browser UI. |\n\n### Exception\nThe legacy import uses Python.\n\n'
        body = '# Architecture\n\n```md\n## Fake dependencies\n```\n\n' + section + '## Other scope\nDo not include this section.\n'
        self.assertEqual(dependency_sections('architecture.md', body), [{'heading': 'Software dependencies', 'body': section}])
        self.assertEqual(dependency_sections('Dependencies.md', 'Unheaded original text.'),
                         [{'heading': 'Dependencies.md', 'body': 'Unheaded original text.'}])
        self.assertEqual(dependency_sections('Notes.md', 'No matching heading.'), [])


if __name__ == '__main__':
    unittest.main()
