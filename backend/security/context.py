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
_readonly_execution = ContextVar('ontology_owned_readonly_execution', default=None)


@contextmanager
def readonly_session_execution(principal: Principal, guard):
    """Server-owned live agent or private-pipe tool scope; never HTTP input.

    The live account, session and grants are still checked by the store. Only
    the mutable UI workspace selection is replaced by the captured home scope.
    """
    if (not isinstance(principal, Principal) or principal.identity_kind != 'session'
            or principal.tenant_id != principal.home_tenant_id or principal.readonly
            or principal.must_change_password or not callable(guard)):
        raise HTTPException(403, '当前身份不能进入本体只读任务。')
    token = _readonly_execution.set((principal, guard))
    try:
        yield
    finally:
        _readonly_execution.reset(token)


def has_readonly_session_execution(principal: Principal):
    scope = _readonly_execution.get()
    if scope is None or scope[0] != principal:
        return False
    if scope[1]() is not True:
        raise HTTPException(409, '本体只读任务已经结束或取消。')
    return True


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
