"""Run native acceptance with the delivered interpreter under a standard user.

Only synthetic model fixtures are used. Reports never include deployment data
or user-supplied credentials. The CI account and its directory are disposable.
"""
from pathlib import Path
import argparse
import ctypes
import json
import os
import subprocess
import sys


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package',type=Path,required=True);parser.add_argument('--report-dir',type=Path,required=True)
    parser.add_argument('--upgrade-baseline',type=Path,required=True)
    args=parser.parse_args();package=args.package.resolve();reports=args.report_dir.resolve();reports.mkdir(parents=True,exist_ok=True)
    if os.name!='nt' or ctypes.windll.shell32.IsUserAnAdmin():raise SystemExit('Native acceptance requires a standard Windows user')
    if 'DEEPSEEK_API_KEY' in os.environ:raise SystemExit('Provider credentials are forbidden in CI')
    python=package/'runtime/python/python.exe'
    if Path(sys.executable).resolve()!=python:raise SystemExit('Acceptance must use the delivered interpreter')
    # Never inherit runneradmin's private profile or temp locations after a
    # credential switch. These directories belong to the disposable QA user.
    profile=package.parent/'profile';temporary=package.parent/'temp'
    for directory in (profile,temporary,profile/'AppData/Local',profile/'AppData/Roaming'):
        directory.mkdir(parents=True,exist_ok=True)
    os.environ.update(USERPROFILE=str(profile),TEMP=str(temporary),TMP=str(temporary),
        LOCALAPPDATA=str(profile/'AppData/Local'),APPDATA=str(profile/'AppData/Roaming'))
    allowed=('SYSTEMROOT','WINDIR','COMSPEC','PATHEXT','TEMP','TMP','USERPROFILE','APPDATA','LOCALAPPDATA')
    env={key:os.environ[key] for key in allowed if key in os.environ}
    env.update(PATH=str(Path(os.environ['SystemRoot'])/'System32')+';'+os.environ['SystemRoot'],
        RUN_HARNESS_SDK_TESTS='1',RUN_WINDOWS_SANDBOX_TESTS='1',ONTOLOGY_REQUIRE_STANDARD_USER='1',
        ONTOLOGY_SKILL_PYTHON_ROOT=str(package/'runtime/skill-python'),
        PYTHONDONTWRITEBYTECODE='1',PYTHONNOUSERSITE='1')
    modules=['tests.test_platform_io','tests.test_windows_process','tests.test_windows_async_process',
        'tests.test_mcp_transport','tests.test_skill_harness','tests.test_windows_sandbox_native',
        'tests.test_windows_launcher','tests.test_windows_initialize','tests.test_windows_distribution',
        'tests.test_source_adapters','tests.test_windows_mysql_utf8',
        'tests.test_harness_worker_modes','tests.test_windows_upgrade']
    modules += sorted('tests.' + path.stem for path in (package/'tests').glob('test_agent*.py'))
    manifest=json.loads((package/'runtime/manifest.json').read_text(encoding='utf-8'))
    diagnostics={'source_commit':manifest['source_commit'],'sys_path':sys.path,'prefix':sys.prefix,
        'backend_exists':(package/'backend/__init__.py').is_file(),
        'pth':(package/'runtime/python/python312._pth').read_text(encoding='utf-8')}
    (reports/'embedded-paths.json').write_text(json.dumps(diagnostics,ensure_ascii=True,indent=2)+'\n',encoding='utf-8')
    results={'source_commit':manifest['source_commit'],'standard_user':True,'delivered_python':True,
             'system_python_required':False,'model':'synthetic_fixture_only','checks':[]}
    commands=[('native_units',[str(python),'-I','-X','utf8','-B','-m','unittest',*modules,'-v'],600),
              ('portable_smoke',[str(python),'-I','-X','utf8','-B',str(package/'tests/windows_portable_smoke.py'),
                '--package',str(package),'--report',str(reports/'windows-portable-smoke.json')],1800),
              ('upgrade_smoke',[str(python),'-I','-X','utf8','-B',str(package/'tests/windows_upgrade_smoke.py'),
                '--package',str(package),'--baseline',str(args.upgrade_baseline.resolve()),
                '--report',str(reports/'windows-upgrade-smoke.json')],1500)]
    try:
        import backend
        if not Path(backend.__file__).resolve().is_relative_to(package/'backend'):
            raise SystemExit('Relocated application import escaped the delivered package')
        from backend.windows_process import spawn_job_process, DEVNULL, STDOUT
        for name,command,timeout in commands:
            with (reports/(name+'.log')).open('wb') as log:
                try:
                    with spawn_job_process(command,cwd=package,env=env,stdin=DEVNULL,stdout=log,stderr=STDOUT) as child:
                        code=child.wait(timeout=timeout)
                except TimeoutError:code=124
            results['checks'].append({'name':name,'exit_code':code,'passed':code==0,
                                      'status':'passed' if code==0 else 'failed'})
            if code!=0:
                # Installation takes several minutes and cannot make a failed
                # isolation suite releasable. Do not repeat it for diagnostics;
                # a green native suite must still run the entire startup smoke.
                for pending,_,_ in commands[len(results['checks']):]:
                    results['checks'].append({'name':pending,'exit_code':None,
                        'passed':False,'status':'not_run','reason':name+'_failed'})
                break
    finally:
        # This uses the instance's authenticated stop API; it never selects PIDs.
        try:
            subprocess.run([str(python),'-I','-X','utf8','-B',str(package/'ops/windows_launcher.py'),'stop','--json'],
                           cwd=package,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=60)
        except (OSError,subprocess.TimeoutExpired):
            results['checks'].append({'name':'final_cleanup','exit_code':124,'passed':False})
    results['passed']=all(item['passed'] for item in results['checks'])
    (reports/'windows-ci-report.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'passed':results['passed'],'checks':len(results['checks'])}))
    return 0 if results['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
