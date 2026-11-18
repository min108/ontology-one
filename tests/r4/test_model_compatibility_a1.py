"""Synthetic mainline fixtures keep the old numeric failure and ground new replies."""

import json
import sys
import unittest
from unittest.mock import patch

from e2e import r2_model


class SyntheticCompatibility(unittest.TestCase):
    def reply(self, evidence: str, question: str = "本条规定的办理时限是什么？") -> str:
        with patch.dict(sys.modules, {"r2_model": r2_model}):
            from e2e.r3_model import r3_reply

        body = {
            "messages": [
                {
                    "role": "user",
                    "content": json.dumps({"question": question, "entity_ids": ["RULE-X"]}, ensure_ascii=False),
                },
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "read-1", "name": "ontology_read", "input": {"entity_id": "RULE-X"}}
                    ],
                },
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "read-1", "content": evidence}]},
            ],
            "tools": [],
        }
        return r3_reply(body)["text"]

    def test_new_deadline_answer_requires_actual_tool_evidence(self) -> None:
        for days in (10, 15):
            answer = self.reply(f"应在办理完成后{days}个工作日内提交申请。")
            self.assertIn(f"{days}个工作日", answer)
            self.assertIn("/assets/RULE-X", answer)
            self.assertNotIn("30日", answer)
        for evidence in ("110个工作日", "2026年10月15日", "没有时限"):
            answer = self.reply(evidence, "本条规定的办理时限是10个工作日吗？")
            self.assertNotIn("10个工作日", answer)
            self.assertNotIn("15个工作日", answer)

    def test_old_route_keeps_unsupported_number_then_correction(self) -> None:
        self.assertIn("31日", self.reply("验收通过后30日内完成付款申请。", "付款制度有什么要求？"))
        self.assertIn("30日", self.reply("验收通过后30日内完成付款申请。", "核对结果：请按原文更正。"))

    def test_extraction_keeps_deliberate_data_and_classification_repairs(self) -> None:
        chunks = [
            {"id": "one", "content": "合同付款台账逐份合同记录付款金额，以元计量；验收记录和付款依据按份登记。"},
            {"id": "two", "content": "经办人员应保存验收记录，审核人员复核付款依据。"},
            {"id": "three", "content": "本办法自2026年1月1日起施行。"},
        ]
        history = [{"role": "user", "content": json.dumps({"chunks": chunks}, ensure_ascii=False)}]
        response = r2_model.reply(
            {
                "tools": [{"name": "submit_task_result"}],
                "messages": [
                    {
                        "role": "user",
                        "content": "Conversation messages (the role labels describe application history):\n"
                        + json.dumps(history),
                    }
                ],
            }
        )
        result = json.loads(response["input"]["text"])
        self.assertEqual([item["kind"] for item in result["candidates"]], ["data", "skill"])
        self.assertEqual(result["candidates"][0]["unit"], "")
        self.assertEqual(result["candidates"][0]["grain"], "")
        self.assertEqual({item["chunk_id"] for item in result["coverage"]}, {"one", "two", "three"})
        for item in result["candidates"]:
            self.assertEqual(item["source_quotes"][0]["quote"], item["content"])


if __name__ == "__main__":
    unittest.main()
