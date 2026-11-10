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


class USEROBJECTFLAGS(C.Structure):
    _fields_ = [('inherit', native.BOOL), ('reserved', native.BOOL), ('flags', native.DWORD)]


class SECURITY_DESCRIPTOR(C.Structure):
    # Absolute SECURITY_DESCRIPTOR; pointers keep their owning token buffers
    # alive throughout AccessCheck. No descriptor is applied to a real object.
    _fields_ = [('revision', C.c_ubyte), ('reserved', C.c_ubyte), ('control', native.WORD),
                ('owner', native.LPVOID), ('group', native.LPVOID),
                ('sacl', native.LPVOID), ('dacl', native.LPVOID)]


def _descriptor_access(advapi, token, label, descriptor, mapping, requested):
    privilege_set = C.create_string_buffer(65536)
    length, granted, permitted = native.DWORD(len(privilege_set)), native.DWORD(), native.BOOL()
    if not advapi.AccessCheck(descriptor, token, 0x02000000, C.byref(mapping), privilege_set,
                            C.byref(length), C.byref(granted), C.byref(permitted)):
        return {'object': label, 'access_check_error': C.get_last_error()}
    mask = int(granted.value)
    return {'object': label, 'requested': requested, 'granted': mask,
            'allowed': bool(permitted.value) and mask & requested == requested,
            'missing_rights': requested & ~mask}


def _audit_user_objects(kernel, advapi, token):
    """Evaluate launcher UI objects; do not claim the child has connected.

    Documented mappings (window-station mappings differ by interactive state):
    https://learn.microsoft.com/windows/win32/winstation/window-station-security-and-access-rights
    https://learn.microsoft.com/windows/win32/winstation/desktop-security-and-access-rights
    Missing rights are a diagnostic inventory, not a minimum required by Python.
    """
    user = C.WinDLL('user32', use_last_error=True)
    user.GetProcessWindowStation.restype, user.GetProcessWindowStation.argtypes = native.HANDLE, []
    user.GetThreadDesktop.restype, user.GetThreadDesktop.argtypes = native.HANDLE, [native.DWORD]
    user.GetUserObjectSecurity.restype = native.BOOL
    user.GetUserObjectSecurity.argtypes = [native.HANDLE, C.POINTER(native.DWORD), native.LPVOID,
                                          native.DWORD, C.POINTER(native.DWORD)]
    user.GetUserObjectInformationW.restype = native.BOOL
    user.GetUserObjectInformationW.argtypes = [native.HANDLE, C.c_int, native.LPVOID,
                                              native.DWORD, C.POINTER(native.DWORD)]
    kernel.GetCurrentThreadId.restype, kernel.GetCurrentThreadId.argtypes = native.DWORD, []

    def check(label, handle, mapping):
        if not handle:
            return {'object': label, 'object_query_error': C.get_last_error()}
        information, required = native.DWORD(0x7), native.DWORD()
        user.GetUserObjectSecurity(handle, C.byref(information), None, 0, C.byref(required))
        if not 20 <= required.value <= 65536:
            return {'object': label, 'security_query_error': C.get_last_error() or 122}
        # ctypes owns an aligned native allocation suitable for self-relative SD.
        descriptor = C.create_string_buffer(required.value)
        if not user.GetUserObjectSecurity(handle, C.byref(information), descriptor,
                                         len(descriptor), C.byref(required)):
            return {'object': label, 'security_query_error': C.get_last_error()}
        return _descriptor_access(advapi, token, label, descriptor, mapping, int(mapping.GenericAll))

    rows = []
    station = user.GetProcessWindowStation()
    if not station:
        rows.append({'object': 'winstation', 'object_query_error': C.get_last_error()})
    else:
        flags, required = USEROBJECTFLAGS(), native.DWORD()
        if not user.GetUserObjectInformationW(station, 1, C.byref(flags), C.sizeof(flags), C.byref(required)):
            rows.append({'object': 'winstation', 'object_query_error': C.get_last_error()})
        else:
            # WSF_VISIBLE identifies the interactive window station. SYNCHRONIZE
            # is not supported for either window stations or desktops.
            mapping = (native.GENERIC_MAPPING(0x20303, 0x2001C, 0x20060, 0xF037F)
                       if flags.flags & 1 else
                       native.GENERIC_MAPPING(0x20103, 0x2000C, 0x20060, 0xF016F))
            rows.append(check('winstation', station, mapping))
    desktop = user.GetThreadDesktop(kernel.GetCurrentThreadId())
    rows.append(check('desktop', desktop, native.GENERIC_MAPPING(0x20041, 0x200BE, 0x20100, 0xF01FF)))
    # GetProcessWindowStation/GetThreadDesktop return borrowed handles. Closing
    # them would affect the launcher; this read-only audit must not close them.
    return rows


