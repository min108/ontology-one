"""Fixed internal agent-service proxy. Identity remains the real user session."""
import os
import re
from urllib.parse import urlsplit
import httpx
from fastapi import APIRouter, HTTPException, Request, Response

router = APIRouter(prefix='/agent')
MAX_BYTES = 2 * 1024 * 1024


def endpoint():
    value = os.getenv('ONTOLOGY_AGENT_SERVICE_URL', '').strip()
    try:
        parsed = urlsplit(value)
        allowed = {'127.0.0.1', '::1'}
        if os.getenv('ONTOLOGY_AGENT_ALLOW_DOCKER_SERVICE') == '1': allowed.add('agent')
        if (parsed.scheme != 'http' or parsed.hostname not in allowed or parsed.username or parsed.password or
                parsed.query or parsed.fragment or parsed.path not in ('', '/') or not parsed.port or '\\' in value):
            raise ValueError()
        return value.rstrip('/')
    except Exception:
        raise HTTPException(503, '受控智能体服务未启动，请检查平台运行环境。') from None


@router.api_route('/{path:path}', methods=['GET', 'POST', 'DELETE'])
async def proxy(path: str, request: Request):
    rules = {
        'GET': (r'capabilities', r'options', r'sessions', r'sessions/[a-f0-9]{32}', r'sessions/[a-f0-9]{32}/runs/[a-f0-9]{32}'),
        'POST': (r'sessions', r'sessions/[a-f0-9]{32}/turns', r'sessions/[a-f0-9]{32}/runs/[a-f0-9]{32}/cancel'),
        'DELETE': (r'sessions/[a-f0-9]{32}',),
    }
    raw_path = request.scope.get('raw_path', b'')
    if (any(value in raw_path for value in (b'%', b'\\', b'.')) or request.url.query or
            not any(re.fullmatch(rule, path) for rule in rules.get(request.method, ()))):
        raise HTTPException(404, '智能体接口不存在。')
    target = endpoint() + '/api/agent/' + path
    headers = {name: request.headers[name] for name in ('cookie', 'x-csrf-token', 'x-workspace-id', 'x-tenant-id', 'origin', 'content-type') if name in request.headers}
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 64 * 1024: raise HTTPException(413, '智能体请求超过限制。')
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=httpx.Timeout(30, connect=2), follow_redirects=False) as client:
            async with client.stream(request.method, target, headers=headers, content=bytes(raw)) as result:
                data = bytearray()
                async for chunk in result.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_BYTES: raise HTTPException(502, '智能体服务响应超过限制。')
                if result.headers.get('content-type', '').split(';', 1)[0] != 'application/json':
                    raise HTTPException(503, '智能体服务暂不可用。')
                return Response(bytes(data), status_code=result.status_code, media_type='application/json', headers={'Cache-Control': 'no-store'})
    except httpx.HTTPError:
        raise HTTPException(503, '智能体服务连接暂不可用。') from None
