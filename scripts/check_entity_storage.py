"""Real MySQL regression for entity JSON migration, in a disposable database.

Uses MYSQL_* / ONTOLOGY_MYSQL_URL; the account must be allowed to create and
remove databases inside its configured namespace. Never touches its base DB.
"""
from __future__ import annotations
import json
from pathlib import Path
import secrets
import sys

import pymysql
from pymysql.cursors import DictCursor

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.db import Connection, SCHEMA, ensure_entity_json_storage, mysql_config
from scripts.restore_backup import schema_columns


def main():
    config = mysql_config()
    database = config.pop('database')[:36] + '_jsoncheck_' + secrets.token_hex(5)
    config.update(charset='utf8mb4', cursorclass=DictCursor, autocommit=True)
    raw = pymysql.connect(**config)
    conn = Connection(raw)
    try:
        conn.execute(f'CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci')
        conn.execute(f'USE `{database}`')
        ddl = next(sql for sql in SCHEMA if 'CREATE TABLE IF NOT EXISTS entities(' in sql)
        conn.execute(ddl.replace('payload JSON,', 'payload LONGTEXT,'))
        samples = [None, {}, {'active_asset': 0}, {'active_asset': 1}, {'active_asset': 2},
                   {'active_asset': False}, {'active_asset': True}, {'active_asset': None},
                   {'active_asset': '0'}, {'active_asset': '1'},
                   {'category': '计划执行', 'definition': 'MiXeDSearchvalue', 'content': '原始中文😀',
                    'nested': {'list': [0, 1.5, None, True]}, 'hide_in_graph': True}]
        rows = [(f'CHECK-{i:03}', 'data', '实体', '', '项目管理', 'sub-01', 'published', 'user',
                 None if payload is None else json.dumps(payload, ensure_ascii=False),
                 '2026-11-10T10:00:00', f'2026-11-10T10:00:{i:02}', 1)
                for i, payload in enumerate(samples)]
        conn.executemany('INSERT INTO entities VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', rows)
        columns = "id," + ','.join(f"json_extract(payload,'$.{field}') `{field}`" for field in
                                   ('category', 'subcategory', 'topic_label', 'topic_id', 'active_asset',
                                    'record_role', 'merged_into', 'type', 'group', 'shared', 'is_demo',
                                    'collection_scope', 'hide_in_graph'))
        snapshot = lambda: [dict(row) for row in conn.execute('SELECT ' + columns + ' FROM entities ORDER BY id')]
        current = lambda: [row[0] for row in conn.execute("SELECT id FROM entities WHERE COALESCE(json_extract(payload,'$.active_asset'),1)!=0 ORDER BY id")]
        before, active_before = snapshot(), current()
        # A legacy bad value must stop conversion and leave all original bytes.
        invalid = list(rows[0]); invalid[0], invalid[8] = 'INVALID', '{invalid json'
        conn.execute('INSERT INTO entities VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', invalid)
        try:
            ensure_entity_json_storage(conn)
        except RuntimeError as exc:
            assert '非法 JSON' in str(exc)
        else:
            raise AssertionError('Malformed legacy JSON was silently converted')
        assert conn.execute("SELECT payload FROM entities WHERE id='INVALID'").fetchone()[0] == '{invalid json'
        conn.execute("DELETE FROM entities WHERE id='INVALID'")
        ensure_entity_json_storage(conn)
        ensure_entity_json_storage(conn)  # Idempotent, no second ALTER.
        assert snapshot() == before and current() == active_before
        assert conn.execute("SELECT DATA_TYPE FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='entities' AND column_name='payload'").fetchone()[0] == 'json'
        assert len(conn.execute('SHOW COLUMNS FROM entities').fetchall()) == 12
        assert len(schema_columns()['entities']) == 12
        # Native JSON uses binary collation; preserve the previous LIKE behavior.
        needle = '%mixedsearchvalue%'
        found = conn.execute('SELECT id FROM entities WHERE CAST(payload AS CHAR CHARACTER SET utf8mb4) COLLATE utf8mb4_unicode_ci LIKE ?', (needle,)).fetchall()
        assert len(found) == 1
        dumped = [dict(row) for row in conn.execute('SELECT * FROM entities ORDER BY id')]
        dumped = json.loads(json.dumps(dumped, ensure_ascii=False))
        assert all(row['payload'] is None or isinstance(row['payload'], str) for row in dumped)
        conn.execute('CREATE TABLE restored LIKE entities')
        conn.executemany('INSERT INTO restored VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', [tuple(row.values()) for row in dumped])
        restored = [dict(row) for row in conn.execute('SELECT * FROM restored ORDER BY id')]
        assert restored == dumped
        for i, row in enumerate(dumped):
            assert (None if row['payload'] is None else json.loads(row['payload'])) == samples[i]
        conn.execute('INSERT INTO entities VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                     ('AFTER', 'knowledge', '新增', '', '', 'sub-01', 'draft', 'user', '{}', '', '', 1))
        for sql in SCHEMA:
            if 'idx_entities_recent ' in sql or 'idx_entities_kind_recent ' in sql:
                conn.execute(sql)
        indexes = {row['Key_name'] for row in conn.execute('SHOW INDEX FROM entities')}
        assert {'idx_entities_recent', 'idx_entities_kind_recent'} <= indexes
        print('PASS: legacy JSON migration/rejection, idempotency, 12-column INSERT, graph/current semantics, case-insensitive payload search, backup JSON roundtrip and page indexes')
    finally:
        try:
            conn.execute(f'DROP DATABASE IF EXISTS `{database}`')
        finally:
            conn.close()


if __name__ == '__main__':
    main()
