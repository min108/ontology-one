"""Authorization-boundary regressions for the federation query layer.

Uses isolated SQLite fixtures behind the same query adapter; deployment tests
exercise the real MySQL session, grants, read-only transactions and latency.
"""
import json
from itertools import zip_longest
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from backend import federation
from backend.db import Row


class FederationBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.tenants = {
            'dept-01': {'id': 'dept-01', 'name': 'A Department', 'category': 'headquarters_department',
                        'org_id': 'dept-01', 'status': 'active', 'db_name': 'a', 'provisioned': True, 'visibility': 'private'},
            'dept-02': {'id': 'dept-02', 'name': 'B Department', 'category': 'headquarters_department',
                        'org_id': 'dept-02', 'status': 'active', 'db_name': 'b', 'provisioned': True, 'visibility': 'published'},
        }
        self.principal = SimpleNamespace(tenant_id='dept-01', home_tenant_id='dept-01', role='modeler',
                                         readonly=False, user_id='user-a', is_superadmin=False)
        for tenant in self.tenants.values():
            connection = sqlite3.connect(self._path(tenant['id']))
            connection.executescript('''
                CREATE TABLE entities(id TEXT PRIMARY KEY,kind TEXT,title TEXT,summary TEXT,domain TEXT,org_id TEXT,
                                      status TEXT,source_kind TEXT,payload TEXT,created_at TEXT,updated_at TEXT,version INTEGER);
                CREATE TABLE entity_access(entity_id TEXT PRIMARY KEY,visibility TEXT,updated_by TEXT,updated_at TEXT);
                CREATE TABLE relations(id TEXT PRIMARY KEY,source TEXT,target TEXT,type TEXT,label TEXT,status TEXT,payload TEXT);
            ''')
            connection.close()
        self.add('dept-01', 'same', 'knowledge', 'Own private article', 'draft', 'institution')
        payload = {'content': 'Published article body', 'api_key': 'secret', 'source_path': '/private/doc',
                   'source_refs': [{'material_id': 'private-source', 'title': 'HIDDEN SOURCE NAME', 'quote': 'HIDDEN QUOTE'}],
                   'article_sections': [{'id': 'one', 'title': 'Reading', 'content': 'Visible lesson',
                                         'source_refs': [{'material_id': 'private-source', 'title': 'HIDDEN SOURCE NAME', 'quote': 'HIDDEN QUOTE'}]}]}
        self.add('dept-02', 'same', 'knowledge', 'Shared published article', 'published', 'headquarters', payload)
        self.add('dept-02', 'draft', 'knowledge', 'HIDDEN DRAFT', 'draft', 'headquarters')
        self.add('dept-02', 'private', 'knowledge', 'HIDDEN PRIVATE', 'published', 'institution')
        self.add('dept-02', 'private-source', 'material', 'HIDDEN SOURCE NAME', 'published', 'headquarters', {'original_text': 'HIDDEN QUOTE'})
        connection = sqlite3.connect(self._path('dept-02'))
        connection.execute("INSERT INTO relations VALUES('rel','same','private-source','derived_from','HIDDEN RELATION','confirmed','{}')")
        connection.commit()
        connection.close()
        self.patches = [
            patch.object(federation, '_scope', side_effect=lambda: (self.principal, list(self.tenants.values()))),
            patch.object(federation.db, 'tenant_connection', side_effect=self.connection),
            patch.object(federation, '_central_links', return_value=[]),
            patch.object(federation.store, 'authorized_read_tenants', side_effect=lambda principal: list(self.tenants.values())),
        ]
        for entry in self.patches:
            entry.start()

    def tearDown(self):
        for entry in reversed(self.patches):
            entry.stop()
        self.temp.cleanup()

    def _path(self, tenant_id):
        return str(Path(self.temp.name) / (tenant_id + '.sqlite'))

    @contextmanager
    def connection(self, tenant_id, readonly=True):
        if tenant_id not in self.tenants:
            raise HTTPException(403, 'revoked')
        connection = sqlite3.connect(self._path(tenant_id))
        connection.row_factory = lambda cursor, row: Row(dict(zip([column[0] for column in cursor.description], row)))
        connection.execute('PRAGMA query_only=ON')
        try:
            yield connection
        finally:
            connection.close()

    def add(self, tenant, ident, kind, title, status, visibility, payload=None):
        connection = sqlite3.connect(self._path(tenant))
        connection.execute('INSERT INTO entities VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                           (ident, kind, title, title, 'Business', tenant, status, 'user', json.dumps(payload or {}), '2026', '2026', 1))
        connection.execute('INSERT INTO entity_access VALUES(?,?,?,?)', (ident, visibility, 'owner', '2026'))
        connection.commit()
        connection.close()

    def search(self, **kwargs):
        return federation.search(**{'q': '', 'kind': '', 'status': '', 'tenant_id': '', 'page': 1, 'page_size': 30, **kwargs})

    def test_department_sees_only_shared_published_assets(self):
        result = self.search()
        self.assertEqual(result['total'], 2)
        self.assertEqual({(item['tenant_id'], item['id']) for item in result['items']}, {('dept-01', 'same'), ('dept-02', 'same')})
        self.assertNotIn('HIDDEN', json.dumps(result))
        self.assertEqual(self.search(q='HIDDEN')['total'], 0)
        self.assertEqual(self.search(page=2, page_size=1)['items'][0]['tenant_id'], 'dept-02')

    def test_nested_hidden_citations_and_raw_metadata_are_removed(self):
        result = federation.entity_detail('dept-02', 'same')
        encoded = json.dumps(result)
        for private in ('HIDDEN', 'secret', '/private/doc', 'private-source'):
            self.assertNotIn(private, encoded)
        self.assertEqual(result['entity']['payload']['content'], 'Published article body')
        self.assertEqual(result['hidden_reference_count'], 2)
        self.assertEqual(result['relations'], {'incoming': [], 'outgoing': []})
        self.assertFalse(result['can_set_visibility'])

    def test_private_or_unpublished_details_are_not_addressable(self):
        for ident in ('private', 'draft', 'private-source'):
            with self.assertRaises(HTTPException) as error:
                federation.entity_detail('dept-02', ident)
            self.assertEqual(error.exception.status_code, 404)

    def test_revoke_is_reflected_without_cached_results(self):
        self.assertEqual(self.search()['total'], 2)
        self.tenants.pop('dept-02')
        self.assertEqual(self.search()['total'], 1)
        with self.assertRaises(HTTPException) as error:
            federation.entity_detail('dept-02', 'same')
        self.assertEqual(error.exception.status_code, 404)

    def test_graph_identifiers_do_not_merge_same_named_entities(self):
        result = federation.graph(tenant_id='', kind='', q='', limit=100)
        self.assertEqual(len(result['nodes']), 2)
        self.assertEqual(len({node['id'] for node in result['nodes']}), 2)
        self.assertEqual(len(result['roots']), 2)
        self.assertEqual(result['edges'], [])
        self.assertNotIn('HIDDEN', json.dumps(result))

    def test_graph_global_budget_preserves_round_robin_and_fetches_only_selected_edges(self):
        for tenant in self.tenants:
            visibility = 'institution' if tenant == 'dept-01' else 'headquarters'
            for ident in ('a', 'b'):
                self.add(tenant, ident, 'knowledge', ident, 'published', visibility)
            with sqlite3.connect(self._path(tenant)) as connection:
                connection.executemany('INSERT INTO relations VALUES(?,?,?,?,?,?,?)', [
                    ('ab', 'a', 'b', 'references', 'A to B', 'confirmed', '{}'),
                    ('bs', 'b', 'same', 'references', 'B to same', 'confirmed', '{}')])
        complete = federation.graph(tenant_id='', kind='', q='', limit=100)
        per_tenant = [[node['id'] for node in complete['nodes'] if node['tenant_id'] == tenant]
                      for tenant in self.tenants]
        expected_order = [ident for row in zip_longest(*per_tenant) for ident in row if ident is not None]
        self.assertEqual(len(complete['edges']), 4)
        original = federation._local_relations
        for limit in (1, 3, 4, 6, 100):
            with self.subTest(limit=limit), patch.object(federation, '_local_relations', wraps=original) as edge_queries:
                result = federation.graph(tenant_id='', kind='', q='', limit=limit)
            selected = set(expected_order[:limit])
            self.assertEqual([node['id'] for node in result['nodes']], expected_order[:limit])
            self.assertEqual(result['truncated'], len(expected_order) > limit)
            self.assertEqual(result['roots'], complete['roots'])
            self.assertEqual(result['edges'], [edge for edge in complete['edges']
                                             if edge['source'] in selected and edge['target'] in selected])
            actual_scope = {call.args[0]['id']: set(call.args[2]) for call in edge_queries.call_args_list}
            expected_scope = {}
            for node in result['nodes']:
                expected_scope.setdefault(node['tenant_id'], set()).add(node['entity_id'])
            self.assertEqual(actual_scope, expected_scope)

    def test_unselected_edges_do_not_consume_selected_graph_edge_limit(self):
        for ident in ('chosen-a', 'chosen-b', 'zz-outside'):
            self.add('dept-01', ident, 'knowledge', ident, 'draft', 'institution')
        with sqlite3.connect(self._path('dept-01')) as connection:
            connection.executemany('INSERT INTO relations VALUES(?,?,?,?,?,?,?)', [
                (f'irrelevant-{index:04d}', 'same', 'zz-outside', 'references', 'Outside graph', 'confirmed', '{}')
                for index in range(1001)])
            connection.execute('INSERT INTO relations VALUES(?,?,?,?,?,?,?)',
                               ('zz-selected', 'chosen-a', 'chosen-b', 'references', 'Selected edge', 'confirmed', '{}'))
        result = federation.graph(tenant_id='dept-01', kind='', q='', limit=2)
        self.assertEqual([node['entity_id'] for node in result['nodes']], ['chosen-a', 'chosen-b'])
        self.assertEqual([edge['id'] for edge in result['edges']], ['relation:dept-01:zz-selected'])
        self.assertTrue(result['truncated'])

    def test_selected_relation_endpoints_still_require_live_publication(self):
        self.add('dept-02', 'peer', 'knowledge', 'Public peer', 'published', 'headquarters')
        with sqlite3.connect(self._path('dept-02')) as connection:
            connection.executemany('INSERT INTO relations VALUES(?,?,?,?,?,?,?)', [
                ('visible', 'same', 'peer', 'references', 'Public edge', 'confirmed', '{}'),
                ('private', 'same', 'private', 'references', 'HIDDEN private edge', 'confirmed', '{}'),
                ('draft', 'same', 'draft', 'references', 'HIDDEN draft edge', 'confirmed', '{}'),
                ('rejected', 'same', 'peer', 'references', 'HIDDEN rejected edge', 'rejected', '{}')])
        selected = {'same', 'peer', 'private', 'draft', 'private-source'}
        edges = federation._local_relations(self.tenants['dept-02'], None, selected)
        self.assertEqual([edge['id'] for edge in edges], ['relation:dept-02:visible'])
        with sqlite3.connect(self._path('dept-02')) as connection:
            connection.execute("UPDATE entity_access SET visibility='institution' WHERE entity_id='peer'")
        self.assertEqual(federation._local_relations(self.tenants['dept-02'], None, selected), [])
        self.assertEqual(federation._local_relations(self.tenants['dept-02'], None, set()), [])

    def test_graph_relation_failure_is_reported_and_scope_is_still_revalidated(self):
        original = federation._local_relations

        def unavailable(tenant, *args):
            if tenant['id'] == 'dept-02':
                raise OSError('fixture connection unavailable')
            return original(tenant, *args)

        with patch.object(federation, '_local_relations', side_effect=unavailable), \
             patch.object(federation, '_assert_scope_current', wraps=federation._assert_scope_current) as final_check:
            result = federation.graph(tenant_id='', kind='', q='', limit=100)
        self.assertEqual(len(result['nodes']), 2)
        self.assertTrue(result['partial'])
        self.assertEqual(result['unavailable_tenants'], ['dept-02'])
        final_check.assert_called_once()

    def test_cross_tenant_reference_cannot_be_relabelled_as_local(self):
        payload = {'source_refs': [{'tenant_id': 'private-tenant', 'material_id': 'same', 'quote': 'HIDDEN QUOTE'}]}
        visible = {'same': {'org_id': 'dept-02', 'detail_url': '/safe', 'title': 'Visible'}}
        result, hidden = federation._safe_payload(payload, self.tenants['dept-02'], visible)
        self.assertEqual(result['source_refs'], [])
        self.assertEqual(hidden, 1)

    def test_headquarters_business_survives_without_institution_root(self):
        self.principal.home_tenant_id = 'hq'
        self.tenants['dept-01']['category'] = 'headquarters'
        self.add('dept-01', 'dept-01', 'organization', 'HQ institution', 'published', 'institution')
        result = federation.graph(tenant_id='', kind='', q='', limit=100)
        hq_nodes = [node for node in result['nodes'] if node['tenant_id'] == 'dept-01']
        self.assertEqual([node['entity_id'] for node in hq_nodes], ['same'])
        self.assertIsNone(hq_nodes[0]['parent_id'])
        self.assertNotIn('dept-01', [root['tenant_id'] for root in result['roots']])

    def test_subsidiary_is_not_an_audience_for_headquarters_publication(self):
        self.tenants['dept-01']['category'] = 'subsidiary'
        scoped = federation._audience_scope(self.principal, list(self.tenants.values()))
        self.assertEqual([tenant['id'] for tenant in scoped], ['dept-01'])

    def test_mid_request_grant_downgrade_rejects_broader_snapshot(self):
        self.principal.home_tenant_id = 'hq'
        old = dict(self.tenants['dept-02'], visibility='private')
        with self.assertRaises(HTTPException) as error:
            federation._assert_scope_current(self.principal, [old])
        self.assertEqual(error.exception.status_code, 403)

    def test_unprovisioned_tenant_is_skipped(self):
        self.tenants['dept-02']['provisioned'] = False
        result = self.search()
        self.assertEqual(result['total'], 1)
        self.assertFalse(result['partial'])


if __name__ == '__main__':
    unittest.main()
