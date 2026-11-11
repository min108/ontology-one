"""Publish exactly the already accepted Windows package; never rebuild it."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import zipfile

REPO = 'min108/ontology-one'
SOURCE = '3831f9096110096912dec8f5c35fa766b0de8d39'
RUN = 42982936247
JOB = 130397346037
VERSION = '0.7.0'
TAG = 'v' + VERSION
BRANCH = 'release/' + VERSION
ZIP = 'ontology-one-0.7.0-windows-x64.zip'
MYSQL = 'mysql-8.4.13-corresponding-source.tar.gz'
SUMS = 'SHA256SUMS.txt'
ZIP_SHA = 'e03c9ff117b13762fb5db37181d73daea25c3814eb07dd7e29dfd384d5d505fc'
ZIP_BYTES = 206961985
DRAFT_RELEASE_ID = 441408050
ARTIFACTS = {13838634705: 'windows-distribution-' + SOURCE,
             13838649561: 'windows-native-reports-' + SOURCE}


class Rejected(Exception):
    pass


def require(value, code):
    if not value:
        raise Rejected(code)


def load(path, maximum=1024 * 1024):
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= maximum, 'REPORT_FILE_INVALID')
    return json.loads(path.read_text(encoding='utf-8-sig'))


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


class GitHub:
    def api(self, endpoint, method='GET', data=None, *, optional=False):
        command = ['gh', 'api', '--include', '--method', method, 'repos/' + REPO + '/' + endpoint]
        if data is not None:
            command += ['--input', '-']
        result = subprocess.run(command, input=json.dumps(data) if data is not None else None,
                                text=True, capture_output=True)
        match = re.match(r'HTTP/\S+\s+(\d{3})[^\r\n]*\r?\n', result.stdout)
        require(match is not None, 'GITHUB_RESPONSE_INVALID')
        status = int(match.group(1))
        if optional and status == 404:
            return None
        require(result.returncode == 0 and 200 <= status < 300,
                'GITHUB_OPERATION_FAILED:' + method + ':' + endpoint.split('?', 1)[0] + ':HTTP' + str(status))
        parts = re.split(r'\r?\n\r?\n', result.stdout, maxsplit=1)
        require(len(parts) == 2, 'GITHUB_RESPONSE_INVALID')
        return json.loads(parts[1]) if parts[1].strip() else None

    def create_release(self, assets, notes):
        result = subprocess.run(['gh', 'release', 'create', TAG, *[str(assets / name) for name in (ZIP, MYSQL, SUMS)],
                                 '--repo', REPO, '--verify-tag', '--target', SOURCE, '--draft',
                                 '--title', '集团本体管理平台 0.7.0', '--notes-file', str(notes)],
                                text=True, capture_output=True)
        require(result.returncode == 0, 'RELEASE_CREATE_FAILED')


def verify_run(gh):
    run = gh.api('actions/runs/' + str(RUN))
    require(run.get('id') == RUN and run.get('status') == 'completed' and run.get('conclusion') == 'success'
            and run.get('event') == 'pull_request' and run.get('head_sha') == SOURCE
            and run.get('path') == '.github/workflows/windows-native.yml'
            and run.get('repository', {}).get('full_name') == REPO, 'QA_RUN_NOT_ACCEPTED')
    jobs = gh.api('actions/runs/' + str(RUN) + '/jobs?per_page=100').get('jobs', [])
    windows = [job for job in jobs if job.get('id') == JOB and job.get('name') == 'windows']
    require(len(windows) == 1 and windows[0].get('conclusion') == 'success'
            and windows[0].get('status') == 'completed', 'QA_JOB_NOT_ACCEPTED')
    for name in ('Build self-contained package from fixed official dependencies',
                 'Native standard-user acceptance without system Python', 'Retain verified distribution assets'):
        steps = [step for step in windows[0].get('steps', []) if step.get('name') == name]
        require(len(steps) == 1 and steps[0].get('conclusion') == 'success', 'QA_STEP_NOT_ACCEPTED')
    for ident, name in ARTIFACTS.items():
        artifact = gh.api('actions/artifacts/' + str(ident))
        require(artifact.get('id') == ident and artifact.get('name') == name and artifact.get('expired') is False
                and artifact.get('workflow_run', {}).get('id') == RUN
                and artifact.get('workflow_run', {}).get('head_sha') == SOURCE, 'QA_ARTIFACT_NOT_ACCEPTED')


def verify_files(assets, reports):
    require(assets.is_dir() and not assets.is_symlink(), 'ASSET_DIRECTORY_INVALID')
    require({p.name for p in assets.iterdir()} == {ZIP, MYSQL, SUMS}, 'ASSET_SET_INVALID')
    for name in (ZIP, MYSQL, SUMS):
        path = assets / name
        require(stat.S_ISREG(path.lstat().st_mode) and not path.is_symlink(), 'ASSET_FILE_INVALID')
    require((assets / SUMS).stat().st_size < 4096, 'CHECKSUM_FILE_INVALID')
    lines = (assets / SUMS).read_text(encoding='ascii').splitlines()
    require(len(lines) == 2, 'CHECKSUM_SET_INVALID')
    expected = {}
    for line in lines:
        match = re.fullmatch(r'([a-f0-9]{64})  ([A-Za-z0-9._-]+)', line)
        require(match is not None and match[2] in (ZIP, MYSQL) and match[2] not in expected, 'CHECKSUM_PATH_INVALID')
        expected[match[2]] = match[1]
    require(set(expected) == {ZIP, MYSQL}, 'CHECKSUM_SET_INVALID')
    hashes = {name: sha(assets / name) for name in (ZIP, MYSQL, SUMS)}
    require(all(hashes[name] == digest for name, digest in expected.items()), 'ASSET_CHECKSUM_MISMATCH')
    require(hashes[ZIP] == ZIP_SHA and (assets / ZIP).stat().st_size == ZIP_BYTES, 'ACCEPTED_PACKAGE_MISMATCH')
    with zipfile.ZipFile(assets / ZIP) as archive:
        manifest_path = 'Ontology-One-0.7.0-windows-x64/runtime/manifest.json'
        entries = [entry for entry in archive.infolist() if entry.filename == manifest_path]
        require(len(entries) == 1 and 0 < entries[0].file_size <= 16 * 1024 * 1024, 'PACKAGE_MANIFEST_INVALID')
        manifest = json.loads(archive.read(entries[0]))
        require(manifest.get('application_version') == VERSION and manifest.get('source_commit') == SOURCE
                and manifest.get('platform') == 'win_amd64' and manifest.get('release_ready') is True,
                'PACKAGE_SOURCE_MISMATCH')
    provenance = load(reports / 'windows-build-provenance.json')
    require(provenance.get('application_version') == VERSION and provenance.get('source_commit') == SOURCE
            and provenance.get('dependency_lock_complete') is True
            and provenance.get('package') == {'filename': ZIP, 'sha256': hashes[ZIP], 'bytes': ZIP_BYTES}
            and provenance.get('corresponding_source') == {'filename': MYSQL, 'sha256': hashes[MYSQL]}, 'BUILD_PROVENANCE_MISMATCH')
    ci = load(reports / 'reports/windows-ci-report.json')
    require(ci.get('source_commit') == SOURCE and ci.get('passed') is True and ci.get('standard_user') is True
            and ci.get('delivered_python') is True and ci.get('system_python_required') is False
            and ci.get('model') == 'synthetic_fixture_only', 'NATIVE_REPORT_NOT_ACCEPTED')
    checks = ci.get('checks', [])
    require(len(checks) == 3 and {x.get('name') for x in checks} == {'native_units', 'portable_smoke', 'upgrade_smoke'}
            and all(x.get('passed') is True and type(x.get('exit_code')) is int and x['exit_code'] == 0 for x in checks),
            'NATIVE_CHECKS_NOT_ACCEPTED')
    portable = load(reports / 'reports/windows-portable-smoke.json')
    require(portable.get('standard_user') is True and portable.get('platform') == 'win_amd64'
            and portable.get('host_runtime_required') is False and portable.get('network_model_calls') == 0
            and type(portable.get('passed')) is int and portable['passed'] == 13 and portable.get('failed') == 0
            and len(portable.get('checks', [])) == 13 and all(x.get('status') == 'passed' for x in portable['checks']), 'PORTABLE_NOT_ACCEPTED')
    upgrade = load(reports / 'reports/windows-upgrade-smoke.json')
    require(upgrade.get('passed') is True and upgrade.get('standard_user') is True
            and upgrade.get('source_version') == '0.6.0' and upgrade.get('target_version') == VERSION
            and upgrade.get('source_commit') == '7743ac75eb7f27cbc647a4f1630fe13276ebb321'
            and upgrade.get('network_model_calls') == 0 and len(upgrade.get('checks', [])) == 11
            and all(x.get('status') == 'passed' for x in upgrade['checks']), 'UPGRADE_NOT_ACCEPTED')
    return hashes


def ensure_refs(gh, *, create=True):
    tag = gh.api('git/ref/tags/' + TAG, optional=True)
    branch = gh.api('git/ref/heads/' + BRANCH, optional=True)
    require(create or tag is not None and branch is not None, 'ACCEPTED_REFS_MISSING')
    if branch is not None:
        require(branch.get('object', {}).get('type') == 'commit' and branch['object'].get('sha') == SOURCE, 'BRANCH_SOURCE_CONFLICT')
    if tag is not None:
        require(tag.get('object', {}).get('type') == 'tag', 'ANNOTATED_TAG_REQUIRED')
        existing = gh.api('git/tags/' + tag['object']['sha'])
        require(existing.get('tag') == TAG and existing.get('object', {}).get('sha') == SOURCE
                and existing['object'].get('type') == 'commit', 'TAG_SOURCE_CONFLICT')
    if tag is None:
        obj = gh.api('git/tags', 'POST', {'tag': TAG, 'message': '0.7.0 accepted Windows distribution; QA run ' + str(RUN),
                                        'object': SOURCE, 'type': 'commit'})
        require(obj.get('object', {}).get('sha') == SOURCE and obj['object'].get('type') == 'commit', 'TAG_OBJECT_MISMATCH')
        gh.api('git/refs', 'POST', {'ref': 'refs/tags/' + TAG, 'sha': obj['sha']})
        tag = gh.api('git/ref/tags/' + TAG)
    obj = tag.get('object', {})
    require(obj.get('type') == 'tag', 'ANNOTATED_TAG_REQUIRED')
    tag_object = gh.api('git/tags/' + obj['sha'])
    require(tag_object.get('tag') == TAG and tag_object.get('object', {}).get('type') == 'commit'
            and tag_object['object'].get('sha') == SOURCE, 'TAG_SOURCE_CONFLICT')
    if branch is None:
        gh.api('git/refs', 'POST', {'ref': 'refs/heads/' + BRANCH, 'sha': SOURCE})
        branch = gh.api('git/ref/heads/' + BRANCH)
    require(branch.get('object', {}).get('type') == 'commit' and branch['object'].get('sha') == SOURCE, 'BRANCH_SOURCE_CONFLICT')


def release_by_tag(gh):
    # The tag endpoint excludes drafts. Use the authenticated release list and
    # fail closed instead of treating an unreadable draft as an absent release.
    releases = gh.api('releases?per_page=100')
    require(isinstance(releases, list) and len(releases) < 100, 'RELEASE_LIST_INVALID')
    matches = [item for item in releases if item.get('tag_name') == TAG]
    require(len(matches) <= 1, 'RELEASE_TAG_NOT_UNIQUE')
    return matches[0] if matches else None


def publish_draft(gh, release, assets, hashes, *, expected_id=None):
    require(release.get('tag_name') == TAG and release.get('draft') is True
            and release.get('target_commitish') == SOURCE
            and (expected_id is None or release.get('id') == expected_id), 'DRAFT_RELEASE_MISMATCH')
    observed = release.get('assets', [])
    require(len(observed) == 3 and {x.get('name') for x in observed} == {ZIP, MYSQL, SUMS}, 'RELEASE_ASSET_SET_INVALID')
    for item in observed:
        require(item.get('state') == 'uploaded' and item.get('digest') == 'sha256:' + hashes[item['name']]
                and item.get('size') == (assets / item['name']).stat().st_size, 'RELEASE_ASSET_MISMATCH')
    gh.api('releases/' + str(release['id']), 'PATCH', {'draft': False, 'make_latest': 'true'})
    final = gh.api('releases/' + str(release['id']))
    require(final.get('draft') is False and final.get('tag_name') == TAG, 'RELEASE_NOT_PUBLIC')
    print(json.dumps({'released': TAG, 'source_commit': SOURCE, 'qa_run': RUN,
                      'zip_sha256': hashes[ZIP], 'zip_bytes': ZIP_BYTES, 'rebuilt': False}))


def publish(gh, assets, reports, notes):
    verify_run(gh)
    hashes = verify_files(assets, reports)
    require(release_by_tag(gh) is None, 'RELEASE_ALREADY_EXISTS')
    ensure_refs(gh)
    notes.write_text('集团本体管理平台 0.7.0\n\n本包直接复用原生 Windows 验收通过的同一份产物，未重新构建。\n'
                     '来源提交：' + SOURCE + '\n验收记录：https://github.com/' + REPO + '/actions/runs/' + str(RUN) + '\n\n'
                     '原生单元282项（272通过、10平台跳过），便携启停13/13，正式0.6.0→0.7.0升级11/11。\n'
                     '提供中文对话技能建设、真实公开正文流、后台队列与恢复、统一智能配置及可选模拟资料。\n'
                     '升级时关闭旧平台，将新包解压到全新目录，运行升级平台.bat并选择旧0.6.0目录；旧目录保留。\n'
                     '请保留SHA256SUMS.txt及随附MySQL对应源码。历史版本不覆盖。\n', encoding='utf-8')
    gh.create_release(assets, notes)
    release = release_by_tag(gh)
    require(release is not None, 'DRAFT_RELEASE_MISSING')
    publish_draft(gh, release, assets, hashes)


def resume(gh, assets, reports):
    """Only finish the specific fully uploaded draft left by the first run."""
    verify_run(gh)
    hashes = verify_files(assets, reports)
    ensure_refs(gh, create=False)
    release = gh.api('releases/' + str(DRAFT_RELEASE_ID))
    listed = release_by_tag(gh)
    require(listed is not None and listed.get('id') == DRAFT_RELEASE_ID, 'DRAFT_RELEASE_NOT_UNIQUE')
    publish_draft(gh, release, assets, hashes, expected_id=DRAFT_RELEASE_ID)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('verify-run', 'publish', 'resume'))
    parser.add_argument('--assets', type=Path, default=Path('release-assets'))
    parser.add_argument('--reports', type=Path, default=Path('qa-reports'))
    args = parser.parse_args()
    require(os.environ.get('GITHUB_REPOSITORY') == REPO and os.environ.get('GITHUB_REF') == 'refs/heads/qa/publish-accepted-0.7.0', 'PUBLISH_CONTEXT_INVALID')
    gh = GitHub()
    if args.action == 'verify-run':
        verify_run(gh)
        print('ACCEPTED_QA_RUN_VERIFIED')
    elif args.action == 'publish':
        publish(gh, args.assets, args.reports, Path('accepted-release-notes.txt'))
    else:
        resume(gh, args.assets, args.reports)


if __name__ == '__main__':
    try:
        main()
    except (Rejected, OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as error:
        print(str(error) if isinstance(error, Rejected) else 'PUBLISH_VALIDATION_FAILED')
        raise SystemExit(1)
