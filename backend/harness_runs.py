"""Single-owner, identity-bound background assistance and public progress streams.

The router used by the multi-worker API only proxies to the existing single
agent service. Prompts/results remain bounded service memory, never queue/audit
records. Leaving a browser page closes its subscription, not the owned task.
"""
from __future__ import annotations

import asyncio
from contextlib import closing, suppress
from contextvars import ContextVar
from datetime import datetime, timezone
import inspect
import re
import time
import uuid
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from .security.context import require_context, use_context
from .security.store import assert_principal_current, assert_job_principal_current
from . import skill_harness as harness

router = APIRouter()
service_router = APIRouter()
_owned_run = ContextVar('ontology_owned_harness_run', default=None)
_public_progress = ContextVar('ontology_harness_public_progress', default=None)
ACTIVE = frozenset({'queued', 'running'})
TTL_SECONDS = 3600
MAX_EVENTS = 256
MAX_RESULT_BYTES = 3 * 1024 * 1024
_REGISTERED = {}
BUSINESS_ERRORS = {
    'version_conflict': '技能内容已被更新，请打开最新内容后继续。',
    'skill_proposal_stale': '技能或参考素材已变化，请重新核对后再生成。',
    'skill_dependency_changed': '关联成果已变化，请重新选择当前有效版本。',
    'skill_tool_dependency_unavailable': '选用的数据来源或访问授权已变化，请重新核对。',
    'skill_forbidden': '当前账号已无权继续此项任务。',
}


def now():
    return datetime.now(timezone.utc).isoformat()


def register_operation(name, handler):
    """Only imported platform code may register an operation, never HTTP input."""
    if not re.fullmatch(r'[a-z][a-z0-9_]{1,63}', name) or name in _REGISTERED:
        raise ValueError('Invalid or repeated assistance operation')
    _REGISTERED[name] = handler


def progress_callback():
    return _public_progress.get()


def background_principal(principal, *, require_write=False):
    """A server-owned active task may retain its captured workspace after UI switch."""
    binding = _owned_run.get()
    if binding is None:
        return None
    store, ident = binding
    run = store.runs.get(ident)
    if not run or run['status'] not in ACTIVE or run['_principal'] != principal or not run.get('_task'):
        raise HTTPException(409, '后台任务已结束或取消。')
    current = assert_job_principal_current(principal, require_write=require_write)
    if (current.readonly, current.visibility) != (principal.readonly, principal.visibility):
        raise HTTPException(403, '后台任务的访问范围已变化，请重新发起。')
    return current


