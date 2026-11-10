"""Opt-in MySQL federation integration test with real sessions and grants.

RUN_FEDERATION_MYSQL_TESTS=1 MYSQL_HOST=... MYSQL_PORT=... MYSQL_USER=...
MYSQL_PASSWORD=... python -m unittest discover -s tests -p test_federation_mysql.py

Creates a random, isolated database namespace and drops only that namespace.
Credentials are read from the environment and never printed or stored in files.
"""
import json
import os
import re
import secrets
import unittest
from contextlib import closing

import pymysql
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import db, federation
from backend.security import store
from backend.security.context import use_context
from backend.security.middleware import SecurityMiddleware


@unittest.skipUnless(os.environ.get('RUN_FEDERATION_MYSQL_TESTS') == '1', 'opt-in MySQL integration test')
class FederationMySQLTests(unittest.TestCase):
    def test_real_sessions_visibility_citations_links_and_revocation(self):
        previous_db = os.environ.get('MYSQL_DATABASE')
        previous_url = os.environ.pop('ONTOLOGY_MYSQL_URL', None)
        base = 'ontology_fedtest_' + secrets.token_hex(5)
        os.environ['MYSQL_DATABASE'] = base
        database_names = {base + '_control'}
        clients = []
        try:
            store.initialize_control()
            database_names.update(tenant['db_name'] for tenant in store.list_tenants())
            federation.initialize_federation()
            initial = 'Fixture1-' + secrets.token_hex(12)
            replacement = 'Fixture2-' + secrets.token_hex(12)
            store.bootstrap_superadmin('fixture-admin', initial)
            raw = store.login('fixture-admin', initial, '127.0.0.1', 'federation-test')
            admin = store.authenticate_session(raw, 'federation-test')[0]
            raw = store.change_password(admin, initial, replacement, '127.0.0.1', 'federation-test')
            admin = store.authenticate_session(raw, 'federation-test')[0]
            app = FastAPI()
            app.include_router(federation.router, prefix='/api')
            app.add_middleware(SecurityMiddleware)

            def client(token):
                c = TestClient(app, base_url='http://127.0.0.1:8765')
                c.cookies.set('ontology_session', token)
                c.headers.update({'x-csrf-token': store.csrf_token(token), 'origin': 'http://127.0.0.1:8765'})
                clients.append(c)
                return c

            identities = {}
            for username, tenant, role in [('dept-a', 'dept-01', 'modeler'), ('dept-b', 'dept-02', 'modeler'),
                                           ('subsidiary', 'sub-01', 'modeler'), ('hq-builder', 'hq', 'modeler'), ('hq-reviewer', 'hq', 'reviewer')]:
                password = 'Fixture3-' + secrets.token_hex(12)
                uid = store.create_user(admin, username=username, display_name=username, tenant_id=tenant,
                                        role=role, password=password, must_change_password=False)
                token = store.login(username, password, '127.0.0.1', 'federation-test')
                principal = store.authenticate_session(token, 'federation-test')[0]
                identities[username] = (uid, principal, client(token))
            for tenant in ('hq', 'dept-01', 'dept-02', 'sub-01'):
                db.ensure_tenant_schema(tenant)

            def add(username, ident, kind, title, status, visibility, payload=None):
                principal = identities[username][1]
                with use_context(principal), closing(db.connect()) as connection, connection:
                    connection.execute('INSERT INTO entities VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                                       (ident, kind, title, title, 'Test', principal.tenant_id, status, 'user',
                                        json.dumps(payload or {}, ensure_ascii=False), db.now(), db.now(), 1))
                    connection.execute('INSERT INTO entity_access VALUES(?,?,?,?)', (ident, visibility, principal.user_id, db.now()))

            add('dept-a', 'same', 'knowledge', 'Own article', 'published', 'institution')
            add('hq-builder', 'hq-asset', 'knowledge', 'HQ business asset', 'published', 'institution')
            payload = {'content': 'Visible business content', 'source_path': '/secret/file', 'api_key': 'SECRET_KEY',
                       'comments': [{'body': 'SECRET_COMMENT'}], 'raw_rows': [{'value': 'SECRET_ROW'}],
                       'source_refs': [{'material_id': 'private-source', 'title': 'HIDDEN SOURCE TITLE', 'quote': 'HIDDEN SOURCE QUOTE'}],
                       'article_sections': [{'id': 'part', 'title': 'Visible section', 'content': 'Visible lesson',
                                             'source_refs': [{'material_id': 'private-source', 'quote': 'HIDDEN SOURCE QUOTE'}]}]}
            add('dept-b', 'same', 'knowledge', 'Shared article', 'published', 'headquarters', payload)
            add('dept-b', 'private', 'knowledge', 'HIDDEN PRIVATE', 'published', 'institution')
            add('dept-b', 'draft', 'knowledge', 'HIDDEN DRAFT', 'draft', 'headquarters')
            add('dept-b', 'private-source', 'material', 'HIDDEN SOURCE TITLE', 'published', 'headquarters', {'original_text': 'HIDDEN SOURCE QUOTE'})
            with use_context(identities['dept-b'][1]), closing(db.connect()) as connection, connection:
                connection.execute('INSERT INTO relations VALUES(?,?,?,?,?,?,?)', ('hidden-link', 'same', 'private-source', 'derived_from', 'HIDDEN LINK', 'confirmed', '{}'))
            a, b, subsidiary = (identities[key][2] for key in ('dept-a', 'dept-b', 'subsidiary'))
            builder, reviewer = (identities[key][2] for key in ('hq-builder', 'hq-reviewer'))
            result = a.get('/api/federation/search?visibility=private&include_history=true').json()
            self.assertEqual({(e['tenant_id'], e['id']) for e in result['items']}, {('dept-01', 'dept-01'), ('dept-01', 'same'), ('dept-02', 'same')})
            self.assertNotIn('HIDDEN', json.dumps(result))
            self.assertEqual(a.get('/api/federation/search?q=HIDDEN').json()['total'], 0)
            detail = a.get('/api/federation/entities/dept-02/same')
            self.assertEqual(detail.status_code, 200, detail.text)
            for hidden in ('HIDDEN', 'SECRET_', '/secret/file', 'private-source'):
                self.assertNotIn(hidden, detail.text)
            self.assertEqual(detail.json()['hidden_reference_count'], 2)
            for ident in ('private', 'draft', 'private-source'):
                self.assertEqual(a.get('/api/federation/entities/dept-02/' + ident).status_code, 404)
            self.assertEqual(a.head('/api/federation/entities/dept-02/private').status_code, 405)
            self.assertEqual(a.head('/api/federation/entities/dept-02/private').content, b'')
            self.assertEqual(a.post('/api/federation/entities/dept-02/same/visibility', json={'visibility': 'institution'}).status_code, 403)
            self.assertEqual(b.post('/api/federation/entities/dept-02/same/visibility', json={'visibility': 'institution'}).status_code, 200)
            self.assertEqual(a.get('/api/federation/entities/dept-02/same').status_code, 404)
            self.assertEqual(b.post('/api/federation/entities/dept-02/same/visibility', json={'visibility': 'headquarters'}).status_code, 200)
            self.assertEqual(subsidiary.get('/api/federation/search?tenant_id=dept-02').status_code, 404)
            self.assertEqual(subsidiary.get('/api/federation/entities/dept-02/same').status_code, 404)
            hq_detail = builder.get('/api/federation/entities/dept-02/same')
            self.assertEqual(hq_detail.status_code, 200, hq_detail.text)
            self.assertIn('HIDDEN SOURCE QUOTE', hq_detail.text)  # Explicit HQ private-read grant.
            for hidden in ('SECRET_', '/secret/file'):
                self.assertNotIn(hidden, hq_detail.text)
            graph = builder.get('/api/federation/graph?limit=100').json()
            self.assertNotIn('hq', [root['tenant_id'] for root in graph['roots']])
            self.assertTrue(any(node['entity_id'] == 'hq-asset' and node['parent_id'] is None for node in graph['nodes']))
            self.assertEqual(len([node for node in graph['nodes'] if node['entity_id'] == 'same']), 2)
            link_body = {'source_tenant_id': 'dept-01', 'source_entity_id': 'same', 'target_tenant_id': 'dept-02',
                         'target_entity_id': 'same', 'relation_type': 'references', 'label': 'Cross-reference', 'reason': 'Integration fixture'}
            created = builder.post('/api/federation/links', json=link_body)
            self.assertEqual(created.status_code, 200, created.text)
            link_id = created.json()['id']
            self.assertEqual(created.json()['status'], 'pending')
            self.assertFalse(any(edge['cross_tenant'] for edge in builder.get('/api/federation/graph').json()['edges']))
            self.assertEqual(builder.patch('/api/federation/links/' + link_id, json={'status': 'confirmed', 'reason': 'Self-review'}).status_code, 403)
            reviewed = reviewer.patch('/api/federation/links/' + link_id, json={'status': 'confirmed', 'reason': 'Reviewed fixture'})
            self.assertEqual(reviewed.status_code, 200, reviewed.text)
            self.assertTrue(any(edge['cross_tenant'] for edge in builder.get('/api/federation/graph').json()['edges']))
            grant = next(g for g in store.list_grants(admin) if g['user_id'] == identities['hq-builder'][0] and g['tenant_id'] == 'dept-02')
            store.revoke_grant(admin, grant['id'])
            self.assertFalse(any(edge['cross_tenant'] for edge in builder.get('/api/federation/graph').json()['edges']))
            self.assertEqual(builder.get('/api/federation/entities/dept-02/same').status_code, 404)
            overview = a.get('/api/federation/overview')
            self.assertEqual(overview.status_code, 200, overview.text)
            self.assertFalse(overview.json()['partial'])
            self.assertTrue(any(not tenant['provisioned'] for tenant in overview.json()['tenants']))
            self.assertEqual(a.get('/api/federation/overview', headers={'x-tenant-id': 'dept-02'}).status_code, 403)
            with store.control_connection() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT action FROM sec_audit WHERE action LIKE 'federation_%%' OR action='entity_visibility'")
                actions = {row['action'] for row in cursor.fetchall()}
            self.assertTrue({'federation_link_create', 'federation_link_review', 'entity_visibility'} <= actions)
        finally:
            for current in clients:
                current.close()
            with db._POOL_LOCK:
                for key in list(db._POOLS):
                    if key[-1] in database_names:
                        pool = db._POOLS.pop(key)
                        while not pool.empty():
                            pool.get_nowait().close()
            settings = db.mysql_config()
            settings.pop('database', None)
            with pymysql.connect(**settings) as connection, connection.cursor() as cursor:
                for name in database_names:
                    assert name.startswith(base) and re.fullmatch('[A-Za-z0-9_]+', name)
                    cursor.execute(f'DROP DATABASE IF EXISTS `{name}`')
            if previous_db is None:
                os.environ.pop('MYSQL_DATABASE', None)
            else:
                os.environ['MYSQL_DATABASE'] = previous_db
            if previous_url is not None:
                os.environ['ONTOLOGY_MYSQL_URL'] = previous_url


if __name__ == '__main__':
    unittest.main()
