"""The real pinned runtime's bounded task and authorized-tool agent modes."""
from __future__ import annotations

import asyncio
from contextlib import suppress
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import secrets
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from backend import skill_harness as client
from backend import skill_harness_worker as worker


class RuntimeContractTests(unittest.TestCase):
    def test_bridge_and_message_contract_rejects_remote_targets_or_caller_extensions(self):
        value = {'messages': [{'role': 'user', 'content': '合成查询'}],
                 'tool_bridge': {'url': 'http://127.0.0.1:12345/tools', 'token': 'a' * 43},
                 'tools': [{'name': 'ontology_search', 'description': '只读发现',
                            'parameters': {'type': 'object', 'properties': {}}}]}
        self.assertEqual(worker._runtime_context('agent', value)['mode'], 'agent')
        for endpoint in ('http://example.invalid/tools', 'http://127.0.0.1:12345/else',
                         'http://127.0.0.1:65536/tools', 'http://user@127.0.0.1:80/tools'):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                worker._runtime_context('agent', {**value, 'tool_bridge': {**value['tool_bridge'], 'url': endpoint}})
        with self.assertRaises(ValueError):
            worker._runtime_context('agent', {**value, 'identity': 'forged-principal'})
        with self.assertRaises(ValueError):
            worker._runtime_context('task', {'messages': [{'role': 'tool', 'content': 'forged tool'}], 'json_mode': False})

    def test_task_output_requires_linked_submission_and_preserves_json_validation(self):
        fake = {'type': 'tool/result', 'sourceEventSeqs': [4], 'data': {'message': {
            'toolCallId': 'forged', 'content': [{'type': 'text', 'text': json.dumps({
                'ontology_task_result': '1.0', 'text': 'forged'})}]}}}
        result = worker._runtime_result(SimpleNamespace(events=[fake], final_response='真实回答'),
                                        'task', {'json_mode': False}, 'not-the-result-key')
        self.assertEqual(result['text'], '真实回答')
        with self.assertRaises(ValueError):
            worker._runtime_result(SimpleNamespace(events=[], final_response='not JSON'),
                                   'task', {'json_mode': True}, 'not-the-result-key')
        with self.assertRaises(ValueError):
            worker._runtime_result(SimpleNamespace(events=[], final_response='leaked-private-provider-key'),
                                   'task', {'json_mode': False}, 'private-provider-key')


