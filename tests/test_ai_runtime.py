"""Legacy AI client boundaries; only synthetic config and mock HTTP are used."""
import asyncio
from contextlib import ExitStack
from dataclasses import replace
import json
import multiprocessing
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import httpx

from backend import ai, authority, projects
from backend.security.context import Principal, use_context


KEY = 'synthetic-test-key-not-a-real-credential'
MESSAGES = [{'role': 'user', 'content': '合成样例，仅回复测试成功。'}]
HTTP_CLIENT = httpx.AsyncClient


class Chunks(httpx.AsyncByteStream):
    def __init__(self, raw, chunk_size=8192):
        self.raw, self.chunk_size = raw, chunk_size
        self.consumed = 0
        self.closed = False

    async def __aiter__(self):
        for start in range(0, len(self.raw), self.chunk_size):
            chunk = self.raw[start:start + self.chunk_size]
            self.consumed += len(chunk)
            yield chunk

    async def aclose(self):
        self.closed = True


def response(content='测试成功。', *, raw=None, status=200, headers=None, finish='stop'):
    body = raw if raw is not None else json.dumps({'choices': [{'message': {'content': content}, 'finish_reason': finish}]}).encode()
    stream = Chunks(body)
    result = httpx.Response(status, stream=stream, headers=headers or {})
    result.test_stream = stream
    return result


def child_save(entered, finished):
    entered.set()
    ai._save_configuration(ai.AIConfiguration(model='concurrent-model'))
    finished.set()


class Fixture:
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='ai-runtime-unit-')
        self.stack = ExitStack()
        self.stack.enter_context(patch.dict(os.environ, {'ONTOLOGY_STORAGE_ROOT': self.temporary.name}))
        self.principal = Principal('user-test', 'synthetic-admin', 'test-ai', 'test-ai', 'admin', False,
                                   'session-test', 1, 'unused_test_database')
        self.stack.enter_context(use_context(self.principal))
        self.disabled = False

        def validate(principal, require_write=False):
            if self.disabled:
                raise HTTPException(401, '登录已失效，请重新登录。')
            if require_write and principal.readonly:
                raise HTTPException(403, '只读身份不可写入。')
            return principal

        self.validator = self.stack.enter_context(patch('backend.jobs.assert_execution_principal_current', side_effect=validate))

    def tearDown(self):
        self.stack.close()
        self.temporary.cleanup()

    def configured(self):
        ai._save_configuration(ai.AIConfiguration(api_key=KEY))
        return ai.config()

    def mock_network(self, handler):
        original = httpx.AsyncClient
        transport = httpx.MockTransport(handler)
        return patch.object(ai.httpx, 'AsyncClient', side_effect=lambda **kwargs: original(transport=transport, **kwargs))


