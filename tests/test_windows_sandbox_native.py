"""Opt-in real Windows LPAC/Job acceptance tests; never mock containment.

PowerShell (official embeddable Python is already unpacked by the CI build):
  $env:RUN_WINDOWS_SANDBOX_TESTS = '1'
  $env:ONTOLOGY_SKILL_PYTHON_ROOT = 'C:\\path\\to\\python-embed'
  python -m unittest discover -s tests -p test_windows_sandbox_native.py -v

Set ONTOLOGY_REQUIRE_STANDARD_USER=1 for the non-elevated acceptance lane.
An elevated CI runner is reported explicitly and does not establish the
standard-user deployment claim. No accounts, privileges, network exemptions,
external model requests, or administrator installation are created here.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, ExitStack, nullcontext
import ctypes
import json
import os
from pathlib import Path
import shutil
import socket
import tempfile
import unittest
from unittest.mock import patch
import uuid

from backend import windows_process as native
from backend import windows_skill_runner as runner
from backend.platform_io import ensure_private_directory


ENABLED = os.name == 'nt' and os.getenv('RUN_WINDOWS_SANDBOX_TESTS') == '1'
LIMITS = native.ProcessLimits(memory_bytes=192 * 1024 * 1024,
                              active_processes=1, cpu_time_seconds=5)


def _elevated():
    kernel, advapi, _, _ = native._api()
    token = native.HANDLE()
    native._check(advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x8, ctypes.byref(token)))
    try:
        value, size = native.DWORD(), native.DWORD()
        native._check(advapi.GetTokenInformation(token, 20, ctypes.byref(value),
                                               ctypes.sizeof(value), ctypes.byref(size)))
        return bool(value.value)
    finally:
        kernel.CloseHandle(token)


def _console_policy():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Console', 0, winreg.KEY_QUERY_VALUE) as key:
            value, kind = winreg.QueryValueEx(key, 'LowBoxConsoleEnabled')
        return {'present': True, 'enabled': kind == winreg.REG_DWORD and value == 1}
    except FileNotFoundError:
        return {'present': False}
    except OSError as exc:
        return {'query_error': exc.winerror}


@contextmanager
def _private_registry_probe():
    """Own one synthetic key; never change ACLs on an existing parent key."""
    import winreg
    from backend.platform_io import current_user_sid

    kernel, advapi, _, _ = native._api()
    advapi.GetSecurityDescriptorDacl.restype = native.BOOL
    advapi.GetSecurityDescriptorDacl.argtypes = [native.LPVOID, ctypes.POINTER(native.BOOL),
        ctypes.POINTER(native.LPVOID), ctypes.POINTER(native.BOOL)]
    advapi.SetSecurityInfo.restype = native.DWORD
    advapi.SetSecurityInfo.argtypes = [native.HANDLE, ctypes.c_int, native.DWORD,
        native.LPVOID, native.LPVOID, native.LPVOID, native.LPVOID]
    sid = current_user_sid()
    relative = 'Software\\OntologyOneSandboxProbe-' + uuid.uuid4().hex
    actual_path = sid + '\\' + relative
    created = False
    descriptor = native.LPVOID()
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, relative, 0,
                               winreg.KEY_ALL_ACCESS | winreg.KEY_WOW64_64KEY) as key:
            created = True
            native._check(advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                'D:P(A;;KA;;;' + sid + ')(A;;KA;;;SY)', 1, ctypes.byref(descriptor), None))
            present, defaulted, dacl = native.BOOL(), native.BOOL(), native.LPVOID()
            native._check(advapi.GetSecurityDescriptorDacl(descriptor, ctypes.byref(present),
                                                          ctypes.byref(dacl), ctypes.byref(defaulted)))
            if not present.value or not dacl:
                raise AssertionError('Synthetic registry key requires a non-null private DACL')
            # SE_REGISTRY_KEY, DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION.
            error = advapi.SetSecurityInfo(native.HANDLE(int(key)), 4, 0x80000004,
                                           None, None, dacl, None)
            if error:
                raise AssertionError('Synthetic registry key private DACL setup failed')
            winreg.SetValueEx(key, 'Sentinel', 0, winreg.REG_SZ, 'synthetic-host-private-value')
        # Positive control uses the same actual HKEY_USERS path as the child.
        # A virtualized/missing HKCU view cannot masquerade as denied access.
        with winreg.OpenKey(winreg.HKEY_USERS, actual_path, 0,
                            winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as key:
            if winreg.QueryValueEx(key, 'Sentinel') != ('synthetic-host-private-value', winreg.REG_SZ):
                raise AssertionError('Synthetic registry key positive control failed')
        yield actual_path
    finally:
        if descriptor:
            kernel.LocalFree(descriptor)
        if created:
            winreg.DeleteKeyEx(winreg.HKEY_CURRENT_USER, relative, winreg.KEY_WOW64_64KEY, 0)
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, relative, 0,
                                    winreg.KEY_QUERY_VALUE | winreg.KEY_WOW64_64KEY):
                    raise AssertionError('Synthetic registry probe key was not removed')
            except FileNotFoundError:
                pass


@unittest.skipUnless(ENABLED, 'real Windows sandbox acceptance is opt-in')
class WindowsSandboxNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # An explicitly enabled but unconfigured Windows run must fail, not
        # silently become a green collection of skipped acceptance checks.
        cls.runtime_files = runner._runtime_files()
        native._headless_error_mode(native._api()[0])
        elevated = _elevated()
        print(json.dumps({'windows_sandbox_acceptance': {
            'elevated': elevated, 'pointer_bits': ctypes.sizeof(ctypes.c_void_p) * 8,
            'standard_user_required': os.getenv('ONTOLOGY_REQUIRE_STANDARD_USER') == '1',
            'runtime_files': len(cls.runtime_files), 'network': 'loopback-only test listener',
            'lowbox_console_policy': _console_policy(),
        }}, ensure_ascii=True), flush=True)
        if os.getenv('ONTOLOGY_REQUIRE_STANDARD_USER') == '1' and elevated:
            raise AssertionError('The standard-user acceptance lane requires a non-elevated token')
        cls.system_root = os.environ.get('SystemRoot', r'C:\Windows')

    @contextmanager
    def sandbox(self, script, *, limits=None, diagnose=True, contained=True, audit=False, profile_readonly=True):
        temporary = tempfile.TemporaryDirectory(prefix='Ontology native 沙盒 ! ')
        root = ensure_private_directory(Path(temporary.name))
        runtime = root / 'runtime'
        work = root / 'work'
        runtime.mkdir()
        work.mkdir()
        for source in self.runtime_files:
            shutil.copyfile(source, runtime / source.name)
        script_path = work / 'case.py'
        script_path.write_text(script, encoding='utf-8')
        (work / 'own-sentinel.txt').write_text('synthetic-private-content', encoding='utf-8')
        profile = native.AppContainerProfile()
        if not profile_readonly:
            # Only the fixed trusted marker diagnostic selects this branch.
            # Production and every isolation acceptance case retain the
            # original read-only profile policy. No source package runs here.
            grant = profile.grant_read_tree
            def keep_os_profile_acl(path, **kwargs):
                if profile.folder is not None and Path(path) == profile.folder:
                    return
                return grant(path, **kwargs)
            profile.grant_read_tree = keep_os_profile_acl
        owner = None
        try:
            profile.__enter__()
            profile.grant_read_tree(root)
            profile.grant_read_tree(runtime, executable=True)
            environment = runner._sandbox_environment(runtime, work, self.system_root)
            original = native._verify_appcontainer_token
            def verify_and_audit(process, sid):
                original(process, sid)
                from tests.windows_loader_diagnostics import audit_loader_access
                audit_loader_access(process, runtime, work, self.system_root)
            with patch.object(native, '_verify_appcontainer_token', side_effect=verify_and_audit) if audit else nullcontext():
                owner = native.spawn_job_process(
                    [str(runtime / 'python.exe'), '-I', '-X', 'utf8', '-B', '-u', str(script_path)],
                    cwd=str(work), env=environment, limits=LIMITS if limits is None else limits, appcontainer=profile if contained else None)
            yield owner, root, runtime, work, profile
        except native.WindowsProcessError as exc:
            if diagnose:
                diagnostic = {'windows_sandbox_failure': exc.diagnostic()}
                if owner is not None:
                    try: diagnostic['native_process_counters'] = owner.diagnostic_counters()
                    except native.WindowsProcessError as failure: diagnostic['native_counters_error'] = failure.diagnostic()
                print(json.dumps(diagnostic, ensure_ascii=True), flush=True)
            raise
        finally:
            try:
                if owner is not None:
                    owner.close()
            finally:
                try:
                    profile.close()
                finally:
                    temporary.cleanup()

    def communicate_json(self, owner, payload, *, timeout=5):
        output, error = owner.communicate(json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                                           timeout=timeout, max_stdout=65536, max_stderr=16384)
        self.assertEqual(owner.returncode, 0, 'Contained Python did not exit successfully')
        self.assertEqual(error, b'', 'Contained Python wrote unexpected diagnostics')
        # Inspect while the Job handle is still live; process_ids() after close
        # would only prove the Python wrapper was closed, not that the Job emptied.
        self.assertEqual(owner.process_ids(), ())
        return json.loads(output)

    def test_normal_json_and_read_only_package_execution(self):
        script = 'import json,sys\nvalue=json.load(sys.stdin)\nprint(json.dumps({"value":value,"ok":True}))\n'
        with self.sandbox(script) as (owner, *_):
            self.assertEqual(self.communicate_json(owner, {'number': 7, 'text': '中文'}),
                             {'value': {'number': 7, 'text': '中文'}, 'ok': True})

    def test_00_trusted_copied_runtime_early_entry_control(self):
        # Diagnostic positive control only; never a production fallback. The
        # exact same copied interpreter/stdio/job run a fixed benign marker.
        with self.sandbox('import os\nos.write(1,b"ONTOLOGY_NATIVE_READY\\n")\n', contained=False) as (owner, *_):
            output, error = owner.communicate(b'', timeout=5, max_stdout=128, max_stderr=1024)
            print(json.dumps({'native_runtime_control': owner.diagnostic_counters()}), flush=True)
            self.assertEqual(owner.returncode, 0); self.assertEqual(output, b'ONTOLOGY_NATIVE_READY\n'); self.assertEqual(error, b'')

    def test_01_lpac_runtime_early_entry_marker(self):
        with self.sandbox('import os\nos.write(1,b"ONTOLOGY_NATIVE_READY\\n")\n', audit=True) as (owner, *_):
            output, error = owner.communicate(b'', timeout=5, max_stdout=128, max_stderr=1024)
            print(json.dumps({'native_lpac_marker': owner.diagnostic_counters()}), flush=True)
            self.assertEqual(owner.returncode, 0); self.assertEqual(output, b'ONTOLOGY_NATIVE_READY\n'); self.assertEqual(error, b'')

    def test_02_trusted_marker_profile_initialization_diagnostic(self):
        # A/B diagnostic, not a security acceptance or fallback. All strong
        # LPAC/private-file/network/resource tests remain mandatory and retain
        # read-only profiles. Only an OS-created, unique disposable profile's
        # default ACL differs for this fixed no-input/no-file-write marker.
        try:
            with self.sandbox('import os\nos.write(1,b"ONTOLOGY_NATIVE_READY\\n")\n',
                              profile_readonly=False) as (owner, *_):
                output, _ = owner.communicate(b'', timeout=5, max_stdout=128, max_stderr=1024)
                print(json.dumps({'native_default_profile_diagnostic': {'early_marker': output == b'ONTOLOGY_NATIVE_READY\n',
                    **owner.diagnostic_counters()}}), flush=True)
        except native.WindowsProcessError as failure:
            print(json.dumps({'native_default_profile_diagnostic': failure.diagnostic()}), flush=True)

    def test_03_previous_console_mode_fixed_marker_diagnostic(self):
        # The previous NO_WINDOW mode can succeed where a host already enables
        # lowbox consoles. This records the A/B result without requiring failure
        # and never changes registry values or a production security property.
        try:
            with patch.object(native, '_APPCONTAINER_PROCESS_MODE', 0x08000000):
                with self.sandbox('import os\nos.write(1,b"ONTOLOGY_NATIVE_READY\\n")\n') as (owner, *_):
                    output, _ = owner.communicate(b'', timeout=5, max_stdout=128, max_stderr=1024)
                    print(json.dumps({'native_previous_console_mode': {'early_marker': output == b'ONTOLOGY_NATIVE_READY\n',
                        **owner.diagnostic_counters()}}), flush=True)
        except native.WindowsProcessError as failure:
            print(json.dumps({'native_previous_console_mode': failure.diagnostic()}), flush=True)

    def test_lpac_proof_rejects_an_ordinary_appcontainer_before_resume(self):
        # Negative control only: create an actual ordinary AC, while retaining
        # the production verifier. Its suspended child must never be resumed.
        # No verification API/result is mocked and the script is a benign noop.
        with patch.object(native, '_ALL_APPLICATION_PACKAGES_OPT_OUT', 0):
            with self.assertRaises(native.WindowsProcessError) as caught:
                with self.sandbox('pass\n', diagnose=False):
                    self.fail('An ordinary AppContainer was accepted as LPAC')
        self.assertEqual(caught.exception.code, 'APPCONTAINER_LPAC_REQUIRED')
        self.assertEqual(caught.exception.operation, 'AccessCheck')
        self.assertEqual(caught.exception.access_mask, 3)

    def test_lpac_proof_rejects_the_standard_host_token(self):
        kernel, advapi, _, _ = native._api()
        token = native.HANDLE()
        native._check(advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x8 | 0x2, ctypes.byref(token)))
        try:
            value, size = native.DWORD(), native.DWORD()
            native._check(advapi.GetTokenInformation(token, 29, ctypes.byref(value), ctypes.sizeof(value), ctypes.byref(size)))
            self.assertEqual(value.value, 0)
            with self.assertRaises(native.WindowsProcessError) as caught:
                native._verify_lpac_access(token)
            self.assertEqual(caught.exception.code, 'APPCONTAINER_LPAC_REQUIRED')
            self.assertEqual(caught.exception.access_mask, 3)
        finally:
            kernel.CloseHandle(token)

    def test_unicode_json_pipes_use_utf8_despite_isolated_mode_and_windows_acp(self):
        script = ('import json,sys\nvalue=json.load(sys.stdin)\n'
                  'json.dump({"value":value,"stdin":sys.stdin.encoding,"stdout":sys.stdout.encoding},'
                  'sys.stdout,ensure_ascii=False)\n')
        value = {'organization': '集团总部·科技与数字化部', 'note': '中文标点：“甲、乙”；金额¥123，📚'}
        with self.sandbox(script) as (owner, *_):
            self.assertEqual(self.communicate_json(owner, value),
                             {'value': value, 'stdin': 'utf-8', 'stdout': 'utf-8'})

    def test_private_host_read_and_all_ungranted_writes_are_denied(self):
        script = '''import json,pathlib,sys
args=json.load(sys.stdin)
try:
    pathlib.Path(args['host_file']).read_bytes()
    host_read=False
except OSError:
    host_read=True
writes=[]
for path in args['write_files']:
    try:
        with open(path,'xb') as stream: stream.write(b'synthetic-test-only')
        writes.append(False)
    except OSError:
        writes.append(True)
print(json.dumps({'host_read_denied':host_read,'writes_denied':writes}))
'''
        with tempfile.TemporaryDirectory(prefix='Ontology host private ') as host:
            private = ensure_private_directory(Path(host))
            sentinel = private / 'sentinel.txt'
            sentinel.write_text('synthetic-host-private-sentinel', encoding='utf-8')
            with self.sandbox(script) as (owner, _, runtime, work, profile):
                unique = 'ontology-denied-' + uuid.uuid4().hex + '.tmp'
                directories = [runtime, work, profile.folder,
                               Path(self.system_root) / 'Temp', Path(tempfile.gettempdir())]
                self.assertTrue(all(path.is_dir() for path in directories),
                                'Write-denial probes require real existing directories')
                paths = [path / unique for path in directories]
                try:
                    result = self.communicate_json(owner, {
                        'host_file': str(sentinel), 'write_files': [str(path) for path in paths]})
                    self.assertEqual(result, {'host_read_denied': True, 'writes_denied': [True] * 5})
                    self.assertTrue(all(not path.exists() for path in paths))
                finally:
                    # Only uniquely named synthetic probe files are touched.
                    for path in paths:
                        path.unlink(missing_ok=True)

    def test_private_host_registry_actual_user_hive_read_and_write_are_denied(self):
        script = '''import json,sys,winreg
args=json.load(sys.stdin)
denied={}
for mode,access in [('read',winreg.KEY_QUERY_VALUE),('write',winreg.KEY_SET_VALUE)]:
    try:
        with winreg.OpenKey(winreg.HKEY_USERS,args['host_path'],0,access|winreg.KEY_WOW64_64KEY) as key:
            if mode=='read':
                winreg.QueryValueEx(key,'Sentinel')
            else:
                winreg.SetValueEx(key,'Sentinel',0,winreg.REG_SZ,'synthetic-child-write-probe')
        denied[mode]=False
    except OSError as exc:
        denied[mode]=exc.winerror==5
print(json.dumps(denied))
'''
        import winreg
        with _private_registry_probe() as actual_path:
            with self.sandbox(script) as (owner, *_):
                result = self.communicate_json(owner, {'host_path': actual_path})
            self.assertEqual(result, {'read': True, 'write': True})
            with winreg.OpenKey(winreg.HKEY_USERS, actual_path, 0,
                                winreg.KEY_QUERY_VALUE | winreg.KEY_WOW64_64KEY) as key:
                self.assertEqual(winreg.QueryValueEx(key, 'Sentinel'),
                                 ('synthetic-host-private-value', winreg.REG_SZ))

    def test_fixed_system_side_by_side_registry_read_without_write_access(self):
        # Probe only the OS path implicated by the loader trace. Requesting
        # KEY_SET_VALUE establishes whether write access is granted; no system
        # registry value is ever created, edited or deleted by this test.
        script = '''import json,winreg
path=r'SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\SideBySide'
try:
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,path,0,winreg.KEY_QUERY_VALUE|winreg.KEY_WOW64_64KEY):
        read_allowed=True
except OSError:
    read_allowed=False
try:
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,path,0,winreg.KEY_SET_VALUE|winreg.KEY_WOW64_64KEY):
        write_denied=False
except OSError as exc:
    write_denied=exc.winerror==5
print(json.dumps({'read_allowed':read_allowed,'write_denied':write_denied}))
'''
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r'SOFTWARE\Microsoft\Windows\CurrentVersion\SideBySide', 0,
                            winreg.KEY_QUERY_VALUE | winreg.KEY_WOW64_64KEY):
            pass  # Positive control: the same real, 64-bit OS key exists.
        with self.sandbox(script) as (owner, *_):
            self.assertEqual(self.communicate_json(owner, {}),
                             {'read_allowed': True, 'write_denied': True})

    def test_loopback_connection_has_no_network_capability(self):
        script = '''import json,sys
port=json.load(sys.stdin)['port']
sock=None;stage='import';denied=False;error=0;kind=None
try:
    import socket
    stage='create';sock=socket.socket()
    stage='configure';sock.settimeout(1)
    stage='connect';sock.connect(('127.0.0.1',port))
except OSError as exc:
    error=getattr(exc,'winerror',0) or 0;kind='OSError'
    denied=error in (5,10013)
except ImportError:
    kind='ImportError'
finally:
    if sock is not None: sock.close()
print(json.dumps({'denied':denied,'stage':stage,'winerror':error,'error_kind':kind}))
'''
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen(2)
            listener.settimeout(.1)
            port = listener.getsockname()[1]
            with socket.create_connection(('127.0.0.1', port), timeout=1):
                accepted, _ = listener.accept(); accepted.close()
            with self.sandbox(script) as (owner, *_):
                result = self.communicate_json(owner, {'port': port})
                self.assertIn(result['stage'], ('create', 'connect'), result)
                self.assertEqual(result['error_kind'], 'OSError', result)
                self.assertIn(result['winerror'], (5, 10013), result)
                self.assertTrue(result['denied'], result)
            with self.assertRaises(socket.timeout):
                accepted, _ = listener.accept()
                accepted.close()

    def test_job_commit_limit_rejects_384_mib_allocation(self):
        script = '''import json
try:
    allocated=bytearray(384*1024*1024)
    denied=False
except MemoryError:
    denied=True
print(json.dumps({'denied':denied}))
'''
        with self.sandbox(script) as (owner, *_):
            self.assertEqual(self.communicate_json(owner, {}), {'denied': True})

    def test_active_process_limit_rejects_another_python(self):
        script = '''import json,subprocess,sys
try:
    child=subprocess.Popen([sys.executable,'-I','-B','-c','pass'],creationflags=subprocess.DETACHED_PROCESS)
    child.wait(timeout=1)
    denied=False
except OSError:
    denied=True
print(json.dumps({'denied':denied}))
'''
        with self.sandbox(script) as (owner, *_):
            self.assertEqual(self.communicate_json(owner, {}), {'denied': True})

    def test_wall_timeout_terminates_the_entire_owned_job(self):
        with self.sandbox('import time\ntime.sleep(30)\n') as (owner, *_):
            self.assertIn(owner.pid, owner.process_ids())
            with self.assertRaises(native.WindowsProcessError) as caught:
                owner.communicate(b'{}', timeout=.5)
            self.assertEqual(caught.exception.code, 'TIMEOUT')
            self.assertEqual(owner.process_ids(), ())

    def test_cpu_user_time_limit_terminates_before_wall_timeout(self):
        limits = native.ProcessLimits(memory_bytes=192 * 1024 * 1024,
                                      active_processes=1, cpu_time_seconds=.1)
        with self.sandbox('while True:\n    pass\n', limits=limits) as (owner, *_):
            # A TIMEOUT exception is a failed test: only the kernel's CPU limit
            # may stop this case, not the parent's five-second wall watchdog.
            owner.communicate(b'{}', timeout=5, max_stdout=1024, max_stderr=1024)
            self.assertIsNotNone(owner.returncode)
            self.assertNotEqual(owner.returncode, 0)
            self.assertEqual(owner.process_ids(), ())

    def test_output_limit_terminates_the_entire_owned_job(self):
        script = "import sys,time\nsys.stdout.write('x'*131072);sys.stdout.flush();time.sleep(30)\n"
        with self.sandbox(script) as (owner, *_):
            with self.assertRaises(native.WindowsProcessError) as caught:
                owner.communicate(b'{}', timeout=5, max_stdout=1024)
            self.assertEqual(caught.exception.code, 'OUTPUT_LIMIT')
            self.assertEqual(owner.process_ids(), ())

    def test_concurrent_profiles_cannot_read_each_others_work(self):
        script = '''import json,pathlib,sys,time
args=json.load(sys.stdin)
own=pathlib.Path(args['own']).read_text()=='synthetic-private-content'
try:
    pathlib.Path(args['other']).read_bytes();denied=False
except OSError:
    denied=True
time.sleep(.5)
print(json.dumps({'own_read':own,'other_denied':denied}))
'''
        with ExitStack() as stack:
            left = stack.enter_context(self.sandbox(script))
            right = stack.enter_context(self.sandbox(script))
            self.assertNotEqual(left[4].sid_string, right[4].sid_string)
            self.assertIn(left[0].pid, left[0].process_ids())
            self.assertIn(right[0].pid, right[0].process_ids())
            first, second = left[3] / 'own-sentinel.txt', right[3] / 'own-sentinel.txt'
            self.assertTrue(first.is_file() and second.is_file())
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(self.communicate_json, left[0], {'own': str(first), 'other': str(second)}),
                           pool.submit(self.communicate_json, right[0], {'own': str(second), 'other': str(first)})]
                for future in futures:
                    self.assertEqual(future.result(timeout=10), {'own_read': True, 'other_denied': True})

    def test_unrelated_inheritable_file_handle_is_not_leaked(self):
        import msvcrt
        import _winapi
        script = '''import json,sys
args=json.load(sys.stdin)
print(json.dumps({'marker':args['marker']}))
'''
        kernel = native._api()[0]
        kernel.GetProcessId.restype, kernel.GetProcessId.argtypes = native.DWORD, [native.HANDLE]
        kernel.GetFileInformationByHandle.restype = native.BOOL
        kernel.GetFileInformationByHandle.argtypes = [native.HANDLE, native.LPVOID]
        current_process = kernel.GetCurrentProcess()
        with tempfile.TemporaryDirectory(prefix='Ontology inherited handle ') as host:
            private = ensure_private_directory(Path(host))
            sentinel = private / 'handle-sentinel.txt'
            synthetic = 'synthetic-handle-' + uuid.uuid4().hex
            sentinel.write_text(synthetic, encoding='ascii')
            with sentinel.open('rb', buffering=0) as unrelated:
                handle = msvcrt.get_osfhandle(unrelated.fileno())
                source_info = (native.DWORD * 13)()
                native._check(kernel.GetFileInformationByHandle(native.HANDLE(handle), source_info))
                source_identity = tuple(int(source_info[index]) for index in (7, 11, 12))

                def inspect_from(process):
                    # DuplicateHandle queries the exact source process from the
                    # trusted parent. The LPAC never touches an unknown HANDLE:
                    # its strict invalid-handle policy can terminate the target.
                    copied = native.HANDLE()
                    result = {'duplicate_error': 0, 'file_type': None, 'type_error': None,
                              'source_identity': False, 'sentinel_match': False,
                              'read_attempted': False, 'directory': False}
                    if not kernel.DuplicateHandle(process, native.HANDLE(handle), current_process,
                                                   ctypes.byref(copied), 0, False, 2):
                        result['duplicate_error'] = ctypes.get_last_error()
                        return result
                    try:
                        try:
                            result['file_type'] = int(_winapi.GetFileType(copied.value))
                        except OSError as failure:
                            # A valid copied event/process/etc. is not a file.
                            # Only the exact invalid-file-type error is allowed.
                            self.assertEqual(failure.winerror, 6)
                            result['type_error'] = 6
                            return result
                        if result['file_type'] == 1:
                            info = (native.DWORD * 13)()
                            native._check(kernel.GetFileInformationByHandle(copied, info))
                            result['source_identity'] = tuple(int(info[index]) for index in (7, 11, 12)) == source_identity
                            result['directory'] = bool(info[0] & 0x10)
                            if not result['directory']:
                                result['read_attempted'] = True
                                data, error = _winapi.ReadFile(copied.value, 128)
                                self.assertEqual(error, 0)
                                result['sentinel_match'] = data == synthetic.encode('ascii')
                        # Never read pipe/character/directory handles. A reused
                        # number is acceptable only when it is a different object.
                        return result
                    finally:
                        native._check(kernel.CloseHandle(copied))  # Only our successfully owned copy.

                # Positive control proves the same DuplicateHandle path detects
                # the source file by both identity and independent sentinel.
                positive = inspect_from(current_process)
                self.assertEqual(positive['duplicate_error'], 0, positive)
                self.assertTrue(positive['source_identity'], positive)
                self.assertTrue(positive['sentinel_match'], positive)
                unrelated.seek(0)
                self.assertEqual(unrelated.tell(), 0)
                observed = []
                original_verifier = native._verify_appcontainer_token
                def verify_and_inspect(process, sid):
                    original_verifier(process, sid)  # Real proof, after Job assignment and before ResumeThread.
                    child_pid = int(native._check(kernel.GetProcessId(process)))
                    result = inspect_from(process)
                    result['child_pid'] = child_pid
                    observed.append(result)
                    if result['duplicate_error']:
                        self.assertEqual(result['duplicate_error'], 6, result)
                    else:
                        self.assertFalse(result['source_identity'], result)
                        self.assertFalse(result['sentinel_match'], result)
                os.set_handle_inheritable(handle, True)
                try:
                    with patch.object(native, '_verify_appcontainer_token', side_effect=verify_and_inspect):
                        with self.sandbox(script) as (owner, *_):
                            self.assertEqual(len(observed), 1)
                            self.assertEqual(observed[0]['child_pid'], owner.pid)
                            marker = uuid.uuid4().hex
                            self.assertEqual(self.communicate_json(owner, {'marker': marker}), {'marker': marker})
                            self.assertEqual(unrelated.tell(), 0, 'Unrelated source handle was inherited and read')
                finally:
                    os.set_handle_inheritable(handle, False)


    def test_product_runner_status_and_two_unicode_skill_cases_end_to_end(self):
        files = {
            'SKILL.md': ('---\nname: native-product-probe\n'
                         'description: Verify isolated synthetic JSON skill execution.\n'
                         '---\nEcho synthetic input as a UTF-8 JSON result.\n'),
            'scripts/main.py': (
                'import json,sys\n'
                'value=json.load(sys.stdin)\n'
                'json.dump({"value":value,"stdin_encoding":sys.stdin.encoding,'
                '"stdout_encoding":sys.stdout.encoding},sys.stdout,ensure_ascii=False)\n'),
        }
        values = [
            {'机构': '集团总部·科技与数字化部', '说明': '中文标点：“甲、乙”；金额¥123，📚'},
            {'机构': '中信银行', '技能': ['数据发现', '语义查询'], '结果': {'通过': True, '数量': 2}},
        ]
        cases = [{'name': 'unicode-' + str(index), 'input': value,
                  'expected_output': {'value': value, 'stdin_encoding': 'utf-8', 'stdout_encoding': 'utf-8'}}
                 for index, value in enumerate(values, 1)]
        previous = os.environ.get('ONTOLOGY_SKILL_TEMP_DIR')
        with tempfile.TemporaryDirectory(prefix='Ontology product 技能 ') as temporary:
            os.environ['ONTOLOGY_SKILL_TEMP_DIR'] = temporary
            try:
                # Exercise the product's real first-run isolation probe and
                # orchestration without changing its cache or replacing APIs.
                status = runner.runner_status()
                self.assertTrue(status['available'], 'Real Windows runner availability probe failed')
                self.assertEqual(list(Path(temporary).glob('request-*')), [],
                                 'Availability probe left a request directory')
                result = runner.execute_tests(files, 'scripts/main.py', cases)
                self.assertEqual(result['outcome'], 'passed')
                self.assertEqual(result['mode'], 'isolated_python')
                self.assertEqual(result['report']['passed'], 2)
                self.assertEqual(result['report']['failed'], 0)
                self.assertEqual([(case['status'], case['code']) for case in result['report']['cases']],
                                 [('passed', 'PASSED'), ('passed', 'PASSED')])
                self.assertEqual(list(Path(temporary).glob('request-*')), [],
                                 'Product execution left a request directory')
                # execute_tests reports cleanup failures as unavailable, so
                # PASSED also requires its own per-case profile cleanup.
            finally:
                if previous is None:
                    os.environ.pop('ONTOLOGY_SKILL_TEMP_DIR', None)
                else:
                    os.environ['ONTOLOGY_SKILL_TEMP_DIR'] = previous


    def test_product_readonly_execution_returns_json_after_cleanup(self):
        from backend.skill_execution import execute_readonly_skill
        files = {'SKILL.md': '---\nname: readonly-output\ndescription: Bounded JSON output.\n---\n只读换算。\n',
                 'scripts/main.py': 'import json,sys\nv=json.load(sys.stdin)\njson.dump({"机构":v["机构"],"结果":v["金额"]*100},sys.stdout,ensure_ascii=False)\n'}
        with tempfile.TemporaryDirectory(prefix='Ontology readonly 技能 ') as root:
            with patch.dict(os.environ, {'ONTOLOGY_SKILL_TEMP_DIR': root}):
                result = execute_readonly_skill(files, 'scripts/main.py', {'机构': '中信银行', '金额': 7})
            self.assertEqual(result, {'outcome': 'passed', 'output': {'机构': '中信银行', '结果': 700}, 'code': 'PASSED'})
            self.assertEqual(list(Path(root).glob('request-*')), [])


if __name__ == '__main__':
    unittest.main()
