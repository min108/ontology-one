"""Real QA19 Windows distribution -> current distribution, standard-user upgrade.

The baseline archive is SHA256-checked by CI before extraction. Credentials and
asset bodies stay in process memory; the report contains fixed checks only.
"""
import argparse
from contextlib import suppress
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time

from backend.version import APP_VERSION
from tests.windows_portable_smoke import Acceptance, CheckFailure, require

BASELINE_COMMIT = '8aba44f4b1ccdc33be4fa2a821856047ccefb2ab'


def tree_digest(root):
    result = hashlib.sha256()
    for path in sorted(root.rglob('*')):
        if path.is_file():
            result.update(path.relative_to(root).as_posix().encode('utf-8'))
            with path.open('rb') as stream:
                result.update(hashlib.file_digest(stream, 'sha256').digest())
    return result.digest()


def run(package, baseline, report):
    require(os.name == 'nt' and not ctypes.windll.shell32.IsUserAnAdmin(), 'STANDARD_WINDOWS_USER_REQUIRED')
    manifest = json.loads((baseline/'runtime/manifest.json').read_text(encoding='utf-8'))
    require(manifest['application_version']=='0.5.0' and manifest['source_commit']==BASELINE_COMMIT,
            'PINNED_REAL_BASELINE_REQUIRED')
    require(not (baseline/'data').exists(), 'FRESH_BASELINE_REQUIRED')
    target = package.parent/'升级目标 Windows !'/f'Ontology-One-{APP_VERSION}-windows-x64'
    require(not target.exists(), 'FRESH_UPGRADE_TARGET_REQUIRED')
    shutil.copytree(package, target, ignore=shutil.ignore_patterns('data', '.platform-control', '__pycache__'))
    old, new = Acceptance(baseline, report), Acceptance(target, report)
    rows, began = [], time.monotonic()

    def check(name, action):
        started = time.monotonic()
        try:
            action()
            rows.append({'name':name, 'status':'passed', 'duration_ms':round((time.monotonic()-started)*1000)})
        except Exception as exc:
            row={'name':name, 'status':'failed', 'code':str(exc) if isinstance(exc,CheckFailure) else type(exc).__name__}
            failure = new.last_launch_failure or old.last_launch_failure
            if failure:
                row['launcher_failure']=failure
            rows.append(row)
            raise

    def reject_running_source():
        result=new.command('upgrade','--from',str(baseline),'--non-interactive',success=False)
        require(result.get('error')=='UPGRADE_SOURCE_BUSY','RUNNING_SOURCE_NOT_REJECTED')
        require(not (target/'data').exists(),'FAILED_UPGRADE_CREATED_DATA')

    def copied():
        nonlocal before
        before=tree_digest(baseline/'data')
        result=new.command('upgrade','--from',str(baseline),'--non-interactive')
        require(result.get('upgraded') and result.get('source_preserved'),'UPGRADE_NOT_COMPLETED')
        require(tree_digest(baseline/'data')==before,'SOURCE_MODIFIED_DURING_COPY')
        new.username,new.password,new.entity=old.username,old.password,old.entity
        new.api_port,new.mysql_port=old.api_port,old.mysql_port
        new.environment.update(ONTOLOGY_BOOTSTRAP_USERNAME=old.username,ONTOLOGY_BOOTSTRAP_PASSWORD=old.password)

    def verify_existing():
        import httpx
        with httpx.Client(base_url=f'http://127.0.0.1:{new.api_port}',trust_env=False,timeout=30) as client:
            response=client.post('/api/auth/login',json={'username':old.username,'password':old.password})
            require(response.status_code==200 and not response.json()['must_change_password'],'UPGRADED_ACCOUNT_NOT_PRESERVED')
            response=client.get('/api/entities/'+old.entity)
            require(response.status_code==200,'UPGRADED_ASSET_MISSING')
            value=response.json()
            require(value['version']==3 and value['status']=='archived'
                and value['payload'].get('portable_acceptance') is True,'UPGRADED_ASSET_CHANGED')
            health=client.get('/api/health')
            require(health.status_code==200 and health.json()['version']==APP_VERSION,'NEW_VERSION_NOT_RUNNING')
        status=new.command('status')
        require(status.get('processes',{}).get('agent',{}).get('alive') is True,'SHARED_AGENT_NOT_RUNNING')

    def reject_existing_target():
        result=new.command('upgrade','--from',str(baseline),'--non-interactive',success=False)
        require(result.get('error')=='UPGRADE_TARGET_NOT_EMPTY','UPGRADE_OVERWROTE_TARGET')

    before=None
    try:
        check('real_0_5_first_start',old.start)
        check('original_account_password_and_asset',old.account_and_crud)
        check('running_source_copy_refused',reject_running_source)
        check('old_services_stopped',old.stopped)
        check('cold_copy_keeps_source_and_credentials',copied)
        check('new_version_migration_and_shared_broker',new.start)
        check('original_login_role_asset_and_version',verify_existing)
        check('new_version_stop',new.stopped)
        check('existing_target_not_overwritten',reject_existing_target)
        check('restart_preserves_copied_account_and_asset',new.persistence)
        check('upgrade_owned_process_cleanup',new.stopped)
        require(tree_digest(baseline/'data')==before,'NEW_VERSION_WROTE_OLD_DATABASE')
    finally:
        for fixture in (new,old):
            with suppress(Exception):fixture.stopped()
        report.write_text(json.dumps({'source_version':'0.5.0','source_commit':BASELINE_COMMIT,
            'target_version':APP_VERSION,'standard_user':True,'network_model_calls':0,
            'duration_ms':round((time.monotonic()-began)*1000),'checks':rows,
            'passed':bool(rows) and all(row['status']=='passed' for row in rows)},indent=2)+'\n',encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package',type=Path,required=True)
    parser.add_argument('--baseline',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    try:
        run(args.package.resolve(),args.baseline.resolve(),args.report.resolve())
        print('WINDOWS_UPGRADE_SMOKE_PASSED')
        return 0
    except Exception as exc:
        print('WINDOWS_UPGRADE_SMOKE_FAILED:'+ (str(exc) if isinstance(exc,CheckFailure) else type(exc).__name__))
        return 1


if __name__=='__main__':sys.exit(main())
