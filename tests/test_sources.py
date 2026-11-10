"""Source request, review, optimistic locking and authorization unit checks."""
import copy, json, sys, unittest
from pathlib import Path
from contextlib import ExitStack
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import HTTPException
from pydantic import ValidationError
from backend import sources as s
from backend.security.context import Principal, use_context
from backend.security.policy import authorize
P = Principal(user_id='author', username='author', home_tenant_id='sub-01', tenant_id='sub-01', role='admin', is_superadmin=False, session_id='session', auth_version=1, database_name='unit_test')
BASE = {'resource_id': 'resource', 'title': '项目字段', 'business_meaning': '项目业务含义', 'grain': '每项目一行', 'object_key': ['id'], 'fields': [{'column': 'id', 'field': 'project_id', 'asset_id': 'D-1', 'meaning': '项目唯一编号', 'unit': '无量纲'}]}

class FakeConnection:

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

class Core(unittest.TestCase):

    def test_unknown_fields(self):
        for cls, body in [(s.QueryBody, {'sql': 'SELECT secret'}), (s.QueryBody, {'tenant_id': 'hq'}), (s.MappingBody, {**BASE, 'status': 'published'}), (s.MappingAction, {'action': 'publish', 'expected_version': 1, 'reason': 'ok', 'reviewed_by': 'other'}), (s.SourceCreate, {'title': 's', 'kind': 'mysql', 'config': {}, 'credential_ref': 'r', 'password': 'secret'})]:
            with self.subTest(cls=cls.__name__), self.assertRaises(ValidationError):
                cls.model_validate(body)

    def test_query_caps_and_scalars(self):
        for body in [{'limit': True}, {'limit': 201}, {'limit': 0}, {'fields': []}, {'filters': [{'field': 'x', 'op': 'in', 'value': []}]}, {'filters': [{'field': 'x', 'op': 'in', 'value': list(range(51))}]}, {'filters': [{'field': 'x', 'op': 'eq', 'value': {'sql': 'secret'}}]}, {'filters': [{'field': 'x', 'op': 'eq', 'value': float('nan')}]}, {'filters': [{'field': 'x', 'op': 'eq', 'value': 'x' * 1001}]}, {'filters': [{'field': 'x', 'op': 'eq'}]}, {'filters': [{'field': 'x', 'op': 'is_null', 'value': False}]}]:
            with self.subTest(body=body), self.assertRaises(ValidationError):
                s.QueryBody.model_validate(body)
        s.QueryBody.model_validate({'filters': [{'field': 'x', 'op': 'is_null'}, {'field': 'x', 'op': 'eq', 'value': None}, {'field': 'x', 'op': 'in', 'value': [1, 'x', None, True]}]})

    def test_mapping_required_semantics(self):
        for key, value in [('grain', ''), ('business_meaning', ''), ('object_key', []), ('object_key', ['missing'])]:
            with self.subTest(key=key), self.assertRaises(ValidationError):
                s.MappingBody.model_validate({**BASE, key: value})
        for update in [{'field': 'bad;select'}, {'meaning': ''}, {'unit': ''}, {'asset_version': 42}]:
            data = copy.deepcopy(BASE)
            data['fields'][0].update(update)
            with self.assertRaises(ValidationError):
                s.MappingBody.model_validate(data)
        data = copy.deepcopy(BASE)
        data['fields'] *= 2
        with self.assertRaises(ValidationError):
            s.MappingBody.model_validate(data)

    def test_projection(self):
        mapping = s.MappingBody.model_validate(BASE).model_dump()
        names, selection = s._selection(mapping, s.QueryBody.model_validate({'fields': ['project_id'], 'filters': [{'field': 'project_id', 'op': 'eq', 'value': "' OR 1=1 --"}]}))
        self.assertEqual(names, ['project_id'])
        self.assertEqual(selection['columns'], ['id'])
        self.assertEqual(selection['filters'][0]['value'], "' OR 1=1 --")
        for request in [{'fields': ['id']}, {'fields': ['project_id', 'project_id']}, {'filters': [{'field': 'unmapped', 'op': 'eq', 'value': 1}]}]:
            with self.assertRaises(HTTPException) as error:
                s._selection(mapping, s.QueryBody.model_validate(request))
            self.assertEqual(error.exception.status_code, 422)

    def test_optimistic_versions(self):
        s._expected({'version': 2}, 2)
        with self.assertRaises(HTTPException) as error:
            s._expected({'version': 2}, 1)
        self.assertEqual(error.exception.status_code, 409)

    def test_reviewer_separation_and_states(self):
        row = {'id': 'map', 'source_id': 'source', 'resource_id': 'resource', 'definition': json.dumps({k: BASE[k] for k in ('business_meaning', 'grain', 'object_key', 'fields')}), 'status': 'in_review', 'version': 2, 'submitted_by': 'author'}
        with use_context(P), ExitStack() as stack:
            stack.enter_context(patch.object(s, 'assert_principal_current', side_effect=lambda p: p))
            stack.enter_context(patch.object(s, 'connect', return_value=FakeConnection()))
            stack.enter_context(patch.object(s, '_authorize', return_value={}))
            stack.enter_context(patch.object(s, '_mapping_row', return_value=row))
            for action in ('publish', 'reject'):
                with self.assertRaises(HTTPException) as error:
                    s.mapping_action('source', 'map', s.MappingAction(action=action, expected_version=2, reason='review'))
                self.assertEqual(error.exception.status_code, 403)
            with self.assertRaises(HTTPException) as error:
                s.mapping_action('source', 'map', s.MappingAction(action='submit', expected_version=2))
            self.assertEqual(error.exception.status_code, 409)

    def test_old_query_cannot_invalidate_new_revision(self):
        connection = FakeConnection()
        from unittest.mock import Mock
        connection.execute = Mock()
        with use_context(P), patch.object(s, 'connect', return_value=connection), patch.object(s, '_authorize', return_value={}), patch.object(s, '_mapping_row', return_value={'status': 'published', 'version': 9}), patch.object(s, '_event') as event:
            s._mark_query_invalid('source', 'map', 'target_changed', 1, 3)
            connection.execute.assert_not_called()
            event.assert_not_called()
            s._mark_query_invalid('source', 'map', 'target_changed', 1, 9)
            self.assertEqual(connection.execute.call_count, 1)
            event.assert_called_once()

    def test_explicit_policy(self):
        for route in ('/api/sources', '/api/sources/lineage/D-1', '/api/sources/s', '/api/sources/s/resources/r/versions', '/api/sources/s/mappings/m/versions'):
            self.assertTrue(authorize(P, 'GET', route).allowed)
        self.assertTrue(authorize(P, 'POST', '/api/sources/s/mappings/m/query').allowed)
        self.assertFalse(authorize(P, 'POST', '/api/sources/s/raw-sql').allowed)

    def test_schema_allowlist(self):

        class Collector:

            def __init__(self):
                self.items = []

            def execute(self, statement):
                self.items.append(statement)
        c = Collector()
        s.ensure_schema(c)
        self.assertEqual(len(c.items), 6)
        self.assertFalse(any(('dataset_rows' in sql or 'password' in sql for sql in c.items)))

    def test_historical_source_identity_and_redaction(self):
        from unittest.mock import Mock
        source = {'id': 'source', 'kind': 'mysql', 'config_version': 1,
                  'config': {'host': 'db.internal', 'port': 3306, 'database': 'old_target',
                             'allowed_objects': ['projects']},
                  'credential_ref': 'never_snapshot_this_reference'}
        row = {'id': 'map', 'source_id': 'source', 'version': 1, 'definition': '{}'}
        connection = FakeConnection()
        connection.execute = Mock(return_value=Mock(fetchone=Mock(return_value=row)))
        with use_context(P), patch.object(s, '_source', return_value=source):
            s._event(connection, 'map', 'create')
            first_json = connection.execute.call_args.args[1][4]
            source['config']['database'] = 'new_target'
            source['config_version'] = 2
            row['version'] = 2
            s._event(connection, 'map', 'invalidate')
            second_json = connection.execute.call_args.args[1][4]
        first, second = json.loads(first_json), json.loads(second_json)
        self.assertEqual(first['source_context']['config']['database'], 'old_target')
        self.assertEqual(first['source_context']['config_version'], 1)
        self.assertEqual(second['source_context']['config']['database'], 'new_target')
        self.assertNotEqual(first['source_context']['config_fingerprint'], second['source_context']['config_fingerprint'])
        self.assertNotIn('credential_ref', first_json)
        for role in ('modeler', 'reviewer'):
            public = s._public_snapshot(first, role)
            self.assertNotIn('config', public['source_context'])
            self.assertEqual(public['source_context']['config_fingerprint'], first['source_context']['config_fingerprint'])
        self.assertIn('config', s._public_snapshot(first, 'admin')['source_context'])
        self.assertIn('config', first['source_context'])  # Sanitizing never mutates history.
        self.assertEqual(set(s._query_source_identity(source)), {'config_version', 'config_fingerprint'})
if __name__ == '__main__':
    unittest.main(verbosity=2)
