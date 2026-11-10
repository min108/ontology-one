"""MCP contract and semantic join security checks, without external services."""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
import unittest
import sqlite3
import json
from unittest.mock import patch

from fastapi import HTTPException
from backend import mcp_contracts as c, mcp_tools as t


def mapping(ident, *, relationship=False):
    return {'id': ident, 'source_id': 'src-' + ident, 'version': 3, 'schema_hash': 'a' * 64,
            'fields': [{'field': 'id', 'column': 'native_id', 'asset_id': 'KEY', 'asset_version': 2,
                        'meaning': '对象编号', 'unit': '无量纲'},
                       {'field': 'value', 'column': 'value', 'asset_id': 'VALUE', 'asset_version': 1,
                        'meaning': '数值', 'unit': '元'}],
            'object_key': ['native_id'],
            'relation_keys': [{'column': 'native_id', 'target_asset_id': 'KEY', 'asset_version': 2}] if relationship else []}


class Connection:
    def __init__(self): self.calls = []
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def close(self): pass
    def execute(self, sql, params=()):
        self.calls.append((sql, params)); return self
    def fetchone(self): return {'id': 'present'}


class Contracts(unittest.TestCase):
    def test_fixed_strict_contract_and_schema(self):
        self.assertEqual(len(c.TOOL_MODELS), 5)
        for name, model in c.TOOL_MODELS.items():
            self.assertFalse(model.model_json_schema()['additionalProperties'])
        for body in ({'tenant_id': 'other'}, {'sql': 'DROP TABLE secret'}, {'limit': True},
                     {'limit': 101}, {'timeout_ms': 30001}, {'contract_version': '2.0'}):
            with self.assertRaises(c.ToolFailure) as caught:
                c.parse_tool('ontology_discover', body)
            self.assertEqual(caught.exception.code, 'INVALID_ARGUMENT')
        for body in ({}, {'entity_id': 'x', 'mapping_id': 'y'}):
            with self.assertRaises(c.ToolFailure): c.parse_tool('ontology_explain', body)
        with self.assertRaises(c.ToolFailure): c.parse_tool('arbitrary_sql', {})

    def test_join_step_and_filter_limits(self):
        base = {'left': {'mapping_id': 'a'}, 'right': {'mapping_id': 'b'}, 'left_key': 'id', 'right_key': 'id'}
        self.assertEqual(c.parse_tool('ontology_join', base).left.query.limit, 50)
        for changed in ({'left': {'mapping_id': 'a', 'query': {'limit': 51}}},
                        {'right': {'mapping_id': 'a'}}, {'max_rows': 201}, {'allow_partial': 'true'},
                        {'right': {'mapping_id': 'b', 'filters': [{'field': 'id', 'op': 'eq', 'value': 1}] * 12}}):
            with self.assertRaises(c.ToolFailure): c.parse_tool('ontology_join', {**base, **changed})

    def test_errors_never_return_rejected_values_or_partial_rows(self):
        marker = 'DO_NOT_RETURN_PRIVATE_FILTER'
        for error in (ValueError(marker), RuntimeError(marker), HTTPException(502, marker),
                      HTTPException(403, {'message': marker}), HTTPException(504, {'message': marker})):
            result = c.safe_failure(error, 'request')
            self.assertEqual(result.status, 'error')
            self.assertIsNone(result.data)
            self.assertEqual(result.provenance, [])
            self.assertNotIn(marker, result.model_dump_json())
        with self.assertRaises(ValueError):
            c.ToolEnvelope(request_id='request', status='error', data={'rows': [marker]},
                           error=c.SafeError(code='ERROR', message='安全消息'))

    def test_final_envelope_is_byte_bounded_and_finite(self):
        with self.assertRaises(c.ToolFailure):
            c.bounded_envelope(c.ToolEnvelope(request_id='r', status='ok', data={'rows': ['x' * c.MAX_RESULT_BYTES]}))
        with self.assertRaises(c.ToolFailure):
            c.bounded_envelope(c.ToolEnvelope(request_id='r', status='ok', data={'rows': [float('nan')]}))


