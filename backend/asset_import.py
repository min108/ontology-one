"""Transactional Excel import and standard templates for data, rule and algorithm assets."""
from __future__ import annotations

import hashlib
import io
import json
import re
import uuid
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

from .db import connect, entity_json, log, now

router = APIRouter()
from .storage import upload_path, stored_path, open_file, read_bytes
MAX_UPLOAD = 30 * 1024 * 1024
KIND_NAMES = {'data': '数据资产', 'rule': '规则资产', 'algorithm': '算法资产'}

FIELDS = {
    'data': ['数据项编码', '二级分类', '三级分类', '数据项名称', '数据形态', '资产归属', '业务含义', '参考形式', '值域与约束', '标准依据', '正例', '反例', '敏感级别', '关联规则资产'],
    'algorithm': ['算法编码', '业务领域', '子类别', '算法类型', '算法逻辑名称', '适用场景', '数据需求', '分析方法', '判定规则', '输出模板', '关联规则资产'],
    'rule': ['规则编码', '规则类型', '二级分类', '规则内容（法规原文·逐字）', '效力位阶', '规范依据', '条款号', '法规编号', '关联数据项', '关联算法', '资产来源'],
}
REQUIRED = {
    'data': ['数据项编码', '二级分类', '三级分类', '数据项名称', '数据形态', '资产归属', '业务含义', '敏感级别'],
    'algorithm': ['算法编码', '业务领域', '子类别', '算法类型', '算法逻辑名称', '适用场景', '数据需求', '分析方法', '判定规则'],
    'rule': ['规则编码', '规则类型', '二级分类', '规则内容（法规原文·逐字）', '规范依据', '资产来源'],
}
ENUMS = {
    '数据形态': ['结构化', '非结构化', '双载体'],
    '资产归属': ['组织管理', '经营管理', '数据治理', '外部数据'],
    '敏感级别': ['公开', '内部', '敏感', '核心'],
    '算法类型': ['数据校验', '统计分析', '关系识别'],
    '资产来源': ['外部法规', '内部制度'],
    '规则类型': ['T1 权责规范', 'T2 禁止规定', 'T3 义务程序', 'T4 指标阈值', 'T5 尺度参照'],
}
DOMAINS = {
    '01': '组织管理', '02': '公司治理', '03': '战略规划与经营', '04': '招标采购',
    '05': '财务资金', '06': '费用薪酬福利', '07': '投资并购', '08': '产权资产交易',
    '09': '关联交易与利益冲突', '10': '人力资源', '11': '企业文化', '12': '合规法务',
    '13': '质量改进', '14': '银行业务', '15': '证券业务', '16': '信托业务', '17': '保险业务',
    '18': '期货租赁消金财务AMC', '19': '工程建设', '20': '房地产业务', '21': '先进制造与材料',
    '22': '资源能源', '23': '新消费业态', '24': '境外资产与跨境', '25': '安全环保质量',
    '26': '科技数据安全', '27': '数据治理', '28': '外部数据',
}
PATTERNS = {
    'data': re.compile(r'D\.\d{2}\.\d{2}\.\d{3}'),
    'algorithm': re.compile(r'A\.\d{2}\.\d{2}\.\d{3}'),
    'rule': re.compile(r'R\.\d{2}\.\d{2}\.\d{3}\.T[1-5]'),
}
WIDTHS = {'编码': 22, '名称': 30, '内容': 60, '含义': 42, '场景': 42, '需求': 48, '方法': 55, '规则': 50, '模板': 45, '依据': 38, '正例': 35, '反例': 35}


def jd(value):
    return json.dumps(value, ensure_ascii=False, default=str)


def text(value, preserve=False):
    if value is None:
        return ''
    if isinstance(value, (datetime, date)):
        return value.isoformat()[:10]
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    result = str(value)
    return result if preserve else result.strip()


