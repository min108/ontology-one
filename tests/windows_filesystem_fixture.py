"""Native synthetic filesystem fixtures; no administrator/symlink privilege."""
import ctypes
import struct

from backend import platform_io as fs


def make_junction(link, target):
    fs.ensure_private_directory(link)
    handle = fs._CreateFile(fs._extended(link), fs._GENERIC_WRITE, 1 | 2 | 4, None,
                          fs._OPEN_EXISTING, fs._OPEN_REPARSE | fs._BACKUP_SEMANTICS, None)
    if handle == fs._INVALID:
        fs._win_error()
    try:
        substitute, printed = ('\\??\\' + str(target)).encode('utf-16-le'), str(target).encode('utf-16-le')
        names = substitute + b'\0\0' + printed + b'\0\0'
        fields = struct.pack('<HHHH', 0, len(substitute), len(substitute) + 2, len(printed))
        data = struct.pack('<IHH', 0xA0000003, len(fields) + len(names), 0) + fields + names
        buffer, returned = ctypes.create_string_buffer(data), fs.wintypes.DWORD()
        call = fs._kernel.DeviceIoControl
        call.restype = fs.wintypes.BOOL
        call.argtypes = [fs.wintypes.HANDLE, fs.wintypes.DWORD, fs.wintypes.LPVOID,
                         fs.wintypes.DWORD, fs.wintypes.LPVOID, fs.wintypes.DWORD,
                         ctypes.POINTER(fs.wintypes.DWORD), fs.wintypes.LPVOID]
        if not call(handle, 0x900A4, buffer, len(data), None, 0, ctypes.byref(returned), None):
            fs._win_error()
    finally:
        fs._Close(handle)


def make_world_accessible(path):
    descriptor = fs.wintypes.LPVOID()
    if not fs._Descriptor('D:P(A;;FA;;;WD)', 1, ctypes.byref(descriptor), None):
        fs._win_error()
    try:
        present, defaulted, acl = fs.wintypes.BOOL(), fs.wintypes.BOOL(), fs.wintypes.LPVOID()
        if not fs._DescriptorDacl(descriptor, ctypes.byref(present), ctypes.byref(acl), ctypes.byref(defaulted)):
            fs._win_error()
        with fs._directory(path.parent) as parent:
            handle = fs._open_handle(path, fs._WRITE_DAC | fs._READ_CONTROL, parent=parent)
            try:
                error = fs._SetSecurity(handle, 1, 0x4 | 0x80000000, None, None, acl, None)
                if error:
                    fs._win_error(error)
            finally:
                fs._Close(handle)
    finally:
        fs._Free(descriptor)
