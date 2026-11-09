"""Small fictional authority examples, created once without replacing user edits."""
import hashlib

from .db import ROOT, now, log
from .authority import Spec, dump, make_payload, sync_relations, get_asset, validate_refs, checks_for
from .provenance import ensure_source_version, register_chunk_anchor

NOTICE = '虚构演示：组织、人员与业务要求仅用于体验建模，不代表任何集团的实际制度或任职安排。'


def _source(c, ident, title, body, org_id, stamp):
    if c.execute('SELECT 1 FROM entities WHERE id=?', (ident,)).fetchone():
        return
    folder = ROOT / 'data/uploads/demo'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (ident + '.md')
    path.write_text(body, encoding='utf-8')
    payload = {'original_text': body, 'format': 'MD', 'material_type': '权责规范编制说明',
               'source_path': path.relative_to(ROOT).as_posix(), 'filename': title + '.md',
               'original_filename': title + '.md', 'sha256': hashlib.sha256(body.encode()).hexdigest(),
               'is_demo': True, 'demo_notice': NOTICE, 'segmentation_status': 'confirmed',
               'segmentation_mode': 'original_paragraphs', 'chunk_count': 1, 'candidates': []}
    c.execute('INSERT INTO entities VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
              (ident, 'material', title, NOTICE, '组织管理', org_id, 'published', 'demo', dump(payload), stamp, stamp, 1))
    source = ensure_source_version(c, ident, body)
    chunk = ident + '::p1'
    c.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?,?)', (chunk, ident, 1, '编制说明', body, '全文', 'completed'))
    register_chunk_anchor(c, ident, chunk, source['id'], 0, len(body), '全文')


def seed_authority(c):
    stamp = now()
    people = [('PERSON-AUTH-DEMO-001', '演示采购专员', 'ORG-HQ-02', '采购经办岗'),
              ('PERSON-AUTH-DEMO-002', '演示数据专员', 'ORG-HQ-13', '数据管理员')]
    for ident, name, org_id, position in people:
        if c.execute('SELECT 1 FROM entities WHERE id=?', (ident,)).fetchone():
            continue
        c.execute('INSERT INTO entities VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                  (ident, 'person', name, position + '（虚构任职）', '组织管理', org_id, 'published', 'demo',
                   dump({'position': position, 'is_demo': True, 'demo_notice': NOTICE}), stamp, stamp, 1))
        c.execute('INSERT INTO relations VALUES(?,?,?,?,?,?,?)',
                  ('rel-auth-member-' + ident, ident, org_id, 'member_of', '任职于', 'confirmed', dump({'is_demo': True})))

    workflow = {'type': 'workflow', 'subtype': '采购审批', 'purpose': '明确采购需求、预算核对、批准与归档的办理顺序。',
                'basis': '虚构采购办理说明 1.0，仅用于流程建模演示。', 'scope': '演示采购管理部门内部需求办理。',
                'org_ids': ['ORG-HQ-02'], 'person_ids': ['PERSON-AUTH-DEMO-001'], 'effective_from': '2026-01-01',
                'nodes': [
                    {'id': 'start', 'kind': 'start', 'title': '提出采购需求', 'x': 0, 'y': 0},
                    {'id': 'prepare', 'title': '整理需求与预算', 'x': 260, 'y': 0, 'org_id': 'ORG-HQ-02',
                     'person_id': 'PERSON-AUTH-DEMO-001', 'role': '采购经办岗', 'responsibility': '记录需求范围、交付日期和预算来源。',
                     'control': '经办与批准由不同岗位办理。', 'evidence': '需求说明及预算记录。'},
                    {'id': 'check', 'kind': 'decision', 'title': '材料完整且预算可用？', 'x': 520, 'y': 0,
                     'org_id': 'ORG-HQ-02', 'role': '采购复核岗', 'responsibility': '核对需求和预算是否对应。'},
                    {'id': 'approve', 'title': '按授权批准', 'x': 520, 'y': 220, 'org_id': 'ORG-HQ-02',
                     'role': '授权审批人', 'responsibility': '在有效授权范围内作出办理意见。', 'authority': '超出权限的按授权清单转交。'},
                    {'id': 'end', 'kind': 'end', 'title': '执行采购并归档', 'x': 260, 'y': 220}],
                'edges': [{'id': a + '-' + b, 'source': a, 'target': b, 'label': label} for a, b, label in
                          [('start', 'prepare', ''), ('prepare', 'check', ''), ('check', 'approve', '是'),
                           ('check', 'prepare', '补充材料'), ('approve', 'end', '')]], 'notes': NOTICE}
    position = {'type': 'position', 'subtype': '数据管理岗位', 'purpose': '明确字段定义、映射维护与版本发布的职责。',
                'basis': '虚构数据管理岗位说明 1.0，仅用于岗位建模演示。', 'scope': '演示数据治理中心的标准字段维护工作。',
                'org_ids': ['ORG-HQ-13'], 'person_ids': ['PERSON-AUTH-DEMO-002'], 'effective_from': '2026-01-01',
                'sections': [
                    {'id': 'definition', 'title': '字段定义', 'content': '与业务负责人确认名称、类型、单位和适用范围，保留来源材料与口径说明。'},
                    {'id': 'mapping', 'title': '映射维护', 'content': '逐列核对原表与标准字段，记录转换方式，不覆盖原始数据。'},
                    {'id': 'version', 'title': '审核与版本', 'content': '修改先保存草稿，提交业务审核后发布；口径变化时检查已有映射并保留历史版本。'}], 'notes': NOTICE}
    for index, title, org_id, spec in [(1, '采购需求办理流程', 'ORG-HQ-02', workflow),
                                      (2, '数据管理员岗位规范', 'ORG-HQ-13', position)]:
        ident, material_id = f'R-AUTH-DEMO-{index:03d}', f'MAT-AUTH-DEMO-{index:03d}'
        if c.execute('SELECT 1 FROM entities WHERE id=?', (ident,)).fetchone():
            continue
        body = '# ' + title + '\n\n' + NOTICE + '\n\n' + spec['purpose'] + '\n\n' + spec['scope']
        body += '\n\n' + '\n'.join(x['title'] + '：' + x.get('responsibility', x.get('content', ''))
                                      for x in spec.get('nodes', []) + spec.get('sections', []))
        _source(c, material_id, title + '·编制说明', body, org_id, stamp)
        spec['source_refs'] = [{'material_id': material_id, 'quote': spec['purpose']}]
        normalized = validate_refs(c, Spec.model_validate(spec).model_dump(), org_id)
        checks = checks_for(normalized)
        if checks['errors']:
            raise ValueError('Invalid authority example: ' + '; '.join(checks['errors']))
        payload = make_payload(normalized)
        payload.update(is_demo=True, demo_notice=NOTICE)
        c.execute('INSERT INTO entities VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                  (ident, 'rule', title, spec['purpose'], '组织管理', org_id, 'published', 'demo', dump(payload), stamp, stamp, 1))
        sync_relations(c, get_asset(c, ident))
        log(c, ident, '登记演示权责规范', NOTICE, '系统演示初始化')
