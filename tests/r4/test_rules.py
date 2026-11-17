"""Rule/gate specification: accepted and blocked cases, then action assertions."""

import unittest
from copy import deepcopy

from backend.kernel import oxl, rules

PROPERTIES = [
    {"key": "amount", "name": "合同金额", "unit": "元"},
    {"key": "approval_ready", "name": "审批材料齐备"},
    {"key": "framework", "name": "框架协议"},
]
RULE = {
    "id": "RULE-test",
    "version": 3,
    "_meta": {"valid_from": "2026-01-01", "valid_to": "2026-12-31"},
    "payload": {
        "judgement": {
            "applies_to": {"object_type": "OT-contract"},
            "condition": {"oxl": "amount > 200000"},
            "requirement": {"oxl": "approval_ready == true"},
            "exceptions": [],
            "severity": "must",
            "test_cases": [
                {"name": "未超过", "input": {"amount": 200000}, "expected": "not_applicable"},
                {"name": "尚待核实", "input": {"amount": 300000}, "expected": "needs_review"},
            ],
        }
    },
}
# ≥5 positive and ≥5 negative specification rows precede rule test implementation.
OUTCOME_CASES = [
    ({"amount": 200000}, [], "not_applicable"),
    ({"amount": 300000, "approval_ready": True}, [], "pass"),
    ({"amount": 300000, "approval_ready": False}, [], "violate"),
    ({"amount": 300000}, [], "needs_review"),
    ({"amount": 300000, "framework": True}, [{"oxl": "framework"}], "not_applicable"),
    ({"amount": 300000, "approval_ready": False}, [{"oxl": "framework"}], "needs_review"),
    ({}, [], "needs_review"),
]
BLOCKED_CASES = [
    {"condition": {"oxl": "undeclared > 1"}},
    {"requirement": {"oxl": "approval_ready.real"}},
    {"test_cases": []},
    {"test_cases": [RULE["payload"]["judgement"]["test_cases"][0]]},
    {"test_cases": [{"input": {"amount": 0}, "expected": "pass"}] * 2},
    {"test_cases": [{"input": {}, "expected": []}] * 2},
    {"test_cases": [{"input": {}}] * 2},
]