class Joins(unittest.TestCase):
    def test_only_declared_same_version_same_unit_object_key_join(self):
        left, right = mapping('left', relationship=True), mapping('right')
        t._join_keys(left, right, 'id', 'id')
        for mutation in ('undeclared', 'different_standard', 'different_version', 'different_unit', 'not_object_key', 'partial_composite_key'):
            a, b = deepcopy(left), deepcopy(right)
            if mutation == 'undeclared': a['relation_keys'] = []
            if mutation == 'different_standard': b['fields'][0]['asset_id'] = 'OTHER'
            if mutation == 'different_version': b['fields'][0]['asset_version'] = 7
            if mutation == 'different_unit': b['fields'][0]['unit'] = '万元'
            if mutation == 'not_object_key': b['object_key'] = []
            if mutation == 'partial_composite_key': b['object_key'] = ['native_id', 'company_id']
            with self.subTest(mutation=mutation), self.assertRaises(c.ToolFailure): t._join_keys(a, b, 'id', 'id')

    def test_explicit_projection_must_include_key(self):
        self.assertEqual(t._projection(None, mapping('a'), 'id'), ['id', 'value'])
        for fields in (['value'], ['id', 'id'], ['id', 'unknown']):
            with self.assertRaises((c.ToolFailure, HTTPException)): t._projection(fields, mapping('a'), 'id')

    def test_typed_keys_duplicates_and_result_cap(self):
        rows, limited, unknown = t._bounded_join([{'id': 1}, {'id': '1'}, {'id': True}, {'id': None}],
                                                [{'id': 1, 'n': 1}, {'id': 1, 'n': 2}, {'id': '1', 'n': 3}],
                                                'id', 'id', 'left', 3)
        self.assertEqual([row['right']['n'] for row in rows], [1, 2, 3])
        self.assertTrue(limited)
        self.assertEqual(unknown, 0)
        self.assertNotEqual(t._key(True), t._key(1))
        self.assertNotEqual(t._key(1.0), t._key(1))
        self.assertNotEqual(t._key('1'), t._key(1))

    def test_right_truncation_never_falsely_claims_unmatched(self):
        rows, limited, unknown = t._bounded_join([{'id': 1}, {'id': 2}, {'id': None}], [{'id': 1}],
                                                'id', 'id', 'left', 20, right_truncated=True)
        self.assertEqual([row['left']['id'] for row in rows], [1, None])
        self.assertIsNone(rows[1]['right'])
        self.assertEqual(unknown, 1)
        self.assertFalse(limited)

    def test_join_amplification_is_bounded_before_final_serialization(self):
        with self.assertRaises(c.ToolFailure) as caught:
            t._bounded_join([{'id': 1, 'large': 'x' * 40000}] * 50, [{'id': 1}] * 20, 'id', 'id', 'inner', 200)
        self.assertEqual(caught.exception.code, 'RESULT_LIMIT')

    def test_right_operational_failure_partial_only_with_explicit_consent(self):
        principal = SimpleNamespace(tenant_id='sub-01')
        left, right = mapping('left', relationship=True), mapping('right')
        loaded = [({'id': 'src-left', 'version': 1}, left, {}), ({'id': 'src-right', 'version': 1}, right, {})]
        args = {'left': {'mapping_id': 'left'}, 'right': {'mapping_id': 'right'}, 'left_key': 'id', 'right_key': 'id'}
        first = {'mapping_version': 3, 'rows': [{'id': 1, 'value': 10}], 'truncated': False}
        for consent, code, allowed in ((True, 'SOURCE_TIMEOUT', True), (True, 'SOURCE_UPSTREAM_UNAVAILABLE', True), (True, 'SOURCE_UPSTREAM_ERROR', False), (True, 'SOURCE_UPSTREAM_AUTH', False),
                                       (False, 'SOURCE_TIMEOUT', False), (True, 'SOURCE_SCHEMA_DRIFT', False),
                                       (True, 'MCP_PROFILE_INCOMPATIBLE', False), (True, 'source_forbidden', False)):
            body = c.parse_tool('ontology_join', {**args, 'allow_partial': consent})
            with patch.object(t, 'connect', return_value=Connection()), patch.object(t, '_binding', side_effect=loaded), \
                 patch.object(t, 'require_context', return_value=principal), patch.object(t, 'validate_receipts') as verify, \
                 patch.object(t.sources, 'execute_mapping_query', side_effect=[first, HTTPException(502, {'code': code})]):
                if allowed:
                    data, receipts, warnings = t._join(body)
                    self.assertEqual(data['rows'], []); self.assertEqual(data['failed_step'], 'right')
                    self.assertEqual(data['unjoined_left'], first['rows']); self.assertFalse(data['complete'])
                    self.assertEqual(len(receipts), 2); verify.assert_called_once_with(receipts)
                else:
                    with self.assertRaises(HTTPException): t._join(body)

    def test_partial_revocation_discards_previously_read_rows(self):
        principal = SimpleNamespace(tenant_id='sub-01')
        left, right = mapping('left', relationship=True), mapping('right')
        loaded = [({'id': 'src-left', 'version': 1}, left, {}), ({'id': 'src-right', 'version': 1}, right, {})]
        body = c.parse_tool('ontology_join', {'left': {'mapping_id': 'left'}, 'right': {'mapping_id': 'right'},
                                            'left_key': 'id', 'right_key': 'id', 'allow_partial': True})
        with patch.object(t, 'connect', return_value=Connection()), patch.object(t, '_binding', side_effect=loaded), \
             patch.object(t, 'require_context', return_value=principal), patch.object(t, 'validate_receipts', side_effect=HTTPException(403, 'revoked')), \
             patch.object(t.sources, 'execute_mapping_query', side_effect=[{'mapping_version': 3, 'rows': [{'id': 1}], 'truncated': False}, HTTPException(504, {'code': 'SOURCE_TIMEOUT'})]):
            with self.assertRaises(HTTPException) as caught: t._join(body)
            self.assertEqual(caught.exception.status_code, 403)

    def test_both_sources_preflight_before_first_query(self):
        body = c.parse_tool('ontology_join', {'left': {'mapping_id': 'left'}, 'right': {'mapping_id': 'right'}, 'left_key': 'id', 'right_key': 'id'})
        with patch.object(t, 'connect', return_value=Connection()), \
             patch.object(t, '_binding', side_effect=[({'id': 'a'}, mapping('left', relationship=True), {}), HTTPException(403, 'denied')]), \
             patch.object(t.sources, 'execute_mapping_query') as query:
            with self.assertRaises(HTTPException): t._join(body)
            query.assert_not_called()


