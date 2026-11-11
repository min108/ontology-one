"""Public progress streaming keeps runs independent of their page viewers."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from backend.agent_runtime import MAX_PUBLIC_EVENTS, SessionStore
from backend.security.context import Principal


def owned_principal():
    return Principal('one', 'synthetic-account', 'a', 'a', 'modeler', False, 'login', 1, 'synthetic_a')


class AgentPublicProgressTests(unittest.TestCase):
    def test_public_events_cannot_echo_provider_messages_or_raw_results(self):
        store = SessionStore()
        run = {'status': 'running'}
        store.progress(run, {'stage': 'model', 'message': 'synthetic-private-reasoning',
                             'text': 'synthetic-private-output', 'rows': [{'secret': 'synthetic'}]})
        store.progress(run, {'stage': 'untrusted-stage', 'message': 'synthetic-private'})
        store.progress(run, {'stage': 'model', 'message': 'second-message'})
        self.assertEqual(len(run['events']), 1)
        self.assertEqual(set(run['events'][0]), {'seq', 'type', 'stage', 'message', 'created_at'})
        self.assertNotIn('synthetic', str(run))
        self.assertEqual(run['stage'], 'model')

    def test_event_buffer_is_bounded_and_terminal_state_cannot_be_reopened(self):
        store = SessionStore()
        run = {'status': 'running'}
        for i in range(300):
            store.progress(run, {'stage': 'model' if i % 2 else 'checking'})
        self.assertEqual(len(run['events']), MAX_PUBLIC_EVENTS)
        self.assertEqual(run['events'][-1]['seq'], 300)
        run['status'] = 'completed'
        store.progress(run, {'stage': 'completed'})
        store.progress(run, {'stage': 'model'})
        self.assertEqual(run['events'][-1]['stage'], 'completed')
        self.assertEqual(run['status'], 'completed')

    def test_global_list_only_returns_current_login_workspace_metadata(self):
        principal = SimpleNamespace(user_id='one', session_id='login', tenant_id='a')
        store = SessionStore()
        with patch('backend.agent_runtime.current', return_value=principal):
            session = store.create(principal, '本人的业务问题')
            session['runs']['a' * 32] = {'id': 'a' * 32, 'status': 'running', 'created_at': '2026-11-11',
                                         'answer': 'synthetic-private-answer', 'stage': 'checking'}
            other = store.create(SimpleNamespace(user_id='two', session_id='login', tenant_id='a'))
            other['runs']['b' * 32] = {'id': 'b' * 32, 'status': 'running', 'created_at': '2026-11-11'}
            value = store.runs(principal)
        self.assertEqual(len(value), 1)
        self.assertEqual(value[0]['href'], '/applications?session=' + session['id'] + '&run=' + 'a' * 32)
        self.assertNotIn('answer', value[0])
        self.assertNotIn('synthetic-private-answer', str(value))


class AgentStreamLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from backend import agent_service
        self.service = agent_service
        self.principal = SimpleNamespace(user_id='one', session_id='login', tenant_id='a')
        self.store = SessionStore()
        self.auth = patch('backend.agent_runtime.current', return_value=self.principal)
        self.auth.start()
        self.session = self.store.create(self.principal)
        self.worker = asyncio.create_task(asyncio.Event().wait())
        self.run = {'id': 'a' * 32, 'status': 'running', 'created_at': '2026-11-11', 'partial': False,
                    'tools': [], 'provenance': [], 'cancelled': __import__('threading').Event(),
                    'task': self.worker, 'bridge': None}
        self.session['runs'][self.run['id']] = self.run
        self.session['active_run_id'] = self.run['id']
        self.store.progress(self.run, {'stage': 'model'})
        self.request = SimpleNamespace(is_disconnected=AsyncMock(return_value=False))

    async def asyncTearDown(self):
        await self.store.close()
        self.auth.stop()

    async def test_closing_progress_viewer_does_not_cancel_background_run(self):
        with patch.object(self.service, 'SESSIONS', self.store), \
             patch.object(self.service, 'principal', return_value=self.principal), \
             patch.object(self.service, 'current', return_value=self.principal), \
             patch.object(self.store, 'validate', new=AsyncMock()):
            response = await self.service.events(self.session['id'], self.run['id'], self.request)
            frame = await anext(response.body_iterator)
            self.assertIn('event: state', frame)
            self.assertIn('正在理解问题', frame)
            await response.body_iterator.aclose()
        self.assertFalse(self.worker.done())
        self.assertFalse(self.run['cancelled'].is_set())
        self.assertEqual(self.run['status'], 'running')
        self.assertEqual(response.headers['cache-control'], 'no-store')

    async def test_source_revocation_resets_view_without_replaying_answer(self):
        self.run['answer'] = 'synthetic-private-answer'
        checks = AsyncMock(side_effect=[None, HTTPException(409, 'synthetic-detail')])
        with patch.object(self.service, 'SESSIONS', self.store), \
             patch.object(self.service, 'principal', return_value=self.principal), \
             patch.object(self.service, 'current', return_value=self.principal), \
             patch.object(self.store, 'validate', new=checks):
            response = await self.service.events(self.session['id'], self.run['id'], self.request)
            frame = await anext(response.body_iterator)
            self.assertIn('event: reset', frame)
            self.assertNotIn('synthetic', frame)
            with self.assertRaises(StopAsyncIteration):
                await anext(response.body_iterator)
        self.assertFalse(self.worker.done())

    async def test_partial_text_is_private_until_current_sources_and_config_are_checked(self):
        self.store.receive(self.run, {'type': 'reasoning_delta', 'text': 'synthetic-private-thought'})
        self.store.receive(self.run, {'type': 'answer_delta', 'text': '第一段', 'message_id': 'answer-1'})
        self.store.receive(self.run, {'type': 'answer_delta', 'text': '，继续生成', 'message_id': 'answer-1'})
        self.assertNotIn('partial_answer', self.store.run_view(self.run))
        self.assertNotIn('synthetic-private', str(self.run))
        self.run['_configuration_signature'] = b'current-config'
        with patch('backend.agent_runtime._configuration_signature', return_value=b'current-config'):
            await self.store.validate(self.session)
        self.assertEqual(self.store.run_view(self.run)['partial_answer'], '第一段，继续生成')
        self.store.receive(self.run, {'type': 'answer_delta', 'text': '新一段回答', 'message_id': 'answer-2'})
        self.assertNotIn('partial_answer', self.store.run_view(self.run))

    async def test_changed_configuration_clears_partial_and_cancels_owned_run(self):
        self.store.receive(self.run, {'type': 'answer_delta', 'text': 'synthetic-draft', 'message_id': 'answer-1'})
        self.run['partial_answer'] = 'synthetic-draft'
        self.run['_configuration_signature'] = b'previous-config'
        self.session['messages'].append({'role': 'assistant', 'content': 'synthetic-previous'})
        with patch('backend.agent_runtime._configuration_signature', return_value=b'changed-config'):
            with self.assertRaises(HTTPException) as error:
                await self.store.validate(self.session)
        self.assertEqual(error.exception.status_code, 409)
        self.assertNotIn('partial_answer', self.run)
        self.assertNotIn('_draft_answer', self.run)
        self.assertTrue(self.run['cancelled'].is_set())
        self.assertEqual(self.session['messages'], [])

    async def test_new_source_and_text_during_validation_wait_for_the_next_proof_batch(self):
        from backend.agent_tools import SessionToolBridge
        self.principal = owned_principal()
        bridge = SessionToolBridge(self.principal)
        bridge.asset_versions = {'previous-source': 1}
        self.run['bridge'] = bridge
        self.store.receive(self.run, {'type': 'answer_delta', 'text': '已核对的前半段', 'message_id': 'attempt-1'})
        snapshots = []

        async def validate(combined, **_):
            snapshots.append(dict(combined.asset_versions))
            if len(snapshots) == 1:
                await asyncio.sleep(0)
                bridge.asset_versions['new-source'] = 1
                self.store.receive(self.run, {'type': 'answer_delta', 'text': '新来源产生的后半段', 'message_id': 'attempt-1'})

        with patch.object(SessionToolBridge, 'validate_async', new=validate):
            await self.store.validate(self.session)
            self.assertEqual(self.store.run_view(self.run)['partial_answer'], '已核对的前半段')
            self.assertNotIn('new-source', snapshots[0])
            await self.store.validate(self.session)
            self.assertIn('new-source', snapshots[1])
            self.assertEqual(self.store.run_view(self.run)['partial_answer'], '已核对的前半段新来源产生的后半段')

    async def test_new_attempt_during_validation_never_replays_old_attempt_text(self):
        from backend.agent_tools import SessionToolBridge
        self.principal = owned_principal()
        self.run['bridge'] = SessionToolBridge(self.principal)
        self.store.receive(self.run, {'type': 'answer_delta', 'text': '旧一轮回答', 'message_id': 'attempt-1'})

        async def validate(*_, **__):
            await asyncio.sleep(0)
            self.store.receive(self.run, {'type': 'answer_delta', 'text': '新一轮回答', 'message_id': 'attempt-2'})

        with patch.object(SessionToolBridge, 'validate_async', new=validate):
            await self.store.validate(self.session)
        self.assertNotIn('partial_answer', self.store.run_view(self.run))


class AgentCapturedExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def exercise_real_channel_close(self, cancel=False):
        from backend.agent_tools import SessionToolBridge
        from backend.jobs import has_readonly_agent_execution
        principal, store, entered = owned_principal(), SessionStore(), asyncio.Event()
        channels, proof_scopes = [], []
        original_enter = SessionToolBridge.__enter__

        def enter(bridge):
            channels.append(bridge)
            return original_enter(bridge)

        async def execute(*_, **__):
            entered.set()
            if cancel:
                await asyncio.Event().wait()
            return {'text': '已完成的业务回答', 'usage': {}}

        async def validate(*_, **__):
            proof_scopes.append(has_readonly_agent_execution(principal))

        with patch('backend.agent_runtime.current', return_value=principal), \
             patch.object(store, 'validate', new=AsyncMock()), \
             patch('backend.agent_runtime._selected_assets', return_value=[]), \
             patch('backend.agent_runtime._configuration_signature', return_value=b'unchanged'), \
             patch('backend.agent_runtime.execute_controlled', new=execute), \
             patch.object(SessionToolBridge, '__enter__', new=enter), \
             patch.object(SessionToolBridge, 'validate_async', new=validate):
            session = store.create(principal)
            result = await store.start(principal, session, '业务问题', [], [], [])
            run = session['runs'][result['run_id']]
            await entered.wait()
            if cancel:
                await store.cancel(session, run)
            else:
                await run['task']
        self.assertTrue(channels[0].stopping.is_set())
        self.assertFalse(channels[0].thread.is_alive())
        self.assertIsNone(channels[0].server)
        if cancel:
            self.assertEqual(run['status'], 'cancelled')
            self.assertTrue(run['cancelled'].is_set())
            self.assertNotIn('answer', run)
        else:
            self.assertEqual(run['status'], 'completed')
            self.assertFalse(run['cancelled'].is_set())
            self.assertEqual(run['answer'], '已完成的业务回答')
            self.assertEqual(proof_scopes, [True, True])
        await store.close()

    async def test_real_tool_channel_cleanup_preserves_live_final_proof(self):
        await self.exercise_real_channel_close()

    async def test_explicit_run_cancel_still_invalidates_owned_scope(self):
        await self.exercise_real_channel_close(cancel=True)

    async def test_owned_execution_scope_expires_when_its_stored_run_is_removed(self):
        from backend.jobs import has_readonly_agent_execution
        principal = owned_principal()
        store, entered, release = SessionStore(), asyncio.Event(), asyncio.Event()
        observed = []

        async def execute(*_, **__):
            observed.append(has_readonly_agent_execution(principal))
            entered.set()
            await release.wait()
            with self.assertRaises(HTTPException) as error:
                has_readonly_agent_execution(principal)
            observed.append(error.exception.status_code)

        with patch('backend.agent_runtime.current', return_value=principal), \
             patch.object(store, 'validate', new=AsyncMock()), \
             patch.object(store, '_execute_owned', new=execute):
            session = store.create(principal)
            result = await store.start(principal, session, '业务问题', [], [], [])
            await entered.wait()
            store.sessions.pop(session['id'])
            release.set()
            await session['runs'][result['run_id']]['task']
        self.assertEqual(observed, [True, 409])
        self.assertFalse(has_readonly_agent_execution(principal))
        await store.close()

    async def test_fresh_tool_thread_carries_owned_scope_into_async_private_pipe(self):
        from backend.agent_tools import SessionToolBridge
        from backend.agent_tool_executor import AgentToolExecutor
        from backend.jobs import readonly_agent_execution, has_readonly_agent_execution
        principal, seen, alive = owned_principal(), [], [True]
        executor = AgentToolExecutor(1)

        async def process(payload, deadline):
            seen.append((has_readonly_agent_execution(principal), payload.get('captured_readonly')))
            return {'ok': True, 'request_id': payload['request_id'], 'value': {'status': 'ok'},
                    'receipts': [], 'skill_versions': {}, 'group_versions': {}, 'asset_versions': {}}

        with readonly_agent_execution(principal, lambda: alive[0]):
            bridge = SessionToolBridge(principal, loop=asyncio.get_running_loop())
        self.assertFalse(has_readonly_agent_execution(principal))
        with patch('backend.agent_tools.assert_principal_current', return_value=principal), \
             patch('backend.agent_tool_executor.EXECUTOR', executor), \
             patch.object(executor, '_run_process', new=process):
            result = await asyncio.to_thread(bridge.invoke, 'ontology_discover', {})
        self.assertEqual(result, {'status': 'ok'})
        self.assertEqual(seen, [(True, True)])
        self.assertEqual(executor.active, 0)
        self.assertFalse(executor.calls)
        alive[0] = False

    async def test_captured_readonly_scope_never_allows_business_writes(self):
        from backend.jobs import readonly_agent_execution
        from backend.security.store import assert_principal_current
        principal = owned_principal()
        with readonly_agent_execution(principal, lambda: True):
            with self.assertRaises(HTTPException) as error:
                assert_principal_current(principal, require_write=True)
        self.assertEqual(error.exception.status_code, 403)


class AgentAutomaticScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_foreign_workspace_activity_is_authenticated_empty_without_home_reads(self):
        from dataclasses import replace
        from backend import agent_service as service
        p = replace(owned_principal(), tenant_id='another-authorized-workspace')
        with patch.object(service, 'require_context', return_value=p), \
             patch('backend.security.store.assert_principal_current', return_value=p) as authenticate, \
             patch.object(service.SESSIONS, 'runs') as home_runs:
            value = await service.runs()
        self.assertEqual(value, {'items': []})
        authenticate.assert_called_once_with(p)
        home_runs.assert_not_called()

    async def exercise(self, scope_mode='auto', skills=1, mappings=1):
        from backend import agent_service as service
        principal = SimpleNamespace(user_id='user', session_id='login', tenant_id='home')
        session = {'id': 'a' * 32}
        value = service.Turn(message='帮我分析业务', scope_mode=scope_mode,
                             skill_ids=['client-foreign-skill'], mapping_ids=['client-foreign-source'])
        catalog = {'skills': [{'id': 'allowed-skill-' + str(i), 'title': '本机构技能'} for i in range(skills)],
                   'mappings': [{'id': 'allowed-source-' + str(i), 'title': '获准查询来源'} for i in range(mappings)],
                   'assets': []}
        start = AsyncMock(return_value={'run_id': 'b' * 32, 'status': 'running'})
        options = __import__('unittest.mock', fromlist=['Mock']).Mock(return_value=catalog)
        with patch.object(service, 'principal', return_value=principal), \
             patch.object(service.SESSIONS, 'get', return_value=session), \
             patch.object(service.SESSIONS, 'start', new=start), \
             patch.object(service, 'body', new=AsyncMock(return_value=value)), \
             patch.object(service, '_options', new=options):
            error = None
            try:
                await service.turn(session['id'], SimpleNamespace())
            except HTTPException as exc:
                error = exc
        return start, options, error

    async def test_automatic_scope_uses_server_authorized_catalog_not_client_overrides(self):
        start, _, error = await self.exercise()
        self.assertIsNone(error)
        args, kwargs = start.call_args
        self.assertEqual(args[3], ['allowed-skill-0'])
        self.assertEqual(args[4], ['allowed-source-0'])
        self.assertNotIn('client-foreign', str(args))
        self.assertEqual(kwargs['resource_catalog']['skills'][0]['id'], 'allowed-skill-0')

    async def test_legacy_selected_mode_keeps_explicit_scope_contract(self):
        start, options, error = await self.exercise(scope_mode='selected')
        self.assertIsNone(error)
        options.assert_not_called()
        self.assertEqual(start.call_args.args[3], ['client-foreign-skill'])
        self.assertIsNone(start.call_args.kwargs['resource_catalog'])

    async def test_oversize_automatic_catalog_requires_focus_instead_of_silent_truncation(self):
        start, _, error = await self.exercise(mappings=21)
        self.assertEqual(error.status_code, 422)
        start.assert_not_called()


if __name__ == '__main__':
    unittest.main()