def split_refs(value):
    return [x.strip() for x in re.split(r'[；;]', text(value)) if x.strip()]


def domain_from_sheet(sheet, ident):
    cleaned = re.sub(r'^\d{1,2}[\s_\-、]*', '', sheet).strip()
    if cleaned in ('资产导入', '总表', '明细', 'Sheet', 'Sheet1'):
        cleaned = ''
    if cleaned:
        return cleaned
    parts = ident.split('.')
    return DOMAINS.get(parts[1], '未分类') if len(parts) > 1 else '未分类'


def sheet_rows(workbook, kind):
    """Read either the standard sheet or the original multi-sheet ledger."""
    candidates = []
    if kind == 'algorithm' and '总表' in workbook.sheetnames:
        candidates = [workbook['总表']]
    else:
        candidates = list(workbook.worksheets)
    records, recognized = [], []
    for ws in candidates:
        rows = list(ws.iter_rows(values_only=True))
        header_index = None
        headers = []
        for index, row in enumerate(rows[:12]):
            current = [text(x) for x in row]
            code_header = FIELDS[kind][0]
            if code_header in current and sum(h in current for h in FIELDS[kind]) >= min(5, len(FIELDS[kind])):
                header_index, headers = index, current
                break
        if header_index is None:
            continue
        recognized.append(ws.title)
        positions = {header: headers.index(header) for header in headers if header in FIELDS[kind]}
        for row_index, row in enumerate(rows[header_index + 1:], header_index + 2):
            values = {field: row[pos] if pos < len(row) else None for field, pos in positions.items()}
            code = text(values.get(FIELDS[kind][0]))
            if not code:
                continue
            records.append({'sheet': ws.title, 'row': row_index, 'values': values})
    return records, recognized


def parse_workbook(path, kind):
    try:
        workbook = load_workbook(io.BytesIO(read_bytes(path)), read_only=True, data_only=False)
    except Exception as exc:
        raise HTTPException(422, '无法读取工作簿，请上传未加密、有效的 .xlsx 文件。') from exc
    try:
        records, recognized = sheet_rows(workbook, kind)
    finally:
        workbook.close()
    if not recognized:
        raise HTTPException(422, f'未识别到{KIND_NAMES[kind]}表头。请下载系统标准模板，或保留原台账的字段名称。')
    if not records:
        raise HTTPException(422, '工作簿中没有可导入的资产记录。请在模板表头下方填写数据，说明页和合计行不会导入。')
    if len(records) > 20000:
        raise HTTPException(422, '单批次最多导入20,000项资产，请按业务领域分批上传。')
    errors, seen = [], {}
    for record in records:
        values, sheet, row = record['values'], record['sheet'], record['row']
        code = text(values.get(FIELDS[kind][0])).upper()
        values[FIELDS[kind][0]] = code
        location = f'工作表“{sheet}”第{row}行'
        if not PATTERNS[kind].fullmatch(code):
            errors.append(f'{location}：{FIELDS[kind][0]}格式不正确（{code or "空"}）。')
        if code in seen:
            errors.append(f'{location}：编码 {code} 与“{seen[code]}”重复；同批编码必须唯一。')
        seen[code] = f'{sheet}第{row}行'
        if kind == 'rule' and not text(values.get('资产来源')) and '资产来源' not in values:
            values['资产来源'] = '外部法规'
        for field in REQUIRED[kind]:
            if not text(values.get(field)):
                errors.append(f'{location}：缺少必填字段“{field}”。')
        for field, choices in ENUMS.items():
            value = text(values.get(field))
            if field in values and value and value not in choices:
                errors.append(f'{location}：“{field}”应为 {"、".join(choices)}，当前为“{value}”。')
        if kind == 'rule':
            code_type = code.rsplit('.T', 1)[-1] if '.T' in code else ''
            rule_type = text(values.get('规则类型'))
            if code_type and rule_type and not rule_type.startswith('T' + code_type):
                errors.append(f'{location}：规则编码末段 T{code_type} 与规则类型“{rule_type}”不一致。')
        if len(errors) >= 80:
            break
    if errors:
        suffix = '\n……错误较多，本次仅列前80项。' if len(errors) >= 80 else ''
        raise HTTPException(422, '校验未通过，未写入任何资产：\n' + '\n'.join(errors) + suffix)
    return records


