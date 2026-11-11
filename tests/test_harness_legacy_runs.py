"""Security boundaries of background adapters, independent of model responses."""
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from backend import legacy_harness_runs as legacy
from backend.security.context import Principal, use_context


def principal(role='modeler', tenant='hq'):
    return Principal(user_id='user-1', username='user.one', home_tenant_id='hq',
                     tenant_id=tenant, role=role, is_superadmin=False,
                     session_id='session-1', auth_version=1, database_name='test_hq')


class LegacyRunSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_review_role_cannot_launch_build_operation_through_generic_route(self):
        operation = AsyncMock()
        handler = legacy._handler('project_plan', '工作安排', '/api/projects/{id}/plan',
                                  '/projects/{id}', legacy.PlanBody, operation,
                                  request_body=True, parameter='pid')
        with patch('backend.agent_tasks.current', return_value=principal('reviewer')):
            with self.assertRaises(HTTPException) as caught:
                await handler(principal('reviewer'), 'PRJ-1', {'instruction': '明确独立交付成果'})
        self.assertEqual(caught.exception.status_code, 403)
        operation.assert_not_called()

    async def test_foreign_or_missing_object_is_rejected_before_model_or_write(self):
        operation = AsyncMock()
        handler = legacy._handler('project_plan', '工作安排', '/api/projects/{id}/plan',
                                  '/projects/{id}', legacy.PlanBody, operation,
                                  request_body=True, parameter='pid')
        with patch('backend.agent_tasks.current', return_value=principal()), \
             patch.object(legacy, '_snapshot', return_value={}):
            with self.assertRaises(HTTPException) as caught:
                await handler(principal(), 'FOREIGN-PRJ', {'instruction': '明确独立交付成果'})
        self.assertEqual(caught.exception.status_code, 404)
        operation.assert_not_called()

    async def test_extra_executor_or_endpoint_fields_are_not_dispatchable(self):
        operation = AsyncMock()
        handler = legacy._handler('ai_ask', '资料答复', '/api/ai/ask', '/briefing',
                                  legacy.AskBody, operation, request_body=True)
        with patch('backend.agent_tasks.current', return_value=principal()):
            for extra in ({'endpoint': 'https://untrusted.invalid'},
                          {'callable': 'os.system'}, {'tenant_id': 'foreign'},
                          {'mode': 'retrieval'}):
                with self.subTest(extra=extra), self.assertRaises(HTTPException) as caught:
                    await handler(principal(), '', {'question': '当前材料有哪些适用条件？', **extra})
                self.assertEqual(caught.exception.status_code, 422)
        operation.assert_not_called()

    async def test_result_replay_rechecks_business_sources_and_configuration(self):
        operation = AsyncMock(return_value={'answer': '需核对适用条件。',
                                           'citations': [{'entity_id': 'RULE-1'}]})
        handler = legacy._handler('ai_ask', '资料答复', '/api/ai/ask', '/briefing',
                                  legacy.AskBody, operation, request_body=True)
        version = {'RULE-1': (1, 'published', '2026-11-11')}
        with patch('backend.agent_tasks.current', return_value=principal()), \
             patch.object(legacy, '_snapshot', side_effect=lambda _: dict(version)), \
             patch('backend.skill_harness._configuration', return_value={'revision': 'config-one'}) as config, \
             use_context(principal()):
            spec = await handler(principal(), '', {'question': '当前材料有哪些适用条件？'})
            result = await spec['operation']()
            self.assertIn('answer', result)
            spec['guard']()
            version['RULE-1'] = (2, 'archived', '2026-11-12')
            with self.assertRaises(HTTPException) as caught:
                spec['guard']()
            self.assertEqual(caught.exception.status_code, 409)
            version['RULE-1'] = (1, 'published', '2026-11-11')
            config.return_value = {'revision': 'config-two'}
            with self.assertRaises(HTTPException) as changed:
                spec['guard']()
            self.assertEqual(changed.exception.detail['code'], 'HARNESS_CONFIG_CHANGED')


class QueueClaimTransactions(unittest.TestCase):
    def test_deadlock_retries_only_rolled_back_claim_not_business_execution(self):
        import pymysql
        from backend import jobs
        lease = {'job_id': 'only-claimed-once'}
        with patch.object(jobs, '_claim_once', side_effect=[pymysql.err.OperationalError(1213, 'fixture'), lease]) as claim, \
             patch('time.sleep'):
            self.assertIs(jobs._claim(), lease)
            self.assertEqual(claim.call_count, 2)

    def test_other_database_failures_and_repeated_deadlocks_are_bounded(self):
        import pymysql
        from backend import jobs
        with patch.object(jobs, '_claim_once', side_effect=pymysql.err.OperationalError(1045, 'fixture')) as claim:
            with self.assertRaises(pymysql.err.OperationalError):
                jobs._claim()
            self.assertEqual(claim.call_count, 1)
        with patch.object(jobs, '_claim_once', side_effect=pymysql.err.OperationalError(1213, 'fixture')) as claim, \
             patch('time.sleep'):
            with self.assertRaises(pymysql.err.OperationalError):
                jobs._claim()
            self.assertEqual(claim.call_count, 3)


if __name__ == '__main__':
    unittest.main()
