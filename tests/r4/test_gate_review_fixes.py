"""Adversarial review regressions: semantic constraints and legacy storage shapes."""

import copy
import os
import unittest
from contextlib import closing

from backend import db
from backend.kernel import gates
from backend.security import store
from backend.security.context import use_context
from scripts.check_security import upload_evidence
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

    def test_exception_and_invalid_expression_cannot_hide_constraint(self) -> None:
        entity = structured("approved")
        entity["payload"]["judgement"]["exceptions"] = [{"oxl": "amount == 0"}]
        self.assertTrue(gates.hard_constraint(entity, expression_schema=SCHEMA))
        self.assertFalse(
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

    def test_amount_definitions_are_not_limits_but_explicit_prose_constraints_remain_hard(self) -> None:
        for kind in ("data", "metric", "rule", "object_type", "knowledge"):
            for prose in (
                "模拟合同约定的人民币金额，单位为元；不是付款金额。",
                "累计已付款金额，以元记录。",
                "本次付款金额，字段为payment_yuan。",
                "金额之差",
                "合同金额，单位为元。",
                "合同金额，单位为万元。",
                "合同金额，单位为亿元。",
            ):
                with self.subTest(kind=kind, prose=prose):
                    self.assertFalse(gates.hard_constraint({"kind": kind, "payload": {"definition": prose}}))
            for prose in (
                "付款金额不得超过合同金额。",
                "付款金额不超过合同金额。",
                "累计付款金额上限为合同金额。",
                "付款金额不能低于合同约定最低值。",
                "审批权限按岗位确定。",
                "应在30日内完成。",
                "本次付款不得超过100万元。",
                "金额阈值以正式制度为准。",
                "金额超过合同金额时需核实。",
                "补助金额十万元。",
                "补助金额100000元。",
                "拨款金额一亿元。",
            ):
                with self.subTest(kind=kind, prose=prose):
                    self.assertTrue(gates.hard_constraint({"kind": kind, "payload": {"definition": prose}}))
        self.assertTrue(gates.hard_constraint({"kind": "rule", "payload": {"authority_spec": {"type": "workflow"}}}))

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

    def test_dataset_backed_amount_fields_pass_but_same_field_limit_requires_regulation(self) -> None:
        client = self.login("fixture.builder")
        ref = {
            "kind": "dataset",
            "dataset_id": "DEMO07-DATASET-CONTRACTS",
            "sheet": "数据表",
            "column": "合同金额（元）",
        }
        for field_name, title in (
            ("amount_yuan", "合同金额"),
            ("paid_yuan", "累计已付款金额"),
            ("payment_yuan", "本次付款金额"),
        ):
            with self.subTest(field_name=field_name):
                payload = {
                    "data_form": "standard_field",
                    "field_name": field_name,
                    "definition": "模拟合同约定的人民币金额，单位为元；不是付款金额。",
                    "unit": "元",
                    "grain": "每份合同",
                    "source_refs": [ref],
                }
                created = self.invoke(
                    client,
                    "element.create_draft",
                    {
                        "kind": "data",
                        "title": "【演示】" + title + " · 出处核对用",
                        "summary": payload["definition"],
                        "payload": payload,
                    },
                )
                ident = created["entity"]["id"]
                checks = self.invoke(client, "quality.check_gates", {"entity_id": ident})
                self.assertFalse(checks["hard_constraint"])
                self.assertTrue(checks["passed"], checks)
                self.assertEqual(next(c["status"] for c in checks["checks"] if c["code"] == "QG1"), "pass")
                changed = self.invoke(
                    client,
                    "element.update_draft",
                    {
                        "entity_id": ident,
                        "version": created["entity"]["version"],
                        "payload": {**payload, "definition": "付款金额不得超过合同金额。"},
                    },
                )
                self.assertEqual(changed["entity"]["id"], ident)
                self.assertEqual(changed["entity"]["payload"]["source_refs"], [ref])
                constrained = self.invoke(client, "quality.check_gates", {"entity_id": ident})
                self.assertTrue(constrained["hard_constraint"])
                self.assertEqual([c["code"] for c in constrained["checks"] if c["status"] == "fail"], ["QG1"])
                denied = client.post("/api/entities/" + ident + "/action", json={"action": "submit"})
                self.assertEqual(denied.status_code, 409, denied.text)
                self.assertIn("出处可定位", denied.text)
                self.assertEqual(client.get("/api/entities/" + ident).json()["status"], "draft")

    def test_structured_threshold_rejects_interview_and_accepts_regulation_with_iso_validity(self) -> None:
        client = self.login("fixture.builder")
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
        text = "访谈中提出每次登记的值不超过100000，仍需核对正式依据。"
        interview = client.post(
            "/api/experience/sessions", json={"title": "登记工作访谈", "context": text, "mode": "structured"}
        )
        self.assertEqual(interview.status_code, 200, interview.text)
        session = interview.json()
        self.assertEqual(session["entity"]["payload"]["material_type"], "经验访谈记录")
        evidence = client.get(
            f"/api/materials/{session['entity']['id']}/source", params={"chunk_id": session["chunks"][0]["id"]}
        )
        self.assertEqual(evidence.status_code, 200, evidence.text)
        focus = evidence.json()["focus"]
        self.assertEqual(focus["quote"], text)
        ref = {
            "kind": "anchor",
            **{
                key: focus[key]
                for key in ("material_id", "source_version", "anchor_id", "start", "end", "quote", "locator")
            },
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
        rejected = self.invoke(client, "quality.check_gates", {"entity_id": ident})
        self.assertTrue(rejected["hard_constraint"])
        self.assertEqual(next(c["status"] for c in rejected["checks"] if c["code"] == "QG1"), "fail")
        self.assertEqual(next(c["status"] for c in rejected["checks"] if c["code"] == "QG3"), "pass")
        response = client.post("/api/entities/" + ident + "/action", json={"action": "submit", "reason": "核对出处"})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(client.get("/api/entities/" + ident).json()["status"], "draft")
        reviewer = self.login("fixture.reviewer")
        queued = self.invoke(
            client,
            "element.submit_draft",
            {
                "entity_id": ident,
                "version": 1,
                "reason": "请独立核对访谈依据",
                "waiver_applications": {"QG1": "试行阶段只有真实访谈记录，申请单项核对"},
            },
        )
        self.assertEqual(queued["entity"]["status"], "review")
        waived = self.invoke(
            reviewer,
            "quality.waive_gates",
            {
                "entity_id": ident,
                "version": queued["entity"]["version"],
                "reason": "核对试行要求",
                "waivers": {"QG1": "试行阶段接受访谈依据，发布后补制度原文"},
            },
        )
        self.assertTrue(waived["hard_constraint"])
        self.assertEqual(next(c["status"] for c in waived["checks"] if c["code"] == "QG1"), "waived")
        self.assertTrue(self.invoke(client, "quality.check_gates", {"entity_id": ident})["hard_constraint"])
        returned = reviewer.post(
            f"/api/v2/reviews/{ident}:decide",
            json={
                "decision": "return",
                "version": queued["entity"]["version"],
                "reason": "请保存正式制度原文，再次送审",
            },
        )
        self.assertEqual(returned.status_code, 200, returned.text)
        regulation = upload_evidence(
            lambda method, path, **kw: client.request(method, "/api" + path, **kw),
            "登记管理办法规定，每次核对时记录值不得超过100000。",
        )
        latest = client.get("/api/entities/" + ident).json()
        self.invoke(
            client,
            "element.update_draft",
            {
                "entity_id": ident,
                "version": latest["version"],
                "reason": "补充正式制度依据",
                "payload": {**latest["payload"], "source_refs": [regulation]},
            },
        )
        accepted = self.invoke(client, "quality.check_gates", {"entity_id": ident})
        self.assertTrue(accepted["hard_constraint"])
        self.assertEqual(next(c["status"] for c in accepted["checks"] if c["code"] == "QG1"), "pass")
        broken = copy.deepcopy(accepted)
        broken["hard_constraint"] = False
        with self.assertRaises(AssertionError):
            self.assertTrue(broken["hard_constraint"])
