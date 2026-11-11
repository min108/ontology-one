"""Disposable session-authorized tool workers; a slot ends only after Job reap."""
from __future__ import annotations

import asyncio
from dataclasses import asdict, replace
import os
import sys
import uuid

from .mcp_contracts import Receipt, ToolFailure
from .mcp_server import ProcessExecutor, _json


class AgentToolExecutor(ProcessExecutor):
    async def _spawn(self):
        environment = dict(os.environ)
        # The trusted source worker needs DB and source configuration, never the
        # model credential. Neither bearer/session cookies nor keys are in IPC.
        for name in ('DEEPSEEK_API_KEY', 'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'OPENAI_API_KEY'):
            environment.pop(name, None)
        environment.update(PYTHONDONTWRITEBYTECODE='1', PYTHONUNBUFFERED='1')
        return await self._spawn_command([sys.executable, '-X', 'utf8', '-B', '-m', 'backend.agent_tool_worker'], environment)

    async def execute(self, principal, mapping_ids, skill_ids, name, args, requestid, *, deadline):
        if self.closing or self.active >= self.maximum:
            raise ToolFailure('BUSY', '只读工具正在处理其他请求，请稍后重试。', True)
        if (principal.identity_kind != 'session' or not isinstance(requestid, str)
                or not 1 <= len(requestid) <= 100):
            raise ToolFailure('FORBIDDEN', '工具需要有效的平台会话。')
        self.active += 1
        call = asyncio.current_task()
        self.calls.add(call)
        payload = {'principal': asdict(replace(principal, request_id=requestid)), 'mapping_ids': sorted(mapping_ids),
                   'skill_ids': sorted(skill_ids), 'tool': name, 'arguments': args, 'request_id': requestid}
        try:
            value = await self._run_process(payload, deadline)
            if (isinstance(value, dict) and value.get('request_id') == requestid
                    and value.get('ok') is False and isinstance(value.get('error'), dict)):
                error = value['error']
                from .mcp_contracts import SafeError
                error = SafeError.model_validate(error, strict=True)
                allowed = {'INVALID_ARGUMENT', 'UNAUTHENTICATED', 'FORBIDDEN', 'STALE_MAPPING',
                           'TIMEOUT', 'BUSY', 'RESULT_LIMIT', 'SOURCE_UNAVAILABLE', 'INTERNAL_ERROR'}
                if error.code in allowed:
                    raise ToolFailure(error.code, error.message, error.retryable)
            if (not isinstance(value, dict) or value.get('request_id') != requestid
                    or value.get('ok') is not True
                    or set(value) != {'ok', 'request_id', 'value', 'receipts', 'skill_versions', 'group_versions', 'asset_versions'}):
                raise ToolFailure('INTERNAL_ERROR', '只读工具执行暂不可用。')
            receipts = value['receipts']
            versions = value['skill_versions']
            group_versions = value['group_versions']
            asset_versions = value['asset_versions']
            if (not isinstance(receipts, list) or len(receipts) > 64 or not isinstance(versions, dict)
                    or len(versions) > 16 or set(versions) - set(skill_ids)
                    or not isinstance(group_versions, dict) or len(group_versions) > 100
                    or len(_json(group_versions)) > 64 * 1024
                    or any(not isinstance(k, str) or '/' not in k or len(k) > 400 or type(v) is not int or v < 1 for k, v in group_versions.items())
                    or not isinstance(asset_versions, dict) or len(asset_versions) > 100
                    or any(not isinstance(k, str) or not 1 <= len(k) <= 191 or type(v) is not int or v < 1 for k, v in asset_versions.items())):
                raise ToolFailure('INTERNAL_ERROR', '只读工具返回格式不符合约定。')
            checked = [Receipt.model_validate(receipt, strict=True).model_dump(mode='python') for receipt in receipts]
            if any(item['tenant_id'] != principal.tenant_id or item['mapping_id'] not in mapping_ids for item in checked):
                raise ToolFailure('FORBIDDEN', '来源归属超出本次会话范围。')
            for version in versions.values():
                if (not isinstance(version, list) or len(version) != 2 or type(version[0]) is not int
                        or version[0] < 1 or not isinstance(version[1], str) or len(version[1]) != 64):
                    raise ToolFailure('INTERNAL_ERROR', '技能版本返回格式不符合约定。')
            if len(_json(value['value'])) > 256 * 1024:
                raise ToolFailure('RESULT_LIMIT', '只读工具结果超过限额。')
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError()
            return {'value': value['value'], 'receipts': checked, 'skill_versions': versions,
                    'group_versions': group_versions, 'asset_versions': asset_versions}
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            raise ToolFailure('TIMEOUT', '只读工具执行超时，请缩小范围。', True) from None
        except ToolFailure:
            raise
        except Exception:
            raise ToolFailure('INTERNAL_ERROR', '只读工具执行暂不可用。') from None
        finally:
            self.active -= 1
            self.calls.discard(call)

    async def validate(self, principal, mapping_ids, skill_ids, receipts, skill_versions,
                       group_versions, asset_versions, *, deadline):
        result = await self.execute(principal, mapping_ids, skill_ids, '__validate_ledger',
                                    {'receipts': receipts, 'skill_versions': skill_versions,
                                     'group_versions': group_versions, 'asset_versions': asset_versions},
                                    'validate-' + uuid.uuid4().hex, deadline=deadline)
        if result['value'] != {'valid': True}:
            raise ToolFailure('INTERNAL_ERROR', '工具授权证明复核未完成。')
        return True


EXECUTOR = AgentToolExecutor()
