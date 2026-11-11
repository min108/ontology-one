"""Legacy feature compatibility through the controlled official Harness task.

Synthetic adapter fixtures are used; native SDK/worker contracts are tested in
separate suites. These tests verify config, authorization and response boundaries.
"""
import asyncio
from contextlib import ExitStack
from dataclasses import replace
import inspect
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

from backend import ai, authority, projects, skill_harness
from backend.security.context import Principal, use_context
from backend.storage import scoped_path

KEY = 'synthetic-test-key-not-a-real-credential'
MESSAGES = [{'role': 'user', 'content': '合成样例，仅回复测试成功。'}]
HTTP_CLIENT = httpx.AsyncClient
AUTHORITY_CHECKS = authority.checks_for


def child_save(entered, finished):
    entered.set()
    ai._save_configuration(ai.AIConfiguration(model='concurrent-model'))
    finished.set()


class Fixture:
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='ai-harness-unit-')
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
        self.stack.enter_context(patch('backend.skill_harness.assert_principal_current', side_effect=validate))
        self.audit = self.stack.enter_context(patch('backend.security.store.append_audit'))

    def tearDown(self):
        self.stack.close()
        self.temporary.cleanup()

    def configured(self):
        ai._save_configuration(ai.AIConfiguration(api_key=KEY))
        return ai.config()

    def mock_network(self, handler):
        # Endpoint authorization tests retain a synthetic failure callback. No
        # HTTP client or provider transport exists in the compatibility layer.
        async def invoke(*args, **kwargs):
            result = handler(None)
            return await result if inspect.isawaitable(result) else result
        return patch('backend.ai_harness.complete_task', side_effect=invoke)


class ConfigurationTests(Fixture, unittest.TestCase):
    def test_shared_config_status_is_safe_and_preserves_legacy_fields(self):
        self.configured()
        self.assertEqual(ai.config_path(), skill_harness._config_path())
        shared = skill_harness._configuration()
        self.assertEqual(shared['api_key'], KEY)
        self.assertEqual(ai.status()['base_url'], skill_harness.DEFAULT_BASE_URL)
        self.assertEqual(ai.status()['model'], skill_harness.DEFAULT_MODEL)
        self.assertTrue(ai.status()['configured'])
        self.assertFalse(ai.status()['online_verified'])
        self.assertNotIn(KEY, json.dumps(ai.status()))
        self.assertFalse(ai.status()['migration_required'])
        self.assertEqual(self.audit.call_args.args[1], 'harness_configured')

    def test_legacy_openai_config_is_detected_without_being_reused(self):
        legacy = scoped_path('data/runtime/ai.json', allowed=('data/runtime',), create_parent=True)
        ai.write_bytes(legacy, json.dumps({'api_key': KEY, 'base_url': 'https://legacy.example', 'model': 'old-model'}).encode())
        self.assertFalse(ai.status()['configured'])
        self.assertTrue(ai.status()['migration_required'])
        self.assertIn('Messages', ai.status()['configuration_notice'])
        self.assertNotIn(KEY, json.dumps(ai.status()))
        self.configured()
        self.assertFalse(ai.status()['migration_required'])

    def test_strict_url_model_and_secret_validation(self):
        for value in ['http://example.com', 'https://user:pass@example.com', 'https://example.com?key=x',
                      'https://example.com/#x', 'https://example.com\\x', 'https://example.com/%2e',
                      'https://example.com/../x', 'https://example.com:0', 'https://example.com:65536',
                      'https://example.com:', 'https://bad..host', 'https://example.com\n',
                      'https://例子.公司', 'https:///missing', 'https://example.com/?']:
            with self.subTest(value=value), self.assertRaises(ValueError): ai.AIConfiguration(base_url=value)
        self.assertEqual(ai.AIConfiguration(base_url='https://EXAMPLE.com:443/v1/').base_url, 'https://example.com/v1')
        for value in [True, 3, [], 'bad model', '', '../model?key=x']:
            with self.subTest(value=value), self.assertRaises(ValueError): ai.AIConfiguration(model=value)
        for value in [True, 123, {}, 'short', 'spaces are invalid', 'has\nnewline', '非ASCII密钥字符串']:
            with self.subTest(value=value), self.assertRaises(ValueError): ai.AIConfiguration(api_key=value)

    def test_endpoint_change_requires_explicit_new_key_or_clear(self):
        before = self.configured()
        with self.assertRaises(HTTPException): ai._save_configuration(ai.AIConfiguration(base_url='https://elsewhere.example'))
        self.assertEqual(ai.config(), before)
        ai._save_configuration(ai.AIConfiguration(base_url='https://elsewhere.example', clear_key=True))
        self.assertFalse(ai.status()['configured'])
        ai._save_configuration(ai.AIConfiguration(api_key='new-synthetic-endpoint-key'))
        self.assertEqual(skill_harness._configuration()['base_url'], 'https://elsewhere.example')

    def test_blank_key_keeps_and_clear_removes_shared_key(self):
        before = self.configured()
        ai._save_configuration(ai.AIConfiguration(api_key='', model='another-model'))
        self.assertEqual(ai.config()['api_key'], before['api_key'])
        with self.assertRaises(ValueError): ai.AIConfiguration(api_key=KEY, clear_key=True)
        ai._save_configuration(ai.AIConfiguration(clear_key=True))
        self.assertEqual(skill_harness._configuration()['api_key'], '')

    def test_verification_binds_full_configuration_and_updates_shared_status(self):
        current = self.configured(); signature = ai._signature(current)
        ai._mark_verified(self.principal, signature)
        self.assertTrue(ai.status()['online_verified'])
        self.assertTrue(skill_harness._configuration()['verified'])
        ai._save_configuration(ai.AIConfiguration(model='changed-model'))
        with self.assertRaises(ai.AIError) as caught: ai._mark_verified(self.principal, signature)
        self.assertEqual(caught.exception.code, 'AI_CONFIG_CHANGED')
        self.assertFalse(skill_harness._configuration()['verified'])

    def test_invalid_config_input_never_echoes_secret(self):
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
    def test_config_writes_share_the_harness_cross_process_lock(self):
        self.configured(); context = multiprocessing.get_context('fork')
        entered, finished = context.Event(), context.Event()
        process = context.Process(target=child_save, args=(entered, finished))
        try:
            with skill_harness._config_lock():
                process.start(); self.assertTrue(entered.wait(2)); self.assertFalse(finished.wait(.1))
                value = skill_harness._configuration()
                value.update(api_key='newest-synthetic-secret', revision=uuid.uuid4().hex)
                ai.write_bytes(ai.config_path(create=True), ai._json(value))
            process.join(3)
            self.assertEqual(process.exitcode, 0)
            self.assertTrue(finished.is_set())
            self.assertEqual(ai.config()['api_key'], 'newest-synthetic-secret')
            self.assertEqual(ai.config()['model'], 'concurrent-model')
        finally:
            if process.is_alive(): process.terminate(); process.join(2)


