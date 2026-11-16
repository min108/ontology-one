"""Probe results must distinguish observed runtime behavior and live evidence."""

import tempfile
import unittest
from pathlib import Path

from scripts.probes.harness.run import CASES, build_patch, classify, compaction_evidence, live_configuration
from scripts.probes.harness.versions import verify_digest


class ProbeContractTests(unittest.TestCase):
    def test_wheel_tampering_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            wheel = Path(directory) / "fixture.whl"
            wheel.write_bytes(b"tampered")
            with self.assertRaises(ValueError):
                verify_digest(wheel, "0" * 64)

    def test_all_eight_decisions_are_declared(self) -> None:
        self.assertEqual(len(CASES), 8)
        self.assertTrue(all(value for value in CASES.values()))

    def test_absent_named_key_cannot_use_other_credentials(self) -> None:
        self.assertIsNone(live_configuration({"API_KEY": "do-not-use"}))

    def test_live_configuration_uses_only_named_key(self) -> None:
        self.assertEqual(live_configuration({"DEEPSEEK_API_KEY": "fixture"})["api_key"], "fixture")

    def test_loop_requires_forty_completed_tool_calls(self) -> None:
        self.assertFalse(classify("loop", {"requests": 41, "tool_calls": 39, "completed": True}))
        self.assertTrue(classify("loop", {"requests": 41, "tool_calls": 40, "completed": True}))

    def test_parallel_requires_observed_overlap(self) -> None:
        self.assertFalse(classify("parallel", {"completed": True, "max_active": 1, "tool_calls": 4}))
        self.assertTrue(classify("parallel", {"completed": True, "max_active": 4, "tool_calls": 4}))

    def test_compaction_requires_replacement_event(self) -> None:
        self.assertFalse(classify("compaction", {"completed": True, "compactions": 0}))
        self.assertFalse(classify("compaction", {"completed": True, "compactions": 1}))
        self.assertTrue(classify("compaction", {"completed": True, "compactions": 1, "surface_replacements": 1}))

    def test_failed_compaction_is_not_counted_as_a_summary(self) -> None:
        evidence = compaction_evidence(
            [
                {"type": "compaction/start"},
                {"type": "compaction/end", "data": {"error": "synthetic"}},
            ]
        )
        self.assertEqual(evidence, {"compactions": 0, "surface_replacements": 0, "compaction_failures": 1})

    def test_compaction_discards_summary_content_from_evidence(self) -> None:
        evidence = compaction_evidence(
            [
                {"type": "compaction/summary", "data": {"shadowedSeqs": [1], "summary": "PRIVATE_SYNTHETIC"}},
                {"type": "user/message", "surfaceOp": {"op": "replace", "startSeq": 1, "endSeq": 1}},
            ]
        )
        self.assertEqual(evidence, {"compactions": 1, "surface_replacements": 1, "compaction_failures": 0})
        self.assertNotIn("PRIVATE_SYNTHETIC", str(evidence))

    def test_retry_requires_recovery_and_auth_failure_not_retried(self) -> None:
        self.assertFalse(classify("retry", {"completed": True, "requests": 1}))
        self.assertTrue(classify("retry", {"completed": True, "requests": 2}))
        self.assertTrue(classify("auth", {"completed": False, "requests": 1}))

    def test_cancel_requires_process_reaped(self) -> None:
        self.assertFalse(classify("cancel", {"cancelled": True, "reaped": False}))

    def test_patch_keeps_host_capabilities_disabled(self) -> None:
        patch = build_patch("parallel", Path("/probe/plugin.mjs"))
        for name in ("persistent-bash", "persistent-pwsh", "tool-fs", "mcp-resources", "sessions"):
            self.assertIn({"id": name, "disabled": True}, patch)
        self.assertEqual(
            next(row for row in patch if row.get("id") == "agent-loop")["config"]["maxParallelToolCalls"], 4
        )

    def test_missing_evidence_never_passes(self) -> None:
        for case in ("loop", "parallel", "compaction", "retry", "stream", "thinking", "protocol", "cancel"):
            self.assertFalse(classify(case, {}))
