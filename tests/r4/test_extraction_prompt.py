"""R4-A A6 extraction prompt matches its unchanged strict candidate contract."""

import copy
import json
import unittest
from unittest.mock import AsyncMock, patch

from backend import materials

SCALARS = ("definition", "scope", "exceptions", "valid_from", "valid_to", "unit", "grain", "boundary")
QUOTE = "办理付款前应核对合同约定与验收资料。"
GROUP = [{"id": "chunk-1", "position": 1, "content": QUOTE}]


def response() -> dict:
    return {
        "candidates": [
            {
                "kind": "rule",
                "title": "付款前核对",
                "summary": "付款前核对资料",
                "content": QUOTE,
                "source_chunk_ids": ["chunk-1"],
                "source_quotes": [{"chunk_id": "chunk-1", "quote": QUOTE}],
                "conditions": [],
                "questions": [],
                **dict.fromkeys(SCALARS, ""),
            }
        ],
        "coverage": [{"chunk_id": "chunk-1", "reason": "已核对完整原文"}],
    }


class ExtractionPromptContract(unittest.IsolatedAsyncioTestCase):
    async def test_full_json_example_declares_scalar_release_fields_and_array_conditions(self) -> None:
        complete = AsyncMock(return_value=response())
        with patch("backend.ai.complete", complete):
            found, coverage = await materials.online_candidates("material-1", GROUP, {})
        self.assertEqual(len(found), 1)
        self.assertEqual(set(coverage), {"chunk-1"})
        prompt = complete.call_args.args[0][0]["content"]
        example = json.loads(prompt.split("仅输出JSON对象", 1)[1].split("。候选来源", 1)[0])
        candidate = example["candidates"][0]
        for field in SCALARS:
            with self.subTest(field=field):
                self.assertIsInstance(candidate[field], str)
        self.assertIsInstance(candidate["conditions"], list)
        self.assertIsInstance(candidate["questions"], list)
        self.assertIn("均为字符串，缺失时用空字符串，不能用数组或对象", prompt)
        self.assertIn("exceptions须用一句文字合并例外，不能写成列表", prompt)

    async def test_optional_scalar_nulls_remain_accepted_without_inventing_values(self) -> None:
        value = response()
        value["candidates"][0].update(dict.fromkeys(SCALARS))
        with patch("backend.ai.complete", AsyncMock(return_value=value)):
            found, _ = await materials.online_candidates("material-1", GROUP, {})
        for field in SCALARS:
            with self.subTest(field=field):
                self.assertEqual(found[0][field], "")

    async def test_array_exceptions_reproduce_the_actual_rejection(self) -> None:
        for exceptions in ([], ["争议时暂停付款"]):
            value = response()
            value["candidates"][0]["exceptions"] = exceptions
            with (
                self.subTest(exceptions=exceptions),
                patch("backend.ai.complete", AsyncMock(return_value=value)),
                self.assertRaisesRegex(ValueError, "AI候选发布条件须为业务文字"),
            ):
                await materials.online_candidates("material-1", GROUP, {})

    async def test_prompt_clarification_does_not_accept_fabricated_sources_or_rewritten_quotes(self) -> None:
        for key, changed in (("chunk_id", "missing"), ("quote", "办理付款前应核对合同和验收材料。")):
            value = copy.deepcopy(response())
            value["candidates"][0]["source_quotes"][0][key] = changed
            with (
                self.subTest(key=key),
                patch("backend.ai.complete", AsyncMock(return_value=value)),
                self.assertRaisesRegex(ValueError, "AI候选的原文引用未通过逐字校验"),
            ):
                await materials.online_candidates("material-1", GROUP, {})