def _audit_default_dacl(advapi, primary, impersonation):
    """AccessCheck synthetic Event/Section descriptors built from TokenDefaultDacl.

    GetTokenInformation/InitializeSecurityDescriptor/SetSecurityDescriptor*
    operate only on owned in-memory buffers here; no token or OS ACL is changed.
    TokenDefaultDacl is a template: map generic ACE rights in a separate owned
    clone for each object type, as object creation would, before AccessCheck.
    https://learn.microsoft.com/windows/win32/secauthz/dacl-for-a-new-object
    """
    class ACL_SIZE_INFORMATION(C.Structure):
        _fields_ = [('AceCount', native.DWORD), ('AclBytesInUse', native.DWORD),
                    ('AclBytesFree', native.DWORD)]

    for name, args in (
        ('InitializeSecurityDescriptor', [native.LPVOID, native.DWORD]),
        ('SetSecurityDescriptorDacl', [native.LPVOID, native.BOOL, native.LPVOID, native.BOOL]),
        ('SetSecurityDescriptorOwner', [native.LPVOID, native.LPVOID, native.BOOL]),
        ('SetSecurityDescriptorGroup', [native.LPVOID, native.LPVOID, native.BOOL]),
        ('IsValidAcl', [native.LPVOID]),
        ('GetAclInformation', [native.LPVOID, native.LPVOID, native.DWORD, C.c_int]),
        ('GetAce', [native.LPVOID, native.DWORD, C.POINTER(native.LPVOID)]),
    ):
        function = getattr(advapi, name)
        function.restype, function.argtypes = native.BOOL, args
    advapi.MapGenericMask.restype = None
    advapi.MapGenericMask.argtypes = [C.POINTER(native.DWORD), C.POINTER(native.GENERIC_MAPPING)]

    buffers = []
    for kind in (4, 5, 6):  # TokenOwner, TokenPrimaryGroup, TokenDefaultDacl.
        required = native.DWORD()
        advapi.GetTokenInformation(primary, kind, None, 0, C.byref(required))
        if not C.sizeof(native.LPVOID) <= required.value <= 65536:
            return [{'object': 'token_default_dacl', 'security_query_error': C.get_last_error() or 122}]
        buffer = C.create_string_buffer(required.value)
        if not advapi.GetTokenInformation(primary, kind, buffer, len(buffer), C.byref(required)):
            return [{'object': 'token_default_dacl', 'security_query_error': C.get_last_error()}]
        buffers.append(buffer)
    owner, group, dacl = [C.cast(buffer, C.POINTER(native.LPVOID))[0] for buffer in buffers]
    if not dacl:
        return [{'object': 'token_default_dacl', 'diagnostic': 'unsupported_null_default_dacl'}]
    # The returned ACL must be wholly inside its owned GetTokenInformation
    # buffer before even reading its header or cloning bytes from its pointer.
    start, end = C.addressof(buffers[2]), C.addressof(buffers[2]) + len(buffers[2])
    if not start <= dacl <= end - 8:
        return [{'object': 'token_default_dacl', 'diagnostic': 'invalid_default_dacl_bounds'}]
    acl_size = C.c_uint16.from_address(dacl + 2).value
    if not 8 <= acl_size <= 65536 or dacl + acl_size > end:
        return [{'object': 'token_default_dacl', 'diagnostic': 'invalid_default_dacl_bounds'}]
    template = C.string_at(dacl, acl_size)

    def check(label, mapping, requested):
        # Never map masks in the token buffer, nor reuse the Event mapping for
        # the Section probe. The copied ACL remains alive through AccessCheck.
        acl = C.create_string_buffer(template, len(template))
        if not advapi.IsValidAcl(acl):
            return {'object': label, 'diagnostic': 'invalid_default_dacl'}
        information = ACL_SIZE_INFORMATION()
        if not advapi.GetAclInformation(acl, C.byref(information), C.sizeof(information), 2):
            return {'object': label, 'acl_query_error': C.get_last_error()}
        used = int(information.AclBytesInUse)
        if not 8 <= used <= len(acl) or information.AceCount > (used - 8) // 4:
            return {'object': label, 'diagnostic': 'invalid_default_dacl_bounds'}
        acl_start, acl_end = C.addressof(acl), C.addressof(acl) + used
        for index in range(information.AceCount):
            ace = native.LPVOID()
            if not advapi.GetAce(acl, index, C.byref(ace)):
                return {'object': label, 'acl_query_error': C.get_last_error()}
            address = ace.value
            if address is None or not acl_start + 8 <= address <= acl_end - 4:
                return {'object': label, 'diagnostic': 'invalid_default_dacl_bounds'}
            ace_type = C.c_ubyte.from_address(address).value
            ace_size = C.c_uint16.from_address(address + 2).value
            if ace_size < 4 or address + ace_size > acl_end:
                return {'object': label, 'diagnostic': 'invalid_default_dacl_bounds'}
            # Callback/object/audit ACEs require different layouts or semantics;
            # this diagnostic deliberately does not guess how to model them.
            if ace_type not in (0, 1):  # ACCESS_ALLOWED_ACE / ACCESS_DENIED_ACE.
                return {'object': label, 'diagnostic': 'unsupported_default_dacl_ace_type'}
            if ace_size < 16:  # ACE_HEADER + ACCESS_MASK + minimum SID header.
                return {'object': label, 'diagnostic': 'invalid_default_dacl_bounds'}
            mask = native.DWORD.from_address(address + 4)
            advapi.MapGenericMask(C.byref(mask), C.byref(mapping))
            if mask.value & 0xF0000000:
                return {'object': label, 'diagnostic': 'unmapped_default_dacl_generic_rights'}
        if not advapi.IsValidAcl(acl):
            return {'object': label, 'diagnostic': 'invalid_default_dacl'}
        descriptor = SECURITY_DESCRIPTOR()
        for operation in (
            lambda: advapi.InitializeSecurityDescriptor(C.byref(descriptor), 1),
            lambda: advapi.SetSecurityDescriptorOwner(C.byref(descriptor), owner, False),
            lambda: advapi.SetSecurityDescriptorGroup(C.byref(descriptor), group, False),
            lambda: advapi.SetSecurityDescriptorDacl(C.byref(descriptor), True, acl, False),
        ):
            if not operation():
                return {'object': label, 'descriptor_error': C.get_last_error()}
        return _descriptor_access(advapi, impersonation, label, C.byref(descriptor), mapping, requested)

    # Event probe asks only MODIFY_STATE|SYNCHRONIZE. Section probe asks only
    # MAP_READ|MAP_EXECUTE. These are synthetic access tests, not evidence of a
    # specific loader object or a request to add rights to the target token.
    return [
        check('default_dacl_event', native.GENERIC_MAPPING(0x20001, 0x20002, 0x120000, 0x1F0003), 0x100002),
        check('default_dacl_section', native.GENERIC_MAPPING(0x20005, 0x20002, 0x20008, 0xF001F), 0xC),
    ]


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
        user_rows = _audit_user_objects(kernel, advapi, impersonation)
        default_dacl_rows = _audit_default_dacl(advapi, token, impersonation)
        print(json.dumps({'native_loader_acl_audit': {'bypass_traverse_enabled': bool(traverse_enabled),
              'objects': rows,
              'user_object_scope': 'launcher_current_winstation_desktop',
              'user_objects': user_rows, 'synthetic_default_dacl_objects': default_dacl_rows}},
              ensure_ascii=True), flush=True)
    finally:
        if impersonation: kernel.CloseHandle(impersonation)
        kernel.CloseHandle(token)
