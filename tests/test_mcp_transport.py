"""Transport and real process-lifecycle tests; no database or external service."""
from __future__ import annotations

import asyncio
from dataclasses import replace
import io
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx2
import anyio

from backend import mcp_server
from backend.mcp_contracts import ToolEnvelope, ToolFailure
from backend.security.context import Principal, get_context


def principal(request_id='request-1', tenant='dept-01'):
    return Principal(user_id='test-user', username='test-user', home_tenant_id=tenant,
                     tenant_id=tenant, role='viewer', is_superadmin=False,
                     session_id='', auth_version=1, database_name='test_unused',
                     request_id=request_id, identity_kind='application',
                     application_id='test-app', credential_id='test-credential', permission_version=1)


CHILD = r'''
import json, os, sys, time
payload=json.loads(sys.stdin.buffer.readline())
mode=sys.argv[1]
if mode == 'hold':
    time.sleep(20)
elif mode == 'stdout':
    while True: os.write(1,b'x'*65536)
elif mode == 'stderr':
    while True: os.write(2,b'x'*65536)
elif mode == 'invalid':
    print('not json',flush=True)
else:
    print(json.dumps({'ready':True,'request_id':payload['request_id']}),flush=True)
    assert json.loads(sys.stdin.buffer.readline()) == {'finalize': True}
    result={'contract_version':'1.0','request_id':payload['request_id'],'status':'ok',
            'data':{'tenant':payload['principal']['tenant_id']},'error':None,'warnings':[],
            'provenance':[],'retention':{'result':'response_only','cache':'disabled','audit':'metadata_only'}}
    print(json.dumps(result),flush=True)
'''


class ProbeExecutor(mcp_server.ProcessExecutor):
    def __init__(self, mode='ok', maximum=1):
        super().__init__(maximum)
        self.mode = mode
        self.spawned = []
        self.started = asyncio.Event()
        self.barrier = None
        self.audit_events = []

    async def _spawn(self):
        process = await self._spawn_command([sys.executable, '-c', CHILD, self.mode])
        self.spawned.append(process)
        self.started.set()
        if self.barrier is not None:
            await self.barrier.wait()
        return process

    async def _audit_failure(self, payload, outcome):
        self.audit_events.append(outcome)


