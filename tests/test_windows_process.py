"""Win32 ABI and ownership ordering; real native containment has separate CI."""
import ctypes as C
import io
import os
from pathlib import Path
import sys
import threading
import types
import unittest
from unittest.mock import patch

from backend import windows_process as win


class Kernel:
    def __init__(self, failure=None):
        self.calls, self.failure, self.counter = [], failure, 200
        self.active = 0
        self.info = win._job_limits(win.ProcessLimits(memory_bytes=192 * 1024 * 1024, active_processes=1, cpu_time_seconds=5))

    def __getattr__(self, name):
        def call(*args):
            self.calls.append((name, args))
            if name == self.failure: return 0
            if name == 'InitializeProcThreadAttributeList':
                C.cast(args[3], C.POINTER(win.SIZE_T))[0] = 128
                return 1 if args[0] is not None else 0
            if name == 'CreateJobObjectW': return 100
            if name == 'CreateFileW':
                self.counter += 1; return self.counter
            if name == 'CreatePipe':
                C.cast(args[0], C.POINTER(win.HANDLE))[0] = self.counter + 1
                C.cast(args[1], C.POINTER(win.HANDLE))[0] = self.counter + 2
                self.counter += 2; return 1
            if name == 'CreateProcessW':
                value = C.cast(args[-1], C.POINTER(win.PROCESS_INFORMATION)).contents
                value.hProcess, value.hThread, value.dwProcessId = 101, 102, 103
            if name == 'WaitForSingleObject': return 0
            if name == 'GetExitCodeProcess': C.cast(args[1], C.POINTER(win.DWORD))[0] = 0
            if name == 'QueryInformationJobObject':
                if args[1] == 1:
                    C.cast(args[2], C.POINTER(win.JOBOBJECT_BASIC_ACCOUNTING_INFORMATION)).contents.ActiveProcesses = self.active
                elif args[1] == 9:
                    C.memmove(args[2], C.byref(self.info), C.sizeof(self.info))
                elif args[1] == 3:
                    header = C.cast(args[2], C.POINTER(win.DWORD)); header[0], header[1] = 2, 2
                    values = C.cast(C.byref(args[2], 8), C.POINTER(win.SIZE_T)); values[0], values[1] = 101, 102
            if name == 'IsProcessInJob': C.cast(args[2], C.POINTER(win.BOOL))[0] = True
            return 1
        return call


