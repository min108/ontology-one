"""The fallback browser fixture changes only a named synthetic provider response."""

import json
import unittest

from e2e import r2_model


class FallbackProviderTest(unittest.TestCase):
    def test_only_explicit_filename_produces_a_strictly_invalid_source_quote(self) -> None:
        source = "第一条 验收通过后10个工作日内提出付款申请。"
        for title, invalid in [("R4A1-unusable-policy.txt", True), ("policy.txt", False)]:
            history = [
                {
                    "role": "user",
                    "content": json.dumps(
                        {"material": {"title": title}, "chunks": [{"id": "chunk", "content": source}]}
                    ),
                }
            ]
            result = r2_model.reply(
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
            payload = json.loads(result["input"]["text"])
            quote = payload["candidates"][0]["source_quotes"][0]["quote"]
            self.assertEqual(payload["candidates"][0]["content"], source)
            self.assertEqual(payload["coverage"][0]["chunk_id"], "chunk")
            if invalid:
                self.assertLess(len(quote), 8)
                self.assertNotIn(quote, source)
            else:
                self.assertEqual(quote, source)