def source_ref(material_id, title, record, chunk_id, excerpt):
    locator = f"'{record['sheet']}'!第{record['row']}行"
    return {'material_id': material_id, 'id': material_id, 'title': title, 'locator': locator,
            'chunk_id': chunk_id, 'excerpt': text(excerpt, True)[:360]}


def build_asset(kind, record, material_id, material_title, chunk_id, batch):
    v = record['values']
    code = text(v[FIELDS[kind][0]]).upper()
    sheet, row = record['sheet'], record['row']
    ref = source_ref(material_id, material_title, record, chunk_id,
                     v.get('业务含义') if kind == 'data' else v.get('适用场景') if kind == 'algorithm' else v.get('规则内容（法规原文·逐字）'))
    common = {'code': code, 'source_locator': f"'{sheet}'!第{row}行", 'source_refs': [ref],
              'source': {'file': material_title, 'sheet': sheet, 'row': row}, 'batch': batch,
              'active_asset': True, 'import_managed': True}
    if kind == 'data':
        content = '\n'.join(f'{label}：{text(v.get(field), True)}' for label, field in
                            [('业务含义', '业务含义'), ('参考形式', '参考形式'), ('值域与约束', '值域与约束'),
                             ('标准依据', '标准依据'), ('正例', '正例'), ('反例', '反例')])
        payload = {**common, 'category': text(v.get('二级分类')), 'subcategory': text(v.get('三级分类')),
                   'data_form': text(v.get('数据形态')), 'ownership': text(v.get('资产归属')),
                   'definition': text(v.get('业务含义'), True), 'format': text(v.get('参考形式'), True),
                   'constraints': text(v.get('值域与约束'), True), 'standard_basis': text(v.get('标准依据'), True),
                   'positive_example': text(v.get('正例'), True), 'negative_example': text(v.get('反例'), True),
                   'sensitivity': text(v.get('敏感级别')), 'related_rules': split_refs(v.get('关联规则资产')),
                   'content': content, 'source_id': material_id, 'maturity': '标准数据元目录，非已接入业务数据',
                   'reference_status': '分类级候选关联'}
        return {'id': code, 'kind': kind, 'title': text(v.get('数据项名称')), 'summary': text(v.get('业务含义'), True),
                'domain': domain_from_sheet(sheet, code), 'status': 'draft', 'payload': payload}
    if kind == 'algorithm':
        content = '\n\n'.join(f'{label}\n{text(v.get(field), True)}' for label, field in
                              [('适用场景', '适用场景'), ('数据需求', '数据需求'), ('分析方法', '分析方法'),
                               ('判定规则', '判定规则'), ('输出模板', '输出模板')])
        inputs = text(v.get('数据需求'), True)
        payload = {**common, 'category': text(v.get('子类别')), 'algorithm_type': text(v.get('算法类型')),
                   'scenario': text(v.get('适用场景'), True), 'inputs': inputs,
                   'input_ids': sorted(set(re.findall(r'D\.\d{2}\.\d{2}\.\d{3}', inputs))),
                   'method': text(v.get('分析方法'), True), 'conditions': text(v.get('判定规则'), True),
                   'outputs': text(v.get('输出模板'), True), 'related_rules': split_refs(v.get('关联规则资产')),
                   'content': content, 'source_id': material_id, 'maturity': '业务分析逻辑，尚未验证为可执行算法',
                   'executable': False, 'review_note': '阈值、数据角色、例外和适用依据需分别复核；未部署为业务自动拦截。'}
        return {'id': code, 'kind': kind, 'title': text(v.get('算法逻辑名称')), 'summary': text(v.get('适用场景'), True),
                'domain': text(v.get('业务领域')) or domain_from_sheet(sheet, code),
                'status': 'draft', 'payload': payload}
    original = text(v.get('规则内容（法规原文·逐字）'), True)
    basis = text(v.get('规范依据'), True)
    clause = text(v.get('条款号'), True)
    category = text(v.get('二级分类'))
    title = f'{category} · {basis.strip("《》")} {clause}'.strip()
    payload = {**common, 'rule_type': text(v.get('规则类型')), 'category': category, 'content': original,
               'original_text': original, 'authority': text(v.get('效力位阶')), 'basis': basis, 'clause': clause,
               'law_id': text(v.get('法规编号')), 'related_data': split_refs(v.get('关联数据项')),
               'related_algorithms': split_refs(v.get('关联算法')), 'asset_source': text(v.get('资产来源')),
               'conditions': '原文条件尚待逐项拆解与审核', 'exceptions': '原文例外尚待结构化确认',
               'source_id': text(v.get('法规编号')) or material_id, 'review_note': '导入内容保留原文；关联与适用条件须结合原文逐项复核。',
               'maturity': '原文条款资产', 'reference_status': 'candidate'}
    return {'id': code, 'kind': kind, 'title': title or code, 'summary': original[:240],
            'domain': domain_from_sheet(sheet, code), 'status': 'draft', 'payload': payload}


