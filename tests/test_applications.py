"""Independent application identity and fail-closed mutation boundaries."""
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import HTTPException
from pydantic import ValidationError
from backend import db, jobs, sources
from backend.security import applications as apps, store
from backend.security.context import Principal, use_context
from backend.security.policy import authorize

SESSION = Principal(user_id='user', username='delegate', home_tenant_id='sub-01', tenant_id='sub-01',
                    role='admin', is_superadmin=False, session_id='session', auth_version=3, database_name='tenant_db')
APP = replace(SESSION, session_id='', identity_kind='application', application_id='app', credential_id='credential', permission_version=4)


def row():
    return {'id': 'app', 'tenant_id': 'sub-01', 'delegate_user_id': 'user', 'username': 'delegate',
            'home_tenant_id': 'sub-01', 'role': 'admin', 'is_active': 1, 'must_change_password': 0,
            'auth_version': 3, 'delegate_auth_version': 3, 'db_name': 'tenant_db', 'tenant_status': 'active',
            'enabled': 1, 'version': 4, 'credential_id': 'credential', 'revoked_at': None,
            'expires_at': time.time() + 100, 'last_used_at': time.time(),
            'tool_scopes': '["ontology_query"]', 'mapping_ids': '["MAP-1"]', 'name': 'Application'}


@contextmanager
def central():
    connection = Mock()
    connection.cursor.return_value.__enter__ = Mock(return_value=Mock())
    connection.cursor.return_value.__exit__ = Mock(return_value=False)
    yield connection


