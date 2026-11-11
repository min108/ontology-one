"""Deterministic regressions for admission across authorization IO awaits."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from backend.agent_runtime import SessionStore


class AdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, delete=False):
        principal = SimpleNamespace(user_id='user', session_id='login', tenant_id='tenant')
        entered, released, running = asyncio.Event(), asyncio.Event(), asyncio.Event()
        store = SessionStore()

        async def validate(_):
            entered.set()
            await released.wait()

        async def execute(*_):
            running.set()
            await asyncio.Event().wait()

        with patch('backend.agent_runtime.current', return_value=principal), \
             patch.object(store, 'validate', side_effect=validate), \
             patch.object(store, '_execute', side_effect=execute) as model:
            session = store.create(principal)
            first = asyncio.create_task(store.start(principal, session, 'one', [], [], []))
            await entered.wait()
            if delete:
                store.sessions.pop(session['id'])
                released.set()
                with self.assertRaises(HTTPException) as error:
                    await first
                self.assertEqual(error.exception.status_code, 404)
                model.assert_not_called()
                self.assertEqual(session['runs'], {})
            else:
                second = asyncio.create_task(store.start(principal, session, 'two', [], [], []))
                released.set()
                outcomes = await asyncio.gather(first, second, return_exceptions=True)
                self.assertEqual(sum(isinstance(value, dict) for value in outcomes), 1)
                failures = [value for value in outcomes if isinstance(value, HTTPException)]
                self.assertEqual([value.status_code for value in failures], [409])
                await running.wait()
                model.assert_called_once()
                self.assertEqual(len(session['runs']), 1)
            await store.close()

    async def test_parallel_turns_have_one_admission(self):
        await self.exercise()

    async def test_delete_during_validation_never_starts_orphaned_model(self):
        await self.exercise(delete=True)
