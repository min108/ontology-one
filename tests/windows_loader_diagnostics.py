"""Read-only native CI diagnostics for the fixed LPAC interpreter marker.

No process commands, environment values, ACL text, file contents or raw paths
are emitted. AccessCheck evaluates the actual suspended token, not an intended
creation flag. The caller remains its normal security context throughout.
"""
import ctypes as C
import json
from pathlib import Path

from backend import windows_process as native


class LUID(C.Structure):
    _fields_ = [('low', native.DWORD), ('high', C.c_int32)]


class LUID_AND_ATTRIBUTES(C.Structure):
    _fields_ = [('luid', LUID), ('attributes', native.DWORD)]


def audit_loader_access(process, runtime, work, system_root):
    kernel, advapi, _, _ = native._api()
    advapi.GetNamedSecurityInfoW.restype = native.DWORD
    advapi.GetNamedSecurityInfoW.argtypes = [C.c_wchar_p, C.c_int, native.DWORD,
        native.LPVOID, native.LPVOID, native.LPVOID, native.LPVOID, C.POINTER(native.LPVOID)]
    advapi.LookupPrivilegeValueW.restype = native.BOOL
    advapi.LookupPrivilegeValueW.argtypes = [C.c_wchar_p, C.c_wchar_p, C.POINTER(LUID)]
    token, impersonation = native.HANDLE(), native.HANDLE()
    native._check(advapi.OpenProcessToken(process, 0x8 | 0x2, C.byref(token)), operation='OpenProcessToken')
    try:
        native._check(advapi.DuplicateToken(token, 2, C.byref(impersonation)), operation='DuplicateToken')
        wanted, required = LUID(), native.DWORD()
        native._check(advapi.LookupPrivilegeValueW(None, 'SeChangeNotifyPrivilege', C.byref(wanted)))
        advapi.GetTokenInformation(token, 3, None, 0, C.byref(required))
        if not 4 <= required.value <= 65536: raise native.WindowsProcessError('PROCESS_QUERY_FAILED')
        buffer = C.create_string_buffer(required.value)
        native._check(advapi.GetTokenInformation(token, 3, buffer, required, C.byref(required)), operation='GetTokenInformation')
        count = C.cast(buffer, C.POINTER(native.DWORD))[0]
        if count > 128 or 4 + count * C.sizeof(LUID_AND_ATTRIBUTES) > len(buffer):
            raise native.WindowsProcessError('PROCESS_QUERY_FAILED')
        privileges = C.cast(C.byref(buffer, 4), C.POINTER(LUID_AND_ATTRIBUTES))
        traverse_enabled = any(privileges[index].luid.low == wanted.low and privileges[index].luid.high == wanted.high
            and privileges[index].attributes & 2 for index in range(count))
        mapping = native.GENERIC_MAPPING(0x120089, 0x120116, 0x1200A0, 0x1F01FF)

        def check(label, path, requested):
            descriptor = native.LPVOID()
            result = advapi.GetNamedSecurityInfoW(str(path), 1, 0x7, None, None, None, None, C.byref(descriptor))
            if result:
                return {'object': label, 'security_query_error': int(result)}
            try:
                privilege_set = C.create_string_buffer(1024)
                length, granted, permitted = native.DWORD(len(privilege_set)), native.DWORD(), native.BOOL()
                success = advapi.AccessCheck(descriptor, impersonation, 0x02000000, C.byref(mapping), privilege_set,
                    C.byref(length), C.byref(granted), C.byref(permitted))
                if not success:
                    return {'object': label, 'access_check_error': C.get_last_error()}
                mask = int(granted.value)
                essential = requested & 0x21  # read data and/or execute/traverse.
                return {'object': label, 'requested': requested, 'granted': mask,
                        'allowed': bool(permitted.value) and mask & requested == requested,
                        'data_or_traverse_allowed': bool(permitted.value) and mask & essential == essential,
                        'missing_rights': requested & ~mask}
            finally:
                kernel.LocalFree(descriptor)

        runtime, work, system = Path(runtime), Path(work), Path(system_root) / 'System32'
        objects = [('runtime_exe', runtime / 'python.exe'), ('runtime_python_dll', runtime / 'python312.dll'),
                   ('runtime_stable_abi_dll', runtime / 'python3.dll'), ('runtime_stdlib', runtime / 'python312.zip'),
                   ('runtime_vcruntime', runtime / 'vcruntime140.dll'), ('runtime_vcruntime_1', runtime / 'vcruntime140_1.dll'),
                   ('runtime_ucrtbase', runtime / 'ucrtbase.dll'),
                   ('system_ntdll', system / 'ntdll.dll'), ('system_kernel32', system / 'kernel32.dll'),
                   ('system_kernelbase', system / 'KernelBase.dll'), ('system_ucrtbase', system / 'ucrtbase.dll'),
                   ('system_advapi32', system / 'advapi32.dll'), ('system_bcrypt', system / 'bcrypt.dll'),
                   ('system_bcryptprimitives', system / 'bcryptPrimitives.dll'), ('system_sechost', system / 'sechost.dll'),
                   ('system_user32', system / 'user32.dll'), ('system_shell32', system / 'shell32.dll'),
                   ('system_ws2_32', system / 'ws2_32.dll'), ('system_version', system / 'version.dll'),
                   ('work_script', work / 'case.py')]
        rows = [check(label, path, 0x1200A9 if label not in {'runtime_stdlib', 'work_script'} else 0x120089)
                for label, path in objects]
        # Labels expose only relative depth, never the user's actual directory.
        for index, parent in enumerate((runtime, *runtime.parents)):
            rows.append(check('runtime_ancestor_' + str(index), parent, 0x20))
        rows.append(check('work_directory', work, 0x20))
        print(json.dumps({'native_loader_acl_audit': {'bypass_traverse_enabled': bool(traverse_enabled),
              'objects': rows}}, ensure_ascii=True), flush=True)
    finally:
        if impersonation: kernel.CloseHandle(impersonation)
        kernel.CloseHandle(token)
