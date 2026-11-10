"""Native filesystem tests. Windows cases must run on real Windows, not mocks."""
import ctypes
import multiprocessing
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from backend import platform_io as fs
from backend.maintenance import runtime_guard, require_maintenance


def lock_probe(path, shared, blocking, entered, release, messages):
    try:
        with fs.secure_open(path, 'a+b') as stream:
            entered.set()
            with fs.file_lock(stream, shared=shared, blocking=blocking):
                messages.put('acquired')
                if release is not None and not release.wait(10):
                    raise RuntimeError('Test lock release timed out')
    except BlockingIOError:
        messages.put('blocked')
    except Exception as exc:
        messages.put(type(exc).__name__)


class _FileFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='platform-io-')
        self.root = fs.ensure_private_directory(Path(self.temporary.name) / '中文 空格!资料')

    def tearDown(self):
        self.temporary.cleanup()


class FileTests(_FileFixture):
    def test_private_unicode_paths_and_atomic_replace_preserve_old_data_on_failure(self):
        path = self.root / '配置 空间!.json'
        fs.atomic_write(path, '合成测试'.encode())
        fs.assert_private(self.root); fs.assert_private(path)
        with fs.secure_open(path, require_private=True) as stream:
            self.assertEqual(stream.read().decode(), '合成测试')
        with self.assertRaises(FileExistsError):
            fs.atomic_write(path, b'overwrite denied', overwrite=False)
        def revoked():
            raise PermissionError('Synthetic permission revocation')
        with self.assertRaises(PermissionError):
            fs.atomic_write(path, b'not installed', before_commit=revoked)
        self.assertEqual(path.read_bytes(), '合成测试'.encode())
        self.assertFalse(list(self.root.glob('.pending-*')))
        fs.atomic_write(path, b'new revision')
        self.assertEqual(path.read_bytes(), b'new revision')

    def test_read_write_update_and_append_modes(self):
        path = self.root / 'modes.txt'
        with fs.secure_open(path, 'xb') as stream:
            stream.write(b'first')
        with fs.secure_open(path, 'a+b') as stream:
            stream.write(b'-second'); stream.flush(); stream.seek(0)
            self.assertEqual(stream.read(), b'first-second')
        with fs.secure_open(path, 'wb') as stream:
            stream.write(b'new')
        with fs.secure_open(path, 'r+b') as stream:
            stream.seek(1); stream.write(b'!')
        self.assertEqual(path.read_bytes(), b'n!w')

    def test_process_shared_exclusive_and_nonblocking_lock_semantics(self):
        path = self.root / 'process.lock'
        context = multiprocessing.get_context('spawn')
        for parent_shared, child_shared, expected in ((True, True, 'acquired'), (True, False, 'blocked'), (False, True, 'blocked')):
            with self.subTest(parent_shared=parent_shared, child_shared=child_shared), fs.secure_open(path, 'a+b') as stream:
                with fs.file_lock(stream, shared=parent_shared):
                    entered, messages = context.Event(), context.Queue()
                    process = context.Process(target=lock_probe, args=(str(path), child_shared, False, entered, None, messages))
                    process.start()
                    try:
                        self.assertTrue(entered.wait(10))
                        self.assertEqual(messages.get(timeout=10), expected)
                        process.join(10)
                        self.assertEqual(process.exitcode, 0)
                    finally:
                        if process.is_alive(): process.terminate(); process.join(5)
                        messages.close()

    def test_blocking_lock_waits_until_release(self):
        path = self.root / 'blocking.lock'
        context = multiprocessing.get_context('spawn')
        entered, release, messages = context.Event(), context.Event(), context.Queue()
        process = context.Process(target=lock_probe, args=(str(path), False, True, entered, release, messages))
        try:
            with fs.secure_open(path, 'a+b') as stream, fs.file_lock(stream):
                process.start()
                self.assertTrue(entered.wait(10))
                with self.assertRaises(__import__('queue').Empty): messages.get(timeout=.1)
            self.assertEqual(messages.get(timeout=10), 'acquired')
            release.set(); process.join(10)
            self.assertEqual(process.exitcode, 0)
        finally:
            if process.is_alive(): process.terminate(); process.join(5)
            messages.close()

    def test_maintenance_excludes_restore_and_resets_context(self):
        with patch.dict(os.environ, {'ONTOLOGY_STORAGE_ROOT': str(self.root / 'data' / 'tenants')}):
            with runtime_guard(shared=True):
                with runtime_guard(shared=True): pass
                with self.assertRaises(RuntimeError):
                    with runtime_guard(shared=False): pass
                with self.assertRaises(RuntimeError): require_maintenance()
            with runtime_guard(shared=False):
                require_maintenance()
                with self.assertRaises(RuntimeError):
                    with runtime_guard(shared=True): pass
            with self.assertRaises(RuntimeError): require_maintenance()


