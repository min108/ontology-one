"""Handle-based local file access and locks for Unix and native Windows.

Windows directory ancestors stay open without WRITE/DELETE sharing for the
entire operation. Reparse points are opened as objects and rejected, rather than
resolved. This prevents both renaming an ancestor and changing its reparse tag
between validation and a child operation. Storage must be on a local volume.
"""
from __future__ import annotations

from contextlib import contextmanager
import errno
import os
from pathlib import Path
import re
import stat
import uuid

WINDOWS = os.name == 'nt'


def _absolute(path):
    value = Path(path).absolute()
    if '\x00' in str(value) or any(part in {'.', '..'} for part in value.parts):
        raise PermissionError(errno.EACCES, 'Unsafe filesystem path')
    if WINDOWS:
        if not re.fullmatch(r'[A-Za-z]:', value.drive) or not value.root:
            raise PermissionError(errno.EACCES, 'A local drive path is required')
        for part in value.parts[1:]:
            if (not part or part[-1:] in {'.', ' '} or any(c in part for c in '<>:"/\\|?*')
                    or any(ord(c) < 32 for c in part)
                    or re.fullmatch(r'(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?', part, re.I)):
                raise PermissionError(errno.EACCES, 'Unsafe Windows path component')
    return value


def reject_reparse_points(path):
    """Preflight only; every actual operation separately pins its directories."""
    value = _absolute(path)
    for current in (*reversed(value.parents), value):
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            break
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise PermissionError(errno.EACCES, 'Reparse points and symbolic links are not allowed')


def _mode(mode):
    if (not isinstance(mode, str) or not mode or mode[0] not in 'rwax'
            or any(c not in 'rwaxbt+' for c in mode) or sum(mode.count(c) for c in 'rwax') != 1
            or mode.count('+') > 1 or mode.count('b') + mode.count('t') > 1):
        raise ValueError('Unsupported file mode')
    return mode[0], '+' in mode, mode[0] in 'wax' or '+' in mode