class ApplicationsTest(unittest.TestCase):
    def test_no_session_or_headquarters_authority(self):
        principal = apps._principal(row(), 'request')
        self.assertEqual(principal, replace(APP, request_id='request'))
        value = row(); value.update(tenant_id='hq', home_tenant_id='hq', role='admin')
        self.assertFalse(apps._principal(value, '').is_superadmin)
        self.assertEqual(apps._principal(value, '').session_id, '')

    def test_credential_and_delegation_fail_closed(self):
        cases = ({'enabled': 0}, {'revoked_at': time.time()}, {'expires_at': time.time() - 1},
                 {'is_active': 0}, {'must_change_password': 1}, {'tenant_status': 'disabled'},
                 {'home_tenant_id': 'hq'}, {'auth_version': 4}, {'role': 'unknown'})
        for changes in cases:
            with self.subTest(changes=changes):
                self.assertIsNone(apps._principal({**row(), **changes}, 'request'))
        with patch.object(store, 'control_connection') as connection:
            for invalid in ('', 'browser-session', 'onto_app_short', None):
                self.assertIsNone(apps.authenticate_application(invalid, 'request'))
            connection.assert_not_called()

    def test_live_version_comparison_and_scope_permissions(self):
        with patch.object(store, 'control_connection', central), patch.object(apps, '_token_row', side_effect=lambda *a, **kw: row()):
            self.assertEqual(apps.assert_application_current(APP), APP)
            apps.require_tool(APP, 'ontology_query')
            apps.require_mapping(APP, 'MAP-1')
            for action in (lambda: apps.require_tool(APP, 'ontology_join'), lambda: apps.require_mapping(APP, 'MAP-2'),
                           lambda: apps.assert_application_current(replace(APP, permission_version=3)),
                           lambda: apps.assert_application_current(replace(APP, tenant_id='sub-02'))):
                with self.assertRaises(HTTPException):
                    action()

    def test_write_context_is_mapping_and_identity_bound(self):
        with patch.object(apps, '_checked_row', side_effect=lambda p: (p, row())), patch.object(apps, 'require_mapping') as require:
            with self.assertRaises(HTTPException):
                apps.assert_application_current(APP, require_write=True)
            with apps.application_source_metadata(APP, 'MAP-1'):
                self.assertEqual(apps.assert_application_current(APP, require_write=True), APP)
                with self.assertRaises(HTTPException):
                    apps.assert_application_current(replace(APP, application_id='other'), require_write=True)
            require.assert_called_once_with(APP, 'MAP-1')
            with self.assertRaises(HTTPException):
                apps.assert_application_current(APP, require_write=True)

    def test_narrow_dml_shapes(self):
        allowed = ["UPDATE source_mappings SET status='stale',version=version+1,invalid_reason=?,updated_at=? WHERE id=?",
                   'UPDATE source_connections SET last_discovered_at=? WHERE id=?',
                   'UPDATE source_resources SET available=0,schema_version=?,observed_at=? WHERE id=?',
                   'INSERT INTO source_schema_versions(id,resource_id,version,schema_hash,available,metadata,observed_by,observed_at) VALUES(?,?,?,?,?,?,?,?)']
        denied = ['DELETE FROM source_resources', 'UPDATE entities SET title=? WHERE id=?',
                  'INSERT INTO dataset_rows VALUES(?,?,?,?)', 'UPDATE source_connections SET config=? WHERE id=?',
                  "UPDATE source_mappings SET status='published' WHERE id=?", 'UPDATE source_grants SET can_query=1',
                  'CREATE TABLE x(a INT)', 'UPDATE source_resources SET observed_at=? WHERE id=? OR 1=1',
                  'UPDATE source_connections SET last_discovered_at=? WHERE id=?; DELETE FROM entities',
                  'INSERT INTO source_schema_versions SELECT * FROM source_schema_versions']
        with patch.object(apps, 'assert_application_current', return_value=APP):
            for sql in allowed:
                apps.validate_application_write(APP, sql)
            for sql in denied:
                with self.subTest(sql=sql), self.assertRaises(HTTPException):
                    apps.validate_application_write(APP, sql)

    def test_public_cursor_reexecution_cannot_bypass_connection_guard(self):
        raw = Mock(); native = raw.cursor.return_value
        connection = db.Connection(raw, APP)
        cursor = connection.execute('SELECT 1')
        with patch.object(apps, 'assert_application_current', return_value=APP):
            for operation in (lambda: cursor.execute('UPDATE entities SET title=?', ('changed',)),
                              lambda: cursor.executemany('INSERT INTO dataset_rows VALUES(?,?,?,?)', [('a', 1, '{}', 'sub-01')]),
                              lambda: cursor.execute('SELECT 1; UPDATE entities SET version=9')):
                with self.assertRaises(HTTPException):
                    operation()
        native.execute.assert_called_once()
        native.executemany.assert_not_called()
        self.assertFalse(connection._dirty)

    def test_application_cannot_manage_or_enqueue_or_federate(self):
        with use_context(APP):
            for action in (apps._manager, lambda: store.require_admin(APP),
                           lambda: jobs.enqueue('job', 'material_extract', 'entity', []),
                           lambda: store.assert_job_principal_current(APP)):
                with self.assertRaises(HTTPException):
                    action()
            with self.assertRaises(HTTPException):
                with store.authorized_control_connection(APP):
                    self.fail('entered control mutation')
            with self.assertRaises(HTTPException):
                with db.tenant_connection('sub-02'):
                    self.fail('entered cross-tenant connection')
        self.assertFalse(authorize(APP, 'POST', '/api/integrations/applications').allowed)
        self.assertFalse(authorize(replace(SESSION, role='modeler'), 'GET', '/api/integrations/applications').allowed)

    def test_source_acl_intersection_never_grants_manage(self):
        connection = Mock(); connection.execute.return_value.fetchone.return_value = {'id': 'MAP-1'}
        with patch.object(apps, 'application_permissions', return_value={'mapping_ids': ['MAP-1']}):
            result = apps.intersect_source_permissions(APP, connection, 'source', {'manage': True, 'discover': True, 'query': False})
            self.assertEqual(result, {'manage': False, 'discover': True, 'query': False})
            connection.execute.return_value.fetchone.return_value = None
            self.assertEqual(apps.intersect_source_permissions(APP, connection, 'source', {'manage': True, 'discover': True, 'query': True}),
                             {'manage': False, 'discover': False, 'query': False})

    def test_management_bodies_and_scope_allowlists(self):
        for model, value in ((apps.ApplicationCreate, {'name': 'a', 'delegate_user_id': 'user', 'tool_scopes': ['ontology_query'], 'tenant_id': 'hq'}),
                             (apps.ApplicationCreate, {'name': 'a', 'delegate_user_id': 'user', 'tool_scopes': [], 'expires_days': 30}),
                             (apps.CredentialRotate, {'expected_version': 1, 'expires_days': 91, 'reason': 'rotate'}),
                             (apps.ApplicationPatch, {'expected_version': True, 'enabled': False}),
                             (apps.ApplicationPatch, {'expected_version': 1, 'delegate_user_id': 'other'})):
            with self.subTest(model=model.__name__), self.assertRaises(ValidationError):
                model.model_validate(value)
        for tools, mappings in ((['unknown'], []), (['ontology_query', 'ontology_query'], []), (['ontology_query'], ['M', 'M'])):
            with self.assertRaises(HTTPException):
                apps._validate_scopes(tools, mappings)

    def test_audit_attaches_identity_and_redacts_opaque_token(self):
        cursor = Mock()
        raw = 'onto_app_' + 'A' * 43
        store._audit(cursor, APP, 'query', raw, 'failed', {'mapping_id': 'MAP-1', 'reason': raw})
        args = cursor.execute.call_args.args[1]
        self.assertNotIn(raw, str(args))
        import json
        details = json.loads(args[-1])
        self.assertEqual(details['application_id'], 'app')
        self.assertEqual(details['credential_id'], 'credential')
        self.assertEqual(details['permission_version'], 4)


if __name__ == '__main__':
    unittest.main(verbosity=2)
