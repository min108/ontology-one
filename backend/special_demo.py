"""A compact, fictional cross-document data-definition workspace."""
import hashlib
import json

from .db import ROOT, log
from .special_projects import insert, edge, _evidence, candidate, init_special_schema
from .provenance import ensure_source_version, register_chunk_anchor, normalize_source_ref

PID = 'PRJ-SPECIAL-001'
MATTER_ID = 'MATTER-GENERIC-001'
NOTICE = '全部内容均为虚构演示，用于体验材料归集、出处定位和本体资产提炼。'


def seed_special_demo(c):
    init_special_schema(c)
    if c.execute('SELECT 1 FROM entities WHERE id=?', (PID,)).fetchone():
        return
    finding = '合同台账的金额字段使用万元，项目计划表的预算字段使用元，汇总前需要确认单位和转换口径。'
    method = '保留原始金额和原单位，统一按元汇总；万元转换为元时乘以10000，并记录转换版本。'
    boundary = '本例只适用于人民币金额；其他币种、税额和汇率口径需单独确认。'
    payload = {'project_mode': 'special_review', 'work_type': '数据标准化成果整理', 'owner': '演示数据建设小组',
               'objective': '从两份业务材料提炼可复用的合同金额字段说明。',
               'scope': '示例业务公司的虚构采购合同与项目计划数据。', 'work_period': '2026-01-01 至 2026-09-30',
               'original_work_status': '业务材料已归集', 'original_work_conclusion': '单位差异已经确认，标准定义待业务审核。',
               'subject_org_ids': ['ORG-SUB-01'], 'role_periods': [], 'material_register': {}, 'asset_candidates': [],
               'progress': 60, 'is_demo': True, 'demo_notice': NOTICE}
    insert(c, 'project', '合同金额口径统一', '两份材料、一项业务事项，体验跨材料提炼与人工采用。', payload,
           domain='数据治理', org_id='ORG-HQ-13', source_kind='demo', status='active', eid=PID)
    insert(c, 'matter', '合同金额单位换算', finding,
           {'project_id': PID, 'description': finding, 'original_finding': finding, 'original_conclusion': '两类原表使用不同金额单位。',
            'original_source_note': '合同字段说明与汇总口径说明。', 'subject_org_ids': ['ORG-SUB-01'],
            'work_period': payload['work_period'], 'review_state': 'checked', 'review_note': '已逐字核对两份演示原文。',
            'is_demo': True, 'demo_notice': NOTICE},
           domain='数据治理', org_id='ORG-SUB-01', source_kind='demo', status='approved', eid=MATTER_ID)
    edge(c, PID, MATTER_ID, 'contains_matter', '纳入业务事项')
    folder = ROOT / 'data/uploads/demo'
    folder.mkdir(parents=True, exist_ok=True)
    refs = []
    documents = [('MAT-SPECIAL-001', '合同原表字段说明', 'working_paper', finding),
                 ('MAT-SPECIAL-002', '金额汇总口径说明', 'explanation', method)]
    for ident, title, category, paragraph in documents:
        text = '# ' + title + '\n\n' + NOTICE + '\n\n' + paragraph + '\n\n' + boundary
        path = folder / (ident + '.md')
        path.write_text(text, encoding='utf-8')
        source_payload = {'filename': title + '.md', 'format': 'MD', 'source_path': path.relative_to(ROOT).as_posix(),
                          'original_filename': title + '.md', 'original_text': text, 'sha256': hashlib.sha256(text.encode()).hexdigest(),
                          'project_id': PID, 'matter_id': MATTER_ID, 'data_owner': 'ORG-HQ-13',
                          'subject_org_ids': ['ORG-SUB-01'], 'document_stage': 'final', 'work_period': payload['work_period'],
                          'segmentation_status': 'confirmed', 'segmentation_mode': 'original_paragraphs', 'source_kind': 'demo',
                          'is_demo': True, 'demo_notice': NOTICE, 'chunk_count': 4, 'candidates': []}
        insert(c, 'material', title, paragraph, source_payload, domain='数据治理', org_id='ORG-HQ-13',
               source_kind='demo', status='published', eid=ident)
        edge(c, PID, ident, 'contains', '归集原始材料', {'role': 'source_material'})
        edge(c, MATTER_ID, ident, 'references_material', '关联事项材料', {'role': 'source_material'})
        source = ensure_source_version(c, ident, text, metadata={'filename': title + '.md', 'demo': True})
        cursor = 0
        for index, part in enumerate(text.split('\n\n'), 1):
            chunk_id = ident + '::p' + str(index)
            start = text.index(part, cursor)
            cursor = start + len(part)
            c.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?,?)',
                      (chunk_id, ident, index, '原始段落', part, f'第{index}段', 'completed'))
            register_chunk_anchor(c, ident, chunk_id, source['id'], start, cursor, f'第{index}段')
        ref = normalize_source_ref(c, ident, {'chunk_id': ident + '::p3', 'quote': paragraph})
        ref.update(title=title, source_id=ident, matter_id=MATTER_ID)
        refs.append(ref)
        payload['material_register'][ident] = {'category': category, 'disposition': 'usable', 'classification_confirmed': True,
                                               'reviewed': True, 'note': '已确认演示原文与金额单位。', 'batch': '通用演示01', 'document_stage': 'final'}
    _evidence(c, PID, MATTER_ID, refs[0], 'supports', '记录两种原表单位差异。', 'confirmed')
    _evidence(c, PID, MATTER_ID, refs[1], 'explains', '保留经说明的单位转换方法。', 'confirmed')
    draft = candidate(c, PID, {'id': 'CAND-SPECIAL-001', 'kind': 'data', 'title': '合同金额统一口径',
                               'summary': method, 'content': method, 'boundary': boundary,
                               'questions': '采用前请确认是否含税及所适用的业务范围。',
                               'matter_ids': [MATTER_ID], 'source_refs': refs}, 'demo_prepared')
    draft['demo_notice'] = '由本地虚构材料预置，等待人工核对；未调用在线模型。'
    payload['asset_candidates'].append(draft)
    c.execute('UPDATE entities SET payload=? WHERE id=?', (json.dumps(payload, ensure_ascii=False), PID))
    log(c, PID, '建立通用成果整理示例', '两份原文、两条依据和一项待采用字段候选均已建立出处关联。', '系统演示初始化')
