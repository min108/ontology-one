"""Handler regressions for trusted review identity and mutation boundaries."""
import asyncio
import json
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from backend import federation, main, placement, projects, work_context
from backend.db import Row
from backend.security.context import Principal, use_context


class ReviewBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / 'review.sqlite')
        self.principal = Principal('real-reviewer-id', 'real-reviewer', 'dept-01', 'dept-01',
                                   'reviewer', False, 'session', 1, 'fixture')
        with self.connect() as conn:
            conn.executescript('''
                CREATE TABLE entities(id TEXT PRIMARY KEY,kind TEXT,title TEXT,summary TEXT,domain TEXT,org_id TEXT,
                    status TEXT,source_kind TEXT,payload TEXT,created_at TEXT,updated_at TEXT,version INTEGER);
                CREATE TABLE relations(id TEXT PRIMARY KEY,source TEXT,target TEXT,type TEXT,label TEXT,status TEXT,payload TEXT);
                CREATE TABLE versions(id INTEGER PRIMARY KEY,entity_id TEXT,version INTEGER,snapshot TEXT,created_at TEXT,reason TEXT);
                CREATE TABLE activities(id INTEGER PRIMARY KEY,entity_id TEXT,action TEXT,detail TEXT,actor TEXT,created_at TEXT);
                CREATE TABLE jobs(id TEXT PRIMARY KEY,kind TEXT,entity_id TEXT,status TEXT,progress INTEGER,message TEXT,result TEXT,created_at TEXT,updated_at TEXT);
                CREATE TABLE federation_links(id TEXT PRIMARY KEY,source_tenant_id TEXT,source_entity_id TEXT,
                    target_tenant_id TEXT,target_entity_id TEXT,relation_type TEXT,label TEXT,status TEXT,reason TEXT,
                    created_by TEXT,reviewed_by TEXT,created_at TEXT,updated_at TEXT);
            ''')
        self.patches = [patch.object(module, 'connect', side_effect=self.connect) for module in (main, projects, placement)]
        self.patches += [patch.object(module, 'log', side_effect=self.record_log) for module in (projects, placement, work_context)]
        for entry in self.patches:
            entry.start()
        self.context = use_context(self.principal)
        self.context.__enter__()

    def tearDown(self):
        self.context.__exit__(None, None, None)
        for entry in reversed(self.patches):
            entry.stop()
        self.temp.cleanup()

    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = lambda cur, row: Row(dict(zip([col[0] for col in cur.description], row)))
        return conn

    @staticmethod
    def record_log(conn, eid, action, detail='', actor=''):
        conn.execute('INSERT INTO activities(entity_id,action,detail,actor,created_at) VALUES(?,?,?,?,?)',
                     (eid, action, detail, actor, '2026'))

    def add(self, ident, kind, status='draft', payload=None):
        with self.connect() as conn:
            conn.execute('INSERT INTO entities VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                         (ident, kind, ident, ident, 'Business', 'dept-01', status, 'user', json.dumps(payload or {}), '2026', '2026', 1))

    def read(self, ident):
        with self.connect() as conn:
            row = dict(conn.execute('SELECT * FROM entities WHERE id=?', (ident,)).fetchone())
        row['payload'] = json.loads(row['payload'])
        return row

    def test_organization_action_cannot_change_managed_root(self):
        self.add('dept-01', 'organization', 'published')
        for action in ('revise', 'submit', 'archive'):
            async def request_json():
                return {'action': action, 'reason': 'Forged workflow request'}
            with self.subTest(action=action), self.assertRaises(HTTPException) as caught:
                asyncio.run(main.action('dept-01', SimpleNamespace(json=request_json)))
            self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual((self.read('dept-01')['status'], self.read('dept-01')['version']), ('published', 1))

    def test_task_approval_uses_session_identity_and_preserves_submission(self):
        self.add('task', 'task', 'review', {'owner': 'builder', 'delivery_note': 'Original delivery', 'delivery_snapshot': []})
        result = projects.task_action('task', {'action': 'approve', 'actor': 'impersonated-admin', 'reason': 'Reviewed source evidence'})
        payload = result['entity']['payload']
        self.assertEqual(payload['reviewer'], self.principal.username)
        self.assertEqual(payload['reviewer_id'], self.principal.user_id)
        self.assertEqual(payload['delivery_note'], 'Original delivery')
        self.assertNotIn('impersonated-admin', json.dumps(result))

    def test_task_approval_rejects_delivery_mutation_even_if_null_or_unchanged(self):
        self.add('task', 'task', 'review', {'owner': 'builder', 'delivery_note': 'Original delivery'})
        for delivery in ('Changed content', 'Original delivery', None):
            with self.subTest(delivery=delivery), self.assertRaises(HTTPException) as caught:
                projects.task_action('task', {'action': 'approve', 'reason': 'Review evidence', 'delivery_note': delivery})
            self.assertEqual(caught.exception.status_code, 403)
        current = self.read('task')
        self.assertEqual((current['status'], current['version'], current['payload']['delivery_note']), ('review', 1, 'Original delivery'))

    def test_task_claim_cannot_impersonate_another_owner(self):
        self.add('task', 'task')
        with use_context(replace(self.principal, role='modeler')):
            result = projects.task_action('task', {'action': 'claim', 'actor': 'other-person'})
        self.assertEqual(result['entity']['payload']['owner'], self.principal.username)
        self.assertEqual(result['entity']['payload']['owner_user_id'], self.principal.user_id)

    def test_placement_signature_and_idempotency_are_bound_to_real_reviewer(self):
        self.add('source', 'knowledge')
        self.add('target', 'rule', 'published')
        result = {'mode': 'local', 'asset_snapshot': {'version': 1}, 'candidates': [{'id': 'target', 'version': 1}],
                  'suggestions': [{'id': 'suggestion', 'target_id': 'target', 'direction': 'outgoing', 'relation_type': 'references'}]}
        with self.connect() as conn:
            conn.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)', ('job', 'asset_placement', 'source', 'ready', 100, 'Ready', json.dumps(result), '2026', '2026'))
        body = placement.ConfirmRequest(job_id='job', expected_version=1, reviewer='impersonated-admin',
            review_note='Reviewed original source', links=[placement.ReviewedLink(suggestion_id='suggestion', target_id='target',
                target_version=1, relation_type='references', reason='Business references verified', reviewed=True)])
        confirmed = placement.confirm('source', body)
        self.assertEqual((confirmed['reviewer'], confirmed['reviewer_id']), (self.principal.username, self.principal.user_id))
        repeated = placement.confirm('source', body.model_copy(update={'reviewer': 'another-forged-name'}))
        self.assertTrue(repeated['idempotent'])
        with use_context(replace(self.principal, user_id='another-user', username='another-reviewer')):
            with self.assertRaises(HTTPException) as caught:
                placement.confirm('source', body)
        self.assertEqual(caught.exception.status_code, 409)
        with self.connect() as conn:
            relation = json.loads(conn.execute('SELECT payload FROM relations').fetchone()[0])
            job = conn.execute('SELECT result FROM jobs').fetchone()[0]
        self.assertEqual(relation['reviewer_id'], self.principal.user_id)
        self.assertNotIn('impersonated-admin', job)

    def test_context_relation_confirmation_ignores_supplied_actor(self):
        self.add('project', 'project', 'active')
        self.add('material', 'material', 'published')
        with self.connect() as conn:
            work_context.attach_context(conn, 'material', project_id='project', actor='forged-actor')
        with self.connect() as conn:
            relation = json.loads(conn.execute('SELECT payload FROM relations').fetchone()[0])
            actor = conn.execute('SELECT actor FROM activities').fetchone()[0]
        self.assertEqual(relation['confirmed_by'], self.principal.user_id)
        self.assertEqual(relation['confirmed_by_name'], self.principal.username)
        self.assertEqual(actor, self.principal.username)

    def test_federation_creation_and_review_roll_back_if_revoked_before_commit(self):
        principal = replace(self.principal, home_tenant_id='hq', tenant_id='hq', role='modeler')
        tenants = [{'id': 'dept-01'}, {'id': 'dept-02'}]
        state = {'revoked': False}

        class CentralCursor:
            def __init__(self, conn):
                self.cursor = conn.cursor()
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.cursor.close()
            def execute(self, sql, args=()):
                self.cursor.execute(sql.replace('%s', '?'), args)
                if sql.lstrip().startswith(('INSERT', 'UPDATE')):
                    state['revoked'] = True
            def fetchone(self):
                return self.cursor.fetchone()
            @property
            def rowcount(self):
                return self.cursor.rowcount

        @contextmanager
        def central_connection(*args):
            conn = self.connect()
            try:
                with conn:
                    yield SimpleNamespace(cursor=lambda: CentralCursor(conn))
            finally:
                conn.close()

        def assert_current(value, require_write=False):
            if state['revoked']:
                raise HTTPException(401, 'Session revoked')
            return value

        def transaction_scope(value, connection):
            assert_current(value)
            return tenants

        body = federation.LinkRequest(source_tenant_id='dept-01', source_entity_id='a', target_tenant_id='dept-02',
                                      target_entity_id='b', label='Business reference', reason='Verified endpoints')
        with patch.object(federation, '_scope', side_effect=lambda: (principal, tenants)), \
             patch.object(federation, '_link_endpoints', return_value=({'kind': 'knowledge'}, {'kind': 'knowledge'})), \
             patch.object(federation.store, 'control_connection', side_effect=central_connection), \
             patch.object(federation.store, 'authorized_control_connection', side_effect=central_connection), \
             patch.object(federation.store, 'authorized_read_tenants', return_value=tenants), \
             patch.object(federation.store, 'authorized_read_tenants_in_transaction', side_effect=transaction_scope), \
             patch.object(federation.store, 'assert_principal_current', side_effect=assert_current), \
             patch.object(federation.store, 'append_audit') as audit:
            with self.assertRaises(HTTPException) as caught:
                federation.create_link(body)
            self.assertEqual(caught.exception.status_code, 401)
            with self.connect() as conn:
                self.assertEqual(conn.execute('SELECT count(*) FROM federation_links').fetchone()[0], 0)
                conn.execute('INSERT INTO federation_links VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    ('link', 'dept-01', 'a', 'dept-02', 'b', 'references', 'Reference', 'pending', 'Original reason', 'other-user', None, '2026', '2026'))
            state['revoked'] = False
            principal = replace(principal, role='reviewer')
            with self.assertRaises(HTTPException) as caught:
                federation.review_link('link', federation.LinkReview(status='confirmed', reason='Reviewed evidence'))
            self.assertEqual(caught.exception.status_code, 401)
            with self.connect() as conn:
                link = conn.execute('SELECT status,reason,reviewed_by FROM federation_links').fetchone()
            self.assertEqual(tuple(link), ('pending', 'Original reason', None))
            audit.assert_not_called()


if __name__ == '__main__':
    unittest.main()