if WINDOWS:
    import ctypes
    from ctypes import wintypes
    import msvcrt

    _kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    _advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    _ntdll = ctypes.WinDLL('ntdll')
    _INVALID = ctypes.c_void_p(-1).value
    _LIST_DIRECTORY = 0x1
    _READ_ATTRIBUTES, _READ_CONTROL, _WRITE_DAC, _DELETE = 0x80, 0x20000, 0x40000, 0x10000
    _GENERIC_READ, _GENERIC_WRITE = 0x80000000, 0x40000000
    _OPEN_REPARSE, _BACKUP_SEMANTICS = 0x00200000, 0x02000000
    _REPARSE, _DIRECTORY = 0x400, 0x10
    _OPEN_EXISTING, _OPEN_ALWAYS, _CREATE_NEW = 3, 4, 1

    class _SecurityAttributes(ctypes.Structure):
        _fields_ = [('nLength', wintypes.DWORD), ('lpSecurityDescriptor', wintypes.LPVOID),
                    ('bInheritHandle', wintypes.BOOL)]

    class _FileInfo(ctypes.Structure):
        _fields_ = [('attributes', wintypes.DWORD), ('created', wintypes.FILETIME),
                    ('accessed', wintypes.FILETIME), ('written', wintypes.FILETIME),
                    ('volume', wintypes.DWORD), ('size_high', wintypes.DWORD),
                    ('size_low', wintypes.DWORD), ('links', wintypes.DWORD),
                    ('index_high', wintypes.DWORD), ('index_low', wintypes.DWORD)]

    class _Overlapped(ctypes.Structure):
        _fields_ = [('Internal', ctypes.c_size_t), ('InternalHigh', ctypes.c_size_t),
                    ('Offset', wintypes.DWORD), ('OffsetHigh', wintypes.DWORD), ('hEvent', wintypes.HANDLE)]

    class _RenameInfo(ctypes.Structure):
        _fields_ = [('ReplaceIfExists', ctypes.c_ubyte), ('RootDirectory', wintypes.HANDLE),
                    ('FileNameLength', wintypes.DWORD), ('FileName', wintypes.WCHAR * 1)]

    class _UnicodeString(ctypes.Structure):
        _fields_ = [('Length', wintypes.USHORT), ('MaximumLength', wintypes.USHORT), ('Buffer', wintypes.LPWSTR)]

    class _ObjectAttributes(ctypes.Structure):
        _fields_ = [('Length', wintypes.ULONG), ('RootDirectory', wintypes.HANDLE),
                    ('ObjectName', ctypes.POINTER(_UnicodeString)), ('Attributes', wintypes.ULONG),
                    ('SecurityDescriptor', wintypes.LPVOID), ('SecurityQualityOfService', wintypes.LPVOID)]

    class _IoStatus(ctypes.Structure):
        _fields_ = [('StatusOrPointer', ctypes.c_size_t), ('Information', ctypes.c_size_t)]

    class _AclSize(ctypes.Structure):
        _fields_ = [('AceCount', wintypes.DWORD), ('AclBytesInUse', wintypes.DWORD),
                    ('AclBytesFree', wintypes.DWORD)]

    class _AceHeader(ctypes.Structure):
        _fields_ = [('AceType', ctypes.c_ubyte), ('AceFlags', ctypes.c_ubyte), ('AceSize', wintypes.WORD)]

    class _AllowedAce(ctypes.Structure):
        _fields_ = [('Header', _AceHeader), ('Mask', wintypes.DWORD), ('SidStart', wintypes.DWORD)]

    def _declare(dll, name, result, *arguments):
        function = getattr(dll, name)
        function.restype, function.argtypes = result, list(arguments)
        return function

    _CreateFile = _declare(_kernel, 'CreateFileW', wintypes.HANDLE, wintypes.LPCWSTR, wintypes.DWORD,
                           wintypes.DWORD, ctypes.POINTER(_SecurityAttributes), wintypes.DWORD,
                           wintypes.DWORD, wintypes.HANDLE)
    _CreateDirectory = _declare(_kernel, 'CreateDirectoryW', wintypes.BOOL, wintypes.LPCWSTR,
                                ctypes.POINTER(_SecurityAttributes))
    _Close = _declare(_kernel, 'CloseHandle', wintypes.BOOL, wintypes.HANDLE)
    _Info = _declare(_kernel, 'GetFileInformationByHandle', wintypes.BOOL, wintypes.HANDLE,
                     ctypes.POINTER(_FileInfo))
    _SetInfo = _declare(_kernel, 'SetFileInformationByHandle', wintypes.BOOL, wintypes.HANDLE,
                        ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
    _Lock = _declare(_kernel, 'LockFileEx', wintypes.BOOL, wintypes.HANDLE, wintypes.DWORD,
                     wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(_Overlapped))
    _Unlock = _declare(_kernel, 'UnlockFileEx', wintypes.BOOL, wintypes.HANDLE, wintypes.DWORD,
                       wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(_Overlapped))
    _Free = _declare(_kernel, 'LocalFree', wintypes.HLOCAL, wintypes.HLOCAL)
    _CurrentProcess = _declare(_kernel, 'GetCurrentProcess', wintypes.HANDLE)
    _DriveType = _declare(_kernel, 'GetDriveTypeW', wintypes.UINT, wintypes.LPCWSTR)
    _VolumeInfo = _declare(_kernel, 'GetVolumeInformationW', wintypes.BOOL, wintypes.LPCWSTR,
                           wintypes.LPWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                           ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
                           wintypes.LPWSTR, wintypes.DWORD)
    _NtCreateFile = _declare(_ntdll, 'NtCreateFile', wintypes.LONG, ctypes.POINTER(wintypes.HANDLE),
                             wintypes.DWORD, ctypes.POINTER(_ObjectAttributes), ctypes.POINTER(_IoStatus),
                             wintypes.LPVOID, wintypes.ULONG, wintypes.ULONG, wintypes.ULONG,
                             wintypes.ULONG, wintypes.LPVOID, wintypes.ULONG)
    _NtSetInfo = _declare(_ntdll, 'NtSetInformationFile', wintypes.LONG, wintypes.HANDLE,
                          ctypes.POINTER(_IoStatus), wintypes.LPVOID, wintypes.ULONG, ctypes.c_int)
    _NtError = _declare(_ntdll, 'RtlNtStatusToDosError', wintypes.ULONG, wintypes.LONG)
    _OpenToken = _declare(_advapi, 'OpenProcessToken', wintypes.BOOL, wintypes.HANDLE,
                          wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE))
    _TokenInfo = _declare(_advapi, 'GetTokenInformation', wintypes.BOOL, wintypes.HANDLE, ctypes.c_int,
                          wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))
    _SidText = _declare(_advapi, 'ConvertSidToStringSidW', wintypes.BOOL, wintypes.LPVOID,
                        ctypes.POINTER(wintypes.LPWSTR))
    _Descriptor = _declare(_advapi, 'ConvertStringSecurityDescriptorToSecurityDescriptorW', wintypes.BOOL,
                           wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.LPVOID),
                           ctypes.POINTER(wintypes.DWORD))
    _DescriptorDacl = _declare(_advapi, 'GetSecurityDescriptorDacl', wintypes.BOOL, wintypes.LPVOID,
                               ctypes.POINTER(wintypes.BOOL), ctypes.POINTER(wintypes.LPVOID),
                               ctypes.POINTER(wintypes.BOOL))
    _SetSecurity = _declare(_advapi, 'SetSecurityInfo', wintypes.DWORD, wintypes.HANDLE, ctypes.c_int,
                            wintypes.DWORD, wintypes.LPVOID, wintypes.LPVOID, wintypes.LPVOID, wintypes.LPVOID)
    _GetSecurity = _declare(_advapi, 'GetSecurityInfo', wintypes.DWORD, wintypes.HANDLE, ctypes.c_int,
                            wintypes.DWORD, ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.LPVOID),
                            ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.LPVOID),
                            ctypes.POINTER(wintypes.LPVOID))
    _DescriptorControl = _declare(_advapi, 'GetSecurityDescriptorControl', wintypes.BOOL, wintypes.LPVOID,
                                  ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.DWORD))
    _AclInfo = _declare(_advapi, 'GetAclInformation', wintypes.BOOL, wintypes.LPVOID, wintypes.LPVOID,
                        wintypes.DWORD, ctypes.c_int)
    _GetAce = _declare(_advapi, 'GetAce', wintypes.BOOL, wintypes.LPVOID, wintypes.DWORD,
                       ctypes.POINTER(wintypes.LPVOID))

    def _win_error(code=None, *, operation=None):
        code = ctypes.get_last_error() if code is None else code
        if code in (80, 183):
            raise FileExistsError(errno.EEXIST, 'File already exists')
        if code in (2, 3):
            raise FileNotFoundError(errno.ENOENT, 'File does not exist')
        if code in (5, 32):
            # Fixed operation names and numeric codes distinguish an access-mask
            # error from sharing contention without exposing a path or content.
            detail = f'File access or sharing was denied (winerror={code}'
            if operation:
                detail += f', operation={operation}'
            error = PermissionError(errno.EACCES, detail + ')')
            error.winerror = code
            raise error
        raise ctypes.WinError(code)

    def _sid_string(sid):
        text = wintypes.LPWSTR()
        if not _SidText(sid, ctypes.byref(text)):
            _win_error()
        try:
            return text.value
        finally:
            _Free(ctypes.cast(text, wintypes.HLOCAL))

    def current_user_sid():
        token = wintypes.HANDLE()
        if not _OpenToken(_CurrentProcess(), 0x8, ctypes.byref(token)):
            _win_error()
        try:
            size = wintypes.DWORD()
            _TokenInfo(token, 1, None, 0, ctypes.byref(size))
            if not 0 < size.value < 65536:
                _win_error()
            buffer = ctypes.create_string_buffer(size.value)
            if not _TokenInfo(token, 1, buffer, size, ctypes.byref(size)):
                _win_error()
            # TOKEN_USER begins with SID_AND_ATTRIBUTES; its first field is PSID.
            return _sid_string(ctypes.cast(buffer, ctypes.POINTER(wintypes.LPVOID))[0])
        finally:
            _Close(token)

    @contextmanager
    def _private_descriptor(directory):
        inheritance = 'OICI' if directory else ''
        sid = current_user_sid()
        sddl = f'O:{sid}D:P(A;{inheritance};FA;;;{sid})(A;{inheritance};FA;;;SY)'
        descriptor = wintypes.LPVOID()
        if not _Descriptor(sddl, 1, ctypes.byref(descriptor), None):
            _win_error()
        try:
            yield descriptor
        finally:
            _Free(descriptor)

    def _set_private(handle, directory):
        _assert_owner(handle)
        with _private_descriptor(directory) as descriptor:
            present, defaulted, acl = wintypes.BOOL(), wintypes.BOOL(), wintypes.LPVOID()
            if not _DescriptorDacl(descriptor, ctypes.byref(present), ctypes.byref(acl), ctypes.byref(defaulted)) or not present:
                _win_error()
            error = _SetSecurity(handle, 1, 0x4 | 0x80000000, None, None, acl, None)
            if error:
                _win_error(error)

    def _assert_owner(handle):
        owner, descriptor = wintypes.LPVOID(), wintypes.LPVOID()
        error = _GetSecurity(handle, 1, 0x1, ctypes.byref(owner), None, None, None, ctypes.byref(descriptor))
        if error:
            _win_error(error)
        try:
            if not owner or _sid_string(owner) != current_user_sid():
                raise PermissionError(errno.EACCES, 'Private storage must be owned by the current user')
        finally:
            _Free(descriptor)

    def _assert_private_handle(handle):
        owner, acl, descriptor = wintypes.LPVOID(), wintypes.LPVOID(), wintypes.LPVOID()
        error = _GetSecurity(handle, 1, 0x1 | 0x4, ctypes.byref(owner), None, ctypes.byref(acl), None, ctypes.byref(descriptor))
        if error:
            _win_error(error)
        try:
            current = current_user_sid()
            if not owner or _sid_string(owner) != current:
                raise PermissionError(errno.EACCES, 'Private storage must be owned by the current user')
            control, revision = wintypes.WORD(), wintypes.DWORD()
            if not _DescriptorControl(descriptor, ctypes.byref(control), ctypes.byref(revision)):
                _win_error()
            if not acl or not control.value & 0x1000:
                raise PermissionError(errno.EACCES, 'Private protected DACL is required')
            size = _AclSize()
            if not _AclInfo(acl, ctypes.byref(size), ctypes.sizeof(size), 2):
                _win_error()
            allowed = {current, 'S-1-5-18'}
            found = set()
            for index in range(size.AceCount):
                address = wintypes.LPVOID()
                if not _GetAce(acl, index, ctypes.byref(address)):
                    _win_error()
                header = ctypes.cast(address, ctypes.POINTER(_AceHeader)).contents
                if header.AceType != 0 or header.AceSize < _AllowedAce.SidStart.offset + 8:
                    raise PermissionError(errno.EACCES, 'Unsupported private ACL entry')
                sid = _sid_string(address.value + _AllowedAce.SidStart.offset)
                if sid not in allowed:
                    raise PermissionError(errno.EACCES, 'File is accessible to an unexpected identity')
                found.add(sid)
            if found != allowed:
                raise PermissionError(errno.EACCES, 'Current user and SYSTEM private ACL is required')
        finally:
            _Free(descriptor)

    def _extended(path):
        return '\\\\?\\' + str(path)

    def _volume(root):
        if _DriveType(str(root)) not in (2, 3, 6):
            raise PermissionError(errno.EACCES, 'Private storage requires a local ACL-capable volume')
        flags = wintypes.DWORD()
        if not _VolumeInfo(_extended(root), None, 0, None, None, ctypes.byref(flags), None, 0):
            _win_error()
        if not flags.value & 0x8:  # FILE_PERSISTENT_ACLS
            raise PermissionError(errno.EACCES, 'The volume cannot enforce private DACLs')

    def _relative_handle(parent, name, access, sharing, disposition, directory, descriptor):
        encoded = name.encode('utf-16-le')
        if not encoded or len(encoded) > 65532 or any(c in name for c in '/\\:'):
            raise PermissionError(errno.EACCES, 'Invalid relative object name')
        buffer = ctypes.create_string_buffer(encoded + b'\0\0')
        text = _UnicodeString(len(encoded), len(encoded) + 2, ctypes.cast(buffer, wintypes.LPWSTR))
        attributes = _ObjectAttributes(ctypes.sizeof(_ObjectAttributes), parent, ctypes.pointer(text),
                                       0x40, descriptor, None)  # OBJ_CASE_INSENSITIVE
        status, handle = _IoStatus(), wintypes.HANDLE()
        options = 0x20 | 0x00200000 | (1 if directory else 0x40)  # synchronous, open reparse object
        create = {_OPEN_EXISTING: 1, _CREATE_NEW: 2, _OPEN_ALWAYS: 3}[disposition]
        result = _NtCreateFile(ctypes.byref(handle), access | 0x100000, ctypes.byref(attributes),
                               ctypes.byref(status), None, 0x10 if directory else 0x80,
                               sharing, create, options, None, 0)
        if result < 0:
            _win_error(_NtError(result), operation='NtCreateFile')
        return handle.value

    def _open_handle(path, access, disposition=_OPEN_EXISTING, *, directory=False, private=False, delete_share=False, parent=None):
        # GetFileInformationByHandle requires FILE_READ_ATTRIBUTES even for a
        # write-only handle. GENERIC_WRITE does not include it on Windows.
        # This grants metadata inspection only, never FILE_READ_DATA.
        access |= _READ_ATTRIBUTES
        if directory:
            # Metadata-only directory handles do not participate in Windows
            # read/write sharing accounting. FILE_LIST_DIRECTORY supplies the
            # minimum read access that makes our no-WRITE/no-DELETE sharing
            # exclude pre-existing and subsequent reparse-capable write handles.
            # No directory contents are enumerated by this helper.
            access |= _LIST_DIRECTORY
        # WRITE sharing on a directory would permit FSCTL_SET_REPARSE_POINT.
        sharing = 1 if directory else 1 | 2 | (4 if delete_share else 0)
        flags = _OPEN_REPARSE | _BACKUP_SEMANTICS
        def create(descriptor=None):
            if parent is not None:
                return _relative_handle(parent, Path(path).name, access, sharing, disposition, directory, descriptor)
            attributes = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), descriptor, False)
            handle = _CreateFile(_extended(path), access, sharing, ctypes.byref(attributes) if descriptor else None,
                                 disposition, flags, None)
            if handle == _INVALID:
                _win_error(operation='CreateFileW')
            return handle
        if private:
            with _private_descriptor(directory) as descriptor:
                handle = create(descriptor)
        else:
            handle = create()
        try:
            info = _FileInfo()
            if not _Info(handle, ctypes.byref(info)):
                _win_error(operation='GetFileInformationByHandle')
            if info.attributes & _REPARSE:
                raise PermissionError(errno.EACCES, 'Reparse points are not allowed')
            if bool(info.attributes & _DIRECTORY) != directory:
                raise PermissionError(errno.EACCES, 'Unexpected file object type')
            if not directory and info.links > 1:
                raise PermissionError(errno.EACCES, 'Hard-linked files are not allowed')
            return handle
        except BaseException:
            _Close(handle)
            raise

    @contextmanager
    def _directory(path, *, create=False, private=False):
        value, handles = _absolute(path), []
        current = Path(value.anchor)
        try:
            handles.append(_open_handle(current, _READ_ATTRIBUTES, directory=True))
            _volume(current)
            for index, part in enumerate(value.parts[1:]):
                current = current / part
                leaf = index == len(value.parts) - 2
                access = _READ_ATTRIBUTES | (_READ_CONTROL | _WRITE_DAC if private and leaf else 0)
                handles.append(_open_handle(current, access, _OPEN_ALWAYS if create else _OPEN_EXISTING,
                                            directory=True, private=create, parent=handles[-1]))
            if private:
                if current == Path(value.anchor):
                    raise PermissionError(errno.EACCES, 'A volume root cannot be made private')
                _set_private(handles[-1], True)
            yield handles[-1]
        finally:
            for handle in reversed(handles):
                _Close(handle)

    def _rename(handle, parent, target, overwrite):
        encoded = target.encode('utf-16-le')
        offset = _RenameInfo.FileName.offset
        data = ctypes.create_string_buffer(max(ctypes.sizeof(_RenameInfo), offset + len(encoded)))
        info = ctypes.cast(data, ctypes.POINTER(_RenameInfo)).contents
        info.ReplaceIfExists, info.RootDirectory, info.FileNameLength = bool(overwrite), parent, len(encoded)
        ctypes.memmove(ctypes.addressof(data) + offset, encoded, len(encoded))
        status = _IoStatus()
        result = _NtSetInfo(handle, ctypes.byref(status), data, len(data), 10)  # FileRenameInformation
        if result < 0:
            _win_error(_NtError(result))

    def _delete_on_close(handle):
        remove = wintypes.BOOL(True)
        if not _SetInfo(handle, 4, ctypes.byref(remove), ctypes.sizeof(remove)):
            _win_error()

