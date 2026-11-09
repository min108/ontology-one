"""Exercise the local DEMO against an isolated MySQL database and file workspace.

Run with MYSQL_* (or ONTOLOGY_MYSQL_URL) settings for a test server. The account
must be able to create/drop a database. Existing databases and uploaded files
are never modified; the unique test database and temporary files are removed.
"""
from __future__ import annotations

import io
import json
import os
from contextlib import closing
from pathlib import Path
import shutil
import sys
import tempfile
import uuid
import zipfile

import pymysql
from fastapi.testclient import TestClient
from openpyxl import load_workbook


def main():
    source = Path(__file__).resolve().parents[1]
    database = 'ontology_demo_test_' + uuid.uuid4().hex[:16]
    created = False
    settings = None
    with tempfile.TemporaryDirectory(prefix='ontology-demo-test-') as folder:
        work = Path(folder)
        shutil.copytree(source / 'backend', work / 'backend',
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        sys.path.insert(0, str(work))
        from backend import db
        settings = db.mysql_config()
        server = dict(host=settings['host'], port=settings['port'],
                      user=settings['user'], password=settings['password'], charset='utf8mb4')
        try:
            with pymysql.connect(**server) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(f'CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci')
                    created = True
            os.environ.pop('ONTOLOGY_MYSQL_URL', None)
            for key, value in settings.items():
                os.environ['MYSQL_' + key.upper()] = str(value)
            os.environ['MYSQL_DATABASE'] = database
            from backend.main import app

            def counts():
                with closing(db.connect()) as connection:
                    tables = [r[0] for r in connection.execute('SHOW TABLES')]
                    return {name: connection.execute(f'SELECT COUNT(*) FROM `{name}`').fetchone()[0] for name in tables}

            def integrity():
                with closing(db.connect()) as connection:
                    checks = {
                        'relation endpoint': 'SELECT COUNT(*) FROM relations r LEFT JOIN entities s ON s.id=r.source LEFT JOIN entities t ON t.id=r.target WHERE s.id IS NULL OR t.id IS NULL',
                        'chunk material': 'SELECT COUNT(*) FROM chunks c LEFT JOIN entities e ON e.id=c.material_id WHERE e.id IS NULL',
                        'organization': "SELECT COUNT(*) FROM entities e LEFT JOIN entities o ON o.id=e.org_id WHERE COALESCE(e.org_id,'')<>'' AND o.id IS NULL",
                        'field standard': "SELECT COUNT(*) FROM mappings m LEFT JOIN entities e ON e.id=m.standard_id WHERE COALESCE(m.standard_id,'')<>'' AND e.id IS NULL",
                    }
                    for label, sql in checks.items():
                        assert connection.execute(sql).fetchone()[0] == 0, label

            with TestClient(app) as client:
                before = counts()
                assert before['dataset_rows'] == 24, before
                assert client.get('/api/entities?kind=dataset').json()['total'] == 2
                integrity()
            with TestClient(app) as client:
                assert counts() == before, 'Restart duplicated or removed initial records'

                def request(method, path, expected=200, **kwargs):
                    response = client.request(method, '/api' + path, **kwargs)
                    assert response.status_code == expected, (method, path, response.status_code, response.text[:1500])
                    return response

                routes = ['/health', '/overview', '/catalog', '/entities', '/entities/facets',
                          '/search?q=项目', '/relations', '/activity', '/materials', '/datasets',
                          '/graph', '/graph?view=organization', '/graph?view=focus&node=D-GEN-001',
                          '/work/catalog', '/projects/PRJ-GEN-001/workspace', '/tasks/TASK-GEN-002/workspace',
                          '/authority/catalog', '/asset-catalog/tree', '/asset-catalog/items',
                          '/knowledge/collections', '/knowledge/collections/COL-GEN-001',
                          '/experience/guide', '/experience/sessions', '/impact/sources',
                          '/ai/status', '/applications/scenarios']
                for route in routes:
                    request('GET', route)
                # Dependencies propagate from changed inputs to their consumers,
                # while organizational/collection links must not widen the scope.
                with closing(db.connect()) as connection, connection:
                    connection.execute('INSERT INTO relations VALUES(?,?,?,?,?,?,?)',
                                       ('REL-TEST-CONTAINS', 'D-GEN-005', 'D-GEN-003', 'contains', '测试收录', 'confirmed', '{}'))
                for ident in ('D-GEN-005', 'R-GEN-001'):
                    impacted = request('GET', '/impact/preview?source_id=' + ident + '&page_size=30').json()['items']
                    assert any(x['id'] == 'A-GEN-001' and x['level'] == 1 for x in impacted), impacted
                    assert all(x['id'] != 'D-GEN-003' and x['kind'] not in ('organization', 'project', 'task') for x in impacted), impacted
                impacted = request('GET', '/impact/preview?source_id=A-GEN-001&page_size=30').json()['items']
                assert not ({x['id'] for x in impacted} & {'D-GEN-005', 'R-GEN-001'}), impacted
                for ident in ('DS-GEN-TASK', 'DS-GEN-PROCUREMENT'):
                    for suffix in ('', '/rows', '/quality', '/versions', '/source', '/export'):
                        request('GET', '/datasets/' + ident + suffix)
                for ident in ('MAT-GEN-DICTIONARY', 'MAT-GEN-PROCESS', 'MAT-GEN-EXPERIENCE'):
                    for suffix in ('', '/source', '/chunks', '/download'):
                        request('GET', '/materials/' + ident + suffix)

                content = '# 数据定义说明\n\n项目编号应在业务单位内唯一。\n\n合同金额统一以元记录。'
                uploaded = request('POST', '/materials/upload',
                                   files={'file': ('数据定义说明.md', content.encode(), 'text/markdown')},
                                   data={'org_id': 'ORG-HQ-13', 'domain': '数据治理'}).json()
                material_id = uploaded['id']
                assert request('GET', f'/materials/{material_id}/download').content == content.encode()
                changed = request('PATCH', f'/entities/{material_id}', json={'summary': '人工补充说明', 'reason': '验证素材修订'}).json()
                assert changed['version'] == 2
                asset = request('POST', '/entities', json={'kind': 'data', 'title': '验收批次编号', 'domain': '项目管理', 'org_id': 'ORG-HQ-13', 'summary': '用于标识每次项目验收', 'payload': {'definition': '每次验收的唯一编号'}}).json()
                asset_id = asset['id']
                request('POST', f'/entities/{asset_id}/action', expected=400, json={'action': 'publish'})
                revised = request('PATCH', f'/entities/{asset_id}', json={'summary': '唯一编号，按项目分配', 'reason': '明确编码范围'}).json()
                assert revised['version'] == 2
                for action, status in [('submit', 'review'), ('approve', 'approved'), ('publish', 'published')]:
                    assert request('POST', f'/entities/{asset_id}/action', json={'action': action}).json()['status'] == status
                versions = request('GET', f'/entities/{asset_id}/versions').json()['items']
                assert len(versions) == 4

                for kind in ('data', 'rule', 'algorithm'):
                    response = request('GET', '/asset-import/template/' + kind)
                    workbook = load_workbook(io.BytesIO(response.content))
                    assert '资产导入' in workbook.sheetnames
                    if kind != 'data':
                        continue
                    sheet = workbook['资产导入']
                    values = {'数据项编码': 'D.26.99.001', '二级分类': '数据治理', '三级分类': '项目标识',
                              '数据项名称': '测试项目编码', '数据形态': '结构化', '资产归属': '经营管理',
                              '业务含义': '唯一标识一次项目建设', '敏感级别': '内部'}
                    for col in range(1, sheet.max_column + 1):
                        sheet.cell(2, col, values.get(sheet.cell(1, col).value, ''))
                    stream = io.BytesIO()
                    workbook.save(stream)
                    result = request('POST', '/asset-import/data', files={'file': ('字段模板.xlsx', stream.getvalue(), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')}).json()
                    assert (result['created'], result['updated']) == (1, 0), result
                    repeated = request('POST', '/asset-import/data', files={'file': ('字段模板.xlsx', stream.getvalue(), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')}).json()
                    assert (repeated['created'], repeated['updated']) == (0, 1), repeated

                scenarios = [
                    ('deadline', 'DS-GEN-TASK', {'due_date': '要求完成日期', 'completed_date': '实际完成日期', 'status': '任务状态', 'record': '任务编号'}, 12),
                    ('evidence', 'DS-GEN-TASK', {'evidence': '完成材料', 'status': '任务状态', 'record': '任务编号'}, 9),
                    ('deposit', 'DS-GEN-PROCUREMENT', {'project': '项目编号', 'bidder': '投标单位', 'payer_account': '付款账号'}, 1),
                ]
                for scenario, dataset, fields, findings in scenarios:
                    result = request('POST', '/applications/run', json={'scenario': scenario, 'dataset_id': dataset, 'fields': fields, 'as_of': '2026-10-31'}).json()
                    assert result['total_rows'] == 12 and result['finding_count'] == findings, result
                    assert result['insufficient_rows'] == 0, result
                    assert request('GET', f'/applications/{result["id"]}/export').content

                local_flow = request('POST', '/authority/assist', json={'type': 'workflow', 'text': '业务部门提交采购申请，数据治理中心审核，审核通过后采购部门办理，材料不齐则退回补充。', 'org_ids': ['ORG-HQ-13']}).json()
                assert local_flow['mode'] == 'local', local_flow
                assert local_flow['spec']['nodes'] and local_flow['spec']['edges'], local_flow
                from backend.authority import checks_for
                assert not checks_for(local_flow['spec'])['errors'], local_flow
                integrity()
                backup = request('GET', '/system/backup')
                with zipfile.ZipFile(io.BytesIO(backup.content)) as archive:
                    assert archive.testzip() is None
                    snapshot = json.loads(archive.read('data/mysql-workspace.json'))
                    assert snapshot['format'] == 'ontology-mysql-workspace-v1'
                    assert len(snapshot['tables']['dataset_rows']) == 24
                    assert all('runtime/' not in name and not name.endswith('.env') for name in archive.namelist())
                print(json.dumps({'result': 'passed', 'core_routes': len(routes), 'seed_rows': 24,
                                  'scenarios': 3, 'restart_idempotent': True,
                                  'checks': ['reference integrity', 'dependency impact direction', 'material upload and revision', 'asset review and publish',
                                             'Excel template/import/update', 'source downloads', 'local workflow builder', 'workspace backup']},
                                 ensure_ascii=False))
        finally:
            if created:
                with pymysql.connect(**server) as connection:
                    with connection.cursor() as cursor:
                        cursor.execute(f'DROP DATABASE `{database}`')


if __name__ == '__main__':
    main()
