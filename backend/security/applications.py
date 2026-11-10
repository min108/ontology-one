"""Independent, tenant-fixed application identities and delegated MCP scopes.

Opaque credentials are never user sessions. Business writes are denied except
for the reviewed source-query boundary's bounded metadata maintenance scope.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
from contextvars import ContextVar
import json
import os
import re
import secrets
import time
import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

from .context import Principal, require_context
from . import store

router = APIRouter(prefix='/integrations/applications', tags=['integration-applications'])
_METADATA_SCOPE = ContextVar('application_source_metadata', default=None)
TOKEN_PATTERN = re.compile(r'^onto_app_[A-Za-z0-9_-]{43}$')
LIMITS = {'timeout_ms': 30000, 'execution_timeout_ms': 30000, 'cleanup_timeout_ms': 3000,
          'max_concurrent_per_process': 4, 'max_rows': 200,
          'max_join_keys': 50, 'max_result_bytes': 2097152}
RETENTION = {'result': 'response_only', 'cache': 'disabled', 'audit': 'metadata_only'}
DDL = (
    '''CREATE TABLE IF NOT EXISTS sec_applications(
       id VARCHAR(64) PRIMARY KEY,tenant_id VARCHAR(64) NOT NULL,name VARCHAR(200) NOT NULL,
       description VARCHAR(2000) NOT NULL,delegate_user_id VARCHAR(64) NOT NULL,
       enabled TINYINT NOT NULL,version INT NOT NULL,tool_scopes JSON NOT NULL,mapping_ids JSON NOT NULL,
       created_by VARCHAR(64) NOT NULL,created_at DOUBLE NOT NULL,updated_at DOUBLE NOT NULL,
       INDEX idx_app_tenant(tenant_id),FOREIGN KEY(tenant_id) REFERENCES sec_tenants(id),
       FOREIGN KEY(delegate_user_id) REFERENCES sec_users(id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''',
    '''CREATE TABLE IF NOT EXISTS sec_application_credentials(
       id VARCHAR(64) PRIMARY KEY,application_id VARCHAR(64) NOT NULL,token_hash CHAR(64) NOT NULL UNIQUE,
       token_prefix VARCHAR(32) NOT NULL,delegate_auth_version INT NOT NULL,expires_at DOUBLE NOT NULL,
       revoked_at DOUBLE NULL,created_by VARCHAR(64) NOT NULL,created_at DOUBLE NOT NULL,last_used_at DOUBLE NULL,
       INDEX idx_app_credential(application_id),FOREIGN KEY(application_id) REFERENCES sec_applications(id)
       ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''',
)


def ensure_schema(conn_or_cursor):
    if hasattr(conn_or_cursor, 'cursor'):
        with conn_or_cursor.cursor() as cursor:
            for statement in DDL:
                cursor.execute(statement)
    else:
        for statement in DDL:
            conn_or_cursor.execute(statement)


def _deny(message='应用无权执行此操作。', status=403):
    raise HTTPException(status, {'code': 'FORBIDDEN' if status == 403 else 'UNAUTHENTICATED', 'message': message})


def _decode(value):
    return json.loads(value) if isinstance(value, str) else value


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _token_row(cursor, *, raw_token=None, principal=None):
    sql = '''SELECT a.*,c.id credential_id,c.delegate_auth_version,c.expires_at,c.revoked_at,
       c.last_used_at,u.username,u.home_tenant_id,u.role,u.is_active,u.must_change_password,u.auth_version,
       t.db_name,t.status tenant_status FROM sec_applications a
       JOIN sec_application_credentials c ON c.application_id=a.id
       JOIN sec_users u ON u.id=a.delegate_user_id JOIN sec_tenants t ON t.id=a.tenant_id WHERE '''
    if raw_token is not None:
        cursor.execute(sql + 'c.token_hash=%s', (store.token_hash(raw_token),))
    else:
        cursor.execute(sql + 'a.id=%s AND c.id=%s', (principal.application_id, principal.credential_id))
    return cursor.fetchone()


def _principal(row, request_id):
    if not row or not row['enabled'] or row['revoked_at'] is not None or row['expires_at'] <= time.time():
        return None
    if (not row['is_active'] or row['must_change_password'] or row['tenant_status'] != 'active'
            or row['home_tenant_id'] != row['tenant_id'] or row['role'] not in store.ROLES
            or row['delegate_auth_version'] != row['auth_version']):
        return None
    return Principal(user_id=row['delegate_user_id'], username=row['username'], home_tenant_id=row['tenant_id'],
                     tenant_id=row['tenant_id'], role=row['role'], is_superadmin=False, session_id='',
                     auth_version=row['auth_version'], database_name=row['db_name'], readonly=False,
                     request_id=request_id, identity_kind='application', application_id=row['id'],
                     credential_id=row['credential_id'], permission_version=row['version'])


def authenticate_application(raw_token, request_id=''):
    if not isinstance(raw_token, str) or not TOKEN_PATTERN.fullmatch(raw_token):
        return None
    with store.control_connection() as connection, connection.cursor() as cursor:
        row = _token_row(cursor, raw_token=raw_token)
        principal = _principal(row, request_id)
        if principal and (row['last_used_at'] is None or row['last_used_at'] < time.time() - 60):
            cursor.execute('UPDATE sec_application_credentials SET last_used_at=%s WHERE id=%s',
                           (time.time(), principal.credential_id))
        return principal


def _same(current, captured):
    return current is not None and all(getattr(current, name) == getattr(captured, name) for name in (
        'identity_kind', 'application_id', 'credential_id', 'permission_version', 'user_id', 'username',
        'home_tenant_id', 'tenant_id', 'database_name', 'role', 'auth_version', 'session_id',
        'is_superadmin', 'readonly', 'must_change_password'))


def _scope_matches(principal):
    scope = _METADATA_SCOPE.get()
    return scope is not None and scope[:4] == (principal.application_id, principal.credential_id,
                                               principal.tenant_id, principal.permission_version)


def _checked_row(principal):
    if principal is None or principal.identity_kind != 'application':
        _deny('需要独立应用身份。', 401)
    with store.control_connection() as connection, connection.cursor() as cursor:
        row = _token_row(cursor, principal=principal)
        current = _principal(row, principal.request_id)
        if not _same(current, principal):
            _deny('应用凭据、委托账号或授权范围已变化。', 401)
        return current, row


def assert_application_current(principal, require_write=False):
    current, _ = _checked_row(principal)
    if require_write and not _scope_matches(principal):
        _deny('应用只允许读取；当前操作不在来源元数据维护范围内。')
    return current


def application_permissions(principal):
    _, row = _checked_row(principal)
    return {'tool_scopes': _decode(row['tool_scopes']), 'mapping_ids': _decode(row['mapping_ids']),
            'version': row['version'], 'name': row['name']}


def require_tool(principal, tool_name):
    if tool_name not in application_permissions(principal)['tool_scopes']:
        _deny('应用未获此工具授权。')


def require_mapping(principal, mapping_id):
    if mapping_id not in application_permissions(principal)['mapping_ids']:
        _deny('应用未获此映射授权。')


def intersect_source_permissions(principal, conn, source_id, base_permissions):
    if principal.identity_kind == 'session':
        return base_permissions
    permissions = application_permissions(principal)
    mapping_ids = permissions['mapping_ids']
    bound = False
    if mapping_ids:
        marks = ','.join('?' for _ in mapping_ids)
        bound = bool(conn.execute('SELECT id FROM source_mappings WHERE source_id=? AND id IN (' + marks + ') LIMIT 1',
                                  [source_id, *mapping_ids]).fetchone())
    return {'manage': False, 'discover': bool(bound and base_permissions.get('discover')),
            'query': bool(bound and base_permissions.get('query'))}


@contextmanager
def application_source_metadata(principal, mapping_id):
    require_mapping(principal, mapping_id)
    scope = (principal.application_id, principal.credential_id, principal.tenant_id,
             principal.permission_version, mapping_id)
    token = _METADATA_SCOPE.set(scope)
    try:
        yield
        assert_application_current(principal)
    finally:
        _METADATA_SCOPE.reset(token)


def validate_application_write(principal, sql):
    """Accept only the exact DML shapes used by source query drift maintenance.

    No DDL, deletion, workflow publication, source grants or generic entity writes
    are allowed, even when the delegated user is an administrator.
    """
    assert_application_current(principal, require_write=True)
    value = ' '.join(sql.strip().split()).lower()
    if any(marker in value for marker in (';', '--', '/*', '*/', '#', '`')):
        _deny('应用元数据写入语句不受支持。')
    if value == 'update source_connections set last_discovered_at=? where id=?':
        return
    if value == "update source_mappings set status='stale',version=version+1,invalid_reason=?,updated_at=? where id=?":
        return
    match = re.fullmatch(r'update source_resources set (.+) where id=\?', value)
    if match:
        assignments = match.group(1).split(',')
        allowed = {'schema_hash=?', 'schema_version=?', 'available=1', 'available=0', 'metadata=?', 'observed_at=?'}
        if assignments and all(part in allowed for part in assignments):
            return
    inserts = {
        'source_resources': 'id,source_id,resource_key,resource_key_hash,schema_hash,schema_version,available,metadata,observed_at',
        'source_schema_versions': 'id,resource_id,version,schema_hash,available,metadata,observed_by,observed_at',
        'source_mapping_versions': 'id,mapping_id,version,action,snapshot,actor_id,reason,created_at',
    }
    for table, columns in inserts.items():
        marks = ','.join('?' for _ in columns.split(','))
        if value == f'insert into {table}({columns}) values({marks})':
            return
    _deny('应用不能修改业务数据或授权，只能维护查询涉及的来源元数据。')


class StrictBody(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, str_strip_whitespace=True)


Name = Annotated[str, Field(min_length=1, max_length=200)]
Version = Annotated[StrictInt, Field(ge=1)]
Days = Annotated[StrictInt, Field(ge=1, le=90)]
ToolScopes = Annotated[list[Annotated[str, Field(min_length=1, max_length=100)]], Field(min_length=1, max_length=20)]
MappingIds = Annotated[list[Annotated[str, Field(min_length=1, max_length=191)]], Field(max_length=100)]


class ApplicationCreate(StrictBody):
    name: Name
    description: Annotated[str, Field(max_length=2000)] = ''
    delegate_user_id: Annotated[str, Field(min_length=1, max_length=64)]
    tool_scopes: ToolScopes
    mapping_ids: MappingIds = Field(default_factory=list)
    expires_days: Days = 30


class ApplicationPatch(StrictBody):
    expected_version: Version
    name: Name | None = None
    description: Annotated[str | None, Field(max_length=2000)] = None
    enabled: StrictBool | None = None
    tool_scopes: ToolScopes | None = None
    mapping_ids: MappingIds | None = None

    @model_validator(mode='after')
    def changes_present(self):
        changes = self.model_dump(exclude_unset=True, exclude={'expected_version'})
        if not changes or any(value is None for value in changes.values()):
            raise ValueError('请提供至少一个非空修改字段。')
        return self


class CredentialRotate(StrictBody):
    expected_version: Version
    expires_days: Days = 30
    reason: Annotated[str, Field(min_length=1, max_length=2000)]


class ApplicationRevoke(StrictBody):
    expected_version: Version
    reason: Annotated[str, Field(min_length=1, max_length=2000)]


def _manager():
    principal = require_context()
    if principal.identity_kind != 'session':
        _deny('应用管理只接受已登录的管理员会话。')
    return store.require_admin(principal)


def _tools():
    from ..mcp_contracts import TOOL_DESCRIPTIONS
    return [{'name': name, **value} for name, value in TOOL_DESCRIPTIONS.items()]


def _limits():
    maximum = int(os.getenv('ONTOLOGY_MCP_MAX_CONCURRENT', '4'))
    if not 1 <= maximum <= 16:
        raise RuntimeError('ONTOLOGY_MCP_MAX_CONCURRENT 须为1至16。')
    return {**LIMITS, 'max_concurrent_per_process': maximum}


def _validate_scopes(tool_scopes, mapping_ids):
    from ..mcp_contracts import TOOL_MODELS
    if len(set(tool_scopes)) != len(tool_scopes) or set(tool_scopes) - TOOL_MODELS.keys():
        raise HTTPException(422, '请选择不重复的已注册工具。')
    if len(set(mapping_ids)) != len(mapping_ids):
        raise HTTPException(422, '映射授权不可重复。')


def _delegate(cursor, user_id, tenant_id, *, lock=False):
    cursor.execute('SELECT id,username,display_name,home_tenant_id,role,is_active,must_change_password,auth_version FROM sec_users WHERE id=%s' + (' FOR SHARE' if lock else ''), (user_id,))
    user = cursor.fetchone()
    if (not user or not user['is_active'] or user['must_change_password'] or user['home_tenant_id'] != tenant_id
            or user['role'] not in store.ROLES):
        raise HTTPException(422, '委托账号须为本机构已启用且已修改初始密码的账号。')
    return user


def _validate_bindings(delegate, mapping_ids):
    if not mapping_ids:
        return
    from ..db import connect
    from ..sources import _source, _mapping, _validate_mapping_current
    with closing(connect()) as conn:
        for mapping_id in sorted(mapping_ids):
            row = conn.execute("SELECT * FROM source_mappings WHERE id=? AND status='published' FOR SHARE", (mapping_id,)).fetchone()
            if not row:
                raise HTTPException(422, '授权映射须为本机构当前已发布的映射。')
            source = _source(conn, row['source_id'])
            if not source['enabled']:
                raise HTTPException(422, '映射对应数据源已停用。')
            if delegate['role'] != 'admin':
                grant = conn.execute('SELECT can_query FROM source_grants WHERE source_id=? AND user_id=?', (source['id'], delegate['id'])).fetchone()
                if not grant or not grant['can_query']:
                    raise HTTPException(422, '委托账号尚未获得某条映射的数据源查询权限。')
            _validate_mapping_current(conn, source, _mapping(row))


def _app(cursor, application_id, tenant_id, *, lock=False):
    cursor.execute('SELECT a.*,u.username delegate_username,u.home_tenant_id,u.role,u.is_active,u.must_change_password,u.auth_version FROM sec_applications a JOIN sec_users u ON u.id=a.delegate_user_id WHERE a.id=%s AND a.tenant_id=%s' + (' FOR UPDATE' if lock else ''), (application_id, tenant_id))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(404, '应用不存在。')
    return row


def _expected(row, version):
    if row['version'] != version:
        raise HTTPException(409, '应用已被更新，请刷新后重试。')


def _public(cursor, row):
    result = {key: row[key] for key in ('id', 'tenant_id', 'name', 'description', 'delegate_user_id', 'delegate_username', 'version')}
    result.update(enabled=bool(row['enabled']), tool_scopes=_decode(row['tool_scopes']), mapping_ids=_decode(row['mapping_ids']),
                  created_at=store._utc(row['created_at']), updated_at=store._utc(row['updated_at']))
    cursor.execute('SELECT id,token_prefix,delegate_auth_version,expires_at,revoked_at,created_at,last_used_at FROM sec_application_credentials WHERE application_id=%s ORDER BY created_at DESC,id', (row['id'],))
    credentials = []
    for credential in cursor.fetchall():
        reason = ('revoked' if credential['revoked_at'] is not None else 'expired' if credential['expires_at'] <= time.time()
                  else 'application_disabled' if not row['enabled'] else 'delegate_changed' if not row['is_active']
                  or row['must_change_password'] or row['home_tenant_id'] != row['tenant_id']
                  or row['auth_version'] != credential['delegate_auth_version'] else None)
        credentials.append({'id': credential['id'], 'prefix': credential['token_prefix'],
                            **{key: store._utc(credential[key]) for key in ('expires_at', 'revoked_at', 'created_at', 'last_used_at')},
                            'usable': reason is None, 'unusable_reason': reason})
    result['credentials'] = credentials
    return result


def _issue(cursor, principal, application_id, delegate, days):
    raw, credential_id, timestamp = 'onto_app_' + secrets.token_urlsafe(32), uuid.uuid4().hex, time.time()
    expiry = timestamp + days * 86400
    cursor.execute('INSERT INTO sec_application_credentials(id,application_id,token_hash,token_prefix,delegate_auth_version,expires_at,revoked_at,created_by,created_at,last_used_at) VALUES(%s,%s,%s,%s,%s,%s,NULL,%s,%s,NULL)',
                   (credential_id, application_id, store.token_hash(raw), raw[:20], delegate['auth_version'], expiry, principal.user_id, timestamp))
    return {'id': credential_id, 'token': raw, 'prefix': raw[:20], 'expires_at': store._utc(expiry)}


@router.get('')
def list_applications():
    principal = _manager()
    with store.control_connection() as connection, connection.cursor() as cursor:
        cursor.execute('SELECT id FROM sec_applications WHERE tenant_id=%s ORDER BY created_at DESC,id', (principal.tenant_id,))
        ids = [row['id'] for row in cursor.fetchall()]
        items = [_public(cursor, _app(cursor, ident, principal.tenant_id)) for ident in ids]
    return {'items': items, 'tools': _tools(), 'endpoint': '/mcp/', 'contract_version': '1.0', 'limits': _limits(), 'retention': RETENTION}


@router.get('/options')
def application_options():
    principal = _manager()
    with store.control_connection() as connection, connection.cursor() as cursor:
        cursor.execute('SELECT id,username,display_name,role FROM sec_users WHERE home_tenant_id=%s AND is_active=1 AND must_change_password=0 ORDER BY username', (principal.tenant_id,))
        users = list(cursor.fetchall())
    from ..db import connect
    with closing(connect()) as connection:
        mappings = [dict(row) for row in connection.execute("SELECT m.id,m.title,m.source_id,s.title source_title,m.status,m.version FROM source_mappings m JOIN source_connections s ON s.id=m.source_id WHERE m.status='published' AND s.enabled=1 ORDER BY m.title,m.id")]
    return {'users': users, 'mappings': mappings, 'tools': _tools()}


@router.post('')
def create_application(body: ApplicationCreate):
    principal = _manager()
    _validate_scopes(body.tool_scopes, body.mapping_ids)
    with store.control_connection() as connection, connection.cursor() as cursor:
        delegate = _delegate(cursor, body.delegate_user_id, principal.tenant_id)
    _validate_bindings(delegate, body.mapping_ids)
    with store.authorized_control_connection(principal) as connection, connection.cursor() as cursor:
        delegate = _delegate(cursor, body.delegate_user_id, principal.tenant_id, lock=True)
        ident, timestamp = uuid.uuid4().hex, time.time()
        cursor.execute('INSERT INTO sec_applications(id,tenant_id,name,description,delegate_user_id,enabled,version,tool_scopes,mapping_ids,created_by,created_at,updated_at) VALUES(%s,%s,%s,%s,%s,1,1,%s,%s,%s,%s,%s)',
                       (ident, principal.tenant_id, body.name, body.description, delegate['id'], _json(body.tool_scopes), _json(body.mapping_ids), principal.user_id, timestamp, timestamp))
        credential = _issue(cursor, principal, ident, delegate, body.expires_days)
        result = _public(cursor, _app(cursor, ident, principal.tenant_id))
        store._audit(cursor, principal, 'application_create', ident, 'success', {'delegate_user_id': delegate['id'], 'tool_count': len(body.tool_scopes), 'mapping_count': len(body.mapping_ids), 'credential_id': credential['id']})
    return {'application': result, 'credential': credential}


@router.patch('/{application_id}')
def update_application(application_id: str, body: ApplicationPatch):
    principal = _manager()
    with store.control_connection() as connection, connection.cursor() as cursor:
        before = _app(cursor, application_id, principal.tenant_id)
        _expected(before, body.expected_version)
        delegate = _delegate(cursor, before['delegate_user_id'], principal.tenant_id) if body.mapping_ids is not None else None
    scopes = body.tool_scopes if body.tool_scopes is not None else _decode(before['tool_scopes'])
    mappings = body.mapping_ids if body.mapping_ids is not None else _decode(before['mapping_ids'])
    _validate_scopes(scopes, mappings)
    if delegate is not None:
        _validate_bindings(delegate, mappings)
    with store.authorized_control_connection(principal) as connection, connection.cursor() as cursor:
        row = _app(cursor, application_id, principal.tenant_id, lock=True)
        _expected(row, body.expected_version)
        changes = body.model_dump(exclude_unset=True, exclude={'expected_version'})
        updated = {**row, **changes}
        cursor.execute('UPDATE sec_applications SET name=%s,description=%s,enabled=%s,tool_scopes=%s,mapping_ids=%s,version=version+1,updated_at=%s WHERE id=%s',
                       (updated['name'], updated['description'], int(updated['enabled']), _json(scopes), _json(mappings), time.time(), application_id))
        result = _public(cursor, _app(cursor, application_id, principal.tenant_id))
        store._audit(cursor, principal, 'application_update', application_id, 'success', {'version': result['version'], 'fields': sorted(changes)})
    return result


@router.post('/{application_id}/credentials')
def rotate_credential(application_id: str, body: CredentialRotate):
    principal = _manager()
    with store.authorized_control_connection(principal) as connection, connection.cursor() as cursor:
        row = _app(cursor, application_id, principal.tenant_id, lock=True)
        _expected(row, body.expected_version)
        delegate = _delegate(cursor, row['delegate_user_id'], principal.tenant_id, lock=True)
        cursor.execute('UPDATE sec_application_credentials SET revoked_at=%s WHERE application_id=%s AND revoked_at IS NULL', (time.time(), application_id))
        cursor.execute('UPDATE sec_applications SET version=version+1,updated_at=%s WHERE id=%s', (time.time(), application_id))
        credential = _issue(cursor, principal, application_id, delegate, body.expires_days)
        result = _public(cursor, _app(cursor, application_id, principal.tenant_id))
        store._audit(cursor, principal, 'application_rotate', application_id, 'success', {'version': result['version'], 'credential_id': credential['id'], 'reason': body.reason})
    return {'application': result, 'credential': credential}


@router.post('/{application_id}/revoke')
def revoke_application(application_id: str, body: ApplicationRevoke):
    principal = _manager()
    with store.authorized_control_connection(principal) as connection, connection.cursor() as cursor:
        row = _app(cursor, application_id, principal.tenant_id, lock=True)
        _expected(row, body.expected_version)
        cursor.execute('UPDATE sec_application_credentials SET revoked_at=%s WHERE application_id=%s AND revoked_at IS NULL', (time.time(), application_id))
        cursor.execute('UPDATE sec_applications SET version=version+1,updated_at=%s WHERE id=%s', (time.time(), application_id))
        result = _public(cursor, _app(cursor, application_id, principal.tenant_id))
        store._audit(cursor, principal, 'application_revoke', application_id, 'success', {'version': result['version'], 'reason': body.reason})
    return result