else:
    def current_user_sid():
        raise NotImplementedError('Windows identities are available only on Windows')

    @contextmanager
    def _directory(path, *, create=False, private=False):
        value, descriptors = _absolute(path), []
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, 'O_CLOEXEC', 0)
        try:
            descriptors.append(os.open(value.anchor, flags))
            for part in value.parts[1:]:
                if create:
                    try:
                        os.mkdir(part, 0o700, dir_fd=descriptors[-1])
                    except FileExistsError:
                        pass
                descriptors.append(os.open(part, flags, dir_fd=descriptors[-1]))
            if private:
                if len(descriptors) == 1:
                    raise PermissionError(errno.EACCES, 'A filesystem root cannot be made private')
                os.fchmod(descriptors[-1], 0o700)
            yield descriptors[-1]
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)


@contextmanager
def file_lock(stream_or_fd, *, shared=False, blocking=True):
    """Lock byte zero on Windows, the file on Unix; contention raises BlockingIOError."""
    descriptor = stream_or_fd if isinstance(stream_or_fd, int) else stream_or_fd.fileno()
    if WINDOWS:
        handle, overlapped = msvcrt.get_osfhandle(descriptor), _Overlapped()
        if not _Lock(handle, (0 if shared else 2) | (0 if blocking else 1), 0, 1, 0, ctypes.byref(overlapped)):
            error = ctypes.get_last_error()
            if error in (33, 158):
                raise BlockingIOError(errno.EAGAIN, 'File lock is held by another process')
            _win_error(error)
        try:
            yield
        finally:
            if not _Unlock(handle, 0, 1, 0, ctypes.byref(overlapped)):
                _win_error()
    else:
        import fcntl
        fcntl.flock(descriptor, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | (0 if blocking else fcntl.LOCK_NB))
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)


