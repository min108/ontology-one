"""Explicit, tenant-local demonstration data with conservative safe removal.

Fixtures start as normal drafts. Loading never impersonates a reviewer or calls
a model. The removal receipt fingerprints every owned row and refuses removal
after edits, new work or references; it never expands a deletion to user data.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
import copy
import csv
import hashlib
import io
import json
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, StrictBool

from . import demo_data as data
from .db import connect, current_org_id, entity_json, log, now
from .security.context import require_context
from .security.store import append_audit, assert_principal_current
from .storage import read_bytes, scoped_path, stored_path, upload_path, write_bytes

router = APIRouter(prefix='/demo', tags=['演示资料'])
PREFIX = 'DEMO07-'
TABLE = 'demo_installations'


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)


def _decode(value):
    return json.loads(value) if isinstance(value, str) else value


def _principal(*, write=False):
    p = require_context()
    if (p.identity_kind != 'session' or p.must_change_password or
            (write and (p.role != 'admin' or p.readonly or p.tenant_id != p.home_tenant_id))):
        raise HTTPException(403, '请由管理员切回所属机构管理演示资料。')
    return assert_principal_current(p, require_write=write)


class Confirm(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    confirm: StrictBool


def _confirmed(body):
    _principal(write=True)
    if not body.confirm:
        raise HTTPException(422, '请先确认在当前机构中操作演示资料。')


def _ensure(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS demo_installations (
      install_key VARCHAR(100) PRIMARY KEY,created_by VARCHAR(191) NOT NULL,
      entity_ids JSON NOT NULL,source_ids JSON NOT NULL,snapshots LONGTEXT NOT NULL,
      files JSON NOT NULL,created_at VARCHAR(40) NOT NULL,state VARCHAR(20) NOT NULL DEFAULT 'active'
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci''')


def _receipt(conn):
    try:
        row = conn.execute('SELECT * FROM demo_installations WHERE install_key=?', (data.PACK,)).fetchone()
    except Exception as exc:
        if getattr(exc, 'args', (None,))[0] == 1146:
            return None
        raise
    if not row:
        return None
    value = dict(row)
    for key in ('entity_ids', 'source_ids', 'snapshots', 'files'):
        value[key] = _decode(value[key])
    return value


@contextmanager
def _locked(conn):
    name = 'demo-' + hashlib.sha256(require_context().tenant_id.encode()).hexdigest()[:40]
    if conn.execute('SELECT GET_LOCK(?,10)', (name,)).fetchone()[0] != 1:
        raise HTTPException(409, '演示资料正在处理，请稍后重试。')
    try:
        yield
    finally:
        conn.execute('SELECT RELEASE_LOCK(?)', (name,))


def _selectors(ids, source_ids):
    """Only fixed table/column identifiers become SQL; IDs remain parameters."""
    placeholders = ','.join('?' for _ in ids)
    owned = ' IN (' + placeholders + ')'
    source_owned = ' IN (' + ','.join('?' for _ in source_ids) + ')'
    result = []
    for table, column in (
        ('entities', 'id'), ('entity_access', 'entity_id'), ('entity_ownership', 'entity_id'),
        ('versions', 'entity_id'), ('activities', 'entity_id'), ('chunks', 'material_id'),
        ('source_versions', 'material_id'), ('source_anchors', 'material_id'),
        ('reading_versions', 'material_id'), ('reading_drafts', 'material_id'),
        ('archived_reading_drafts', 'material_id'), ('dataset_rows', 'dataset_id'),
        ('dataset_sheets', 'dataset_id'), ('dataset_row_sources', 'dataset_id'),
        ('dataset_ai_reviews', 'dataset_id'), ('dataset_import_receipts', 'dataset_id'),
        ('dataset_mapping_versions', 'dataset_id'), ('jobs', 'entity_id'),
        ('skill_documents', 'entity_id'), ('skill_revisions', 'entity_id'), ('skill_checks', 'entity_id'),
    ):
        result.append((table, column + owned, ids))
    result.extend([
        ('relations', 'source' + owned + ' OR target' + owned, ids + ids),
        ('mappings', 'dataset_id' + owned + ' OR standard_id' + owned, ids + ids),
        ('chunk_anchors', 'chunk_id IN (SELECT id FROM chunks WHERE material_id' + owned + ')', ids),
        ('source_connections', 'id' + source_owned, source_ids),
        ('source_grants', 'source_id' + source_owned, source_ids),
        ('source_resources', 'source_id' + source_owned, source_ids),
        ('source_schema_versions', 'resource_id IN (SELECT id FROM source_resources WHERE source_id' + source_owned + ')', source_ids),
        ('source_mappings', 'source_id' + source_owned, source_ids),
        ('source_mapping_versions', 'mapping_id IN (SELECT id FROM source_mappings WHERE source_id' + source_owned + ')', source_ids),
    ])
    return result


def _snapshot(conn, ids, source_ids, *, lock=False):
    result = {}
    for table, where, args in _selectors(ids, source_ids):
        rows = [dict(row) for row in conn.execute('SELECT * FROM ' + table + ' WHERE ' + where + (' FOR UPDATE' if lock else ''), args)]
        encoded = _json(sorted((_json(row) for row in rows)))
        result[table] = {'count': len(rows), 'sha256': hashlib.sha256(encoded.encode()).hexdigest()}
    return result


def _external_references(conn, ids):
    marks = ','.join('?' for _ in ids)
    clauses = ' OR '.join('CAST(payload AS CHAR CHARACTER SET utf8mb4) LIKE ?' for _ in ids)
    row = conn.execute('SELECT id FROM entities WHERE id NOT IN (' + marks + ') AND (' + clauses + ') LIMIT 1',
                       ids + ['%' + ident + '%' for ident in ids]).fetchone()
    return bool(row)


def _changes(conn, receipt, *, lock=False):
    current = _snapshot(conn, receipt['entity_ids'], receipt['source_ids'], lock=lock)
    changed = [table for table, value in current.items() if value != receipt['snapshots'].get(table)]
    if _external_references(conn, receipt['entity_ids']):
        changed.append('external_references')
    for item in receipt['files']:
        try:
            content = read_bytes(scoped_path(item['path'], must_exist=True))
            if hashlib.sha256(content).hexdigest() != item['sha256']:
                changed.append('files')
        except (OSError, HTTPException):
            changed.append('files')
    return sorted(set(changed))


def _insert_entity(conn, ident, kind, title, summary, payload=None, status='draft'):
    if kind not in {'data', 'rule', 'knowledge', 'material', 'dataset', 'project', 'task'} or not isinstance(title, str) or not isinstance(summary, str):
        raise ValueError('Invalid demonstration fixture')
    stamp = now()
    body = {**(payload or {}), 'demo': True, 'demo_pack': data.PACK,
            'demo_notice': '全部为模拟资料，不用于真实业务结论。'}
    conn.execute('INSERT INTO entities VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                 (ident, kind, '【演示】' + title, summary, '模拟业务验证', current_org_id(), status,
                  'demo', _json(body), stamp, stamp, 1))
    log(conn, ident, '加载演示草稿', '固定合成资料；未代替人工审核发布。')
    return ident


def _csv(rows):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writerow({name: data.FIELDS[name][0] + ('（元）' if data.FIELDS[name][2] == '元' else '') for name in rows[0]})
    writer.writerows(rows)
    return ('\ufeff' + stream.getvalue()).encode('utf-8')


def _files():
    return {'contracts.csv': _csv(data.CONTRACTS), 'payments.csv': _csv(data.PAYMENTS),
            'payment-rules.md': data.RULE_TEXT.encode('utf-8'), 'interview.md': data.INTERVIEW_TEXT.encode('utf-8'),
            'authority-review.md': data.AUTHORITY_TEXT.encode('utf-8'), 'special-project.md': data.SPECIAL_TEXT.encode('utf-8')}


def _write_file(path, content, written):
    try:
        write_bytes(path, content, overwrite=False)
    except FileExistsError:
        raise HTTPException(409, '同名演示原件已存在，未覆盖任何文件。请联系管理员核对。') from None
    written.append(path)


def _seed(conn, written):
    from . import datasets, materials, projects, skills, sources
    from .provenance import ensure_source_version
    ids = []
    for field, (title, kind, unit, definition) in data.FIELDS.items():
        ids.append(_insert_entity(conn, PREFIX + 'FIELD-' + field.upper(), 'data', title,
            definition, {'data_form': 'standard_field', 'data_type': kind, 'unit': unit,
                         'definition': definition, 'field_name': field, 'example': str(data.CONTRACTS[0].get(field, data.PAYMENTS[0].get(field)))}))
    rule = PREFIX + 'RULE-PAYMENT'
    ids.append(_insert_entity(conn, rule, 'rule', '大额合同核对要求', '金额大于100000元时核实审批，等于阈值不触发；附件空值只代表未核实。',
        {'content': data.RULE_TEXT, 'applicability': '只适用于模拟记录', 'condition': '合同金额 > 100000元', 'exceptions': '附件空值不作为违规结论'}))
    knowledge = PREFIX + 'KNOWLEDGE-GUIDE'
    ids.append(_insert_entity(conn, knowledge, 'knowledge', '合同与付款核对方法', '合同一份一行，付款一笔一行；关联前按合同编号汇总，保留核实事项。',
        {'content': data.INTERVIEW_TEXT, 'knowledge_type': '工作方法', 'applicability': '模拟金额核对演示'}))
    entity, _ = skills._create_document(conn, '【演示】合同付款余额核对', '输入两个模拟金额，计算非负余额并提示超付，不作合规判断。',
                                        copy.deepcopy(data.SKILL_FILES), copy.deepcopy(data.SKILL_MANIFEST))
    skill_id = entity['id']; ids.append(skill_id)
    payload = dict(entity['payload'], demo=True, demo_pack=data.PACK, demo_notice='模拟算术技能，须测试并由另一真实账号审核发布。')
    conn.execute('UPDATE entities SET source_kind=?,payload=? WHERE id=?', ('demo', _json(payload), skill_id))
    for name, text in [('payment-rules.md', data.RULE_TEXT), ('interview.md', data.INTERVIEW_TEXT)]:
        ident = PREFIX + 'MATERIAL-' + ('RULES' if name.startswith('payment') else 'INTERVIEW')
        content = text.encode('utf-8'); path = upload_path(PREFIX + name, create_parent=True)
        _write_file(path, content, written)
        _, blocks, warnings = materials.read_source(content, '.md')
        ids.append(_insert_entity(conn, ident, 'material', '合同付款核对说明' if name.startswith('payment') else '业务人员访谈记录',
            '已保存模拟原件，供分段、阅读与有出处萃取验证。',
            {'filename': '【演示】' + name, 'stored_name': path.name, 'format': 'MD', 'file_size': len(content),
             'sha256': hashlib.sha256(content).hexdigest(), 'original_text': text, 'source_blocks': blocks,
             'parse_warnings': warnings, 'chunk_count': 0, 'segmentation_status': 'unsegmented',
             'candidates': [], 'coverage': {}, 'processing_mode': None}))
        ensure_source_version(conn, ident, text, metadata={'demo': True, 'filename': '【演示】' + name})
    for key, rows in data.TABLES.items():
        ident = PREFIX + 'DATASET-' + key.upper(); content = _csv(rows)
        path = upload_path(PREFIX + key + '.csv', 'datasets', create_parent=True)
        _write_file(path, content, written)
        parsed = datasets.parse_source(path, path.name)
        count, names = 0, []
        for position, sheet in enumerate(parsed):
            prepared = datasets.prepare_sheet(sheet, 1, 1); names.append(prepared['name'])
            count += datasets.insert_sheet(conn, ident, prepared, position, count, current_org_id())
        ids.append(_insert_entity(conn, ident, 'dataset', '合同台账' if key == 'contracts' else '付款明细',
            f'{count}条模拟记录，原始字段、映射与台账发布可继续按业务流程验证。',
            {'row_count': count, 'sheet_names': names, 'original_name': '【演示】' + key + '.csv', 'source_path': stored_path(path),
             'sha256': hashlib.sha256(content).hexdigest(), 'file_size': len(content), 'batch_number': 1,
             'reporting_org_id': current_org_id(), 'published_for_analysis': False, 'analysis_readiness': 'not_ready',
             'grain': '待确认', 'ingestion': '模拟台账；请核对合同与付款明细的不同粒度。'}))
    project = PREFIX + 'PROJECT-VERIFY'
    ids.append(_insert_entity(conn, project, 'project', '合同付款验证专题', '按六类工作验证素材、规则、数据字典、技能、经验及关系。',
        {'objective': '用少量模拟记录走完建设、审核与只读应用流程。', 'scope': '仅本演示包；不接触真实业务系统。',
         'expected_outputs': '经过业务核对与异人审核的演示成果', 'progress': 0, 'total_tasks': 6, 'completed_tasks': 0}))
    for index, task in enumerate(projects.TASK_TYPES, 1):
        ident = PREFIX + 'TASK-' + task['value'].upper()
        ids.append(_insert_entity(conn, ident, 'task', task['label'] + '验证', task['description'],
            {'project_id': project, 'task_type': task['value'], 'instruction': task['description'], 'owner': '',
             'checklist': [{'item': value, 'done': False, 'required': True} for value in task['checks']]}))
        conn.execute('INSERT INTO relations VALUES(?,?,?,?,?,?,?)',
                     (PREFIX + 'REL-TASK-' + str(index), project, ident, 'contains', '组织开展', 'confirmed', _json({'demo_pack': data.PACK})))
    relations = [(rule, PREFIX + 'FIELD-AMOUNT_YUAN', 'uses_data', '核对金额依据'),
                 (skill_id, rule, 'based_on', '参考核对边界'),
                 (knowledge, rule, 'explains', '解释核对方法')]
    for index, (source, target, kind, label) in enumerate(relations):
        conn.execute('INSERT INTO relations VALUES(?,?,?,?,?,?,?)',
                     (PREFIX + 'REL-ASSET-' + str(index), source, target, kind, label, 'confirmed', _json({'demo_pack': data.PACK})))
    stamp = now()
    conn.execute('INSERT INTO source_connections VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                 (data.SOURCE_ID, '【演示】内置模拟台账（非真实数据库）', 'demo', _json(data.SOURCE_CONFIG), 'builtin-demo', 1, 1, 1,
                  require_context().user_id, stamp, stamp, None))
    source = sources._source(conn, data.SOURCE_ID)
    sources._persist_discovery(conn, source, data.resources())
    return ids, [data.SOURCE_ID]


def _items(conn, ids):
    marks = ','.join('?' for _ in ids)
    return [dict(row) for row in conn.execute('SELECT id,title,kind,status,version FROM entities WHERE id IN (' + marks + ') ORDER BY kind,title', ids)]


def _status(conn, receipt):
    if not receipt:
        return {'loaded': False, 'notice': '仅按需加载少量模拟资料；不会自动调用模型，也不会连接真实业务数据库。', 'items': [], 'source_id': ''}
    changed = _changes(conn, receipt)
    items = _items(conn, receipt['entity_ids'])
    standards = [row for row in items if row['kind'] == 'data']
    data_ready = len(standards) == len(data.FIELDS) and all(row['status'] == 'published' for row in standards)
    mappings = [dict(row) for row in conn.execute('SELECT id,title,status FROM source_mappings WHERE source_id=? ORDER BY title', (data.SOURCE_ID,))]
    archived = receipt['state'] == 'archived'
    return {'loaded': not archived, 'archived': archived, 'pack': data.PACK, 'created_at': receipt['created_at'], 'items': items,
            'source_id': data.SOURCE_ID, 'mappings': mappings, 'data_standards_published': data_ready,
            'can_clear': not archived and not changed, 'can_restore': archived and not changed, 'protected': bool(changed),
            'notice': '演示成果均按正常流程审核；同一人不能代替另一人审核技能或数据映射。',
            'clear_notice': '演示内容已被修改、使用或引用，自动归档或恢复已停止，请保留成果并逐项核对。' if changed else
                            '演示资料已归档，原件和历史保留；可恢复原有演示资料。' if archived else
                            '尚未修改或引用，可归档本轮演示资料；原件和历史保留。'}


def _mark_pack(conn, receipt, *, archived):
    """Retain IDs and original files; concurrent references cannot dangle."""
    for ident in receipt['entity_ids']:
        entity = entity_json(conn.execute('SELECT * FROM entities WHERE id=? FOR UPDATE', (ident,)).fetchone())
        if not entity:
            raise HTTPException(409, '演示记录已变化，未更改任何内容。')
        conn.execute('INSERT INTO versions(entity_id,version,snapshot,created_at,reason) VALUES(?,?,?,?,?)',
                     (ident, entity['version'], _json(entity), now(), '归档演示资料' if archived else '恢复演示资料'))
        payload = dict(entity['payload'])
        if archived:
            payload.update(active_asset=0, demo_archived=True)
        else:
            payload.pop('active_asset', None); payload.pop('demo_archived', None)
        conn.execute('UPDATE entities SET status=?,payload=?,version=version+1,updated_at=? WHERE id=?',
                     ('archived' if archived else 'draft', _json(payload), now(), ident))
        log(conn, ident, '归档演示资料' if archived else '恢复演示资料', '保留原件、编号与历史记录。')
    conn.execute('UPDATE source_connections SET enabled=?,version=version+1,config_version=config_version+1,updated_at=? WHERE id=?',
                 (0 if archived else 1, now(), data.SOURCE_ID))
    snapshots = _snapshot(conn, receipt['entity_ids'], receipt['source_ids'])
    conn.execute('UPDATE demo_installations SET state=?,snapshots=? WHERE install_key=?',
                 ('archived' if archived else 'active', _json(snapshots), data.PACK))


@router.get('/status')
def status():
    _principal()
    with closing(connect()) as conn:
        return _status(conn, _receipt(conn))


@router.post('/load')
def load(body: Confirm):
    _confirmed(body)
    from . import datasets, skills, sources
    from .provenance import initialize_provenance
    written = []
    try:
        with closing(connect()) as conn, conn, _locked(conn):
            _ensure(conn)
            previous = _receipt(conn)
            if previous:
                if previous['state'] == 'archived':
                    if _changes(conn, previous, lock=True):
                        raise HTTPException(409, '归档后的演示资料已变化或被引用，未覆盖任何内容。请逐项核对。')
                    _mark_pack(conn, previous, archived=False)
                    result = _status(conn, _receipt(conn)); conn.commit()
                    return {**result, 'already_loaded': False, 'restored': True}
                return {**_status(conn, previous), 'already_loaded': True}
            if conn.execute('SELECT 1 FROM entities WHERE id LIKE ? LIMIT 1', (PREFIX + '%',)).fetchone() or conn.execute('SELECT 1 FROM source_connections WHERE id=?', (data.SOURCE_ID,)).fetchone():
                raise HTTPException(409, '当前机构已有同名演示记录，未覆盖任何内容。请联系管理员核对。')
            datasets.ensure_schema(conn); skills.ensure_schema(conn); sources.ensure_schema(conn); initialize_provenance(conn)
            ids, source_ids = _seed(conn, written)
            files = [{'path': stored_path(path), 'sha256': hashlib.sha256(read_bytes(path)).hexdigest()} for path in written]
            snapshots = _snapshot(conn, ids, source_ids)
            conn.execute('INSERT INTO demo_installations(install_key,created_by,entity_ids,source_ids,snapshots,files,created_at) VALUES(?,?,?,?,?,?,?)',
                         (data.PACK, require_context().user_id, _json(ids), _json(source_ids), _json(snapshots), _json(files), now()))
            result = _status(conn, _receipt(conn))
            conn.commit()
    except BaseException:
        for path in written:
            path.unlink(missing_ok=True)
        raise
    append_audit(require_context(), 'demo_loaded', data.PACK, 'success', {'entity_count': len(ids), 'synthetic': True})
    return {**result, 'already_loaded': False}


@router.post('/clear')
def clear(body: Confirm):
    _confirmed(body)
    with closing(connect()) as conn, conn, _locked(conn):
        receipt = _receipt(conn)
        if not receipt:
            return {'loaded': False, 'already_cleared': True}
        if receipt['state'] == 'archived':
            return {**_status(conn, receipt), 'already_cleared': True}
        if _changes(conn, receipt, lock=True):
            raise HTTPException(409, '演示内容已被修改、使用或引用，未归档任何资料。请保留成果或逐项核对。')
        _mark_pack(conn, receipt, archived=True)
        result = _status(conn, _receipt(conn))
        conn.commit()
    append_audit(require_context(), 'demo_archived', data.PACK, 'success', {'entity_count': len(receipt['entity_ids']), 'files_retained': True})
    return {**result, 'already_cleared': False}


@router.post('/prepare-mappings')
def prepare_mappings(body: Confirm):
    """Use the normal governed creation path, after actual standard publication."""
    _confirmed(body)
    from . import sources
    with closing(connect()) as conn, _locked(conn):
        receipt = _receipt(conn)
        if not receipt or receipt['state'] != 'active':
            raise HTTPException(409, '请先加载演示资料。')
        expected = {PREFIX + 'FIELD-' + name.upper() for name in data.FIELDS}
        standards = _items(conn, sorted(expected))
        if len(standards) != len(expected) or any(row['status'] != 'published' for row in standards):
            raise HTTPException(409, '请先在业务资产中完成六项演示数据标准的正常审核发布，再准备台账查询草稿。')
        existing = {row['resource_id'] for row in conn.execute('SELECT resource_id FROM source_mappings WHERE source_id=?', (data.SOURCE_ID,))}
        resources = [dict(row) for row in conn.execute('SELECT id,resource_key FROM source_resources WHERE source_id=?', (data.SOURCE_ID,))]
        created = 0
        for resource in resources:
            if resource['id'] in existing:
                continue
            key = resource['resource_key']; row = data.TABLES[key][0]
            fields = [{'column': name, 'field': name, 'asset_id': PREFIX + 'FIELD-' + name.upper(),
                       'meaning': data.FIELDS[name][3], 'unit': data.FIELDS[name][2]} for name in row]
            sources.create_mapping(data.SOURCE_ID, sources.MappingBody(resource_id=resource['id'],
                title='【演示】' + ('合同台账查询' if key == 'contracts' else '付款明细查询'),
                business_meaning='固定模拟记录，供语义查询与按合同编号分步关联验证；不连接真实业务系统。',
                grain='每份合同一行' if key == 'contracts' else '每笔付款一行',
                object_key=['contract_id' if key == 'contracts' else 'payment_id'], fields=fields,
                relation_keys=[] if key == 'contracts' else [{'column': 'contract_id', 'target_asset_id': PREFIX + 'FIELD-CONTRACT_ID'}]))
            created += 1
    return {'already_prepared': created == 0, 'created_count': created, 'source_id': data.SOURCE_ID}


@router.get('/files/{name}')
def sample_file(name: str):
    _principal()
    files = _files()
    if name not in files:
        raise HTTPException(404, '未找到演示文件。')
    return Response(files[name], media_type='text/csv; charset=utf-8' if name.endswith('.csv') else 'text/markdown; charset=utf-8',
                    headers={'Content-Disposition': "attachment; filename*=UTF-8''" + quote('演示-' + name), 'Cache-Control': 'no-store'})
