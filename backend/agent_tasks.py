"""Bounded, memory-only generic tasks on the same pinned official Harness."""
from __future__ import annotations
import asyncio
import time
import uuid
from fastapi import HTTPException
from . import skill_harness as harness
from .security.context import use_context
from .security.store import assert_principal_current, append_audit


def current(principal):
    if principal.tenant_id != principal.home_tenant_id:
        raise HTTPException(403, '请切回所属机构启动智能体，可通过集团授权查阅其他机构成果。')
    if principal.identity_kind != 'session' or principal.must_change_password:
        raise HTTPException(403, '当前会话不能启动智能体。')
    from .jobs import assert_execution_principal_current
    return assert_execution_principal_current(principal, require_write=False)


async def execute_controlled(principal, *, mode, metadata, instruction='完成当前只读任务。', timeout_seconds=90, on_event=None):
    """Only platform code selects mode/tools. Provider secrets stay in private IPC."""
    with use_context(principal):
        await asyncio.to_thread(current, principal)
        config = await asyncio.to_thread(harness._configuration)
        if not config['api_key']:
            raise harness._error('HARNESS_NOT_CONFIGURED')
        executable = await asyncio.to_thread(harness._runtime)
        payload = {'instruction': instruction, 'files': {'SKILL.md': '---\nname: controlled-task\ndescription: Platform controlled task.\n---\nRead-only task.'},
                   'manifest': {}, 'metadata': metadata, 'request_id': uuid.uuid4().hex,
                   'configuration': {key: config[key] for key in ('api_key', 'base_url', 'model')},
                   'runtime_executable': str(executable), 'mode': mode}
        started = time.monotonic()
        await asyncio.to_thread(append_audit, principal, 'harness.' + mode + '.started', '', 'started', {'runtime_version': harness.SDK_VERSION})
        try:
            options = {'timeout_seconds': timeout_seconds}
            if on_event is not None:
                options['on_event'] = on_event
            result = await harness.EXECUTOR.execute(payload, **options)
            await asyncio.to_thread(current, principal)
            fresh = await asyncio.to_thread(harness._configuration)
            if any(fresh.get(key) != config.get(key) for key in ('revision', 'base_url', 'model', 'api_key')):
                raise harness._error('HARNESS_CONFIG_CHANGED')
            text = result.get('text')
            if (not isinstance(text, str) or not text.strip() or len(text.encode('utf-8')) > 128 * 1024
                    or config['api_key'] in text):
                raise harness._error('HARNESS_INCOMPLETE')
            result = {'text': text, 'usage': harness._safe_usage(result.get('usage'))}
            await asyncio.to_thread(append_audit, principal, 'harness.' + mode + '.finished', '', 'success',
                                    {'runtime_version': harness.SDK_VERSION, 'duration_ms': round((time.monotonic()-started)*1000), 'usage': result['usage']})
            return result
        except BaseException as exc:
            code = ('cancelled' if isinstance(exc, asyncio.CancelledError) else
                    exc.detail.get('code', 'failed') if isinstance(exc, HTTPException) and isinstance(exc.detail, dict) else 'failed')
            try:
                await asyncio.shield(asyncio.to_thread(append_audit, principal, 'harness.' + mode + '.failed', '', 'failed',
                                                    {'code': code, 'duration_ms': round((time.monotonic()-started)*1000)}))
            except Exception:
                pass
            raise
        finally:
            payload.clear()


async def complete_task(principal, messages, *, json_mode=False):
    if (not isinstance(messages, list) or not 1 <= len(messages) <= 24 or type(json_mode) is not bool
            or len(harness._json(messages)) > 256 * 1024):
        raise HTTPException(413, '智能任务上下文超过限制。')
    for message in messages:
        if (not isinstance(message, dict) or set(message) != {'role', 'content'} or
                message['role'] not in {'system', 'user', 'assistant'} or not isinstance(message['content'], str)):
            raise HTTPException(422, '智能任务消息格式无效。')
    return (await execute_controlled(principal, mode='task', metadata={'messages': messages, 'json_mode': json_mode}))['text']
