"""Background ownership, cancellation and safe progress without provider calls."""
import asyncio
import copy
from dataclasses import replace
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch, AsyncMock

from fastapi import HTTPException

from backend import harness_runs as runs
from backend import skill_harness_worker as worker
from backend import skill_harness as harness
from backend.security.context import Principal, use_context, require_context
from starlette.requests import Request


P = Principal(user_id='builder', username='builder', home_tenant_id='sub-01', tenant_id='sub-01',
              role='modeler', is_superadmin=False, session_id='session-a', auth_version=1, database_name='test')


class BackgroundOwnership(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = runs.RunStore(maximum=8, owner_maximum=4, ttl=10)
        self.current = patch.object(runs, 'assert_principal_current', side_effect=lambda principal, **_: principal)
        self.captured = patch.object(runs, 'assert_job_principal_current', side_effect=lambda principal, **_: principal)
        self.current.start(); self.captured.start()

    async def asyncTearDown(self):
        await self.store.close()
        self.current.stop(); self.captured.stop()

    async def finish(self, ident):
        await asyncio.wait_for(asyncio.shield(self.store.runs[ident]['_task']), 1)

    async def test_submit_returns_before_work_and_navigation_does_not_cancel(self):
        release, started = asyncio.Event(), asyncio.Event()
        async def operation():
            started.set()
            await release.wait()
            return {'summary': '合成业务结果'}
        submitted = await self.store.submit(P, kind='skill_author', entity_id='skill-a', title='合成技能',
                                             instruction='核对合成材料', operation=operation)
        await started.wait()
        self.assertEqual((await self.store.detail(submitted['id'], P))['status'], 'running')
        # Unsubscribing/reading is independent of the task; only cancel owns it.
        release.set()
        await self.finish(submitted['id'])
        detail = await self.store.detail(submitted['id'], P)
        self.assertEqual(detail['result'], {'summary': '合成业务结果'})
        self.assertEqual(detail['instruction'], '核对合成材料')
        self.assertEqual(detail['status'], 'completed')
        self.assertNotIn('instruction', (await self.store.list(P))['items'][0])
        self.assertIn('?run=', detail['href'])

    async def test_every_detail_and_cancel_are_owner_and_tenant_bound(self):
        gate = asyncio.Event()
        async def operation():
            await gate.wait()
            return {'private': 'only-owner'}
        item = await self.store.submit(P, kind='skill_author', entity_id='skill-a', title='private', operation=operation)
        for foreign in (replace(P, user_id='other'), replace(P, tenant_id='sub-02'), replace(P, session_id='session-b'),
                        replace(P, role='viewer'), replace(P, readonly=True, visibility='published')):
            self.assertEqual((await self.store.list(foreign))['items'], [])
            with self.assertRaises(HTTPException) as caught:
                await self.store.detail(item['id'], foreign)
            self.assertEqual(caught.exception.status_code, 404)
            with self.assertRaises(HTTPException):
                await self.store.cancel(item['id'], foreign)
        gate.set(); await self.finish(item['id'])

    async def test_duplicate_active_work_is_not_replayed(self):
        gate = asyncio.Event()
        async def operation():
            await gate.wait()
            return {}
        item = await self.store.submit(P, kind='skill_author', entity_id='same', title='same', operation=operation)
        with self.assertRaises(HTTPException) as caught:
            await self.store.submit(P, kind='skill_author', entity_id='same', title='same', operation=operation)
        self.assertEqual(caught.exception.status_code, 409)
        gate.set(); await self.finish(item['id'])

    async def test_explicit_cancel_waits_for_owned_cleanup(self):
        started, cleaned = asyncio.Event(), asyncio.Event()
        async def operation():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                cleaned.set()
        item = await self.store.submit(P, kind='skill_author', entity_id='cancel', title='cancel', operation=operation)
        await started.wait()
        result = await self.store.cancel(item['id'], P)
        self.assertTrue(cleaned.is_set())
        self.assertEqual(result['status'], 'cancelled')
        self.assertNotIn('result', result)

    async def test_stale_source_guard_discards_result_and_safe_error_hides_exception(self):
        async def operation():
            return {'secret-business-result': 'must-not-escape'}
        def guard():
            raise HTTPException(409, {'code': 'source_changed', 'message': 'PRIVATE_PROVIDER_HEADER'})
        item = await self.store.submit(P, kind='skill_author', entity_id='stale', title='stale', operation=operation, guard=guard)
        await self.finish(item['id'])
        result = await self.store.detail(item['id'], P)
        self.assertEqual(result['status'], 'failed')
        self.assertNotIn('result', result)
        self.assertNotIn('PRIVATE_PROVIDER_HEADER', str(result))

    async def test_result_guard_is_repeated_on_later_reads(self):
        allowed = True
        def guard():
            if not allowed:
                raise HTTPException(409, 'changed')
        async def operation():
            return {'value': 'accepted'}
        item = await self.store.submit(P, kind='skill_author', entity_id='read', title='read', operation=operation, guard=guard)
        await self.finish(item['id'])
        self.assertEqual((await self.store.detail(item['id'], P))['result']['value'], 'accepted')
        allowed = False
        with self.assertRaises(HTTPException):
            await self.store.detail(item['id'], P)

    async def test_completed_results_expire_and_shutdown_closes_active_tasks(self):
        async def completed():
            return {'value': 1}
        item = await self.store.submit(P, kind='skill_author', entity_id='expire', title='expire', operation=completed)
        await self.finish(item['id'])
        self.store.runs[item['id']]['_touched'] -= 11
        with self.assertRaises(HTTPException):
            await self.store.detail(item['id'], P)
        await self.store.close()
        with self.assertRaises(HTTPException):
            await self.store.submit(P, kind='skill_author', entity_id='closed', title='closed', operation=completed)

    async def test_progress_is_allowlisted_and_cannot_smuggle_raw_text(self):
        release, started = asyncio.Event(), asyncio.Event()
        async def operation():
            started.set(); await release.wait(); return {}
        item = await self.store.submit(P, kind='skill_author', entity_id='progress', title='progress', operation=operation)
        await started.wait()
        run = self.store.runs[item['id']]
        self.store.event(run, {'stage': 'model', 'message': 'AUTHORIZATION_SECRET', 'text': 'raw chain of thought'})
        self.assertNotIn('AUTHORIZATION_SECRET', str(run['events']))
        self.assertNotIn('chain of thought', str(run['events']))
        self.assertEqual(run['events'][-1]['message'], '正在处理任务。')
        release.set(); await self.finish(item['id'])

    async def test_background_capture_is_revalidated_from_a_worker_thread(self):
        from backend.jobs import assert_execution_principal_current
        captured = []
        async def operation():
            value = await asyncio.to_thread(assert_execution_principal_current, P, require_write=True)
            captured.append(value)
            return {'value': 'allowed'}
        with patch.object(runs, 'assert_job_principal_current', side_effect=lambda principal, **_: principal):
            item = await self.store.submit(P, kind='skill_author', entity_id='captured', title='captured', operation=operation)
            await self.finish(item['id'])
        self.assertEqual(captured, [P])
        self.assertEqual((await self.store.detail(item['id'], P))['status'], 'completed')

    async def test_captured_scope_downgrade_discards_a_prepared_result(self):
        gate, started = asyncio.Event(), asyncio.Event()
        async def operation():
            started.set(); await gate.wait(); return {'answer': 'PRIVATE_PREPARED_RESULT'}
        item = await self.store.submit(P, kind='ai_ask', entity_id='scope', title='scope', operation=operation)
        await started.wait()
        with patch.object(runs, 'assert_job_principal_current', return_value=replace(P, readonly=True, visibility='published')):
            gate.set(); await self.finish(item['id'])
        result = await self.store.detail(item['id'], P)
        self.assertEqual(result['status'], 'failed')
        self.assertNotIn('PRIVATE_PREPARED_RESULT', str(result))

    async def test_stream_disconnection_does_not_cancel_and_foreign_stream_cannot_open(self):
        class Connected:
            async def is_disconnected(self): return False
        gate, started = asyncio.Event(), asyncio.Event()
        async def operation():
            started.set(); await gate.wait(); return {'answer': 'owned'}
        item = await self.store.submit(P, kind='skill_author', entity_id='stream', title='stream', operation=operation)
        await started.wait()
        with patch.object(runs, 'RUNS', self.store), use_context(P):
            response = await runs.run_events(item['id'], Connected(), after=0)
            iterator = response.body_iterator
            event = await anext(iterator)
            self.assertIn('event: progress', event)
            await iterator.aclose()
            self.assertFalse(self.store.runs[item['id']]['_task'].done())
        with patch.object(runs, 'RUNS', self.store), use_context(replace(P, user_id='foreign')):
            with self.assertRaises(HTTPException) as caught:
                await runs.run_events(item['id'], Connected(), after=0)
            self.assertEqual(caught.exception.status_code, 404)
        gate.set(); await self.finish(item['id'])

    async def test_legacy_result_link_preserves_business_query_and_has_recovery_id(self):
        async def operation(): return {'answer': 'owned'}
        item = await self.store.submit(P, kind='experience_turn', entity_id='experience-a', title='conversation',
                                        href='/experience?session=session-a#result', operation=operation)
        self.assertEqual(item['href'], '/experience?session=session-a&harness_run=' + item['id'] + '#result')
        await self.finish(item['id'])

    async def test_revocation_after_stream_open_emits_no_retained_result(self):
        class Connected:
            async def is_disconnected(self): return False
        async def operation(): return {'answer': 'PRIVATE_RETAINED_ANSWER'}
        item = await self.store.submit(P, kind='skill_author', entity_id='revoke', title='revoke', operation=operation)
        await self.finish(item['id'])
        with patch.object(runs, 'RUNS', self.store), use_context(P):
            response = await runs.run_events(item['id'], Connected(), after=0)
            with patch.object(runs, 'assert_principal_current', side_effect=HTTPException(401, 'revoked')):
                event = await anext(response.body_iterator)
        self.assertIn('event: unavailable', event)
        self.assertNotIn('PRIVATE_RETAINED_ANSWER', event)


def request_json(value):
    raw = json.dumps(value).encode()
    async def receive():
        return {'type': 'http.request', 'body': raw, 'more_body': False}
    return Request({'type': 'http', 'method': 'POST', 'headers': [(b'content-type', b'application/json')], 'path': '/'}, receive)


class MultiTurnAuthor(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = BackgroundOwnership.asyncSetUp
    asyncTearDown = BackgroundOwnership.asyncTearDown
    finish = BackgroundOwnership.finish
    async def test_followup_uses_unaccepted_parent_and_inherits_reference_proofs(self):
        from backend import skills
        manifest = skills.Manifest().model_dump(mode='python')
        entity = {'title': '金额核验', 'version': 1}
        document = {'files': {'SKILL.md': 'saved'}, 'manifest': manifest, 'digest': 'saved-digest'}
        prepared = []
        def prepare(ident, body):
            prepared.append(body)
            proofs = [(ident, 1, 'source-proof') for ident in body.reference_material_ids + body.reference_entity_ids]
            return require_context(), dict(entity), copy.deepcopy(document), {'dependencies': []}, proofs
        model = AsyncMock(side_effect=[{'summary': '初步方案', 'files': {'SKILL.md': 'parent-100000'}, 'manifest': manifest},
                                       {'summary': '修改方案', 'files': {'SKILL.md': 'child-200000'}, 'manifest': manifest}])
        with patch.object(runs, 'RUNS', self.store), patch.object(skills, '_role', side_effect=lambda *_: require_context()), \
             patch.object(runs, '_prepare_author', side_effect=prepare), patch.object(runs, '_author_guard'), \
             patch.object(skills, '_audit'), \
             patch.object(harness, 'propose_skill', model), patch.object(skills, '_coherent_files', side_effect=lambda files, _: (files, {})), \
             patch.object(skills, '_validation', return_value={'valid': True}), use_context(P):
            parent = await runs.submit_author('skill-a', request_json({'expected_version': 1, 'instruction': '金额十万元', 'reference_material_ids': ['material-a']}))
            await self.finish(parent['id'])
            child = await runs.submit_author('skill-a', request_json({'expected_version': 1, 'instruction': '改为二十万元', 'parent_run_id': parent['id']}))
            await self.finish(child['id'])
            detail = await self.store.detail(child['id'], P)
        self.assertEqual(model.await_args_list[1].args[2], {'SKILL.md': 'parent-100000'})
        self.assertEqual(model.await_args_list[1].args[4]['previous_turn']['instruction'], '金额十万元')
        self.assertEqual(prepared[1].reference_material_ids, ['material-a'])
        self.assertEqual(detail['parent_run_id'], parent['id'])
        self.assertEqual(detail['result']['base_version'], 1)
        self.assertEqual(detail['result']['base_digest'], 'saved-digest')

    async def test_parent_from_another_skill_is_rejected_without_a_model_call(self):
        async def completed(): return {'base_version': 1, 'base_digest': 'saved'}
        parent = await self.store.submit(P, kind='skill_author', entity_id='other-skill', title='other', operation=completed)
        await self.finish(parent['id'])
        from backend import skills
        with patch.object(runs, 'RUNS', self.store), patch.object(skills, '_role', return_value=P), \
             patch.object(harness, 'propose_skill', AsyncMock()) as model, use_context(P):
            with self.assertRaises(HTTPException) as caught:
                await runs.submit_author('skill-a', request_json({'expected_version': 1, 'instruction': '补充', 'parent_run_id': parent['id']}))
            self.assertEqual(caught.exception.status_code, 409)
            model.assert_not_awaited()

    async def test_parent_source_or_saved_version_change_never_falls_back_to_new_generation(self):
        from backend import skills
        manifest = skills.Manifest().model_dump(mode='python')
        async def completed():
            return {'base_version': 1, 'base_digest': 'saved', 'files': {'SKILL.md': 'parent'}, 'manifest': manifest}
        parent = await self.store.submit(P, kind='skill_author', entity_id='skill-a', title='skill', operation=completed)
        await self.finish(parent['id'])
        self.store.runs[parent['id']].update(reference_material_ids=['material-a'], reference_entity_ids=[],
                                            _author_fingerprints=[('material-a', 1, 'original-source')])
        with patch.object(runs, 'RUNS', self.store), patch.object(skills, '_role', return_value=P), \
             patch.object(harness, 'propose_skill', AsyncMock()) as model, use_context(P):
            for version, digest, proof in [(1, 'saved', 'changed-source'), (2, 'new-save', 'original-source')]:
                prepared = (P, {'version': version, 'title': 'skill'}, {'files': {}, 'manifest': manifest, 'digest': digest},
                            {}, [('material-a', 1, proof)])
                with patch.object(runs, '_prepare_author', return_value=prepared), self.assertRaises(HTTPException) as caught:
                    await runs.submit_author('skill-a', request_json({'expected_version': version, 'instruction': '补充', 'parent_run_id': parent['id']}))
                self.assertEqual(caught.exception.status_code, 409)
            model.assert_not_awaited()


class PublicWorkerProgress(unittest.TestCase):
    def notice(self, kind, data):
        return SimpleNamespace(method='session.event', payload={'event': {'type': kind, 'data': data}})

    def test_reasoning_and_tool_arguments_results_are_never_public(self):
        for kind in ('assistant/reasoning', 'assistant/thinking', 'tool/result'):
            self.assertIsNone(worker._public_progress(self.notice(kind, {'text': 'PRIVATE', 'arguments': 'SECRET'})))
        value = worker._public_progress(self.notice('tool/call', {'name': 'read_skill_file', 'arguments': {'path': 'PRIVATE'}}))
        self.assertEqual(value['message'], '正在查阅参考内容。')
        self.assertNotIn('PRIVATE', str(value))
        self.assertIsNone(worker._public_progress(self.notice('tool/call', {'name': 'unregistered_secret_tool'})))

    def test_writer_emits_only_public_metadata_without_creating_a_journal(self):
        writer, values = worker.PublicProgressWriter(), []
        with patch.object(writer, '_post', side_effect=values.append):
            writer(self.notice('assistant/message', {'message': {'content': [{'type': 'text', 'text': 'PRIVATE'}]}}))
        self.assertEqual(values, [{'type': 'progress', 'stage': 'generating', 'message': '正在整理回答。'}])

    def test_public_text_redacts_secrets_split_across_chunks_and_resets_attempts(self):
        secret, bridge = 'synthetic-provider-secret', 'B' * 43
        writer, values = worker.PublicProgressWriter(secret_values=(secret, bridge)), []
        with patch.object(writer, '_post', side_effect=values.append):
            writer.stream({'type': 'answer_start', 'message_id': 'owned:1'})
            for text in ('公开说明。' * 15, secret[:7], secret[7:], '之后文字', bridge[:20], bridge[20:], '收尾'):
                writer.stream({'type': 'answer_delta', 'message_id': 'owned:1', 'text': text})
            writer.stream({'type': 'answer_end', 'message_id': 'owned:1'})
            writer.stream({'type': 'answer_start', 'message_id': 'owned:2'})
            writer.stream({'type': 'answer_delta', 'message_id': 'owned:2', 'text': '第二轮'})
            writer.stream({'type': 'answer_end', 'message_id': 'owned:2'})
        self.assertTrue(writer.ended.is_set())
        first = ''.join(event['text'] for event in values if event['message_id'] == 'owned:1')
        self.assertNotIn(secret, first); self.assertNotIn(bridge, first)
        self.assertEqual(first, '公开说明。' * 15 + '〔敏感内容已隐藏〕之后文字〔敏感内容已隐藏〕收尾')
        self.assertEqual(''.join(event['text'] for event in values if event['message_id'] == 'owned:2'), '第二轮')

    def test_business_create_needs_only_a_title_and_internal_names_are_unique(self):
        from backend.skills import CreateBody, _automatic_package_name
        self.assertEqual(CreateBody.model_validate({'title': '合同核验'}).package_name, '')
        first, second = _automatic_package_name('合同核验'), _automatic_package_name('合同核验')
        self.assertNotEqual(first, second)
        self.assertRegex(first, '^[a-z0-9]+(?:-[a-z0-9]+)*$')


if __name__ == '__main__':
    unittest.main()
