"""Authenticated business-flow smoke test against a disposable MySQL workspace.

MYSQL_* or ONTOLOGY_MYSQL_URL select the test server. The Sandbox harness creates
random accounts/databases and an isolated HTTP server, then removes its databases
and storage in finally. No existing workspace or external AI service is used.
"""
from __future__ import annotations

import argparse
from io import BytesIO
import json
from pathlib import Path
import time
from urllib.parse import quote

from openpyxl import load_workbook

from check_security import Sandbox, Session, expect


def run_checks(box):
    checks, artifacts = [], {}
    admin = Session(box, box.admin_name, box.admin_password)
    admin.login()
    builder = box.user(admin, 'sub-01', 'modeler')
    reviewer = box.user(admin, 'sub-01', 'reviewer')

    def check(name, operation):
        started = time.monotonic()
        try:
            operation()
            result = {'name': name, 'passed': True}
        except Exception as exc:
            result = {'name': name, 'passed': False, 'error': str(exc)[:1200]}
        result['seconds'] = round(time.monotonic() - started, 3)
        checks.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)

    def approve_asset(ident, authority=False):
        for actor, action in ((builder, 'submit'), (reviewer, 'approve'), (reviewer, 'publish')):
            current = builder.ok('GET', '/entities/' + ident).json()
            body = {'action': action, 'reason': 'QA 已核对测试内容及业务范围'}
            if authority:
                body['expected_version'] = current['version']
            actor.ok('POST', '/entities/' + ident + '/action', json=body)
        assert builder.ok('GET', '/entities/' + ident).json()['status'] == 'published'

    def asset_workflow():
        ident = 'QA-WORKFLOW-STANDARD'
        entity = builder.create(ident, 'QA任务编号', org_id='sub-01', payload={
            'definition': 'QA测试任务的唯一标识，每个测试任务一条记录。', 'format': '文本',
            'data_type': 'string', 'data_form': 'standard_field', 'record_grain': '一条记录代表一个任务',
            'constraints': '非空且唯一', 'active_asset': True})
        artifacts['standard'] = ident
        assert entity['owner_tenant_id'] == 'sub-01' and entity['org_id'] == 'sub-01'
        expect(builder.request('POST', '/entities/' + ident + '/action', json={'action': 'approve'}), 403)
        expect(reviewer.request('PATCH', '/entities/' + ident, json={'summary': 'QA unauthorized edit'}), 403)
        approve_asset(ident)
        versions = builder.ok('GET', '/entities/' + ident + '/versions').json()['items']
        assert len(versions) >= 3

    check('standard asset creation, separate approval and publication', asset_workflow)

    def datasets():
        template = builder.ok('GET', '/asset-import/template/data')
        book = load_workbook(BytesIO(template.content), read_only=True)
        assert book.sheetnames and book.active.max_row >= 1
        book.close()
        raw = 'QA任务编号\r\nQA-TASK-001\r\nQA-TASK-002\r\n'.encode('utf-8-sig')
        result = builder.ok('POST', '/datasets/upload', data={
            'org_id': 'sub-01', 'title': 'QA 两行任务台账', 'header_start': '1', 'header_rows': '1',
            'import_group_id': 'QA-WORKFLOW-GROUP', 'import_item_id': 'QA-WORKFLOW-CSV'},
            files={'file': ('QA-workflow.csv', raw, 'text/csv')}).json()
        ident = result['entity']['id']
        artifacts['dataset'] = ident
        detail = builder.ok('GET', '/datasets/' + ident).json()
        assert detail['stats']['rows'] == 2 and len(detail['sheets']) == 1
        sheet = detail['sheets'][0]['name']
        column = detail['mappings'][0]['column_name']
        builder.ok('PUT', '/datasets/' + ident + '/sheets/' + quote(sheet, safe='') + '/grain',
                   json={'grain': '每行代表一个QA测试任务', 'confirmed': True})
        mapping = {'column_name': column, 'standard_id': artifacts['standard'], 'transform': 'identity',
                   'status': 'confirmed', 'payload': {'reason': 'QA 已核对任务编号含义、文本类型及每任务一行的记录粒度'}}
        result = builder.ok('PUT', '/datasets/' + ident + '/mappings',
                            json={'mappings': [mapping], 'reason': 'QA 人工确认字段对应'}).json()
        assert result['readiness']['confirmed'] == 1
        expect(builder.request('POST', '/datasets/' + ident + '/publish', json={'acknowledge_unconfirmed': False}), 403)
        reviewer.ok('POST', '/datasets/' + ident + '/publish', json={'acknowledge_unconfirmed': False})
        detail = reviewer.ok('GET', '/datasets/' + ident).json()
        assert detail['entity']['status'] == 'published' and detail['readiness']['state'] == 'ready'
        assert detail['mappings'][0]['payload']['confirmed_by'] == builder.user['id']
        rows = reviewer.ok('GET', '/datasets/' + ident + '/rows').json()
        assert rows['total'] == 2
        assert reviewer.ok('GET', '/datasets/' + ident + '/source').content == raw
        exported = reviewer.ok('GET', '/datasets/' + ident + '/export?standardized=true')
        assert 'QA-TASK-001' in exported.text and 'QA-TASK-002' in exported.text

    check('CSV upload, template, mapping, reviewer publication and source export', datasets)

    def experience():
        narrative = 'QA虚构经验：项目交付前，我们逐项核对任务清单，并记录验收依据，避免遗漏必要的业务材料。'
        session = builder.ok('POST', '/experience/sessions', json={
            'title': 'QA 本地经验访谈', 'context': narrative, 'org_id': 'sub-01', 'mode': 'structured'}).json()
        ident = session['entity']['id']
        artifacts['experience'] = ident
        assert session['entity']['owner_tenant_id'] == 'sub-01'
        assert session['entity']['payload']['turns'][-1]['mode'] == 'structured'
        addition = 'QA补充：对尚未取得的依据先作标记，再安排责任岗位复核，原始记录保持可追溯。'
        session = builder.ok('POST', '/experience/' + ident + '/turns', json={
            'content': addition, 'mode': 'structured', 'expected_version': session['entity']['version']}).json()
        draft = builder.ok('POST', '/experience/' + ident + '/draft', json={}).json()
        assert narrative in draft['draft']['content'] and addition in draft['draft']['content']
        assert draft['draft']['generated_from'] == 'user_narratives'
        edited = {'title': 'QA 人工整理经验草稿', 'summary': 'QA 本地访谈及人工整理验证',
                  'content': draft['draft']['content'], 'boundary': '仅供QA功能测试，不作为业务结论', 'questions': ''}
        draft = builder.ok('PATCH', '/experience/' + ident + '/draft', json=edited).json()
        assert draft['draft']['title'] == edited['title'] and len(draft['chunks']) == 2
        expect(reviewer.request('PATCH', '/experience/' + ident + '/draft', json=edited), 403)

    check('local experience interview, source-preserving draft and edit separation', experience)

    def authority():
        spec = {'type': 'workflow', 'subtype': '文件审批', 'purpose': 'QA验证流程本体的建模与审核。',
                'basis': 'QA虚构流程规范第一版，仅用于功能验证。', 'scope': 'QA测试任务与模拟资料',
                'effective_from': '2026-01-01', 'org_ids': ['sub-01'], 'nodes': [
                    {'id': 'start', 'kind': 'start', 'title': 'QA申请', 'x': 0, 'y': 0},
                    {'id': 'check', 'kind': 'step', 'title': 'QA复核', 'org_id': 'sub-01', 'role': 'QA审核岗位',
                     'responsibility': '逐项核对资料完整性', 'control': '确认依据可追溯', 'x': 160, 'y': 0},
                    {'id': 'end', 'kind': 'end', 'title': 'QA归档', 'x': 320, 'y': 0}],
                'edges': [{'id': 'one', 'source': 'start', 'target': 'check'},
                          {'id': 'two', 'source': 'check', 'target': 'end'}]}
        body = {'title': 'QA 文件审批流程', 'summary': 'QA三节点流程测试', 'org_id': 'sub-01', 'spec': spec}
        result = builder.ok('POST', '/authority/assets', json=body).json()
        entity = result['entity']
        ident = entity['id']
        artifacts['authority'] = ident
        body.update(summary='QA流程建模、修订和审核联通验证', expected_version=entity['version'])
        saved = builder.ok('PUT', '/authority/assets/' + ident, json=body).json()
        assert len(saved['entity']['payload']['authority_spec']['nodes']) == 3
        approve_asset(ident, authority=True)
        final = reviewer.ok('GET', '/authority/assets/' + ident).json()
        assert final['entity']['status'] == 'published'
        assert final['entity']['org_id'] == 'sub-01'

    check('authority workflow graph save, versioning, review and publication', authority)

    def projects():
        project = builder.create('QA-WORKFLOW-PROJECT', 'QA 本体建设专题', kind='project', status='active',
                                 org_id='sub-01', payload={'objective': 'QA既有任务流程适配验证'})
        pid = project['id']
        artifacts['project'] = pid
        task = builder.ok('POST', '/projects/' + pid + '/tasks', json={
            'title': 'QA 核对任务编号标准', 'task_type': 'dictionary', 'instruction': 'QA核对标准字段及交付清单'}).json()
        tid = task['entity']['id']
        artifacts['task'] = tid
        task = builder.ok('POST', '/tasks/' + tid + '/action', json={'action': 'claim'}).json()
        assert task['entity']['payload']['owner_user_id'] == builder.user['id']
        builder.ok('POST', '/work/' + tid + '/objects', json={
            'target_id': artifacts['standard'], 'role': 'produced_asset', 'required': False})
        task = builder.ok('GET', '/tasks/' + tid + '/workspace').json()
        checklist = [{**item, 'done': True} for item in task['entity']['payload']['checklist']]
        task = builder.ok('PATCH', '/tasks/' + tid + '/workspace', json={
            'version': task['entity']['version'], 'checklist': checklist, 'delivery_note': 'QA标准字段、原表与对应关系已逐项核对。'}).json()
        assert not task['missing']
        task = builder.ok('POST', '/tasks/' + tid + '/action', json={
            'action': 'submit', 'version': task['entity']['version'], 'reason': 'QA提交交付成果复核'}).json()
        assert task['entity']['status'] == 'review'
        rejected = reviewer.request('POST', '/tasks/' + tid + '/action', json={
            'action': 'approve', 'version': task['entity']['version'], 'reason': 'QA审核', 'delivery_note': 'QA不能由审核者改写'})
        expect(rejected, 403)
        task = reviewer.ok('POST', '/tasks/' + tid + '/action', json={
            'action': 'approve', 'version': task['entity']['version'], 'reason': 'QA核对清单和关联版本一致，审核通过'}).json()
        assert task['entity']['payload']['reviewer_id'] == reviewer.user['id']
        task = builder.ok('POST', '/tasks/' + tid + '/action', json={
            'action': 'complete', 'version': task['entity']['version'], 'reason': 'QA经审核后完成交付'}).json()
        assert task['entity']['status'] == 'completed'
        workspace = builder.ok('GET', '/projects/' + pid + '/workspace').json()
        assert workspace['progress'] == 100
        # Exercise the documented admin maintenance path, then archive QA work.
        admin.switch('sub-01')
        archived = admin.ok('POST', '/projects/' + pid + '/action', json={
            'action': 'archive', 'version': workspace['entity']['version'], 'reason': 'QA流程验证结束，归档测试专题'}).json()
        assert archived['entity']['status'] == 'archived'

    check('project task assignment, checklist, separate review, completion and archive', projects)
    return {'result': 'passed' if all(item['passed'] for item in checks) else 'failed', 'checks': checks,
            'transport': 'real HTTP sessions and CSRF; disposable MySQL and tenant storage; no authorization mocks',
            'ai_mode': 'structured local prompts and user narrative drafts; no external AI calls',
            'business_artifacts': artifacts, 'database_namespace': box.namespace,
            'backend_snapshot_sha256': box.source_sha256, 'cleanup': 'Sandbox finally removes all test databases and storage'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8879)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / 'work' / 'workflow-report.json')
    args = parser.parse_args()
    report = {'result': 'failed'}
    try:
        with Sandbox(port=args.port, output=args.output) as box:
            report = run_checks(box)
    except Exception as exc:
        report['result'] = 'failed'
        report['fatal_error'] = str(exc)[:1500]
    finally:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps({'result': report['result'], 'report': str(args.output)}, ensure_ascii=False))
    return 0 if report['result'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