def relation_id(source, target, typ, batch):
    return 'REL-IMPORT-' + hashlib.sha256(f'{batch}|{source}|{typ}|{target}'.encode()).hexdigest()[:28]


def add_relation(conn, source, target, typ, label, batch, locator):
    conn.execute('INSERT INTO relations(id,source,target,type,label,status,payload) VALUES(?,?,?,?,?,?,?)',
                 (relation_id(source, target, typ, batch), source, target, typ, label, 'candidate',
                  jd({'asset_import_managed': True, 'batch': batch, 'source': locator, 'semantics_verified': False})))


def refresh_import_links(conn, kind, assets, batch):
    for asset in assets:
        p = asset['payload']
        if kind in ('data', 'algorithm'):
            for value in p.get('related_rules', []):
                for rid in re.findall(r'R\.\d{2}\.\d{2}\.\d{3}\.T[1-5]', value):
                    if conn.execute("SELECT 1 FROM entities WHERE id=? AND kind='rule'", (rid,)).fetchone():
                        add_relation(conn, asset['id'], rid, 'references_rule', '声明规则依据·待核语义', batch, p['source_locator'])
        if kind == 'algorithm':
            for did in p['input_ids']:
                if conn.execute("SELECT 1 FROM entities WHERE id=? AND kind='data'", (did,)).fetchone():
                    add_relation(conn, asset['id'], did, 'requires_data', '声明数据依赖·待核语义', batch, p['source_locator'])
        elif kind == 'rule':
            for did in p['related_data']:
                if conn.execute("SELECT 1 FROM entities WHERE id=? AND kind='data'", (did,)).fetchone():
                    add_relation(conn, asset['id'], did, 'references_data', '分类级数据关联·待核', batch, p['source_locator'])
            for value in p['related_algorithms']:
                for aid in re.findall(r'A\.\d{2}\.\d{2}\.\d{3}', value):
                    if conn.execute("SELECT 1 FROM entities WHERE id=? AND kind='algorithm'", (aid,)).fetchone():
                        add_relation(conn, asset['id'], aid, 'references_algorithm', '分类级方法关联·待核', batch, p['source_locator'])
            law_id = p.get('law_id')
            if law_id and conn.execute("SELECT 1 FROM entities WHERE id=? AND kind='material'", (law_id,)).fetchone():
                add_relation(conn, asset['id'], law_id, 'derived_from', '对应法规或制度原文', batch, p['source_locator'])
    if kind == 'rule':
        # Data and algorithm ledgers may declare rules that are imported later.
        # Rebuild those declared candidates in both import orders.
        current_rules = {asset['id']: asset for asset in assets}
        rows = conn.execute("SELECT id,kind,payload FROM entities WHERE kind IN ('data','algorithm') AND payload LIKE ?", ('%related_rules%',)).fetchall()
        for row in rows:
            try:
                declared = json.loads(row['payload'] or '{}').get('related_rules', [])
            except (TypeError, ValueError):
                continue
            for value in declared:
                for rid in re.findall(r'R\.\d{2}\.\d{2}\.\d{3}\.T[1-5]', value):
                    if rid in current_rules:
                        add_relation(conn, row['id'], rid, 'references_rule', '声明规则依据·待核语义', batch,
                                     current_rules[rid]['payload']['source_locator'])
    if kind == 'data':
        current = {asset['id']: asset for asset in assets}
        for row in conn.execute("SELECT id,payload FROM entities WHERE kind='algorithm' AND payload LIKE ?", ('%input_ids%',)).fetchall():
            inputs = json.loads(row['payload'] or '{}').get('input_ids', [])
            for did in current.keys() & set(inputs):
                add_relation(conn, row['id'], did, 'requires_data', '声明数据依赖·待核语义', batch,
                             current[did]['payload']['source_locator'])
        for row in conn.execute("SELECT id,payload FROM entities WHERE kind='rule' AND payload LIKE ?", ('%related_data%',)).fetchall():
            declared = json.loads(row['payload'] or '{}').get('related_data', [])
            for value in declared:
                for did in current.keys() & set(re.findall(r'D\.\d{2}\.\d{2}\.\d{3}', value)):
                    add_relation(conn, row['id'], did, 'references_data', '分类级数据关联·待核', batch,
                                 current[did]['payload']['source_locator'])
    if kind == 'algorithm':
        current = {asset['id']: asset for asset in assets}
        for row in conn.execute("SELECT id,payload FROM entities WHERE kind='rule' AND payload LIKE ?", ('%related_algorithms%',)).fetchall():
            declared = json.loads(row['payload'] or '{}').get('related_algorithms', [])
            for value in declared:
                for aid in current.keys() & set(re.findall(r'A\.\d{2}\.\d{2}\.\d{3}', value)):
                    add_relation(conn, row['id'], aid, 'references_algorithm', '分类级方法关联·待核', batch,
                                 current[aid]['payload']['source_locator'])