class RunStore:
    def __init__(self, *, maximum=64, owner_maximum=12, ttl=TTL_SECONDS, clock=time.monotonic):
        self.runs = {}
        self.maximum, self.owner_maximum, self.ttl, self.clock = maximum, owner_maximum, ttl, clock
        self.closed = False

    @staticmethod
    def owner(principal):
        return (principal.session_id, principal.user_id, principal.tenant_id, principal.auth_version,
                principal.home_tenant_id, principal.database_name, principal.role, principal.readonly, principal.visibility)

    def prune(self):
        for ident, run in list(self.runs.items()):
            if run['status'] not in ACTIVE and self.clock() - run['_touched'] >= self.ttl:
                run.clear()
                self.runs.pop(ident, None)

    def event(self, run, value):
        if run['status'] not in ACTIVE:
            return
        if not isinstance(value, dict):
            return
        stage = value.get('stage', 'working')
        if stage not in {'queued', 'preparing', 'model', 'tool', 'generating', 'checking', 'working'}:
            stage = 'working'
        # Only platform-owned fixed progress messages enter this stream.
        allowed = {'正在排队，等待智能服务。', '正在准备任务和参考素材。', '正在连接智能服务。',
                   '正在理解业务要求。', '正在查阅参考内容。', '正在构建技能提案。',
                   '正在执行获准的查询或技能。', '正在整理回答。', '正在核对结果与当前权限。',
                   '正在处理任务。'}
        message = value.get('message')
        if message not in allowed:
            message = '正在处理任务。'
        event = {'seq': run['_seq'] + 1, 'id': run['_seq'] + 1, 'type': 'progress', 'kind': 'progress',
                 'stage': stage, 'message': message, 'created_at': now()}
        if (isinstance(value.get('tool'), str)
                and re.fullmatch('[a-z][a-z0-9_]{0,63}', value['tool'])):
            event['tool'] = value['tool']
        run['_seq'] += 1
        run['events'].append(event)
        run['events'] = run['events'][-MAX_EVENTS:]
        run['status'] = 'queued' if stage == 'queued' else 'running'
        run.update(stage=stage, message=message, updated_at=event['created_at'])
        run['_signal'].set()

    @staticmethod
    def summary(run):
        keys = ('id', 'run_id', 'kind', 'title', 'status', 'entity_id', 'href', 'created_at',
                'updated_at', 'stage', 'message', 'expires_at')
        return {key: run[key] for key in keys if key in run}

    def _owned(self, ident, principal):
        self.prune()
        run = self.runs.get(ident)
        if not run or run['_owner'] != self.owner(principal):
            raise HTTPException(404, '未找到当前账号和机构的后台任务。')
        return run

    async def submit(self, principal, *, kind, title, entity_id, operation, guard=None, instruction='', href=None):
        if self.closed:
            raise HTTPException(503, '智能服务正在停止，请稍后重试。')
        if principal.identity_kind != 'session' or principal.must_change_password:
            raise HTTPException(403, '请先完成账号安全检查后再发起智能任务。')
        await asyncio.to_thread(assert_principal_current, principal)
        self.prune()
        owned = [run for run in self.runs.values() if run['_owner'] == self.owner(principal)]
        if len(self.runs) >= self.maximum or len(owned) >= self.owner_maximum:
            # Evict only this owner's completed history before rejecting. Never
            # discard another user's active work or duplicate a model request.
            for run in sorted(owned, key=lambda item: item['_touched']):
                if run['status'] not in ACTIVE:
                    self.runs.pop(run['id'], None)
                    run.clear()
                    break
            else:
                raise HTTPException(429, '后台任务较多，请先等待当前任务完成。')
        if any(run['entity_id'] == entity_id and run['kind'] == kind and run['status'] in ACTIVE
               for run in self.runs.values() if run['_owner'] == self.owner(principal)):
            raise HTTPException(409, '当前内容已有后台任务，请查看运行进度。')
        ident, stamp = uuid.uuid4().hex, now()
        run = {'id': ident, 'run_id': ident, 'kind': kind, 'title': str(title)[:200],
               'entity_id': entity_id, 'status': 'queued', 'href': href or '',
               'created_at': stamp, 'updated_at': stamp, 'events': [], 'stage': 'queued',
               'message': '正在排队，等待智能服务。', 'instruction': instruction,
               'retention': 'temporary_service_memory', 'retention_notice': '离开页面后继续处理；任务及结果临时保留一小时，服务重启后清除。',
               '_owner': self.owner(principal), '_principal': principal, '_guard': guard,
               '_touched': self.clock(), '_seq': 0, '_signal': asyncio.Event()}
        if kind == 'skill_author':
            run['href'] = '/skill-development/' + entity_id + '?run=' + ident
        elif run['href']:
            parts = urlsplit(run['href'])
            if parts.scheme or parts.netloc or not parts.path.startswith('/') or parts.path.startswith('//'):
                raise ValueError('Background result link must be an internal path')
            query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key != 'harness_run']
            run['href'] = urlunsplit(('', '', parts.path, urlencode(query + [('harness_run', ident)]), parts.fragment))
        self.runs[ident] = run
        self.event(run, {'stage': 'queued', 'message': run['message']})
        run['_task'] = asyncio.create_task(self._perform(run, operation))
        return self.summary(run)

    async def _perform(self, run, operation):
        principal = run['_principal']
        token = _owned_run.set((self, run['id']))
        emitter = _public_progress.set(lambda value: self.event(run, value))
        try:
            with use_context(principal):
                await asyncio.to_thread(background_principal, principal, require_write=False)
                run['status'] = 'running'
                self.event(run, {'stage': 'preparing', 'message': '正在准备任务和参考素材。'})
                result = await operation()
                self.event(run, {'stage': 'checking', 'message': '正在核对结果与当前权限。'})
                await asyncio.to_thread(background_principal, principal, require_write=False)
                if run['_guard']:
                    check = run['_guard']()
                    if inspect.isawaitable(check):
                        await check
                result = jsonable_encoder(result)
                if len(harness._json(result)) > MAX_RESULT_BYTES:
                    raise harness._error('HARNESS_LIMIT')
                run['result'] = result
                run.update(status='completed', stage='completed', message='任务已完成，可以查看结果。')
        except asyncio.CancelledError:
            run.pop('result', None)
            run.update(status='cancelled', stage='cancelled', message='任务已取消。')
        except BaseException as exc:
            code = exc.detail.get('code') if isinstance(exc, HTTPException) and isinstance(exc.detail, dict) else None
            code = code if code in harness.SAFE_ERRORS or code in BUSINESS_ERRORS else 'HARNESS_FAILED'
            message = BUSINESS_ERRORS.get(code) or harness.SAFE_ERRORS[code][1]
            run.pop('result', None)
            run.update(status='failed', stage='failed', message=message,
                       error={'code': code, 'message': message})
        finally:
            _public_progress.reset(emitter)
            _owned_run.reset(token)
            run['_touched'] = self.clock()
            run['updated_at'] = now()
            run['expires_at'] = datetime.fromtimestamp(time.time() + self.ttl, timezone.utc).isoformat()
            run['_seq'] += 1
            run['events'].append({'seq': run['_seq'], 'id': run['_seq'], 'type': run['status'],
                                  'kind': run['status'], 'stage': run['stage'], 'message': run['message'],
                                  'created_at': run['updated_at']})
            run['events'] = run['events'][-MAX_EVENTS:]
            run['_signal'].set()

    async def detail(self, ident, principal):
        await asyncio.to_thread(assert_principal_current, principal)
        run = self._owned(ident, principal)
        # Every result read repeats the business/source-version guard. A cached
        # completed proposal never bypasses later withdrawal or content changes.
        if run.get('result') is not None and run.get('_guard'):
            with use_context(principal):
                checked = run['_guard']()
                if inspect.isawaitable(checked):
                    await checked
        return self.summary(run) | {key: run[key] for key in ('events', 'result', 'error', 'instruction', 'retention', 'retention_notice',
                                                            'parent_run_id', 'reference_entity_ids', 'reference_material_ids') if key in run}

    async def list(self, principal, *, kind='', entity_id='', limit=20):
        await asyncio.to_thread(assert_principal_current, principal)
        self.prune()
        items = [self.summary(run) for run in self.runs.values()
                 if run['_owner'] == self.owner(principal) and (not kind or run['kind'] == kind)
                 and (not entity_id or run['entity_id'] == entity_id)]
        return {'items': sorted(items, key=lambda item: item['created_at'], reverse=True)[:limit],
                'retention': 'temporary_service_memory', 'retention_seconds': self.ttl}

    async def cancel(self, ident, principal):
        await asyncio.to_thread(assert_principal_current, principal)
        run = self._owned(ident, principal)
        if run['status'] in ACTIVE:
            run['_task'].cancel()
            with suppress(asyncio.CancelledError):
                await run['_task']
            # Cancellation may arrive before asyncio starts the coroutine, in
            # which case its finally block has never owned any work to clean.
            if run['status'] in ACTIVE:
                run.update(status='cancelled', stage='cancelled', message='任务已取消。', updated_at=now())
                run['_touched'] = self.clock()
                run['_seq'] += 1
                run['events'].append({'seq': run['_seq'], 'id': run['_seq'], 'type': 'cancelled', 'kind': 'cancelled',
                                      'stage': 'cancelled', 'message': run['message'], 'created_at': run['updated_at']})
                run['_signal'].set()
        return await self.detail(ident, principal)

    async def close(self):
        self.closed = True
        tasks = [run['_task'] for run in self.runs.values() if run['status'] in ACTIVE]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for run in self.runs.values():
            run.clear()
        self.runs.clear()


