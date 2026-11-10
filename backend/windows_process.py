"""Native Windows process ownership and low-privilege AppContainer execution.

Only documented Win32 APIs are used. A child cannot execute an instruction until
it belongs to its non-breakaway, kill-on-close Job Object. AppContainer failures
are terminal; there is no ordinary-process fallback. This module imports on
other systems for contract tests, but every native operation fails closed there.
"""
from __future__ import annotations

import ctypes as C
from dataclasses import dataclass
from functools import lru_cache
import math
import os
from pathlib import Path
import shutil
import stat
import subprocess
import threading
import time
import uuid

DWORD, WORD, BOOL, HRESULT = C.c_uint32, C.c_uint16, C.c_int32, C.c_int32
HANDLE, SIZE_T, LPVOID = C.c_void_p, C.c_size_t, C.c_void_p
PIPE, DEVNULL, STDOUT = subprocess.PIPE, subprocess.DEVNULL, subprocess.STDOUT
_ALL_APPLICATION_PACKAGES_OPT_OUT = 1


class WindowsProcessError(OSError):
    """Safe fixed diagnostic: never embeds command lines, paths or child output."""
    def __init__(self, code='PROCESS_UNAVAILABLE', *, native_error=None, operation=None, token_info_class=None, access_mask=None):
        self.code = code
        self.native_error = native_error if type(native_error) is int and 0 <= native_error <= 0xFFFFFFFF else None
        self.operation = operation if operation in {'CreateProcessW', 'AssignProcessToJobObject',
            'UpdateProcThreadAttribute', 'ResumeThread', 'OpenProcessToken', 'GetTokenInformation', 'DuplicateToken', 'AccessCheck'} else None
        self.token_info_class = token_info_class if type(token_info_class) is int and token_info_class in {29, 30, 31, 46} else None
        self.access_mask = access_mask if type(access_mask) is int and 0 <= access_mask <= 0xFFFFFFFF else None
        super().__init__(code)

    def diagnostic(self):
        # Opt-in native acceptance logging only; ordinary API reports still use
        # the fixed code. Numeric errors avoid FormatMessage/path disclosure.
        result = {'code': self.code, 'operation': self.operation, 'win32_error': self.native_error}
        if self.token_info_class is not None: result['token_info_class'] = self.token_info_class
        if self.access_mask is not None: result['access_mask'] = self.access_mask
        return result


@dataclass(frozen=True)
class ProcessLimits:
    memory_bytes: int | None = None
    active_processes: int | None = None
    cpu_time_seconds: float | None = None
    cpu_rate_percent: int | None = None

    def __post_init__(self):
        if self.memory_bytes is not None and (type(self.memory_bytes) is not int or not 16 * 1024 * 1024 <= self.memory_bytes <= (1 << (8 * C.sizeof(SIZE_T))) - 1):
            raise ValueError('Invalid memory limit')
        if self.active_processes is not None and (type(self.active_processes) is not int or not 1 <= self.active_processes <= 256):
            raise ValueError('Invalid process limit')
        if self.cpu_time_seconds is not None and (type(self.cpu_time_seconds) not in (int, float) or not math.isfinite(self.cpu_time_seconds) or not 0 < self.cpu_time_seconds <= ((1 << 63) - 1) / 10_000_000):
            raise ValueError('Invalid CPU limit')
        if self.cpu_time_seconds is not None and not 1 <= round(self.cpu_time_seconds * 10_000_000) <= (1 << 63) - 1:
            raise ValueError('Invalid CPU limit')
        if self.cpu_rate_percent is not None and (type(self.cpu_rate_percent) is not int or not 1 <= self.cpu_rate_percent <= 100):
            raise ValueError('Invalid CPU rate')


class SECURITY_ATTRIBUTES(C.Structure):
    _fields_ = [('nLength', DWORD), ('lpSecurityDescriptor', LPVOID), ('bInheritHandle', BOOL)]


class STARTUPINFOW(C.Structure):
    _fields_ = [('cb', DWORD), ('lpReserved', C.c_wchar_p), ('lpDesktop', C.c_wchar_p), ('lpTitle', C.c_wchar_p),
                ('dwX', DWORD), ('dwY', DWORD), ('dwXSize', DWORD), ('dwYSize', DWORD),
                ('dwXCountChars', DWORD), ('dwYCountChars', DWORD), ('dwFillAttribute', DWORD), ('dwFlags', DWORD),
                ('wShowWindow', WORD), ('cbReserved2', WORD), ('lpReserved2', LPVOID),
                ('hStdInput', HANDLE), ('hStdOutput', HANDLE), ('hStdError', HANDLE)]


class STARTUPINFOEXW(C.Structure):
    _fields_ = [('StartupInfo', STARTUPINFOW), ('lpAttributeList', LPVOID)]


class PROCESS_INFORMATION(C.Structure):
    _fields_ = [('hProcess', HANDLE), ('hThread', HANDLE), ('dwProcessId', DWORD), ('dwThreadId', DWORD)]


