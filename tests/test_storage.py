"""Filesystem boundaries are enforced independently of request path validation."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from backend import storage
from backend.security.context import Principal, use_context


def identity(tenant):
    return Principal(user_id='tester', username='tester', home_tenant_id=tenant,
                     tenant_id=tenant, role='modeler', is_superadmin=False,
                     session_id='session', auth_version=1, database_name='test_' + tenant)


class StorageTests(unittest.TestCase):
    def test_same_name_isolation_and_rejected_paths(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {'ONTOLOGY_STORAGE_ROOT': directory}), \
                patch.object(storage, 'assert_storage_write'):
            paths = {}
            active = storage.ScopedActivitySet()
            for tenant in ('tenant-a', 'tenant-b'):
                with use_context(identity(tenant)):
                    paths[tenant] = storage.upload_path('same.txt', create_parent=True)
                    storage.write_bytes(paths[tenant], tenant.encode())
                    with self.assertRaises(FileExistsError):
                        storage.write_bytes(paths[tenant], b'conflicting retry', overwrite=False)
                    self.assertNotIn('same-id', active)
                    active.add('same-id')
            for tenant in ('tenant-a', 'tenant-b'):
                with use_context(identity(tenant)):
                    self.assertEqual(storage.read_bytes(paths[tenant]), tenant.encode())
                    self.assertIn('same-id', active)
                    for path in ('../tenant-b/data/uploads/same.txt', '/etc/passwd',
                                 'C:/Windows/win.ini', 'data/uploads/../runtime/ai.json',
                                 'data\\uploads\\same.txt', 'data/source/manual.txt'):
                        with self.subTest(path=path), self.assertRaises(HTTPException):
                            storage.scoped_path(path)
            with use_context(identity('tenant-a')):
                with self.assertRaises(HTTPException):
                    storage.read_bytes(paths['tenant-b'])
                link = paths['tenant-a'].parent / 'foreign.txt'
                link.symlink_to(paths['tenant-b'])
                with self.assertRaises(HTTPException):
                    storage.scoped_path('data/uploads/foreign.txt', must_exist=True)
                directory_link = paths['tenant-a'].parent / 'foreign-directory'
                directory_link.symlink_to(paths['tenant-b'].parent, target_is_directory=True)
                with self.assertRaises(HTTPException):
                    storage.scoped_path('data/uploads/foreign-directory/same.txt')


if __name__ == '__main__':
    unittest.main()