def ensure_schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS asset_import_batches(
        id VARCHAR(191) PRIMARY KEY,kind VARCHAR(32) NOT NULL,filename VARCHAR(500),sha256 VARCHAR(64),
        material_id VARCHAR(191),created_count INT,updated_count INT,status VARCHAR(32),created_at VARCHAR(40)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")


@router.get('/asset-import/template/{kind}')
def download_template(kind: str):
    if kind not in KIND_NAMES:
        raise HTTPException(404, '该资产类型不提供 Excel 模板。')
    workbook = Workbook()
    ws = workbook.active
    ws.title = '资产导入'
    headers = FIELDS[kind]
    ws.append(headers)
    fill = PatternFill('solid', fgColor='4472C4')
    for index, header in enumerate(headers, 1):
        cell = ws.cell(1, index)
        cell.fill = fill
        cell.font = Font(name='微软雅黑', size=10, bold=True, color='FFFFFF')
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        cell.number_format = '@'
        width = next((value for key, value in WIDTHS.items() if key in header), 20)
        ws.column_dimensions[cell.column_letter].width = width
    ws.row_dimensions[1].height = 34
    ws.freeze_panes = 'A2'
    ws.auto_filter.ref = f'A1:{ws.cell(1, len(headers)).coordinate}'
    for row in range(2, 5002):
        for column in range(1, len(headers) + 1):
            ws.cell(row, column).number_format = '@'
    for field, choices in ENUMS.items():
        if field in headers:
            col = headers.index(field) + 1
            letter = ws.cell(1, col).column_letter
            validation = DataValidation(type='list', formula1='"' + ','.join(choices) + '"', allow_blank=field not in REQUIRED[kind])
            validation.error = '请从下拉选项中选择。'
            validation.errorTitle = '取值不符合模板规范'
            validation.prompt = '可选值：' + '、'.join(choices)
            validation.promptTitle = field
            validation.showErrorMessage = True
            validation.showInputMessage = True
            ws.add_data_validation(validation)
            validation.add(f'{letter}2:{letter}5001')
    guide = workbook.create_sheet('填写说明')
    guide_rows = [
        ['项目', '说明'],
        ['模板用途', f'{KIND_NAMES[kind]}标准空模板；在“资产导入”工作表中一行填写一项资产，不要合并单元格、插空行或添加合计行。'],
        ['必填字段', '、'.join(REQUIRED[kind])],
        ['编码格式', {'data': 'D.01.01.001', 'algorithm': 'A.01.01.001', 'rule': 'R.01.01.001.T4；末段须与规则类型一致'}[kind]],
        ['文本保留', '编码按文本保存；长文本可保留单元格内换行，导入不会把换行拆成多条记录。'],
        ['关联清单', '多个关联编码使用中文分号“；”分隔；没有关联时可留空，不要编造编码。'],
        ['导入范围', '也可上传符合字段规范的多工作表台账；算法存在“总表”时仅读取总表，避免与领域表重复。'],
        ['写入方式', '整批校验通过后以草稿写入，提交审核后才能发布；同编号资产更新时保留原版本快照并重新审核。'],
    ]
    if kind == 'rule':
        guide_rows.insert(3, ['资产来源', '必须区分“外部法规”与“内部制度”。上传不含此列的法规台账时按外部法规导入。'])
    for row in guide_rows:
        guide.append(row)
    for cell in guide[1]:
        cell.fill = fill
        cell.font = Font(name='微软雅黑', size=10, bold=True, color='FFFFFF')
        cell.alignment = Alignment(horizontal='center', vertical='center')
    guide.column_dimensions['A'].width = 20
    guide.column_dimensions['B'].width = 110
    for row in guide.iter_rows(min_row=2):
        row[0].font = Font(name='微软雅黑', bold=True)
        for cell in row:
            cell.alignment = Alignment(vertical='top', wrap_text=True)
    guide.freeze_panes = 'A2'
    output = io.BytesIO()
    workbook.save(output)
    output.seek(0)
    filename = f'Ontology-One-0.2.0-{KIND_NAMES[kind]}-标准导入模板.xlsx'
    return StreamingResponse(output, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                             headers={'Content-Disposition': f"attachment; filename*=UTF-8''{quote(filename)}"})


@router.post('/asset-import/{kind}')
def import_assets(kind: str, file: UploadFile = File(...)):
    if kind not in KIND_NAMES:
        raise HTTPException(404, '知识资产不通过此入口导入；请选择数据、规则或算法资产。')
    original_name = Path(file.filename or f'{KIND_NAMES[kind]}.xlsx').name
    if Path(original_name).suffix.lower() != '.xlsx':
        raise HTTPException(422, '请上传 .xlsx 文件；旧版 .xls 请先另存为 .xlsx。')
    batch = 'ASSET-IMPORT-' + uuid.uuid4().hex[:16].upper()
    path = upload_path(f'{batch}.xlsx', 'asset-imports', create_parent=True)
    hasher, size = hashlib.sha256(), 0
    try:
        with open_file(path, 'wb') as output:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise HTTPException(413, '文件超过30 MB，请按业务领域分批导入。')
                hasher.update(chunk)
                output.write(chunk)
        records = parse_workbook(path, kind)
        stamp = now()
        material_id = 'MAT-' + batch
        material_title = f'{KIND_NAMES[kind]}导入台账 · {original_name}'
        created = updated = 0
        with closing(connect()) as conn, conn:
            ensure_schema(conn)
            assets = []
            material_payload = {'material_type': '原始资产工作簿', 'source_file': original_name,
                                'original_filename': original_name, 'source_path': stored_path(path),
                                'sha256': hasher.hexdigest(), 'batch': batch, 'asset_kind': kind,
                                'content': '系统内一键导入的原始工作簿。每项资产保留工作表、行号和原文片段。',
                                'chunk_count': len(records), 'version_info': {'batch': batch, 'source_sha256': hasher.hexdigest()}}
            conn.execute('INSERT INTO entities(id,kind,title,summary,domain,org_id,status,source_kind,payload,created_at,updated_at,version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                         (material_id, 'material', material_title, f'{KIND_NAMES[kind]}一键导入，共{len(records)}项。', '基础资产', None,
                          'published', 'imported', jd(material_payload), stamp, stamp, 1))
            for position, record in enumerate(records, 1):
                chunk_id = f'{material_id}::p{position:06d}'
                asset = build_asset(kind, record, material_id, material_title, chunk_id, batch)
                assets.append(asset)
                content = asset['payload'].get('content') or asset['summary']
                conn.execute('INSERT INTO chunks(id,material_id,position,heading,content,locator,status) VALUES(?,?,?,?,?,?,?)',
                             (chunk_id, material_id, position, f"{asset['id']} {asset['title']}", content,
                              asset['payload']['source_locator'], 'available'))
                old_row = conn.execute('SELECT * FROM entities WHERE id=?', (asset['id'],)).fetchone()
                old = entity_json(old_row)
                if old:
                    if old['kind'] != kind:
                        raise HTTPException(422, f"编码 {asset['id']} 已被{old['kind']}对象使用，未写入任何资产。")
                    conn.execute('INSERT INTO versions(entity_id,version,snapshot,created_at,reason) VALUES(?,?,?,?,?)',
                                 (asset['id'], old['version'], jd(old), stamp, f'Excel 台账同编号更新 · {batch}'))
                    payload = {**old['payload'], **asset['payload']}
                    conn.execute('UPDATE entities SET title=?,summary=?,domain=?,status=?,source_kind=?,payload=?,updated_at=?,version=version+1 WHERE id=?',
                                 (asset['title'], asset['summary'], asset['domain'], asset['status'], 'imported', jd(payload), stamp, asset['id']))
                    updated += 1
                else:
                    conn.execute('INSERT INTO entities(id,kind,title,summary,domain,org_id,status,source_kind,payload,created_at,updated_at,version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                                 (asset['id'], kind, asset['title'], asset['summary'], asset['domain'], None, asset['status'],
                                  'imported', jd(asset['payload']), stamp, stamp, 1))
                    created += 1
                add_relation(conn, asset['id'], material_id, 'sourced_from', '来源工作簿', batch, asset['payload']['source_locator'])
                log(conn, asset['id'], 'Excel资产导入', f'{original_name}；{record["sheet"]}第{record["row"]}行；{batch}', '资产台账管理员')
            refresh_import_links(conn, kind, assets, batch)
            conn.execute('INSERT INTO asset_import_batches(id,kind,filename,sha256,material_id,created_count,updated_count,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                         (batch, kind, original_name, hasher.hexdigest(), material_id, created, updated, 'completed', stamp))
        return {'ok': True, 'kind': kind, 'batch_id': batch, 'material_id': material_id, 'total': len(records),
                'created': created, 'updated': updated, 'filename': original_name,
                'message': f'成功导入 {len(records)} 项{KIND_NAMES[kind]}：新增 {created} 项，更新 {updated} 项。'}
    except Exception:
        path.unlink(missing_ok=True)
        raise
