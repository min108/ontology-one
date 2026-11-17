"""Synthetic revision actions retain supported quantities; deletion is a failing counterexample."""

import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from backend.agent_runtime import SessionStore
from backend.agent_tools import SessionToolBridge
from backend.domains.artifacts import render
from tests.test_agent_streaming import owned_principal


class RevisionQuantityActions(unittest.IsolatedAsyncioTestCase):
    async def revised_spec(self, *, delete_supported_quantity: bool = False) -> dict:
        principal, store = owned_principal(), SessionStore()
        original = "付款制度规定30日内申请，金额不超过10000元，另有2项要求。"
        corrected = (
            "付款制度规定按时申请，金额不超过1万元。"
            if delete_supported_quantity
            else "付款制度规定30日内申请，金额不超过1万元。"
        )
        prompts = []

        async def synthetic_model(*_: object, metadata: dict, **__: object) -> dict:
            prompt = metadata["messages"][-1]["content"]
            prompts.append(prompt)
            return {"text": corrected if len(prompts) == 2 else original, "usage": {}, "source_kind": "mock"}

        sources = {"c1": {"title": "付款制度", "quote": "应在30日内申请，金额不超过10000元。", "version": 1}}

        def report(answer: str) -> dict:
            return {"title": "付款报告", "blocks": render.blocks_from_answer(answer, sources), "citations": sources}

        with (
            patch("backend.agent_runtime.current", return_value=principal),
            patch.object(store, "validate", new=AsyncMock()),
            patch("backend.agent_runtime._selected_assets", return_value=[]),
            patch("backend.agent_runtime._configuration_signature", return_value=b"synthetic-config"),
            patch("backend.agent_runtime.execute_controlled", new=synthetic_model),
            patch.object(SessionToolBridge, "validate_async", new=AsyncMock()),
        ):
            session = store.create(principal)
            try:
                started = await store.start(principal, session, "说明付款时限和金额。", [], [], [])
                run = session["runs"][started["run_id"]]
                await run["task"]
                self.assertEqual(run["status"], "completed")
                with self.assertRaises(HTTPException) as error:
                    render.verify(report(run["answer"]))
                self.assertIn("2项", str(error.exception.detail))
                revised = await store.start(
                    principal, session, "核对结果：删除没有依据的2项计数。", [], [], [], revision_of=run["id"]
                )
                result = session["runs"][revised["run_id"]]
                await result["task"]
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["revision_of"], run["id"])
                self.assertEqual(len(prompts), 2)
                self.assertIn("保留有依据的关键数字与单位", prompts[-1])
                self.assertIn("条款序号或表格行数", prompts[-1])
                self.assertIn(original, prompts[-1])
                value = report(result["answer"])
                render.verify(value)  # Numeric validity alone cannot detect a deleted supported claim.
                return value
            finally:
                await store.close()

    def assert_preserves_supported_quantities(self, value: dict) -> None:
        quantities = render.quantities("\n".join(block["text"] for block in value["blocks"]))
        self.assertIn((Decimal(30), "日"), quantities, "修订丢失了原回答有依据的30日期限")
        self.assertIn((Decimal(10000), "元"), quantities, "修订丢失了原回答有依据的金额与单位")

    async def test_revision_preserves_supported_numbers_and_units(self) -> None:
        self.assert_preserves_supported_quantities(await self.revised_spec())

    async def test_generated_revision_deleting_supported_number_fails_the_action_assertion(self) -> None:
        value = await self.revised_spec(delete_supported_quantity=True)
        with self.assertRaisesRegex(AssertionError, "修订丢失了原回答有依据的30日期限"):
            self.assert_preserves_supported_quantities(value)
