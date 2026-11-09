"""Small, fully synthetic first-run content for the generic ontology demo.

The baseline contains no customer documents or imported domain corpus. Records
are generated offline, validated as a graph, and inserted only into an empty
workspace. Existing user data and edits are never overwritten on restart.
"""
from __future__ import annotations

import hashlib
import json
import time
from collections import Counter
from datetime import datetime, timedelta
from .db import ROOT

MARKER = 'ORG-GROUP'
NOTICE = '虚构演示：组织、人员、资料、业务记录及规则均为合成示例，仅用于体验本体建设流程。'
BASE_DATE = datetime(2026, 10, 1, 9)


def js(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def stamp(day=0):
    return (BASE_DATE + timedelta(days=day)).isoformat(timespec='seconds')


class Records:
    def __init__(self):
        self.entities = {}
        self.relations = {}
        self.chunks = []
        self.versions = []
        self.activities = []

    def entity(self, ident, kind, title, summary='', domain='数据治理', org_id='ORG-HQ-13',
               status='published', payload=None):
        if ident in self.entities:
            raise ValueError(f'Duplicate entity {ident}')
        body = {'is_demo': True, 'demo_notice': NOTICE, **(payload or {})}
        entity = dict(id=ident, kind=kind, title=title, summary=summary, domain=domain,
                      org_id=org_id, status=status, source_kind='demo', payload=body,
                      created_at=stamp(), updated_at=stamp(), version=1)
        self.entities[ident] = entity
        return entity

    def relation(self, source, target, kind, label, payload=None):
        ident = 'REL-' + hashlib.sha256(f'{source}|{kind}|{target}'.encode()).hexdigest()[:24]
        self.relations[ident] = (ident, source, target, kind, label, 'confirmed', js(payload or {}))

    def material(self, ident, title, body):
        text = title + '\n\n' + NOTICE + '\n\n' + body
        self.entity(ident, 'material', title, body.split('\n')[0], payload={
            'filename': ident + '.txt', 'original_filename': ident + '.txt',
            'source_path': 'data/uploads/demo/' + ident + '.txt',
            'original_text': text, 'content': text, 'format': 'TXT',
            'material_type': '演示业务说明', 'sha256': hashlib.sha256(text.encode()).hexdigest(),
            'segmentation_status': 'confirmed', 'segmentation_mode': 'original_paragraphs',
            'chunk_count': 1, 'candidates': [],
        })
        self.chunks.append((ident + '::p1', ident, 1, '业务说明全文', text, '全文', 'completed'))
        return {'material_id': ident, 'id': ident, 'source_id': ident,
                'chunk_id': ident + '::p1', 'title': title, 'locator': '全文', 'quote': body}

    def validate(self):
        ids = set(self.entities)
        for _, source, target, *_ in self.relations.values():
            if source not in ids or target not in ids:
                raise ValueError(f'Dangling relation {source} -> {target}')
        for entity in self.entities.values():
            if entity['org_id'] and entity['org_id'] not in ids:
                raise ValueError(f"Unknown organization for {entity['id']}")
            for ref in entity['payload'].get('source_refs', []):
                material = self.entities.get(ref.get('material_id'))
                if not material or material['kind'] != 'material':
                    raise ValueError(f"Unknown source for {entity['id']}")
                if ref.get('quote') and ref['quote'] not in material['payload']['original_text']:
                    raise ValueError(f"Source quote mismatch for {entity['id']}")
        if len({c[0] for c in self.chunks}) != len(self.chunks):
            raise ValueError('Duplicate material chunks')
        if any(c[1] not in ids for c in self.chunks):
            raise ValueError('Unknown chunk material')
        return {'entities': len(ids), 'relations': len(self.relations), 'chunks': len(self.chunks),
                'versions': len(self.versions), 'activities': len(self.activities),
                'kinds': dict(Counter(e['kind'] for e in self.entities.values()))}


def build_records():
    out = Records()
    organizations = [
        ('ORG-GROUP', '示例集团', None, '集团'),
        ('ORG-HQ-02', '采购管理部', 'ORG-GROUP', '总部部门'),
        ('ORG-HQ-06', '财务管理部', 'ORG-GROUP', '总部部门'),
        ('ORG-HQ-07', '人力资源部', 'ORG-GROUP', '总部部门'),
        ('ORG-HQ-13', '数据治理中心', 'ORG-GROUP', '总部部门'),
        ('ORG-SUB-01', '示例业务公司', 'ORG-GROUP', '一级子公司'),
    ]
    for ident, title, parent, typ in organizations:
        out.entity(ident, 'organization', title, '虚构组织，用于演示集团共建与业务归属。',
                   '集团组织', ident, payload={'type': typ, 'group': typ, 'parent_id': parent,
                   'parent_name': '示例集团' if parent else '', 'level': 1 if parent else 0,
                   'is_lead': ident == 'ORG-HQ-13', 'hierarchy': '演示组织架构'})
        if parent:
            out.relation(ident, parent, 'belongs_to', '归属集团')

    fields = [
        ('项目编号', '文本', '项目的唯一业务标识；跨单位使用时需结合单位编号。', 'PRJ-001'),
        ('项目名称', '文本', '项目的业务名称，不能替代唯一编号。', '示例设备采购'),
        ('供应商名称', '文本', '提供商品或服务的组织名称。', '示例供应商甲'),
        ('合同金额', '数值', '合同约定金额，单位为元；不与实际支付金额混用。', '120000.00'),
        ('要求完成日期', '日期', '当前计划中约定的完成日期，格式为YYYY-MM-DD。', '2026-10-10'),
        ('实际完成日期', '日期', '业务执行完成的日期；未完成事项可以为空。', '2026-10-09'),
        ('任务状态', '文本', '任务当前办理阶段，示例取值为进行中、待验收、已完成。', '已完成'),
        ('完成材料', '文本', '任务完成时形成的交付清单、验收记录等材料目录。', '交付清单、验收记录'),
        ('任务编号', '文本', '任务的唯一业务标识，用于关联办理记录。', 'TASK-001'),
        ('付款账号', '文本', '发起支付一方的账号标识，不能使用收款账号替代。', 'DEMO-ACCOUNT-001'),
        ('投标单位', '文本', '参与项目报价的业务主体名称。', '示例供应商甲'),
        ('合同编号', '文本', '合同的唯一业务标识，用于对应版本与履约记录。', 'CONTRACT-001'),
    ]
    dictionary = '\n'.join(f'{i:02d}. {title}：{definition}' for i, (title, _, definition, _) in enumerate(fields, 1))
    ref = out.material('MAT-GEN-DICTIONARY', '集团业务字段说明（演示）', dictionary)
    for i, (title, fmt, definition, example) in enumerate(fields, 1):
        ident = f'D-GEN-{i:03d}'
        out.entity(ident, 'data', title, definition, payload={
            'code': ident, 'category': '集团基础数据', 'subcategory': '业务字段',
            'data_form': '结构化数据', 'ownership': '集团共建', 'definition': definition,
            'format': fmt, 'data_type': fmt, 'unit': '元' if title == '合同金额' else '',
            'constraints': '按字段定义填写，保留原始值及来源。', 'standard_basis': '原创演示字段说明',
            'positive_example': example, 'negative_example': '不使用其他业务含义相近的字段替代。',
            'sensitivity': '演示数据', 'content': definition, 'active_asset': True,
            'source_refs': [ref], 'source_id': ref['material_id'], 'source_locator': '全文',
        })
        out.relation(ident, ref['material_id'], 'derived_from', '来源于字段说明')

    rule_text = '任务登记完成时，应保留交付清单和验收记录；尚未完成的事项可暂不填写验收记录，并说明当前进度。'
    rule_ref = out.material('MAT-GEN-PROCESS', '项目交付资料说明（演示）', rule_text)
    out.entity('R-GEN-001', 'rule', '项目交付材料记录要求', rule_text, '项目管理', payload={
        'active_asset': True, 'rule_type': '业务规则', 'asset_source': '内部制度',
        'rule_text': rule_text, 'original_text': rule_text, 'content': rule_text, 'obligation': rule_text,
        'scope': '示例集团项目交付流程', 'conditions': '任务登记为已完成时',
        'exceptions': '未完成事项可以不提供验收记录，但需记录进度。',
        'basis': '原创项目交付资料说明', 'source_refs': [rule_ref],
        'review_note': '示例要求不构成任何真实组织的正式制度。',
    })
    out.relation('R-GEN-001', 'MAT-GEN-PROCESS', 'derived_from', '来源于业务说明')
    method = '逐行读取要求完成日期、实际完成日期和任务状态；比较有效日期，分别输出按期、延期、进行中及信息不足的记录。'
    out.entity('A-GEN-001', 'algorithm', '任务计划与完成日期比较', method, '项目管理', payload={
        'active_asset': True, 'algorithm_type': '规则计算', 'scenario': '任务期限分析',
        'category': '项目管理', 'scope': '具有明确计划与完成日期的任务台账',
        'inputs': ['要求完成日期', '实际完成日期', '任务状态', '任务编号'],
        'method': [method, '对缺失或无法识别的日期单独列示，不自动推断完成状态。'],
        'conditions': '日期可解析且业务口径已确认', 'outputs': '任务办理情况及待补信息清单',
        'boundary': '结果只反映台账记录，需要结合业务变更与验收资料解释。',
        'source_refs': [rule_ref], 'maturity': '演示方法说明',
    })
    out.relation('A-GEN-001', 'R-GEN-001', 'implements_rule', '落实业务规则')
    for ident in ('D-GEN-005', 'D-GEN-006', 'D-GEN-007', 'D-GEN-009'):
        out.relation('A-GEN-001', ident, 'uses_data', '使用字段')

    lesson = '字段名称相近不表示含义相同。合同金额、开票金额和付款金额分别对应不同业务环节；建立映射前，应先核对对象、单位、时点和粒度。'
    knowledge_ref = out.material('MAT-GEN-EXPERIENCE', '合同数据整理访谈摘录（演示）', lesson)
    out.entity('K-GEN-001', 'knowledge', '先确认业务含义，再建立字段映射', lesson, payload={
        'active_asset': True, 'knowledge_form': 'experience', 'origin_type': 'expert_record', 'content': lesson,
        'topic_label': '字段标准化', 'source_refs': [knowledge_ref],
        'article_sections': [{'id': 'context', 'title': '业务情境', 'content': '不同业务系统使用相近列名表达不同金额。', 'source_refs': [knowledge_ref]},
                             {'id': 'method', 'title': '整理方法', 'content': lesson, 'source_refs': [knowledge_ref]},
                             {'id': 'boundary', 'title': '适用边界', 'content': '字段定义需要由业务负责人确认，示例不自动适用于真实系统。', 'source_refs': []}],
    })
    out.relation('K-GEN-001', 'MAT-GEN-EXPERIENCE', 'derived_from', '来源于访谈材料')
    out.relation('K-GEN-001', 'D-GEN-004', 'references_data', '参考字段口径')
    out.entity('COL-GEN-001', 'collection', '集团数据治理实践', '收录可复用的字段整理与本体共建方法。', payload={'collection_type': 'knowledge', 'collection_scope': 'topic', 'intro': '原创虚构知识专栏：从字段口径确认开始，学习集团本体共建方法。'})
    out.relation('COL-GEN-001', 'K-GEN-001', 'includes_knowledge', '收录知识', {'adopted_version': 1, 'reason': '演示专栏初始化'})

    out.entity('PRJ-GEN-001', 'project', '合同与项目字段共建', '围绕业务字段、资料要求和分析方法开展一次小型共建。', status='active', payload={
        'owner': '数据治理共建组', 'objective': '形成可追溯的字段、规则、方法和经验。',
        'scope': '虚构集团合同与项目业务', 'progress': 50, 'total_tasks': 2, 'completed_tasks': 1,
        'work_type': '本体共建', 'source_refs': [ref, rule_ref],
    })
    tasks = [('TASK-GEN-001', '确认合同金额字段口径', 'dictionary', 'completed', 'D-GEN-004', ref),
             ('TASK-GEN-002', '完善项目交付资料规则', 'rule', 'active', 'R-GEN-001', rule_ref)]
    for ident, title, typ, state, output, source in tasks:
        done = state == 'completed'
        out.entity(ident, 'task', title, '核对业务含义、原文出处与成果范围。', status=state, payload={
            'project_id': 'PRJ-GEN-001', 'task_type': typ, 'owner': '演示共建人员',
            'source_refs': [source], 'delivery_note': '已登记字段定义与来源。' if done else '',
            'checklist': [{'item': '核对原文及业务范围', 'done': done, 'required': True},
                          {'item': '确认成果口径及关联', 'done': done, 'required': True}],
            'due_date': '2026-10-15',
        })
        out.relation('PRJ-GEN-001', ident, 'contains', '分解任务')
        out.relation(ident, source['material_id'], 'references_material', '依据材料', {'role': 'source_material', 'required': True, 'done': done})
        out.relation(ident, output, 'produces', '交付成果', {'role': 'produced_asset', 'done': done, 'adopted_version': 1})

    for entity in out.entities.values():
        out.versions.append((entity['id'], 1, js(entity), stamp(), '登记通用演示初始版本'))
        if entity['kind'] not in ('organization', 'data'):
            out.activities.append((entity['id'], 'create', '登记原创虚构示例并建立来源关联。', '演示初始化', stamp()))
    out.validate()
    return out


def seed(conn):
    started = time.perf_counter()
    if conn.execute('SELECT 1 FROM entities LIMIT 1').fetchone():
        return {'skipped': True, 'reason': 'workspace is not empty'}
    records = build_records()
    stats = records.validate()
    # Downloadable originals are derived runtime data, not repository assets.
    for entity in records.entities.values():
        if entity['kind'] == 'material':
            path = ROOT / entity['payload']['source_path']
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(entity['payload']['original_text'], encoding='utf-8')
    conn.execute('SAVEPOINT initial_ontology_seed')
    try:
        conn.executemany('INSERT INTO entities VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                         ((e['id'], e['kind'], e['title'], e['summary'], e['domain'], e['org_id'], e['status'],
                           e['source_kind'], js(e['payload']), e['created_at'], e['updated_at'], e['version']) for e in records.entities.values()))
        conn.executemany('INSERT INTO relations VALUES(?,?,?,?,?,?,?)', records.relations.values())
        conn.executemany('INSERT INTO chunks VALUES(?,?,?,?,?,?,?)', records.chunks)
        conn.executemany('INSERT INTO versions(entity_id,version,snapshot,created_at,reason) VALUES(?,?,?,?,?)', records.versions)
        conn.executemany('INSERT INTO activities(entity_id,action,detail,actor,created_at) VALUES(?,?,?,?,?)', records.activities)
        conn.execute('RELEASE SAVEPOINT initial_ontology_seed')
    except Exception:
        conn.execute('ROLLBACK TO SAVEPOINT initial_ontology_seed')
        conn.execute('RELEASE SAVEPOINT initial_ontology_seed')
        raise
    stats['elapsed_seconds'] = round(time.perf_counter() - started, 3)
    return stats