class ConfigurationTests(Fixture, unittest.TestCase):
    def test_status_is_backward_compatible_without_credentials_or_fingerprint(self):
        self.assertEqual(set(ai.status()), {'configured', 'model', 'base_url', 'online_verified', 'mode'})
        self.configured()
        self.assertNotIn(KEY, json.dumps(ai.status()))
        self.assertTrue(ai.status()['configured'])
        self.assertFalse(ai.status()['online_verified'])

    def test_strict_url_and_model_secret_validation(self):
        invalid_urls = ['http://example.com', 'https://user:pass@example.com', 'https://example.com?key=x',
                        'https://example.com/#x', 'https://example.com\\x', 'https://example.com/%2e',
                        'https://example.com/../x', 'https://example.com:0', 'https://example.com:65536',
                        'https://example.com:', 'https://bad..host', 'https://example.com\n',
                        'https://例子.公司', 'https:///missing', 'https://example.com/?']
        for value in invalid_urls:
            with self.subTest(value=value), self.assertRaises(ValueError):
                ai.AIConfiguration(base_url=value)
        self.assertEqual(ai.AIConfiguration(base_url='https://EXAMPLE.com:443/v1/').base_url, 'https://example.com/v1')
        for value in [True, 3, [], 'bad model', '', '../model?key=x']:
            with self.subTest(model=value), self.assertRaises(ValueError):
                ai.AIConfiguration(model=value)
        for value in [True, 123, {}, 'short', 'spaces are invalid', 'has\nnewline', '非ASCII密钥字符串']:
            with self.subTest(secret=value), self.assertRaises(ValueError):
                ai.AIConfiguration(api_key=value)

    def test_endpoint_change_cannot_reuse_key_without_explicit_input(self):
        before = self.configured()
        for endpoint in ['https://elsewhere.example', 'https://api.deepseek.com/v1', 'https://api.deepseek.com:9443']:
            with self.subTest(endpoint=endpoint), self.assertRaises(HTTPException):
                ai._save_configuration(ai.AIConfiguration(base_url=endpoint))
            self.assertEqual(ai.config(), before)
        ai._save_configuration(ai.AIConfiguration(base_url='https://elsewhere.example', clear_key=True))
        self.assertFalse(ai.status()['configured'])
        ai._save_configuration(ai.AIConfiguration(api_key='new-synthetic-endpoint-key'))
        self.assertEqual(ai.config()['base_url'], 'https://elsewhere.example')

    def test_blank_key_keeps_existing_key_and_clear_is_explicit(self):
        before = self.configured()
        ai._save_configuration(ai.AIConfiguration(api_key='', model='another-model'))
        self.assertEqual(ai.config()['api_key'], before['api_key'])
        with self.assertRaises(ValueError):
            ai.AIConfiguration(api_key=KEY, clear_key=True)
        ai._save_configuration(ai.AIConfiguration(clear_key=True))
        self.assertEqual(ai.config()['api_key'], '')

    def test_verification_is_bound_to_full_configuration_and_revision(self):
        current = self.configured()
        signature = ai._signature(current)
        ai._mark_verified(self.principal, signature)
        self.assertTrue(ai.status()['online_verified'])
        ai._save_configuration(ai.AIConfiguration(model='changed-model'))
        with self.assertRaises(ai.AIError) as caught:
            ai._mark_verified(self.principal, signature)
        self.assertEqual(caught.exception.code, 'AI_CONFIG_CHANGED')
        self.assertFalse(ai.status()['online_verified'])

    def test_legacy_configuration_is_usable_but_unbound_verification_is_reset(self):
        path = ai.config_path(create=True)
        ai.write_bytes(path, json.dumps({'api_key': KEY, 'model': 'legacy-model', 'base_url': 'https://example.com', 'online_verified': True}).encode())
        self.assertEqual(ai.config()['model'], 'legacy-model')
        self.assertFalse(ai.status()['online_verified'])
        ai._mark_verified(self.principal, ai._signature(ai.config()))
        self.assertTrue(ai.status()['online_verified'])

    def test_invalid_secret_input_never_appears_in_validation_response(self):
        app = FastAPI(); app.include_router(ai.router)
        with TestClient(app) as client:
            for body in [{'api_key': {'nested': KEY}}, {'api_key': KEY, 'clear_key': True},
                         {'api_key': KEY, 'unknown': 'bad'}, {'api_key': KEY, 'clear_key': 'true'}]:
                result = client.post('/ai/config', json=body)
                self.assertEqual(result.status_code, 422)
                self.assertNotIn(KEY, result.text)
                self.assertNotIn('input', result.text)
            duplicate = client.post('/ai/config', content='{"api_key":"' + KEY + '","api_key":"another-value"}', headers={'Content-Type': 'application/json'})
            self.assertEqual(duplicate.status_code, 422)
            oversized = client.post('/ai/config', content='x' * (ai.MAX_CONFIG_BYTES + 1), headers={'Content-Type': 'application/json'})
            self.assertEqual(oversized.status_code, 413)

    @unittest.skipUnless('fork' in multiprocessing.get_all_start_methods(), 'Linux process lock test')
    def test_configuration_writes_serialize_across_processes_and_preserve_latest_key(self):
        self.configured()
        context = multiprocessing.get_context('fork')
        entered, finished = context.Event(), context.Event()
        process = context.Process(target=child_save, args=(entered, finished))
        try:
            with ai._config_lock():
                process.start()
                self.assertTrue(entered.wait(2))
                self.assertFalse(finished.wait(.1))
                value = ai.config()
                value.update(api_key='newest-synthetic-secret', revision=uuid.uuid4().hex)
                ai.write_bytes(ai.config_path(create=True), ai._json(value))
            process.join(3)
            self.assertEqual(process.exitcode, 0)
            self.assertTrue(finished.is_set())
            self.assertEqual(ai.config()['api_key'], 'newest-synthetic-secret')
            self.assertEqual(ai.config()['model'], 'concurrent-model')
        finally:
            if process.is_alive():
                process.terminate(); process.join(2)


