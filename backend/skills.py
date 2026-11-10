"""Tenant-scoped Agent Skills documents, review and digest-bound evidence.

Skill instructions are content, never identity or authority. Only this module
may mutate a managed skill's package or publication state. Execution providers
receive a bounded snapshot after authorization and cannot publish their output.
"""
from __future__ import annotations

from contextlib import closing
import asyncio
import hashlib
import json
import time
import uuid
import threading
from typing import Annotated, Any, Literal
import math

from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from .db import connect, current_org_id, entity_json, log, now
from .security.context import require_context
from .security.store import append_audit, assert_principal_current

router = APIRouter(prefix='/skills', tags=['skill-development'])
BUILD = {'admin', 'modeler'}
REVIEW = {'admin', 'reviewer'}
ROLES = BUILD | REVIEW
MAX_BODY_BYTES = 3 * 1024 * 1024
MANIFEST_PATH = 'assets/ontology-one.json'

SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS skill_documents(
      entity_id VARCHAR(191) PRIMARY KEY,current_revision INT NOT NULL,
      files JSON NOT NULL,manifest JSON NOT NULL,digest CHAR(64) NOT NULL,
      last_editor_id VARCHAR(191) NOT NULL,submitted_by VARCHAR(191),updated_at VARCHAR(40) NOT NULL,
      CONSTRAINT fk_skill_document_entity FOREIGN KEY(entity_id) REFERENCES entities(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci''',
    '''CREATE TABLE IF NOT EXISTS skill_revisions(
      id VARCHAR(64) PRIMARY KEY,entity_id VARCHAR(191) NOT NULL,revision INT NOT NULL,
      files JSON NOT NULL,manifest JSON NOT NULL,digest CHAR(64) NOT NULL,
      status VARCHAR(20) NOT NULL,actor_id VARCHAR(191) NOT NULL,
      created_at VARCHAR(40) NOT NULL,reason VARCHAR(2000) NOT NULL,
      INDEX idx_skill_revision(entity_id,revision),
      CONSTRAINT fk_skill_revision_entity FOREIGN KEY(entity_id) REFERENCES entities(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci''',
    '''CREATE TABLE IF NOT EXISTS skill_checks(
      id VARCHAR(64) PRIMARY KEY,entity_id VARCHAR(191) NOT NULL,revision INT NOT NULL,
      digest CHAR(64) NOT NULL,outcome VARCHAR(40) NOT NULL,report JSON NOT NULL,
      actor_id VARCHAR(191) NOT NULL,created_at VARCHAR(40) NOT NULL,
      INDEX idx_skill_check(entity_id,revision,digest),
      CONSTRAINT fk_skill_check_entity FOREIGN KEY(entity_id) REFERENCES entities(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci''',
)


def ensure_schema(conn):
    for statement in SCHEMA:
        conn.execute(statement)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _decode(value):
    return json.loads(value) if isinstance(value, str) else value


def _connect():
    # A source grant acquired after waiting for its lock must be read from the
    # latest committed state, not an earlier REPEATABLE READ snapshot. This is
    # the first business statement (connect only sets session read-only mode).
    conn = connect()
    try:
        conn.execute('SET TRANSACTION ISOLATION LEVEL READ COMMITTED')
        return conn
    except BaseException:
        conn.close()
        raise


def _id(prefix):
    return prefix + '-' + uuid.uuid4().hex


def _fail(code, message, status=422):
    raise HTTPException(status, {'code': code, 'message': message})


def _role(roles=ROLES):
    principal = assert_principal_current(require_context())
    if (principal.identity_kind != 'session' or principal.role not in roles or
            principal.readonly or principal.must_change_password):
        _fail('skill_forbidden', '当前账号无权执行此技能开发操作。', 403)
    return principal


def _expected(entity, version):
    if entity['version'] != version:
        _fail('version_conflict', '技能已被其他操作更新，请重新加载后核对。', 409)


def is_managed_skill(conn, entity):
    """A marker alone never makes an asset executable; a document is authoritative."""
    if isinstance(entity, str):
        return bool(conn.execute('SELECT entity_id FROM skill_documents WHERE entity_id=?', (entity,)).fetchone())
    return bool((entity.get('payload') or {}).get('managed_skill')) or is_managed_skill(conn, entity['id'])


def guard_generic_mutation(conn, entity):
    if is_managed_skill(conn, entity):
        _fail('managed_skill_route', '此技能由技能开发模块维护，请在技能开发中编辑、测试和审核。', 409)


def guard_generic_entity(conn, entity_or_id=None, body=None):
    payload = body.get('payload') if isinstance(body, dict) else None
    if isinstance(payload, dict) and any(key in payload for key in ('managed_skill', 'package_digest', 'package_revision', 'package_name')):
        _fail('managed_skill_route', '技能包标识由技能开发模块维护，不能通过通用编辑设置。', 409)
    if entity_or_id is not None:
        guard_generic_mutation(conn, entity_or_id)


def _document(conn, ident, *, lock=False, shared=False):
    locking = ' FOR UPDATE' if lock else ' FOR SHARE' if shared else ''
    entity = entity_json(conn.execute('SELECT * FROM entities WHERE id=?' + locking, (ident,)).fetchone())
    row = conn.execute('SELECT * FROM skill_documents WHERE entity_id=?' + locking, (ident,)).fetchone()
    if not entity or entity['kind'] != 'skill' or not row or not entity['payload'].get('managed_skill'):
        _fail('skill_not_found', '技能包不存在；历史方法说明须先在技能开发中建立技能包。', 404)
    document = dict(row)
    document['files'] = _decode(document['files'])
    document['manifest'] = _decode(document['manifest'])
    return entity, document


def _audit(action, ident, outcome='success', **details):
    # Call sites provide only identifiers, versions, counters and fixed codes.
    append_audit(require_context(), 'skill_' + action, ident, outcome, details)


async def _body(request: Request, model):
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > MAX_BODY_BYTES:
            _fail('skill_request_limit', '技能请求超过3MiB，请减少文件内容。', 413)
    try:
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result: raise ValueError('Duplicate JSON key')
                result[key] = value
            return result
        value = json.loads(bytes(raw), object_pairs_hook=unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Invalid JSON scalar')))
        return model.model_validate(value, strict=True)
    except Exception:
        _fail('skill_invalid_request', '请求不符合技能开发契约，请核对字段、类型与范围。')


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


Identifier = Annotated[str, Field(min_length=1, max_length=191)]
Version = Annotated[StrictInt, Field(ge=1)]


def _bounded_json(value, *, max_bytes=256 * 1024, max_depth=16, max_nodes=10000):
    count = 0
    def visit(item, depth):
        nonlocal count
        count += 1
        if count > max_nodes or depth > max_depth:
            raise ValueError('JSON structure exceeds profile limits')
        if item is None or type(item) in (str, bool, int):
            return
        if type(item) is float and math.isfinite(item):
            return
        if isinstance(item, list):
            for child in item: visit(child, depth + 1)
            return
        if isinstance(item, dict) and all(isinstance(k, str) for k in item):
            for child in item.values(): visit(child, depth + 1)
            return
        raise ValueError('Invalid JSON scalar')
    visit(value, 0)
    if len(_json(value).encode('utf-8')) > max_bytes:
        raise ValueError('JSON data exceeds profile limits')
    return value


SCHEMA_KEYS = frozenset({'type', 'properties', 'required', 'additionalProperties', 'items', 'enum', 'const',
                         'minLength', 'maxLength', 'minItems', 'maxItems', 'minimum', 'maximum', 'description', 'title'})
SCHEMA_TYPES = frozenset({'object', 'array', 'string', 'integer', 'number', 'boolean', 'null'})


def validate_schema(value):
    """A bounded, reference-free JSON Schema profile; no regex or resolver IO."""
    _bounded_json(value, max_bytes=32768, max_depth=12, max_nodes=1500)
    def visit(schema, depth=0):
        if not isinstance(schema, dict) or depth > 8 or set(schema) - SCHEMA_KEYS:
            raise ValueError('Unsupported schema feature')
        kind = schema.get('type')
        if not isinstance(kind, str) or kind not in SCHEMA_TYPES:
            raise ValueError('Schema requires an explicit supported type')
        if 'properties' in schema:
            properties = schema['properties']
            if kind != 'object' or not isinstance(properties, dict) or len(properties) > 100:
                raise ValueError('Invalid object schema')
            for key, child in properties.items():
                if not 1 <= len(key) <= 200: raise ValueError('Invalid property name')
                visit(child, depth + 1)
        if 'required' in schema:
            required = schema['required']
            if (kind != 'object' or not isinstance(required, list) or len(required) > 100 or
                    any(not isinstance(item, str) for item in required) or len(set(required)) != len(required) or
                    not set(required) <= set(schema.get('properties', {}))):
                raise ValueError('Invalid required fields')
        if 'additionalProperties' in schema and (kind != 'object' or type(schema['additionalProperties']) is not bool):
            raise ValueError('Additional properties must be boolean')
        if 'items' in schema:
            if kind != 'array': raise ValueError('Items require array type')
            visit(schema['items'], depth + 1)
        if kind == 'array' and 'items' not in schema:
            raise ValueError('Arrays require an item schema')
        for key in ('minLength', 'maxLength', 'minItems', 'maxItems'):
            if key in schema and (type(schema[key]) is not int or not 0 <= schema[key] <= (100 if 'Items' in key else 10000)):
                raise ValueError('Invalid schema bound')
        for key in ('minimum', 'maximum'):
            if key in schema and (type(schema[key]) not in (int, float) or not math.isfinite(schema[key])):
                raise ValueError('Invalid numeric bound')
        for low, high in (('minimum', 'maximum'), ('minLength', 'maxLength'), ('minItems', 'maxItems')):
            if low in schema and high in schema and schema[low] > schema[high]: raise ValueError('Contradictory bounds')
        for key in ('title', 'description'):
            if key in schema and (not isinstance(schema[key], str) or len(schema[key]) > 2000):
                raise ValueError('Invalid schema annotation')
        if 'enum' in schema and (not isinstance(schema['enum'], list) or not 1 <= len(schema['enum']) <= 100):
            raise ValueError('Invalid enumeration')
    visit(value)
    from jsonschema import Draft202012Validator
    Draft202012Validator.check_schema(value)
    return value


class Step(StrictModel):
    title: Annotated[str, Field(min_length=1, max_length=200)]
    instruction: Annotated[str, Field(min_length=1, max_length=4000)]

    @field_validator('title', 'instruction')
    @classmethod
    def meaningful(cls, value):
        if not value.strip(): raise ValueError('Step must contain text')
        return value


class TestCase(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=100)]
    input: Any
    expected_output: Any

    @field_validator('input', 'expected_output')
    @classmethod
    def bounded(cls, value):
        return _bounded_json(value, max_bytes=65536)


class Dependency(StrictModel):
    entity_id: Identifier
    version: Version
    kind: Literal['data', 'rule', 'knowledge']


class ToolDependency(StrictModel):
    name: Literal['ontology_discover', 'ontology_query', 'ontology_traverse', 'ontology_explain', 'ontology_join']
    mapping_ids: Annotated[list[Identifier], Field(max_length=20)] = Field(default_factory=list)
    purpose: Annotated[str, Field(min_length=1, max_length=1000)]

    @model_validator(mode='after')
    def unique_mappings(self):
        if len(set(self.mapping_ids)) != len(self.mapping_ids): raise ValueError('Repeated mapping')
        if self.name == 'ontology_query' and not self.mapping_ids: raise ValueError('Query needs a mapping')
        if self.name == 'ontology_join' and len(self.mapping_ids) < 2: raise ValueError('Join needs two mappings')
        return self


class Manifest(StrictModel):
    schema_version: Literal['1.0'] = '1.0'
    applicability: Annotated[str, Field(max_length=4000)] = ''
    inputs: dict[str, Any] = Field(default_factory=lambda: {'type': 'object'})
    outputs: dict[str, Any] = Field(default_factory=lambda: {'type': 'object'})
    steps: Annotated[list[Step], Field(max_length=30)] = Field(default_factory=list)
    entrypoint: Annotated[str | None, Field(max_length=240)] = None
    test_cases: Annotated[list[TestCase], Field(max_length=10)] = Field(default_factory=list)
    dependencies: Annotated[list[Dependency], Field(max_length=50)] = Field(default_factory=list)
    tool_dependencies: Annotated[list[ToolDependency], Field(max_length=10)] = Field(default_factory=list)

    @field_validator('inputs', 'outputs')
    @classmethod
    def schema(cls, value):
        return validate_schema(value)

    @model_validator(mode='after')
    def unique_entries(self):
        for values in ([item.name for item in self.test_cases], [item.entity_id for item in self.dependencies],
                       [item.name for item in self.tool_dependencies]):
            if len(set(values)) != len(values): raise ValueError('Repeated manifest identifier')
        if self.entrypoint is not None and (not self.entrypoint.startswith('scripts/') or not self.entrypoint.endswith('.py') or
                '..' in self.entrypoint.split('/') or '\\' in self.entrypoint):
            raise ValueError('Entrypoint must be a Python script within scripts')
        _bounded_json(self.model_dump(mode='python'), max_bytes=256 * 1024)
        return self


class CreateBody(StrictModel):
    title: Annotated[str, Field(min_length=1, max_length=200)]
    summary: Annotated[str, Field(max_length=2000)] = ''
    package_name: Annotated[str, Field(min_length=1, max_length=64)]
    legacy_entity_id: Identifier | None = None

    @field_validator('title')
    @classmethod
    def meaningful(cls, value):
        if not value.strip(): raise ValueError('Title must contain text')
        return value.strip()


class SaveBody(StrictModel):
    expected_version: Version
    title: Annotated[str, Field(min_length=1, max_length=200)]
    summary: Annotated[str, Field(max_length=2000)] = ''
    files: Annotated[dict[str, str], Field(min_length=1, max_length=64)]
    manifest: Manifest
    reason: Annotated[str, Field(min_length=1, max_length=2000)]

    @field_validator('title', 'reason')
    @classmethod
    def meaningful(cls, value):
        if not value.strip(): raise ValueError('Value must contain text')
        return value.strip()


class VersionBody(StrictModel):
    expected_version: Version


class ActionBody(VersionBody):
    action: Literal['submit', 'publish', 'return', 'revise', 'archive']
    reason: Annotated[str, Field(min_length=1, max_length=2000)]

    @field_validator('reason')
    @classmethod
    def meaningful(cls, value):
        if not value.strip(): raise ValueError('Reason must contain text')
        return value.strip()


class AssistBody(VersionBody):
    instruction: Annotated[str, Field(min_length=1, max_length=8000)]


def portable_manifest(manifest):
    value = manifest.model_dump(mode='python') if isinstance(manifest, Manifest) else dict(manifest)
    return {key: value[key] for key in ('schema_version', 'applicability', 'inputs', 'outputs', 'steps', 'entrypoint', 'test_cases')}


def _coherent_files(files, manifest):
    from .skill_package import sync_manifest, validate_files
    try:
        result = sync_manifest(files, portable_manifest(manifest))
        validation = validate_files(result)
    except Exception:
        _fail('skill_package_invalid', '技能包路径、格式或内容不符合导入规范。')
    # Draft scripts may be incomplete, but unsafe paths/credentials/headers are
    # never accepted even into a draft. Publication requires a clean validation.
    if any(item['code'] not in {'PYTHON_SYNTAX'} for item in validation['errors']):
        _fail('skill_package_invalid', '技能包存在格式错误，请先校验并修正文件。')
    return result, validation


def _validation(files, manifest):
    from .skill_package import validate_files
    package = validate_files(files)
    errors = list(package['errors'])
    warnings = list(package['warnings'])
    try:
        profile = Manifest.model_validate(manifest, strict=True)
    except Exception:
        errors.append({'code': 'manifest_invalid', 'path': MANIFEST_PATH, 'message': '平台技能清单不符合受控格式。'})
        profile = None
    if profile:
        if not profile.applicability.strip():
            errors.append({'code': 'applicability_required', 'path': MANIFEST_PATH, 'message': '请说明技能适用条件。'})
        if not profile.steps:
            errors.append({'code': 'steps_required', 'path': MANIFEST_PATH, 'message': '请至少编写一个业务步骤。'})
        if not profile.test_cases:
            errors.append({'code': 'test_cases_required', 'path': MANIFEST_PATH, 'message': '请至少提供一个合成输入输出测试用例。'})
        if profile.entrypoint and profile.entrypoint not in files:
            errors.append({'code': 'entrypoint_missing', 'path': profile.entrypoint, 'message': '选定的测试脚本不存在。'})
        from jsonschema import Draft202012Validator
        for index, case in enumerate(profile.test_cases):
            for key, schema, value in (('input', profile.inputs, case.input), ('expected_output', profile.outputs, case.expected_output)):
                if not Draft202012Validator(schema).is_valid(value):
                    errors.append({'code': 'example_schema_mismatch', 'path': f'test_cases/{index}/{key}', 'message': '测试用例不符合声明的输入输出格式。'})
        if not profile.entrypoint:
            warnings.append({'code': 'contract_only', 'path': MANIFEST_PATH, 'message': '这是说明型技能，测试只校验输入输出契约，不执行脚本。'})
    return {'valid': not errors, 'errors': errors, 'warnings': warnings, 'digest': package.get('digest', ''),
            'file_count': package.get('file_count', 0), 'total_bytes': package.get('total_bytes', 0)}


def _dependency_state(conn, manifest, *, strict=True):
    """Pinned local ontology and source metadata; never queries source rows."""
    from . import sources
    principal = _role()
    checks, metadata = [], []
    for dependency in sorted(manifest.get('dependencies', []), key=lambda item: item['entity_id']):
        row = conn.execute('SELECT id,title,kind,status,version,payload FROM entities WHERE id=? FOR SHARE', (dependency['entity_id'],)).fetchone()
        valid = bool(row and row['kind'] == dependency['kind'] and row['status'] == 'published' and row['version'] == dependency['version'])
        if valid:
            payload = _decode(row['payload'] or '{}')
            valid = payload.get('active_asset', True) not in (False, 0, '0')
        checks.append({'kind': 'asset', 'id': dependency['entity_id'], 'version': row['version'] if row else None,
                       'current': valid, 'code': 'ok' if valid else 'dependency_changed'})
        if not valid and strict:
            _fail('skill_dependency_changed', '关联资产已变化或不可用，请重新选择当前已发布版本。', 409)
        if valid:
            metadata.append({key: row[key] for key in ('id', 'title', 'kind', 'version')})
    mapping_ids = sorted({ident for tool in manifest.get('tool_dependencies', []) for ident in tool['mapping_ids']})
    mappings = []
    for ident in mapping_ids:
        row = conn.execute('SELECT source_id FROM source_mappings WHERE id=?', (ident,)).fetchone()
        mappings.append((row['source_id'] if row else '', ident))
    for source_id, ident in sorted(mappings):
        valid = False
        proof = {}
        try:
            if not source_id: raise ValueError()
            conn.execute('SELECT id FROM source_connections WHERE id=? FOR SHARE', (source_id,)).fetchone()
            source = sources._authorize(conn, source_id, 'query')
            mapping = sources._mapping(sources._mapping_row(conn, source_id, ident))
            if mapping['status'] != 'published': raise ValueError()
            sources._validate_mapping_current(conn, source, mapping)
            valid = True
            proof = {'source_id': source_id, 'mapping_version': mapping['version'],
                     'source_config_version': source['config_version'], 'schema_hash': mapping['schema_hash']}
        except (HTTPException, ValueError):
            pass
        checks.append({'kind': 'mapping', 'id': ident, 'current': valid, 'code': 'ok' if valid else 'mapping_unavailable', **proof})
        if not valid and strict:
            _fail('skill_tool_dependency_unavailable', '工具依赖的映射、源授权或结构版本不可用。', 403)
    assert_principal_current(principal)
    return checks, metadata


def _proof_fingerprint(document, checks):
    return hashlib.sha256(_json({'package_digest': document['digest'], 'manifest': document['manifest'], 'dependencies': checks}).encode()).hexdigest()


def _history_event(conn, entity, document, reason):
    conn.execute('INSERT INTO skill_revisions(id,entity_id,revision,files,manifest,digest,status,actor_id,created_at,reason) VALUES(?,?,?,?,?,?,?,?,?,?)',
                 (_id('SKREV'), entity['id'], document['current_revision'], _json(document['files']), _json(document['manifest']),
                  document['digest'], entity['status'], require_context().user_id, now(), reason))


def _snapshot_entity(conn, entity, reason):
    conn.execute('INSERT INTO versions(entity_id,version,snapshot,created_at,reason) VALUES(?,?,?,?,?)',
                 (entity['id'], entity['version'], _json(entity), now(), reason))


def _last_check(conn, document):
    row = conn.execute('SELECT * FROM skill_checks WHERE entity_id=? AND revision=? AND digest=? ORDER BY created_at DESC,id DESC LIMIT 1',
                       (document['entity_id'], document['current_revision'], document['digest'])).fetchone()
    if not row: return None
    return {**{key: row[key] for key in ('id', 'revision', 'digest', 'outcome', 'actor_id', 'created_at')}, 'report': _decode(row['report'])}


def _detail(conn, entity, document):
    principal = _role()
    validation = _validation(document['files'], document['manifest'])
    checks, _ = _dependency_state(conn, document['manifest'], strict=False)
    validation['dependency_checks'] = checks
    validation['valid'] = validation['valid'] and all(item['current'] for item in checks)
    history = [dict(row) for row in conn.execute('SELECT revision,status,digest,actor_id,created_at,reason FROM skill_revisions WHERE entity_id=? ORDER BY created_at DESC,id DESC LIMIT 100', (entity['id'],))]
    result = {'entity': entity, 'files': document['files'], 'manifest': document['manifest'],
              'package_name': entity['payload']['package_name'], 'package_revision': document['current_revision'],
              'digest': document['digest'], 'validation': validation, 'last_test': _last_check(conn, document),
              'history': history, 'read_only': entity['status'] not in {'draft', 'returned'},
              'capabilities': {'edit': principal.role in BUILD and entity['status'] in {'draft', 'returned'},
                               'submit': principal.role in BUILD and entity['status'] in {'draft', 'returned'},
                               'review': principal.role in REVIEW and entity['status'] == 'review' and principal.user_id not in {document['last_editor_id'], document['submitted_by']},
                               'test': principal.role in BUILD and entity['status'] in {'draft', 'returned'},
                               'assist': principal.role in BUILD}}
    assert_principal_current(principal)
    return result


def _sync_relations(conn, entity, document):
    status = 'confirmed' if entity['status'] == 'published' else 'rejected' if entity['status'] == 'archived' else 'candidate'
    existing = conn.execute('SELECT * FROM relations WHERE source=? AND JSON_EXTRACT(payload,\'$.skill_managed\')=true', (entity['id'],)).fetchall()
    for row in existing:
        conn.execute("UPDATE relations SET status='rejected' WHERE id=?", (row['id'],))
    predicates = {'data': ('uses_data', '使用数据口径'), 'rule': ('based_on', '依据规则'), 'knowledge': ('references', '引用知识')}
    for dep in document['manifest']['dependencies']:
        predicate, label = predicates[dep['kind']]
        ident = 'rel-skill-' + hashlib.sha256((entity['id'] + '\0' + dep['entity_id']).encode()).hexdigest()[:32]
        payload = {'skill_managed': True, 'skill_dependency': True, 'skill_id': entity['id'],
                   'package_revision': document['current_revision'], 'target_version': dep['version']}
        conn.execute('INSERT INTO relations(id,source,target,type,label,status,payload) VALUES(?,?,?,?,?,?,?) ON DUPLICATE KEY UPDATE type=VALUES(type),label=VALUES(label),status=VALUES(status),payload=VALUES(payload)',
                     (ident, entity['id'], dep['entity_id'], predicate, label, status, _json(payload)))


def _create_document(conn, title, summary, files, manifest, legacy_id=None):
    principal = _role(BUILD)
    files, package = _coherent_files(files, manifest)
    package_name = package['frontmatter']['name']
    ident, stamp = legacy_id or _id('SKILL'), now()
    if legacy_id:
        entity = entity_json(conn.execute('SELECT * FROM entities WHERE id=? FOR UPDATE', (legacy_id,)).fetchone())
        if not entity or entity['kind'] != 'skill' or is_managed_skill(conn, entity):
            _fail('skill_legacy_unavailable', '请选择本机构尚未建立技能包的历史技能资产。', 409)
        _snapshot_entity(conn, entity, '历史技能说明转为受治理技能草稿')
        payload = {**entity['payload'], 'managed_skill': True, 'package_name': package_name, 'package_revision': 1, 'package_digest': package['digest']}
        conn.execute("UPDATE entities SET title=?,summary=?,status='draft',payload=?,version=version+1,updated_at=? WHERE id=?",
                     (title, summary, _json(payload), stamp, ident))
    else:
        payload = {'managed_skill': True, 'package_name': package_name, 'package_revision': 1, 'package_digest': package['digest']}
        conn.execute('INSERT INTO entities(id,kind,title,summary,domain,org_id,status,source_kind,payload,created_at,updated_at,version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                     (ident, 'skill', title, summary, '技能开发', current_org_id(), 'draft', 'user', _json(payload), stamp, stamp, 1))
    conn.execute('INSERT INTO skill_documents(entity_id,current_revision,files,manifest,digest,last_editor_id,submitted_by,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                 (ident, 1, _json(files), _json(manifest), package['digest'], principal.user_id, None, stamp))
    entity, document = _document(conn, ident)
    _history_event(conn, entity, document, '创建技能草稿')
    log(conn, ident, '创建技能草稿', '登记Agent Skills包及本机构治理清单')
    return entity, document


@router.get('')
def list_skills(q: str = Query('', max_length=200), status: str = Query('', max_length=30),
                page: int = Query(1, ge=1, le=10000), page_size: int = Query(30, ge=1, le=100)):
    principal = _role()
    conditions, parameters = ["e.kind='skill'"], []
    if q:
        conditions.append('(e.id LIKE ? OR e.title LIKE ? OR e.summary LIKE ?)')
        parameters.extend(['%' + q + '%'] * 3)
    if status:
        if status not in {'draft', 'review', 'published', 'returned', 'archived', 'approved'}:
            _fail('skill_status_invalid', '技能状态过滤条件无效。')
        conditions.append('e.status=?'); parameters.append(status)
    with closing(_connect()) as conn:
        where = ' WHERE ' + ' AND '.join(conditions)
        total = conn.execute('SELECT COUNT(*) FROM entities e' + where, parameters).fetchone()[0]
        rows = conn.execute('SELECT e.id,e.title,e.summary,e.status,e.version,e.payload,e.updated_at,d.current_revision,d.digest,d.entity_id '
                            'FROM entities e LEFT JOIN skill_documents d ON d.entity_id=e.id' + where +
                            ' ORDER BY e.updated_at DESC,e.id LIMIT ? OFFSET ?', [*parameters, page_size, (page - 1) * page_size]).fetchall()
        items = []
        for row in rows:
            payload = _decode(row['payload'] or '{}')
            managed = bool(row['entity_id'] and payload.get('managed_skill'))
            check = _last_check(conn, {'entity_id': row['id'], 'current_revision': row['current_revision'], 'digest': row['digest']}) if managed else None
            items.append({**{key: row[key] for key in ('id', 'title', 'summary', 'status', 'version', 'updated_at')},
                          'package_name': payload.get('package_name', ''), 'package_revision': row['current_revision'],
                          'digest': row['digest'], 'managed': managed, 'validation_status': check['outcome'] if check else 'not_tested', 'last_test': check})
    assert_principal_current(principal)
    return {'items': items, 'total': total, 'page': page, 'page_size': page_size}


@router.get('/options')
def options(q: str = Query('', max_length=200)):
    principal = _role()
    from . import sources
    from .mcp_contracts import TOOL_DESCRIPTIONS
    with closing(_connect()) as conn:
        assets = [dict(row) for row in conn.execute("SELECT id,title,kind,version FROM entities WHERE kind IN ('data','rule','knowledge') "
                  "AND status='published' AND COALESCE(JSON_EXTRACT(payload,'$.active_asset'),1)!=0 AND (id LIKE ? OR title LIKE ?) ORDER BY title,id LIMIT 300", ('%' + q + '%', '%' + q + '%'))]
        mappings = []
        for row in conn.execute("SELECT id,source_id,title,version FROM source_mappings WHERE status='published' AND (id LIKE ? OR title LIKE ?) ORDER BY title,id LIMIT 300", ('%' + q + '%', '%' + q + '%')):
            source = sources._source(conn, row['source_id'])
            if source['enabled'] and sources._permissions(conn, source, principal)['query']:
                mappings.append(dict(row))
    try:
        from .skill_execution import runner_status
        runner = runner_status()
    except ImportError:
        runner = {'available': False, 'reason': '隔离测试服务尚未配置。'}
    try:
        from .skill_harness import harness_status
        harness = harness_status()
    except ImportError:
        harness = {'available': False, 'configured': False, 'version': '', 'reason': 'Harness服务尚未配置。'}
    assert_principal_current(principal)
    return {'assets': assets, 'mappings': mappings, 'tools': [{'name': name, 'title': value['title']} for name, value in TOOL_DESCRIPTIONS.items()],
            'limits': {'max_files': 64, 'max_file_bytes': 262144, 'max_package_bytes': 2097152, 'max_test_cases': 10, 'test_timeout_seconds': 30},
            'runner': runner, 'harness': harness}


@router.post('')
async def create_skill(request: Request):
    _role(BUILD)
    body = await _body(request, CreateBody)
    def create():
        manifest = Manifest().model_dump(mode='python')
        files = {'SKILL.md': '---\nname: ' + json.dumps(body.package_name, ensure_ascii=False) + '\ndescription: ' +
                 json.dumps((body.summary or body.title)[:1024], ensure_ascii=False) + '\n---\n\n# ' + body.title + '\n\n请补充技能说明、适用条件与操作步骤。\n'}
        with closing(_connect()) as conn, conn:
            entity, document = _create_document(conn, body.title, body.summary, files, manifest, body.legacy_entity_id)
            result = _detail(conn, entity, document)
        _audit('create', entity['id'], revision=document['current_revision'], digest=document['digest'])
        return result
    return await asyncio.to_thread(create)


async def _import_payload(request):
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > MAX_BODY_BYTES:
            _fail('skill_request_limit', '导入请求超过3MiB。', 413)
    content_type = request.headers.get('content-type', '').split(';', 1)[0].strip().lower()
    title = request.query_params.get('title', '')
    if content_type in {'application/zip', 'application/octet-stream'}:
        return bytes(raw), title
    if content_type != 'multipart/form-data':
        _fail('skill_import_type', '请选择ZIP技能包。', 415)
    # Bounded raw multipart is parsed entirely in memory. No UploadFile spool,
    # client filename, archive path or temporary extraction directory is used.
    from python_multipart.multipart import MultipartParser, parse_options_header
    _, options = parse_options_header(request.headers['content-type'])
    boundary = options.get(b'boundary')
    if not boundary or len(boundary) > 200:
        _fail('skill_import_type', '上传格式无效。')
    parts, current = [], {}
    def begin():
        if len(parts) >= 2: raise ValueError()
        current.clear(); current.update(headers={}, field=bytearray(), value=bytearray(), data=bytearray())
    def field(data, start, end): current['field'].extend(data[start:end])
    def value(data, start, end): current['value'].extend(data[start:end])
    def header_end():
        if len(current['field']) + len(current['value']) > 4096: raise ValueError()
        current['headers'][bytes(current['field']).lower()] = bytes(current['value'])
        current['field'].clear(); current['value'].clear()
    def data_chunk(data, start, end): current['data'].extend(data[start:end])
    def end():
        _, disposition = parse_options_header(current['headers'].get(b'content-disposition', b''))
        parts.append((disposition.get(b'name'), bytes(current['data'])))
    try:
        parser = MultipartParser(boundary, {'on_part_begin': begin, 'on_header_field': field, 'on_header_value': value,
                    'on_header_end': header_end, 'on_part_data': data_chunk, 'on_part_end': end}, max_size=MAX_BODY_BYTES)
        parser.write(bytes(raw)); parser.finalize()
        if len({name for name, _ in parts}) != len(parts) or any(name not in {b'file', b'title'} for name, _ in parts): raise ValueError()
        values = dict(parts)
        if b'file' not in values: raise ValueError()
        return values[b'file'], values.get(b'title', title.encode()).decode('utf-8')
    except Exception:
        _fail('skill_import_type', '上传格式无效，请只提交一个ZIP文件及可选标题。')


@router.post('/import')
async def import_skill(request: Request):
    _role(BUILD)
    raw, title = await _import_payload(request)
    if len(raw) > 2 * 1024 * 1024 or len(title) > 200:
        _fail('skill_import_limit', 'ZIP最多2MiB，标题最多200字符。', 413)
    def perform():
        from .skill_package import read_zip, validate_files, SkillPackageError
        try:
            files = read_zip(raw)
            parsed = validate_files(files)
        except SkillPackageError as exc:
            _fail('skill_import_invalid', exc.message)
        manifest, warnings = Manifest().model_dump(mode='python'), list(parsed['warnings'])
        pointer = parsed['frontmatter'].get('metadata', {}).get('ontology-one.manifest')
        if pointer and pointer in files:
            try:
                value = json.loads(files[pointer])
                portable = {key: value[key] for key in portable_manifest(manifest) if key in value}
                manifest = Manifest.model_validate(portable, strict=True).model_dump(mode='python')
            except Exception:
                warnings.append({'code': 'MANIFEST_REVIEW_REQUIRED', 'path': MANIFEST_PATH, 'message': '原包清单不符合平台契约，已建立空治理清单供人工补齐。'})
        warnings.append({'code': 'LOCAL_AUTHORITY_NOT_IMPORTED', 'path': MANIFEST_PATH, 'message': '导入不会继承机构关联、工具授权、审核状态或测试证明，请在本机构重新选择并核对。'})
        with closing(_connect()) as conn, conn:
            entity, document = _create_document(conn, title.strip() or parsed['frontmatter']['name'], parsed['frontmatter']['description'], files, manifest)
            result = _detail(conn, entity, document)
        result['import_warnings'] = warnings
        _audit('import', entity['id'], revision=1, digest=document['digest'], files=len(files))
        return result
    return await asyncio.to_thread(perform)


@router.get('/{ident}')
def skill_detail(ident: str):
    _role()
    with closing(_connect()) as conn:
        return _detail(conn, *_document(conn, ident))


@router.put('/{ident}')
async def save_skill(ident: str, request: Request):
    _role(BUILD)
    body = await _body(request, SaveBody)
    return await asyncio.to_thread(_save_skill, ident, body)


def _save_skill(ident, body):
    principal = _role(BUILD)
    manifest = body.manifest.model_dump(mode='python')
    files, package = _coherent_files(body.files, manifest)
    with closing(_connect()) as conn, conn:
        entity, document = _document(conn, ident, lock=True)
        _expected(entity, body.expected_version)
        if entity['status'] not in {'draft', 'returned'}:
            _fail('skill_not_editable', '请先修订为草稿，再编辑技能。', 409)
        _dependency_state(conn, manifest)
        _snapshot_entity(conn, entity, body.reason)
        changed = document['digest'] != package['digest'] or document['manifest'] != manifest
        revision = document['current_revision'] + int(changed)
        payload = {**entity['payload'], 'managed_skill': True, 'package_name': package['frontmatter']['name'],
                   'package_revision': revision, 'package_digest': package['digest']}
        conn.execute("UPDATE entities SET title=?,summary=?,payload=?,status='draft',version=version+1,updated_at=? WHERE id=?",
                     (body.title, body.summary, _json(payload), now(), ident))
        conn.execute('UPDATE skill_documents SET current_revision=?,files=?,manifest=?,digest=?,last_editor_id=?,submitted_by=NULL,updated_at=? WHERE entity_id=?',
                     (revision, _json(files), _json(manifest), package['digest'], principal.user_id, now(), ident))
        entity, document = _document(conn, ident)
        _sync_relations(conn, entity, document)
        _history_event(conn, entity, document, body.reason)
        log(conn, ident, '保存技能草稿', '保存技能包及依赖版本，等待校验和审核')
        result = _detail(conn, entity, document)
    _audit('save', ident, revision=revision, digest=document['digest'])
    return result


def _validate_current(conn, entity, document, *, proof=False):
    validation = _validation(document['files'], document['manifest'])
    if not validation['valid']:
        _fail('skill_validation_failed', '技能尚未通过完整校验，请修正错误并重新测试。', 409)
    checks, metadata = _dependency_state(conn, document['manifest'])
    if proof:
        last = _last_check(conn, document)
        mode = 'isolated_python' if document['manifest']['entrypoint'] else 'contract'
        if (not last or last['outcome'] != 'passed' or last['report'].get('mode') != mode or
                last['report'].get('proof_fingerprint') != _proof_fingerprint(document, checks)):
            _fail('skill_test_required', '当前技能版本尚无匹配的通过测试，请先执行测试。', 409)
    return validation, checks, metadata


@router.post('/{ident}/validate')
async def validate_skill(ident: str, request: Request):
    _role()
    body = await _body(request, VersionBody)
    def perform():
        with closing(_connect()) as conn:
            entity, document = _document(conn, ident)
            _expected(entity, body.expected_version)
            validation = _validation(document['files'], document['manifest'])
            checks, _ = _dependency_state(conn, document['manifest'], strict=False)
            validation['valid'] = validation['valid'] and all(item['current'] for item in checks)
        _audit('validate', ident, 'success' if validation['valid'] else 'failed', revision=document['current_revision'], digest=document['digest'])
        return {'validation': validation, 'dependency_checks': checks, 'digest': document['digest'], 'package_revision': document['current_revision']}
    return await asyncio.to_thread(perform)


def _safe_test_result(raw, manifest, mode):
    outcome = raw.get('outcome') if raw.get('outcome') in {'passed', 'failed', 'unavailable'} else 'failed'
    report = raw.get('report') if isinstance(raw.get('report'), dict) else {}
    cases = report.get('cases') if isinstance(report.get('cases'), list) else []
    safe_cases = []
    codes = {'PASSED', 'ASSERTION_FAILED', 'STDOUT_NOT_JSON', 'SCRIPT_ERROR', 'TIMEOUT', 'CANCELLED',
             'OUTPUT_LIMIT', 'RESOURCE_LIMIT', 'RUNNER_BUSY', 'RUNNER_UNAVAILABLE', 'INVALID_TEST_REQUEST',
             'NOT_RUN', 'CONTRACT_VALID'}
    for index, case in enumerate(manifest['test_cases']):
        observed = cases[index] if index < len(cases) and isinstance(cases[index], dict) else {}
        state = observed.get('status') if observed.get('status') in {'passed', 'failed', 'skipped'} else 'failed'
        code = str(observed.get('code', '')).upper()
        duration = observed.get('duration_ms', 0)
        safe_cases.append({'name': case['name'], 'index': index, 'status': state,
                           'code': code if code in codes else 'SCRIPT_ERROR',
                           'duration_ms': round(min(60000, max(0, duration)), 3) if type(duration) in (int, float) and math.isfinite(duration) else 0})
    if outcome == 'passed' and (not safe_cases or any(case['status'] != 'passed' for case in safe_cases)):
        outcome = 'failed'
    duration = report.get('duration_ms', 0)
    return {'outcome': outcome, 'mode': mode, 'report': {'mode': mode, 'cases': safe_cases,
        'passed': sum(case['status'] == 'passed' for case in safe_cases), 'failed': sum(case['status'] == 'failed' for case in safe_cases),
        'duration_ms': round(min(60000, max(0, duration)), 3) if type(duration) in (int, float) and math.isfinite(duration) else 0,
        'retention': 'metadata_only'}}


@router.post('/{ident}/test')
async def test_skill(ident: str, request: Request):
    principal = _role(BUILD)
    body = await _body(request, VersionBody)
    def prepare():
        with closing(_connect()) as conn:
            entity, document = _document(conn, ident)
            _expected(entity, body.expected_version)
            if entity['status'] not in {'draft', 'returned'}:
                _fail('skill_not_editable', '请在草稿或退回状态执行版本测试。', 409)
            _, checks, _ = _validate_current(conn, entity, document)
            return document, _proof_fingerprint(document, checks)
    document, initial_proof = await asyncio.to_thread(prepare)
    manifest = document['manifest']
    mode = 'isolated_python' if manifest['entrypoint'] else 'contract'
    cancelled = threading.Event()
    if mode == 'contract':
        raw = {'outcome': 'passed', 'report': {'cases': [{'status': 'passed', 'code': 'contract_valid', 'duration_ms': 0} for _ in manifest['test_cases']], 'duration_ms': 0}}
    else:
        from .skill_execution import execute_tests
        async def disconnect():
            while True:
                if await request.is_disconnected():
                    cancelled.set(); return
                await asyncio.sleep(.2)
        work = asyncio.create_task(asyncio.to_thread(execute_tests, document['files'], manifest['entrypoint'], manifest['test_cases'], timeout_seconds=30, cancelled=cancelled.is_set))
        watcher = asyncio.create_task(disconnect())
        try:
            raw = await asyncio.shield(work)
            if cancelled.is_set():
                _fail('skill_test_cancelled', '测试请求已取消，结果未保存。', 409)
        except asyncio.CancelledError:
            cancelled.set()
            import anyio
            with anyio.CancelScope(shield=True):
                await work
            raise
        finally:
            cancelled.set()
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
    result = _safe_test_result(raw, manifest, mode)
    def store_result():
        assert_principal_current(principal)
        with closing(_connect()) as conn, conn:
            entity, fresh = _document(conn, ident, lock=True)
            _expected(entity, body.expected_version)
            if fresh['digest'] != document['digest'] or fresh['current_revision'] != document['current_revision']:
                _fail('skill_test_stale', '测试期间技能内容发生变化，结果未保存。', 409)
            checks, _ = _dependency_state(conn, fresh['manifest'])
            if initial_proof != _proof_fingerprint(fresh, checks):
                _fail('skill_test_stale', '测试期间依赖映射或结构版本已变化，结果未保存。', 409)
            result['report']['proof_fingerprint'] = initial_proof
            result['report']['dependency_checks'] = checks
            result['report']['source_rows_queried'] = False
            check_id = _id('SKCHECK')
            conn.execute('INSERT INTO skill_checks(id,entity_id,revision,digest,outcome,report,actor_id,created_at) VALUES(?,?,?,?,?,?,?,?)',
                         (check_id, ident, fresh['current_revision'], fresh['digest'], result['outcome'], _json(result['report']), principal.user_id, now()))
        _audit('test', ident, result['outcome'], revision=fresh['current_revision'], digest=fresh['digest'], mode=mode,
               passed=result['report']['passed'], failed=result['report']['failed'])
        return {**result, 'check_id': check_id, 'digest': fresh['digest'], 'package_revision': fresh['current_revision']}
    return await asyncio.to_thread(store_result)


@router.post('/{ident}/action')
async def skill_action(ident: str, request: Request):
    body = await _body(request, ActionBody)
    return await asyncio.to_thread(_action, ident, body)


def _action(ident, body):
    principal = _role(REVIEW if body.action in {'publish', 'return'} else BUILD)
    allowed = {'submit': {'draft', 'returned'}, 'publish': {'review'}, 'return': {'review'},
               'revise': {'published', 'review', 'returned', 'archived'}, 'archive': {'draft', 'returned', 'review', 'published'}}
    statuses = {'submit': 'review', 'publish': 'published', 'return': 'returned', 'revise': 'draft', 'archive': 'archived'}
    with closing(_connect()) as conn, conn:
        entity, document = _document(conn, ident, lock=True)
        _expected(entity, body.expected_version)
        if entity['status'] not in allowed[body.action]:
            _fail('skill_transition_invalid', '当前技能状态不允许此操作。', 409)
        if body.action in {'publish', 'return'} and principal.user_id in {document['last_editor_id'], document['submitted_by']}:
            _fail('skill_self_review', '编辑人和提交人不能审核自己的技能，管理员同样适用。', 403)
        if body.action in {'submit', 'publish'}:
            _validate_current(conn, entity, document, proof=True)
        _snapshot_entity(conn, entity, body.reason)
        submitted = principal.user_id if body.action == 'submit' else None if body.action == 'revise' else document['submitted_by']
        conn.execute('UPDATE entities SET status=?,version=version+1,updated_at=? WHERE id=?', (statuses[body.action], now(), ident))
        conn.execute('UPDATE skill_documents SET submitted_by=?,updated_at=? WHERE entity_id=?', (submitted, now(), ident))
        entity, document = _document(conn, ident)
        _sync_relations(conn, entity, document)
        _history_event(conn, entity, document, body.reason)
        log(conn, ident, {'submit': '提交技能审核', 'publish': '发布技能版本', 'return': '退回技能补充', 'revise': '修订技能草稿', 'archive': '归档技能'}[body.action], body.reason)
        result = _detail(conn, entity, document)
    _audit(body.action, ident, revision=document['current_revision'], digest=document['digest'], entity_version=entity['version'])
    return result


@router.get('/{ident}/versions')
def skill_versions(ident: str):
    _role()
    with closing(_connect()) as conn:
        _document(conn, ident)
        return {'items': [dict(row) for row in conn.execute('SELECT revision,status,digest,actor_id,created_at,reason FROM skill_revisions WHERE entity_id=? ORDER BY created_at DESC,id DESC LIMIT 200', (ident,))]}


def _revision(conn, ident, revision):
    row = conn.execute('SELECT * FROM skill_revisions WHERE entity_id=? AND revision=? ORDER BY created_at DESC,id DESC LIMIT 1', (ident, revision)).fetchone()
    if not row:
        _fail('skill_version_not_found', '技能历史版本不存在。', 404)
    return {**dict(row), 'files': _decode(row['files']), 'manifest': _decode(row['manifest']), 'read_only': True}


@router.get('/{ident}/versions/{revision}')
def skill_version(ident: str, revision: int):
    principal = _role()
    with closing(_connect()) as conn:
        _document(conn, ident)
        result = _revision(conn, ident, revision)
    assert_principal_current(principal)
    return result


@router.get('/{ident}/export')
def export_skill(ident: str, version: int = Query(0, ge=0)):
    principal = _role()
    from .skill_package import write_zip, SkillPackageError
    with closing(_connect()) as conn:
        entity, document = _document(conn, ident)
        files = _revision(conn, ident, version)['files'] if version else document['files']
    try:
        raw = write_zip(files)
    except SkillPackageError:
        _fail('skill_export_invalid', '技能包尚不符合开放格式，请修正校验错误后导出。', 409)
    assert_principal_current(principal)
    _audit('export', ident, revision=version or document['current_revision'], bytes=len(raw))
    # Stable server-generated filename avoids trusting a package name as a header.
    return Response(raw, media_type='application/zip', headers={'Content-Disposition': f'attachment; filename="skill-{hashlib.sha256(ident.encode()).hexdigest()[:16]}.zip"', 'Cache-Control': 'no-store'})


@router.get('/{ident}/load')
def load_skill(ident: str):
    principal = _role()
    with closing(_connect()) as conn:
        # Current publication and its package are one locked read, so a concurrent
        # revise/archive cannot race between the status and document queries.
        entity, document = _document(conn, ident, shared=True)
        if entity['status'] != 'published':
            _fail('skill_not_published', '仅当前已发布且依赖有效的技能可加载。', 409)
        _validate_current(conn, entity, document, proof=True)
        result = {'skill_id': ident, 'version': entity['version'], 'package_revision': document['current_revision'],
                  'digest': document['digest'], 'files': document['files'], 'manifest': document['manifest'],
                  'tool_policy': {'tools': document['manifest']['tool_dependencies'], 'tenant_id': principal.tenant_id,
                                  'credentials_included': False, 'execution_authorized': False}}
    assert_principal_current(principal)
    _audit('load', ident, revision=document['current_revision'], digest=document['digest'])
    return result


@router.post('/{ident}/assist')
async def assist_skill(ident: str, request: Request):
    principal = _role(BUILD)
    body = await _body(request, AssistBody)
    def prepare():
        with closing(_connect()) as conn:
            entity, document = _document(conn, ident)
            _expected(entity, body.expected_version)
            _, metadata = _dependency_state(conn, document['manifest'])
            return entity, document, metadata
    entity, document, metadata = await asyncio.to_thread(prepare)
    from .skill_harness import propose_skill
    request_id = principal.request_id or uuid.uuid4().hex
    proposal = await _await_connected(request, propose_skill(principal, body.instruction, document['files'], document['manifest'], metadata, request_id))
    try:
        proposed_manifest = Manifest.model_validate(proposal['manifest'], strict=True).model_dump(mode='python')
        # A model cannot add/change local authority bindings, even as a proposal.
        proposed_manifest['dependencies'] = document['manifest']['dependencies']
        proposed_manifest['tool_dependencies'] = document['manifest']['tool_dependencies']
        files, _ = _coherent_files(proposal['files'], proposed_manifest)
    except (KeyError, ValueError, TypeError):
        _fail('skill_proposal_invalid', 'Harness候选内容不符合技能格式，请调整要求后重试。', 502)
    def recheck():
        assert_principal_current(principal)
        with closing(_connect()) as conn:
            fresh_entity, fresh_document = _document(conn, ident)
            _expected(fresh_entity, body.expected_version)
            if fresh_document['digest'] != document['digest']:
                _fail('skill_proposal_stale', '生成期间技能已变化，请重新加载并生成。', 409)
            _dependency_state(conn, fresh_document['manifest'])
        _audit('assist', ident, revision=document['current_revision'], digest=document['digest'])
    await asyncio.to_thread(recheck)
    return {'request_id': request_id, 'base_version': entity['version'], 'base_digest': document['digest'],
            'summary': str(proposal.get('summary', ''))[:2000], 'files': files, 'manifest': proposed_manifest,
            'validation': _validation(files, proposed_manifest), 'retention': 'response_only'}


async def _await_connected(request, operation):
    """Disconnect actually cancels and awaits the killable Harness adapter."""
    async def disconnect():
        while True:
            if await request.is_disconnected(): return
            await asyncio.sleep(.2)
    work = asyncio.create_task(operation)
    watcher = asyncio.create_task(disconnect())
    try:
        await asyncio.wait({work, watcher}, return_when=asyncio.FIRST_COMPLETED)
        if watcher.done():
            work.cancel()
            import anyio
            with anyio.CancelScope(shield=True):
                await asyncio.gather(work, return_exceptions=True)
            _fail('skill_assist_cancelled', '生成请求已取消，候选内容未保存。', 409)
        return await work
    except asyncio.CancelledError:
        work.cancel()
        import anyio
        with anyio.CancelScope(shield=True):
            await asyncio.gather(work, return_exceptions=True)
        raise
    finally:
        watcher.cancel()
        import anyio
        with anyio.CancelScope(shield=True):
            await asyncio.gather(watcher, return_exceptions=True)


def demote_restored_skills(conn):
    """Backups restore governed content, never a live publication or test proof."""
    conn.execute('DELETE FROM skill_checks')
    for row in conn.execute('SELECT entity_id FROM skill_documents').fetchall():
        entity, document = _document(conn, row['entity_id'], lock=True)
        _snapshot_entity(conn, entity, '恢复后重新校验技能发布权限')
        conn.execute("UPDATE entities SET status='draft',version=version+1,updated_at=? WHERE id=?", (now(), entity['id']))
        conn.execute('UPDATE skill_documents SET submitted_by=NULL,last_editor_id=?,updated_at=? WHERE entity_id=?', (require_context().user_id, now(), entity['id']))
        entity, document = _document(conn, entity['id'])
        _sync_relations(conn, entity, document)
        _history_event(conn, entity, document, '工作区恢复后重新测试和审核')
