"""R4-A A6 release decisions: examples are the contract, before implementation."""

import copy
import unittest
from collections.abc import Callable

from backend.kernel import gates


def complete_rule() -> dict:
    return {
        "id": "R",
        "kind": "rule",
        "title": "办理时限",
        "version": 1,
        "status": "draft",
        "payload": {
            "definition": "核对办理期限。",
            "scope": "本机构",
            "conditions": ["收到材料"],
            "exceptions": "无例外",
            "source_refs": [{"anchor_id": "A"}],
        },
    }


def complete_facts() -> dict:
    return {
        "evidence": True,
        "regulation_evidence": True,
        "step_evidence": True,
        "duplicate": False,
        "relations": True,
        "snapshot": True,
        "tests": True,
    }


# Five valid decisions and five invalid decisions explicitly precede the implementation.
ALLOW = [
    ("rule", {}, {}),
    ("knowledge", {"conditions": ["有原始记录"], "boundary": "只供核对"}, {}),
    ("term", {}, {}),
    ("data", {"unit": "元", "grain": "每笔付款"}, {}),
    ("metric", {"unit": "元", "grain": "每份合同", "formula": {"oxl": "1"}}, {}),
]
BLOCK = [
    ("QG1", {}, {"evidence": False}),
    ("QG2", {"definition": " "}, {}),
    ("QG3", {"exceptions": " "}, {}),
    ("QG4", {}, {"relations": False}),
    ("QG5", {}, {"snapshot": False}),
]


class GateDecisionTable(unittest.TestCase):
    def result(
        self, kind: str = "rule", payload: dict | None = None, facts: dict | None = None, meta: dict | None = None
    ) -> dict:
        entity = complete_rule()
        entity["kind"] = kind
        entity["payload"].update(payload or {})
        return gates.evaluate_checks(
            entity, {"valid_from": "2026-11-17", **(meta or {})}, {**complete_facts(), **(facts or {})}
        )

    def test_allow_table(self) -> None:
        for kind, payload, facts in ALLOW:
            with self.subTest(kind=kind):
                self.assertTrue(self.result(kind, payload, facts)["passed"])

    def test_block_table(self) -> None:
        for code, payload, facts in BLOCK:
            with self.subTest(code=code):
                result = self.result(payload=payload, facts=facts)
                self.assertFalse(result["passed"])
                self.assertEqual(next(c for c in result["checks"] if c["code"] == code)["status"], "fail")

    def test_assertion_rejects_false_green_counterexample(self) -> None:
        result = self.result(facts={"relations": False})
        with self.assertRaises(AssertionError):
            self.assertTrue(result["passed"])
        changed = copy.deepcopy(result)
        changed["checks"][3]["status"] = "pass"
        with self.assertRaises(AssertionError):
            self.assertEqual(changed["checks"][3]["status"], "fail")


def _case(kind: str, payload: dict, facts: dict, meta: dict, passed: bool, failing: list[str]) -> Callable:
    def test(self: GateDecisionTable) -> None:
        result = self.result(kind, payload, facts, meta)
        self.assertEqual(result["passed"], passed)
        self.assertEqual({c["code"] for c in result["checks"] if c["status"] == "fail"}, set(failing))

    return test


# Twenty-two independent boundary cases plus the three table/counterexample tests.
CASES = [
    ("missing_date", "rule", {}, {}, {"valid_from": ""}, False, ["QG3"]),
    ("bad_date", "rule", {}, {}, {"valid_from": "tomorrow"}, False, ["QG3"]),
    ("backwards_dates", "rule", {}, {}, {"valid_to": "2020-01-01"}, False, ["QG3"]),
    ("missing_scope", "rule", {"scope": ""}, {}, {}, False, ["QG3"]),
    ("missing_condition", "rule", {"conditions": []}, {}, {}, False, ["QG3"]),
    (
        "hard_constraint_without_regulation",
        "rule",
        {"content": "应在30日内办理"},
        {"regulation_evidence": False},
        {},
        False,
        ["QG1"],
    ),
    ("duplicate_definition", "term", {}, {"duplicate": True}, {}, False, ["QG2"]),
    ("data_missing_unit", "data", {"grain": "一笔"}, {}, {}, False, ["QG2"]),
    ("data_missing_grain", "data", {"unit": "元"}, {}, {}, False, ["QG2"]),
    ("object_missing_identity", "object_type", {}, {}, {}, False, ["QG2"]),
    (
        "object_with_identity",
        "object_type",
        {"identity": {"properties": ["number"]}, "properties": [{"key": "number"}]},
        {},
        {},
        True,
        [],
    ),
    (
        "metric_failed_tests",
        "metric",
        {"unit": "元", "grain": "一笔", "formula": {"oxl": "1"}},
        {"tests": False},
        {},
        False,
        ["QG3"],
    ),
    ("rule_failed_tests", "rule", {"judgement": {"condition": {"oxl": "true"}}}, {"tests": False}, {}, False, ["QG3"]),
    ("knowledge_missing_boundary", "knowledge", {}, {}, {}, False, ["QG3"]),
    ("skill_missing_steps", "skill", {"inputs": ["合同"], "outputs": ["结果"]}, {}, {}, False, ["QG3"]),
    ("process_missing_steps", "process", {"trigger": "收到", "end_result": "办结"}, {}, {}, False, ["QG3"]),
    (
        "process_missing_step_source",
        "process",
        {
            "trigger": "收到",
            "end_result": "办结",
            "steps": [
                {
                    "key": "a",
                    "actors": [{"org_id": "hq"}],
                    "outputs": [{"note": "登记"}],
                    "confirmed": True,
                    "next": ["b"],
                },
                {
                    "key": "b",
                    "actors": [{"org_id": "hq"}],
                    "outputs": [{"note": "结果"}],
                    "confirmed": True,
                    "next": [],
                },
            ],
        },
        {"step_evidence": False},
        {},
        False,
        ["QG1"],
    ),
    ("material_exempt", "material", {}, {"evidence": False, "snapshot": False}, {}, True, []),
    ("organization_exempt", "organization", {}, {"evidence": False, "snapshot": False}, {}, True, []),
    ("published_keeps_status", "rule", {}, {"evidence": False}, {}, False, ["QG1"]),
    ("position_requires_identity", "position", {}, {}, {}, False, ["QG2"]),
    ("party_identity", "party", {"unified_code": "123"}, {"evidence": False}, {}, True, []),
]
for _name, *_arguments in CASES:
    setattr(GateDecisionTable, "test_" + _name, _case(*_arguments))