class RuleTests(unittest.TestCase):
    def test_four_outcomes_and_unknown_exception(self) -> None:
        for values, exceptions, expected in OUTCOME_CASES:
            with self.subTest(values=values, exceptions=exceptions):
                rule = deepcopy(RULE)
                rule["payload"]["judgement"]["exceptions"] = exceptions
                result = rules.evaluate(rule, [values], properties=PROPERTIES)[0]
                self.assertEqual(result["outcome"], expected)
                self.assertEqual(result["label"], rules.OUTCOMES[expected])
                self.assertEqual(result["notice"], rules.VERIFICATION_NOTICE if expected == "violate" else "")

    def test_save_generates_sentence_without_mutating_input(self) -> None:
        prepared = rules.prepare_payload("rule", RULE["payload"], properties=PROPERTIES)
        self.assertEqual(prepared["judgement"]["condition"]["sentence"], "合同金额 大于 200000 元")
        self.assertNotIn("sentence", RULE["payload"]["judgement"]["condition"])
        prepared["judgement"]["condition"]["sentence"] = "人工核对后的中文"
        again = rules.prepare_payload("rule", prepared, properties=PROPERTIES)
        self.assertEqual(again["judgement"]["condition"]["sentence"], "人工核对后的中文")

    def test_two_test_cases_run_and_gate_changes_only_when_all_pass(self) -> None:
        payload = deepcopy(RULE["payload"])
        payload["judgement"]["test_cases"][1]["expected"] = "violate"
        failed = rules.run_test_cases("rule", payload, properties=PROPERTIES)
        self.assertFalse(failed["passed"])
        self.assertEqual(failed["passed_count"], 1)
        self.assertEqual(failed["results"][1]["actual"], "needs_review")
        payload["judgement"]["test_cases"][1]["expected"] = "needs_review"
        passed = rules.run_test_cases("rule", payload, properties=PROPERTIES)
        self.assertTrue(passed["passed"])
        self.assertEqual(passed["passed_count"], 2)
        self.assertEqual(passed["total"], 2)
        # Counterexample: the actual action assertion fails on incorrect expected output.
        with self.assertRaises(AssertionError):
            self.assertTrue(failed["passed"])
        # Counterexample: a superficially green count cannot hide incorrect individual results.
        mutated = deepcopy(passed)
        mutated["results"][1]["actual"] = "violate"
        with self.assertRaises(AssertionError):
            self.assertEqual(mutated["results"][1]["actual"], "needs_review")

    def test_blocked_specification_table(self) -> None:
        for changes in BLOCKED_CASES:
            with self.subTest(changes=changes):
                payload = deepcopy(RULE["payload"])
                payload["judgement"].update(changes)
                self.assertFalse(rules.run_test_cases("rule", payload, properties=PROPERTIES)["passed"])

    def test_result_keeps_evidence_and_beijing_as_of(self) -> None:
        row = {"input": {"amount": 300000}, "object_ref": {"dataset_id": "DS-test", "row_no": 8}}
        result = rules.evaluate(RULE, [row], "2026-11-17T16:00:00Z", properties=PROPERTIES)[0]
        self.assertEqual(result["object_ref"], row["object_ref"])
        self.assertEqual(result["values"], {"amount": 300000, "approval_ready": None})
        self.assertEqual(result["missing_property_names"], ["审批材料齐备"])
        self.assertEqual(result["rule_version"], 3)
        self.assertEqual(result["valid_from"], "2026-01-01")
        self.assertEqual(result["valid_to"], "2026-12-31")
        self.assertEqual(result["as_of"], "2026-11-18")

    def test_metric_test_cases_and_strict_expected_boolean(self) -> None:
        payload = {
            "formula": {"oxl": "max(0, amount - paid)"},
            "test_cases": [
                {"name": "部分支付", "input": {"amount": 100, "paid": 30}, "expected": 70},
                {"name": "全部支付", "input": {"amount": 100, "paid": 100}, "expected": 0},
            ],
        }
        prepared = rules.prepare_payload("metric", payload, properties=["amount", "paid"])
        self.assertIn("sentence", prepared["formula"])
        result = rules.run_test_cases("metric", payload, properties=["amount", "paid"])
        self.assertTrue(result["passed"])
        self.assertEqual([case["actual"] for case in result["results"]], [70, 0])
        payload["test_cases"][1]["expected"] = False
        self.assertFalse(rules.run_test_cases("metric", payload, properties=["amount", "paid"])["passed"])

    def test_runtime_errors_are_case_failures_not_false_green_or_crashes(self) -> None:
        payload = {
            "formula": {"oxl": "1 / amount"},
            "test_cases": [
                {"input": {"amount": 0}, "expected": None},
                {"input": {"amount": 2}, "expected": 0.5},
            ],
        }
        result = rules.run_test_cases("metric", payload, properties=PROPERTIES)
        self.assertFalse(result["passed"])
        self.assertEqual(result["passed_count"], 1)
        self.assertEqual(result["results"][0]["error_code"], "calculation")
        self.assertEqual(result["results"][1]["actual"], 0.5)

    def test_description_only_rule_can_save_but_cannot_execute(self) -> None:
        payload = {"rule_text": "请保留审批材料。"}
        self.assertEqual(rules.prepare_payload("rule", payload), payload)
        self.assertFalse(rules.run_test_cases("rule", payload)["passed"])
        with self.assertRaises(oxl.OXLError):
            rules.evaluate(payload, [{}])

    def test_input_does_not_grant_property_declaration(self) -> None:
        result = rules.run_test_cases("rule", RULE["payload"])
        self.assertFalse(result["passed"])
        self.assertIn("未在业务对象中声明", result["errors"][0])
        with self.assertRaises(oxl.OXLError):
            rules.prepare_payload("rule", RULE["payload"])

    def test_aggregate_rule_records_related_missing_values(self) -> None:
        rule = deepcopy(RULE)
        rule["payload"]["judgement"]["condition"] = {"oxl": "true"}
        rule["payload"]["judgement"]["requirement"] = {"oxl": "sum(payments.amount) <= amount"}
        result = rules.evaluate(
            rule,
            [{"input": {"amount": 100}, "related": {"payments": [{"amount": 10}, {}]}}],
            properties=PROPERTIES,
            relations={"payments": PROPERTIES},
        )[0]
        self.assertEqual(result["outcome"], "needs_review")
        self.assertEqual(result["related_values"], {"payments.amount": [10, None]})
        self.assertIn("payments.amount", result["missing_properties"])

    def test_irrelevant_exception_and_requirement_do_not_execute(self) -> None:
        rule = deepcopy(RULE)
        rule["payload"]["judgement"]["exceptions"] = [{"oxl": "1 / 0 == 1"}]
        rule["payload"]["judgement"]["requirement"] = {"oxl": "1 / 0 == 1"}
        self.assertEqual(rules.evaluate(rule, [{"amount": 0}], properties=PROPERTIES)[0]["outcome"], "not_applicable")
        rule["payload"]["judgement"]["exceptions"].insert(0, {"oxl": "true"})
        self.assertEqual(
            rules.evaluate(rule, [{"amount": 300000}], properties=PROPERTIES)[0]["outcome"], "not_applicable"
        )

    def test_malformed_related_input_is_controlled_case_failure(self) -> None:
        payload = deepcopy(RULE["payload"])
        payload["judgement"]["condition"] = {"oxl": "count(payments) > 1"}
        for value in ([], "bad", {"payments": [1]}, {"payments": {"amount": 1}}):
            with self.subTest(value=value):
                payload["judgement"]["test_cases"][0]["related"] = value
                result = rules.run_test_cases(
                    "rule", payload, properties=PROPERTIES, relations={"payments": PROPERTIES}
                )
                self.assertFalse(result["passed"])
                self.assertEqual(result["results"][0]["error_code"], "value")
