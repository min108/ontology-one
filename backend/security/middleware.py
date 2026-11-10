"""ASGI identity, tenant context, CSRF and explicit business authorization.

The principal comes only from a server-side session lookup on every request.
Raw tokens, passwords, CSRF tokens and request bodies never enter audit details.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import uuid
from http.cookies import CookieError, SimpleCookie
from urllib.parse import urlsplit, parse_qs

from starlette.responses import JSONResponse

from .context import use_context
from ..db import business_readonly
from . import store
from .policy import authorize, is_control_path, needs_body

COOKIE_NAME = 'ontology_session'
MAX_POLICY_BODY = 1024 * 1024
MAX_SKILL_BODY = 3 * 1024 * 1024
SAFE_METHODS = frozenset({'GET', 'HEAD', 'OPTIONS'})
PUBLIC = frozenset({
    ('GET', '/api/health'),
    ('HEAD', '/api/health'),
    ('POST', '/api/auth/login'),
    ('GET', '/api/auth/oidc'),
    ('GET', '/api/auth/oidc/config'),
})
log = logging.getLogger(__name__)


def _headers(scope) -> dict[str, str]:
    return {key.decode('latin-1').lower(): value.decode('latin-1') for key, value in scope.get('headers', ())}


def _cookie(headers: dict[str, str]) -> str:
    try:
        parsed = SimpleCookie()
        parsed.load(headers.get('cookie', ''))
        item = parsed.get(COOKIE_NAME)
        value = item.value if item else ''
        return value if 20 <= len(value) <= 256 else ''
    except (CookieError, ValueError):
        return ''


def _origin(value: str) -> str:
    """Canonical origin without accepting credentials, paths or opaque origins."""
    try:
        url = urlsplit(value)
        if url.scheme not in {'http', 'https'} or not url.hostname or url.username or url.password:
            return ''
        if url.path not in {'', '/'} or url.query or url.fragment:
            return ''
        default = 443 if url.scheme == 'https' else 80
        port = url.port or default
        host = url.hostname.lower()
        host = '[' + host + ']' if ':' in host else host
        return url.scheme + '://' + host + (':' + str(port) if port != default else '')
    except (TypeError, ValueError):
        return ''


class SecurityMiddleware:
    def __init__(self, app, trusted_origins=None):
        self.app = app
        configured = trusted_origins if trusted_origins is not None else os.getenv('ONTOLOGY_TRUSTED_ORIGINS', '').split(',')
        if isinstance(configured, str):
            configured = configured.split(',')
        defaults = ['http://localhost:8765', 'http://127.0.0.1:8765', 'http://localhost:5173', 'http://127.0.0.1:5173']
        self.trusted_origins = frozenset(filter(None, (_origin(value.strip()) for value in (configured if any(configured) else defaults))))

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            # No WebSocket API is declared. An unguarded future socket must not
            # become a second authenticated transport accidentally.
            if scope['type'] == 'websocket':
                await send({'type': 'websocket.close', 'code': 1008})
                return
            await self.app(scope, receive, send)
            return
        path, method = scope.get('path', ''), scope['method'].upper()
        if not (path == '/api' or path.startswith('/api/') or path in {'/docs', '/redoc', '/openapi.json'}):
            await self.app(scope, receive, send)
            return
        request_id = uuid.uuid4().hex
        scope.setdefault('state', {})['request_id'] = request_id
        headers = _headers(scope)

        async def emit(message):
            if message['type'] == 'http.response.start':
                message = dict(message)
                response_headers = [(k, v) for k, v in message.get('headers', ()) if k.lower() not in {b'x-request-id', b'cache-control'}]
                response_headers.extend([(b'x-request-id', request_id.encode()), (b'cache-control', b'no-store')])
                message['headers'] = response_headers
            await send(message)

        async def error(status, detail, *, clear_cookie=False):
            response = JSONResponse({'detail': detail, 'request_id': request_id}, status_code=status)
            if clear_cookie:
                response.delete_cookie(COOKIE_NAME, path='/', httponly=True, samesite='strict')
            await response(scope, receive, emit)

        # A browser's Origin is checked against explicit deployment configuration,
        # never a user-controlled Host header. Non-browser API clients still need
        # the session's CSRF secret for every unsafe authenticated request.
        origin = headers.get('origin')
        if origin is not None and _origin(origin) not in self.trusted_origins:
            await error(403, '请求来源不在允许的站点范围。')
            return
        if method == 'OPTIONS':
            await self.app(scope, receive, emit)
            return
        if (method, path) in PUBLIC:
            if path == '/api/auth/login' and headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
                await error(415, '登录请求须使用 JSON。')
                return
            await self.app(scope, receive, emit)
            return
        token = _cookie(headers)
        if not token:
            await error(401, '请先登录。')
            return
        try:
            authenticated = await asyncio.to_thread(store.authenticate_session, token, request_id)
        except Exception:
            log.exception('Identity store unavailable; request=%s', request_id)
            await error(503, '身份服务暂时不可用，请稍后重试。')
            return
        if authenticated is None:
            await error(401, '登录已失效，请重新登录。', clear_cookie=True)
            return
        principal, csrf_hash = authenticated
        scope['state']['principal'] = principal

        async def audit(action, outcome, details=None):
            await asyncio.to_thread(store.append_audit, principal, action, path, outcome, details)

        async def deny(status, detail, reason):
            try:
                await audit('access_denied', 'denied', {'method': method, 'reason': reason})
            except Exception:
                log.exception('Audit store unavailable; request=%s', request_id)
                await error(503, '安全审计服务暂时不可用，请稍后重试。')
                return
            await error(status, detail)

        workspace_header = headers.get('x-workspace-id')
        query_workspace = parse_qs(scope.get('query_string', b'').decode('utf-8', errors='replace')).get('workspace_id', [])
        workspace_values = ([workspace_header] if workspace_header is not None else []) + query_workspace
        if any(value != principal.tenant_id for value in workspace_values):
            try:
                await audit('access_denied', 'denied', {'method': method, 'reason': 'workspace_changed'})
            except Exception:
                await error(503, '安全审计服务暂时不可用，请稍后重试。')
                return
            await JSONResponse({'detail': '工作区已在另一窗口切换，请刷新页面后继续。', 'code': 'workspace_changed', 'request_id': request_id}, status_code=409)(scope, receive, emit)
            return

        tenant_header = headers.get('x-tenant-id')
        if tenant_header is not None and tenant_header != principal.tenant_id:
            await deny(403, '机构选择与当前服务端会话不一致，请使用工作区切换入口。', 'tenant_header_mismatch')
            return
        if method not in SAFE_METHODS:
            csrf = headers.get('x-csrf-token', '')
            if not csrf or len(csrf) > 256 or not hmac.compare_digest(hashlib.sha256(csrf.encode()).hexdigest(), str(csrf_hash)):
                await deny(403, '请求校验已失效，请刷新页面后重试。', 'csrf_failed')
                return
            if path in {'/api/auth/password', '/api/auth/workspace'} and headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
                await error(415, '此账号操作须使用 JSON 请求。')
                return

        body = None
        original_receive = receive
        policy_body = needs_body(method, path)
        skill_body = method not in SAFE_METHODS and (path == '/api/skills' or path.startswith('/api/skills/'))
        if policy_body or skill_body:
            if policy_body and headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
                await error(415, '此操作须使用 JSON 请求。')
                return
            chunks, size = [], 0
            while True:
                message = await original_receive()
                if message['type'] == 'http.disconnect':
                    return
                if message['type'] != 'http.request':
                    continue
                chunk = message.get('body', b'')
                size += len(chunk)
                if size > (MAX_SKILL_BODY if skill_body else MAX_POLICY_BODY):
                    await error(413, '请求正文超过此操作的大小限制。')
                    return
                chunks.append(chunk)
                if not message.get('more_body', False):
                    break
            raw = b''.join(chunks)
            if policy_body:
                try:
                    body = json.loads(raw)
                    if not isinstance(body, dict):
                        raise ValueError('object required')
                except (ValueError, UnicodeDecodeError):
                    await error(400, '请求正文须为有效 JSON 对象。')
                    return
            delivered = False

            async def replay_receive():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {'type': 'http.request', 'body': raw, 'more_body': False}
                return await original_receive()

            receive = replay_receive
        decision = authorize(principal, method, path, body)
        if not decision.allowed:
            await deny(decision.code, decision.reason, 'role_policy')
            return

        # Keep ContextVar scope open through response streaming/background work.
        # asyncio.to_thread and Starlette's thread pool copy this context.
        with use_context(principal), business_readonly(method in SAFE_METHODS):
            mutation = method not in SAFE_METHODS and not is_control_path(path)
            if mutation:
                try:
                    await audit('business_request', 'started', {'method': method})
                except Exception:
                    log.exception('Audit store unavailable before mutation; request=%s', request_id)
                    await error(503, '安全审计服务暂时不可用，操作未执行。')
                    return
            response_status, started = 500, False

            async def track(message):
                nonlocal response_status, started
                if message['type'] == 'http.response.start':
                    response_status, started = message['status'], True
                await emit(message)

            try:
                await self.app(scope, receive, track)
            except Exception:
                if mutation:
                    try:
                        await audit('business_request', 'error', {'method': method, 'status': 500})
                    except Exception:
                        log.exception('Audit completion unavailable; request=%s', request_id)
                if started:
                    raise
                log.exception('Business request failed; request=%s', request_id)
                await error(500, '请求处理失败，请稍后重试。')
                return
            if mutation:
                try:
                    await audit('business_request', 'success' if response_status < 400 else 'rejected', {'method': method, 'status': response_status})
                except Exception:
                    # The append-only started event remains available to reconcile
                    # the outcome; never claim the completed mutation was undone.
                    log.exception('Audit completion unavailable; request=%s', request_id)
