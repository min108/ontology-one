"""Semantic migration regressions: preserve history, identifiers and provenance."""
import json
import sqlite3
import unittest
from copy import deepcopy

from fastapi import HTTPException
from openpyxl import Workbook

from backend.asset_kinds import canonical_entity, canonical_kind, migrate_skill_assets
from backend.asset_import import FIELDS, PATTERNS, sheet_rows


class SkillMigrationTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript('''
            CREATE TABLE entities(id TEXT PRIMARY KEY,kind TEXT,payload TEXT);
            CREATE TABLE relations(id TEXT PRIMARY KEY,source TEXT,target TEXT,type TEXT,payload TEXT);
            CREATE TABLE versions(id TEXT PRIMARY KEY,snapshot TEXT);
            CREATE TABLE audit(id TEXT PRIMARY KEY,detail TEXT);
        ''')

    def tearDown(self):
        self.conn.close()

    def add(self, ident, kind, payload):
        self.conn.execute('INSERT INTO entities VALUES(?,?,?)', (ident, kind, json.dumps(payload, ensure_ascii=False)))

    def test_idempotent_upgrade_preserves_ids_links_quotes_and_historic_bytes(self):
        quoted = '原始算法说明 algorithm 不应被替换。'
        original = {'id': 'A.01.01.001', 'kind': 'algorithm', 'payload': {'algorithm_type': '关系识别', 'content': quoted, 'executable': True}}
        snapshot = json.dumps(original, ensure_ascii=False)
        self.add(original['id'], original['kind'], original['payload'])
        self.add('R.01.01.001.T1', 'rule', {'related_algorithms': [original['id']], 'content': quoted})
        self.conn.execute('INSERT INTO relations VALUES(?,?,?,?,?)', ('rel', 'R.01.01.001.T1', original['id'], 'references_algorithm', '{"reason":"算法依据"}'))
        self.conn.execute('INSERT INTO versions VALUES(?,?)', ('v1', snapshot))
        self.conn.execute('INSERT INTO audit VALUES(?,?)', ('event', quoted))
        self.assertEqual(migrate_skill_assets(self.conn), 2)
        self.assertEqual(migrate_skill_assets(self.conn), 0)
        asset = dict(self.conn.execute('SELECT * FROM entities WHERE id=?', (original['id'],)).fetchone())
        payload = json.loads(asset['payload'])
        self.assertEqual(asset['kind'], 'skill')
        self.assertTrue(payload['legacy_skill'])
        self.assertFalse(payload['executable'])
        self.assertEqual(payload['content'], quoted)
        self.assertEqual(payload['skill_type'], '关系识别')
        self.assertNotIn('algorithm_type', payload)
        relation = dict(self.conn.execute('SELECT * FROM relations').fetchone())
        self.assertEqual((relation['id'], relation['source'], relation['target'], relation['type']), ('rel', 'R.01.01.001.T1', original['id'], 'references_skill'))
        self.assertEqual(self.conn.execute('SELECT snapshot FROM versions').fetchone()[0], snapshot)
        self.assertEqual(self.conn.execute('SELECT detail FROM audit').fetchone()[0], quoted)

    def test_historical_view_is_canonical_without_mutating_raw_snapshot(self):
        old = {'id': 'A-old', 'kind': 'algorithm', 'version': 7, 'payload': json.dumps({'algorithm_type': '核对', '算法类型': '历史类型', 'content': '算法原文'})}
        before = deepcopy(old)
        visible = canonical_entity(old)
        self.assertEqual(old, before)
        self.assertEqual((visible['kind'], visible['version'], visible['payload']['skill_type']), ('skill', 7, '核对'))
        self.assertEqual(visible['payload']['content'], '算法原文')
        self.assertEqual(visible['payload']['技能类型'], '历史类型')
        self.assertEqual(canonical_entity(visible), visible)
        self.assertEqual(canonical_kind('algorithm'), 'skill')
        self.assertEqual(canonical_kind('knowledge'), 'knowledge')

    def test_only_governed_pending_candidate_kind_is_rewritten(self):
        nested = {'kind': 'algorithm', 'text': '用户原文'}
        self.add('material', 'material', {'candidates': [{'id': 'candidate-original', 'kind': 'algorithm', 'content': '算法原文'}], 'business_json': nested})
        migrate_skill_assets(self.conn)
        result = json.loads(self.conn.execute('SELECT payload FROM entities').fetchone()[0])
        self.assertEqual(result['candidates'][0], {'id': 'candidate-original', 'kind': 'skill', 'content': '算法原文'})
        self.assertEqual(result['business_json'], nested)

    def test_conflicting_fields_fail_before_any_migration_write(self):
        self.add('first', 'algorithm', {'algorithm_type': 'legacy'})
        self.add('conflict', 'algorithm', {'algorithm_type': 'legacy', 'skill_type': 'different'})
        with self.assertRaises(ValueError):
            migrate_skill_assets(self.conn)
        self.assertEqual({row['kind'] for row in self.conn.execute('SELECT kind FROM entities')}, {'algorithm'})

    def test_legacy_spreadsheet_headers_and_new_skill_codes_are_accepted(self):
        workbook = Workbook(); sheet = workbook.active
        reverse = {'技能编码': '算法编码', '技能类型': '算法类型', '技能逻辑名称': '算法逻辑名称'}
        sheet.append([reverse.get(header, header) for header in FIELDS['skill']])
        sheet.append(['A.01.01.001', '组织管理', '核对', '关系识别', '原名称', '场景', '输入', '方法', '规则', '输出', ''])
        records, recognized = sheet_rows(workbook, 'skill')
        self.assertTrue(recognized)
        self.assertEqual(records[0]['values']['技能编码'], 'A.01.01.001')
        self.assertTrue(PATTERNS['skill'].fullmatch('S.01.01.001'))
        self.assertTrue(PATTERNS['skill'].fullmatch('A.01.01.001'))
        self.assertFalse(PATTERNS['skill'].fullmatch('D.01.01.001'))

    def test_ambiguous_old_and_new_headers_do_not_silently_discard_values(self):
        workbook = Workbook(); sheet = workbook.active
        sheet.append(FIELDS['skill'] + ['算法编码'])
        with self.assertRaises(HTTPException) as caught:
            sheet_rows(workbook, 'skill')
        self.assertEqual(caught.exception.status_code, 422)

    def test_legacy_kind_input_cannot_bypass_new_asset_publication_rules(self):
        from backend.main import _create_entity
        with self.assertRaises(HTTPException) as caught:
            _create_entity({'kind': 'algorithm', 'title': 'Legacy client request', 'status': 'published'})
        self.assertIn(caught.exception.status_code, (400, 403))


if __name__ == '__main__':
    unittest.main()
