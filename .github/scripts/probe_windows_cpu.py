"""Temporary standard-user CPU diagnostic using verified QA19 runtime bytes.

The kernel budget remains 0.1 user-CPU seconds and the wall watchdog remains
five seconds. This lane never starts MySQL, contacts a model, or loads keys.
"""
import argparse
from contextlib import redirect_stdout
import ctypes
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import time

BASELINE = '8aba44f4b1ccdc33be4fa2a821856047ccefb2ab'
CODES = {'TIMEOUT', 'PROCESS_IO_FAILED', 'OUTPUT_LIMIT', 'PROCESS_CLEANUP_FAILED',
    'PROCESS_QUERY_FAILED', 'PROCESS_UNAVAILABLE', 'APPCONTAINER_UNAVAILABLE',
    'APPCONTAINER_CLEANUP_FAILED', 'PROCESS_CLOSED', 'OTHER_ERROR'}
NUMBERS = {'trial', 'setup_ms', 'wall_ms', 'total_ms', 'exit_code', 'user_cpu_ms',
    'kernel_cpu_ms', 'stdin_bytes', 'stdout_bytes', 'stderr_bytes',
    'process_read_bytes', 'process_write_bytes'}


def safe_summary(value):
    rows = []
    for original in value.get('trials', [])[:10]:
        if not isinstance(original, dict):
            continue
        row = {name: item for name, item in original.items() if name in NUMBERS
               and type(item) is int and 0 <= item < 2**64}
        if original.get('code') in CODES:
            row['code'] = original['code']
        for name in ('job_empty', 'nonzero_exit'):
            if type(original.get(name)) is bool:
                row[name] = original[name]
        rows.append(row)
    return {'schema_version': 1, 'source_commit': BASELINE, 'standard_user': True,
        'provider_credentials_used': False, 'cpu_limit_seconds': 0.1,
        'wall_watchdog_seconds': 5, 'trial_count': len(rows), 'trials': rows}


def run(package, report, expected):
    if os.name != 'nt' or ctypes.windll.shell32.IsUserAnAdmin():
        raise ValueError('STANDARD_USER_REQUIRED')
    if 'DEEPSEEK_API_KEY' in os.environ:
        raise ValueError('PROVIDER_KEY_FORBIDDEN')
    python = package / 'runtime/python/python.exe'
    if Path(sys.executable).resolve() != python:
        raise ValueError('DELIVERED_PYTHON_REQUIRED')
    manifest = json.loads((package / 'runtime/manifest.json').read_text(encoding='utf-8'))
    if manifest['application_version'] != '0.5.0' or manifest['source_commit'] != BASELINE:
        raise ValueError('PINNED_BASELINE_REQUIRED')
    from backend import windows_process as native
    actual = Path(native.__file__).resolve()
    if not actual.is_relative_to(package / 'backend') or hashlib.sha256(actual.read_bytes()).hexdigest() != expected:
        raise ValueError('PROCESS_SOURCE_MISMATCH')
    # Use only disposable, standard-user-owned profile and temporary paths.
    root = report.parent
    profile, temporary = root / 'profile', root / 'temp'
    for directory in (temporary, profile / 'AppData/Local', profile / 'AppData/Roaming'):
        directory.mkdir(parents=True, exist_ok=True)
    keep = {'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT'}
    env = {key: item for key, item in os.environ.items() if key.upper() in keep}
    system = Path(os.environ.get('SystemRoot', r'C:\Windows'))
    env.update(PATH=str(system / 'System32'), USERPROFILE=str(profile), TEMP=str(temporary),
        TMP=str(temporary), LOCALAPPDATA=str(profile / 'AppData/Local'),
        APPDATA=str(profile / 'AppData/Roaming'), RUN_WINDOWS_SANDBOX_TESTS='1',
        ONTOLOGY_REQUIRE_STANDARD_USER='1', ONTOLOGY_SKILL_PYTHON_ROOT=str(package / 'runtime/skill-python'))
    os.environ.clear(); os.environ.update(env)
    from tests.test_windows_sandbox_native import WindowsSandboxNativeTests
    with redirect_stdout(io.StringIO()):
        WindowsSandboxNativeTests.setUpClass()
    fixture = WindowsSandboxNativeTests()
    limits = native.ProcessLimits(memory_bytes=192 * 1024 * 1024, active_processes=1, cpu_time_seconds=0.1)
    rows = []
    for trial in range(1, 11):
        started = time.monotonic()
        row = {'trial': trial}
        try:
            with fixture.sandbox('while True:\n    pass\n', limits=limits, diagnose=False) as (owner, *_):
                communicating = time.monotonic()
                row['setup_ms'] = round((communicating - started) * 1000)
                try:
                    owner.communicate(b'{}', timeout=5, max_stdout=1024, max_stderr=1024)
                except native.WindowsProcessError as error:
                    row['code'] = error.code if error.code in CODES else 'OTHER_ERROR'
                row['wall_ms'] = round((time.monotonic() - communicating) * 1000)
                row.update(owner.diagnostic_counters())
                row['nonzero_exit'] = owner.returncode is not None and owner.returncode != 0
                row['job_empty'] = owner.process_ids() == ()
        except Exception:
            row.setdefault('code', 'OTHER_ERROR')
        row['total_ms'] = round((time.monotonic() - started) * 1000)
        rows.append(row)
        report.write_text(json.dumps(safe_summary({'trials': rows})), encoding='utf-8')
    return all('code' not in row and row.get('nonzero_exit') and row.get('job_empty') for row in rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--process-sha256')
    parser.add_argument('--summarize', action='store_true')
    args = parser.parse_args()
    try:
        if args.summarize:
            with args.report.open('rb') as stream:
                raw = stream.read(128 * 1024 + 1)
            if len(raw) > 128 * 1024:
                raise ValueError('REPORT_LIMIT')
            print(json.dumps(safe_summary(json.loads(raw))))
            return 0
        return 0 if run(args.package.resolve(), args.report.resolve(), args.process_sha256) else 1
    except Exception:
        print('{"status":"CPU_PROBE_UNAVAILABLE","provider_credentials_used":false}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