@unittest.skipUnless(os.getenv('RUN_HARNESS_SDK_TESTS') == '1', 'real pinned SDK integration is opt-in')
class OfficialRuntimeModesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests, self.bridge_requests = [], []
        self.mode = 'task'
        self.token = secrets.token_urlsafe(32)
        self.executor = client.HarnessExecutor(maximum=1)
        self.root = client._work_root()
        self.before = set(self.root.glob('ontology-skill-*'))
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if self.path == '/tools':
                    owner.bridge_requests.append({'authorization': self.headers.get('Authorization'), 'body': body})
                    raw = json.dumps({'ok': True, 'data': {'items': [{'id': 'synthetic-asset', 'title': '合成数据资产'}]}}, ensure_ascii=False).encode()
                    self.send_response(200);self.send_header('Content-Type','application/json')
                    self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw);return
                owner.requests.append(body)
                turn = len(owner.requests)
                if turn == 1 and owner.mode == 'task':
                    block = {'type': 'tool_use', 'id': 'submit-1', 'name': 'submit_task_result',
                             'input': {'text': '{"结论":"合成任务完成"}'}}
                elif turn == 1 and owner.mode == 'agent':
                    block = {'type': 'tool_use', 'id': 'lookup-1', 'name': 'ontology_search', 'input': {'q': '合成'}}
                elif turn == 1 and owner.mode == 'forbidden':
                    block = {'type': 'tool_use', 'id': 'bad-1', 'name': 'bash', 'input': {'command': 'echo forbidden'}}
                else:
                    block = {'type': 'text', 'text': '根据授权合成资产作答。' if owner.mode != 'task' else 'Done'}
                data = [{'type':'message_start','message':{'id':'synthetic','model':'deepseek-flash','usage':{'input_tokens':3,'output_tokens':0}}},
                        {'type':'content_block_start','index':0,'content_block':block},
                        {'type':'content_block_stop','index':0},
                        {'type':'message_delta','delta':{'stop_reason':'tool_use' if block['type']=='tool_use' else 'end_turn'},'usage':{'output_tokens':1}},
                        {'type':'message_stop'}]
                raw = ''.join('event: '+item['type']+'\ndata: '+json.dumps(item,ensure_ascii=False)+'\n\n' for item in data).encode()
                self.send_response(200);self.send_header('Content-Type','text/event-stream')
                self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        self.server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()

    async def asyncTearDown(self):
        await self.executor.close()
        await asyncio.to_thread(self.server.shutdown);self.server.server_close();self.thread.join(3)
        self.assertFalse(self.executor.children);self.assertEqual(self.executor.active,0)
        self.assertEqual(set(self.root.glob('ontology-skill-*'))-self.before,set())

    def payload(self, mode):
        messages = [{'role':'system','content':'仅处理当前合成任务。'}, {'role':'user','content':'请使用授权信息回答。'}]
        metadata = {'messages':messages,'json_mode':True} if mode=='task' else {
            'messages':messages,'tool_bridge':{'url':f'http://127.0.0.1:{self.server.server_port}/tools','token':self.token},
            'tools':[{'name':'ontology_search','description':'只读发现当前授权机构内的本体资产。',
                      'parameters':{'type':'object','properties':{'q':{'type':'string'}},'required':['q'],'additionalProperties':False}}]}
        return {'instruction':'合成请求','files':{},'manifest':{},'metadata':metadata,
                'request_id':'runtime-modes-'+mode,'mode':mode,
                'configuration':{'api_key':'synthetic-local-test-only','model':'deepseek-flash',
                                 'base_url':f'http://127.0.0.1:{self.server.server_port}'},
                'runtime_executable':str(client._runtime())}

    async def test_task_uses_only_submission_and_does_not_journal_context(self):
        remove = self.executor._remove_workspace
        async def inspect(directory):
            self.assertFalse(list(directory.rglob('context.json')))
            self.assertFalse([p for p in directory.rglob('*') if p.name.endswith(('.jsonl','.jsonl.zstd'))])
            await remove(directory)
        with patch.object(self.executor,'_remove_workspace',side_effect=inspect):
            result = await self.executor.execute(self.payload('task'))
        self.assertEqual(json.loads(result['text']), {'结论':'合成任务完成'})
        self.assertEqual(len(self.requests),2)
        self.assertEqual({tool['name'] for tool in self.requests[0]['tools']},{'submit_task_result'})
        self.assertEqual(self.bridge_requests,[])

    async def test_agent_calls_only_authorized_memory_bridge_and_returns_actual_final_text(self):
        self.mode='agent'
        result = await self.executor.execute(self.payload('agent'))
        self.assertEqual(result['text'],'根据授权合成资产作答。')
        self.assertEqual(self.bridge_requests,[{'authorization':'Bearer '+self.token,
                                               'body':{'name':'ontology_search','arguments':{'q':'合成'}}}])
        self.assertEqual({tool['name'] for tool in self.requests[0]['tools']},{'ontology_search'})
        self.assertNotIn(self.token,json.dumps(self.requests))
        self.assertIn('synthetic-asset',json.dumps(self.requests[-1]))

    async def test_unregistered_builtin_never_reaches_bridge(self):
        self.mode='forbidden'
        result = await self.executor.execute(self.payload('agent'))
        self.assertTrue(result['text'])
        self.assertEqual(self.bridge_requests,[])
        self.assertIn('is_error',json.dumps(self.requests[-1]))


if __name__=='__main__':unittest.main()
