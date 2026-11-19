"""Extraction accepts only individually verified quotes and retries malformed responses once."""

import copy
import json
import unittest
from unittest.mock import AsyncMock, patch

from backend import materials
from backend.ai import AIError
from tests.r4.test_extraction_prompt import GROUP, response


class ExtractionRecovery(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.summary = {"retried_batches": 0, "discarded_candidates": 0}

    async def extract(self, replies: list) -> tuple[tuple[list[dict], dict], int]:
        model = AsyncMock(side_effect=replies)
        with patch("backend.ai.complete", model):
            result = await materials._extract_online_batch("document", GROUP, {}, self.summary)
        return result, model.await_count

    async def test_one_rewritten_quote_does_not_remove_its_valid_neighbor(self) -> None:
        mixed = response()
        wrong = copy.deepcopy(mixed["candidates"][0])
        wrong["title"] = "不应进入方案"
        wrong["source_quotes"][0]["quote"] = "没有出现在材料里的另一句话。"
        mixed["candidates"].insert(0, wrong)
        (accepted, coverage), calls = await self.extract([mixed])
        self.assertEqual([item["title"] for item in accepted], ["付款前核对"])
        self.assertEqual(set(coverage), {"chunk-1"})
        self.assertEqual(calls, 1)
        self.assertEqual(self.summary, {"retried_batches": 0, "discarded_candidates": 1})

    async def test_invalid_structure_triggers_one_independent_validated_attempt(self) -> None:
        for bad in ({"candidates": {}}, "{unfinished", AIError("AI_OUTPUT_INCOMPLETE")):
            with self.subTest(response_type=type(bad).__name__):
                self.summary["retried_batches"] = 0
                (accepted, _), calls = await self.extract([bad, response()])
                self.assertEqual(len(accepted), 1)
                self.assertEqual(calls, 2)
                self.assertEqual(self.summary["retried_batches"], 1)

    async def test_second_invalid_response_is_rejected_without_a_third_call(self) -> None:
        model = AsyncMock(return_value="invalid json")
        with patch("backend.ai.complete", model), self.assertRaises(json.JSONDecodeError):
            await materials._extract_online_batch("document", GROUP, {}, self.summary)
        self.assertEqual(model.await_count, 2)
        self.assertEqual(self.summary["retried_batches"], 1)

    async def test_unknown_source_never_survives_a_retry(self) -> None:
        bad = response()
        bad["candidates"][0]["source_chunk_ids"] = ["other-document"]
        with self.assertRaisesRegex(ValueError, "不存在的原文位置"):
            await self.extract([bad, bad])
        self.assertEqual(self.summary, {"retried_batches": 1, "discarded_candidates": 1})

    async def test_repaired_retry_replaces_only_its_own_discard_count(self) -> None:
        self.summary["discarded_candidates"] = 3  # final results from earlier batches
        bad = response()
        bad["candidates"][0]["source_quotes"][0]["quote"] = "没有出现在原文中的句子。"
        (accepted, _), calls = await self.extract([bad, response()])
        self.assertEqual(calls, 2)
        self.assertEqual(len(accepted), 1)
        self.assertEqual(self.summary, {"retried_batches": 1, "discarded_candidates": 3})

    async def test_adjacent_batches_report_a_rejected_context_source_once_even_if_the_owner_batch_accepts_it(
        self,
    ) -> None:
        mixed = response()
        wrong = copy.deepcopy(mixed["candidates"][0])
        wrong["title"] = "另一项建议"
        wrong["source_quotes"][0]["quote"] = "模型改写的引文未通过。"
        mixed["candidates"].append(wrong)
        seen, discarded = set(), []
        with patch("backend.ai.complete", AsyncMock(side_effect=[response(), mixed, mixed])):
            for _ in range(3):
                await materials._extract_online_batch(
                    "document", GROUP, {}, self.summary, seen_discarded=seen, discarded=discarded
                )
        self.assertEqual(self.summary, {"retried_batches": 0, "discarded_candidates": 1})
        self.assertEqual({cid for sources in discarded for cid in sources}, {"chunk-1"})

    async def test_unavailable_provider_is_not_retried_as_bad_model_output(self) -> None:
        model = AsyncMock(side_effect=AIError("AI_RATE_LIMIT"))
        with patch("backend.ai.complete", model), self.assertRaises(AIError):
            await materials._extract_online_batch("document", GROUP, {}, self.summary)
        self.assertEqual(model.await_count, 1)
        self.assertEqual(self.summary["retried_batches"], 0)