class CompletionTests(Fixture, unittest.IsolatedAsyncioTestCase):
    async def test_success_preserves_chat_contract_and_json_mode(self):
        self.configured()
        seen = []
        async def handle(request):
            seen.append(request)
            return response()
        with self.mock_network(handle):
            self.assertEqual(await ai.complete(MESSAGES, json_mode=True), '测试成功。')
        self.assertEqual(str(seen[0].url), 'https://api.deepseek.com/chat/completions')
        self.assertEqual(seen[0].headers['Authorization'], 'Bearer ' + KEY)
        body = json.loads(seen[0].content)
        self.assertEqual(body['response_format'], {'type': 'json_object'})
        self.assertEqual(body['messages'], MESSAGES)

    async def test_reviewer_and_readonly_assistance_do_not_require_write_access(self):
        self.configured()
        with use_context(replace(self.principal, role='reviewer', readonly=True)), self.mock_network(lambda _: response()):
            self.assertEqual(await ai.complete(MESSAGES), '测试成功。')

    async def test_account_revocation_during_network_discards_result(self):
        self.configured()
        async def handle(_):
            self.disabled = True
            return response('must not be returned')
        with self.mock_network(handle), self.assertRaises(HTTPException) as caught:
            await ai.complete(MESSAGES)
        self.assertEqual(caught.exception.status_code, 401)

    async def test_upstream_failure_cannot_hide_account_revocation(self):
        self.configured()
        for failure in ('timeout', 'connection', 'rejected', 'malformed'):
            self.disabled = False
            async def handle(_):
                self.disabled = True
                if failure == 'timeout':
                    raise httpx.ReadTimeout('synthetic timeout')
                if failure == 'connection':
                    raise httpx.ConnectError('synthetic unavailable')
                return response(raw=b'invalid', status=503 if failure == 'rejected' else 200)
            with self.subTest(failure=failure), self.mock_network(handle), self.assertRaises(HTTPException) as caught:
                await ai.complete(MESSAGES)
            self.assertEqual(caught.exception.status_code, 401)

    async def test_config_change_during_network_discards_result(self):
        self.configured()
        async def handle(_):
            ai._save_configuration(ai.AIConfiguration(model='changed-during-call'))
            return response('must not be returned')
        with self.mock_network(handle), self.assertRaises(ai.AIError) as caught:
            await ai.complete(MESSAGES)
        self.assertEqual(caught.exception.code, 'AI_CONFIG_CHANGED')

    async def test_connection_test_cannot_mark_new_config_verified(self):
        self.configured()
        async def handle(_):
            ai._save_configuration(ai.AIConfiguration(model='new-not-tested'))
            return response()
        with self.mock_network(handle), self.assertRaises(HTTPException) as caught:
            await ai.test()
        self.assertEqual(caught.exception.status_code, 409)
        self.assertFalse(ai.status()['online_verified'])

    async def test_connection_test_marks_only_successful_configuration_verified(self):
        self.configured()
        with self.mock_network(lambda _: response()):
            self.assertTrue((await ai.test())['ok'])
        self.assertTrue(ai.status()['online_verified'])

    async def test_timeout_error_is_safe_and_does_not_claim_a_saved_task(self):
        self.configured()
        async def handle(_):
            raise httpx.ReadTimeout('unsafe-provider-detail-' + KEY)
        with self.mock_network(handle), self.assertRaises(ai.AIError) as caught:
            await ai.complete(MESSAGES)
        self.assertEqual(caught.exception.code, 'AI_TIMEOUT')
        self.assertNotIn('已保存', str(caught.exception))
        self.assertNotIn(KEY, str(caught.exception))

    async def test_whole_call_timeout_covers_slow_provider(self):
        self.configured()
        async def handle(_):
            await asyncio.sleep(10)
            return response()
        with self.mock_network(handle), patch.object(ai, 'COMPLETION_TIMEOUT', .01), self.assertRaises(ai.AIError) as caught:
            await ai.complete(MESSAGES)
        self.assertEqual(caught.exception.code, 'AI_TIMEOUT')

    async def test_provider_errors_and_redirects_never_echo_body_or_follow_location(self):
        self.configured()
        for status in (302, 401, 403, 500):
            received = []
            def handle(request):
                received.append(request)
                return response(raw=KEY.encode(), status=status, headers={'Location': 'https://other.example/'})
            with self.subTest(status=status), self.mock_network(handle), self.assertRaises(ai.AIError) as caught:
                await ai.complete(MESSAGES)
            self.assertEqual(caught.exception.code, 'AI_PROVIDER_REJECTED')
            self.assertEqual(len(received), 1)
            self.assertNotIn(KEY, str(caught.exception))

    async def test_response_limit_is_enforced_while_streaming_and_closes_response(self):
        self.configured()
        received = response(raw=b'x' * (ai.MAX_RESPONSE_BYTES + 100_000))
        with self.mock_network(lambda _: received), self.assertRaises(ai.AIError) as caught:
            await ai.complete(MESSAGES)
        self.assertEqual(caught.exception.code, 'AI_RESPONSE_LIMIT')
        self.assertLessEqual(received.test_stream.consumed, ai.MAX_RESPONSE_BYTES + 65536)
        self.assertTrue(received.test_stream.closed)

    async def test_declared_response_size_rejects_before_reading(self):
        self.configured()
        received = response(headers={'Content-Length': str(ai.MAX_RESPONSE_BYTES + 1)})
        with self.mock_network(lambda _: received), self.assertRaises(ai.AIError) as caught:
            await ai.complete(MESSAGES)
        self.assertEqual(caught.exception.code, 'AI_RESPONSE_LIMIT')
        self.assertEqual(received.test_stream.consumed, 0)

    async def test_malformed_compressed_truncated_and_secret_echo_responses_fail_closed(self):
        self.configured()
        cases = [response(raw=b'{"choices":[],"choices":[]}'), response(raw=b'NaN'),
                 response(raw=b'{invalid-json'), response(raw=b'{"choices":{}}'),
                 response(content={'unexpected': 'object'}), response(content=''),
                 response(content=KEY), response(headers={'Content-Encoding': 'gzip'}),
                 response(finish='length'), response(content='x' * (ai.MAX_CONTENT_BYTES + 1))]
        for index, received in enumerate(cases):
            with self.subTest(index=index), self.mock_network(lambda _: received), self.assertRaises(ai.AIError) as caught:
                await ai.complete(MESSAGES)
            self.assertNotIn(KEY, str(caught.exception))
            self.assertIn(caught.exception.code, {'AI_RESPONSE_INVALID', 'AI_OUTPUT_INCOMPLETE', 'AI_RESPONSE_LIMIT'})

    async def test_invalid_or_oversize_input_never_reaches_provider(self):
        self.configured()
        for messages in [[], [{'role': 'tool', 'content': 'x'}], [{'role': 'user', 'content': 'x' * ai.MAX_REQUEST_BYTES}]]:
            with self.subTest(size=len(str(messages))), self.mock_network(lambda _: self.fail('Network must not be called')), self.assertRaises(ai.AIError) as caught:
                await ai.complete(messages)
            self.assertEqual(caught.exception.code, 'AI_INPUT_INVALID')


