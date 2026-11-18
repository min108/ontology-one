"""A.1 issues 7/8/12/13: deterministic small counterexamples, no stress runs."""

import json
import subprocess
import sys
import time
import unittest
from copy import deepcopy
from unittest.mock import patch

from backend.kernel import gates, kinds, metadata, oxl, rules


def judgement() -> dict:
    return {"condition": {"oxl": "true"}, "requirement": {"oxl": "true"}, "exceptions": [], "test_cases": []}


class KernelSafety(unittest.TestCase):
    def test_round_literal_rejected_during_parse_and_dynamic_before_round(self) -> None:
        for expression in ("round(1, -4000000) == 0", "round(1, 101)", "round(1, -101)"):
            with self.subTest(expression=expression), self.assertRaisesRegex(oxl.OXLError, "-100 到 100"):
                oxl.parse(expression)
        parsed = oxl.parse("round(1, digits)", properties=["digits"])
        with (
            patch("builtins.round", side_effect=AssertionError("unbounded rounding reached")),
            self.assertRaisesRegex(oxl.OXLError, "-100 到 100"),
        ):
            oxl.evaluate(parsed, {"digits": -4000000})
        for digits in (-100, -2, 0, 2, 100):
            self.assertEqual(oxl.evaluate(oxl.parse(f"round(123.456, {digits})"), {}), round(123.456, digits))

    def test_long_numeric_prose_and_meaningful_unit_counterexample(self) -> None:
        number = "7" * 10000
        began = time.monotonic()
        self.assertFalse(gates.hard_constraint({"kind": "data", "payload": {"content": number}}))
        self.assertLess(time.monotonic() - began, 1)
        # The same complete token becomes a real monetary constraint with a unit.
        self.assertTrue(gates.hard_constraint({"kind": "data", "payload": {"content": number + "万元"}}))
        for text in ("30日内", "十二个工作日", "1.5 万元", "不得超过合同金额", "金额高于合同金额时需核实"):
            with self.subTest(text=text):
                self.assertTrue(gates.hard_constraint({"kind": "data", "payload": {"content": text}}))
        for text in ("单位为万元", "金额字段", "金额 = 单价 * 数量", "对象编号12345"):
            with self.subTest(text=text):
                self.assertFalse(gates.hard_constraint({"kind": "data", "payload": {"content": text}}))

    def test_import_and_dates_with_windows_without_tzdata(self) -> None:
        # A fresh interpreter prevents an already-imported kernel hiding a tzdata dependency.
        code = """
import sys
import zoneinfo
from unittest.mock import patch
missing_zone = patch.object(zoneinfo, "ZoneInfo", side_effect=zoneinfo.ZoneInfoNotFoundError("no system tzdata"))
with patch.object(sys, "platform", "win32"), missing_zone:
    from backend.kernel import oxl
    assert oxl.as_of_date("2026-11-17T16:00:00Z").isoformat() == "2026-11-18"
    assert oxl.as_of_date("2026-11-17T15:59:59Z").isoformat() == "2026-11-17"
    expression = oxl.parse("days_between('2026-11-17', '2026-11-18') == 1 and today() == '2026-11-18'")
    assert oxl.evaluate(expression, {}, as_of="2026-11-17T16:00:00Z") is True
"""
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_judgement_shape_rejects_each_nested_malformed_value(self) -> None:
        for field, value in (
            ("applies_to", None),
            ("applies_to", []),
            ("applies_to", {"object_type": []}),
            ("condition", []),
            ("requirement", {"oxl": 3}),
            ("exceptions", {}),
            ("exceptions", [None]),
            ("test_cases", None),
            ("test_cases", ["bad"]),
        ):
            body = {"judgement": {**judgement(), field: value}}
            before = deepcopy(body)
            with self.subTest(field=field, value=value), self.assertRaises(oxl.OXLError):
                rules.prepare_payload("rule", body)
            self.assertEqual(body, before)
            report = gates.run_cases(None, {"kind": "rule", "payload": body})
            self.assertFalse(report["passed"])
            self.assertTrue(report["errors"])
        self.assertIsNotNone(rules.prepare_payload("rule", {"judgement": judgement()}))
        self.assertEqual(rules.prepare_payload("rule", {"definition": "说明型规则"}), {"definition": "说明型规则"})

    def test_legacy_kind_defaults_are_generic_but_new_kind_remains_rejected(self) -> None:
        entity = {"id": "OLD-MEMO", "kind": "memo", "payload": json.dumps({"content": "原有记录"})}
        before = deepcopy(entity)
        self.assertEqual(metadata.defaults(entity)["pillar"], "aux")
        self.assertIsNone(metadata.defaults(entity)["module_id"])
        self.assertFalse(kinds.requires_quality_gates("memo"))
        with self.assertRaises(ValueError):
            kinds.get_kind("memo")
        self.assertEqual(entity, before)


if __name__ == "__main__":
    unittest.main()
