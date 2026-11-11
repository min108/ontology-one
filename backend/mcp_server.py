"""Authenticated, stateless official MCP transport with disposable workers."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from contextvars import ContextVar
from dataclasses import asdict
import json
import logging
import os
from pathlib import Path
import signal
import sys
import uuid

import anyio
from fastapi import HTTPException
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse

from .version import APP_VERSION
from .mcp_contracts import (MAX_RESULT_BYTES, TOOL_DESCRIPTIONS, TOOL_MODELS,
                            ToolEnvelope, ToolFailure, bounded_envelope,
                            parse_tool, safe_failure)
from .security.context import use_context

MAX_BODY_BYTES = 128 * 1024
MAX_STDERR_BYTES = 16 * 1024
MAX_INPUT_BYTES = 160 * 1024
_ROOT = Path(__file__).resolve().parent.parent
_TRANSPORT_LOG_SCOPE = ContextVar('mcp_transport_log_scope', default=False)
_previous_factory = logging.getLogRecordFactory()


def _safe_log_record(*args, **kwargs):
    record = _previous_factory(*args, **kwargs)
    if _TRANSPORT_LOG_SCOPE.get() and record.name.startswith(('mcp', 'httpx', 'httpcore')):
        record.msg, record.args = 'MCP transport event (details suppressed)', ()
        record.exc_info = record.exc_text = record.stack_info = None
    return record


logging.setLogRecordFactory(_safe_log_record)


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()


def _loads(raw):
    def invalid(_):
        raise ValueError('Invalid JSON scalar')
    return json.loads(raw, parse_constant=invalid)


class ProcessExecutor:
    """No waiting queue; a slot belongs to a call until every child is reaped."""

    def __init__(self, max_concurrent=None):
        self.maximum = int(max_concurrent if max_concurrent is not None else os.getenv('ONTOLOGY_MCP_MAX_CONCURRENT', '4'))
        if not 1 <= self.maximum <= 16:
            raise ValueError('MCP concurrency must be between 1 and 16')
        self.active = 0
        self.children = set()
        self.calls = set()
        self.closing = False

    async def _spawn(self):
        environment = dict(os.environ)
        environment['PYTHONDONTWRITEBYTECODE'] = '1'
        environment['PYTHONUNBUFFERED'] = '1'
        return await self._spawn_command([sys.executable, '-X', 'utf8', '-m', 'backend.mcp_worker'], environment)

    async def _spawn_command(self, command, environment=None):
        if os.name == 'nt':
            from .windows_async_process import async_spawn
            from .windows_process import ProcessLimits
            memory_mb = max(256, min(1536, int(os.getenv('ONTOLOGY_MCP_WORKER_MEMORY_MB', '768'))))
            return await async_spawn(
                command, cwd=str(_ROOT), env=environment or dict(os.environ),
                limits=ProcessLimits(memory_bytes=memory_mb * 1024 * 1024,
                                     active_processes=4, cpu_time_seconds=15),
                max_stdout=MAX_RESULT_BYTES + 2048, max_stderr=MAX_STDERR_BYTES,
                max_line_bytes=MAX_RESULT_BYTES + 1, max_stdin=MAX_INPUT_BYTES + 64,
            )
        return await asyncio.create_subprocess_exec(
            *command, cwd=str(_ROOT), env=environment,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, start_new_session=True,
            limit=MAX_RESULT_BYTES + 4096,
        )

    async def _reap(self, process):
        if os.name == 'nt':
            # The owner kills the entire Job and joins every pipe reader/writer
            # before releasing the call's slot, including after normal exit.
            await process.aclose()
            self.children.discard(process)
            return

        async def discard(stream):
            while await stream.read(65536):
                pass

        drainers = [asyncio.create_task(discard(stream)) for stream in (process.stdout, process.stderr)]
        if process.returncode is None:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(process.wait(), .25)
            except asyncio.TimeoutError:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                await process.wait()
        else:
            await process.wait()
        # A future handler must not leave descendants behind after its leader.
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        await asyncio.gather(*drainers, return_exceptions=True)
        if process.stdin:
            process.stdin.close()
        self.children.discard(process)

    async def _stderr(self, stream):
        seen = 0
        while chunk := await stream.read(4096):
            seen += len(chunk)
            if seen > MAX_STDERR_BYTES:
                raise ToolFailure('RESULT_LIMIT', '工具输出超过允许范围。')

    async def _exchange(self, process, payload, finalize):
        async def frame():
            try:
                raw = await process.stdout.readline()
            except ValueError:
                raise ToolFailure('RESULT_LIMIT', '工具输出超过允许范围。') from None
            if len(raw) > MAX_RESULT_BYTES + 1 or not raw.endswith(b'\n'):
                raise ToolFailure('RESULT_LIMIT', '工具输出超过允许范围。')
            return _loads(raw)

        wire = _json(payload)
        if len(wire) > MAX_INPUT_BYTES:
            raise ToolFailure('INVALID_ARGUMENT', '工具参数超过允许范围。')
        process.stdin.write(wire + b'\n')
        await process.stdin.drain()
        value = await frame()
        if finalize and value == {'ready': True, 'request_id': payload['request_id']}:
            process.stdin.write(b'{"finalize":true}\n')
            await process.stdin.drain()
            value = await frame()
        process.stdin.close()
        # Extra stdout is never accepted, including after an otherwise valid result.
        if await process.stdout.read(1):
            raise ToolFailure('RESULT_LIMIT', '工具返回格式不符合约定。')
        await process.wait()
        if process.returncode != 0:
            raise ToolFailure('INTERNAL_ERROR', '工具执行暂不可用。')
        return value

    async def _run_process(self, payload, deadline, *, finalize=True):
        process = None
        spawn = asyncio.create_task(self._spawn())
        tasks = []
        try:
            async with asyncio.timeout_at(deadline):
                process = await asyncio.shield(spawn)
                self.children.add(process)
                if self.closing:
                    raise asyncio.CancelledError()
                exchange = asyncio.create_task(self._exchange(process, payload, finalize))
                stderr = asyncio.create_task(self._stderr(process.stderr))
                tasks = [exchange, stderr]
                results = await asyncio.gather(*tasks)
                return results[0]
        finally:
            # AnyIO cancellation is level-triggered: shield cleanup itself,
            # not just an orphanable task returned by asyncio.shield().
            with anyio.CancelScope(shield=True):
                if process is None:
                    with suppress(Exception):
                        process = await spawn
                        self.children.add(process)
                for task in tasks:
                    task.cancel()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                if process is not None:
                    await self._reap(process)

    async def _audit_failure(self, payload, outcome):
        event = {**payload, 'mode': 'audit', 'arguments': {'outcome': outcome}}
        with anyio.CancelScope(shield=True):
            with suppress(Exception):
                await self._run_process(event, asyncio.get_running_loop().time() + 2, finalize=False)

    async def execute(self, principal, tool, arguments, request_id, *, deadline):
        if self.closing or self.active >= self.maximum:
            raise ToolFailure('BUSY', '工具服务正在处理其他请求，请稍后重试。', True)
        self.active += 1
        call = asyncio.current_task()
        self.calls.add(call)
        payload = {'principal': asdict(principal), 'tool': tool, 'arguments': arguments,
                   'request_id': request_id, 'mode': 'execute'}
        try:
            result = await self._run_process(payload, deadline)
            envelope = bounded_envelope(ToolEnvelope.model_validate(result))
            if envelope.request_id != request_id:
                raise ToolFailure('INTERNAL_ERROR', '工具返回格式不符合约定。')
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError()
            return envelope
        except asyncio.CancelledError:
            await self._audit_failure(payload, 'cancelled')
            raise
        except TimeoutError:
            await self._audit_failure(payload, 'timeout')
            raise ToolFailure('TIMEOUT', '工具执行超时，请缩小查询范围。', True) from None
        except ToolFailure as exc:
            await self._audit_failure(payload, 'result_limit' if exc.code == 'RESULT_LIMIT' else 'failed')
            raise
        except Exception as exc:
            # The native pipe owner enforces byte caps before buffering. Its
            # fixed codes contain no provider, query, credential or row data.
            from .windows_async_process import WindowsProcessIOError
            if isinstance(exc, WindowsProcessIOError) and exc.code == 'WINDOWS_PROCESS_OUTPUT_LIMIT':
                await self._audit_failure(payload, 'result_limit')
                raise ToolFailure('RESULT_LIMIT', '工具输出超过允许范围。') from None
            await self._audit_failure(payload, 'failed')
            raise ToolFailure('INTERNAL_ERROR', '工具执行暂不可用。') from None
        finally:
            self.active -= 1
            self.calls.discard(call)

    async def close(self):
        self.closing = True
        with anyio.CancelScope(shield=True):
            pending = [call for call in self.calls if call is not asyncio.current_task()]
            for call in pending:
                call.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)


def _security_settings():
    hosts = os.getenv('ONTOLOGY_MCP_ALLOWED_HOSTS', '127.0.0.1,127.0.0.1:*,localhost,localhost:*,[::1],[::1]:*').split(',')
    origins = os.getenv('ONTOLOGY_TRUSTED_ORIGINS', 'http://localhost:8765,http://127.0.0.1:8765,http://localhost:5173,http://127.0.0.1:5173').split(',')
    hosts, origins = [item.strip() for item in hosts if item.strip()], [item.strip() for item in origins if item.strip()]
    if not hosts or '*' in hosts or any('*' in item for item in origins):
        raise ValueError('MCP requires explicit trusted hosts and origins')
    return TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=origins)


class MCPApplication:
    def __init__(self):
        self.executor = ProcessExecutor()
        self.auth_limiter = anyio.CapacityLimiter(16)
        schemas = {name: model.model_json_schema() for name, model in TOOL_MODELS.items()}
        output_schema = ToolEnvelope.model_json_schema()

        async def list_tools(ctx, params):
            state = ctx.request.scope['state']
            allowed = state['mcp_permissions']['tool_scopes']
            return types.ListToolsResult(tools=[types.Tool(
                name=name, title=TOOL_DESCRIPTIONS[name]['title'],
                description=TOOL_DESCRIPTIONS[name]['description'],
                input_schema=schemas[name], output_schema=output_schema,
                annotations=types.ToolAnnotations(read_only_hint=True, destructive_hint=False,
                                                  idempotent_hint=True, open_world_hint=True),
            ) for name in TOOL_MODELS if name in allowed])

        async def call_tool(ctx, params):
            state = ctx.request.scope['state']
            request_id, principal = state['request_id'], state['mcp_principal']
            started = asyncio.get_running_loop().time()
            try:
                arguments = parse_tool(params.name, params.arguments or {})
                if params.name not in state['mcp_permissions']['tool_scopes']:
                    raise ToolFailure('FORBIDDEN', '应用未获准调用此工具。')
                result = await self.executor.execute(
                    principal, params.name, arguments.model_dump(mode='json'), request_id,
                    deadline=started + arguments.timeout_ms / 1000,
                )
            except Exception as exc:
                result = safe_failure(exc, request_id)
            result = bounded_envelope(result)
            return types.CallToolResult(
                is_error=result.status == 'error', structured_content=result.model_dump(mode='json'),
                content=[types.TextContent(type='text', text={
                    'ok': '工具调用完成。', 'partial': '工具返回不完整结果，请查看结构化说明。',
                    'error': '工具调用未完成，请查看结构化错误。',
                }[result.status])],
            )

        self.server = Server('Ontology One', version=APP_VERSION, on_list_tools=list_tools,
                             on_call_tool=call_tool, get_tool_input_schema=schemas.get)
        # Low-level Server installs only OpenTelemetry by default. The public
        # middleware list lets this endpoint opt out even when an exporter is
        # configured elsewhere in the deployment.
        self.server.middleware.clear()
        self.sdk_app = self.server.streamable_http_app(
            streamable_http_path='/', stateless_http=True, json_response=False,
            max_request_body_size=MAX_BODY_BYTES, transport_security=_security_settings(),
        )

    @asynccontextmanager
    async def lifespan(self):
        async with self.sdk_app.router.lifespan_context(self.sdk_app):
            try:
                yield
            finally:
                await self.executor.close()

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            if scope['type'] == 'websocket':
                await send({'type': 'websocket.close', 'code': 1008})
            else:
                await self.sdk_app(scope, receive, send)
            return
        request_id = uuid.uuid4().hex
        log_token = _TRANSPORT_LOG_SCOPE.set(True)

        async def emit(message):
            if message['type'] == 'http.response.start':
                message = dict(message)
                message['headers'] = [(k, v) for k, v in message.get('headers', [])
                                      if k.lower() not in {b'cache-control', b'x-request-id'}] + [
                    (b'cache-control', b'no-store'), (b'x-request-id', request_id.encode())]
            await send(message)

        async def error(status, message):
            response = JSONResponse({'detail': message, 'request_id': request_id}, status_code=status,
                                    headers={'WWW-Authenticate': 'Bearer'} if status == 401 else None)
            await response(scope, receive, emit)

        try:
            pairs = scope.get('headers', [])
            headers = {key.lower(): value for key, value in pairs}
            override = any(any(part in key.lower() for part in (b'tenant', b'workspace', b'actor'))
                           or key.lower() in {b'x-user-id', b'x-user', b'x-authenticated-user'} for key, _ in pairs)
            if scope.get('query_string') or override or b'cookie' in headers:
                await error(403, 'MCP 仅接受独立应用身份，不能指定其他工作空间或用户。')
                return
            values = [value for key, value in pairs if key.lower() == b'authorization']
            if len(values) != 1 or not values[0].lower().startswith(b'bearer ') or not 20 <= len(values[0]) <= 512:
                await error(401, '需要有效的应用访问凭据。')
                return
            try:
                raw_token = values[0][7:].decode('ascii')
            except UnicodeDecodeError:
                await error(401, '需要有效的应用访问凭据。')
                return
            if any(char.isspace() for char in raw_token):
                await error(401, '需要有效的应用访问凭据。')
                return
            from .security.applications import authenticate_application, application_permissions

            def authenticate():
                principal = authenticate_application(raw_token, request_id)
                if principal is None:
                    return None, None
                return principal, application_permissions(principal)

            try:
                self.auth_limiter.acquire_nowait()
            except anyio.WouldBlock:
                await error(429, '认证服务繁忙，请稍后重试。')
                return
            try:
                try:
                    principal, permissions = await anyio.to_thread.run_sync(authenticate)
                except HTTPException as exc:
                    status = 401 if exc.status_code in (401, 409) else 403 if exc.status_code == 403 else 503
                    await error(status, '应用凭据或当前授权已失效。' if status in (401, 403) else '应用认证服务暂不可用。')
                    return
                except Exception:
                    await error(503, '应用认证服务暂不可用。')
                    return
            finally:
                self.auth_limiter.release()
            if principal is None:
                await error(401, '应用凭据无效、已过期或已撤销。')
                return
            state = scope.setdefault('state', {})
            state.update(request_id=request_id, mcp_principal=principal, mcp_permissions=permissions)
            with use_context(principal):
                await self.sdk_app(scope, receive, emit)
        finally:
            _TRANSPORT_LOG_SCOPE.reset(log_token)


def build_mcp_app():
    """Mount at /mcp; enter .lifespan() explicitly from the parent lifespan."""
    return MCPApplication()
