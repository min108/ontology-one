"""R4-A A6 extraction prompt matches its unchanged strict candidate contract."""

import copy
import json
import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

from backend import materials
from backend.ai import AIError

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


class ExtractionSafeDiagnostics(unittest.IsolatedAsyncioTestCase):
    async def test_actual_batch_failure_logs_only_fixed_codes_and_preserves_generic_errors(self) -> None:
        secret = "synthetic-sensitive-token-not-for-logs"
        provider = AIError("AI_PROVIDER_REJECTED")
        provider.args = (secret,)
        tampered = AIError("AI_RESPONSE_INVALID")
        tampered.code = secret
        tampered.args = (secret,)
        failures = [
            (ValueError("AI候选发布条件须为业务文字，缺失内容应留空"), "CANDIDATE_SCALAR_FIELD_INVALID"),
            (ValueError("AI候选的原文引用未通过逐字校验"), "SOURCE_QUOTE_INVALID"),
            (json.JSONDecodeError(secret, secret, 0), "JSON_PARSE_INVALID"),
            (provider, "MODEL_AI_PROVIDER_REJECTED"),
            (AIError("AI_OUTPUT_INCOMPLETE"), "MODEL_AI_OUTPUT_INCOMPLETE"),
            (AIError("AI_RESPONSE_INVALID"), "MODEL_AI_RESPONSE_INVALID"),
            (AIError("AI_RATE_LIMIT"), "MODEL_AI_RATE_LIMIT"),
            (tampered, "MODEL_CALL_FAILED"),
            (ValueError(secret), "VALIDATION_UNKNOWN"),
            (RuntimeError(secret), "EXTRACTION_UNEXPECTED"),
        ]
        for failure, code in failures:
            item = {"title": secret, "domain": secret, "org_id": secret, "source_kind": secret, "payload": {}}
            with self.subTest(code=code), ExitStack() as stack:
                stack.enter_context(patch("backend.materials.connect", return_value=MagicMock()))
                stack.enter_context(patch("backend.materials.material", return_value=item))
                stack.enter_context(patch("backend.materials.get_active_chunks", return_value=GROUP))
                stack.enter_context(patch("backend.materials.online_candidates", AsyncMock(side_effect=failure)))
                stack.enter_context(patch("backend.materials.save_payload"))
                stack.enter_context(patch("backend.materials.activity"))
                stack.enter_context(patch("backend.materials._active", MagicMock()))
                job_update = stack.enter_context(patch("backend.materials.job_update"))
                captured = stack.enter_context(self.assertLogs("backend.materials", level="WARNING"))
                await materials.run_extraction(secret, secret, "online", False)
                self.assertEqual(captured.output, ["WARNING:backend.materials:material_extraction_failed code=" + code])
                record = captured.records[0]
                self.assertIsNone(record.exc_info)
                self.assertIsNone(record.exc_text)
                self.assertIsNone(record.stack_info)
                self.assertNotIn(secret, repr(record.__dict__))
                self.assertNotIn(QUOTE, repr(record.__dict__))
                expected = (
                    "AI返回内容未通过结构或来源校核，本批未写入候选，请重试。"
                    if isinstance(failure, ValueError)
                    else "在线识读未完成，请检查AI连接配置后重试；已有候选和原文均已保留。"
                )
                self.assertEqual(item["payload"]["coverage"]["chunk-1"]["message"], expected)
                self.assertEqual(item["payload"]["candidates"], [])
                self.assertEqual(job_update.call_args.args[2], "failed")

    def test_every_known_validation_reason_has_a_fixed_non_sensitive_code(self) -> None:
        for reason, code in materials._EXTRACTION_VALIDATION_CODES.items():
            with self.subTest(code=code):
                self.assertRegex(code, r"^[A-Z_]+$")
                self.assertEqual(materials._extraction_failure_code(ValueError(reason)), code)
                self.assertEqual(
                    materials._extraction_failure_code(ValueError(reason + " private-source")), "VALIDATION_UNKNOWN"
                )
