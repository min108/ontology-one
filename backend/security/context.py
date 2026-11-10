"""Immutable, request-scoped identity. Never derive tenancy from request parameters."""
from __future__ import annotations
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from fastapi import HTTPException


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: str
    username: str
    home_tenant_id: str
    tenant_id: str
    role: str
    is_superadmin: bool
    session_id: str
    auth_version: int
    database_name: str
    readonly: bool = False
    request_id: str = ''
    visibility: str = 'private'
    must_change_password: bool = False
    identity_kind: str = 'session'
    application_id: str = ''
    credential_id: str = ''
    permission_version: int = 0


_current: ContextVar[Principal | None] = ContextVar('ontology_principal', default=None)


def get_context() -> Principal | None:
    return _current.get()


def require_context() -> Principal:
    principal = get_context()
    if principal is None:
        raise HTTPException(401, '请先登录。')
    return principal


@contextmanager
def use_context(principal: Principal):
    token = _current.set(principal)
    try:
        yield principal
    finally:
        _current.reset(token)
