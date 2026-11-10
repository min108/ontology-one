"""Tenant-scoped external source metadata and reviewed, response-only queries.

External business rows never enter the tenant database. Only schema and mapping
metadata are versioned here; query audits contain identifiers and measurements.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import math
import re
import time
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StringConstraints, model_validator

from .db import connect, now
from .security.context import require_context
from .security.store import append_audit, assert_principal_current, control_connection

router = APIRouter(prefix='/sources', tags=['external-sources'])
RETENTION = {'result': 'response_only', 'cache': 'disabled', 'audit': 'metadata_only'}
BUILD = {'admin', 'modeler'}
REVIEW = {'admin', 'reviewer'}
ALL_ROLES = BUILD | REVIEW

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS source_connections(
      id VARCHAR(64) PRIMARY KEY,title VARCHAR(200) NOT NULL,kind VARCHAR(20) NOT NULL,
      config JSON NOT NULL,credential_ref VARCHAR(120) NOT NULL,enabled TINYINT NOT NULL DEFAULT 1,
      version INT NOT NULL,config_version INT NOT NULL,created_by VARCHAR(191) NOT NULL,created_at VARCHAR(40) NOT NULL,
      updated_at VARCHAR(40) NOT NULL,last_discovered_at VARCHAR(40)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    """CREATE TABLE IF NOT EXISTS source_grants(
      source_id VARCHAR(64) NOT NULL,user_id VARCHAR(191) NOT NULL,can_discover TINYINT NOT NULL,
      can_query TINYINT NOT NULL,version INT NOT NULL,updated_by VARCHAR(191) NOT NULL,
      updated_at VARCHAR(40) NOT NULL,PRIMARY KEY(source_id,user_id),
      CONSTRAINT fk_source_grant FOREIGN KEY(source_id) REFERENCES source_connections(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    """CREATE TABLE IF NOT EXISTS source_resources(
      id VARCHAR(64) PRIMARY KEY,source_id VARCHAR(64) NOT NULL,resource_key VARCHAR(1000) NOT NULL,
      resource_key_hash CHAR(64) NOT NULL,
      schema_hash CHAR(64) NOT NULL,schema_version INT NOT NULL,available TINYINT NOT NULL,
      metadata JSON NOT NULL,observed_at VARCHAR(40) NOT NULL,
      UNIQUE KEY source_resource_key(source_id,resource_key_hash),
      CONSTRAINT fk_source_resource FOREIGN KEY(source_id) REFERENCES source_connections(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    """CREATE TABLE IF NOT EXISTS source_schema_versions(
      id VARCHAR(64) PRIMARY KEY,resource_id VARCHAR(64) NOT NULL,version INT NOT NULL,
      schema_hash CHAR(64) NOT NULL,available TINYINT NOT NULL,metadata JSON NOT NULL,
      observed_by VARCHAR(191) NOT NULL,observed_at VARCHAR(40) NOT NULL,
      UNIQUE KEY source_schema_revision(resource_id,version),
      CONSTRAINT fk_source_schema_resource FOREIGN KEY(resource_id) REFERENCES source_resources(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    """CREATE TABLE IF NOT EXISTS source_mappings(
      id VARCHAR(64) PRIMARY KEY,source_id VARCHAR(64) NOT NULL,resource_id VARCHAR(64) NOT NULL,
      title VARCHAR(200) NOT NULL,definition JSON NOT NULL,status VARCHAR(20) NOT NULL,
      version INT NOT NULL,schema_hash CHAR(64) NOT NULL,source_version INT NOT NULL,
      created_by VARCHAR(191) NOT NULL,submitted_by VARCHAR(191),reviewed_by VARCHAR(191),
      review_reason TEXT,invalid_reason VARCHAR(100),created_at VARCHAR(40) NOT NULL,updated_at VARCHAR(40) NOT NULL,
      KEY source_mapping_source(source_id,status),KEY source_mapping_resource(resource_id),
      CONSTRAINT fk_source_mapping FOREIGN KEY(source_id) REFERENCES source_connections(id),
      CONSTRAINT fk_source_mapping_resource FOREIGN KEY(resource_id) REFERENCES source_resources(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    """CREATE TABLE IF NOT EXISTS source_mapping_versions(
      id VARCHAR(64) PRIMARY KEY,mapping_id VARCHAR(64) NOT NULL,version INT NOT NULL,
      action VARCHAR(40) NOT NULL,snapshot JSON NOT NULL,actor_id VARCHAR(191) NOT NULL,
      reason VARCHAR(2000) NOT NULL,created_at VARCHAR(40) NOT NULL,
      UNIQUE KEY source_mapping_revision(mapping_id,version),
      CONSTRAINT fk_source_mapping_revision FOREIGN KEY(mapping_id) REFERENCES source_mappings(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
)


def ensure_schema(conn):
    for statement in SCHEMA:
        conn.execute(statement)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, str_strip_whitespace=True)


Text = Annotated[str, Field(min_length=1, max_length=2000)]
Identifier = Annotated[str, StringConstraints(strip_whitespace=False), Field(min_length=1, max_length=191)]
NativeColumn = Annotated[str, StringConstraints(strip_whitespace=False), Field(min_length=1, max_length=1000)]
Version = Annotated[StrictInt, Field(ge=1)]


class SourceCreate(StrictModel):
    title: Annotated[str, Field(min_length=1, max_length=200)]
    kind: Literal['mysql', 'mcp']
    config: dict[str, Any]
    credential_ref: Annotated[str, Field(min_length=1, max_length=120, pattern=r'^[A-Za-z0-9_.:-]+$')]


class SourcePatch(StrictModel):
    title: Annotated[str | None, Field(min_length=1, max_length=200)] = None
    enabled: StrictBool | None = None
    config: dict[str, Any] | None = None
    credential_ref: Annotated[str | None, Field(min_length=1, max_length=120, pattern=r'^[A-Za-z0-9_.:-]+$')] = None
    expected_version: Version

    @model_validator(mode='after')
    def meaningful(self):
        changes = self.model_dump(exclude_unset=True, exclude={'expected_version'})
        if not changes or any(value is None for value in changes.values()):
            raise ValueError('更新须包含至少一个非空修改字段。')
        return self


class GrantBody(StrictModel):
    user_id: Identifier
    discover: StrictBool
    query: StrictBool


class MappingField(StrictModel):
    column: NativeColumn
    field: Annotated[str, Field(min_length=1, max_length=64, pattern=r'^[a-zA-Z_][a-zA-Z0-9_]*$')]
    asset_id: Identifier
    meaning: Text
    unit: Annotated[str, Field(min_length=1, max_length=200)]


class RelationKey(StrictModel):
    column: NativeColumn
    target_asset_id: Identifier


class MappingBody(StrictModel):
    resource_id: Identifier
    title: Annotated[str, Field(min_length=1, max_length=200)]
    business_meaning: Text
    grain: Text
    object_key: Annotated[list[NativeColumn], Field(min_length=1, max_length=200)]
    relation_keys: Annotated[list[RelationKey], Field(max_length=200)] = Field(default_factory=list)
    fields: Annotated[list[MappingField], Field(min_length=1, max_length=200)]

    @model_validator(mode='after')
    def unique_fields(self):
        columns = [field.column for field in self.fields]
        names = [field.field for field in self.fields]
        if len(set(columns)) != len(columns) or len(set(names)) != len(names):
            raise ValueError('原始字段和语义字段名不可重复。')
        if len(set(self.object_key)) != len(self.object_key) or not set(self.object_key) <= set(columns):
            raise ValueError('对象键须唯一，且每个对象键均须有显式字段映射。')
        pairs = [(key.column, key.target_asset_id) for key in self.relation_keys]
        if len(set(pairs)) != len(pairs):
            raise ValueError('关系键不可重复。')
        return self


class MappingUpdate(MappingBody):
    expected_version: Version


class MappingAction(StrictModel):
    action: Literal['submit', 'publish', 'reject', 'revise']
    expected_version: Version
    reason: Annotated[str, Field(max_length=2000)] = ''

    @model_validator(mode='after')
    def review_reason_required(self):
        if self.action in {'publish', 'reject'} and not self.reason:
            raise ValueError('审核操作必须填写理由。')
        return self


class QueryFilter(StrictModel):
    field: Annotated[str, Field(min_length=1, max_length=64)]
    op: Literal['eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'in', 'is_null']
    value: Any = None

    @model_validator(mode='after')
    def bounded_scalar(self):
        def scalar(value):
            return (value is None or type(value) in (bool, int) or
                    (type(value) is float and math.isfinite(value)) or
                    (isinstance(value, str) and len(value) <= 1000))
        if self.op == 'in':
            if not isinstance(self.value, list) or not 1 <= len(self.value) <= 50 or not all(scalar(v) for v in self.value):
                raise ValueError('in 条件须为1至50个标量值。')
        elif self.op == 'is_null':
            if self.value is not None:
                raise ValueError('is_null 不接受值。')
        elif 'value' not in self.model_fields_set or not scalar(self.value):
            raise ValueError('条件值须为有限标量，字符串最长1000字符。')
        return self


class QueryOrder(StrictModel):
    field: Annotated[str, Field(min_length=1, max_length=64)]
    direction: Literal['asc', 'desc'] = 'asc'


class QueryBody(StrictModel):
    fields: Annotated[list[str] | None, Field(min_length=1, max_length=200)] = None
    filters: Annotated[list[QueryFilter], Field(max_length=12)] = Field(default_factory=list)
    order_by: Annotated[list[QueryOrder], Field(max_length=12)] = Field(default_factory=list)
    limit: Annotated[StrictInt, Field(ge=1, le=200)] = 50


def _id(prefix):
    return prefix + '-' + uuid.uuid4().hex


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _decode(value):
    return json.loads(value) if isinstance(value, str) else value


def _fail(code, message, status=422):
    raise HTTPException(status, {'code': code, 'message': message})


def _role(roles):
    principal = assert_principal_current(require_context())
    if principal.role not in roles or principal.readonly or principal.must_change_password:
        _fail('source_forbidden', '当前账号无权执行此数据源操作。', 403)
    return principal


def _source(conn, source_id, *, lock=False):
    row = conn.execute('SELECT * FROM source_connections WHERE id=?' + (' FOR UPDATE' if lock else ''), (source_id,)).fetchone()
    if not row:
        _fail('source_not_found', '数据源不存在。', 404)
    value = dict(row)
    value['config'] = _decode(value['config'])
    value['tenant_id'] = require_context().tenant_id
    value['enabled'] = bool(value['enabled'])
    return value


def _permissions(conn, source, principal=None):
    principal = principal or require_context()
    if principal.role == 'admin':
        return {'manage': True, 'discover': True, 'query': True}
    grant = conn.execute('SELECT can_discover,can_query FROM source_grants WHERE source_id=? AND user_id=?', (source['id'], principal.user_id)).fetchone()
    return {'manage': False, 'discover': bool(grant and grant['can_discover']), 'query': bool(grant and grant['can_query'])}


def _authorize(conn, source_id, capability=None, *, lock=False, expected_version=None, enabled=True):
    principal = _role(ALL_ROLES)
    source = _source(conn, source_id, lock=lock)
    if expected_version is not None and source['version'] != expected_version:
        _fail('source_changed', '数据源配置已变更，请重新加载。', 409)
    if enabled and not source['enabled']:
        _fail('source_disabled', '数据源已停用。', 403)
    if capability and not _permissions(conn, source, principal)[capability]:
        _fail('source_forbidden', '未获此数据源的相应授权。', 403)
    return source


def _summary(conn, source):
    value = {key: source[key] for key in ('id', 'title', 'kind', 'enabled', 'version', 'created_at', 'updated_at', 'last_discovered_at')}
    value['enabled'] = bool(value['enabled'])
    value['permissions'] = _permissions(conn, source)
    if value['permissions']['manage']:
        value.update(config=source['config'], credential_ref=source['credential_ref'])
    return value


def _resource(row):
    value = _decode(row['metadata'])
    return {**value, 'id': row['id'], 'source_id': row['source_id'], 'schema_hash': row['schema_hash'],
            'schema_version': row['schema_version'], 'available': bool(row['available']), 'observed_at': row['observed_at']}


def _mapping(row):
    if not row:
        _fail('mapping_not_found', '映射不存在。', 404)
    value = dict(row)
    definition = _decode(value.pop('definition'))
    return {**value, **definition}


def _mapping_row(conn, source_id, mapping_id, *, lock=False):
    row = conn.execute('SELECT * FROM source_mappings WHERE id=? AND source_id=?' + (' FOR UPDATE' if lock else ''), (mapping_id, source_id)).fetchone()
    if not row:
        _fail('mapping_not_found', '映射不存在。', 404)
    return row


def _expected(row, version):
    if row['version'] != version:
        _fail('version_conflict', '记录已被其他操作更新，请重新加载。', 409)


def _source_context(source):
    """Capture the exact non-secret target, independent of future source edits."""
    config = json.loads(_json(source['config']))
    fingerprint = hashlib.sha256(_json({'kind': source['kind'], 'config': config}).encode('utf-8')).hexdigest()
    return {'id': source['id'], 'kind': source['kind'], 'config_version': source['config_version'],
            'config': config, 'config_fingerprint': fingerprint}


def _query_source_identity(source):
    if source is None:
        return {}
    context = _source_context(source)
    return {key: context[key] for key in ('config_version', 'config_fingerprint')}


def _public_snapshot(snapshot, role):
    value = dict(snapshot)
    if role != 'admin' and isinstance(value.get('source_context'), dict):
        value['source_context'] = {key: child for key, child in value['source_context'].items() if key != 'config'}
    return value


def _event(conn, mapping_id, action, reason=''):
    row = conn.execute('SELECT * FROM source_mappings WHERE id=?', (mapping_id,)).fetchone()
    snapshot = {**_mapping(row), 'source_context': _source_context(_source(conn, row['source_id']))}
    conn.execute('INSERT INTO source_mapping_versions(id,mapping_id,version,action,snapshot,actor_id,reason,created_at) VALUES(?,?,?,?,?,?,?,?)',
                 (_id('SMV'), mapping_id, row['version'], action, _json(snapshot), require_context().user_id, reason, now()))


def _invalidate(conn, source_id, reason, resource_ids=None):
    where, args = 'source_id=? AND status<>\'stale\'', [source_id]
    if resource_ids is not None:
        if not resource_ids:
            return
        where += ' AND resource_id IN (' + ','.join('?' for _ in resource_ids) + ')'
        args.extend(resource_ids)
    rows = conn.execute('SELECT id FROM source_mappings WHERE ' + where + ' FOR UPDATE', args).fetchall()
    for row in rows:
        conn.execute("UPDATE source_mappings SET status='stale',version=version+1,invalid_reason=?,updated_at=? WHERE id=?", (reason, now(), row['id']))
        _event(conn, row['id'], 'invalidate', reason)


def _audit(action, resource, outcome='success', **details):
    # Callers deliberately pass no user-provided filters, rows, config or errors.
    append_audit(require_context(), 'source_' + action, resource, outcome, details)


def _adapter_failure(exc):
    # Adapter errors are typed and sanitized at the external boundary.
    _fail(exc.code, exc.message, exc.status)


def _validate_config(kind, config):
    from .source_adapters import SourceFailure, validate_config
    try:
        return validate_config(kind, config)
    except SourceFailure as exc:
        _adapter_failure(exc)


def _remote_discover(source):
    from .source_adapters import SourceFailure, discover
    try:
        return discover(source)
    except SourceFailure as exc:
        _audit('discover', source['id'], 'failed', code=exc.code)
        _adapter_failure(exc)


def _schema_hash(resource):
    return hashlib.sha256(_json(resource).encode('utf-8')).hexdigest()


def _persist_discovery(conn, source, resources):
    existing = {row['resource_key']: dict(row) for row in conn.execute('SELECT * FROM source_resources WHERE source_id=? FOR UPDATE', (source['id'],))}
    observed, changes, changed_ids = now(), [], []
    for metadata in resources:
        key, digest = metadata['key'], _schema_hash(metadata)
        previous = existing.pop(key, None)
        if previous and previous['schema_hash'] == digest and previous['available']:
            conn.execute('UPDATE source_resources SET observed_at=? WHERE id=?', (observed, previous['id']))
            continue
        resource_id = previous['id'] if previous else _id('SRC')
        version = previous['schema_version'] + 1 if previous else 1
        if previous:
            conn.execute('UPDATE source_resources SET schema_hash=?,schema_version=?,available=1,metadata=?,observed_at=? WHERE id=?',
                         (digest, version, _json(metadata), observed, resource_id))
        else:
            conn.execute('INSERT INTO source_resources(id,source_id,resource_key,resource_key_hash,schema_hash,schema_version,available,metadata,observed_at) VALUES(?,?,?,?,?,?,?,?,?)',
                         (resource_id, source['id'], key, hashlib.sha256(key.encode('utf-8')).hexdigest(), digest, version, 1, _json(metadata), observed))
        conn.execute('INSERT INTO source_schema_versions(id,resource_id,version,schema_hash,available,metadata,observed_by,observed_at) VALUES(?,?,?,?,?,?,?,?)',
                     (_id('SSV'), resource_id, version, digest, 1, _json(metadata), require_context().user_id, observed))
        changes.append({'resource_id': resource_id, 'key': key, 'change': 'changed' if previous else 'added', 'schema_version': version})
        changed_ids.append(resource_id)
    for key, previous in existing.items():
        if not previous['available']:
            continue
        version = previous['schema_version'] + 1
        conn.execute('UPDATE source_resources SET available=0,schema_version=?,observed_at=? WHERE id=?', (version, observed, previous['id']))
        conn.execute('INSERT INTO source_schema_versions(id,resource_id,version,schema_hash,available,metadata,observed_by,observed_at) VALUES(?,?,?,?,?,?,?,?)',
                     (_id('SSV'), previous['id'], version, previous['schema_hash'], 0, previous['metadata'], require_context().user_id, observed))
        changes.append({'resource_id': previous['id'], 'key': key, 'change': 'removed', 'schema_version': version})
        changed_ids.append(previous['id'])
    _invalidate(conn, source['id'], 'schema_drift', changed_ids)
    conn.execute('UPDATE source_connections SET last_discovered_at=? WHERE id=?', (observed, source['id']))
    return changes


def _target(conn, asset_id, *, expected=None):
    row = conn.execute("SELECT id,kind,status,version,payload FROM entities WHERE id=? FOR SHARE", (asset_id,)).fetchone()
    if not row or row['kind'] != 'data' or row['status'] != 'published':
        _fail('target_unpublished', '映射目标须为本机构已发布的数据标准。', 409)
    payload = _decode(row['payload'] or '{}')
    if isinstance(payload, dict) and payload.get('active_asset', 1) in (0, False, '0'):
        _fail('target_inactive', '映射目标已失效，请重新选择已发布的数据标准。', 409)
    if expected is not None and row['version'] != expected:
        _fail('target_changed', '目标标准版本已变化，映射须重新修订审核。', 409)
    return row['version']


def _definition(conn, source_id, body):
    resource = conn.execute('SELECT * FROM source_resources WHERE id=? AND source_id=?', (body.resource_id, source_id)).fetchone()
    if not resource or not resource['available']:
        _fail('resource_unavailable', '请选择当前可用的远端资源。', 409)
    metadata = _decode(resource['metadata'])
    columns = {column['name'] for column in metadata['columns']}
    required = set(body.object_key) | {field.column for field in body.fields} | {key.column for key in body.relation_keys}
    if not required <= columns:
        _fail('column_unknown', '映射包含不在当前结构中的字段。')
    fields, relation_keys = [], []
    for field in body.fields:
        fields.append({**field.model_dump(), 'asset_version': _target(conn, field.asset_id)})
    for relation in body.relation_keys:
        relation_keys.append({**relation.model_dump(), 'asset_version': _target(conn, relation.target_asset_id)})
    definition = {'business_meaning': body.business_meaning, 'grain': body.grain, 'object_key': body.object_key,
                  'relation_keys': relation_keys, 'fields': fields}
    return definition, resource


def _validate_mapping_current(conn, source, mapping):
    if mapping['source_version'] != source['config_version']:
        _fail('source_changed', '数据源配置已变化，映射须重新审核。', 409)
    resource = conn.execute('SELECT * FROM source_resources WHERE id=? AND source_id=?', (mapping['resource_id'], source['id'])).fetchone()
    if not resource or not resource['available'] or resource['schema_hash'] != mapping['schema_hash']:
        _fail('schema_drift', '远端资源结构已变化，映射须重新审核。', 409)
    for field in mapping['fields']:
        _target(conn, field['asset_id'], expected=field['asset_version'])
    for key in mapping['relation_keys']:
        _target(conn, key['target_asset_id'], expected=key['asset_version'])
    return resource


@router.get('')
def list_sources():
    _role(ALL_ROLES)
    with closing(connect()) as conn:
        items = [_summary(conn, _source(conn, row['id'])) for row in conn.execute('SELECT id FROM source_connections ORDER BY created_at,id')]
        return {'items': items, 'contract_version': '1.0', 'retention': RETENTION}


@router.post('')
def create_source(body: SourceCreate):
    principal = _role({'admin'})
    config, ident, stamp = _validate_config(body.kind, body.config), _id('SOURCE'), now()
    with closing(connect()) as conn, conn:
        conn.execute('INSERT INTO source_connections(id,title,kind,config,credential_ref,enabled,version,config_version,created_by,created_at,updated_at,last_discovered_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                     (ident, body.title, body.kind, _json(config), body.credential_ref, 1, 1, 1, principal.user_id, stamp, stamp, None))
        result = _summary(conn, _source(conn, ident))
    _audit('create', ident, version=1)
    return result


# Static route precedes /{source_id}; lineage includes metadata only.
@router.get('/lineage/{asset_id}')
def lineage(asset_id: str):
    _role(ALL_ROLES)
    with closing(connect()) as conn:
        if not conn.execute('SELECT id FROM entities WHERE id=?', (asset_id,)).fetchone():
            _fail('asset_not_found', '资产不存在。', 404)
        items = []
        for row in conn.execute('SELECT * FROM source_mappings ORDER BY updated_at DESC,id'):
            value = _mapping(row)
            matches = [field for field in value['fields'] if field['asset_id'] == asset_id]
            relations = [key for key in value['relation_keys'] if key['target_asset_id'] == asset_id]
            if matches or relations:
                source = _source(conn, value['source_id'])
                permissions = _permissions(conn, source)
                if permissions['discover'] or permissions['query']:
                    resource = conn.execute('SELECT metadata FROM source_resources WHERE id=?', (value['resource_id'],)).fetchone()
                    items.append({**value, 'source_title': source['title'], 'resource_name': _decode(resource['metadata'])['name'] if resource else '', 'matched_fields': matches, 'matched_relation_keys': relations})
        return {'asset_id': asset_id, 'items': items, 'retention': RETENTION}


@router.get('/{source_id}')
def source_detail(source_id: str):
    _role(ALL_ROLES)
    with closing(connect()) as conn:
        source = _authorize(conn, source_id, enabled=False)
        result = {'source': _summary(conn, source), 'resources': [_resource(row) for row in conn.execute('SELECT * FROM source_resources WHERE source_id=? ORDER BY resource_key', (source_id,))],
                  'mappings': [_mapping(row) for row in conn.execute('SELECT * FROM source_mappings WHERE source_id=? ORDER BY updated_at DESC,id', (source_id,))], 'retention': RETENTION}
        if require_context().role == 'admin':
            result['grants'] = [{'user_id': row['user_id'], 'discover': bool(row['can_discover']), 'query': bool(row['can_query']), 'version': row['version']} for row in conn.execute('SELECT * FROM source_grants WHERE source_id=? ORDER BY user_id', (source_id,))]
        return result


@router.patch('/{source_id}')
def patch_source(source_id: str, body: SourcePatch):
    _role({'admin'})
    with closing(connect()) as conn, conn:
        source = _authorize(conn, source_id, 'manage', lock=True, enabled=False)
        _expected(source, body.expected_version)
        changes = body.model_dump(exclude_unset=True, exclude={'expected_version'})
        if 'config' in changes:
            changes['config'] = _validate_config(source['kind'], changes['config'])
        affects_mapping = 'config' in changes or 'credential_ref' in changes or changes.get('enabled') is False
        updated = {**source, **changes}
        conn.execute('UPDATE source_connections SET title=?,enabled=?,config=?,credential_ref=?,version=version+1,config_version=config_version+?,updated_at=? WHERE id=?',
                     (updated['title'], int(updated['enabled']), _json(updated['config']), updated['credential_ref'], int(affects_mapping), now(), source_id))
        if affects_mapping:
            _invalidate(conn, source_id, 'source_changed')
        result = _summary(conn, _source(conn, source_id))
    _audit('update', source_id, version=result['version'])
    return result


@router.put('/{source_id}/grants')
def set_grant(source_id: str, body: GrantBody):
    principal = _role({'admin'})
    with control_connection() as central, central.cursor() as cursor:
        cursor.execute("SELECT u.id,u.home_tenant_id,u.role,u.is_superadmin FROM sec_users u JOIN sec_tenants t ON t.id=u.home_tenant_id WHERE u.id=%s AND u.is_active=1 AND t.status='active'", (body.user_id,))
        user = cursor.fetchone()
        if not user or (user['home_tenant_id'] != principal.tenant_id and not (user['home_tenant_id'] == 'hq' and user['role'] == 'admin' and user['is_superadmin'])):
            _fail('grant_target_invalid', '只能授权本机构启用账号或具备本工作区运维权限的总部管理员。', 403)
    with closing(connect()) as conn, conn:
        _authorize(conn, source_id, 'manage', lock=True, enabled=False)
        conn.execute('INSERT INTO source_grants(source_id,user_id,can_discover,can_query,version,updated_by,updated_at) VALUES(?,?,?,?,?,?,?) ON DUPLICATE KEY UPDATE can_discover=VALUES(can_discover),can_query=VALUES(can_query),version=version+1,updated_by=VALUES(updated_by),updated_at=VALUES(updated_at)',
                     (source_id, body.user_id, int(body.discover), int(body.query), 1, principal.user_id, now()))
    _audit('grant', source_id, user_id=body.user_id, discover=body.discover, query=body.query)
    return {'user_id': body.user_id, 'discover': body.discover, 'query': body.query}


@router.post('/{source_id}/discover')
def discover_source(source_id: str):
    _role(BUILD)
    with closing(connect()) as conn:
        source = _authorize(conn, source_id, 'discover')
    resources = _remote_discover(source)
    with closing(connect()) as conn, conn:
        fresh = _authorize(conn, source_id, 'discover', lock=True, expected_version=source['version'])
        changes = _persist_discovery(conn, fresh, resources)
        result = {'source': _summary(conn, _source(conn, source_id)), 'resources': [_resource(row) for row in conn.execute('SELECT * FROM source_resources WHERE source_id=? ORDER BY resource_key', (source_id,))], 'changes': changes}
    _audit('discover', source_id, resource_count=len(resources), change_count=len(changes))
    return result


@router.get('/{source_id}/resources/{resource_id}/versions')
def resource_versions(source_id: str, resource_id: str):
    _role(ALL_ROLES)
    with closing(connect()) as conn:
        _authorize(conn, source_id, enabled=False)
        if not conn.execute('SELECT id FROM source_resources WHERE id=? AND source_id=?', (resource_id, source_id)).fetchone():
            _fail('resource_not_found', '资源不存在。', 404)
        items = []
        for row in conn.execute('SELECT * FROM source_schema_versions WHERE resource_id=? ORDER BY version DESC', (resource_id,)):
            value = dict(row); value['metadata'] = _decode(value['metadata']); value['available'] = bool(value['available']); items.append(value)
        return {'items': items}


@router.post('/{source_id}/mappings')
def create_mapping(source_id: str, body: MappingBody):
    principal = _role(BUILD)
    with closing(connect()) as conn, conn:
        source = _authorize(conn, source_id, 'discover', lock=True)
        definition, resource = _definition(conn, source_id, body)
        ident, stamp = _id('MAP'), now()
        conn.execute('INSERT INTO source_mappings(id,source_id,resource_id,title,definition,status,version,schema_hash,source_version,created_by,submitted_by,reviewed_by,review_reason,invalid_reason,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                     (ident, source_id, body.resource_id, body.title, _json(definition), 'draft', 1, resource['schema_hash'], source['config_version'], principal.user_id, None, None, None, None, stamp, stamp))
        _event(conn, ident, 'create')
        result = _mapping(_mapping_row(conn, source_id, ident))
    _audit('mapping_create', ident, source_id=source_id, version=1)
    return result


@router.put('/{source_id}/mappings/{mapping_id}')
def update_mapping(source_id: str, mapping_id: str, body: MappingUpdate):
    _role(BUILD)
    with closing(connect()) as conn, conn:
        source = _authorize(conn, source_id, 'discover', lock=True)
        row = _mapping_row(conn, source_id, mapping_id, lock=True)
        _expected(row, body.expected_version)
        if row['status'] != 'draft':
            _fail('mapping_not_draft', '请先修订为草稿，再编辑映射。', 409)
        definition, resource = _definition(conn, source_id, body)
        conn.execute('UPDATE source_mappings SET resource_id=?,title=?,definition=?,version=version+1,schema_hash=?,source_version=?,invalid_reason=NULL,updated_at=? WHERE id=?',
                     (body.resource_id, body.title, _json(definition), resource['schema_hash'], source['config_version'], now(), mapping_id))
        _event(conn, mapping_id, 'edit')
        result = _mapping(_mapping_row(conn, source_id, mapping_id))
    _audit('mapping_edit', mapping_id, source_id=source_id, version=result['version'])
    return result


@router.post('/{source_id}/mappings/{mapping_id}/action')
def mapping_action(source_id: str, mapping_id: str, body: MappingAction):
    principal = _role(REVIEW if body.action in {'publish', 'reject'} else BUILD)
    publish_source_version = None
    if body.action == 'publish':
        # A reviewer verifies the actual remote structure, without holding a
        # business transaction while the source is contacted.
        with closing(connect()) as conn:
            source = _authorize(conn, source_id, 'discover')
            candidate = _mapping(_mapping_row(conn, source_id, mapping_id))
            _expected(candidate, body.expected_version)
            if candidate['status'] != 'in_review':
                _fail('mapping_transition_invalid', '仅待审核映射可发布。', 409)
            if candidate['submitted_by'] == principal.user_id:
                _fail('reviewer_is_submitter', '提交人不能审核自己的映射，管理员同样适用。', 403)
            _validate_mapping_current(conn, source, candidate)
        observed = _remote_discover(source)
        with closing(connect()) as conn, conn:
            fresh = _authorize(conn, source_id, 'discover', lock=True, expected_version=source['version'])
            before = _mapping(_mapping_row(conn, source_id, mapping_id, lock=True))
            _expected(before, body.expected_version)
            _persist_discovery(conn, fresh, observed)
            checked = _mapping(_mapping_row(conn, source_id, mapping_id))
            drifted = checked['status'] == 'stale'
            publish_source_version = fresh['version']
        if drifted:
            _audit('mapping_publish', mapping_id, 'failed', source_id=source_id, code='schema_drift')
            _fail('schema_drift', '远端结构已变化，映射已失效；请修订后重新提交审核。', 409)
    with closing(connect()) as conn, conn:
        source = _authorize(conn, source_id, 'discover', lock=True, expected_version=publish_source_version)
        mapping = _mapping(_mapping_row(conn, source_id, mapping_id, lock=True))
        _expected(mapping, body.expected_version)
        action = body.action
        allowed = {'submit': {'draft'}, 'publish': {'in_review'}, 'reject': {'in_review'}, 'revise': {'published', 'rejected', 'stale'}}
        if mapping['status'] not in allowed[action]:
            _fail('mapping_transition_invalid', '当前映射状态不允许此操作。', 409)
        if action in {'publish', 'reject'} and mapping['submitted_by'] == principal.user_id:
            _fail('reviewer_is_submitter', '提交人不能审核自己的映射，管理员同样适用。', 403)
        if action in {'submit', 'publish'}:
            _validate_mapping_current(conn, source, mapping)
        status = {'submit': 'in_review', 'publish': 'published', 'reject': 'rejected', 'revise': 'draft'}[action]
        submitted = principal.user_id if action == 'submit' else None if action == 'revise' else mapping['submitted_by']
        reviewed = principal.user_id if action in {'publish', 'reject'} else None
        conn.execute('UPDATE source_mappings SET status=?,version=version+1,submitted_by=?,reviewed_by=?,review_reason=?,invalid_reason=NULL,updated_at=? WHERE id=?',
                     (status, submitted, reviewed, body.reason, now(), mapping_id))
        _event(conn, mapping_id, action, body.reason)
        result = _mapping(_mapping_row(conn, source_id, mapping_id))
    _audit('mapping_' + body.action, mapping_id, source_id=source_id, version=result['version'])
    return result


@router.get('/{source_id}/mappings/{mapping_id}/versions')
def mapping_versions(source_id: str, mapping_id: str):
    _role(ALL_ROLES)
    with closing(connect()) as conn:
        _authorize(conn, source_id, enabled=False)
        _mapping_row(conn, source_id, mapping_id)
        return {'items': [{**dict(row), 'snapshot': _public_snapshot(_decode(row['snapshot']), require_context().role)} for row in conn.execute('SELECT * FROM source_mapping_versions WHERE mapping_id=? ORDER BY version DESC', (mapping_id,))]}


def _selection(mapping, body):
    fields = {field['field']: field['column'] for field in mapping['fields']}
    selected = body.fields if body.fields is not None else list(fields)
    if len(set(selected)) != len(selected) or not set(selected) <= fields.keys():
        _fail('field_unknown', '查询字段须为不重复的已映射语义字段。')
    if any(item.field not in fields for item in [*body.filters, *body.order_by]):
        _fail('field_unknown', '条件和排序只能使用已映射的语义字段。')
    filters = []
    for item in body.filters:
        condition = {'column': fields[item.field], 'op': item.op}
        if item.op != 'is_null': condition['value'] = item.value
        filters.append(condition)
    selection = {'columns': [fields[field] for field in selected], 'filters': filters,
                 'order_by': [{'column': fields[item.field], 'direction': item.direction} for item in body.order_by], 'limit': body.limit}
    return selected, selection


def _mark_query_invalid(source_id, mapping_id, code, source_version, mapping_version):
    with closing(connect()) as conn, conn:
        _authorize(conn, source_id, 'query', lock=True, expected_version=source_version)
        row = _mapping_row(conn, source_id, mapping_id, lock=True)
        # An old in-flight query must never invalidate a newer reviewed revision.
        if row['status'] == 'published' and row['version'] == mapping_version:
            conn.execute("UPDATE source_mappings SET status='stale',version=version+1,invalid_reason=?,updated_at=? WHERE id=?", (code, now(), mapping_id))
            _event(conn, mapping_id, 'invalidate', code)


def execute_mapping_query(mapping_id: str, request: QueryBody | dict):
    """Shared read-only invocation boundary for HTTP and future MCP tools."""
    from .source_adapters import SourceFailure, discover, query
    body = request if isinstance(request, QueryBody) else QueryBody.model_validate(request)
    _role(ALL_ROLES)
    began, source, mapping = time.perf_counter(), None, None
    try:
        with closing(connect()) as conn:
            found = conn.execute('SELECT source_id FROM source_mappings WHERE id=?', (mapping_id,)).fetchone()
            if not found: _fail('mapping_not_found', '映射不存在。', 404)
            source = _authorize(conn, found['source_id'], 'query')
            mapping = _mapping(_mapping_row(conn, source['id'], mapping_id))
            if mapping['status'] != 'published':
                _fail('mapping_unpublished', '仅已发布且有效的映射可查询。', 409)
            resource = _validate_mapping_current(conn, source, mapping)
            fields, selection = _selection(mapping, body)
        # No business connection/lock is held while waiting on the external source.
        remote_resources = discover(source)
        remote = next((item for item in remote_resources if item['key'] == resource['resource_key']), None)
        if remote is None or _schema_hash(remote) != mapping['schema_hash']:
            with closing(connect()) as conn, conn:
                fresh = _authorize(conn, source['id'], 'query', lock=True, expected_version=source['version'])
                _persist_discovery(conn, fresh, remote_resources)
            _fail('schema_drift', '远端结构已漂移，映射已失效；请修订并重新审核。', 409)
        result = query(source, remote, selection)
        with closing(connect()) as conn, conn:
            fresh = _authorize(conn, source['id'], 'query', lock=True, expected_version=source['version'])
            current = _mapping(_mapping_row(conn, source['id'], mapping_id, lock=True))
            _expected(current, mapping['version'])
            if current['status'] != 'published':
                _fail('mapping_unpublished', '映射状态已变化，查询结果未返回。', 409)
            _validate_mapping_current(conn, fresh, current)
            # Exact adapter projection prevents hidden/unmapped columns escaping.
            if result['columns'] != selection['columns'] or any(set(row) != set(selection['columns']) for row in result['rows']):
                _fail('upstream_projection_invalid', '远端结果字段与已授权映射不一致。', 502)
            rows = [{field: row[column] for field, column in zip(fields, selection['columns'])} for row in result['rows']]
            response = {'mapping_id': mapping_id, 'mapping_version': mapping['version'], 'source_id': source['id'],
                        'resource_id': mapping['resource_id'], 'schema_hash': mapping['schema_hash'], 'queried_at': now(),
                        'columns': fields, 'rows': rows, 'truncated': result['truncated'], 'elapsed_ms': result['elapsed_ms'], 'retention': RETENTION}
            assert_principal_current(require_context())
            _audit('query', mapping_id, source_id=source['id'], schema_hash=mapping['schema_hash'], mapping_version=mapping['version'],
                   row_count=len(rows), duration_ms=round((time.perf_counter()-began)*1000, 2), code='ok', **_query_source_identity(source))
        return response
    except SourceFailure as exc:
        _audit('query', mapping_id, 'failed', source_id=source['id'] if source else None, code=exc.code,
               duration_ms=round((time.perf_counter()-began)*1000, 2), **_query_source_identity(source))
        if source and mapping and exc.code == 'SOURCE_SCHEMA_DRIFT':
            _mark_query_invalid(source['id'], mapping_id, 'schema_drift', source['version'], mapping['version'])
        _adapter_failure(exc)
    except HTTPException as exc:
        code = exc.detail.get('code', 'authorization_changed') if isinstance(exc.detail, dict) else 'authorization_changed'
        if source and mapping and code in {'target_changed', 'target_unpublished', 'target_inactive'}:
            _mark_query_invalid(source['id'], mapping_id, code, source['version'], mapping['version'])
        _audit('query', mapping_id, 'failed', source_id=source['id'] if source else None, code=code,
               duration_ms=round((time.perf_counter()-began)*1000, 2), **_query_source_identity(source))
        raise


@router.post('/{source_id}/mappings/{mapping_id}/query')
def query_mapping(source_id: str, mapping_id: str, body: QueryBody):
    _role(ALL_ROLES)
    with closing(connect()) as conn:
        _authorize(conn, source_id, 'query')
        _mapping_row(conn, source_id, mapping_id)
    return execute_mapping_query(mapping_id, body)
