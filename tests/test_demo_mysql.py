"""Opt-in real tenant storage and conservative demo archive/restore checks."""
import os
import secrets
import shutil
import tempfile
import unittest
from contextlib import closing
from unittest.mock import patch

import pymysql
from fastapi import HTTPException
from starlette.staticfiles import StaticFiles

from backend import db, demo, demo_data, sources
from backend.security import store
from backend.security.context import use_context


@unittest.skipUnless(os.environ.get('RUN_JOBS_MYSQL_TESTS') == '1', 'opt-in MySQL integration test')
class DemoMySQL(unittest.TestCase):
    def test_fixtures_are_drafts_idempotent_tenant_local_and_safely_archived(self):
        previous = {key: os.environ.get(key) for key in ('MYSQL_DATABASE', 'ONTOLOGY_MYSQL_URL', 'ONTOLOGY_STORAGE_ROOT')}
        base = 'ontology_demotest_' + secrets.token_hex(5)
        os.environ.pop('ONTOLOGY_MYSQL_URL', None)
        os.environ['MYSQL_DATABASE'] = base
        os.environ['ONTOLOGY_STORAGE_ROOT'] = tempfile.mkdtemp(prefix='ontology-demo-storage-')
        created = []
        password = 'DemoFixture9-' + secrets.token_hex(16)
        try:
            store.initialize_control(); store.bootstrap_superadmin('demo-owner', password)
            token = store.login('demo-owner', password, '127.0.0.1', 'demo-test')
            owner = store.authenticate_session(token, 'test')[0]
            token = store.change_password(owner, password, password + 'x', '127.0.0.1', 'demo-test')
            owner = store.authenticate_session(token, 'test')[0]
            principals = []
            for index, tenant in enumerate(('dept-01', 'dept-02')):
                db.ensure_tenant_schema(tenant); created.append(store.get_tenant(tenant)['db_name'])
                username = 'demo-admin-' + str(index)
                store.create_user(owner, username=username, display_name='演示测试管理员', tenant_id=tenant,
                                  role='admin', password=password, must_change_password=False)
                raw = store.login(username, password, '127.0.0.1', 'demo-test')
                principals.append(store.authenticate_session(raw, 'test')[0])
            p, other = principals
            reviewer_id = store.create_user(owner, username='demo-reviewer', display_name='独立演示审核人', tenant_id='dept-01',
                                            role='reviewer', password=password, must_change_password=False)
            raw = store.login('demo-reviewer', password, '127.0.0.1', 'demo-test')
            reviewer = store.authenticate_session(raw, 'test')[0]
            with use_context(p):
                self.assertFalse(demo.status()['loaded'])
                first = demo.load(demo.Confirm(confirm=True))
                self.assertEqual(len(first['items']), 20)
                self.assertTrue(first['loaded']); self.assertTrue(first['can_clear'])
                self.assertTrue(all(item['status'] == 'draft' for item in first['items']))
                original_ids = {item['id'] for item in first['items']}
                second = demo.load(demo.Confirm(confirm=True))
                self.assertTrue(second['already_loaded'])
                self.assertEqual({item['id'] for item in second['items']}, original_ids)
                with closing(db.connect()) as conn:
                    self.assertEqual(conn.execute('SELECT COUNT(*) FROM dataset_rows').fetchone()[0], 9)
                    self.assertEqual(conn.execute('SELECT COUNT(*) FROM source_mappings').fetchone()[0], 0)
                    self.assertEqual(conn.execute('SELECT kind FROM source_connections WHERE id=?', (demo_data.SOURCE_ID,)).fetchone()[0], 'demo')
                    receipt = demo._receipt(conn)
                    self.assertEqual(len(receipt['files']), 4)
                with self.assertRaises(HTTPException) as error:
                    demo.prepare_mappings(demo.Confirm(confirm=True))
                self.assertEqual(error.exception.status_code, 409)
                archived = demo.clear(demo.Confirm(confirm=True))
                self.assertTrue(archived['archived']); self.assertTrue(archived['can_restore']); self.assertFalse(archived['loaded'])
                with closing(db.connect()) as conn:
                    self.assertEqual(conn.execute('SELECT COUNT(*) FROM entities WHERE id IN (' + ','.join('?' for _ in original_ids) + ')', sorted(original_ids)).fetchone()[0], 20)
                    self.assertTrue(all(demo.scoped_path(item['path']).exists() for item in receipt['files']))
                    self.assertFalse(conn.execute('SELECT enabled FROM source_connections WHERE id=?', (demo_data.SOURCE_ID,)).fetchone()[0])
                restored = demo.load(demo.Confirm(confirm=True))
                self.assertTrue(restored['restored']); self.assertTrue(restored['loaded'])
                self.assertEqual({item['id'] for item in restored['items']}, original_ids)
                # Exercise actual governed standard and mapping transitions with
                # two real login identities. No fixture installs published rows.
                # This is a domain/storage test, not a static-file server test;
                # frontend builds may replace dist concurrently in a shared QA.
                with patch('fastapi.staticfiles.StaticFiles', side_effect=lambda **kw: StaticFiles(**kw, check_dir=False)):
                    from backend.main import _entity_action
                standards = [item['id'] for item in restored['items'] if item['kind'] == 'data']
                for ident in standards:
                    _entity_action(ident, {'action': 'submit', 'reason': '提交模拟口径核对'})
                    with use_context(reviewer):
                        _entity_action(ident, {'action': 'approve', 'reason': '独立核对模拟字段口径'})
                        _entity_action(ident, {'action': 'publish', 'reason': '发布已核对的模拟标准'})
                result = demo.prepare_mappings(demo.Confirm(confirm=True))
                self.assertEqual(result['created_count'], 2)
                self.assertTrue(demo.prepare_mappings(demo.Confirm(confirm=True))['already_prepared'])
                sources.set_grant(demo_data.SOURCE_ID, sources.GrantBody(user_id=reviewer_id, discover=True, query=True))
                with closing(db.connect()) as conn:
                    mappings = [dict(row) for row in conn.execute('SELECT id,title,version FROM source_mappings WHERE source_id=?', (demo_data.SOURCE_ID,))]
                for mapping in mappings:
                    submitted = sources.mapping_action(demo_data.SOURCE_ID, mapping['id'], sources.MappingAction(action='submit', expected_version=mapping['version']))
                    with use_context(reviewer):
                        sources.mapping_action(demo_data.SOURCE_ID, mapping['id'], sources.MappingAction(action='publish', expected_version=submitted['version'], reason='独立核对模拟台账查询口径'))
                contract_mapping = next(item['id'] for item in mappings if '合同台账' in item['title'])
                queried = sources.query_mapping(demo_data.SOURCE_ID, contract_mapping, sources.QueryBody(
                    fields=['contract_id', 'amount_yuan'], filters=[{'field': 'amount_yuan', 'op': 'gt', 'value': 100000}], limit=4))
                self.assertEqual({row['contract_id'] for row in queried['rows']}, {'DEMO-C002', 'DEMO-C004'})
                with closing(db.connect()) as conn, conn:
                    # A new business record referencing the fixture is outside the
                    # owned pack. It must remain intact and block bulk archiving.
                    conn.execute('INSERT INTO entities VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                        ('USER-NEW-REFERENCE', 'knowledge', '用户自己的记录', '不能随演示资料归档', '业务', p.tenant_id, 'draft', 'user',
                         demo._json({'related_asset_id': next(iter(original_ids))}), db.now(), db.now(), 1))
                protected = demo.status()
                self.assertFalse(protected['can_clear']); self.assertTrue(protected['protected'])
                with self.assertRaises(HTTPException) as error:
                    demo.clear(demo.Confirm(confirm=True))
                self.assertEqual(error.exception.status_code, 409)
                with closing(db.connect()) as conn:
                    self.assertEqual(conn.execute('SELECT status FROM entities WHERE id=?', ('USER-NEW-REFERENCE',)).fetchone()[0], 'draft')
                    self.assertTrue(conn.execute('SELECT enabled FROM source_connections WHERE id=?', (demo_data.SOURCE_ID,)).fetchone()[0])
            with use_context(other):
                self.assertFalse(demo.status()['loaded'])
                with closing(db.connect()) as conn:
                    self.assertFalse(conn.execute('SELECT 1 FROM source_connections WHERE id=?', (demo_data.SOURCE_ID,)).fetchone())
        finally:
            config = db.mysql_config()
            with pymysql.connect(host=config['host'], port=config['port'], user=config['user'], password=config['password']) as connection, connection.cursor() as cursor:
                for name in [*created, base + '_control']:
                    cursor.execute('DROP DATABASE IF EXISTS `' + name + '`')
            shutil.rmtree(os.environ['ONTOLOGY_STORAGE_ROOT'], ignore_errors=True)
            for key, value in previous.items():
                if value is None: os.environ.pop(key, None)
                else: os.environ[key] = value


if __name__ == '__main__':
    unittest.main()
