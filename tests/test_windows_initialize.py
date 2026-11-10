"""Interrupted first-start recovery without a database or real credentials."""
import io
import json
import os
import unittest
from unittest.mock import MagicMock, patch

from backend.security import store
from ops import windows_initialize


class InitializeTests(unittest.TestCase):
    def test_progress_contains_only_run_phase_and_counts(self):
        with patch.dict(os.environ, {'ONTOLOGY_INITIALIZATION_RUN_ID': 'a' * 32}), \
                patch('backend.platform_io.atomic_write') as write:
            progress = windows_initialize.ProgressWriter()
            progress('tenants', 51, 51)
            progress('bootstrap')
            value = json.loads(write.call_args.args[1])
            self.assertEqual(value, {'run_id': 'a' * 32, 'phase': 'bootstrap', 'completed': 51, 'total': 51})
            with self.assertRaises(ValueError): progress('account-secret')
            with self.assertRaises(ValueError): progress('tenants', True, 51)

    def invoke(self, body, row):
        stdin = MagicMock()
        stdin.buffer = io.BytesIO(json.dumps(body).encode())
        connection, cursor = MagicMock(), MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchone.return_value = row
        context = MagicMock()
        context.__enter__.return_value = connection
        with patch.object(windows_initialize.sys, 'stdin', stdin), patch('ops.provision.main') as provision, \
                patch.object(store, 'control_connection', return_value=context), \
                patch.object(store, 'bootstrap_superadmin', return_value='synthetic-user') as bootstrap:
            windows_initialize.main()
        return cursor, bootstrap, provision

    def test_interrupted_bootstrap_accepts_only_original_credentials(self):
        password = 'Synthetic9-Original'
        row = {'username': 'group.admin', 'password_hash': store.hash_password(password)}
        cursor, bootstrap, provision = self.invoke({'username': 'Group.Admin', 'password': password}, row)
        bootstrap.assert_called_once_with('group.admin', password)
        provision.assert_called_once_with()
        query, arguments = cursor.execute.call_args.args
        self.assertIn('is_superadmin=1', query)
        self.assertIn('is_active=1', query)
        self.assertEqual(arguments, ('synthetic-user',))
        for body, existing in (({'username': 'group.admin', 'password': 'Synthetic9-Different'}, row),
                               ({'username': 'other.admin', 'password': password}, row),
                               ({'username': 'group.admin', 'password': password}, None)):
            with self.subTest(username=body['username'], existing=bool(existing)), self.assertRaises(RuntimeError):
                self.invoke(body, existing)

    def test_marker_resume_still_requires_active_headquarters_superadmin(self):
        cursor, bootstrap, _ = self.invoke({}, {'n': 1})
        bootstrap.assert_not_called()
        self.assertIn('is_superadmin=1', cursor.execute.call_args.args[0])
        with self.assertRaises(RuntimeError):
            self.invoke({}, {'n': 0})


if __name__ == '__main__':
    unittest.main()
