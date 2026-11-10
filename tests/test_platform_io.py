"""Native filesystem tests. Windows cases must run on real Windows, not mocks."""
import ctypes
import multiprocessing
import os
from pathlib import Path
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
        from tests.windows_filesystem_fixture import make_junction
        make_junction(link, target)

    def test_native_64bit_structure_layout(self):
        self.assertEqual(ctypes.sizeof(ctypes.c_void_p), 8)
        self.assertEqual(ctypes.sizeof(fs._ObjectAttributes), 48)
        self.assertEqual(ctypes.sizeof(fs._UnicodeString), 16)
        self.assertEqual(ctypes.sizeof(fs._IoStatus), 16)
        self.assertEqual(fs._RenameInfo.FileName.offset, 20)

    def test_write_only_handle_can_validate_metadata_but_cannot_read_file_data(self):
        path = self.root / 'write-only.json'
        with fs._directory(path.parent) as parent:
            handle = fs._open_handle(path, fs._GENERIC_WRITE, fs._CREATE_NEW, private=True, parent=parent)
            try:
                info = fs._FileInfo()
                self.assertTrue(fs._Info(handle, ctypes.byref(info)))
                # The minimum metadata right must not become GENERIC_READ.
                read = fs._kernel.ReadFile
                read.restype = fs.wintypes.BOOL
                read.argtypes = [fs.wintypes.HANDLE, fs.wintypes.LPVOID, fs.wintypes.DWORD,
                                 ctypes.POINTER(fs.wintypes.DWORD), fs.wintypes.LPVOID]
                value, received = ctypes.create_string_buffer(1), fs.wintypes.DWORD()
                self.assertFalse(read(handle, value, 1, ctypes.byref(received), None))
                self.assertEqual(ctypes.get_last_error(), 5)
            finally:
                fs._Close(handle)

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

    def test_metadata_only_handle_cannot_retag_a_pinned_empty_directory(self):
        from tests.windows_filesystem_fixture import set_junction_on_handle
        outside = fs.ensure_private_directory(self.root / 'retag-target')
        fs.atomic_write(outside / 'marker', b'foreign-unchanged')
        for preopened in (False, True):
            with self.subTest(preopened=preopened):
                folder = fs.ensure_private_directory(self.root / ('retag-before' if preopened else 'retag-after'))
                handle = None
                def metadata_handle():
                    # FILE_WRITE_ATTRIBUTES is deliberately excluded from
                    # read/write sharing accounting, so its open may succeed.
                    opened = fs._CreateFile(fs._extended(folder), 0x100, 1 | 2 | 4, None,
                                            fs._OPEN_EXISTING, fs._OPEN_REPARSE | fs._BACKUP_SEMANTICS, None)
                    self.assertNotEqual(opened, fs._INVALID)
                    return opened
                try:
                    if preopened:
                        handle = metadata_handle()
                    with fs._directory(folder) as pinned:
                        if handle is None:
                            handle = metadata_handle()
                        # The folder is empty: success cannot be prevented only
                        # by NTFS's non-empty-directory junction restriction.
                        with self.assertRaises(PermissionError) as failure:
                            set_junction_on_handle(handle, outside)
                        self.assertEqual(failure.exception.winerror, 5)
                        info = fs._FileInfo()
                        self.assertTrue(fs._Info(pinned, ctypes.byref(info)))
                        self.assertFalse(info.attributes & fs._REPARSE)
                finally:
                    if handle is not None:
                        fs._Close(handle)
                    # On regression remove the junction itself, never its target.
                    if getattr(os.lstat(folder), 'st_file_attributes', 0) & fs._REPARSE:
                        folder.rmdir()
                self.assertEqual((outside / 'marker').read_bytes(), b'foreign-unchanged')

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
        from tests.windows_filesystem_fixture import make_world_accessible
        make_world_accessible(path)
        with self.assertRaises(PermissionError): fs.assert_private(path)
        with self.assertRaises(PermissionError):
            with fs.secure_open(path, require_private=True): pass
        fs.restrict_permissions(path)
        with fs.secure_open(path, require_private=True) as stream:
            self.assertEqual(stream.read(), b'synthetic-only')

    def test_private_check_reads_owner_and_rejects_foreign_owner_identity(self):
        path = self.root / 'owner-checked.json'
        fs.atomic_write(path, b'synthetic-only')
        current = fs.current_user_sid()
        with fs.secure_open(path, require_private=True):
            pass
        # Standard users cannot assign a foreign owner. Inject only that SID
        # decoding result; descriptor acquisition remains a real handle API.
        with patch.object(fs, 'current_user_sid', return_value=current), \
                patch.object(fs, '_sid_string', return_value='S-1-5-21-1-2-3-1001'), \
                patch.object(fs, '_GetSecurity', wraps=fs._GetSecurity) as security:
            with self.assertRaises(PermissionError):
                with fs.secure_open(path, require_private=True):
                    pass
            self.assertEqual(security.call_args.args[2], 0x1 | 0x4)


if __name__ == '__main__':
    unittest.main()
