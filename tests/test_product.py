import json
from contextlib import chdir
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
from memory_module import Memory, Conflict
from memory_module import install
from memory_module.cli import doctor
from memory_module.mcp import dispatch, write


class ProductTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.project=Path(self.temp.name)
        self.launch=[sys.executable,'-m','memory_module.cli']
    def tearDown(self):self.temp.cleanup()
    def setup(self,**kwargs):return install.setup(self.project,_launcher=self.launch,**kwargs)
    def test_generic_setup_actual_process_and_uninstall_preserve_data(self):
        first=self.setup(requirements=['Keep data locally.'])
        self.setup()
        report=doctor(Path(first['database']))
        self.assertTrue(report['mcp_process_verified']);self.assertEqual(report['observed_hooks'],{})
        install.uninstall(self.project)
        with Memory(first['database']) as memory:self.assertEqual(memory.requirements,['Keep data locally.'])
    def test_doctor_children_ignore_a_project_package_with_the_same_name(self):
        info=self.setup()
        shadow=self.project/'memory_module';shadow.mkdir()
        (shadow/'__init__.py').write_text('raise RuntimeError("The project package was imported.")\n')
        with chdir(self.project):
            self.assertTrue(doctor(Path(info['database']))['mcp_process_verified'])
    def test_codex_setup_preserves_other_settings_and_repeats_without_duplicates(self):
        local=self.project/'.codex';local.mkdir()
        (local/'config.toml').write_text('model = "other"\n')
        (local/'hooks.json').write_text(json.dumps({'hooks':{'Stop':[{'hooks':[{'type':'command','command':'other'}]}]}}))
        a=self.setup(client='codex');b=self.setup(client='codex')
        self.assertEqual(a['database'],b['database'])
        hooks=json.loads((local/'hooks.json').read_text())
        self.assertEqual(len(hooks['hooks']['Stop']),2)
        install.uninstall(self.project)
        self.assertEqual((local/'config.toml').read_text(),'model = "other"\n')
        self.assertEqual(json.loads((local/'hooks.json').read_text())['hooks']['Stop'][0]['hooks'][0]['command'],'other')
    def test_interrupted_upgrade_recovers_before_or_after_hook_write(self):
        for fail_at in [2,3]:
            self.setup(client='codex')
            real=install.atomic;calls=[]
            def fail(path,content):
                calls.append(path)
                if len(calls)==fail_at:raise OSError('Injected interruption')
                return real(path,content)
            with patch.object(install,'atomic',side_effect=fail):
                with self.assertRaises(OSError):install.setup(self.project,client='codex',_launcher=['new-launcher'])
            install.setup(self.project,client='codex',_launcher=['new-launcher'])
            hooks=json.loads((self.project/'.codex/hooks.json').read_text())['hooks']
            self.assertTrue(all(len(groups)==1 for groups in hooks.values()))
            self.assertIn('new-launcher',(self.project/'.codex/config.toml').read_text())
            install.uninstall(self.project)
    def test_refuses_unmanaged_configuration_and_changed_requirements(self):
        self.setup(requirements=['Keep quality.'])
        with self.assertRaises(Conflict):self.setup(requirements=['Drop quality.'])
        local=self.project/'.codex';local.mkdir()
        (local/'config.toml').write_text('[mcp_servers.project_memory]\ncommand="other"\n')
        with self.assertRaises(Conflict):self.setup(client='codex')
        self.assertIn('other',(local/'config.toml').read_text())
    def test_legacy_adapter_does_not_receive_a_duplicate_installation(self):
        local=self.project/'.codex';local.mkdir()
        config='[mcp_servers.memory]\ncommand="python"\nargs=["-m","memory_module.mcp","--db","old.sqlite"]\n'
        (local/'config.toml').write_text(config)
        with self.assertRaises(Conflict):self.setup(client='codex')
        self.assertEqual((local/'config.toml').read_text(),config)
        self.assertFalse((self.project/'.memory/project.sqlite').exists())
    def test_direction_preserves_history_and_invalidates_dependent_decisions(self):
        info=self.setup(requirements=['Keep data locally.'])
        with Memory(info['database']) as m:
            source=m.source('vision','Vision','Allow reviewed exports.','The owner approves reviewed exports.','user')
            ep=m.start('Export','Decide export policy.','planning','Preserve customer control.')
            payload={'decision':'Keep data local.','why':'Original policy.','expected':'No export.','reconsider_when':'Policy changes.','uncertainty':'Demand is unknown.','alternatives':['Reviewed export.']}
            decision=m.record(ep['id'],'decision',payload,expected_version=0,actor='test',evidence=[{'source_id':source['id'],'reason':'Consider a policy revision.'}],request_key='decision')
            values={'requirements':['Allow an export when the customer approves it.'],'reason':'The owner approves this policy.','actor':'owner',
                    'evidence':[{'source_id':source['id'],'reason':'The owner explicitly approves reviewed exports.'}],'expected_version':0}
            result=write(m,'approve_requirements','approval',values)
            self.assertEqual(result['version'],1);self.assertEqual(write(m,'approve_requirements','approval',values),result)
            self.assertEqual(m.read(decision['id'])['status'],'needs_review')
            self.assertEqual(m.read(decision['id'])['payload']['project_revision'],0)
            self.assertEqual(m.record(ep['id'],'decision',payload,expected_version=0,actor='test',evidence=[{'source_id':source['id'],'reason':'Consider a policy revision.'}],request_key='decision')['id'],decision['id'])
            history=dispatch(m,'memory_get',{'view':'direction'})['revisions']
            self.assertEqual([r['version'] for r in history],[1,0]);self.assertEqual(history[1]['requirements'],['Keep data locally.'])
            with self.assertRaises(sqlite3.IntegrityError):m.db.execute('DELETE FROM project_revisions')
            m.export_html(self.project/'view.html')
            self.assertIn('Project requirements, version 1',(self.project/'view.html').read_text())
    def test_direction_ddl_does_not_commit_outer_transaction(self):
        info=self.setup()
        with Memory(info['database']) as m:
            source=m.source('approval','Approval','Keep scope.','The user approves the stated scope.','user')
            with self.assertRaises(RuntimeError):
                with m._write():
                    m.approve_requirements(requirements=['Keep scope.'],reason='Explicit approval.',actor='owner',evidence=[{'source_id':source['id'],'reason':'Approval.'}],expected_version=0,request_key='approval')
                    raise RuntimeError('Interrupt before adapter receipt commit')
            self.assertEqual(m.direction()['version'],0)
            self.assertFalse(m.db.in_transaction)