class Receipts(unittest.TestCase):
    def test_receipt_rechecks_all_sources_in_stable_lock_order(self):
        principal = SimpleNamespace(tenant_id='sub-01')
        values = [c.Receipt(tenant_id='sub-01', source_id='src-b', mapping_id='b', mapping_version=3, source_version=1, schema_hash='a' * 64),
                  c.Receipt(tenant_id='sub-01', source_id='src-a', mapping_id='a', mapping_version=3, source_version=1, schema_hash='a' * 64)]
        conn = Connection(); auth = SimpleNamespace(require_mapping=lambda p, m: None)
        with patch.object(t, '_principal', return_value=principal), patch.object(t, '_auth', return_value=auth), \
             patch.object(t, 'connect', return_value=conn), patch.object(t, 'assert_principal_current'), \
             patch.object(t, '_binding', side_effect=[({'id': 'src-a', 'version': 1}, mapping('a'), {}),
                                                    ({'id': 'src-b', 'version': 1}, mapping('b'), {})]) as binding:
            t.validate_receipts(values)
            self.assertEqual([call.args[1] for call in binding.call_args_list], ['a', 'b'])
            self.assertEqual(conn.calls[0][0], 'SET TRANSACTION ISOLATION LEVEL READ COMMITTED')
            self.assertTrue(all('FOR SHARE' in sql for sql, _ in conn.calls[1:]))

    def test_scope_or_new_version_rejects_entire_receipt_batch(self):
        principal = SimpleNamespace(tenant_id='sub-01')
        receipt = c.Receipt(tenant_id='sub-01', source_id='src-a', mapping_id='a', mapping_version=3, source_version=1, schema_hash='a' * 64)
        with patch.object(t, '_principal', return_value=principal):
            with self.assertRaises(c.ToolFailure): t.validate_receipts([receipt.model_copy(update={'tenant_id': 'sub-02'})])
            with self.assertRaises(c.ToolFailure): t.validate_receipts([receipt, receipt.model_copy(update={'mapping_version': 4})])
        with patch.object(t, '_principal', return_value=principal), patch.object(t, '_auth', return_value=SimpleNamespace(require_mapping=lambda p, m: None)), \
             patch.object(t, 'connect', return_value=Connection()), patch.object(t, '_binding', return_value=({'id': 'src-a', 'version': 2}, mapping('a'), {})):
            with self.assertRaises(c.ToolFailure): t.validate_receipts([receipt])

    def test_explanation_never_contains_connection_or_query_definition(self):
        source = {'id': 's', 'title': '源', 'kind': 'mcp', 'version': 1,
                  'credential_ref': 'PRIVATE_CREDENTIAL_REF', 'config': {'endpoint': 'PRIVATE_ENDPOINT'}}
        bind = {**mapping('a'), 'title': '映射', 'status': 'published', 'business_meaning': '业务意义', 'grain': '一对象一行',
                'resource_id': 'r', 'reviewed_by': 'reviewer', 'updated_at': 'now'}
        resource = {'schema_version': 1, 'metadata': {'name': '资源', 'kind': 'saved_query', 'definition': 'PRIVATE_SQL'}}
        with patch.object(t, 'require_context', return_value=SimpleNamespace(tenant_id='sub-01')):
            result = t._safe_binding(source, bind, resource)
        for forbidden in ('PRIVATE_CREDENTIAL_REF', 'PRIVATE_ENDPOINT', 'PRIVATE_SQL'):
            self.assertNotIn(forbidden, str(result))


