"""Real pinned Harness orchestration for task/agent modes, simulated model only.

RUN_HARNESS_SDK_TESTS=1 enables the actual runtime. These tests verify SDK/tool
transport and resource retention; the separate HTTP acceptance verifies actual
account, tenant, source and publication authorization without identity mocks.
"""
from __future__ import annotations

import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from backend import skill_harness as client


@unittest.skipUnless(os.getenv('RUN_HARNESS_SDK_TESTS') == '1', 'real pinned SDK integration is opt-in')
class AgentOfficialSDKTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests, self.bridge_calls = [], []
        self.plan = []
        self.hold = threading.Event()
        self.release = threading.Event()
        # Keep cleanup assertions scoped to this fixture even when an HTTP
        # acceptance run uses the same tmpfs/runtime concurrently.
        self.root = Path(tempfile.mkdtemp(prefix='ontology-agent-sdk-', dir=client._work_root()))
        if sys.platform == 'win32':
            from backend.platform_io import ensure_private_directory
            ensure_private_directory(self.root)
        self.addCleanup(self.root.rmdir)
        self.root_patch = patch.object(client, '_work_root', return_value=self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.before = set(self.root.glob('ontology-skill-*'))
        self.executor = client.HarnessExecutor(maximum=1)
        self.bridge_token = secrets.token_urlsafe(32)
        owner = self

        class Model(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                owner.requests.append(body)
                if owner.hold.is_set():
                    owner.release.wait(10)
                index = len(owner.requests) - 1
                content = owner.plan[min(index, len(owner.plan)-1)]
                events = [
                    {'type': 'message_start', 'message': {'id': 'synthetic-agent', 'model': 'deepseek-flash',
                     'usage': {'input_tokens': 3, 'output_tokens': 0}}},
                    {'type': 'content_block_start', 'index': 0, 'content_block': content},
                    {'type': 'content_block_stop', 'index': 0},
                    {'type': 'message_delta', 'delta': {'stop_reason': 'tool_use' if content['type'] == 'tool_use' else 'end_turn'},
                     'usage': {'output_tokens': 1}}, {'type': 'message_stop'},
                ]
                raw = ''.join('event: '+item['type']+'\ndata: '+json.dumps(item, ensure_ascii=False)+'\n\n'
                              for item in events).encode()
                self.send_response(200); self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Content-Length', str(len(raw))); self.end_headers()
                try: self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError): pass

        class Bridge(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_POST(self):
                if self.path != '/tools' or self.headers.get('Authorization') != 'Bearer '+owner.bridge_token:
                    self.send_response(403); self.end_headers(); return
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                owner.bridge_calls.append(body)
                value = {'contract_version': '1.0', 'status': 'ok', 'data': {'entities': [
                    {'id': 'synthetic-asset', 'title': '合成金额标准', 'owner_tenant_id': 'sub-01'}]},
                    'provenance': [], 'retention': {'result': 'response_only', 'cache': 'disabled', 'audit': 'metadata_only'}}
                raw = json.dumps(value, ensure_ascii=False).encode()
                self.send_response(200); self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw)

        self.servers, self.threads = [], []
        for handler in (Model, Bridge):
            server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
            server.daemon_threads = True
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start(); self.servers.append(server); self.threads.append(thread)

    async def asyncTearDown(self):
        self.release.set()
        await self.executor.close()
        for server, thread in zip(self.servers, self.threads):
            await asyncio.to_thread(server.shutdown); server.server_close(); thread.join(2)
        self.assertFalse(self.executor.children)
        self.assertFalse(self.executor.calls)
        self.assertEqual(self.executor.active, 0)
        self.assertEqual(set(self.root.glob('ontology-skill-*')) - self.before, set())

    def payload(self, mode='task', *, json_mode=True):
        metadata = {'messages': [{'role': 'user', 'content': '仅处理合成金额；保留中文业务含义。'}], 'json_mode': json_mode}
        if mode == 'agent':
            metadata = {'messages': metadata['messages'],
                        'tool_bridge': {'url': 'http://127.0.0.1:'+str(self.servers[1].server_port)+'/tools',
                                        'token': self.bridge_token},
                        'tools': [{'name': 'ontology_discover', 'description': 'Read synthetic metadata only.',
                                   'parameters': {'type': 'object', 'properties': {'query': {'type': 'string'}},
                                                  'additionalProperties': False}}]}
        return {'instruction': 'Complete the bounded platform task.',
                'files': {'SKILL.md': '---\nname: synthetic-task\ndescription: Synthetic.\n---\nSynthetic.'},
                'manifest': {}, 'metadata': metadata, 'request_id': 'official-agent-sdk-test',
                'configuration': {'api_key': 'synthetic-local-agent-test-only',
                                  'base_url': 'http://127.0.0.1:'+str(self.servers[0].server_port),
                                  'model': 'deepseek-flash'},
                'runtime_executable': str(client._runtime()), 'mode': mode}

    async def test_task_json_uses_official_tool_and_returns_valid_unicode_value(self):
        expected = {'业务含义': '合成金额', '结果': 700}
        self.plan = [{'type': 'tool_use', 'id': 'submit-1', 'name': 'submit_task_result',
                      'input': {'text': json.dumps(expected, ensure_ascii=False)}}, {'type': 'text', 'text': '完成'}]
        result = await self.executor.execute(self.payload())
        self.assertEqual(json.loads(result['text']), expected)
        self.assertEqual(len(self.requests), 2)
        self.assertEqual({item['name'] for item in self.requests[0]['tools']}, {'submit_task_result'})
        self.assertEqual(self.bridge_calls, [])

    async def test_task_invalid_json_tool_result_is_rejected_then_can_be_corrected(self):
        self.plan = [{'type': 'tool_use', 'id': 'bad-json', 'name': 'submit_task_result', 'input': {'text': 'not-json'}},
                     {'type': 'tool_use', 'id': 'good-json', 'name': 'submit_task_result', 'input': {'text': '{"valid":true}'}},
                     {'type': 'text', 'text': '完成'}]
        result = await self.executor.execute(self.payload())
        self.assertEqual(json.loads(result['text']), {'valid': True})
        errors = [part for message in self.requests[1]['messages'] if isinstance(message.get('content'), list)
                  for part in message['content'] if part.get('type') == 'tool_result']
        self.assertTrue(errors[-1]['is_error'])

    async def test_agent_only_exposes_fixed_selected_tool_and_calls_authenticated_bridge(self):
        self.plan = [{'type': 'tool_use', 'id': 'discover', 'name': 'ontology_discover', 'input': {'query': '金额'}},
                     {'type': 'text', 'text': '已读取合成金额标准，未修改业务数据。'}]
        result = await self.executor.execute(self.payload('agent'))
        self.assertIn('合成金额标准', result['text'])
        self.assertEqual(self.bridge_calls, [{'name': 'ontology_discover', 'arguments': {'query': '金额'}}])
        for request in self.requests:
            self.assertEqual({item['name'] for item in request['tools']}, {'ontology_discover'})
            self.assertNotIn(self.bridge_token, json.dumps(request))

    async def test_model_request_for_builtin_terminal_never_reaches_bridge(self):
        self.plan = [{'type': 'tool_use', 'id': 'forbidden', 'name': 'bash', 'input': {'command': 'printf forbidden'}},
                     {'type': 'text', 'text': '该工具不在授权范围中。'}]
        result = await self.executor.execute(self.payload('agent'))
        self.assertIn('授权范围', result['text'])
        self.assertEqual(self.bridge_calls, [])
        results = [part for message in self.requests[1]['messages'] if isinstance(message.get('content'), list)
                   for part in message['content'] if part.get('type') == 'tool_result']
        self.assertTrue(results[-1]['is_error'])

    async def test_runtime_session_never_writes_prompt_result_or_provider_key(self):
        markers = ['PRIVATE_SYNTHETIC_PROMPT_901', 'PRIVATE_SYNTHETIC_RESULT_902', 'PRIVATE_SYNTHETIC_KEY_903']
        self.plan = [{'type': 'text', 'text': markers[1]}]
        payload = self.payload('agent')
        payload['metadata']['messages'][0]['content'] = markers[0]
        payload['configuration']['api_key'] = markers[2]
        directories, inspected = [], []
        spawn, reap = self.executor._spawn, self.executor._reap
        async def capture(directory):
            directories.append(directory)
            return await spawn(directory)
        async def inspect(process):
            await reap(process)
            inspected.append(True)
            for path in directories[-1].rglob('*'):
                if path.is_file():
                    self.assertNotEqual(path.name, 'context.json')
                    self.assertFalse(path.name.endswith(('.jsonl', '.jsonl.zstd')))
                    self.assertFalse(any(marker.encode() in path.read_bytes() for marker in markers), path.name)
        with patch.object(self.executor, '_spawn', side_effect=capture), patch.object(self.executor, '_reap', side_effect=inspect):
            result = await self.executor.execute(payload)
        self.assertEqual(result['text'], markers[1])
        self.assertEqual(inspected, [True])
        self.assertFalse(directories[0].exists())

    async def test_cancel_actual_agent_runtime_reaps_full_owned_processes(self):
        self.plan = [{'type': 'text', 'text': '未发送'}]
        self.hold.set()
        task = asyncio.create_task(self.executor.execute(self.payload('agent')))
        try:
            async with asyncio.timeout(20):
                while not self.requests:
                    if task.done(): await task
                    await asyncio.sleep(.01)
            if sys.platform == 'win32':
                observed = {pid for process in self.executor.children for pid in process.process_ids()}
            else:
                observed = {process.pid for process in self.executor.children}
            self.assertTrue(observed)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
            self.assertFalse(self.executor.children)
            self.assertEqual(self.executor.active, 0)
            if sys.platform == 'linux':
                self.assertFalse([pid for pid in observed if Path('/proc', str(pid)).exists()])
            else:
                from tests.test_skill_harness import windows_pid_alive
                self.assertFalse([pid for pid in observed if windows_pid_alive(pid)])
        finally:
            self.release.set()
            if not task.done(): task.cancel()
            await asyncio.gather(task, return_exceptions=True)


if __name__ == '__main__':
    unittest.main()