class WindowsProcessContractTests(unittest.TestCase):
    def test_windows_x64_abi_sizes_are_explicit_even_when_host_long_is_64bit(self):
        if C.sizeof(C.c_void_p) != 8: self.skipTest('x64 layout test')
        for kind, expected in [(win.SECURITY_ATTRIBUTES, 24), (win.STARTUPINFOW, 104), (win.STARTUPINFOEXW, 112),
                               (win.PROCESS_INFORMATION, 24), (win.SECURITY_CAPABILITIES, 24),
                               (win.JOBOBJECT_BASIC_LIMIT_INFORMATION, 64), (win.IO_COUNTERS, 48),
                               (win.JOBOBJECT_EXTENDED_LIMIT_INFORMATION, 144),
                               (win.JOBOBJECT_CPU_RATE_CONTROL_INFORMATION, 8)]:
            with self.subTest(kind=kind.__name__): self.assertEqual(C.sizeof(kind), expected)

    def test_limits_are_kernel_aggregate_limits_without_breakaway(self):
        info = win._job_limits(win.ProcessLimits(memory_bytes=192 * 1024 * 1024, active_processes=1, cpu_time_seconds=5))
        self.assertEqual(info.BasicLimitInformation.LimitFlags, 0x2000 | 0x200 | 0x8 | 0x4)
        self.assertEqual(info.JobMemoryLimit, 192 * 1024 * 1024)
        self.assertEqual(info.BasicLimitInformation.PerJobUserTimeLimit, 50_000_000)
        for values in ({'memory_bytes': 2**80}, {'cpu_time_seconds': 2**80}, {'active_processes': True}, {'cpu_rate_percent': 101}):
            with self.assertRaises(ValueError): win.ProcessLimits(**values)

    def test_environment_requires_explicit_nonambiguous_bounded_strings(self):
        self.assertEqual(win._environment_block({'Z': 'value', 'A': '内容'}), 'A=内容\0Z=value\0\0')
        for environment in ({'Path': 'a', 'PATH': 'b'}, {'A=B': 'c'}, {'A': '\0'}):
            with self.assertRaises(ValueError): win._environment_block(environment)

    def spawn(self, kernel, **kwargs):
        with patch.object(win, '_api', return_value=(kernel, None, None, None)), \
             patch.object(win, 'assert_network_isolation_services'), patch.dict(sys.modules, {'msvcrt': types.SimpleNamespace()}):
            return win.spawn_job_process([str(Path(sys.executable).absolute()), '-I'], cwd=str(Path(__file__).absolute().parent), env={},
                stdin=win.DEVNULL, stdout=win.DEVNULL, stderr=win.DEVNULL, **kwargs)

    def test_network_isolation_requires_both_running_services_and_query_permission(self):
        states, closed = {'BFE': 4, 'MpsSvc': 4}, []
        def status(service, _, buffer, _length, _returned):
            C.cast(buffer, C.POINTER(win.SERVICE_STATUS_PROCESS)).contents.dwCurrentState = states[service]
            return True
        api = types.SimpleNamespace(OpenSCManagerW=lambda *_: 100, OpenServiceW=lambda _, name, __: name,
            QueryServiceStatusEx=status, CloseServiceHandle=closed.append)
        with patch.object(win, '_api', return_value=(None, api, None, None)):
            win.assert_network_isolation_services()
            self.assertEqual(closed, ['BFE', 'MpsSvc', 100])
            for name, state in (('BFE', 1), ('MpsSvc', 2)):
                states.update(BFE=4, MpsSvc=4); states[name] = state
                with self.assertRaises(win.WindowsProcessError) as caught: win.assert_network_isolation_services()
                self.assertEqual(caught.exception.code, 'NETWORK_ISOLATION_UNAVAILABLE')
            api.OpenSCManagerW = lambda *_: None
            with self.assertRaises(win.WindowsProcessError): win.assert_network_isolation_services()

    def test_stopped_filtering_service_prevents_process_creation(self):
        kernel = Kernel()
        with patch.object(win, '_api', return_value=(kernel, None, None, None)), \
             patch.object(win, 'assert_network_isolation_services', side_effect=win.WindowsProcessError('NETWORK_ISOLATION_UNAVAILABLE')):
            with self.assertRaises(win.WindowsProcessError):
                win.spawn_job_process([str(Path(sys.executable).absolute())], cwd=str(Path.cwd()), env={},
                    appcontainer=win.AppContainerProfile())
        self.assertFalse(kernel.calls)

    def test_suspended_child_is_assigned_before_resume_with_only_stdio_inherited(self):
        kernel = Kernel(); process = self.spawn(kernel)
        names = [name for name, _ in kernel.calls]
        self.assertLess(names.index('AssignProcessToJobObject'), names.index('ResumeThread'))
        creation = next(args for name, args in kernel.calls if name == 'CreateProcessW')
        self.assertTrue(creation[5] & 0x4)
        handle_list = next(args for name, args in kernel.calls if name == 'UpdateProcThreadAttribute' and args[2] == 0x20002)
        self.assertEqual(set(handle_list[3]), {201, 202, 203})
        self.assertNotIn(100, handle_list[3])
        process.close()

    def test_job_assignment_failure_never_resumes_and_closes_every_owned_handle(self):
        kernel = Kernel('AssignProcessToJobObject')
        with self.assertRaises(win.WindowsProcessError): self.spawn(kernel)
        names = [name for name, _ in kernel.calls]
        self.assertNotIn('ResumeThread', names)
        self.assertIn('TerminateProcess', names); self.assertIn('TerminateJobObject', names)
        closed = {args[0] for name, args in kernel.calls if name == 'CloseHandle'}
        self.assertEqual(closed, {100, 101, 102, 201, 202, 203})

    def test_uninitialized_attribute_list_is_not_deleted(self):
        kernel = Kernel()
        def denied(_attrs, _count, _flags, size):
            C.cast(size, C.POINTER(win.SIZE_T))[0] = 128
            return 0
        with patch.object(kernel, 'InitializeProcThreadAttributeList', side_effect=denied):
            with self.assertRaises(win.WindowsProcessError): self.spawn(kernel)
        self.assertNotIn('DeleteProcThreadAttributeList', [name for name, _ in kernel.calls])

    def test_fdopen_failure_closes_transferred_crt_descriptor(self):
        kernel = Kernel()
        descriptor = os.open(os.devnull, os.O_RDONLY)
        crt = types.SimpleNamespace(open_osfhandle=lambda *_: descriptor)
        with patch.object(win, '_api', return_value=(kernel, None, None, None)), \
             patch.dict(sys.modules, {'msvcrt': crt}), patch.object(win.os, 'O_BINARY', 0, create=True), \
             patch.object(win.os, 'fdopen', side_effect=OSError('synthetic failure')):
            with self.assertRaises(OSError):
                win.spawn_job_process([str(Path(sys.executable).absolute())], cwd=str(Path(__file__).absolute().parent),
                    env={}, stdin=win.DEVNULL, stdout=win.PIPE, stderr=win.DEVNULL)
        with self.assertRaises(OSError): os.fstat(descriptor)
        self.assertNotIn('ResumeThread', [name for name, _ in kernel.calls])

    def test_appcontainer_has_lpac_and_no_capabilities_before_resume(self):
        kernel = Kernel(); profile = win.AppContainerProfile(); profile.sid = 444
        with patch.object(win, '_verify_appcontainer_token') as verify:
            process = self.spawn(kernel, appcontainer=profile)
        verify.assert_called_once_with(101, 444)
        updates = {args[2]: args for name, args in kernel.calls if name == 'UpdateProcThreadAttribute'}
        capabilities = C.cast(updates[0x20009][3], C.POINTER(win.SECURITY_CAPABILITIES)).contents
        self.assertEqual(capabilities.CapabilityCount, 0)
        self.assertEqual(C.cast(updates[0x2000F][3], C.POINTER(win.DWORD))[0], 1)
        process.close()

    def test_current_job_limits_are_queried_from_kernel_not_environment(self):
        kernel = Kernel()
        with patch.object(win, '_api', return_value=(kernel, None, None, None)):
            observed = win.assert_job_limits(max_memory_bytes=192 * 1024 * 1024, max_processes=1, max_cpu_time_seconds=5)
            self.assertTrue(observed['kill_on_close'])
            kernel.info.BasicLimitInformation.LimitFlags |= 0x800
            with self.assertRaises(win.WindowsProcessError):
                win.assert_job_limits(max_memory_bytes=192 * 1024 * 1024, max_processes=1, max_cpu_time_seconds=5)

    def test_output_limit_terminates_job_and_reaps_pipe_threads(self):
        kernel = Kernel()
        process = win.WindowsProcess(kernel, 100, 101, 103, (io.BytesIO(), io.BytesIO(b'x' * 100), io.BytesIO()))
        with self.assertRaises(win.WindowsProcessError) as caught:
            process.communicate(max_stdout=10)
        self.assertEqual(caught.exception.code, 'OUTPUT_LIMIT')
        self.assertTrue(any(name == 'TerminateJobObject' for name, _ in kernel.calls))
        process.close()

    def test_closed_unreaped_process_errors_instead_of_waiting_forever(self):
        kernel = Kernel('TerminateJobObject')
        process = win.WindowsProcess(kernel, 100, 101, 103, (None, None, None))
        with patch.object(kernel, 'WaitForSingleObject', return_value=258):
            with self.assertRaises(win.WindowsProcessError): process.close()
        self.assertEqual([args[0] for name, args in kernel.calls if name == 'CloseHandle'], [100, 101])
        with self.assertRaises(win.WindowsProcessError) as caught: process.wait()
        self.assertEqual(caught.exception.code, 'PROCESS_CLOSED')

    def test_job_query_failure_still_joins_all_owned_pipe_threads(self):
        kernel = Kernel('QueryInformationJobObject')
        process = win.WindowsProcess(kernel, 100, 101, 103, (io.BytesIO(), io.BytesIO(b'{}'), io.BytesIO()))
        joined = []
        original = threading.Thread.join
        def join(thread, *args, **kwargs):
            joined.append(thread.name); return original(thread, *args, **kwargs)
        with patch.object(threading.Thread, 'join', join):
            with self.assertRaises(win.WindowsProcessError): process.communicate()
        self.assertEqual(set(joined), {'windows-stdin', 'windows-stdout', 'windows-stderr'})
        with self.assertRaises(win.WindowsProcessError): process.close()

    def test_process_ids_query_only_owned_job(self):
        kernel = Kernel(); process = win.WindowsProcess(kernel, 100, 101, 103, (None, None, None))
        self.assertEqual(process.process_ids(), (101, 102))
        query = next(args for name, args in kernel.calls if name == 'QueryInformationJobObject')
        self.assertEqual(query[:2], (100, 3)); process.close()

    @unittest.skipIf(os.name == 'nt', 'non-Windows refusal')
    def test_native_operations_refuse_non_windows_without_fallback(self):
        with self.assertRaises(win.WindowsProcessError) as caught: win._api()
        self.assertEqual(caught.exception.code, 'WINDOWS_X64_REQUIRED')


if __name__ == '__main__': unittest.main()