class DiagnosticAndGraph(unittest.TestCase):
    def test_draft_diagnostic_does_not_expose_unpublished_text(self):
        source = {'id': 's', 'version': 2, 'title': 'source', 'kind': 'mysql'}
        bind = {**mapping('a'), 'title': 'PRIVATE_DRAFT_TITLE', 'status': 'draft', 'resource_id': 'r',
                'business_meaning': 'PRIVATE_DRAFT_MEANING', 'invalid_reason': None}
        with patch.object(t, '_binding', return_value=(source, bind, {'schema_version': 2, 'metadata': {}})), \
             patch.object(t, 'require_context', return_value=SimpleNamespace(tenant_id='sub-01')):
            result, receipt = t._explanation(Connection(), 'a')
        self.assertFalse(result['locally_current'])
        self.assertEqual(result['invalid_reason'], 'mapping_draft')
        self.assertEqual(receipt.purpose, 'diagnostic')
        self.assertNotIn('PRIVATE_DRAFT', str(result))
        self.assertNotIn('fields', result)
        self.assertNotIn('title', result)

    def graph_database(self):
        conn = sqlite3.connect(':memory:'); conn.row_factory = sqlite3.Row
        conn.executescript('CREATE TABLE entities(id TEXT PRIMARY KEY,kind TEXT,title TEXT,summary TEXT,status TEXT,version INTEGER,payload TEXT);'
                           'CREATE TABLE relations(id TEXT PRIMARY KEY,source TEXT,target TEXT,type TEXT,label TEXT,status TEXT);')
        entities = [('a','data','published',{}),('b','data','draft',{}),('c','knowledge','published',{}),
                    ('d','rule','published',{}),('e','data','published',{}),('f','knowledge','published',{}),
                    ('hidden','organization','published',{'hide_in_graph':True}),('rows','dataset','published',{})]
        conn.executemany('INSERT INTO entities VALUES(?,?,?,?,?,?,?)',
                         [(ident,kind,ident,'meaning',status,1,json.dumps(payload)) for ident,kind,status,payload in entities])
        conn.executemany('INSERT INTO relations VALUES(?,?,?,?,?,?)',
                         [('ab','a','b','related','private','confirmed'),('bc','b','c','related','private bridge','confirmed'),
                          ('ad','a','d','related','public','confirmed'),('df','d','f','related','public second hop','confirmed'),
                          ('ae','a','e','related','unreviewed','candidate'),('ah','a','hidden','related','hidden root','confirmed'),
                          ('ar','a','rows','related','business rows','confirmed')])
        return conn

    def test_traversal_cannot_bridge_through_hidden_or_unpublished_nodes(self):
        conn = self.graph_database()
        with patch.object(t, 'connect', return_value=conn), patch.object(t, 'require_context', return_value=SimpleNamespace(tenant_id='sub-01')):
            data, receipts, warnings = t._traverse(c.TraverseInput(entity_id='a', depth=3))
        self.assertEqual({n['id'] for n in data['nodes']}, {'a','d','f'})
        self.assertEqual({e['id'] for e in data['edges']}, {'ad','df'})
        self.assertEqual(receipts, [])

    def test_traversal_reports_node_budget_truncation(self):
        conn = self.graph_database()
        with patch.object(t, 'connect', return_value=conn), patch.object(t, 'require_context', return_value=SimpleNamespace(tenant_id='sub-01')):
            data, _, warnings = t._traverse(c.TraverseInput(entity_id='a', depth=3, max_nodes=2))
        self.assertEqual(len(data['nodes']), 2)
        self.assertTrue(data['truncated'])
        self.assertEqual(warnings[0].code, 'TRUNCATED')


if __name__ == '__main__':
    unittest.main()
