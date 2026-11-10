"""CI-only fixed loader probe for the administrator's separately scoped trace.

The standard user runs only the fixed benign marker below. Receipts contain
owned child PIDs, timestamps, fixed stage/error codes and numeric counters.
They never contain command lines, environments, file contents or credentials.
The raw administrator trace is never accessible to or consumed by this probe.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import uuid

from backend import windows_process as native
from backend.platform_io import ensure_private_directory
from tests.test_windows_sandbox_native import WindowsSandboxNativeTests, _elevated


FIXTURE = 'import os\nos.write(1,b"ONTOLOGY_NATIVE_READY\\n")\n'


def _utc():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def _write_receipt(path, receipt):
    # This non-sensitive cross-account CI receipt intentionally retains the
    # reports directory's inherited QA-user/administrator ACL. Production secret
    # and storage files continue to use platform_io's protected private ACL.
    path = native._plain_path(path)
    if not path.parent.is_dir() or path.suffix != '.json':
        raise ValueError('Invalid receipt destination')
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8', newline='\n') as stream:
            json.dump(receipt, stream, ensure_ascii=True, separators=(',', ':'))
            stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def _probe_environment(receipt_path):
    """Discard the credential-switch caller's profile, temp and credential env."""
    package = Path(__file__).resolve().parents[1]
    if Path(sys.executable).resolve() != package / 'runtime' / 'python' / 'python.exe':
        raise ValueError('The delivered interpreter is required')
    # Query the OS, rather than trusting SystemRoot inherited from another user.
    kernel = native._api()[0]
    get_windows = kernel.GetWindowsDirectoryW
    get_windows.argtypes = [ctypes.c_wchar_p, native.DWORD]
    get_windows.restype = native.DWORD
    buffer = ctypes.create_unicode_buffer(32768)
    count = get_windows(buffer, len(buffer))
    if not count or count >= len(buffer):
        raise ValueError('The Windows directory is unavailable')
    system_root = Path(buffer.value)
    previous_environment, previous_tempdir = dict(os.environ), tempfile.tempdir
    with tempfile.TemporaryDirectory(prefix='lpac-fixed-probe-', dir=receipt_path.parent) as temporary:
        private = ensure_private_directory(Path(temporary))
        profile, scratch = private / 'profile', private / 'temp'
        for directory in (profile, scratch, profile / 'AppData' / 'Local', profile / 'AppData' / 'Roaming'):
            directory.mkdir(parents=True, exist_ok=True)
        environment = {
            'SystemRoot': str(system_root), 'WINDIR': str(system_root),
            'COMSPEC': str(system_root / 'System32' / 'cmd.exe'), 'PATHEXT': '.COM;.EXE;.BAT;.CMD',
            'PATH': str(system_root / 'System32') + ';' + str(system_root),
            'USERPROFILE': str(profile), 'HOME': str(profile),
            'LOCALAPPDATA': str(profile / 'AppData' / 'Local'), 'APPDATA': str(profile / 'AppData' / 'Roaming'),
            'TEMP': str(scratch), 'TMP': str(scratch),
            'ONTOLOGY_SKILL_PYTHON_ROOT': str(package / 'runtime' / 'skill-python'),
            'ONTOLOGY_REQUIRE_STANDARD_USER': '1', 'RUN_WINDOWS_SANDBOX_TESTS': '1',
            'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1',
        }
        os.environ.clear(); os.environ.update(environment); tempfile.tempdir = None
        try:
            yield
        finally:
            os.environ.clear(); os.environ.update(previous_environment); tempfile.tempdir = previous_tempdir


def main():
    parser = argparse.ArgumentParser(description='Run only the fixed Windows LPAC loader diagnostic.')
    parser.add_argument('--receipt', required=True, type=Path)
    arguments = parser.parse_args()
    if os.name != 'nt' or _elevated():
        raise SystemExit('Loader probe requires a standard Windows user')
    receipt = {'schema_version': 1, 'purpose': 'fixed_lpac_loader_diagnostic', 'child_pids': [],
               'started_at_utc': _utc(), 'finished_at_utc': None,
               'fixture_sha256': hashlib.sha256(FIXTURE.encode()).hexdigest(),
               'stage': 'preparing', 'marker_received': False, 'cleanup_complete': False}
    arguments.receipt = native._plain_path(arguments.receipt)
    _write_receipt(arguments.receipt, receipt)
    try:
        with _probe_environment(arguments.receipt):
            WindowsSandboxNativeTests.setUpClass()
            probe = WindowsSandboxNativeTests(methodName='test_01_lpac_runtime_early_entry_marker')
            with probe.sandbox(FIXTURE, audit=True) as (process, *_):
                receipt.update(child_pids=[process.pid], stage='spawned', token_verified_before_resume=True)
                _write_receipt(arguments.receipt, receipt)
                try:
                    output, _ = process.communicate(b'', timeout=5, max_stdout=128, max_stderr=1024)
                    receipt.update(stage='exited', marker_received=output == b'ONTOLOGY_NATIVE_READY\n')
                except native.WindowsProcessError as failure:
                    receipt.update(stage='execution_failed', error=failure.diagnostic())
                finally:
                    receipt['counters'] = process.diagnostic_counters()
                    receipt['active_owned_pids_after_communication'] = list(process.process_ids())
        receipt['cleanup_complete'] = True
    except native.WindowsProcessError as failure:
        receipt.update(stage='native_failure', error=failure.diagnostic())
    except BaseException as failure:
        # Exception messages can contain local paths; retain only a fixed class
        # identifier selected from this bounded list, never str(failure).
        kind = type(failure).__name__
        receipt.update(stage='probe_failed', error_class=kind if kind in
            {'OSError', 'PermissionError', 'FileNotFoundError', 'ValueError', 'TimeoutError', 'AssertionError'} else 'ProbeError')
    finally:
        receipt['finished_at_utc'] = _utc()
        _write_receipt(arguments.receipt, receipt)
    print(json.dumps({'loader_probe_recorded': True, 'marker_received': receipt['marker_received'],
                      'cleanup_complete': receipt['cleanup_complete']}), flush=True)
    # A receipt records evidence, not successful security acceptance. The
    # unchanged native isolation suite separately gates the release.
    return 0 if receipt['cleanup_complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
