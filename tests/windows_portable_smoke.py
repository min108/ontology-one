"""Real native-Windows portable-package acceptance on a disposable package copy.

Run as a standard Windows user with PATH containing only Windows directories.
Never run against an existing data directory. Reports contain fixed status and
policy metadata only; no passwords, control tokens, HTTP bodies or tenant data.
"""
from __future__ import annotations

import argparse
from contextlib import suppress
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import uuid


class CheckFailure(Exception):
    pass


def require(condition, code):
    if not condition:
        raise CheckFailure(code)


def port_free():
    with socket.socket() as channel:
        channel.bind(('127.0.0.1', 0))
        return channel.getsockname()[1]


def can_bind(port):
    with socket.socket() as channel:
        channel.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            channel.bind(('127.0.0.1', port))
            return True
        except OSError:
            return False


def process_modules(pid):
    """Observe actual DLL resolution, rather than assuming installed VC is unused."""
    kernel, psapi = ctypes.WinDLL('kernel32', use_last_error=True), ctypes.WinDLL('psapi', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    psapi.EnumProcessModulesEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.HMODULE), wintypes.DWORD,
                                         ctypes.POINTER(wintypes.DWORD), wintypes.DWORD]
    psapi.EnumProcessModulesEx.restype = wintypes.BOOL
    psapi.GetModuleFileNameExW.argtypes = [wintypes.HANDLE, wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
    psapi.GetModuleFileNameExW.restype = wintypes.DWORD
    process = kernel.OpenProcess(0x400 | 0x10, False, pid)
    require(bool(process), 'MODULE_PROCESS_ACCESS')
    try:
        modules, needed = (wintypes.HMODULE * 4096)(), wintypes.DWORD()
        require(psapi.EnumProcessModulesEx(process, modules, ctypes.sizeof(modules), ctypes.byref(needed), 3), 'MODULE_ENUMERATION')
        require(needed.value <= ctypes.sizeof(modules), 'MODULE_ENUMERATION_LIMIT')
        result = []
        for module in modules[:needed.value // ctypes.sizeof(wintypes.HMODULE)]:
            buffer = ctypes.create_unicode_buffer(32768)
            if psapi.GetModuleFileNameExW(process, module, buffer, len(buffer)):
                result.append(Path(buffer.value))
        return result
    finally:
        kernel.CloseHandle(process)


def owned_process_ids(package):
    """Read-only process enumeration; never terminate by a PID or image name."""
    class Entry(ctypes.Structure):
        _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD), ('th32ProcessID', wintypes.DWORD),
                    ('th32DefaultHeapID', ctypes.c_size_t), ('th32ModuleID', wintypes.DWORD),
                    ('cntThreads', wintypes.DWORD), ('th32ParentProcessID', wintypes.DWORD),
                    ('pcPriClassBase', wintypes.LONG), ('dwFlags', wintypes.DWORD), ('szExeFile', wintypes.WCHAR * 260)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    require(snapshot not in (None, ctypes.c_void_p(-1).value), 'PROCESS_ENUMERATION')
    result, entry = set(), Entry()
    entry.dwSize = ctypes.sizeof(entry)
    try:
        found = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while found:
            process = kernel.OpenProcess(0x1000, False, entry.th32ProcessID)
            if process:
                try:
                    buffer, size = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
                    if kernel.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size)):
                        if Path(buffer.value).resolve().is_relative_to(package):
                            result.add(entry.th32ProcessID)
                finally:
                    kernel.CloseHandle(process)
            found = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    return result


