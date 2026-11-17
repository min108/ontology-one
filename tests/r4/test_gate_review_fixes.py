"""Adversarial review regressions: semantic constraints and legacy storage shapes."""

import copy
import os
import unittest
from contextlib import closing

from backend import db
from backend.kernel import gates
from backend.security import store
from backend.security.context import use_context
from tests.r2 import test_build as construction

SCHEMA = {
    "properties": [
        {"key": "amount", "name": "值", "datatype": "decimal"},
        {"key": "budget", "name": "比较值", "datatype": "decimal"},
        {"key": "opened", "name": "登记日期", "datatype": "date"},
        {"key": "closed", "name": "结束日期", "datatype": "date"},
        {"key": "approved", "name": "已核对", "datatype": "boolean"},
        {"key": "category", "name": "类别", "datatype": "string"},
    ],
    "relations": {"payments": [{"key": "amount", "datatype": "decimal"}]},
}


def structured(expression: str, *, field: str = "requirement") -> dict:
    return {
        "kind": "rule",
        "title": "记录核对",
        "payload": {"definition": "核对记录", "judgement": {field: {"oxl": expression, "sentence": "核对记录"}}},
    }


class GateReviewTable(unittest.TestCase):
    def test_numeric_dates_and_arithmetic_require_regulation_without_prose_clues(self) -> None:
        for expression in (
            "amount <= 100000",
            "amount <= budget",
            "amount - budget <= 0",
            "days_between(opened, closed) <= 30",
            "today() > closed",
            "sum(payments.amount) <= amount",
            "closed <= '2026-12-31'",
        ):
            with self.subTest(expression=expression):
                self.assertTrue(gates.hard_constraint(structured(expression), expression_schema=SCHEMA))

    def test_boolean_text_checks_do_not_invent_hard_constraints_from_test_data(self) -> None:
        for expression in (
            "approved == true",
            "category == 'ordinary'",
            "not approved",
            "category in ['a', 'b']",
            "is_null(category)",
        ):
            entity = structured(expression)
            entity["payload"]["judgement"]["test_cases"] = [{"input": {"amount": 999999}, "expected": "pass"}]
            with self.subTest(expression=expression):
                self.assertFalse(gates.hard_constraint(entity, expression_schema=SCHEMA))

    def test_exception_formula_and_invalid_expression_cannot_hide_constraint(self) -> None:
        entity = structured("approved")
        entity["payload"]["judgement"]["exceptions"] = [{"oxl": "amount == 0"}]
        self.assertTrue(gates.hard_constraint(entity, expression_schema=SCHEMA))
        self.assertTrue(
            gates.hard_constraint(
                {"kind": "metric", "payload": {"formula": {"oxl": "amount - budget", "sentence": "金额之差"}}},
                expression_schema=SCHEMA,
            )
        )
        self.assertTrue(gates.hard_constraint(structured("unknown <= 10"), expression_schema=SCHEMA))
        self.assertTrue(gates.hard_constraint({"kind": "rule", "payload": {"conditions": ["30日内完成"]}}))

    def test_ordinary_metric_aggregation_is_not_a_rule_threshold(self) -> None:
        for expression in ("count(records)", "sum(records.quantity)", "1 + 2"):
            entity = {
                "kind": "metric",
                "title": "记录统计",
                "payload": {
                    "definition": "记录统计口径",
                    "unit": "条",
                    "grain": "每组",
                    "formula": {"oxl": expression, "sentence": "汇总记录"},
                },
            }
            with self.subTest(expression=expression):
                self.assertFalse(gates.hard_constraint(entity))
                result = gates.evaluate_checks(
                    entity,
                    {},
                    {
                        "evidence": True,
                        "regulation_evidence": False,
                        "tests": True,
                        "relations": True,
                        "snapshot": True,
                    },
                )
                self.assertEqual(next(c["status"] for c in result["checks"] if c["code"] == "QG1"), "pass")
                self.assertFalse(result["hard_constraint"])
        self.assertTrue(gates.hard_constraint(structured("count(payments) <= 5"), expression_schema=SCHEMA))
        self.assertTrue(gates.hard_constraint(structured("amount <= 100000"), expression_schema=SCHEMA))
        # Missing declarations must not silently downgrade a rule's requirement.
        self.assertTrue(gates.hard_constraint(structured("amount <= 100000")))

    def test_qg3_accepts_iso_dates_and_timestamps_and_compares_actual_instants(self) -> None:
        for meta in (
            {"valid_from": "2026-01-01"},
            {"valid_from": "2026-01-01T00:00:00Z"},
            {"valid_from": "2026-01-01T08:00:00+08:00", "valid_to": "2026-01-01T01:00:00+00:00"},
            {"valid_from": "2026-01-01T00:00:00", "valid_to": "2026-01-02"},
            {"valid_from": " 2026-01-01 ", "valid_to": None},
        ):
            with self.subTest(meta=meta):
                self.assertTrue(gates._dates(meta))
        for meta in (
            {},
            {"valid_from": "tomorrow"},
            {"valid_from": "2026-02-30"},
            {"valid_from": "2026-01-02", "valid_to": "2026-01-01"},
            {"valid_from": "2026-01-01T01:00:00Z", "valid_to": "2026-01-01T08:00:00+08:00"},
        ):
            with self.subTest(meta=meta):
                self.assertFalse(gates._dates(meta))

    def test_malformed_process_steps_are_blocked_without_attribute_or_type_errors(self) -> None:
        for steps in (
            ["核对原件", "保存记录"],
            3,
            {"key": "s"},
            [{"key": []}, {"key": "s"}],
            [{"key": "a", "next": 3}, {"key": "b"}],
        ):
            with self.subTest(steps=steps):
                self.assertFalse(gates._process_complete({"trigger": "收到", "end_result": "完成", "steps": steps}))


