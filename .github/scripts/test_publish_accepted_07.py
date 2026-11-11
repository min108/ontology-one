import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

spec = importlib.util.spec_from_file_location('publisher', Path(__file__).with_name('publish_accepted_07.py'))
p = importlib.util.module_from_spec(spec); spec.loader.exec_module(p)


class FakeGitHub:
    def __init__(self):
        self.tag = self.branch = self.release = None
        self.calls = []
        self.tag_object = {'sha': 'a' * 40, 'tag': p.TAG, 'object': {'sha': p.SOURCE, 'type': 'commit'}}
        self.run = {'id': p.RUN, 'status': 'completed', 'conclusion': 'success', 'event': 'pull_request',
                    'head_sha': p.SOURCE, 'path': '.github/workflows/windows-native.yml', 'repository': {'full_name': p.REPO}}
    def api(self, endpoint, method='GET', data=None, **kwargs):
        self.calls.append((method, endpoint, data))
        if endpoint == 'actions/runs/' + str(p.RUN): return self.run
        if endpoint.startswith('actions/runs/'):
            return {'jobs': [{'id': p.JOB, 'name': 'windows', 'status': 'completed', 'conclusion': 'success',
                             'steps': [{'name': name, 'conclusion': 'success'} for name in (
                                 'Build self-contained package from fixed official dependencies',
                                 'Native standard-user acceptance without system Python', 'Retain verified distribution assets')]}]}
        if endpoint.startswith('actions/artifacts/'):
            ident = int(endpoint.rsplit('/', 1)[1]);return {'id': ident, 'name': p.ARTIFACTS[ident],
                'expired': False, 'workflow_run': {'id': p.RUN, 'head_sha': p.SOURCE}}
        if endpoint == 'git/ref/tags/' + p.TAG: return self.tag
        if endpoint == 'git/ref/heads/' + p.BRANCH: return self.branch
        if endpoint == 'git/tags': return self.tag_object
        if endpoint.startswith('git/tags/'): return self.tag_object
        if endpoint == 'git/refs':
            if data['ref'].startswith('refs/tags/'):
                self.tag = {'object': {'type': 'tag', 'sha': self.tag_object['sha']}}
            else:self.branch = {'object': {'type': 'commit', 'sha': data['sha']}}
            return {}
        if endpoint == 'releases?per_page=100':return [self.release] if self.release else []
        if endpoint.startswith('releases/') and method == 'PATCH':self.release['draft'] = False;return self.release
        if endpoint.startswith('releases/'): return self.release
        raise AssertionError(endpoint)
    def create_release(self, assets, notes):
        self.calls.append(('CREATE_RELEASE', p.TAG, None))
        assert self.release is None
        self.release = {'id': 100, 'tag_name': p.TAG, 'target_commitish':p.SOURCE, 'draft': True, 'assets': [
            {'name': name, 'state': 'uploaded', 'digest': 'sha256:' + p.sha(assets / name),
             'size': (assets / name).stat().st_size} for name in (p.ZIP, p.MYSQL, p.SUMS)]}


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.assets = self.root/'assets';self.reports = self.root/'reports'
        self.assets.mkdir();(self.reports/'reports').mkdir(parents=True)
        manifest = {'application_version': p.VERSION, 'source_commit': p.SOURCE, 'platform': 'win_amd64', 'release_ready': True}
        with zipfile.ZipFile(self.assets/p.ZIP, 'w') as archive:
            archive.writestr('Ontology-One-0.7.0-windows-x64/runtime/manifest.json', json.dumps(manifest))
        (self.assets/p.MYSQL).write_bytes(b'fixed corresponding source fixture')
        self.refresh()
        self.addCleanup(patch.stopall)
        patch.object(p, 'ZIP_SHA', p.sha(self.assets/p.ZIP)).start()
        patch.object(p, 'ZIP_BYTES', (self.assets/p.ZIP).stat().st_size).start()
        provenance = {'application_version': p.VERSION, 'source_commit': p.SOURCE, 'dependency_lock_complete': True,
                      'package': {'filename': p.ZIP, 'sha256': p.ZIP_SHA, 'bytes': p.ZIP_BYTES},
                      'corresponding_source': {'filename': p.MYSQL, 'sha256': p.sha(self.assets/p.MYSQL)}}
        self.save('windows-build-provenance.json', provenance)
        self.save('reports/windows-ci-report.json', {'source_commit': p.SOURCE, 'passed': True, 'standard_user': True,
            'delivered_python': True, 'system_python_required': False, 'model': 'synthetic_fixture_only',
            'checks': [{'name': name, 'passed': True, 'exit_code': 0} for name in ('native_units','portable_smoke','upgrade_smoke')]})
        self.save('reports/windows-portable-smoke.json', {'standard_user': True, 'platform': 'win_amd64',
            'host_runtime_required': False, 'network_model_calls': 0, 'passed': 13, 'failed': 0,
            'checks': [{'status': 'passed'}] * 13})
        self.save('reports/windows-upgrade-smoke.json', {'standard_user': True, 'passed': True,
            'source_version': '0.6.0', 'target_version': p.VERSION, 'source_commit': '7743ac75eb7f27cbc647a4f1630fe13276ebb321',
            'network_model_calls': 0, 'checks': [{'status': 'passed'}] * 11})
    def save(self, name, value): (self.reports/name).write_text(json.dumps(value))
    def refresh(self): (self.assets/p.SUMS).write_bytes(''.join(p.sha(self.assets/name)+'  '+name+'\r\n' for name in (p.ZIP,p.MYSQL)).encode())
    def test_valid_artifacts_are_verified_and_same_bytes_published(self):
        gh=FakeGitHub();p.publish(gh,self.assets,self.reports,self.root/'notes')
        self.assertFalse(gh.release['draft'])
        self.assertEqual(sum(call[0]=='CREATE_RELEASE' for call in gh.calls),1)
    def test_failed_or_other_source_run_cannot_publish(self):
        for field,value in [('conclusion','failure'),('head_sha','b'*40),('status','in_progress'),('event','push')]:
            gh=FakeGitHub();gh.run[field]=value
            with self.subTest(field=field),self.assertRaises(p.Rejected):p.verify_run(gh)
            self.assertFalse(any(call[0]!='GET' for call in gh.calls))
    def test_checksum_paths_missing_or_extra_files_are_rejected(self):
        original=(self.assets/p.SUMS).read_bytes()
        for malformed in (original.replace(p.ZIP.encode(),b'../escape.zip'),original+original,original.splitlines()[0]+b'\n'):
            (self.assets/p.SUMS).write_bytes(malformed)
            with self.assertRaises(p.Rejected):p.verify_files(self.assets,self.reports)
        (self.assets/p.SUMS).write_bytes(original)
        (self.assets/'unreviewed.txt').write_text('extra')
        with self.assertRaises(p.Rejected):p.verify_files(self.assets,self.reports)
    def test_manifest_source_and_release_ready_are_not_inferred_from_checksum(self):
        for field,value in [('source_commit','b'*40),('release_ready',False),('application_version','0.6.0')]:
            with zipfile.ZipFile(self.assets/p.ZIP,'w') as archive:
                manifest={'application_version':p.VERSION,'source_commit':p.SOURCE,'platform':'win_amd64','release_ready':True,field:value}
                archive.writestr('Ontology-One-0.7.0-windows-x64/runtime/manifest.json',json.dumps(manifest))
            self.refresh()
            with patch.object(p,'ZIP_SHA',p.sha(self.assets/p.ZIP)),patch.object(p,'ZIP_BYTES',(self.assets/p.ZIP).stat().st_size):
                with self.subTest(field=field),self.assertRaises(p.Rejected):p.verify_files(self.assets,self.reports)
    def test_incomplete_provenance_and_failed_native_report_are_rejected(self):
        provenance=p.load(self.reports/'windows-build-provenance.json');provenance['dependency_lock_complete']=False
        self.save('windows-build-provenance.json',provenance)
        with self.assertRaises(p.Rejected):p.verify_files(self.assets,self.reports)
        provenance['dependency_lock_complete']=True;self.save('windows-build-provenance.json',provenance)
        ci=p.load(self.reports/'reports/windows-ci-report.json');ci['checks'][0]['exit_code']=1
        self.save('reports/windows-ci-report.json',ci)
        with self.assertRaises(p.Rejected):p.verify_files(self.assets,self.reports)
    def test_existing_same_refs_are_not_rewritten(self):
        gh=FakeGitHub();gh.tag={'object':{'type':'tag','sha':'a'*40}};gh.branch={'object':{'type':'commit','sha':p.SOURCE}}
        p.ensure_refs(gh);self.assertTrue(all(x[0]=='GET' for x in gh.calls))
    def test_conflicting_refs_are_rejected_before_any_write(self):
        for wrong in ('tag','branch'):
            gh=FakeGitHub()
            if wrong=='tag':gh.tag={'object':{'type':'tag','sha':'a'*40}};gh.tag_object['object']['sha']='b'*40
            else:gh.branch={'object':{'type':'commit','sha':'b'*40}}
            with self.subTest(wrong=wrong),self.assertRaises(p.Rejected):p.ensure_refs(gh)
            self.assertTrue(all(x[0]=='GET' for x in gh.calls))
    def test_existing_release_is_never_overwritten_or_reuploaded(self):
        gh=FakeGitHub();gh.release={'id':1,'tag_name':p.TAG,'draft':False}
        with self.assertRaises(p.Rejected):p.publish(gh,self.assets,self.reports,self.root/'notes')
        self.assertTrue(all(x[0]=='GET' for x in gh.calls))

    def prepared_draft(self):
        gh=FakeGitHub()
        gh.create_release(self.assets,self.root/'notes')
        gh.release['id']=p.DRAFT_RELEASE_ID
        gh.tag={'object':{'type':'tag','sha':'a'*40}}
        gh.branch={'object':{'type':'commit','sha':p.SOURCE}}
        gh.calls.clear()
        return gh

    def test_resume_correct_specific_draft_only_patches_without_creating_or_uploading(self):
        gh=self.prepared_draft()
        p.resume(gh,self.assets,self.reports)
        writes=[call for call in gh.calls if call[0]!='GET']
        self.assertEqual(writes,[('PATCH','releases/'+str(p.DRAFT_RELEASE_ID),{'draft':False,'make_latest':'true'})])
        self.assertFalse(gh.release['draft'])

    def test_resume_wrong_draft_source_or_assets_cannot_patch(self):
        for change in ('id','tag','source','digest','published'):
            gh=self.prepared_draft()
            if change=='id':gh.release['id']=1
            elif change=='tag':gh.release['tag_name']='v0.6.0'
            elif change=='source':gh.release['target_commitish']='b'*40
            elif change=='digest':gh.release['assets'][0]['digest']='sha256:'+'b'*64
            else:gh.release['draft']=False
            with self.subTest(change=change),self.assertRaises(p.Rejected):p.resume(gh,self.assets,self.reports)
            self.assertTrue(all(call[0]=='GET' for call in gh.calls))


if __name__=='__main__':unittest.main()