def ensure_private_directory(path):
    value = _absolute(path)
    with _directory(value, create=True, private=True):
        pass
    return value


@contextmanager
def secure_open(path, mode='rb', *, create_parents=False, require_private=False):
    value = _absolute(path)
    operation, update, writing = _mode(mode)
    with _directory(value.parent, create=create_parents) as parent:
        if WINDOWS:
            access = (_GENERIC_READ if operation == 'r' or update else 0) | (_GENERIC_WRITE if writing else 0)
            if writing:
                access |= _WRITE_DAC | _READ_CONTROL
            handle = _open_handle(value, access, _CREATE_NEW if operation == 'x' else _OPEN_ALWAYS if operation in 'wa' else _OPEN_EXISTING,
                                  private=writing, delete_share=not writing, parent=parent)
            try:
                if writing:
                    _set_private(handle, False)
                if require_private:
                    _assert_private_handle(handle)
                flags = os.O_BINARY | (os.O_RDWR if update else os.O_WRONLY if writing else os.O_RDONLY)
                if operation == 'a':
                    flags |= os.O_APPEND
                descriptor = msvcrt.open_osfhandle(handle, flags)
            except BaseException:
                _Close(handle)
                raise
            if operation == 'w':
                try:
                    os.ftruncate(descriptor, 0)
                except BaseException:
                    os.close(descriptor)
                    raise
        else:
            flags = os.O_NOFOLLOW | getattr(os, 'O_CLOEXEC', 0)
            flags |= os.O_RDWR if update else os.O_WRONLY if writing else os.O_RDONLY
            if operation == 'w': flags |= os.O_CREAT | os.O_TRUNC
            elif operation == 'x': flags |= os.O_CREAT | os.O_EXCL
            elif operation == 'a': flags |= os.O_CREAT | os.O_APPEND
            descriptor = os.open(value.name, flags, 0o600, dir_fd=parent)
            if require_private:
                info = os.fstat(descriptor)
                if info.st_uid != os.geteuid() or info.st_mode & 0o077:
                    os.close(descriptor)
                    raise PermissionError(errno.EACCES, 'Private current-user permissions are required')
        with os.fdopen(descriptor, mode) as stream:
            yield stream


