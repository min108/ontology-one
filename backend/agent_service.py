"""Internal single-worker volatile agent service, protected by real session auth."""
from contextlib import asynccontextmanager, closing
import asyncio
import json
import time
from datetime import datetime, timezone
from fastapi import FastAPI, APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from .security.context import require_context
from .security.middleware import SecurityMiddleware
from .agent_tasks import current
from .agent_runtime import SESSIONS, TTL
from . import skill_harness as harness
from .agent_tools import definitions
from .db import connect, business_readonly
from .version import APP_VERSION


@asynccontextmanager
async def _runtime_lifespan(app):
    from .legacy_harness_runs import register_operations
    register_operations()
    async def sweep():
        while True:
            await asyncio.sleep(30)
            SESSIONS._prune()
    sweeper = asyncio.create_task(sweep())
    try:
        yield
    finally:
        sweeper.cancel()
        await asyncio.gather(sweeper, return_exceptions=True)
        from .harness_runs import RUNS
        await RUNS.close()
        await SESSIONS.close()
        await harness.EXECUTOR.close()
        from .agent_tool_executor import EXECUTOR
        await EXECUTOR.close()


@asynccontextmanager
async def lifespan(app):
    from .maintenance import runtime_guard
    with runtime_guard(shared=True):
        async with _runtime_lifespan(app):
            yield