@unittest.skipUnless(os.getenv("RUN_JOBS_MYSQL_TESTS") == "1", "opt-in gate review/MySQL integration")
class GateReviewMySQL(unittest.TestCase):
    setUpClass = classmethod(construction.BuildMySQL.setUpClass.__func__)
    tearDownClass = classmethod(construction.BuildMySQL.tearDownClass.__func__)
    login = construction.BuildMySQL.login
    invoke = construction.BuildMySQL.invoke

    def test_actual_element_capability_reads_legacy_string_steps(self) -> None:
        client = self.login("fixture.builder")
        created = self.invoke(
            client,
            "element.create_draft",
            {
                "kind": "knowledge",
                "title": "旧版文字步骤兼容",
                "payload": {
                    "definition": "核对资料的方法",
                    "conditions": "资料齐全",
                    "boundary": "仅供参考",
                    "steps": ["查看原件", "记录发现"],
                },
            },
        )
        ident = created["entity"]["id"]
        response = client.get("/api/v2/elements/" + ident)
        self.assertEqual(response.status_code, 200, response.text)
        detail = response.json()["data"]
        self.assertEqual(detail["entity"]["payload"]["steps"], ["查看原件", "记录发现"])
        self.assertIn("核对资料", detail["card"]["one_liner"])
        self.assertEqual(next(item["status"] for item in detail["gates"]["checks"] if item["code"] == "QG3"), "pass")
        self.assertFalse(self.invoke(client, "quality.check_gates", {"entity_id": ident})["hard_constraint"])

    def test_dataset_anchor_reads_real_sheet_columns_and_rejects_foreign_or_missing_fields(self) -> None:
        client = self.login("fixture.builder")
        person = store.authenticate_session(client.cookies.get("ontology_session"), "dataset-proof")[0]
        ref = {
            "kind": "dataset",
            "dataset_id": "DEMO07-DATASET-CONTRACTS",
            "sheet": "数据表",
            "column": "合同金额（元）",
        }
        with use_context(person), closing(db.connect()) as conn:
            self.assertEqual(gates._valid_reference(conn, ref), (True, False))
            for update in (
                {"dataset_id": "missing"},
                {"sheet": "不存在的表"},
                {"column": "虚构字段"},
                {"sheet": ""},
                {"dataset_id": "DEMO07-MATERIAL-RULES"},
            ):
                with self.subTest(update=update):
                    self.assertEqual(gates._valid_reference(conn, {**ref, **update}), (False, False))
        other = self.login("r2.other")
        other_person = store.authenticate_session(other.cookies.get("ontology_session"), "other-proof")[0]
        with use_context(other_person), closing(db.connect()) as conn:
            self.assertEqual(gates._valid_reference(conn, ref), (False, False))
        created = self.invoke(
            client,
            "element.create_draft",
            {"kind": "knowledge", "title": "台账字段出处", "payload": {"definition": "字段说明", "source_refs": [ref]}},
        )
        checks = created["gates"]["checks"]
        self.assertEqual(next(item["status"] for item in checks if item["code"] == "QG1"), "pass")

    def test_structured_threshold_rejects_interview_and_accepts_regulation_with_iso_validity(self) -> None:
        client = self.login("fixture.builder")
        person = store.authenticate_session(client.cookies.get("ontology_session"), "constraint-proof")[0]
        object_row = self.invoke(
            client,
            "element.create_draft",
            {
                "kind": "object_type",
                "title": "结构判定对象",
                "payload": {
                    "schema": "object_type/1",
                    "properties": [{"key": "amount", "name": "值", "datatype": "decimal", "note": "记录值"}],
                },
            },
        )["entity"]
        with use_context(person), closing(db.connect()) as conn:
            anchor = conn.execute(
                "SELECT a.*,s.text FROM source_anchors a JOIN source_versions s ON s.id=a.source_version LIMIT 1"
            ).fetchone()
        quote = anchor["text"][anchor["start"] : anchor["end"]]
        ref = {
            "kind": "anchor",
            "material_id": anchor["material_id"],
            "source_version": anchor["source_version"],
            "anchor_id": anchor["id"],
            "quote": quote,
        }
        created = self.invoke(
            client,
            "element.create_draft",
            {
                "kind": "rule",
                "title": "结构核对条件",
                "payload": {
                    "definition": "核对记录",
                    "scope": "登记记录",
                    "conditions": "已有记录",
                    "exceptions": "无",
                    "source_refs": [ref],
                    "judgement": {
                        "applies_to": {"object_type": object_row["id"]},
                        "condition": {"oxl": "true"},
                        "requirement": {"oxl": "amount <= 100000"},
                        "exceptions": [],
                        "severity": "must",
                        "test_cases": [
                            {"name": "范围内", "input": {"amount": 100000}, "expected": "pass"},
                            {"name": "范围外", "input": {"amount": 100001}, "expected": "violate"},
                        ],
                    },
                },
                "meta": {"valid_from": "2026-01-01T00:00:00+08:00"},
            },
        )
        ident = created["entity"]["id"]
        with use_context(person), closing(db.connect()) as conn, conn:
            subtype = conn.execute(
                "SELECT subtype FROM entity_meta WHERE entity_id=?", (anchor["material_id"],)
            ).fetchone()["subtype"]
            conn.execute("UPDATE entity_meta SET subtype='interview' WHERE entity_id=?", (anchor["material_id"],))
        try:
            rejected = self.invoke(client, "quality.check_gates", {"entity_id": ident})
            self.assertTrue(rejected["hard_constraint"])
            self.assertEqual(next(c["status"] for c in rejected["checks"] if c["code"] == "QG1"), "fail")
            self.assertEqual(next(c["status"] for c in rejected["checks"] if c["code"] == "QG3"), "pass")
            response = client.post(
                "/api/entities/" + ident + "/action", json={"action": "submit", "reason": "核对出处"}
            )
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(client.get("/api/entities/" + ident).json()["status"], "draft")
            reviewer = self.login("fixture.reviewer")
            waived = self.invoke(
                reviewer,
                "quality.waive_gates",
                {
                    "entity_id": ident,
                    "version": 1,
                    "reason": "核对试行要求",
                    "waivers": {"QG1": "试行阶段接受访谈依据，发布后补制度原文"},
                },
            )
            self.assertTrue(waived["hard_constraint"])
            self.assertEqual(next(c["status"] for c in waived["checks"] if c["code"] == "QG1"), "waived")
            self.assertTrue(self.invoke(client, "quality.check_gates", {"entity_id": ident})["hard_constraint"])
            with use_context(person), closing(db.connect()) as conn, conn:
                conn.execute("UPDATE entity_meta SET subtype='regulation' WHERE entity_id=?", (anchor["material_id"],))
            accepted = self.invoke(client, "quality.check_gates", {"entity_id": ident})
            self.assertTrue(accepted["hard_constraint"])
            self.assertEqual(next(c["status"] for c in accepted["checks"] if c["code"] == "QG1"), "pass")
            broken = copy.deepcopy(accepted)
            broken["hard_constraint"] = False
            with self.assertRaises(AssertionError):
                self.assertTrue(broken["hard_constraint"])
        finally:
            with use_context(person), closing(db.connect()) as conn, conn:
                conn.execute("UPDATE entity_meta SET subtype=? WHERE entity_id=?", (subtype, anchor["material_id"]))
