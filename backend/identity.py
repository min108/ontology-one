"""Password sessions and explicit account/tenant administration API.

OIDC is an extension boundary; no password login is presented as enterprise SSO.
"""
from __future__ import annotations
import os
from typing import Literal
from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from .security.context import require_context
from .security import store

router = APIRouter(tags=['身份与机构权限'])


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Login(StrictModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=128)


class PasswordChange(StrictModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


class Workspace(StrictModel):
    tenant_id: str = Field(min_length=1, max_length=64)


class UserCreate(StrictModel):
    username: str = Field(min_length=3, max_length=80)
    display_name: str = Field(default='', max_length=120)
    tenant_id: str = Field(min_length=1, max_length=64)
    role: Literal['admin', 'modeler', 'reviewer']
    password: str = Field(min_length=12, max_length=128)
    must_change_password: bool = True


class UserUpdate(StrictModel):
    reason: str | None = Field(default=None, min_length=2, max_length=500)
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    role: Literal['admin', 'modeler', 'reviewer'] | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=12, max_length=128)


class GrantCreate(StrictModel):
    user_id: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(min_length=1, max_length=64)
    access: Literal['read_published', 'read_private', 'write']
    reason: str = Field(min_length=2, max_length=500)


def _ip(request):
    # Forwarded IP headers are intentionally not trusted by the application.
    return request.client.host if request.client else 'unknown'


def _raw(request):
    return request.cookies.get(store.COOKIE_NAME, '')


def _cookie(response, request, raw):
    configured = os.getenv('ONTOLOGY_COOKIE_SECURE', '').strip().lower()
    secure = configured in ('true', '1', 'yes') if configured else request.url.scheme == 'https'
    response.set_cookie(store.COOKIE_NAME, raw, max_age=store.SESSION_TTL, httponly=True,
                        secure=secure, samesite='lax', path='/')
    response.headers['Cache-Control'] = 'no-store'


def _logged_in(raw, request):
    result = store.authenticate_session(raw, getattr(request.state, 'request_id', ''))
    if not result:
        raise HTTPException(401, '登录状态无效，请重新登录。')
    return result[0]


@router.post('/auth/login')
def login(body: Login, request: Request, response: Response):
    raw = store.login(body.username, body.password, _ip(request), request.headers.get('user-agent', ''), getattr(request.state, 'request_id', ''))
    principal = _logged_in(raw, request)
    from .db import ensure_tenant_schema
    ensure_tenant_schema(principal.tenant_id)
    result = store.session_payload(principal, raw)
    _cookie(response, request, raw)
    return result


@router.get('/auth/session')
@router.get('/auth/me')
def current_session(request: Request):
    return store.session_payload(require_context(), _raw(request))


@router.post('/auth/logout')
def logout(response: Response):
    principal = require_context()
    store.revoke_session(principal, principal.session_id)
    response.delete_cookie(store.COOKIE_NAME, path='/', httponly=True, samesite='lax')
    return {'ok': True}


@router.get('/auth/sessions')
def sessions():
    return {'items': store.sessions(require_context())}


@router.delete('/auth/sessions/{session_id}')
def revoke_session(session_id: str, response: Response):
    principal = require_context()
    store.revoke_session(principal, session_id)
    if session_id == principal.session_id:
        response.delete_cookie(store.COOKIE_NAME, path='/', httponly=True, samesite='lax')
    return {'ok': True}


@router.post('/auth/password')
def change_password(body: PasswordChange, request: Request, response: Response):
    raw = store.change_password(require_context(), body.current_password, body.new_password,
                                _ip(request), request.headers.get('user-agent', ''))
    result = store.session_payload(_logged_in(raw, request), raw)
    _cookie(response, request, raw)
    return result


@router.post('/auth/workspace')
def workspace(body: Workspace, request: Request):
    principal = require_context()
    current = store.session_payload(principal, _raw(request))
    if body.tenant_id not in {item['tenant_id'] for item in current['workspaces']}:
        raise HTTPException(403, '未授权进入此机构业务工作区。')
    from .db import ensure_tenant_schema
    ensure_tenant_schema(body.tenant_id)
    store.switch_workspace(principal, body.tenant_id)
    return store.session_payload(_logged_in(_raw(request), request), _raw(request))


@router.get('/auth/oidc')
@router.get('/auth/oidc/config')
def oidc_configuration():
    configured = bool(os.getenv('ONTOLOGY_OIDC_ISSUER'))
    return {'enabled': False, 'configured': configured,
            'status': 'adapter_not_implemented' if configured else 'not_configured',
            'message': '企业 OIDC 单点登录适配器尚未接入；当前使用平台账号登录。' if configured else '尚未配置企业 OIDC，当前使用平台账号登录。',
            'required_capabilities': ['issuer discovery and signing-key validation', 'authorization code with PKCE', 'state and nonce validation', 'explicit subject-to-user mapping']}


@router.get('/admin/tenants')
def tenants():
    principal = store.require_admin(require_context())
    rows = store.list_tenants()
    return {'items': [{key: row[key] for key in ('id', 'name', 'category', 'status')} for row in rows
                      if principal.is_superadmin or row['id'] == principal.home_tenant_id]}


@router.get('/admin/users')
def users():
    return {'items': store.list_users(require_context())}


@router.post('/admin/users')
def create_user(body: UserCreate):
    uid = store.create_user(require_context(), **body.model_dump())
    return {'id': uid, 'ok': True}


@router.patch('/admin/users/{user_id}')
def update_user(user_id: str, body: UserUpdate):
    store.update_user(require_context(), user_id, body.model_dump(exclude_unset=True, exclude_none=True))
    return {'id': user_id, 'ok': True, 'sessions_revoked': True}


@router.get('/admin/grants')
def grants():
    return {'items': store.list_grants(require_context())}


@router.post('/admin/grants')
def grant(body: GrantCreate):
    ident = store.grant_access(require_context(), **body.model_dump())
    return {'id': ident, 'ok': True}


@router.delete('/admin/grants/{grant_id}')
def revoke_grant(grant_id: str):
    store.revoke_grant(require_context(), grant_id)
    return {'ok': True}


@router.get('/admin/audit')
def audit(page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100)):
    return store.audit_records(require_context(), page, page_size)
