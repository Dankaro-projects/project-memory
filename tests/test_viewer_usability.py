"""Viewer projections preserve decision attribution, scope and bounded groups."""
import tempfile
from pathlib import Path
import unittest

from memory_module import Memory
from memory_module.install import setup
import json

from memory_module.api import row, latest_decisions, page
from memory_module.planning import board
from memory_module.workspace import action


def fixture(root):
    info=setup(root,requirements=['Reject invalid UTF-8.','Preserve the tagged Latin-1 exception.'])
    with Memory(info['database']) as m:
        document=root/'Encoding contract.md'
        document.write_text('# Encoding contract\n\nThe importer supports two explicit input paths.\n\n## Strict input\n\nReject invalid UTF-8.\n\n## Tagged exception\n\nPreserve the tagged Latin-1 exception.\n\n## Evidence\n\nCompare both fixtures before accepting an outcome.\n')
        source=m.document(str(document))
        refs=[{'source_id':source['id'],'reason':'The contract requires both encoding paths.'}]
        work=action(m,'plan',{'title':'Preserve supported encodings','objective':'Import valid files without losing the tagged exception.',
            'criterion':'Both documented input paths pass.','subject':'code','payload':{'state':'in_progress','owner':'human',
            'next_action':'Review the repaired decoder.','scope':'Change decoding only. Preserve the tagged exception.',
            'autonomy':'suggest','reason':'The user requests a comparison of both paths.'}},'work')
        episode=work['episode_id']
        def record(kind,payload,**kwargs):
            return m.record(episode,kind,payload,expected_version=m.episode(episode)['version'],
                            request_key=kind+str(m.episode(episode)['version']),actor='fixture',evidence=refs,**kwargs)
        old=record('decision',{'decision':'Decode every input as strict UTF-8.','why':'Strict decoding exposes invalid input.',
            'expected':'Every supported fixture imports successfully.','uncertainty':'The tagged path has not been checked.',
            'alternatives':['Keep a separate tagged decoding path.'],'reconsider_when':'A supported input fails.'})
        record('action',{'action':'Run both encoding fixtures.'},decision_id=old['id'])
        failed=record('outcome',{'observed':'The tagged Latin-1 fixture failed.','assessment':'bad',
            'assessment_reason':'The choice omitted a required exception.','severity':'major','attribution':'Every file used UTF-8.',
            'completion':'partial'},decision_id=old['id'])
        revised=record('decision',{'decision':'Use strict UTF-8 and retain a separate tagged Latin-1 path.',
            'why':'The first attempt failed the tagged fixture; the contract requires that exception.',
            'expected':'Both fixtures import through their documented paths.','uncertainty':'Production data remains untested.',
            'alternatives':['Remove support for the tagged path.'],'reconsider_when':'The encoding contract changes.'},supersedes=old['id'])
        record('action',{'action':'Repair the decoder and run both fixtures again.'},decision_id=revised['id'])
        good=record('outcome',{'observed':'Both encoding fixtures passed after the repair.','assessment':'good',
            'assessment_reason':'Strict validation and the tagged exception both passed.','severity':'none',
            'attribution':'The repair restores the explicit branch.','completion':'complete'},decision_id=revised['id'])
        lesson=record('lesson',{'when':'An input format has a documented exception.','do':'Check the normal and exceptional paths.',
            'because':'The first attempt omitted a supported path.','exceptions':'Undocumented encodings remain unsupported.',
            'pattern_type':'recovery'})
        for state,title,next_action in [('blocked','Validate the staging import','Obtain access to staging.'),
                                       ('review','Review onboarding wording','Confirm that the wording preserves the exception.'),
                                       ('ready','Prepare release notes','Describe the supported encoding paths.')]:
            action(m,'plan',{'title':title,'objective':next_action,'criterion':'The result has explicit evidence.',
                'subject':'writing' if state!='blocked' else 'code','payload':{'state':state,'next_action':next_action,
                'scope':'Preserve the approved conditions.','autonomy':'suggest','reason':'The user requests this step.'}},state)
        m.export_html(root/'snapshot.html',include_bodies=True)
        return {**info,'work':episode,'old':old['id'],'revised':revised['id'],'failed':failed['id'],'good':good['id'],
                'lesson':lesson['id'],'source':source['id']}


class ViewerUsabilityTests(unittest.TestCase):
    def test_overview_shows_latest_choice_and_keeps_prior_history(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            info=fixture(Path(folder))
            with Memory(info['database']) as m:
                result=latest_decisions(m)
                self.assertEqual(result['total'],1)
                self.assertEqual([r['id'] for r in result['records']],[info['revised']])
                self.assertEqual(result['records'][0]['outcome']['id'],info['good'])
                self.assertEqual(page(m,{'view':'decisions'})['total'],2)
                self.assertEqual(row(m,info['old'])['outcome']['id'],info['failed'])

    def test_projection_keeps_the_failed_and_revised_outcomes_separate(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            info=fixture(Path(folder))
            with Memory(info['database']) as m:
                old=row(m,info['old']);new=row(m,info['revised'])
                self.assertEqual(old['outcome']['id'],info['failed'])
                self.assertEqual(new['outcome']['id'],info['good'])
                self.assertEqual(old['outcome']['detail']['payload']['assessment'],'bad')
                self.assertEqual(new['episode_title'],'Preserve supported encodings')
                self.assertEqual(new['detail']['supersedes'],info['old'])
                self.assertNotIn('body',row(m,info['source'])['detail'])

    def test_overview_counts_all_work_without_unbounded_group_payloads(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            info=fixture(Path(folder))
            with Memory(info['database']) as m:
                for i in range(12):
                    action(m,'plan',{'title':f'Ready action {i}','objective':'Inspect a bounded fixture.','criterion':'Preserve its exception.',
                        'subject':'code','payload':{'state':'ready','next_action':'Inspect the fixture.','scope':'Keep the exception.',
                        'autonomy':'suggest','reason':'The user requests this fixture.'}},f'ready-{i}')
                result=board(m,limit=3,grouped=True)
                self.assertEqual(result['counts']['ready'],13)
                self.assertEqual(len(result['groups']['ready']),3)
                self.assertEqual(result['counts']['in_progress'],1)
                self.assertEqual(sum(result['counts'].values()),result['total'])
                self.assertNotIn('groups',board(m,limit=3))


    def test_snapshot_embeds_the_latest_choice_and_both_outcomes(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            info=fixture(Path(folder))
            text=(Path(folder)/'snapshot.html').read_text().split('<script id="memory-data" type="application/json">')[1].split('</script>')[0]
            responses=json.loads(text)['responses']
            latest=responses['now']['latest_decisions']
            self.assertEqual([d['id'] for d in latest],[info['revised']])
            self.assertEqual(latest[0]['outcome']['assessment'],'good')
            self.assertEqual(responses['records?limit=100&view=decisions']['total'],2)
            self.assertEqual(responses['record?id='+info['old']]['record']['outcome']['id'],info['failed'])
            self.assertEqual(responses['now']['counts']['blocked'],1)
            self.assertIn('lineage?id='+info['revised'],responses)
            self.assertIn('Encoding contract',responses['record?body_offset=0&id='+info['source']]['record']['detail']['body'])


if __name__=='__main__':unittest.main()