app = FastAPI(title='Ontology Agent', docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.add_middleware(SecurityMiddleware)
router = APIRouter(prefix='/api/agent')


@app.get('/internal/health')
def health():
    return {'ready': not SESSIONS.closed, 'service': 'ontology-agent', 'version': APP_VERSION}


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Create(Strict):
    title: str = Field(default='新会话', min_length=1, max_length=160)


class Turn(Strict):
    message: str = Field(min_length=1, max_length=12000)
    skill_ids: list[str] = Field(default_factory=list, max_length=16)
    mapping_ids: list[str] = Field(default_factory=list, max_length=20)
    entity_ids: list[str] = Field(default_factory=list, max_length=50)
    scope_mode: Literal['selected', 'auto'] = 'selected'


async def body(request, model):
    if request.headers.get('content-type', '').split(';', 1)[0].lower() != 'application/json':
        raise HTTPException(415, '请使用 JSON 请求。')
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 64 * 1024: raise HTTPException(413, '请求超过限制。')
    try:
        value = model.model_validate(harness._loads(raw), strict=True)
        for field in ('skill_ids', 'mapping_ids', 'entity_ids'):
            if hasattr(value, field) and any(not isinstance(i, str) or not 1 <= len(i) <= 191 for i in getattr(value, field)):
                raise ValueError()
        return value
    except Exception:
        raise HTTPException(422, '智能体请求格式无效。') from None


def principal():
    return current(require_context())


def _options():
    p = principal()
    from . import sources, skills as skill_documents
    assets, skills, mappings = [], [], []
    with business_readonly(True), closing(connect()) as conn:
        rows = conn.execute("SELECT id,title,kind,version FROM entities e WHERE " + __import__('backend.mcp_tools', fromlist=['_visible'])._visible() + ' ORDER BY title LIMIT 100').fetchall()
        assets = [dict(r) for r in rows]
        skill_rows = conn.execute("SELECT e.id,e.title,e.kind,e.version FROM entities e WHERE e.kind='skill' AND " +
                                  __import__('backend.mcp_tools', fromlist=['_visible'])._visible() + ' ORDER BY e.title LIMIT 101').fetchall()
        for row in skill_rows:
            try:
                entity, document = skill_documents._document(conn, row['id'])
                skill_documents._validate_current(conn, entity, document, proof=True)
            except HTTPException:
                continue
            skills.append(dict(row))
        if not p.readonly:
            for row in conn.execute("SELECT * FROM source_mappings WHERE status='published' ORDER BY title LIMIT 101").fetchall():
                try:
                    source = sources._authorize(conn, row['source_id'], 'query')
                    sources._validate_mapping_current(conn, source, sources._mapping(row))
                except HTTPException:
                    continue
                mappings.append({key: row[key] for key in ('id', 'source_id', 'title', 'version')} | {'source_title': source['title']})
    return {'assets': assets, 'skills': skills, 'mappings': mappings, 'asset_selection': 'context_focus_not_permission_grant'}


@router.get('/capabilities')
async def capabilities():
    p = require_context()
    if p.tenant_id != p.home_tenant_id:
        return {'available': False, 'configured': False, 'verified': False, 'model': '', 'requires_home_workspace': True,
                'reason': '请切回所属机构启动智能体，可通过集团授权查阅其他机构成果。', 'tools': [],
                'limits': {'session_ttl_seconds': TTL}, 'retention': {'session': 'memory_only'}}
    p = principal()
    status = await asyncio.to_thread(harness.harness_status)
    return {'available': status.get('available', status.get('runtime_available', False)), 'configured': status['configured'],
            'verified': status.get('verified', False), 'model': status['model'],
            'limits': {'session_ttl_seconds': TTL, 'max_sessions_per_login': 8, 'max_turns': 10, 'turn_timeout_seconds': 90,
                       'max_model_steps': 8, 'max_tool_calls': 16}, 'retention': {'session': 'memory_only', 'source_rows': 'memory_only', 'audit': 'metadata_only'},
            'tools': definitions([], []), 'scope': 'current_workspace_published_ontology_and_explicit_sources'}


@router.get('/options')
async def options():
    return await asyncio.to_thread(_options)


@router.get('/sessions')
async def sessions():
    p = principal()
    SESSIONS._prune()
    owner = SESSIONS._owner(p)
    return {'items': [SESSIONS.summary(s) for s in SESSIONS.sessions.values() if s['owner'] == owner]}


@router.get('/runs')
async def runs():
    p = require_context()
    if p.tenant_id != p.home_tenant_id:
        from .security.store import assert_principal_current
        await asyncio.to_thread(assert_principal_current, p)
        return {'items': []}
    return {'items': SESSIONS.runs(principal())}


@router.post('/sessions')
async def create_session(request: Request):
    p = principal()
    value = await body(request, Create)
    return SESSIONS.detail(SESSIONS.create(p, value.title))


@router.get('/sessions/{ident}')
async def get_session(ident: str):
    session = SESSIONS.get(principal(), ident)
    await SESSIONS.validate(session)
    if SESSIONS.get(principal(), ident) is not session:
        raise HTTPException(404, '会话已结束。')
    return SESSIONS.detail(session)


@router.post('/sessions/{ident}/turns')
async def turn(ident: str, request: Request):
    p = principal(); session = SESSIONS.get(p, ident)
    value = await body(request, Turn)
    if not value.message.strip(): raise HTTPException(422, '请填写问题。')
    resource_catalog = None
    if value.scope_mode == 'auto':
        available = await asyncio.to_thread(_options)
        if len(available['skills']) > 16 or len(available['mappings']) > 20:
            raise HTTPException(422, '可用资料较多，请展开参考范围，选用与本次问题相关的技能或数据来源。')
        value.skill_ids = [item['id'] for item in available['skills']]
        value.mapping_ids = [item['id'] for item in available['mappings']]
        resource_catalog = {'skills': available['skills'], 'data_sources': available['mappings']}
    return await SESSIONS.start(p, session, value.message, value.skill_ids, value.mapping_ids, value.entity_ids,
                                resource_catalog=resource_catalog)


@router.get('/sessions/{ident}/runs/{run_id}')
async def run(ident: str, run_id: str):
    session = SESSIONS.get(principal(), ident)
    await SESSIONS.validate(session)
    if SESSIONS.get(principal(), ident) is not session:
        raise HTTPException(404, '会话已结束。')
    value = session['runs'].get(run_id)
    if value is None: raise HTTPException(404, '未找到本会话的运行记录。')
    return SESSIONS.run_view(value)


@router.get('/sessions/{ident}/runs/{run_id}/events')
async def events(ident: str, run_id: str, request: Request):
    p = principal()
    session = SESSIONS.get(p, ident)
    await SESSIONS.validate(session)
    if run_id not in session['runs']:
        raise HTTPException(404, '未找到本会话的运行记录。')

    async def stream():
        previous, heartbeat = None, time.monotonic()
        try:
            while True:
                if await request.is_disconnected():
                    return  # Closing a viewer never cancels its background task.
                try:
                    await asyncio.to_thread(current, p)
                    live = SESSIONS.get(p, ident)
                    await SESSIONS.validate(live)
                    await asyncio.to_thread(current, p)
                    if SESSIONS.sessions.get(ident) is not live:
                        raise HTTPException(404, '会话已结束。')
                    value = live['runs'].get(run_id)
                    if value is None:
                        raise HTTPException(404, '运行已清除。')
                    snapshot = json.dumps(SESSIONS.run_view(value), ensure_ascii=False, separators=(',', ':'))
                except Exception:
                    yield 'event: reset\ndata: {"message":"会话、来源或访问范围已变化，请重新打开。"}\n\n'
                    return
                if snapshot != previous:
                    previous, heartbeat = snapshot, time.monotonic()
                    yield 'event: state\ndata: ' + snapshot + '\n\n'
                elif time.monotonic() - heartbeat >= 10:
                    heartbeat = time.monotonic()
                    yield ': keepalive\n\n'
                if value['status'] != 'running':
                    return
                await asyncio.sleep(.5)
        except asyncio.CancelledError:
            return

    return StreamingResponse(stream(), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})


@router.post('/sessions/{ident}/runs/{run_id}/cancel')
async def cancel(ident: str, run_id: str):
    session = SESSIONS.get(principal(), ident)
    value = session['runs'].get(run_id)
    if value is None: raise HTTPException(404, '未找到本会话的运行记录。')
    return await SESSIONS.cancel(session, value)


@router.delete('/sessions/{ident}')
async def delete(ident: str):
    session = SESSIONS.get(principal(), ident)
    SESSIONS.sessions.pop(ident, None)
    for value in list(session['runs'].values()):
        await SESSIONS.cancel(session, value)
    return {'deleted': True}


app.include_router(router)
from .harness_runs import service_router
app.include_router(service_router, prefix='/api')