@unittest.skipIf(fs.WINDOWS, 'Unix dirfd behavior')
class UnixRaceTests(_FileFixture):
    def test_atomic_write_uses_original_pinned_parent_when_name_is_swapped(self):
        folder = fs.ensure_private_directory(self.root / 'original')
        outside = fs.ensure_private_directory(self.root / 'outside')
        moved = self.root / 'moved'
        fs.atomic_write(folder / 'value', b'old')
        fs.atomic_write(outside / 'value', b'outside must not change')
        def swap():
            folder.rename(moved)
            folder.symlink_to(outside, target_is_directory=True)
        try:
            fs.atomic_write(folder / 'value', b'new', before_commit=swap)
            self.assertEqual((moved / 'value').read_bytes(), b'new')
            self.assertEqual((outside / 'value').read_bytes(), b'outside must not change')
            with self.assertRaises(OSError):
                with fs.secure_open(folder / 'value'): pass
        finally:
            if folder.is_symlink(): folder.unlink()

    def test_private_read_rejects_broad_permissions_on_opened_file(self):
        path = self.root / 'wide.json'
        path.write_bytes(b'synthetic')
        path.chmod(0o644)
        with self.assertRaises(PermissionError):
            with fs.secure_open(path, require_private=True): pass
        fs.restrict_permissions(path)
        with fs.secure_open(path, require_private=True) as stream:
            self.assertEqual(stream.read(), b'synthetic')


