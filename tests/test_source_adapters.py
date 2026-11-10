"""Pure adapter regressions: no network, real credentials, servers or source rows persisted."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal
import io
import json
import logging
import math
import os
from pathlib import Path
import ssl
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pymysql
from mcp import types as mcp_types

from backend import source_adapters as adapters
from backend import platform_io as fs


def resource(key='items', columns=None, kind='table'):
    columns = columns or ['id', 'amount', 'observed_at']
    return {'key': key, 'name': key, 'kind': kind,
            'columns': [{'name': name, 'type': 'varchar', 'nullable': name != 'id',
                         'primary_key': name == 'id'} for name in columns],
            'primary_key': ['id'] if 'id' in columns else []}


def mysql_resource(key='items', columns=None, kind='table'):
    result = resource(key, columns, kind)
    result['description'] = ''
    for column in result['columns']:
        column['comment'] = ''
    if kind == 'view':
        result['definition_version'] = 'a' * 64
    return result


def tool(operation):
    selection = {'type': 'object', 'properties': {
        'columns': {'type': 'array', 'items': {'type': 'string'}},
        'filters': {'type': 'array', 'items': {'type': 'object'}},
        'order_by': {'type': 'array', 'items': {'type': 'object'}},
        'limit': {'type': 'integer'}}, 'required': ['columns', 'filters', 'order_by', 'limit']}
    inputs = {'contract_version': {'type': 'string', 'const': '1.0'}}
    outputs = {'contract_version': {'type': 'string', 'const': '1.0'}}
    if operation == 'discover':
        outputs['resources'] = {'type': 'array', 'items': {'type': 'object'}}
    else:
        inputs.update(resource={'type': 'string'}, selection=selection)
        outputs.update(columns={'type': 'array', 'items': {'type': 'string'}},
                       rows={'type': 'array', 'items': {'type': 'object'}},
                       truncated={'type': 'boolean'}, elapsed_ms={'type': 'number'})
    return mcp_types.Tool(name='ontology_source_' + operation,
        input_schema={'type': 'object', 'properties': inputs, 'required': list(inputs)},
        output_schema={'type': 'object', 'properties': outputs, 'required': list(outputs)},
        annotations=mcp_types.ToolAnnotations(read_only_hint=True))


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rows = []
        self.index = 0
        self.description = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def execute(self, statement, params=None):
        params = tuple(params or ())
        self.connection.statements.append((statement, params))
        normalized = statement.upper()
        if 'INFORMATION_SCHEMA.TABLES' in normalized:
            self.rows = [{'object_name': item['key'], 'object_kind': 'VIEW' if item['kind'] == 'view' else 'BASE TABLE',
                          'description': item.get('description', '')} for item in self.connection.resources
                         if item['key'] in params[1:]]
        elif 'INFORMATION_SCHEMA.COLUMNS' in normalized:
            item = next(item for item in self.connection.resources if item['key'] == params[1])
            self.rows = [{'column_name': column['name'], 'data_type': column['type'],
                          'is_nullable': 'YES' if column['nullable'] else 'NO',
                          'column_key': 'PRI' if column['primary_key'] else '',
                          'column_comment': column.get('comment', '')} for column in item['columns']]
        elif 'INFORMATION_SCHEMA.VIEWS' in normalized:
            item = next(item for item in self.connection.resources if item['key'] == params[1])
            self.rows = [{'definition_version': item.get('definition_version')}]
        elif 'SSL_CIPHER' in normalized:
            self.rows = [{'Variable_name': 'Ssl_cipher', 'Value': 'TLS_AES_256_GCM_SHA384'}]
        elif normalized.rstrip().endswith('LIMIT 0'):
            self.rows = []
        elif normalized.lstrip().startswith('SELECT'):
            self.rows = deepcopy(self.connection.query_rows)
        else:
            self.rows = []
        self.index = 0
        self.description = tuple((key,) for key in self.rows[0]) if self.rows else None
        return len(self.rows)

    def fetchall(self):
        values = self.rows[self.index:]
        self.index = len(self.rows)
        return values

    def fetchone(self):
        rows = self.fetchmany(1)
        return rows[0] if rows else None

    def fetchmany(self, size=1):
        self.connection.fetch_sizes.append(size)
        values = self.rows[self.index:self.index + size]
        self.index += len(values)
        return values

    def __iter__(self):
        return iter(self.fetchall())

    def close(self):
        self.connection.events.append('cursor.close')


class FakeConnection:
    def __init__(self, rows=(), resources=None):
        self.query_rows = list(rows)
        self.resources = deepcopy(resources if resources is not None else
                                  [mysql_resource(), mysql_resource('summary_view', kind='view')])
        self.statements = []
        self.fetch_sizes = []
        self.closed = False
        self.rollbacks = 0
        self.events = []

    def cursor(self, *args, **kwargs):
        return FakeCursor(self)

    def close(self):
        self.events.append('connection.close')
        self.closed = True

    def rollback(self):
        self.rollbacks += 1

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class ChunkStream(adapters.httpx2.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk

    async def aclose(self):
        self.closed = True


class FakeMcpSession:
    def __init__(self, resources=None, response=None, failure=None):
        self.resources = resources if resources is not None else [resource()]
        self.response = response
        self.failure = failure
        self.operations = []
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True

    async def initialize(self):
        self.operations.append(('initialize', None))

    async def list_tools(self, **kwargs):
        self.operations.append(('list_tools', None))
        return mcp_types.ListToolsResult(tools=[tool('discover'), tool('query')])

    async def call_tool(self, name, arguments, **kwargs):
        self.operations.append((name, deepcopy(arguments)))
        if self.failure:
            raise self.failure
        if name == 'ontology_source_discover':
            payload = {'contract_version': '1.0', 'resources': deepcopy(self.resources)}
        else:
            payload = {'contract_version': '1.0', 'columns': arguments['selection']['columns'],
                       'rows': [{name: 'value-' + name for name in arguments['selection']['columns']}],
                       'truncated': False, 'elapsed_ms': 1.0}
        if self.response is not None:
            return self.response(name, payload)
        return mcp_types.CallToolResult(content=[], structured_content=payload, is_error=False)


class SourceAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='ontology-adapter-unit-')
        self.registry = Path(self.temporary.name) / 'credentials.json'
        self.secret = 'UNIT_ONLY_NOT_A_REAL_PASSWORD'
        self.mysql = {'id': 'source', 'tenant_id': 'sub-01', 'kind': 'mysql', 'credential_ref': 'MYSQL_TEST',
                      'config': {'host': 'db.example.test', 'port': 3306, 'database': 'business',
                                 'allowed_objects': ['items', 'summary_view']}}
        self.mcp = {'id': 'mcp-source', 'tenant_id': 'sub-01', 'kind': 'mcp', 'credential_ref': 'MCP_TEST',
                    'config': {'endpoint': 'https://mcp.example.test/mcp', 'allowed_objects': ['items']}}
        self.refs = {
            'MYSQL_TEST': {'tenant_id': 'sub-01', 'kind': 'mysql', 'host': 'db.example.test', 'port': 3306,
                           'database': 'business', 'username': 'readonly', 'password': self.secret},
            'MCP_TEST': {'tenant_id': 'sub-01', 'kind': 'mcp', 'endpoint': 'https://mcp.example.test/mcp',
                         'token': 'UNIT_ONLY_NOT_A_REAL_TOKEN'}}
        self.write_registry()
        self.environment = patch.dict(os.environ, {'ONTOLOGY_SOURCE_CREDENTIALS_FILE': str(self.registry)})
        self.environment.start()
        self.network_guard = patch('socket.create_connection', side_effect=AssertionError('Unit tests cannot open sockets'))
        self.network_attempts = self.network_guard.start()

    def tearDown(self):
        self.network_guard.stop()
        self.environment.stop()
        self.temporary.cleanup()
        self.network_attempts.assert_not_called()

    def write_registry(self, mode=0o600):
        fs.atomic_write(self.registry, json.dumps({'refs': self.refs}).encode('utf-8'))
        if fs.WINDOWS:
            if mode != 0o600:
                from tests.windows_filesystem_fixture import make_world_accessible
                make_world_accessible(self.registry)
        else:
            self.registry.chmod(mode)

    def selection(self, **overrides):
        return {'columns': ['id', 'amount', 'observed_at'], 'filters': [], 'order_by': [], 'limit': 2, **overrides}

    @contextmanager
    def mocked_mcp(self, session):
        observed = []

        @asynccontextmanager
        async def transport(endpoint, **kwargs):
            observed.append((endpoint, kwargs))
            yield object(), object()

        with patch.object(adapters, 'ClientSession', return_value=session), \
             patch.object(adapters, 'streamable_http_client', side_effect=transport):
            yield observed

    def assert_failure(self, operation):
        with self.assertRaises(adapters.SourceFailure) as caught:
            operation()
        error = caught.exception
        self.assertIsInstance(error.code, str)
        self.assertTrue(error.code)
        self.assertIsInstance(error.message, str)
        self.assertNotIn(self.secret, error.message)
        self.assertNotIn(self.secret, str(error))
        self.assertIn(error.status, (400, 403, 409, 422, 500, 502, 503, 504))
        return error

    def test_config_accepts_supported_targets_and_rejects_unknown_keys(self):
        adapters.validate_config('mysql', self.mysql['config'])
        adapters.validate_config('mcp', self.mcp['config'])
        for kind, config in [('mysql', {**self.mysql['config'], 'password': self.secret}),
                             ('mcp', {**self.mcp['config'], 'headers': {'Authorization': 'incoming-token'}}),
                             ('mysql', {**self.mysql['config'], 'sql': 'SELECT anything'})]:
            with self.subTest(kind=kind, unknown_key=set(config) - set(self.mysql['config'])):
                self.assert_failure(lambda: adapters.validate_config(kind, config))

    def test_config_rejects_empty_or_oversized_allowlist_and_invalid_target(self):
        for changes in ({'allowed_objects': []}, {'allowed_objects': ['a'] * 101}, {'allowed_objects': ['']},
                        {'allowed_objects': 'items'}, {'host': ''}, {'port': 0}, {'port': 65536}, {'port': True}):
            with self.subTest(changes=changes):
                self.assert_failure(lambda: adapters.validate_config('mysql', {**self.mysql['config'], **changes}))
        for endpoint in ('not a URL', 'file:///etc/passwd', 'https://user:pass@host/mcp', 'https://host/mcp#token'):
            with self.subTest(endpoint=endpoint):
                self.assert_failure(lambda: adapters.validate_config('mcp', {**self.mcp['config'], 'endpoint': endpoint}))

    def test_registry_is_exactly_bound_to_tenant_kind_and_target(self):
        adapters._resolve_credentials(self.mysql)
        for field, value in [('tenant_id', 'sub-02'), ('kind', 'mcp'), ('host', 'other.example.test'),
                             ('port', 3307), ('database', 'other_database')]:
            original = self.refs['MYSQL_TEST'][field]
            self.refs['MYSQL_TEST'][field] = value
            self.write_registry()
            with self.subTest(field=field):
                self.assert_failure(lambda: adapters._resolve_credentials(self.mysql))
            self.refs['MYSQL_TEST'][field] = original
        self.write_registry()
        self.refs['MCP_TEST']['endpoint'] += '/other'
        self.write_registry()
        self.assert_failure(lambda: adapters._resolve_credentials(self.mcp))

    def test_registry_is_reread_and_revocation_takes_effect_without_cache(self):
        adapters._resolve_credentials(self.mysql)
        self.refs.pop('MYSQL_TEST')
        self.write_registry()
        self.assert_failure(lambda: adapters._resolve_credentials(self.mysql))

    def test_registry_rejects_world_readable_symlink_and_invalid_json(self):
        self.write_registry(0o644)
        self.assert_failure(lambda: adapters._resolve_credentials(self.mysql))
        self.write_registry()
        if fs.WINDOWS:
            from tests.windows_filesystem_fixture import make_junction
            alias = self.registry.with_name('junction')
            make_junction(alias, self.registry.parent)
            try:
                with patch.dict(os.environ, {'ONTOLOGY_SOURCE_CREDENTIALS_FILE': str(alias / self.registry.name)}):
                    self.assert_failure(lambda: adapters._resolve_credentials(self.mysql))
            finally:
                alias.rmdir()
        else:
            alias = self.registry.with_name('alias.json')
            alias.symlink_to(self.registry)
            with patch.dict(os.environ, {'ONTOLOGY_SOURCE_CREDENTIALS_FILE': str(alias)}):
                self.assert_failure(lambda: adapters._resolve_credentials(self.mysql))
        fs.atomic_write(self.registry, ('{"refs": ' + self.secret).encode('utf-8'))
        self.assert_failure(lambda: adapters._resolve_credentials(self.mysql))

    def test_missing_registry_and_default_mcp_transport_fail_closed(self):
        with patch.dict(os.environ, {'ONTOLOGY_SOURCE_CREDENTIALS_FILE': str(self.registry.with_name('missing'))}):
            self.assert_failure(lambda: adapters._resolve_credentials(self.mysql))
        for scheme in ('http', 'HTTP'):
            self.mcp['config']['endpoint'] = self.refs['MCP_TEST']['endpoint'] = scheme + '://127.0.0.1:9999/mcp'
            self.write_registry()
            with self.subTest(scheme=scheme):
                self.assert_failure(lambda: adapters._resolve_credentials(self.mcp))
        self.refs['MCP_TEST']['allow_insecure'] = True
        self.write_registry()
        adapters._resolve_credentials(self.mcp)

    def test_resource_validation_filters_inventory_and_requires_stable_saved_query_definition(self):
        resources = [resource(), resource('outside'), {**resource('registered', kind='saved_query'),
                     'definition': 'Registered monthly totals', 'definition_version': 'v1'}]
        selected = adapters._validate_resources(resources, ['items', 'registered'])
        self.assertEqual({item['key'] for item in selected}, {'items', 'registered'})
        self.assert_failure(lambda: adapters._validate_resources([resource('registered', kind='saved_query')], ['registered']))
        self.assert_failure(lambda: adapters._validate_resources([resource(), resource()], ['items']))
        self.assert_failure(lambda: adapters._validate_resources([{**resource(), 'primary_key': ['missing']}], ['items']))

    def test_resource_count_columns_and_encoded_metadata_limits(self):
        self.assert_failure(lambda: adapters._validate_resources([resource('r' + str(i)) for i in range(101)], ['r0']))
        self.assert_failure(lambda: adapters._validate_resources([resource(columns=['c' + str(i) for i in range(201)])], ['items']))
        oversized = resource()
        oversized['description'] = 'x' * (2 * 1024 * 1024)
        self.assert_failure(lambda: adapters._validate_resources([oversized], ['items']))
        invalid = resource()
        invalid['columns'][0]['nullable'] = 'false'
        self.assert_failure(lambda: adapters._validate_resources([invalid], ['items']))

    def test_selection_rejects_unknown_columns_sql_and_unbounded_filters(self):
        columns = ['id', 'amount', 'observed_at']
        adapters._validate_selection(self.selection(), columns)
        invalid = [self.selection(sql='DELETE FROM source'), self.selection(columns=['unknown']),
                   self.selection(columns=['id', 'id']), self.selection(limit=201), self.selection(limit=True),
                   self.selection(filters=[{'column': 'id', 'op': 'eq', 'value': 'x'}] * 13),
                   self.selection(filters=[{'column': 'id', 'op': 'in', 'value': list(range(51))}]),
                   self.selection(filters=[{'column': 'id', 'op': 'eq', 'value': {'sql': 'SELECT secret'}}]),
                   self.selection(filters=[{'column': 'id', 'op': 'eq', 'value': 'x' * 1001}]),
                   self.selection(filters=[{'column': 'id', 'op': 'eq', 'value': math.inf}]),
                   self.selection(filters=[{'column': 'id', 'op': 'execute', 'value': 'anything'}]),
                   self.selection(order_by=[{'column': 'id', 'direction': 'asc; DROP TABLE items'}])]
        for value in invalid:
            with self.subTest(value_keys=list(value)):
                self.assert_failure(lambda: adapters._validate_selection(value, columns))

    def test_result_checks_exact_columns_rows_scalar_types_and_limits(self):
        result = {'columns': ['id'], 'rows': [{'id': 'one'}], 'truncated': False, 'elapsed_ms': 2.0}
        self.assertEqual(adapters._validate_result(result, ['id'], 2)['rows'], [{'id': 'one'}])
        invalid = [{**result, 'columns': ['extra']}, {**result, 'rows': [{'id': 'one', 'secret': 'hidden'}]},
                   {**result, 'rows': [{}]}, {**result, 'rows': [{'id': {'nested': 'object'}}]},
                   {**result, 'rows': [{'id': ['array']}]}, {**result, 'rows': [{'id': math.nan}]},
                   {**result, 'rows': [{'id': b'bytes'}]}, {**result, 'rows': [{'id': 'x' * (16 * 1024 + 1)}]},
                   {**result, 'rows': [{'id': '\U0001f600' * 5000}]},
                   {**result, 'rows': [{'id': i} for i in range(3)]},
                   {**result, 'truncated': 'false'}, {**result, 'elapsed_ms': math.inf}]
        for value in invalid:
            with self.subTest(value_keys=list(value)):
                self.assert_failure(lambda: adapters._validate_result(value, ['id'], 2))
        large = {**result, 'rows': [{'id': 'x' * 16384} for _ in range(200)]}
        self.assert_failure(lambda: adapters._validate_result(large, ['id'], 200))

    def test_mcp_tool_schema_requires_exact_profile_and_expected_fields(self):
        for operation in ('discover', 'query'):
            good = tool(operation)
            adapters._validate_tool_schema(good, operation)
            bad = tool(operation)
            bad.name = 'execute_anything'
            self.assert_failure(lambda: adapters._validate_tool_schema(bad, operation))
            bad = tool(operation)
            bad.output_schema = None
            self.assert_failure(lambda: adapters._validate_tool_schema(bad, operation))
            bad = tool(operation)
            bad.input_schema['required'] = []
            self.assert_failure(lambda: adapters._validate_tool_schema(bad, operation))
            bad = tool(operation)
            del bad.output_schema['properties']['resources' if operation == 'discover' else 'rows']
            self.assert_failure(lambda: adapters._validate_tool_schema(bad, operation))

    def test_mcp_schema_rejects_expensive_or_recursive_validation(self):
        safe = {'type': 'object', 'properties': {'value': {
            'anyOf': [{'type': 'string'}, {'type': 'null'}]}}}
        adapters._validate_structural_schema(safe)
        deep = {'type': 'string'}
        for _ in range(22):
            deep = {'type': 'array', 'items': deep}
        too_many = {'type': 'object', 'properties': {
            'group' + str(group): {'type': 'object', 'properties': {
                'item' + str(item): {'type': 'string'} for item in range(60)}}
            for group in range(40)}}
        invalid = [{'type': 'string', 'pattern': '(a+)+$'},
                   {'oneOf': [{'type': 'string'}, {'type': 'integer'}]},
                   {'$ref': 'https://schema.example.test/remote.json'},
                   {'$defs': {'Node': {'type': 'object', 'properties': {'next': {'$ref': '#/$defs/Node'}}}},
                    '$ref': '#/$defs/Node'},
                   {'anyOf': [{'type': 'object', 'properties': {}}]}, deep, too_many]
        for schema in invalid:
            with self.subTest(keys=list(schema)):
                failure = self.assert_failure(lambda: adapters._validate_structural_schema(schema))
                self.assertEqual(failure.code, 'MCP_PROFILE_INCOMPATIBLE')

    def test_mcp_sdk_boundary_negotiates_profile_and_uses_only_registry_credentials(self):
        session = FakeMcpSession(resources=[resource(), resource('outside')])
        source = {**self.mcp, 'token': 'untrusted-incoming-token'}
        with self.mocked_mcp(session) as transports:
            discovered = adapters.discover(source)
        self.assertEqual([item['key'] for item in discovered], ['items'])
        self.assertEqual([name for name, _ in session.operations],
                         ['initialize', 'list_tools', 'ontology_source_discover'])
        self.assertEqual(session.operations[-1][1], {'contract_version': '1.0'})
        self.assertTrue(session.closed)
        self.assertEqual(len(transports), 1)
        endpoint, options = transports[0]
        self.assertEqual(endpoint, self.mcp['config']['endpoint'])
        client = options['http_client']
        self.assertEqual(client.headers['Authorization'], 'Bearer ' + self.refs['MCP_TEST']['token'])
        self.assertNotIn('untrusted-incoming-token', repr(client.headers))
        self.assertFalse(client.follow_redirects)
        self.assertEqual(client.headers['Accept-Encoding'], 'identity')
        self.assertEqual(options['max_sse_event_size'], adapters.MAX_WIRE_BYTES)

    def test_mcp_query_accepts_fixed_saved_query_and_rejects_nonprofile_output(self):
        saved = {**resource('items', kind='saved_query'), 'definition': 'Registered monthly totals',
                 'definition_version': 'immutable-v1'}
        session = FakeMcpSession(resources=[saved])
        with self.mocked_mcp(session):
            result = adapters.query(self.mcp, saved, self.selection())
        self.assertEqual(result['columns'], ['id', 'amount', 'observed_at'])
        self.assertEqual(result['rows'][0]['id'], 'value-id')
        calls = [(name, args) for name, args in session.operations if name.startswith('ontology_source_')]
        self.assertEqual([name for name, _ in calls],
                         ['ontology_source_discover', 'ontology_source_query', 'ontology_source_discover'])
        query_arguments = next(args for name, args in calls if name == 'ontology_source_query')
        self.assertEqual(set(query_arguments), {'contract_version', 'resource', 'selection'})
        self.assertEqual(query_arguments['resource'], 'items')
        self.assertNotIn('sql', query_arguments)
        for mode in ('wrong_version', 'only_text', 'remote_error', 'extra_column'):
            def response(name, payload):
                if mode == 'wrong_version':
                    return mcp_types.CallToolResult(content=[], structured_content={**payload, 'contract_version': '9.0'})
                if mode == 'only_text':
                    return mcp_types.CallToolResult(content=[mcp_types.TextContent(type='text', text=json.dumps(payload))])
                if mode == 'remote_error':
                    return mcp_types.CallToolResult(content=[mcp_types.TextContent(type='text', text=self.secret)], is_error=True)
                if name == 'ontology_source_query':
                    payload['rows'][0]['secret'] = 'unexpected'
                return mcp_types.CallToolResult(content=[], structured_content=payload)
            with self.subTest(mode=mode), self.mocked_mcp(FakeMcpSession(resources=[saved], response=response)):
                self.assert_failure(lambda: adapters.query(self.mcp, saved, self.selection()))

    def test_mcp_transport_and_session_errors_are_safe_and_timeout_is_explicit(self):
        for failure, status in ((RuntimeError('REMOTE_BODY ' + self.secret), 502),
                                (TimeoutError('REMOTE_SESSION ' + self.secret), 504),
                                (adapters.MCPError(adapters.REQUEST_TIMEOUT, 'REMOTE_TIMEOUT ' + self.secret), 504)):
            with self.subTest(kind=type(failure).__name__), self.mocked_mcp(FakeMcpSession(failure=failure)):
                error = self.assert_failure(lambda: adapters.discover(self.mcp))
            self.assertEqual(error.status, status)
            self.assertNotIn('REMOTE_', error.message)

    def test_mcp_query_revalidates_schema_before_reading_and_before_returning(self):
        saved = {**resource('items', kind='saved_query'), 'definition': 'Registered monthly totals',
                 'definition_version': 'v1'}
        for when in ('before', 'after'):
            for changed in ('column', 'definition', 'definition_version'):
                discoveries = 0

                def response(name, payload):
                    nonlocal discoveries
                    if name == 'ontology_source_discover':
                        discoveries += 1
                        if discoveries == (1 if when == 'before' else 2):
                            if changed == 'column':
                                payload['resources'][0]['columns'][1]['type'] = 'integer'
                            else:
                                payload['resources'][0][changed] = 'changed-v2'
                    return mcp_types.CallToolResult(content=[], structured_content=payload)

                session = FakeMcpSession(resources=[saved], response=response)
                with self.subTest(when=when, changed=changed), self.mocked_mcp(session):
                    failure = self.assert_failure(lambda: adapters.query(self.mcp, saved, self.selection()))
                self.assertEqual(failure.code, 'SOURCE_SCHEMA_DRIFT')
                self.assertEqual(failure.status, 409)
                calls = [name for name, _ in session.operations if name.startswith('ontology_source_')]
                self.assertEqual(calls, ['ontology_source_discover'] if when == 'before' else
                                 ['ontology_source_discover', 'ontology_source_query', 'ontology_source_discover'])
                self.assertTrue(session.closed)

    def test_limited_transport_rejects_redirect_compression_and_length_before_reading(self):
        observed = []

        async def run(status, headers):
            stream = ChunkStream([b'not consumed'])
            upstream = SimpleNamespace()

            async def handle(request):
                return adapters.httpx2.Response(status, headers=headers, stream=stream)

            async def close():
                pass

            upstream.handle_async_request = handle
            upstream.aclose = close
            budget = {'bytes': 0, 'failure': None}
            observed.append((stream, budget))
            with patch.object(adapters.httpx2, 'AsyncHTTPTransport', return_value=upstream):
                transport = adapters._LimitedTransport(budget)
                try:
                    await transport.handle_async_request(adapters.httpx2.Request('POST', 'https://example.test/mcp'))
                finally:
                    await transport.aclose()
            return stream, budget

        examples = [(302, {'Location': 'https://attacker.test/?token=' + self.secret}),
                    (200, {'Content-Encoding': 'gzip'}),
                    (200, {'Content-Length': str(adapters.MAX_WIRE_BYTES + 1)}),
                    (200, {'Content-Length': 'invalid'})]
        for status, headers in examples:
            with self.subTest(status=status, headers=list(headers)):
                self.assert_failure(lambda: asyncio.run(run(status, headers)))
            stream, budget = observed[-1]
            self.assertTrue(stream.closed)
            self.assertEqual(budget['bytes'], 0)
            self.assertIsInstance(budget['failure'], adapters.SourceFailure)

    def test_limited_stream_enforces_per_response_and_total_session_budgets(self):
        async def consume(stream):
            async for _ in stream:
                pass

        with patch.object(adapters, 'MAX_WIRE_BYTES', 8):
            budget = {'bytes': 0, 'failure': None}
            stream = ChunkStream([b'12345', b'6789'])
            self.assert_failure(lambda: asyncio.run(consume(adapters._LimitedStream(stream, budget))))
            self.assertIsInstance(budget['failure'], adapters.SourceFailure)
            budget = {'bytes': 0, 'failure': None}
            for _ in range(4):
                asyncio.run(consume(adapters._LimitedStream(ChunkStream([b'12345678']), budget)))
            self.assertEqual(budget['bytes'], 32)
            self.assert_failure(lambda: asyncio.run(consume(adapters._LimitedStream(ChunkStream([b'1']), budget))))
            closing_stream = ChunkStream([])
            asyncio.run(adapters._LimitedStream(closing_stream, {'bytes': 0, 'failure': None}).aclose())
            self.assertTrue(closing_stream.closed)

    def test_remote_logging_context_redacts_child_tasks_and_restores_other_logs(self):
        output = io.StringIO()
        handler = logging.StreamHandler(output)
        root = logging.getLogger()
        root.addHandler(handler)

        async def remote_child():
            try:
                raise RuntimeError('traceback-secret:' + self.secret)
            except RuntimeError:
                logging.getLogger('mcp.client').error('upstream %s', self.secret, exc_info=True, stack_info=True)
            logging.getLogger('httpx2').error('https://example.test/?token=%s', self.secret)

        async def remote_operation():
            await asyncio.create_task(remote_child())
            logging.getLogger('ontology.application').error('unrelated-application-marker')

        try:
            adapters._boundary(lambda: asyncio.run(remote_operation()))
            logging.getLogger('mcp.client').error('outside-operation-marker')
        finally:
            root.removeHandler(handler)
        logged = output.getvalue()
        self.assertNotIn(self.secret, logged)
        self.assertNotIn('traceback-secret', logged)
        self.assertNotIn('https://example.test/', logged)
        self.assertIn('unrelated-application-marker', logged)
        self.assertIn('outside-operation-marker', logged)

    def test_mysql_query_is_parameterized_readonly_bounded_and_closes(self):
        source = deepcopy(self.mysql)
        source['config']['allowed_objects'] = ['items`quoted']
        selected = mysql_resource('items`quoted')
        when = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        connection = FakeConnection([{'id': 'one', 'amount': Decimal('12.30'), 'observed_at': when},
                                     {'id': 'two', 'amount': None, 'observed_at': date(2026, 1, 3)},
                                     {'id': 'three', 'amount': Decimal('1.00'), 'observed_at': None}], resources=[selected])
        injection = "x' OR 1=1; --"
        with patch.object(adapters, '_TLSRequiredConnection', return_value=connection) as connect:
            result = adapters.query(source, selected, self.selection(
                filters=[{'column': 'id', 'op': 'eq', 'value': injection}],
                order_by=[{'column': 'id', 'direction': 'desc'}]))
        self.assertTrue(connection.closed)
        self.assertTrue(result['truncated'])
        self.assertEqual(len(result['rows']), 2)
        self.assertEqual(result['rows'][0]['amount'], '12.30')
        self.assertEqual(result['rows'][0]['observed_at'], when.isoformat())
        self.assertEqual(result['rows'][1]['observed_at'], '2026-01-03')
        statements = '\n'.join(sql for sql, _ in connection.statements).upper()
        self.assertIn('START TRANSACTION READ ONLY', statements)
        self.assertIn('MAX_EXECUTION_TIME', statements)
        select = [(sql, params) for sql, params in connection.statements
                  if sql.lstrip().upper().startswith('SELECT') and 'INFORMATION_SCHEMA' not in sql.upper()
                  and not sql.rstrip().upper().endswith('LIMIT 0')]
        self.assertEqual(len(select), 1)
        self.assertIn('`items``quoted`', select[0][0])
        self.assertNotIn(injection, select[0][0])
        self.assertIn(injection, select[0][1])
        self.assertTrue(3 in select[0][1] or 3 in connection.fetch_sizes)
        options = connect.call_args.kwargs
        self.assertLessEqual(options['connect_timeout'], 5)
        self.assertLessEqual(options['read_timeout'], 5)
        self.assertLessEqual(options['write_timeout'], 5)
        tls = options.get('ssl')
        self.assertIsNotNone(tls)
        if isinstance(tls, ssl.SSLContext):
            self.assertEqual(tls.verify_mode, ssl.CERT_REQUIRED)
            self.assertTrue(tls.check_hostname)

    def test_mysql_rejects_missing_server_tls_before_authentication(self):
        for tls, capabilities in ((False, adapters.CLIENT.SSL), (True, 0)):
            connection = object.__new__(adapters._TLSRequiredConnection)
            connection.ssl = tls
            connection.server_capabilities = capabilities
            connection._sock = connection._rfile = None
            with self.subTest(tls=tls, capabilities=capabilities), \
                 patch.object(pymysql.connections.Connection, '_request_authentication') as authenticate:
                failure = self.assert_failure(connection._request_authentication)
                authenticate.assert_not_called()
            self.assertEqual(failure.code, 'SOURCE_TLS_REQUIRED')
        connection = object.__new__(adapters._TLSRequiredConnection)
        connection.ssl = True
        connection.server_capabilities = adapters.CLIENT.SSL
        connection._sock = connection._rfile = None
        with patch.object(pymysql.connections.Connection, '_request_authentication', return_value='authenticated') as authenticate:
            self.assertEqual(connection._request_authentication(), 'authenticated')
            authenticate.assert_called_once()

    def test_mysql_plaintext_requires_explicit_registry_opt_in(self):
        self.refs['MYSQL_TEST']['allow_insecure'] = True
        self.write_registry()
        connection = FakeConnection()
        with patch.object(adapters.pymysql, 'connect', return_value=connection) as connect, \
             patch.object(adapters, '_TLSRequiredConnection') as tls_connect:
            adapters.discover(self.mysql)
        tls_connect.assert_not_called()
        self.assertTrue(connect.call_args.kwargs['ssl_disabled'])
        self.assertNotIn('ssl', connect.call_args.kwargs)

    def test_mysql_query_blocks_column_or_view_definition_drift_before_data_select(self):
        for kind, change in (('table', 'column'), ('view', 'definition'), ('view', 'unverifiable')):
            selected = mysql_resource(kind=kind)
            current = deepcopy(selected)
            if change == 'column':
                current['columns'][1]['type'] = 'integer'
            elif change == 'definition':
                current['definition_version'] = 'b' * 64
            else:
                current.pop('definition_version')
            connection = FakeConnection(resources=[current])
            with self.subTest(kind=kind, change=change), \
                 patch.object(adapters, '_TLSRequiredConnection', return_value=connection):
                failure = self.assert_failure(lambda: adapters.query(self.mysql, selected, self.selection()))
            self.assertEqual(failure.code, 'SOURCE_VIEW_UNVERIFIABLE' if change == 'unverifiable' else 'SOURCE_SCHEMA_DRIFT')
            self.assertTrue(connection.closed)
            reads = [sql for sql, _ in connection.statements if sql.lstrip().upper().startswith('SELECT')
                     and 'INFORMATION_SCHEMA' not in sql.upper()]
            self.assertEqual(len(reads), 1)
            self.assertTrue(reads[0].endswith('LIMIT 0'))

    def test_mysql_oversized_cell_closes_connection_before_draining_cursor(self):
        selected = mysql_resource(columns=['id'])
        connection = FakeConnection([{'id': 'x' * (16 * 1024 + 1)}], resources=[selected])
        with patch.object(adapters, '_TLSRequiredConnection', return_value=connection):
            self.assert_failure(lambda: adapters.query(self.mysql, selected, self.selection(columns=['id'])))
        first_close = connection.events.index('connection.close')
        self.assertEqual(connection.events[first_close + 1], 'cursor.close')
        self.assertTrue(connection.closed)

    def test_mysql_discovery_is_metadata_only_and_allowlisted(self):
        connection = FakeConnection()
        with patch.object(adapters, '_TLSRequiredConnection', return_value=connection):
            resources = adapters.discover(self.mysql)
        self.assertTrue(connection.closed)
        self.assertEqual({item['key'] for item in resources}, {'items', 'summary_view'})
        self.assertEqual({item['kind'] for item in resources}, {'table', 'view'})
        queries = [(sql, params) for sql, params in connection.statements if sql.lstrip().upper().startswith('SELECT')]
        self.assertTrue(queries)
        self.assertTrue(all('INFORMATION_SCHEMA' in sql.upper() for sql, _ in queries))
        self.assertTrue(any('business' in params for _, params in queries))
        self.assertTrue(any('items' in params for _, params in queries))
        self.assertEqual(resources[0]['primary_key'], ['id'])

    def test_query_rejects_disallowed_resource_before_opening_connection(self):
        with patch.object(adapters, '_TLSRequiredConnection') as connect:
            self.assert_failure(lambda: adapters.query(self.mysql, resource('not_allowed'), self.selection()))
            connect.assert_not_called()

    def test_mysql_upstream_errors_are_sanitized_without_raw_logs(self):
        output = io.StringIO()
        handler = logging.StreamHandler(output)
        logger = logging.getLogger()
        logger.addHandler(handler)
        try:
            error = pymysql.OperationalError(1045, 'Access denied at db.example.test: ' + self.secret)
            with patch.object(adapters, '_TLSRequiredConnection', side_effect=error):
                failure = self.assert_failure(lambda: adapters.discover(self.mysql))
            self.assertNotIn('db.example.test', failure.message)
            self.assertNotIn(self.secret, output.getvalue())
        finally:
            logger.removeHandler(handler)

    def test_authorization_failures_are_not_operational_partial_candidates(self):
        for number in (1044, 1045, 1142, 1143, 1227, 2026):
            failure = pymysql.OperationalError(number, 'PRIVATE_SOURCE_TEXT')
            with self.subTest(number=number), patch.object(adapters, '_TLSRequiredConnection', side_effect=failure):
                error = self.assert_failure(lambda: adapters.discover(self.mysql))
                self.assertEqual((error.code, error.status), ('SOURCE_UPSTREAM_AUTH', 403))
        wrapped = pymysql.OperationalError(2003, 'PRIVATE_SOURCE_TEXT')
        wrapped.original_exception = ssl.SSLCertVerificationError('PRIVATE_CERTIFICATE_TEXT')
        with patch.object(adapters, '_TLSRequiredConnection', side_effect=wrapped):
            error = self.assert_failure(lambda: adapters.discover(self.mysql))
            self.assertEqual((error.code, error.status), ('SOURCE_UPSTREAM_AUTH', 403))

    def test_http_source_auth_and_service_outage_are_distinguished_before_body(self):
        class Remote(adapters.httpx2.AsyncBaseTransport):
            def __init__(self, status): self.status = status
            async def handle_async_request(self, request):
                return adapters.httpx2.Response(self.status, content=b'PRIVATE_UPSTREAM_BODY')
        async def check(status):
            budget = {'bytes': 0, 'failure': None}
            transport = adapters._LimitedTransport(budget)
            await transport.transport.aclose()
            transport.transport = Remote(status)
            try:
                with self.assertRaises(adapters.SourceFailure) as caught:
                    await transport.handle_async_request(adapters.httpx2.Request('POST', 'https://source.example.test/mcp'))
                error = caught.exception
                expected = ('SOURCE_UPSTREAM_AUTH', 403) if status in (401, 403) else ('SOURCE_UPSTREAM_UNAVAILABLE', 503)
                self.assertEqual((error.code, error.status), expected)
                self.assertNotIn('PRIVATE', str(error))
            finally:
                await transport.aclose()
        for status in (401, 403, 429, 500, 503):
            with self.subTest(status=status): asyncio.run(check(status))

    def test_mysql_repeated_query_has_no_result_cache(self):
        selected = mysql_resource(columns=['id'])
        connections = [FakeConnection([{'id': 'first'}], resources=[selected]), FakeConnection([{'id': 'second'}], resources=[selected])]
        with patch.object(adapters, '_TLSRequiredConnection', side_effect=connections) as connect:
            first = adapters.query(self.mysql, selected, self.selection(columns=['id']))
            second = adapters.query(self.mysql, selected, self.selection(columns=['id']))
        self.assertEqual(first['rows'], [{'id': 'first'}])
        self.assertEqual(second['rows'], [{'id': 'second'}])
        self.assertEqual(connect.call_count, 2)
        self.assertTrue(all(connection.closed for connection in connections))


if __name__ == '__main__':
    unittest.main()