class EndpointAuthorizationTests(Fixture, unittest.IsolatedAsyncioTestCase):
    """Real API response boundaries; only source storage/model transport are mocked."""
    def setUp(self):
        super().setUp()
        self.configured()
        self.app = FastAPI()
        for router in (ai.router, authority.router, projects.router):
            self.app.include_router(router)
        self.citation = {'id': 'synthetic-rule', 'title': 'PRIVATE-CITATION-TITLE', 'kind': 'rule',
                         '_excerpt': 'PRIVATE-CITATION-TEXT', 'source_kind': 'user', 'status': 'published'}
        self.local = {'title': 'PRIVATE-LOCAL-DRAFT', 'summary': 'PRIVATE-DRAFT-SUMMARY',
                      'spec': authority.Spec(type='position', purpose='PRIVATE-SOURCE-TEXT').model_dump(), 'warnings': []}
        self.stack.enter_context(patch.object(ai, 'retrieve', return_value=[self.citation]))
        self.stack.enter_context(patch.object(authority, 'connect', return_value=MagicMock()))
        self.stack.enter_context(patch.object(authority, 'build_local_proposal', return_value=self.local))
        self.stack.enter_context(patch.object(authority, 'validate_refs', side_effect=lambda _, spec: spec))
        self.stack.enter_context(patch.object(authority, 'checks_for', return_value={'errors': [], 'warnings': []}))
        self.stack.enter_context(patch.object(projects, 'workspace', return_value={
            'entity': {'title': 'PRIVATE-PROJECT', 'summary': 'PRIVATE-PROJECT-SUMMARY', 'version': 1,
                       'payload': {'objective': 'PRIVATE-OBJECTIVE'}}, 'tasks': []}))
        self.endpoints = [('/ai/ask', {'question': '合成测试问题', 'mode': 'online'}),
                          ('/authority/assist', {'type': 'position', 'text': '合成测试职责材料'}),
                          ('/projects/synthetic-project/plan', {'instruction': '合成测试任务要求'})]

    async def post(self, path, body):
        async with HTTP_CLIENT(transport=httpx.ASGITransport(app=self.app), base_url='http://local-test') as client:
            return await client.post(path, json=body)

    def denied_without_assets(self, result, status):
        self.assertEqual(result.status_code, status, result.text)
        self.assertEqual(set(result.json()), {'detail'})
        for marker in ('PRIVATE-', 'citations', 'tasks', 'spec', 'local_fallback'):
            self.assertNotIn(marker, result.text)

    async def test_complete_401_and_403_are_preserved_by_all_response_only_endpoints(self):
        for status in (401, 403):
            for path, body in self.endpoints:
                with self.subTest(status=status, endpoint=path), patch.object(ai, 'complete', new=AsyncMock(side_effect=HTTPException(status, '授权已失效。'))):
                    self.denied_without_assets(await self.post(path, body), status)

    async def test_provider_timeout_after_revocation_returns_no_citations_or_local_draft(self):
        for path, body in self.endpoints:
            self.disabled = False
            async def handle(_):
                self.disabled = True
                raise httpx.ReadTimeout('synthetic upstream timeout')
            with self.subTest(endpoint=path), self.mock_network(handle):
                self.denied_without_assets(await self.post(path, body), 401)

    async def test_successful_complete_still_requires_current_identity_at_response_boundary(self):
        result = json.dumps({'tasks': [{'title': 'PRIVATE-TASK', 'task_type': 'rule', 'instruction': 'PRIVATE-TASK-INSTRUCTION'}],
                             'title': 'PRIVATE-ONLINE-DRAFT', 'spec': self.local['spec']})
        for path, body in self.endpoints:
            self.disabled = False
            async def revoke_then_return(*_, **__):
                self.disabled = True
                return result
            with self.subTest(endpoint=path), patch.object(ai, 'complete', new=AsyncMock(side_effect=revoke_then_return)):
                self.denied_without_assets(await self.post(path, body), 401)

    async def test_local_retrieval_checks_identity_after_reading_evidence(self):
        def revoked_retrieval(*_, **__):
            self.disabled = True
            return [self.citation]
        with patch.object(ai, 'retrieve', side_effect=revoked_retrieval):
            self.denied_without_assets(await self.post('/ai/ask', {'question': '合成检索', 'mode': 'retrieval'}), 401)

    async def test_unconfigured_local_authority_draft_checks_identity_before_returning(self):
        ai._save_configuration(ai.AIConfiguration(clear_key=True))
        def revoked_local_builder(*_, **__):
            self.disabled = True
            return self.local
        with patch.object(authority, 'build_local_proposal', side_effect=revoked_local_builder):
            self.denied_without_assets(await self.post('/authority/assist', {'type': 'position', 'text': '合成岗位说明'}), 401)

    async def test_authorized_upstream_failure_keeps_explicit_local_fallback(self):
        async def unavailable(_):
            raise httpx.ConnectError('synthetic unavailable')
        with self.mock_network(unavailable):
            answer = await self.post(*self.endpoints[0])
            self.assertEqual(answer.status_code, 200)
            self.assertEqual(answer.json()['source'], 'retrieval')
            self.assertEqual(answer.json()['citations'][0]['excerpt'], 'PRIVATE-CITATION-TEXT')
            draft = await self.post(*self.endpoints[1])
            self.assertEqual(draft.status_code, 200)
            self.assertEqual(draft.json()['mode'], 'local_fallback')
            plan = await self.post(*self.endpoints[2])
            self.assertEqual(plan.status_code, 502)


if __name__ == '__main__':
    unittest.main()