@unittest.skipUnless(fs.WINDOWS, 'Requires actual native Windows filesystem APIs')
class WindowsNativeTests(_FileFixture):
    def junction(self, link, target):
        # Native NTFS mount-point creation needs no Developer Mode or symlink
        # privilege, and does not involve shell quoting for Unicode/! paths.
        fs.ensure_private_directory(link)
        handle = fs._CreateFile(fs._extended(link), fs._GENERIC_WRITE, 1 | 2 | 4, None,
                                 fs._OPEN_EXISTING, fs._OPEN_REPARSE | fs._BACKUP_SEMANTICS, None)
        self.assertNotEqual(handle, fs._INVALID)
        try:
            substitute = ('\\??\\' + str(target)).encode('utf-16-le')
            printed = str(target).encode('utf-16-le')
            names = substitute + b'\0\0' + printed + b'\0\0'
            fields = struct.pack('<HHHH', 0, len(substitute), len(substitute) + 2, len(printed))
            data = struct.pack('<IHH', 0xA0000003, len(fields) + len(names), 0) + fields + names
            buffer, returned = ctypes.create_string_buffer(data), fs.wintypes.DWORD()
            call = fs._kernel.DeviceIoControl
            call.restype = fs.wintypes.BOOL
            call.argtypes = [fs.wintypes.HANDLE, fs.wintypes.DWORD, fs.wintypes.LPVOID,
                             fs.wintypes.DWORD, fs.wintypes.LPVOID, fs.wintypes.DWORD,
                             ctypes.POINTER(fs.wintypes.DWORD), fs.wintypes.LPVOID]
            self.assertTrue(call(handle, 0x900A4, buffer, len(data), None, 0, ctypes.byref(returned), None),
                            'Native junction setup failed')
        finally:
            fs._Close(handle)

    def test_native_64bit_structure_layout(self):
        self.assertEqual(ctypes.sizeof(ctypes.c_void_p), 8)
        self.assertEqual(ctypes.sizeof(fs._ObjectAttributes), 48)
        self.assertEqual(ctypes.sizeof(fs._UnicodeString), 16)
        self.assertEqual(ctypes.sizeof(fs._IoStatus), 16)
        self.assertEqual(fs._RenameInfo.FileName.offset, 20)

    def test_junctions_are_rejected_without_following_foreign_tree(self):
        outside = fs.ensure_private_directory(self.root / 'foreign')
        fs.atomic_write(outside / 'secret', b'foreign')
        link = self.root / '连接 空格!'
        self.junction(link, outside)
        try:
            with self.assertRaises(OSError): fs.reject_reparse_points(link / 'secret')
            with self.assertRaises(OSError): fs.ensure_private_directory(link / 'new')
            with self.assertRaises(OSError):
                with fs.secure_open(link / 'secret'): pass
            with self.assertRaises(OSError): fs.atomic_write(link / 'secret', b'changed')
            self.assertEqual((outside / 'secret').read_bytes(), b'foreign')
        finally:
            link.rmdir()

    def test_pinned_directory_denies_rename_and_reparse_write_handle(self):
        folder = fs.ensure_private_directory(self.root / 'pinned')
        path = folder / 'value'
        fs.atomic_write(path, b'unchanged')
        with fs.secure_open(path) as stream:
            with self.assertRaises(OSError): folder.rename(self.root / 'moved')
            # FSCTL_SET_REPARSE_POINT needs a write handle; it must be denied
            # even if an attacker tries to change the directory in place.
            handle = fs._CreateFile(fs._extended(folder), fs._GENERIC_WRITE, 1 | 2 | 4, None,
                                     fs._OPEN_EXISTING, fs._OPEN_REPARSE | fs._BACKUP_SEMANTICS, None)
            if handle != fs._INVALID:
                fs._Close(handle)
                self.fail('Pinned directory admitted a reparse-capable write handle')
            self.assertEqual(ctypes.get_last_error(), 32)
            self.assertEqual(stream.read(), b'unchanged')

    def test_earlier_directory_write_handle_prevents_safe_open(self):
        folder = fs.ensure_private_directory(self.root / 'preopened')
        path = folder / 'value'
        fs.atomic_write(path, b'unchanged')
        handle = fs._CreateFile(fs._extended(folder), fs._GENERIC_WRITE, 1 | 2 | 4, None,
                                 fs._OPEN_EXISTING, fs._OPEN_REPARSE | fs._BACKUP_SEMANTICS, None)
        self.assertNotEqual(handle, fs._INVALID)
        try:
            with self.assertRaises(OSError):
                with fs.secure_open(path): pass
        finally:
            fs._Close(handle)

    def test_windows_aliases_ads_and_non_acl_volumes_fail_closed(self):
        for name in ('NUL', 'con.txt', 'COM1', 'value:stream', 'trailing.', 'trailing '):
            with self.subTest(name=name), self.assertRaises(OSError):
                fs.atomic_write(self.root / name, b'unsafe')
        with patch.object(fs, '_DriveType', return_value=4), self.assertRaises(OSError):
            fs.ensure_private_directory(self.root / 'network')
        def no_acl(_root, _name, _length, _serial, _max_component, flags, _fs_name, _fs_length):
            ctypes.cast(flags, ctypes.POINTER(fs.wintypes.DWORD)).contents.value = 0
            return True
        with patch.object(fs, '_VolumeInfo', side_effect=no_acl), self.assertRaises(OSError):
            fs.ensure_private_directory(self.root / 'no-acl')

    def test_hardlinked_leaf_is_rejected(self):
        original = self.root / 'original-file'
        alias = self.root / 'hard-link'
        fs.atomic_write(original, b'not shared')
        os.link(original, alias)
        with self.assertRaises(OSError):
            with fs.secure_open(alias): pass

    def test_broad_dacl_is_rejected_on_the_actual_read_handle(self):
        path = self.root / 'synthetic-credential.json'
        fs.atomic_write(path, b'synthetic-only')
        descriptor = fs.wintypes.LPVOID()
        self.assertTrue(fs._Descriptor('D:P(A;;FA;;;WD)', 1, ctypes.byref(descriptor), None))
        try:
            present, defaulted, acl = fs.wintypes.BOOL(), fs.wintypes.BOOL(), fs.wintypes.LPVOID()
            self.assertTrue(fs._DescriptorDacl(descriptor, ctypes.byref(present), ctypes.byref(acl), ctypes.byref(defaulted)))
            with fs._directory(path.parent) as parent:
                handle = fs._open_handle(path, fs._WRITE_DAC | fs._READ_CONTROL, parent=parent)
                try:
                    self.assertEqual(fs._SetSecurity(handle, 1, 0x4 | 0x80000000, None, None, acl, None), 0)
                finally:
                    fs._Close(handle)
        finally:
            fs._Free(descriptor)
        with self.assertRaises(PermissionError): fs.assert_private(path)
        with self.assertRaises(PermissionError):
            with fs.secure_open(path, require_private=True): pass
        fs.restrict_permissions(path)
        with fs.secure_open(path, require_private=True) as stream:
            self.assertEqual(stream.read(), b'synthetic-only')


if __name__ == '__main__':
    unittest.main()