def atomic_write(path, data, *, overwrite=True, before_commit=None):
    """Write and replace under the same pinned parent; never reopen a temp path."""
    value = _absolute(path)
    temporary = '.pending-' + uuid.uuid4().hex
    with _directory(value.parent) as parent:
        if WINDOWS:
            handle = _open_handle(value.with_name(temporary), _GENERIC_WRITE | _DELETE | _WRITE_DAC,
                                  _CREATE_NEW, private=True, parent=parent)
            try:
                descriptor = msvcrt.open_osfhandle(handle, os.O_BINARY | os.O_WRONLY)
            except BaseException:
                _Close(handle)
                raise
            installed = False
            with os.fdopen(descriptor, 'wb') as stream:
                try:
                    size = stream.write(data)
                    stream.flush(); os.fsync(stream.fileno())
                    if before_commit:
                        before_commit()
                    # Leaf validation rejects an existing junction/symlink. The
                    # rename itself replaces an object, never follows its target.
                    reject_reparse_points(value)
                    _rename(handle, parent, value.name, overwrite)
                    installed = True
                    return size
                finally:
                    if not installed:
                        _delete_on_close(handle)
        else:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=parent)
            try:
                with os.fdopen(descriptor, 'wb') as stream:
                    size = stream.write(data)
                    stream.flush(); os.fsync(stream.fileno())
                    if before_commit:
                        before_commit()
                    try:
                        target = os.stat(value.name, dir_fd=parent, follow_symlinks=False)
                        if stat.S_ISLNK(target.st_mode):
                            raise PermissionError(errno.EACCES, 'Symbolic link target is not allowed')
                    except FileNotFoundError:
                        pass
                    if overwrite:
                        os.replace(temporary, value.name, src_dir_fd=parent, dst_dir_fd=parent)
                    else:
                        os.link(temporary, value.name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
                    os.fsync(parent)
                    return size
            finally:
                try:
                    os.unlink(temporary, dir_fd=parent)
                except FileNotFoundError:
                    pass


def _permissions(path, *, restrict):
    value = _absolute(path)
    with _directory(value.parent) as parent:
        if WINDOWS:
            attributes = os.lstat(value).st_file_attributes
            directory = bool(attributes & _DIRECTORY)
            access = _READ_CONTROL | _READ_ATTRIBUTES | (_WRITE_DAC if restrict else 0)
            handle = _open_handle(value, access, directory=directory, parent=parent)
            try:
                _set_private(handle, directory) if restrict else _assert_private_handle(handle)
            finally:
                _Close(handle)
        else:
            descriptor = os.open(value.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                info = os.fstat(descriptor)
                if restrict:
                    os.fchmod(descriptor, 0o700 if stat.S_ISDIR(info.st_mode) else 0o600)
                elif info.st_uid != os.geteuid() or info.st_mode & 0o077:
                    raise PermissionError(errno.EACCES, 'Private current-user permissions are required')
            finally:
                os.close(descriptor)


def restrict_permissions(path):
    _permissions(path, restrict=True)


def assert_private(path):
    _permissions(path, restrict=False)