RUNS = RunStore()


async def launch_registered(principal, name, entity_id, body):
    handler = _REGISTERED.get(name)
    if handler is None:
        raise HTTPException(422, '不支持这项智能任务。')
    spec = await handler(principal, entity_id, body)
    if not isinstance(spec, dict) or not callable(spec.get('operation')):
        raise HTTPException(500, '智能任务未正确准备。')
    return await RUNS.submit(principal, kind=name, title=spec['title'], entity_id=spec.get('entity_id', entity_id),
                             operation=spec['operation'], guard=spec.get('guard'), href=spec.get('href', ''),
                             instruction=spec.get('instruction', ''))


class TaskBody(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    operation: str = Field(min_length=1, max_length=64)
    entity_id: str = Field(default='', max_length=191)
    body: dict = Field(default_factory=dict)


@service_router.post('/harness/tasks', status_code=202)
async def submit_registered(request: Request):
    from .skills import _body
    body = await _body(request, TaskBody)
    return await launch_registered(require_context(), body.operation, body.entity_id, body.body)


class AuthorBody(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    expected_version: int = Field(ge=1)
    instruction: str = Field(min_length=1, max_length=8000)
    reference_entity_ids: list[str] = Field(default_factory=list, max_length=24)
    reference_material_ids: list[str] = Field(default_factory=list, max_length=8)
    parent_run_id: str = Field(default='', max_length=32, pattern=r'^(?:[a-f0-9]{32})?$')


def _references(conn, body):
    from .mcp_tools import _visible
    from .provenance import get_active_chunks
    values, fingerprints = [], []
    identifiers = list(dict.fromkeys(body.reference_entity_ids + body.reference_material_ids))
    if any(not isinstance(ident, str) or not 1 <= len(ident) <= 191 for ident in identifiers):
        raise HTTPException(422, '参考素材选择无效。')
    used = 0
    for ident in identifiers:
        condition = "e.kind='material' AND e.status!='archived' AND COALESCE(json_extract(e.payload,'$.active_asset'),1)!=0" if ident in body.reference_material_ids else _visible()
        row = conn.execute('SELECT * FROM entities e WHERE id=? AND ' + condition, (ident,)).fetchone()
        if not row:
            raise HTTPException(404, '参考素材已不可用，请重新选择。')
        item = dict(row)
        if ident in body.reference_material_ids and item['kind'] != 'material':
            raise HTTPException(422, '请选择已上传的参考材料。')
        payload = harness._loads(item.get('payload') or '{}')
        reference = {'entity_id': ident, 'title': item['title'], 'kind': item['kind'], 'version': item['version'],
                     'summary': item.get('summary') or '', 'untrusted_reference': True}
        if item['kind'] == 'material':
            chunks = get_active_chunks(conn, ident)
            source = conn.execute('SELECT id,version,sha256,text FROM source_versions WHERE material_id=? ORDER BY version DESC LIMIT 1', (ident,)).fetchone()
            text = source['text'] if source else payload.get('original_text') or '\n'.join(str(chunk.get('content') or '') for chunk in chunks)
            reference['text'] = str(text)[:24000]
            reference['truncated'] = len(str(text)) > 24000
            if source:
                reference.update(source_version=source['id'], source_version_number=source['version'], source_sha256=source['sha256'])
            reference['chunks'] = [{'id': chunk['id'], 'heading': chunk.get('heading') or '',
                                    'content': str(chunk.get('content') or '')[:6000]} for chunk in chunks[:8]]
        else:
            # Asset contents are locally owned data, never source credentials or
            # provider configuration. Select explicit business fields only.
            reference['content'] = {key: payload[key] for key in ('content', 'text', 'description', 'statement', 'steps', 'fields', 'columns', 'parameters', 'conditions', 'applicability') if key in payload}
        encoded = harness._json(reference)
        used += len(encoded)
        if used > 80000:
            raise HTTPException(413, '参考素材较多，请缩小本次选用范围。')
        values.append(reference)
        fingerprints.append((ident, item['version'], harness.hashlib.sha256(encoded).hexdigest()))
    return values, fingerprints


def _prepare_author(ident, body):
    from . import skills
    principal = skills._role(skills.BUILD)
    with closing(skills._connect()) as conn:
        entity, document = skills._document(conn, ident)
        skills._expected(entity, body.expected_version)
        _, metadata = skills._dependency_state(conn, document['manifest'])
        references, fingerprints = _references(conn, body)
        metadata = {'dependencies': metadata, 'reference_materials': references}
    return principal, entity, document, metadata, fingerprints


def _author_guard(ident, body, digest, fingerprints):
    from . import skills
    from .jobs import assert_execution_principal_current
    assert_execution_principal_current(require_context(), require_write=True)
    with closing(skills._connect()) as conn:
        entity, document = skills._document(conn, ident)
        skills._expected(entity, body.expected_version)
        if document['digest'] != digest:
            skills._fail('skill_proposal_stale', '生成期间技能已变化，请重新加载并生成。', 409)
        skills._dependency_state(conn, document['manifest'])
        if _references(conn, body)[1] != fingerprints:
            skills._fail('skill_proposal_stale', '参考素材已变化，请重新选择后生成。', 409)


@service_router.post('/skills/{ident}/assist-runs', status_code=202)
async def submit_author(ident: str, request: Request):
    from . import skills
    body = await skills._body(request, AuthorBody)
    principal = await asyncio.to_thread(skills._role, skills.BUILD)
    parent = None
    if body.parent_run_id:
        parent_view = await RUNS.detail(body.parent_run_id, principal)
        parent = RUNS._owned(body.parent_run_id, principal)
        if (parent_view['kind'] != 'skill_author' or parent_view['entity_id'] != ident
                or parent_view['status'] != 'completed' or not isinstance(parent_view.get('result'), dict)):
            raise HTTPException(409, '上一轮方案不可用于本次修改，请重新载入。')
        asset_ids = list(dict.fromkeys(parent.get('reference_entity_ids', []) + body.reference_entity_ids))
        material_ids = list(dict.fromkeys(parent.get('reference_material_ids', []) + body.reference_material_ids))
        if len(asset_ids) > 24 or len(material_ids) > 8:
            raise HTTPException(413, '连续对话选用的参考素材较多，请先保存当前方案后再继续。')
        body = body.model_copy(update={'reference_entity_ids': asset_ids, 'reference_material_ids': material_ids})
    principal, entity, document, metadata, fingerprints = await asyncio.to_thread(_prepare_author, ident, body)
    input_files, input_manifest = document['files'], document['manifest']
    if parent is not None:
        result = parent['result']
        if result['base_version'] != entity['version'] or result['base_digest'] != document['digest']:
            raise HTTPException(409, '技能已保存或发生变化，请基于当前版本重新开始。')
        current_proofs = {proof[0]: proof for proof in fingerprints}
        if any(current_proofs.get(proof[0]) != proof for proof in parent.get('_author_fingerprints', [])):
            raise HTTPException(409, '上一轮参考素材已变化，请重新核对后开始。')
        input_files, input_manifest = result['files'], result['manifest']
        metadata['previous_turn'] = {'instruction': parent.get('instruction', '')[:8000], 'summary': str(result.get('summary', ''))[:2000]}
    guard = lambda: asyncio.to_thread(_author_guard, ident, body, document['digest'], fingerprints)

    async def operation():
        proposal = await harness.propose_skill(principal, body.instruction, input_files, input_manifest, metadata, uuid.uuid4().hex)
        try:
            manifest = skills.Manifest.model_validate(proposal['manifest'], strict=True).model_dump(mode='python')
            manifest['dependencies'] = document['manifest']['dependencies']
            manifest['tool_dependencies'] = document['manifest']['tool_dependencies']
            files, _ = skills._coherent_files(proposal['files'], manifest)
        except (KeyError, ValueError, TypeError):
            raise harness._error('HARNESS_INCOMPLETE') from None
        await guard()
        await asyncio.to_thread(skills._audit, 'assist', ident, revision=document.get('current_revision', 0), digest=document['digest'])
        return {'request_id': principal.request_id or uuid.uuid4().hex, 'base_version': entity['version'],
                'base_digest': document['digest'], 'summary': proposal['summary'], 'files': files,
                'manifest': manifest, 'validation': skills._validation(files, manifest), 'retention': 'temporary_service_memory'}

    summary = await RUNS.submit(principal, kind='skill_author', title=entity['title'], entity_id=ident,
                                instruction=body.instruction, operation=operation, guard=guard)
    run = RUNS._owned(summary['id'], principal)
    run.update(parent_run_id=body.parent_run_id, reference_entity_ids=body.reference_entity_ids,
               reference_material_ids=body.reference_material_ids, _author_fingerprints=fingerprints)
    return summary


@service_router.get('/harness/runs')
async def list_runs(kind: str = '', entity_id: str = '', limit: int = Query(20, ge=1, le=64)):
    principal = require_context()
    result = await RUNS.list(principal, kind=kind, entity_id=entity_id, limit=limit)
    from .jobs import harness_queue_summaries
    queued = await asyncio.to_thread(harness_queue_summaries, principal, limit)
    result['items'] += [item for item in queued if (not kind or item['kind'] == kind) and (not entity_id or item['entity_id'] == entity_id)]
    result['items'] = sorted(result['items'], key=lambda item: item.get('updated_at') or item.get('created_at') or '', reverse=True)[:limit]
    return result


@service_router.get('/harness/runs/{ident}')
async def run_detail(ident: str):
    return await RUNS.detail(ident, require_context())


@service_router.post('/harness/runs/{ident}/cancel')
async def cancel_run(ident: str):
    return await RUNS.cancel(ident, require_context())


@service_router.get('/harness/runs/{ident}/events')
async def run_events(ident: str, request: Request, after: int = Query(0, ge=0)):
    principal = require_context()
    await RUNS.detail(ident, principal)

    async def stream():
        cursor = after
        while True:
            if await request.is_disconnected():
                return
            try:
                detail = await RUNS.detail(ident, principal)
                for event in detail['events']:
                    if event['seq'] > cursor:
                        cursor = event['seq']
                        yield 'id: ' + str(cursor) + '\nevent: progress\ndata: ' + harness._json(event).decode() + '\n\n'
                yield 'event: state\ndata: ' + harness._json(detail).decode() + '\n\n'
                if detail['status'] not in ACTIVE:
                    return
                run = RUNS._owned(ident, principal)
                run['_signal'].clear()
                try:
                    await asyncio.wait_for(run['_signal'].wait(), 10)
                except TimeoutError:
                    yield ': heartbeat\n\n'
            except HTTPException:
                yield 'event: unavailable\ndata: {"message":"任务或授权已变化，请重新加载。"}\n\n'
                return
    return StreamingResponse(stream(), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})


@router.api_route('/harness/runs{tail:path}', methods=['GET', 'POST'])
@router.post('/harness/tasks')
@router.post('/skills/{ident}/assist-runs')
async def public_proxy(request: Request, tail: str = '', ident: str = ''):
    """A fixed authenticated bridge to the one owner, including streaming IO."""
    from .agent_proxy import endpoint
    path = request.url.path.removeprefix('/api')
    method = request.method
    valid = (method == 'POST' and path == '/harness/tasks') or (method == 'POST' and re.fullmatch(r'/skills/[^/]{1,191}/assist-runs', path)) or (
        method == 'GET' and re.fullmatch(r'/harness/runs(?:/[a-f0-9]{32}(?:/events)?)?', path)) or (
        method == 'POST' and re.fullmatch(r'/harness/runs/[a-f0-9]{32}/cancel', path))
    if not valid or any(value in request.scope.get('raw_path', b'') for value in (b'%', b'\\', b'..')):
        raise HTTPException(404, '后台任务接口不存在。')
    allowed = {'after'} if path.endswith('/events') else {'kind', 'entity_id', 'limit'} if path == '/harness/runs' else set()
    if any(key not in allowed for key in request.query_params):
        raise HTTPException(422, '后台任务查询参数无效。')
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > harness.MAX_REQUEST_BYTES:
            raise HTTPException(413, '任务要求较长，请缩小本次范围。')
    headers = {key: request.headers[key] for key in ('cookie', 'x-csrf-token', 'x-workspace-id', 'x-tenant-id', 'origin', 'content-type') if key in request.headers}
    url = endpoint() + '/api' + path
    client = httpx.AsyncClient(trust_env=False, timeout=httpx.Timeout(20, connect=2), follow_redirects=False)
    try:
        outgoing = client.build_request(method, url, params=request.query_params, headers=headers, content=bytes(raw))
        response = await client.send(outgoing, stream=True)
        media = response.headers.get('content-type', '').split(';', 1)[0]
        if media == 'text/event-stream' and path.endswith('/events') and response.status_code == 200:
            async def relay():
                try:
                    async for chunk in response.aiter_bytes():
                        yield chunk
                finally:
                    await response.aclose()
                    await client.aclose()
            return StreamingResponse(relay(), media_type='text/event-stream',
                                     headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})
        if media != 'application/json':
            raise HTTPException(503, '智能服务暂不可用。')
        data = bytearray()
        async for chunk in response.aiter_bytes():
            data.extend(chunk)
            if len(data) > MAX_RESULT_BYTES + 262144:
                raise HTTPException(502, '智能服务返回内容过大。')
        await response.aclose()
        await client.aclose()
        return JSONResponse(harness._loads(data), status_code=response.status_code, headers={'Cache-Control': 'no-store'})
    except httpx.HTTPError:
        await client.aclose()
        raise HTTPException(503, '智能服务连接暂不可用。') from None
    except BaseException:
        await client.aclose()
        raise