class SECURITY_CAPABILITIES(C.Structure):
    _fields_ = [('AppContainerSid', LPVOID), ('Capabilities', LPVOID), ('CapabilityCount', DWORD), ('Reserved', DWORD)]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(C.Structure):
    _fields_ = [('PerProcessUserTimeLimit', C.c_int64), ('PerJobUserTimeLimit', C.c_int64), ('LimitFlags', DWORD),
                ('MinimumWorkingSetSize', SIZE_T), ('MaximumWorkingSetSize', SIZE_T), ('ActiveProcessLimit', DWORD),
                ('Affinity', SIZE_T), ('PriorityClass', DWORD), ('SchedulingClass', DWORD)]


class IO_COUNTERS(C.Structure):
    _fields_ = [(name, C.c_uint64) for name in ('ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
                                              'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(C.Structure):
    _fields_ = [('BasicLimitInformation', JOBOBJECT_BASIC_LIMIT_INFORMATION), ('IoInfo', IO_COUNTERS),
                ('ProcessMemoryLimit', SIZE_T), ('JobMemoryLimit', SIZE_T), ('PeakProcessMemoryUsed', SIZE_T), ('PeakJobMemoryUsed', SIZE_T)]


class JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(C.Structure):
    _fields_ = [('TotalUserTime', C.c_int64), ('TotalKernelTime', C.c_int64), ('ThisPeriodTotalUserTime', C.c_int64),
                ('ThisPeriodTotalKernelTime', C.c_int64), ('TotalPageFaultCount', DWORD), ('TotalProcesses', DWORD),
                ('ActiveProcesses', DWORD), ('TotalTerminatedProcesses', DWORD)]


class JOBOBJECT_CPU_RATE_CONTROL_INFORMATION(C.Structure):
    _fields_ = [('ControlFlags', DWORD), ('CpuRate', DWORD)]


class SERVICE_STATUS_PROCESS(C.Structure):
    _fields_ = [(name, DWORD) for name in ('dwServiceType', 'dwCurrentState', 'dwControlsAccepted',
        'dwWin32ExitCode', 'dwServiceSpecificExitCode', 'dwCheckPoint', 'dwWaitHint', 'dwProcessId', 'dwServiceFlags')]


class GENERIC_MAPPING(C.Structure):
    _fields_ = [(name, DWORD) for name in ('GenericRead', 'GenericWrite', 'GenericExecute', 'GenericAll')]


@lru_cache(maxsize=1)
def _api():
    if os.name != 'nt' or C.sizeof(LPVOID) != 8:
        raise WindowsProcessError('WINDOWS_X64_REQUIRED')
    kernel = C.WinDLL('kernel32', use_last_error=True)
    advapi = C.WinDLL('advapi32', use_last_error=True)
    userenv = C.WinDLL('userenv', use_last_error=True)
    ole = C.WinDLL('ole32', use_last_error=True)
    signatures = {
        'CloseHandle': (BOOL, [HANDLE]), 'GetCurrentProcess': (HANDLE, []),
        'CreateJobObjectW': (HANDLE, [LPVOID, C.c_wchar_p]),
        'SetInformationJobObject': (BOOL, [HANDLE, C.c_int, LPVOID, DWORD]),
        'QueryInformationJobObject': (BOOL, [HANDLE, C.c_int, LPVOID, DWORD, LPVOID]),
        'AssignProcessToJobObject': (BOOL, [HANDLE, HANDLE]), 'TerminateJobObject': (BOOL, [HANDLE, DWORD]),
        'IsProcessInJob': (BOOL, [HANDLE, HANDLE, C.POINTER(BOOL)]),
        'TerminateProcess': (BOOL, [HANDLE, DWORD]), 'WaitForSingleObject': (DWORD, [HANDLE, DWORD]),
        'GetExitCodeProcess': (BOOL, [HANDLE, C.POINTER(DWORD)]), 'ResumeThread': (DWORD, [HANDLE]),
        'CreatePipe': (BOOL, [C.POINTER(HANDLE), C.POINTER(HANDLE), C.POINTER(SECURITY_ATTRIBUTES), DWORD]),
        'SetHandleInformation': (BOOL, [HANDLE, DWORD, DWORD]),
        'CreateFileW': (HANDLE, [C.c_wchar_p, DWORD, DWORD, C.POINTER(SECURITY_ATTRIBUTES), DWORD, DWORD, HANDLE]),
        'DuplicateHandle': (BOOL, [HANDLE, HANDLE, HANDLE, C.POINTER(HANDLE), DWORD, BOOL, DWORD]),
        'InitializeProcThreadAttributeList': (BOOL, [LPVOID, DWORD, DWORD, C.POINTER(SIZE_T)]),
        'UpdateProcThreadAttribute': (BOOL, [LPVOID, DWORD, SIZE_T, LPVOID, SIZE_T, LPVOID, LPVOID]),
        'DeleteProcThreadAttributeList': (None, [LPVOID]),
        'CreateProcessW': (BOOL, [C.c_wchar_p, C.c_wchar_p, LPVOID, LPVOID, BOOL, DWORD, LPVOID, C.c_wchar_p,
                                  C.POINTER(STARTUPINFOEXW), C.POINTER(PROCESS_INFORMATION)]),
        'LocalFree': (LPVOID, [LPVOID]),
    }
    for name, (result, args) in signatures.items():
        function = getattr(kernel, name); function.restype, function.argtypes = result, args
    for name, result, args in (
        ('ConvertStringSecurityDescriptorToSecurityDescriptorW', BOOL, [C.c_wchar_p, DWORD, C.POINTER(LPVOID), LPVOID]),
        ('SetFileSecurityW', BOOL, [C.c_wchar_p, DWORD, LPVOID]),
        ('ConvertSidToStringSidW', BOOL, [LPVOID, C.POINTER(LPVOID)]),
        ('FreeSid', LPVOID, [LPVOID]),
        ('OpenProcessToken', BOOL, [HANDLE, DWORD, C.POINTER(HANDLE)]),
        ('GetTokenInformation', BOOL, [HANDLE, C.c_int, LPVOID, DWORD, C.POINTER(DWORD)]),
        ('EqualSid', BOOL, [LPVOID, LPVOID]),
        ('DuplicateToken', BOOL, [HANDLE, C.c_int, C.POINTER(HANDLE)]),
        ('AccessCheck', BOOL, [LPVOID, HANDLE, DWORD, C.POINTER(GENERIC_MAPPING), LPVOID,
                               C.POINTER(DWORD), C.POINTER(DWORD), C.POINTER(BOOL)]),
        ('OpenSCManagerW', HANDLE, [C.c_wchar_p, C.c_wchar_p, DWORD]),
        ('OpenServiceW', HANDLE, [HANDLE, C.c_wchar_p, DWORD]),
        ('QueryServiceStatusEx', BOOL, [HANDLE, C.c_int, LPVOID, DWORD, C.POINTER(DWORD)]),
        ('CloseServiceHandle', BOOL, [HANDLE]),
    ):
        function = getattr(advapi, name); function.restype, function.argtypes = result, args
    for name, args in (
        ('CreateAppContainerProfile', [C.c_wchar_p, C.c_wchar_p, C.c_wchar_p, LPVOID, DWORD, C.POINTER(LPVOID)]),
        ('DeleteAppContainerProfile', [C.c_wchar_p]),
        ('GetAppContainerFolderPath', [C.c_wchar_p, C.POINTER(LPVOID)]),
    ):
        function = getattr(userenv, name); function.restype, function.argtypes = HRESULT, args
    ole.CoTaskMemFree.restype, ole.CoTaskMemFree.argtypes = None, [LPVOID]
    return kernel, advapi, userenv, ole


def _check(value, code='PROCESS_UNAVAILABLE', *, operation=None, token_info_class=None):
    if not value:
        # Cleanup Win32 calls can overwrite the thread-local last error.
        native_error = C.get_last_error() if hasattr(C, 'get_last_error') else None
        raise WindowsProcessError(code, native_error=native_error, operation=operation, token_info_class=token_info_class)
    return value


def _environment_block(env):
    if not isinstance(env, dict):
        raise ValueError('Explicit environment required')
    seen, entries = set(), []
    for key, value in sorted(env.items(), key=lambda item: str(item[0]).upper()):
        if (not isinstance(key, str) or not key or '=' in key or '\0' in key or key.upper() in seen
                or not isinstance(value, str) or '\0' in value):
            raise ValueError('Invalid environment')
        seen.add(key.upper()); entries.append(key + '=' + value)
    return '\0'.join(entries) + '\0\0'


def _validate_appcontainer_environment(env):
    # Windows' AppContainer process creation requires these two entries even
    # with an explicit environment block. Missing LOCALAPPDATA raises Win32
    # 203 before the executable starts (Microsoft/mxc regression #1158).
    # Their presence does not grant directory access; the LPAC DACL still does.
    entries = {key.upper(): value for key, value in env.items()}
    if not all(entries.get(key) for key in ('SYSTEMROOT', 'LOCALAPPDATA')):
        raise WindowsProcessError('APPCONTAINER_ENVIRONMENT_REQUIRED')


def _job_limits(limits):
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    # KILL_ON_JOB_CLOSE; neither BREAKAWAY_OK nor SILENT_BREAKAWAY_OK is set.
    info.BasicLimitInformation.LimitFlags = 0x2000
    if limits.memory_bytes is not None:
        info.BasicLimitInformation.LimitFlags |= 0x200
        info.JobMemoryLimit = limits.memory_bytes
    if limits.active_processes is not None:
        info.BasicLimitInformation.LimitFlags |= 0x8
        info.BasicLimitInformation.ActiveProcessLimit = limits.active_processes
    if limits.cpu_time_seconds is not None:
        info.BasicLimitInformation.LimitFlags |= 0x4
        info.BasicLimitInformation.PerJobUserTimeLimit = round(limits.cpu_time_seconds * 10_000_000)
    return info


def _verify_lpac_access(token):
    """Prove effective LPAC semantics on the actual suspended child's token.

    Token information class 46 is enumerated, but some Windows user-mode builds
    reject it with ERROR_INVALID_PARAMETER. We instead use documented Win32
    DuplicateToken + AccessCheck: an LPAC disregards ALL APPLICATION PACKAGES.
    This is the same synthetic access-mask check used by Chromium's sandbox:
    https://chromium.googlesource.com/chromium/src/+/main/sandbox/win/src/app_container_test.cc
    See CheckLpacToken; the 1/2 masks are our synthetic rights, not OS flags.
    No real resource ACL is modified, and failure never permits ResumeThread.
    """
    kernel, advapi, _, _ = _api()
    impersonation, descriptor = HANDLE(), LPVOID()
    _check(advapi.DuplicateToken(token, 2, C.byref(impersonation)), 'APPCONTAINER_UNAVAILABLE', operation='DuplicateToken')
    try:
        sddl = 'O:SYG:SYD:(A;;0x3;;;WD)(A;;0x1;;;S-1-15-2-1)(A;;0x2;;;S-1-15-2-2)'
        _check(advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, C.byref(descriptor), None), 'APPCONTAINER_UNAVAILABLE')
        mapping = GENERIC_MAPPING()
        privileges = C.create_string_buffer(1024)
        privilege_size, granted, permitted = DWORD(len(privileges)), DWORD(), BOOL()
        _check(advapi.AccessCheck(descriptor, impersonation, 0x02000000, C.byref(mapping), privileges,
            C.byref(privilege_size), C.byref(granted), C.byref(permitted)), 'APPCONTAINER_UNAVAILABLE', operation='AccessCheck')
        if not permitted.value or granted.value != 0x2:
            raise WindowsProcessError('APPCONTAINER_LPAC_REQUIRED', operation='AccessCheck', access_mask=int(granted.value))
    finally:
        if descriptor: kernel.LocalFree(descriptor)
        kernel.CloseHandle(impersonation)


def _verify_appcontainer_token(process, expected_sid):
    kernel, advapi, _, _ = _api()
    token = HANDLE()
    _check(advapi.OpenProcessToken(process, 0x8 | 0x2, C.byref(token)), 'APPCONTAINER_UNAVAILABLE', operation='OpenProcessToken')
    try:
        value, size = DWORD(), DWORD()
        _check(advapi.GetTokenInformation(token, 29, C.byref(value), C.sizeof(value), C.byref(size)),
               'APPCONTAINER_UNAVAILABLE', operation='GetTokenInformation', token_info_class=29)
        if not value.value or size.value != C.sizeof(value):
            raise WindowsProcessError('APPCONTAINER_UNAVAILABLE', operation='GetTokenInformation', token_info_class=29)
        _verify_lpac_access(token)
        size = DWORD()
        advapi.GetTokenInformation(token, 31, None, 0, C.byref(size))
        if not 0 < size.value < 65536: raise WindowsProcessError('APPCONTAINER_UNAVAILABLE')
        data = C.create_string_buffer(size.value)
        _check(advapi.GetTokenInformation(token, 31, data, size, C.byref(size)), 'APPCONTAINER_UNAVAILABLE',
               operation='GetTokenInformation', token_info_class=31)
        observed_sid = C.cast(data, C.POINTER(LPVOID))[0]
        _check(advapi.EqualSid(observed_sid, expected_sid), 'APPCONTAINER_UNAVAILABLE')
        size = DWORD()
        advapi.GetTokenInformation(token, 30, None, 0, C.byref(size))
        if not 0 < size.value < 65536: raise WindowsProcessError('APPCONTAINER_UNAVAILABLE')
        data = C.create_string_buffer(size.value)
        _check(advapi.GetTokenInformation(token, 30, data, size, C.byref(size)), 'APPCONTAINER_UNAVAILABLE',
               operation='GetTokenInformation', token_info_class=30)
        if C.cast(data, C.POINTER(DWORD))[0] != 0:
            raise WindowsProcessError('APPCONTAINER_UNAVAILABLE')
    finally:
        kernel.CloseHandle(token)


def assert_network_isolation_services():
    """Require our conservative network-isolation prerequisites, read-only.

    AppContainer capability checks rely on Windows network isolation/WFP. A
    token flag alone is not a substitute for an operational filtering stack.
    Microsoft documents BFE and Windows Firewall as prerequisites for the
    firewall stack. It does not promise LPAC isolation with stopped services;
    this product therefore refuses that unverified state. This does not change
    firewall profiles, start services or request elevation.
    """
    _, advapi, _, _ = _api()
    manager = _check(advapi.OpenSCManagerW(None, None, 0x1), 'NETWORK_ISOLATION_UNAVAILABLE')
    try:
        for name in ('BFE', 'MpsSvc'):
            service = _check(advapi.OpenServiceW(manager, name, 0x4), 'NETWORK_ISOLATION_UNAVAILABLE')
            try:
                status, size = SERVICE_STATUS_PROCESS(), DWORD()
                _check(advapi.QueryServiceStatusEx(service, 0, C.byref(status), C.sizeof(status), C.byref(size)), 'NETWORK_ISOLATION_UNAVAILABLE')
                if status.dwCurrentState != 4:  # SERVICE_RUNNING, not START_PENDING.
                    raise WindowsProcessError('NETWORK_ISOLATION_UNAVAILABLE')
            finally:
                advapi.CloseServiceHandle(service)
    finally:
        advapi.CloseServiceHandle(manager)


def current_process_in_job():
    kernel, _, _, _ = _api()
    result = BOOL()
    _check(kernel.IsProcessInJob(kernel.GetCurrentProcess(), None, C.byref(result)))
    return bool(result.value)


def assert_job_limits(*, max_memory_bytes, max_processes, max_cpu_time_seconds):
    """Verify the kernel's current job; an environment marker is not evidence."""
    kernel, _, _, _ = _api()
    if not current_process_in_job():
        raise WindowsProcessError('PROCESS_LIMITS_REQUIRED')
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    _check(kernel.QueryInformationJobObject(None, 9, C.byref(info), C.sizeof(info), None), 'PROCESS_LIMITS_REQUIRED')
    basic = info.BasicLimitInformation
    required = 0x2000 | 0x200 | 0x8 | 0x4
    if (basic.LimitFlags & required != required or basic.LimitFlags & (0x800 | 0x1000)
            or not 0 < info.JobMemoryLimit <= max_memory_bytes
            or not 0 < basic.ActiveProcessLimit <= max_processes
            or not 0 < basic.PerJobUserTimeLimit <= round(max_cpu_time_seconds * 10_000_000)):
        raise WindowsProcessError('PROCESS_LIMITS_REQUIRED')
    return {'memory_bytes': int(info.JobMemoryLimit), 'active_processes': int(basic.ActiveProcessLimit),
            'cpu_time_seconds': basic.PerJobUserTimeLimit / 10_000_000, 'kill_on_close': True}


class WindowsProcess:
    def __init__(self, kernel, job, process, pid, streams):
        self._kernel, self._job, self._process = kernel, job, process
        self.pid = pid
        self.stdin, self.stdout, self.stderr = streams
        self._returncode = None
        self._lock, self._communication = threading.RLock(), threading.Lock()
        self._closed = False

    @property
    def returncode(self):
        return self.poll()

    def poll(self):
        with self._lock:
            if self._returncode is not None:
                return self._returncode
            if self._closed:
                raise WindowsProcessError('PROCESS_CLOSED')
            status = self._kernel.WaitForSingleObject(self._process, 0)
            if status == 258:
                return None
            if status != 0:
                raise WindowsProcessError()
            code = DWORD(); _check(self._kernel.GetExitCodeProcess(self._process, C.byref(code)))
            self._returncode = int(code.value)
            return self._returncode

    def wait(self, timeout=None):
        if timeout is not None and (not math.isfinite(timeout) or timeout < 0):
            raise ValueError('Invalid timeout')
        deadline = None if timeout is None else time.monotonic() + timeout
        while (code := self.poll()) is None:
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError('PROCESS_TIMEOUT')
            time.sleep(.01)
        return code

    def terminate_tree(self):
        with self._lock:
            if not self._closed:
                _check(self._kernel.TerminateJobObject(self._job, 0xE0000001), 'PROCESS_CLEANUP_FAILED')

    terminate = terminate_tree
    kill = terminate_tree

    def _wait_empty(self, timeout=5):
        deadline = time.monotonic() + timeout
        while True:
            if self._closed or self._job is None:
                raise WindowsProcessError('PROCESS_CLOSED')
            info = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
            _check(self._kernel.QueryInformationJobObject(self._job, 1, C.byref(info), C.sizeof(info), None), 'PROCESS_CLEANUP_FAILED')
            if not info.ActiveProcesses:
                return
            if time.monotonic() >= deadline:
                raise WindowsProcessError('PROCESS_CLEANUP_FAILED')
            time.sleep(.01)

    def process_ids(self):
        """An observation of this job only; never a global process enumeration."""
        with self._lock:
            if self._closed: return ()
            for capacity in (64, 256, 1024, 4096):
                data = C.create_string_buffer(8 + capacity * C.sizeof(SIZE_T))
                if self._kernel.QueryInformationJobObject(self._job, 3, data, len(data), None):
                    header = C.cast(data, C.POINTER(DWORD))
                    assigned, listed = int(header[0]), int(header[1])
                    if assigned <= listed <= capacity:
                        values = C.cast(C.byref(data, 8), C.POINTER(SIZE_T))
                        return tuple(int(values[index]) for index in range(listed))
            raise WindowsProcessError('PROCESS_QUERY_FAILED')

    def communicate(self, input=b'', *, timeout=None, cancelled=None, max_stdout=2 * 1024 * 1024, max_stderr=64 * 1024):
        if not isinstance(input, bytes) or type(max_stdout) is not int or type(max_stderr) is not int or min(max_stdout, max_stderr) < 0:
            raise ValueError('Invalid communication limits')
        if timeout is not None and (not math.isfinite(timeout) or timeout <= 0):
            raise ValueError('Invalid timeout')
        if not self._communication.acquire(blocking=False):
            raise WindowsProcessError('PROCESS_ALREADY_READING')
        output, errors, guard = [bytearray(), bytearray()], [], threading.Lock()
        deadline = None if timeout is None else time.monotonic() + timeout
        def fail(code):
            with guard:
                if not errors: errors.append(code)
            self.terminate_tree()
        def read(stream, index, limit):
            if stream is None: return
            try:
                while chunk := stream.read(16384):
                    if len(output[index]) + len(chunk) > limit:
                        fail('OUTPUT_LIMIT'); return
                    output[index].extend(chunk)
            except (OSError, ValueError):
                if self.poll() is None: fail('PROCESS_IO_FAILED')
        def write():
            if self.stdin is None: return
            try:
                view = memoryview(input)
                while view:
                    count = self.stdin.write(view[:65536])
                    if not count: break
                    view = view[count:]
                self.stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                pass
            finally:
                self.stdin.close()
        threads = [threading.Thread(target=read, args=(self.stdout, 0, max_stdout), name='windows-stdout'),
                   threading.Thread(target=read, args=(self.stderr, 1, max_stderr), name='windows-stderr'),
                   threading.Thread(target=write, name='windows-stdin')]
        try:
            for thread in threads: thread.start()
            while self.poll() is None or any(thread.is_alive() for thread in threads):
                try: interrupted = bool(cancelled() if callable(cancelled) else cancelled is not None and cancelled.is_set())
                except Exception: interrupted = True
                if interrupted: fail('CANCELLED'); break
                if deadline is not None and time.monotonic() >= deadline: fail('TIMEOUT'); break
                if errors: break
                # A descendant may retain a pipe after its leader exits. Job
                # ownership, rather than pipe EOF, determines cleanup authority.
                if self.poll() is not None:
                    self.terminate_tree()
                time.sleep(.01)
            self.wait(timeout=5)
        finally:
            try:
                try:
                    self.terminate_tree()
                    self._wait_empty()
                finally:
                    # A concurrent cancellation fallback may already have
                    # closed the Job. Native-query errors still cannot abandon
                    # the three threads that own this communication's pipes.
                    for thread in threads:
                        if thread.ident is not None: thread.join(timeout=5)
                    if any(thread.is_alive() for thread in threads):
                        raise WindowsProcessError('PROCESS_CLEANUP_FAILED')
            finally:
                self._communication.release()
        if errors:
            raise WindowsProcessError(errors[0])
        return bytes(output[0]), bytes(output[1])

    def close(self):
        with self._lock:
            if self._closed: return
            try:
                self.terminate_tree(); self._wait_empty(); self.wait(timeout=5)
            finally:
                # If explicit termination failed, close the kill-on-close job
                # before touching pipes: no descendant may keep a pipe writer
                # alive while its owner's reader is being shut down.
                self._kernel.CloseHandle(self._job)
                self._job = None
                if self._returncode is None and self._kernel.WaitForSingleObject(self._process, 5000) == 0:
                    code = DWORD()
                    if self._kernel.GetExitCodeProcess(self._process, C.byref(code)):
                        self._returncode = int(code.value)
                pipe_error = False
                try:
                    for stream in (self.stdin, self.stdout, self.stderr):
                        try:
                            if stream is not None and not stream.closed: stream.close()
                        except OSError:
                            pipe_error = True
                finally:
                    self._kernel.CloseHandle(self._process)
                    self._closed = True
                if pipe_error:
                    raise WindowsProcessError('PROCESS_CLEANUP_FAILED')

    def __enter__(self): return self
    def __exit__(self, *_): self.close()


def spawn_job_process(argv, *, cwd, env, limits=None, appcontainer=None, stdin=PIPE, stdout=PIPE, stderr=PIPE):
    """Create one owned job and a suspended child; resume only after assignment."""
    kernel, _, _, _ = _api()
    if appcontainer is not None:
        assert_network_isolation_services()
    import msvcrt
    if (not isinstance(argv, (list, tuple)) or not argv or any(not isinstance(arg, str) or '\0' in arg for arg in argv)
            or not Path(argv[0]).is_absolute() or not Path(cwd).is_absolute()):
        raise ValueError('Absolute executable and working directory required')
    limits = ProcessLimits() if limits is None else limits
    if not isinstance(limits, ProcessLimits): raise ValueError('Invalid limits')
    environment = C.create_unicode_buffer(_environment_block(env))
    if appcontainer is not None:
        _validate_appcontainer_environment(env)
    command = C.create_unicode_buffer(subprocess.list2cmdline(argv))
    job, process, attrs = None, PROCESS_INFORMATION(), None
    attrs_ready = False
    child_handles, parent_handles, streams = [], [], []
    inherited = SECURITY_ATTRIBUTES(C.sizeof(SECURITY_ATTRIBUTES), None, True)
    def child_stream(spec, reading):
        if spec == PIPE:
            read, write = HANDLE(), HANDLE()
            _check(kernel.CreatePipe(C.byref(read), C.byref(write), C.byref(inherited), 0))
            child, parent = (read.value, write.value) if reading else (write.value, read.value)
            child_handles.append(child); parent_handles.append(parent)
            _check(kernel.SetHandleInformation(parent, 1, 0))
            return child, parent
        if spec == DEVNULL:
            child = kernel.CreateFileW('NUL', 0x80000000 if reading else 0x40000000, 3, C.byref(inherited), 3, 0, None)
            if child in (None, C.c_void_p(-1).value): raise WindowsProcessError()
        elif hasattr(spec, 'fileno'):
            child = HANDLE()
            _check(kernel.DuplicateHandle(kernel.GetCurrentProcess(), msvcrt.get_osfhandle(spec.fileno()),
                kernel.GetCurrentProcess(), C.byref(child), 0, True, 2))
            child = child.value
        else:
            raise ValueError('Explicit binary pipe, DEVNULL or file required')
        child_handles.append(child)
        return child, None
    try:
        job = _check(kernel.CreateJobObjectW(None, None))
        info = _job_limits(limits)
        _check(kernel.SetInformationJobObject(job, 9, C.byref(info), C.sizeof(info)))
        if limits.cpu_rate_percent is not None:
            cpu = JOBOBJECT_CPU_RATE_CONTROL_INFORMATION(0x1 | 0x4, limits.cpu_rate_percent * 100)
            _check(kernel.SetInformationJobObject(job, 15, C.byref(cpu), C.sizeof(cpu)))
        si = STARTUPINFOEXW(); si.StartupInfo.cb = C.sizeof(si); si.StartupInfo.dwFlags = 0x100
        cin, pin = child_stream(stdin, True)
        cout, pout = child_stream(stdout, False)
        cerr, perr = (cout, None) if stderr == STDOUT else child_stream(stderr, False)
        si.StartupInfo.hStdInput, si.StartupInfo.hStdOutput, si.StartupInfo.hStdError = cin, cout, cerr
        count = 3 if appcontainer is not None else 1
        size = SIZE_T(); kernel.InitializeProcThreadAttributeList(None, count, 0, C.byref(size))
        if not size.value: raise WindowsProcessError()
        attrs = C.create_string_buffer(size.value)
        _check(kernel.InitializeProcThreadAttributeList(attrs, count, 0, C.byref(size)))
        attrs_ready = True
        si.lpAttributeList = C.cast(attrs, LPVOID)
        handles = (HANDLE * len(set(child_handles)))(*dict.fromkeys(child_handles))
        _check(kernel.UpdateProcThreadAttribute(attrs, 0, 0x20002, handles, C.sizeof(handles), None, None))
        if appcontainer is not None:
            if not isinstance(appcontainer, AppContainerProfile) or not appcontainer.sid:
                raise WindowsProcessError('APPCONTAINER_UNAVAILABLE')
            capabilities = SECURITY_CAPABILITIES(appcontainer.sid, None, 0, 0)
            policy = DWORD(_ALL_APPLICATION_PACKAGES_OPT_OUT)  # Always 1 in production; native tests exercise the rejecting negative control.
            _check(kernel.UpdateProcThreadAttribute(attrs, 0, 0x20009, C.byref(capabilities), C.sizeof(capabilities), None, None), 'APPCONTAINER_UNAVAILABLE')
            _check(kernel.UpdateProcThreadAttribute(attrs, 0, 0x2000F, C.byref(policy), C.sizeof(policy), None, None), 'APPCONTAINER_UNAVAILABLE')
        flags = 0x4 | 0x80000 | 0x400 | 0x08000000
        _check(kernel.CreateProcessW(argv[0], command, None, None, True, flags, environment, str(cwd), C.byref(si), C.byref(process)),
               'APPCONTAINER_UNAVAILABLE' if appcontainer is not None else 'PROCESS_UNAVAILABLE', operation='CreateProcessW')
        _check(kernel.AssignProcessToJobObject(job, process.hProcess), operation='AssignProcessToJobObject')
        if appcontainer is not None:
            _verify_appcontainer_token(process.hProcess, appcontainer.sid)
        # Convert only parent ends. The child ends and job handle are never
        # exposed to uploaded code beyond the explicit three-handle allowlist.
        for parent, reading in ((pin, False), (pout, True), (perr, True)):
            if parent is None:
                streams.append(None); continue
            fd = msvcrt.open_osfhandle(parent, os.O_BINARY | (os.O_RDONLY if reading else os.O_WRONLY))
            parent_handles.remove(parent)
            try:
                stream = os.fdopen(fd, 'rb' if reading else 'wb', buffering=0)
            except BaseException:
                os.close(fd); raise
            streams.append(stream)
        if kernel.ResumeThread(process.hThread) == 0xFFFFFFFF:
            raise WindowsProcessError()
        owner = WindowsProcess(kernel, job, process.hProcess, int(process.dwProcessId), streams)
        job, process.hProcess = None, None
        return owner
    except BaseException:
        if process.hProcess:
            kernel.TerminateProcess(process.hProcess, 0xE0000001)
            kernel.WaitForSingleObject(process.hProcess, 5000)
            kernel.CloseHandle(process.hProcess)
        if job:
            kernel.TerminateJobObject(job, 0xE0000001); kernel.CloseHandle(job)
        for stream in streams:
            if stream is not None: stream.close()
        raise
    finally:
        if process.hThread: kernel.CloseHandle(process.hThread)
        if attrs_ready: kernel.DeleteProcThreadAttributeList(attrs)
        for handle in child_handles + parent_handles: kernel.CloseHandle(handle)


spawn = spawn_job_process


def _plain_path(path):
    path = Path(path)
    if not path.is_absolute(): raise WindowsProcessError('APPCONTAINER_PATH_INVALID')
    for item in (path, *path.parents):
        if item.exists() and getattr(item.lstat(), 'st_file_attributes', 0) & 0x400:
            raise WindowsProcessError('APPCONTAINER_PATH_INVALID')
    return path


def _container_acl(path, sid, *, executable=False):
    """Replace the DACL of an owned copy, never mutate a shared installation."""
    from .platform_io import current_user_sid
    path = _plain_path(path)
    if not path.exists(): raise WindowsProcessError('APPCONTAINER_PATH_INVALID')
    kernel, advapi, _, _ = _api()
    inheritance = 'OICI' if path.is_dir() else ''
    access = 'GRGX' if executable or path.is_dir() else 'GR'
    descriptor = f'D:P(A;{inheritance};FA;;;SY)(A;{inheritance};FA;;;{current_user_sid()})(A;{inheritance};{access};;;{sid})'
    sd = LPVOID()
    _check(advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(descriptor, 1, C.byref(sd), None), 'APPCONTAINER_ACL_FAILED')
    try:
        _check(advapi.SetFileSecurityW(str(path), 0x4 | 0x80000000, sd), 'APPCONTAINER_ACL_FAILED')
    finally:
        kernel.LocalFree(sd)


class AppContainerProfile:
    """One random LPAC identity, without network capabilities or reusable trust."""
    def __init__(self):
        self.name, self.sid, self.sid_string = 'OntologyOne.Skill.' + uuid.uuid4().hex, None, None
        self.folder = None
        self._created = False

    def __enter__(self):
        kernel, advapi, userenv, ole = _api()
        assert_network_isolation_services()
        sid = LPVOID()
        if userenv.CreateAppContainerProfile(self.name, 'Ontology One isolated skill', 'Ephemeral synthetic skill test', None, 0, C.byref(sid)) < 0:
            raise WindowsProcessError('APPCONTAINER_UNAVAILABLE')
        self.sid = sid.value
        self._created = True
        try:
            text = LPVOID()
            _check(advapi.ConvertSidToStringSidW(sid, C.byref(text)), 'APPCONTAINER_UNAVAILABLE')
            try: self.sid_string = C.wstring_at(text)
            finally: kernel.LocalFree(text)
            folder = LPVOID()
            if userenv.GetAppContainerFolderPath(self.sid_string, C.byref(folder)) < 0:
                raise WindowsProcessError('APPCONTAINER_UNAVAILABLE')
            try: self.folder = _plain_path(C.wstring_at(folder))
            finally: ole.CoTaskMemFree(folder)
            # An AppContainer normally receives writable LocalState/TempState.
            # Skill tests use only input/output pipes, so remove that disk-write
            # grant too; a Job Object has no filesystem-size quota.
            self.grant_read_tree(self.folder)
            return self
        except BaseException:
            self.close(); raise

    def grant_read_tree(self, root, *, executable=False):
        if self.sid is None: raise WindowsProcessError('APPCONTAINER_UNAVAILABLE')
        root = _plain_path(root)
        paths = [root, *root.rglob('*')]
        for path in paths:
            _container_acl(path, self.sid_string, executable=executable)

    def close(self):
        if not self._created: return
        _, advapi, userenv, _ = _api()
        try:
            for attempt in range(5):
                if userenv.DeleteAppContainerProfile(self.name) >= 0:
                    self._created = False
                    return
                time.sleep(.05 * (attempt + 1))
            raise WindowsProcessError('APPCONTAINER_CLEANUP_FAILED')
        finally:
            if self.sid is not None: advapi.FreeSid(self.sid)
            self.sid = None

    def __exit__(self, *_): self.close()