class ProcessTests(unittest.IsolatedAsyncioTestCase):
    async def execute(self, executor, *, seconds=2, tenant='dept-01'):
        return await executor.execute(principal(tenant=tenant), 'ontology_discover', {},
                                      'request-1', deadline=asyncio.get_running_loop().time()+seconds)

    def assert_reaped(self, executor):
        self.assertEqual(executor.active, 0)
        self.assertFalse(executor.children)
        self.assertFalse(executor.calls)
        for process in executor.spawned:
            self.assertIsNotNone(process.returncode)
            if os.name == 'nt':
                # aclose waits for ActiveProcesses == 0 and closes the native
                # Job/pipe handles; os.kill(pid, 0) is destructive on Windows.
                self.assertTrue(process._cleanup.done())
                self.assertIsNone(process._cleanup.exception())
                self.assertFalse(any(thread.is_alive() for thread in process._threads))
            else:
                with self.assertRaises(ProcessLookupError):
                    os.kill(process.pid, 0)

    async def test_two_phase_result_and_real_child_reaped(self):
        executor = ProbeExecutor()
        result = await self.execute(executor, tenant='总部部门-中文-!')
        self.assertEqual(result.data, {'tenant': '总部部门-中文-!'})
        self.assert_reaped(executor)

    async def test_timeout_terminates_and_reaps_before_releasing_slot(self):
        executor = ProbeExecutor('hold')
        with self.assertRaises(ToolFailure) as caught:
            await self.execute(executor, seconds=.10)
        self.assertEqual(caught.exception.code, 'TIMEOUT')
        self.assertEqual(executor.audit_events, ['timeout'])
        self.assert_reaped(executor)

    async def test_cancellation_terminates_and_reaps_real_pid(self):
        executor = ProbeExecutor('hold')
        task = asyncio.create_task(self.execute(executor))
        await executor.started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(executor.audit_events, ['cancelled'])
        self.assert_reaped(executor)

    async def test_anyio_level_cancellation_also_waits_for_real_reap(self):
        executor = ProbeExecutor('hold')
        with anyio.CancelScope() as scope:
            async def cancel_after_spawn():
                await executor.started.wait()
                scope.cancel()
            trigger = asyncio.create_task(cancel_after_spawn())
            await self.execute(executor)
        await trigger
        self.assertEqual(executor.audit_events, ['cancelled'])
        self.assert_reaped(executor)

    async def test_busy_has_no_queue_or_second_process(self):
        executor = ProbeExecutor('hold')
        first = asyncio.create_task(self.execute(executor))
        await executor.started.wait()
        try:
            with self.assertRaises(ToolFailure) as caught:
                await self.execute(executor)
            self.assertEqual(caught.exception.code, 'BUSY')
            self.assertEqual(len(executor.spawned), 1)
        finally:
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
        self.assert_reaped(executor)

    async def test_oversized_stdout_and_stderr_are_stopped_without_pipe_deadlock(self):
        for mode in ('stdout', 'stderr'):
            with self.subTest(mode=mode):
                executor = ProbeExecutor(mode)
                with self.assertRaises(ToolFailure) as caught:
                    await asyncio.wait_for(self.execute(executor), 3)
                self.assertEqual(caught.exception.code, 'RESULT_LIMIT')
                self.assert_reaped(executor)

    async def test_shutdown_during_spawn_waits_for_reap(self):
        executor = ProbeExecutor('hold')
        executor.barrier = asyncio.Event()
        task = asyncio.create_task(self.execute(executor))
        await executor.started.wait()
        shutdown = asyncio.create_task(executor.close())
        await asyncio.sleep(.02)
        self.assertFalse(shutdown.done())
        executor.barrier.set()
        await asyncio.wait_for(shutdown, 2)
        self.assertTrue(task.cancelled())
        self.assert_reaped(executor)
        with self.assertRaises(ToolFailure) as caught:
            await self.execute(executor)
        self.assertEqual(caught.exception.code, 'BUSY')

    async def test_mixed_tenants_run_in_distinct_processes(self):
        executor = ProbeExecutor(maximum=2)
        results = await asyncio.gather(self.execute(executor, tenant='dept-01'),
                                       self.execute(executor, tenant='sub-01'))
        self.assertEqual([item.data['tenant'] for item in results], ['dept-01', 'sub-01'])
        self.assertEqual(len({item.pid for item in executor.spawned}), 2)
        self.assert_reaped(executor)

    async def test_worker_ready_has_no_rows_and_finalization_revalidates(self):
        # The production worker is run with isolated in-process dependency
        # stubs. A control flag is changed between ready and finalize.
        script = r'''
import pathlib, sys, types
from fastapi import HTTPException
from backend.security import applications, store
from backend.mcp_contracts import ToolEnvelope
flag=pathlib.Path(sys.argv[1])
def check(p):
    if flag.exists(): raise HTTPException(401,'never expose this')
    return p
applications.assert_application_current=check
store.append_audit=lambda *a,**k:None
module=types.ModuleType('backend.mcp_tools')
def dispatch(name,args,request_id):
    print('ROW_SHOULD_NOT_BE_LOGGED')
    return ToolEnvelope(request_id=request_id,status='ok',data={'row':'SYNTHETIC_ROW'})
module.dispatch_tool=dispatch
module.validate_receipts=lambda receipts:None
sys.modules['backend.mcp_tools']=module
from backend.mcp_worker import main
main()
'''
        from dataclasses import asdict
        with tempfile.TemporaryDirectory() as directory:
            flag = Path(directory) / 'revoked'
            executor = mcp_server.ProcessExecutor()
            process = await executor._spawn_command([sys.executable, '-c', script, str(flag)])
            try:
                payload = {'principal': asdict(principal()), 'tool': 'ontology_discover',
                           'arguments': {}, 'request_id': 'request-1', 'mode': 'execute'}
                process.stdin.write(json.dumps(payload).encode()+b'\n')
                await process.stdin.drain()
                ready = await asyncio.wait_for(process.stdout.readline(), 4)
                self.assertEqual(json.loads(ready), {'ready': True, 'request_id': 'request-1'})
                self.assertNotIn(b'SYNTHETIC_ROW', ready)
                flag.touch()
                process.stdin.write(b'{"finalize":true}\n')
                await process.stdin.drain()
                process.stdin.close()
                raw = await asyncio.wait_for(process.stdout.read(), 4)
                result = json.loads(raw)
                self.assertEqual(result['error']['code'], 'UNAUTHENTICATED')
                self.assertIsNone(result['data'])
                self.assertNotIn(b'SYNTHETIC_ROW', raw)
                self.assertNotIn(b'ROW_SHOULD_NOT_BE_LOGGED', raw)
                self.assertEqual(await process.stderr.read(), b'')
                await process.wait()
            finally:
                await executor._reap(process)


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.authentication = patch('backend.security.applications.authenticate_application',
                                    side_effect=lambda token, rid: principal(rid, 'dept-01' if token.endswith('a') else 'sub-01'))
        self.permissions = patch('backend.security.applications.application_permissions',
                                 return_value={'tool_scopes': list(mcp_server.TOOL_MODELS)})
        self.auth = self.authentication.start()
        self.permissions.start()
        self.app = mcp_server.build_mcp_app()
        self.life_started, self.life_stopped = asyncio.Event(), asyncio.Event()

        async def run_lifespan():
            async with self.app.lifespan():
                self.life_started.set()
                await self.life_stopped.wait()

        self.life = asyncio.create_task(run_lifespan())
        await self.life_started.wait()
        self.client = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=self.app), base_url='http://localhost:8877')
        self.headers = {'Authorization': 'Bearer onto_app_'+'a'*43,
                        'MCP-Protocol-Version': '2025-11-25', 'Accept': 'application/json, text/event-stream'}

    async def asyncTearDown(self):
        await self.client.aclose()
        self.life_stopped.set()
        await self.life
        self.authentication.stop()
        self.permissions.stop()

    async def post(self, method, params, headers=None, path='/'):
        return await self.client.post(path, headers=self.headers if headers is None else headers,
                                      json={'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})

    def result(self, response):
        if response.headers.get('content-type', '').startswith('text/event-stream'):
            data = next(line[6:] for line in response.text.splitlines() if line.startswith('data: '))
            return json.loads(data)['result']
        return response.json()['result']

    async def test_official_list_tools_has_strict_discoverable_contract_and_no_session(self):
        response = await self.post('tools/list', {})
        tools = self.result(response)['tools']
        self.assertEqual({item['name'] for item in tools}, set(mcp_server.TOOL_MODELS))
        self.assertTrue(all(item['inputSchema']['additionalProperties'] is False for item in tools))
        self.assertTrue(all('outputSchema' in item for item in tools))
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertIn('x-request-id', response.headers)
        self.assertNotIn('mcp-session-id', response.headers)
        self.assertEqual(self.app.server.middleware, [])

    async def test_cookie_overrides_duplicate_auth_and_missing_bearer_rejected(self):
        cases = [({'Cookie': 'ontology_session=synthetic'}, '/', 403),
                 ({**self.headers, 'X-Tenant-Id': 'sub-01'}, '/', 403),
                 (self.headers, '/?tenant=sub-01', 403),
                 ({}, '/', 401)]
        for headers, path, status in cases:
            with self.subTest(status=status, path=path):
                response = await self.post('tools/list', {}, headers, path)
                self.assertEqual(response.status_code, status)
        self.auth.assert_not_called()
        response = await self.post('tools/list', {}, [('authorization', self.headers['Authorization']),
                                                      ('authorization', self.headers['Authorization'])])
        self.assertEqual(response.status_code, 401)

    async def test_invalid_arguments_never_spawn_and_never_echo_rejected_value(self):
        with patch.object(self.app.executor, 'execute', new_callable=AsyncMock) as execute:
            response = await self.post('tools/call', {'name': 'ontology_discover',
                                                    'arguments': {'tenant_id': 'SENSITIVE_INPUT'}})
        execute.assert_not_called()
        result = self.result(response)
        self.assertTrue(result['isError'])
        self.assertEqual(result['structuredContent']['error']['code'], 'INVALID_ARGUMENT')
        self.assertNotIn('SENSITIVE_INPUT', response.text)

    async def test_per_request_identity_isolated_and_sdk_logs_redacted(self):
        captured = io.StringIO()
        handler = logging.StreamHandler(captured)
        logger = logging.getLogger('mcp.transport.probe')
        logger.addHandler(handler)
        logger.setLevel(logging.WARNING)

        async def execute(p, tool, args, rid, **kwargs):
            await asyncio.sleep(.01)
            self.assertEqual(get_context(), p)
            logger.warning('TOKEN_AND_ROW_SENTINEL %s', 'FILTER_SENTINEL')
            return ToolEnvelope(request_id=rid, status='ok', data={'tenant': p.tenant_id})

        try:
            with patch.object(self.app.executor, 'execute', side_effect=execute):
                first, second = await asyncio.gather(
                    self.post('tools/call', {'name': 'ontology_discover', 'arguments': {}}),
                    self.post('tools/call', {'name': 'ontology_discover', 'arguments': {}},
                              {**self.headers, 'Authorization': 'Bearer onto_app_'+'b'*43}),
                )
            self.assertEqual(self.result(first)['structuredContent']['data']['tenant'], 'dept-01')
            self.assertEqual(self.result(second)['structuredContent']['data']['tenant'], 'sub-01')
            self.assertEqual(self.auth.call_count, 2)
            self.assertIsNone(get_context())
            self.assertNotIn('SENTINEL', captured.getvalue())
            logger.warning('unrelated request remains visible')
            self.assertIn('unrelated request remains visible', captured.getvalue())
        finally:
            logger.removeHandler(handler)


if __name__ == '__main__':
    unittest.main()