class CompletionTests(Fixture, unittest.IsolatedAsyncioTestCase):
    async def test_delegates_only_to_official_governed_task_contract(self):
        self.configured()
        with patch('backend.ai_harness.complete_task', new=AsyncMock(return_value='{"结果":"成功"}')) as run:
            self.assertEqual(await ai.complete(MESSAGES, json_mode=True), '{"结果":"成功"}')
        run.assert_awaited_once_with(self.principal, MESSAGES, json_mode=True)

    async def test_reviewer_readonly_assistance_does_not_require_write(self):
        self.configured()
        with use_context(replace(self.principal, role='reviewer', readonly=True)), patch('backend.ai_harness.complete_task', new=AsyncMock(return_value='成功')):
            self.assertEqual(await ai.complete(MESSAGES), '成功')

    async def test_identity_revocation_discards_both_success_and_failure(self):
        self.configured()
        for error in (None, HTTPException(502, {'code': 'HARNESS_PROVIDER_UNAVAILABLE'}), RuntimeError(KEY)):
            self.disabled = False
            async def run(*args, **kwargs):
                self.disabled = True
                if error is not None: raise error
                return 'must not return'
            with self.subTest(error=type(error).__name__), patch('backend.ai_harness.complete_task', side_effect=run), self.assertRaises(HTTPException) as caught:
                await ai.complete(MESSAGES)
            self.assertEqual(caught.exception.status_code, 401)

    async def test_config_changes_discard_returned_text(self):
        self.configured()
        async def run(*args, **kwargs):
            ai._save_configuration(ai.AIConfiguration(model='changed-during-call'))
            return 'must not return'
        with patch('backend.ai_harness.complete_task', side_effect=run), self.assertRaises(ai.AIError) as caught:
            await ai.complete(MESSAGES)
        self.assertEqual(caught.exception.code, 'AI_CONFIG_CHANGED')

    async def test_connection_test_marks_only_its_exact_config(self):
        self.configured()
        async def changed(*args, **kwargs):
            ai._save_configuration(ai.AIConfiguration(model='new-not-tested'))
            return '成功'
        with patch('backend.ai_harness.complete_task', side_effect=changed), self.assertRaises(HTTPException) as caught:
            await ai.test()
        self.assertEqual(caught.exception.status_code, 409)
        self.assertFalse(ai.status()['online_verified'])
        with patch('backend.ai_harness.complete_task', new=AsyncMock(return_value='成功')):
            self.assertTrue((await ai.test())['ok'])
        self.assertTrue(skill_harness._configuration()['verified'])

    async def test_timeout_and_cancellation_reap_adapter_task(self):
        self.configured(); completed = asyncio.Event()
        async def slow(*args, **kwargs):
            try: await asyncio.Event().wait()
            finally: completed.set()
        with patch('backend.ai_harness.complete_task', side_effect=slow), patch.object(ai, 'COMPLETION_TIMEOUT', .01), self.assertRaises(ai.AIError) as caught:
            await ai.complete(MESSAGES)
        self.assertEqual(caught.exception.code, 'AI_TIMEOUT'); self.assertTrue(completed.is_set())
        completed.clear()
        with patch('backend.ai_harness.complete_task', side_effect=slow):
            task = asyncio.create_task(ai.complete(MESSAGES)); await asyncio.sleep(.02); task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
        self.assertTrue(completed.is_set())

    async def test_safe_runtime_errors_are_mapped_without_raw_details(self):
        self.configured()
        for code, expected in [('HARNESS_TIMEOUT', 'AI_TIMEOUT'), ('HARNESS_PROVIDER_AUTH', 'AI_PROVIDER_REJECTED'),
                               ('HARNESS_PROVIDER_QUOTA', 'AI_PROVIDER_REJECTED'), ('HARNESS_PROVIDER_UNAVAILABLE', 'AI_CONNECTION_FAILED'),
                               ('HARNESS_OUTPUT_LIMIT', 'AI_OUTPUT_INCOMPLETE'), ('HARNESS_BUSY', 'AI_BUSY'),
                               ('HARNESS_PROVIDER_RATE_LIMIT', 'AI_RATE_LIMIT'), ('HARNESS_UNAVAILABLE', 'AI_RUNTIME_UNAVAILABLE'),
                               ('HARNESS_FAILED', 'AI_RESPONSE_INVALID')]:
            with self.subTest(code=code), patch('backend.ai_harness.complete_task', new=AsyncMock(side_effect=HTTPException(502, {'code': code, 'unsafe': KEY}))), self.assertRaises(ai.AIError) as caught:
                await ai.complete(MESSAGES)
            self.assertEqual(caught.exception.code, expected); self.assertNotIn(KEY, str(caught.exception))

    async def test_response_must_be_bounded_text_and_json_mode_requires_object(self):
        self.configured()
        for value, json_mode in [('',False), (KEY,False), ({'unexpected':'object'},False),
                                 ('x' * (ai.MAX_CONTENT_BYTES + 1),False), ('[1,2]',True), ('{"x":1,"x":2}',True), ('NaN',True)]:
            with self.subTest(kind=type(value).__name__, json_mode=json_mode), patch('backend.ai_harness.complete_task', new=AsyncMock(return_value=value)), self.assertRaises(ai.AIError):
                await ai.complete(MESSAGES, json_mode=json_mode)

    async def test_invalid_input_is_rejected_before_task_creation(self):
        self.configured()
        for messages in [[], [{'role':'tool','content':'x'}], [{'role':'user','content':'x' * ai.MAX_REQUEST_BYTES}]]:
            with self.subTest(size=len(str(messages))), patch('backend.ai_harness.complete_task', new=AsyncMock()) as run, self.assertRaises(ai.AIError) as caught:
                await ai.complete(messages)
            self.assertEqual(caught.exception.code, 'AI_INPUT_INVALID'); run.assert_not_awaited()


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

    async def test_project_plan_receives_existing_task_scope_beyond_its_title(self):
        scope = '仅核对金额单位、实际交付日期与验收日期，形成字段映射；批准权限另待核。'
        legacy_scope = '保留原文出处和待核事项，不重复编制已有字段映射。'
        workspace = {'entity': {'title': '合成专题', 'summary': '合成范围', 'version': 3, 'payload': {}},
                     'tasks': [
                         {'title': '核对口径', 'summary': '简短摘要不足以界定办理范围',
                          'payload': {'task_type': 'dictionary', 'instruction': scope}},
                         {'title': '历史任务', 'summary': legacy_scope, 'payload': {'task_type': 'material'}}]}
        proposal = {'tasks': [{'title': '核对批准权限依据', 'task_type': 'rule',
                              'instruction': '依据原文定位批准权限；材料不足项保留待核。'}]}
        with patch.object(projects, 'workspace', return_value=workspace), \
                patch.object(ai, 'complete', new=AsyncMock(return_value=json.dumps(proposal))) as complete:
            response = await self.post('/projects/synthetic-project/plan', {'instruction': '只补充独立缺口'})
        self.assertEqual(response.status_code, 200, response.text)
        context = json.loads(complete.await_args.args[0][1]['content'])
        self.assertEqual([task['instruction'] for task in context['现有任务']], [scope, legacy_scope])
        self.assertEqual(response.json()['project_version'], 3)
        self.assertTrue(response.json()['requires_review'])

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

    def workflow_contract(self):
        return authority.Spec(type='workflow', purpose='合成采购付款流程', scope='仅本次虚构材料',
            basis='仅据本次合成描述，正式依据待核', org_ids=['synthetic-org'], nodes=[
                {'id': 'start', 'kind': 'start', 'title': '开始'},
                {'id': 'check', 'kind': 'decision', 'title': '材料齐备吗', 'role': '财务复核', 'responsibility': '复核材料'},
                {'id': 'correct', 'kind': 'step', 'title': '补正材料', 'role': '经办人', 'responsibility': '补正后重新复核'},
                {'id': 'end', 'kind': 'end', 'title': '结束'}], edges=[
                {'id': 'e1', 'source': 'start', 'target': 'check'},
                {'id': 'e2', 'source': 'check', 'target': 'correct', 'label': '材料不齐'},
                {'id': 'e3', 'source': 'correct', 'target': 'check', 'label': '补正完成后重新复核'},
                {'id': 'e4', 'source': 'check', 'target': 'end', 'label': '材料齐备'}]).model_dump()

    async def test_authority_rejects_real_from_to_failure_without_exposing_model_validation_input(self):
        spec = self.workflow_contract()
        for edge in spec['edges']:
            edge['from'] = edge.pop('source')
            edge['to'] = edge.pop('target')
        marker = 'SYNTHETIC_PRIVATE_MODEL_VALUE_' + KEY
        spec['edges'][0]['from'] = marker
        reply = json.dumps({'title': '合成草稿', 'spec': spec, 'warnings': []})
        with (patch.object(ai, 'complete', new=AsyncMock(return_value=reply)),
              patch.object(authority, 'get_entity', return_value={'id': 'synthetic-org', 'title': '合成机构'})):
            result = await self.post('/authority/assist', {'type': 'workflow', 'text': '合成材料不齐退回补正后重新复核。', 'org_ids': ['synthetic-org']})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['mode'], 'local_fallback')
        self.assertEqual(result.json()['fallback_code'], 'AUTHORITY_MODEL_OUTPUT_INVALID')
        for value in (marker, KEY, 'ValidationError', 'input_value', 'Field required'):
            self.assertNotIn(value, result.text)

    async def test_authority_sends_actual_edge_schema_and_accepts_valid_conditional_loop_online(self):
        spec = self.workflow_contract()
        self.assertEqual(AUTHORITY_CHECKS(spec)['errors'], [])
        observed = {}
        async def complete(messages, json_mode=False):
            observed['payload'] = json.loads(messages[1]['content'])
            observed['prompt'] = messages[0]['content']
            self.assertTrue(json_mode)
            edge_schema = observed['payload']['spec_schema']['$defs']['Edge']
            self.assertTrue({'id', 'source', 'target'} <= set(edge_schema['required']))
            self.assertIn('label', edge_schema['properties'])
            return json.dumps({'title': '合成条件回路', 'summary': '补正后重新复核', 'spec': spec, 'warnings': []})
        with (patch.object(ai, 'complete', side_effect=complete),
              patch.object(authority, 'get_entity', return_value={'id': 'synthetic-org', 'title': '合成机构'}),
              patch.object(authority, 'checks_for', side_effect=AUTHORITY_CHECKS)):
            result = await self.post('/authority/assist', {'type': 'workflow', 'text': '合成材料不齐退回补正后重新复核。', 'org_ids': ['synthetic-org']})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['mode'], 'online')
        self.assertEqual(result.json()['spec']['edges'], spec['edges'])
        self.assertNotIn('fallback_code', result.json())


if __name__ == '__main__':
    unittest.main()