class Acceptance:
    def __init__(self, package, report):
        self.package, self.report = package.resolve(), report.resolve()
        self.python = self.package / 'runtime' / 'python' / 'python.exe'
        self.launcher = self.package / 'ops' / 'windows_launcher.py'
        self.api_port, self.mysql_port = port_free(), port_free()
        while self.api_port == self.mysql_port:
            self.mysql_port = port_free()
        self.password = 'Portable9-' + secrets.token_hex(18)
        self.username = 'portable.admin'
        keep = {'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'COMSPEC'}
        self.environment = {key: value for key, value in os.environ.items() if key.upper() in keep}
        system = Path(os.environ.get('SystemRoot', r'C:\Windows'))
        self.environment['PATH'] = str(system / 'System32')
        self.environment.update(ONTOLOGY_BOOTSTRAP_USERNAME=self.username, ONTOLOGY_BOOTSTRAP_PASSWORD=self.password)
        self.rows = []
        self.sentinel = None
        self.last_launch_failure = None
        self.initial_processes = owned_process_ids(self.package) if os.name == 'nt' else set()

    def record(self, name, action):
        began = time.monotonic()
        self.last_launch_failure = None
        try:
            action()
            self.rows.append({'name': name, 'status': 'passed', 'duration_ms': round((time.monotonic() - began) * 1000)})
        except Exception as exc:
            row = {'name': name, 'status': 'failed', 'code': str(exc) if isinstance(exc, CheckFailure) else type(exc).__name__,
                   'duration_ms': round((time.monotonic() - began) * 1000)}
            if self.last_launch_failure:
                row['launcher_failure'] = self.last_launch_failure
            self.rows.append(row)
            raise

    def command(self, *arguments, success=True, timeout=930):
        result = subprocess.run([str(self.python), '-I', '-X', 'utf8', '-B', str(self.launcher), *arguments, '--json'],
                                cwd=self.package, env=self.environment, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
        require(len(result.stdout) <= 16384 and len(result.stderr) <= 16384, 'LAUNCH_OUTPUT_LIMIT')
        try:
            value = json.loads((result.stdout if result.returncode == 0 else result.stderr).decode('utf-8'))
        except (UnicodeError, ValueError):
            raise CheckFailure('LAUNCH_SAFE_JSON_REQUIRED') from None
        code = value.get('error', '') if isinstance(value, dict) else ''
        safe_code = code if isinstance(code, str) and all(character in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ_0123456789' for character in code) else ''
        if result.returncode:
            self.last_launch_failure = {'error': safe_code or 'UNKNOWN'}
            diagnostic = value.get('diagnostic') if isinstance(value, dict) else None
            if isinstance(diagnostic, dict):
                safe = {}
                for name in ('phase', 'exception', 'operation', 'code', 'migration_phase'):
                    item = diagnostic.get(name)
                    if isinstance(item, str) and 0 < len(item) <= 64 and all(c.isascii() and (c.isalnum() or c == '_') for c in item):
                        safe[name] = item
                for name in ('native_error', 'winerror', 'errno', 'process_exit', 'completed_tenants', 'total_tenants', 'elapsed_ms'):
                    item = diagnostic.get(name)
                    if type(item) is int and 0 <= item < 2**32:
                        safe[name] = item
                counters = diagnostic.get('process_counters')
                if isinstance(counters, dict):
                    safe['process_counters'] = {name: item for name, item in counters.items()
                        if name in {'exit_code', 'user_cpu_ms', 'kernel_cpu_ms', 'process_read_bytes', 'process_write_bytes',
                                    'stdin_bytes', 'stdout_bytes', 'stderr_bytes'} and type(item) is int and 0 <= item < 2**64}
                mysql_codes = diagnostic.get('mysql_codes')
                if isinstance(mysql_codes, list) and len(mysql_codes) <= 12:
                    valid_codes = [item for item in mysql_codes if isinstance(item, str) and len(item) == 9
                                   and item.startswith('MY-') and item[3:].isascii() and item[3:].isdigit()]
                    if valid_codes:
                        safe['mysql_codes'] = valid_codes
                client_codes = diagnostic.get('mysql_client_codes')
                if isinstance(client_codes, list) and len(client_codes) <= 12:
                    valid_codes = [item for item in client_codes if type(item) is int and 0 < item < 65536]
                    if valid_codes:
                        safe['mysql_client_codes'] = valid_codes
                if safe:
                    self.last_launch_failure['diagnostic'] = safe
        require((result.returncode == 0) == success, 'UNEXPECTED_LAUNCH_EXIT_' + str(result.returncode) + ('_' + safe_code if safe_code else ''))
        return value

    def start(self):
        result = self.command('start', '--no-browser', '--non-interactive', '--port', str(self.api_port), '--mysql-port', str(self.mysql_port))
        require(result.get('ready') is True and result.get('api_port') == self.api_port, 'START_NOT_READY')
        manifest=json.loads((self.package/'runtime/manifest.json').read_text(encoding='utf-8'))
        if manifest['application_version']!='0.5.0':
            require(result.get('processes',{}).get('agent',{}).get('alive') is True,'SHARED_AGENT_NOT_RUNNING')
        return result

    def stopped(self):
        result = self.command('stop', timeout=60)
        require(result.get('running') is False, 'STOP_NOT_FINISHED')
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            allowed = self.initial_processes | ({os.getpid(), self.sentinel.pid} if self.sentinel else {os.getpid()})
            remaining = owned_process_ids(self.package) - allowed
            if not remaining and can_bind(self.api_port) and can_bind(self.mysql_port):
                break
            time.sleep(.2)
        require(not remaining, 'OWNED_PROCESS_REMAINED')
        require(can_bind(self.api_port) and can_bind(self.mysql_port), 'OWNED_PORT_REMAINED')
        if self.sentinel:
            require(self.sentinel.poll() is None, 'UNRELATED_PROCESS_TERMINATED')

    def port_conflict(self):
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind(('127.0.0.1', self.api_port)); listener.listen(1)
            result = self.command('start', '--no-browser', '--non-interactive', '--port', str(self.api_port),
                                  '--mysql-port', str(self.mysql_port), success=False)
            require(result.get('error') == 'PORT_BUSY', 'PORT_CONFLICT_NOT_REJECTED')
            require('端口已被占用' in result.get('message', ''), 'CHINESE_ERROR_JSON_NOT_UTF8')
            with socket.create_connection(('127.0.0.1', self.api_port), timeout=2):
                connected, _ = listener.accept(); connected.close()
            require(not (self.package / 'data' / 'mysql').exists(), 'CONFLICT_STARTED_DATABASE')

    def redirected_utf8(self):
        # Deliberately conflicting environment settings prove the explicit
        # command-line mode works with -I and redirected Windows streams.
        environment = dict(self.environment, PYTHONUTF8='0', PYTHONIOENCODING='ascii')
        source = ("import json,sys;value={'text':'中文 空格 !','utf8':sys.flags.utf8_mode};"
                  "print(json.dumps(value,ensure_ascii=False));"
                  "sys.stderr.write(json.dumps({'message':'端口已被占用'},ensure_ascii=False))")
        result = subprocess.run([str(self.python), '-I', '-X', 'utf8', '-B', '-c', source],
            cwd=self.package, env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=15)
        require(result.returncode == 0, 'UTF8_CHILD_FAILED')
        try:
            output, error = json.loads(result.stdout.decode('utf-8')), json.loads(result.stderr.decode('utf-8'))
        except (UnicodeError, ValueError):
            raise CheckFailure('REDIRECTED_JSON_NOT_UTF8') from None
        require(output == {'text': '中文 空格 !', 'utf8': 1} and error == {'message': '端口已被占用'}, 'UTF8_MODE_NOT_ENFORCED')

    def first_failure(self):
        helper = self.package / 'ops' / 'windows_initialize.py'
        original = helper.read_bytes()
        try:
            helper.write_bytes(b'raise SystemExit(19)\n')
            result = self.command('start', '--no-browser', '--non-interactive', '--port', str(self.api_port),
                                  '--mysql-port', str(self.mysql_port), success=False)
            require(result.get('error') == 'MIGRATION_FAILED', 'FAILURE_NOT_REPORTED')
            require(not (self.package / 'data' / 'local' / 'initialized.json').exists(), 'FAILED_START_MARKED_INITIALIZED')
        finally:
            helper.write_bytes(original)
        self.stopped()

    def interrupted_bootstrap(self):
        """The committed admin survives a crash before the ready receipt."""
        helper = self.package / 'ops' / 'windows_initialize.py'
        original = helper.read_bytes()
        injected = original.replace(b'        main(progress=ProgressWriter())\n',
                                    b'        main(progress=ProgressWriter())\n        raise SystemExit(19)\n', 1)
        require(injected != original, 'BOOTSTRAP_FAILURE_INJECTION_MISSING')
        try:
            helper.write_bytes(injected)
            result = self.command('start', '--no-browser', '--non-interactive', '--port', str(self.api_port),
                                  '--mysql-port', str(self.mysql_port), success=False)
            require(result.get('error') == 'MIGRATION_FAILED', 'COMMITTED_BOOTSTRAP_FAILURE_NOT_REPORTED')
            require(not (self.package / 'data' / 'local' / 'initialized.json').exists(), 'FAILED_BOOTSTRAP_MARKED_READY')
        finally:
            helper.write_bytes(original)
        self.stopped()
        # A retry must reject a newly supplied password instead of silently
        # claiming that the existing administrator has been reset.
        self.environment['ONTOLOGY_BOOTSTRAP_PASSWORD'] = 'Different9-' + secrets.token_hex(18)
        try:
            result = self.command('start', '--no-browser', '--non-interactive', '--port', str(self.api_port),
                                  '--mysql-port', str(self.mysql_port), success=False)
            require(result.get('error') == 'MIGRATION_FAILED', 'BOOTSTRAP_PASSWORD_MISMATCH_ACCEPTED')
            require(not (self.package / 'data' / 'local' / 'initialized.json').exists(), 'MISMATCH_MARKED_READY')
        finally:
            self.environment['ONTOLOGY_BOOTSTRAP_PASSWORD'] = self.password
        self.stopped()

    def app_local_crt(self):
        status = self.command('status')
        observed = set()
        for name in ('mysql', 'api'):
            for path in process_modules(status['processes'][name]['pid']):
                if path.name.lower().startswith(('vcruntime140', 'msvcp140', 'concrt140')):
                    require(path.resolve().is_relative_to(self.package), 'VC_RUNTIME_RESOLVED_OUTSIDE_PACKAGE')
                    observed.add(path.name.lower())
        require('vcruntime140.dll' in observed, 'VC_RUNTIME_MODULE_NOT_OBSERVED')

    def account_and_crud(self):
        import httpx
        self.client = httpx.Client(base_url=f'http://127.0.0.1:{self.api_port}', trust_env=False, timeout=30)
        response = self.client.post('/api/auth/login', json={'username': self.username, 'password': self.password})
        require(response.status_code == 200, 'LOGIN_FAILED')
        data = response.json(); require(data['must_change_password'], 'INITIAL_PASSWORD_CHANGE_NOT_REQUIRED')
        require(data['user']['home_tenant_id'] == 'hq' and data['user']['role'] == 'admin', 'HEADQUARTERS_ADMIN_NOT_CREATED')
        updated = 'Changed8-' + secrets.token_hex(18)
        response = self.client.post('/api/auth/password', json={'current_password': self.password, 'new_password': updated},
                                    headers={'X-CSRF-Token': data['csrf_token']})
        require(response.status_code == 200, 'PASSWORD_CHANGE_FAILED')
        self.password = updated; self.csrf = response.json()['csrf_token']
        self.entity = 'KNOWLEDGE-QA-' + uuid.uuid4().hex[:16]
        response = self.client.post('/api/entities', headers={'X-CSRF-Token': self.csrf},
            json={'id': self.entity, 'kind': 'knowledge', 'title': '原生 Windows 中文资产', 'summary': '创建验收', 'payload': {}})
        require(response.status_code == 200 and response.json()['id'] == self.entity, 'ASSET_CREATE_FAILED')
        response = self.client.get('/api/entities/' + self.entity)
        require(response.status_code == 200 and response.json()['title'] == '原生 Windows 中文资产', 'ASSET_READ_FAILED')
        response = self.client.patch('/api/entities/' + self.entity, headers={'X-CSRF-Token': self.csrf},
            json={'summary': '修改后仍应保留', 'payload': {'portable_acceptance': True}})
        require(response.status_code == 200 and response.json()['version'] == 2, 'ASSET_UPDATE_FAILED')
        response = self.client.post('/api/entities/' + self.entity + '/action', headers={'X-CSRF-Token': self.csrf},
                                   json={'action': 'archive', 'reason': '验收归档'})
        require(response.status_code == 200 and response.json()['status'] == 'archived', 'ASSET_ARCHIVE_FAILED')
        self.client.close()

    def persistence(self):
        import httpx
        self.stopped(); self.start()
        with httpx.Client(base_url=f'http://127.0.0.1:{self.api_port}', trust_env=False, timeout=30) as client:
            response = client.post('/api/auth/login', json={'username': self.username, 'password': self.password})
            require(response.status_code == 200 and not response.json()['must_change_password'], 'ACCOUNT_NOT_PRESERVED')
            response = client.get('/api/entities/' + self.entity)
            require(response.status_code == 200, 'ASSET_NOT_PRESERVED')
            data = response.json()
            require(data['summary'] == '修改后仍应保留' and data['version'] == 3 and data['status'] == 'archived'
                    and data['payload'].get('portable_acceptance') is True, 'ASSET_CONTENT_NOT_PRESERVED')

    def batch_entrypoints(self):
        """Exercise the exact double-click entry chain after first-run setup."""
        system = Path(os.environ.get('SystemRoot', r'C:\Windows'))
        powershell = system / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'
        probe = subprocess.run([str(powershell), '-NoLogo', '-NoProfile', '-NonInteractive',
            '-Command', '[Console]::Write($PSVersionTable.PSVersion.ToString())'],
            cwd=self.package, env=self.environment, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        require(probe.returncode == 0 and probe.stdout.decode('ascii').startswith('5.1.'), 'SYSTEM_POWERSHELL51_REQUIRED')
        cmd = system / 'System32' / 'cmd.exe'
        # Keep the command grammar fixed. The sole expanded variable is the
        # quoted absolute BAT path. /d disables AutoRun, /v:off protects literal
        # exclamation marks, and there is no CALL or second expansion of paths.
        command_line = 'cmd.exe /d /v:off /s /c ""%ONTOLOGY_QA_BATCH_ENTRY%""'
        for filename, expected in (('启动平台.bat', '平台已就绪'), ('关闭平台.bat', '本安装实例已关闭')):
            environment = dict(self.environment, ONTOLOGY_QA_BATCH_ENTRY=str(self.package / filename))
            result = subprocess.run(command_line, executable=str(cmd), shell=False,
                cwd=self.package, env=environment, input=b'\r\n', stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, timeout=930 if filename == '启动平台.bat' else 90)
            require(result.returncode == 0, 'BATCH_ENTRY_FAILED_' + ('START' if filename == '启动平台.bat' else 'STOP'))
            require(len(result.stdout) <= 16384 and len(result.stderr) <= 16384, 'BATCH_OUTPUT_LIMIT')
            try:
                output = result.stdout.decode('utf-8')
                result.stderr.decode('utf-8')
            except UnicodeError:
                raise CheckFailure('BATCH_CHINESE_OUTPUT_NOT_UTF8') from None
            require(expected in output, 'BATCH_ENTRY_RESULT_NOT_CONFIRMED')
            state = self.command('status')
            require(state.get('ready') is (filename == '启动平台.bat'), 'BATCH_STATE_NOT_CONFIRMED')
        # This additionally proves both ports are released, no owned jobs remain,
        # and the unrelated process with the same python.exe is still alive.
        self.stopped()

    def run(self):
        require(os.name == 'nt' and ctypes.sizeof(ctypes.c_void_p) == 8, 'WINDOWS_X64_REQUIRED')
        require(not ctypes.windll.shell32.IsUserAnAdmin(), 'STANDARD_USER_REQUIRED')
        require(not (self.package / 'data').exists(), 'DISPOSABLE_FRESH_PACKAGE_REQUIRED')
        require(' ' in str(self.package) and '!' in str(self.package) and any(ord(x) > 127 for x in str(self.package)), 'UNICODE_SPACE_BANG_PATH_REQUIRED')
        began = time.monotonic()
        try:
            self.record('offline_runtime_check', lambda: require(self.command('--check').get('runtime_valid'), 'RUNTIME_INVALID'))
            self.record('explicit_utf8_redirected_chinese_json', self.redirected_utf8)
            self.sentinel = subprocess.Popen([str(self.python), '-I', '-X', 'utf8', '-B', '-c', 'import time;time.sleep(900)'],
                cwd=self.package, env=self.environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.record('port_conflict_preserves_other_listener', self.port_conflict)
            self.record('first_migration_failure_reaps_only_owned_jobs', self.first_failure)
            self.record('interrupted_bootstrap_preserves_original_credentials', self.interrupted_bootstrap)
            self.record('recover_first_start_and_health', self.start)
            self.record('crt_modules_resolve_app_local', self.app_local_crt)
            self.record('headquarters_login_password_change_asset_lifecycle', self.account_and_crud)
            self.record('restart_preserves_account_and_asset', self.persistence)
            self.record('duplicate_start_reuses_instance', self.start)
            self.record('stop_reaps_jobs_preserves_unrelated_process', self.stopped)
            self.record('powershell51_chinese_batch_start_and_stop', self.batch_entrypoints)
            self.record('duplicate_stop_is_idempotent', self.stopped)
        finally:
            with suppress(Exception): self.stopped()
            if self.sentinel:
                # This is the exact subprocess handle created by this test,
                # never a PID from a file or a search by executable name.
                self.sentinel.terminate()
                with suppress(Exception): self.sentinel.wait(timeout=5)
            self.report.parent.mkdir(parents=True, exist_ok=True)
            self.report.write_text(json.dumps({'platform': 'win_amd64', 'standard_user': True,
                'host_runtime_required': False, 'network_model_calls': 0,
                'passed': sum(row['status'] == 'passed' for row in self.rows),
                'failed': sum(row['status'] == 'failed' for row in self.rows),
                'duration_ms': round((time.monotonic() - began) * 1000), 'checks': self.rows,
                'boundary': 'Windows kernel/native process verification; OS version is recorded by CI. No Windows10/11-specific claim is inferred from a Windows Server runner.'},
                ensure_ascii=False, indent=2), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    try:
        Acceptance(args.package, args.report).run()
        print('WINDOWS_PORTABLE_SMOKE_PASSED')
        return 0
    except Exception as exc:
        print('WINDOWS_PORTABLE_SMOKE_FAILED:' + (str(exc) if isinstance(exc, CheckFailure) else type(exc).__name__))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
