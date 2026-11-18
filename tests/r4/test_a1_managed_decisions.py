"""Managed-skill D11 decisions stay inside the version-bound specialist transaction."""

import json
import os
import secrets
import unittest
from contextlib import closing
from dataclasses import replace
from unittest.mock import patch

from backend import db
from backend.kernel import gates
from backend.security import store
from backend.security.context import use_context
from tests.r4 import test_a1_specialized_review as specialized
from tests.test_skills import MANIFEST


@unittest.skipUnless(os.getenv("RUN_JOBS_MYSQL_TESTS") == "1", "opt-in managed-skill HTTP/MySQL workflow")
class ManagedDecisionsHTTP(unittest.TestCase):
    setUpClass = specialized.SpecializedReviewHTTP.__dict__["setUpClass"]
    tearDownClass = specialized.SpecializedReviewHTTP.__dict__["tearDownClass"]
    login = specialized.SpecializedReviewHTTP.login
    invoke = specialized.SpecializedReviewHTTP.invoke
    request = specialized.SpecializedReviewHTTP.request

    def ready(self) -> tuple:
        admin, builder, reviewer = (
            self.login(name) for name in ("fixture.admin", "fixture.builder", "fixture.reviewer")
        )
        name = "a1-decisions-" + secrets.token_hex(4)
        detail = self.request(admin, "POST", "/api/skills", json={"title": name, "package_name": name})
        ident = detail["entity"]["id"]
        manifest = {
            **MANIFEST,
            "applicability": "收到合同台账时",
            "inputs": {"type": "object", "properties": {"amount": {"type": "number", "description": "合同金额"}}},
            "outputs": {"type": "object", "properties": {"note": {"type": "string", "description": "核对说明"}}},
            "dependency_note": "只核对显式输入，无其他业务依赖。",
        }
        detail = self.request(
            builder,
            "PUT",
            f"/api/skills/{ident}",
            json={
                "expected_version": 1,
                "title": name,
                "files": {
                    **detail["files"],
                    "SKILL.md": detail["files"]["SKILL.md"] + "\n内部包文件正文不应混入门禁字段。\n",
                },
                "manifest": manifest,
                "reason": "如实声明方法，手工说明尚无原文",
            },
        )
        proof = self.request(builder, "POST", f"/api/skills/{ident}/test", json={"expected_version": 2})
        self.assertEqual(proof["outcome"], "passed")
        self.invoke(
            builder,
            "element.update_draft",
            {"entity_id": ident, "version": 2, "waiver_applications": {"QG1": "手工工作方法，暂缺可定位原文"}},
        )
        detail = self.request(
            builder,
            "POST",
            f"/api/skills/{ident}/action",
            json={"action": "submit", "expected_version": 3, "reason": "提交独立核对"},
        )
        self.assertEqual(detail["entity"]["status"], "review")
        self.assertTrue(detail["gates"]["ready_to_submit"])
        self.assertFalse(detail["gates"]["passed"])
        return admin, builder, reviewer, detail

    def test_dedicated_review_publishes_with_application_and_visible_true_facts(self) -> None:
        admin, _builder, reviewer, detail = self.ready()
        ident = detail["entity"]["id"]
        author_view = self.request(admin, "GET", f"/api/skills/{ident}")
        reviewer_view = self.request(reviewer, "GET", f"/api/skills/{ident}")
        self.assertFalse(author_view["capabilities"]["review"])
        self.assertTrue(reviewer_view["capabilities"]["review"])
        fields = {item["key"]: item for item in reviewer_view["gates"]["fields"]}
        self.assertEqual(fields["definition"]["value"], detail["entity"]["title"])
        self.assertEqual(json.loads(fields["inputs"]["value"]), detail["manifest"]["inputs"])
        self.assertEqual(json.loads(fields["outputs"]["value"]), detail["manifest"]["outputs"])
        self.assertIn(MANIFEST["steps"][0]["instruction"], fields["steps"]["value"])
        self.assertEqual(fields["usage_conditions"]["value"], "收到合同台账时")
        self.assertEqual(fields["inputs"]["source"], "editor")
        self.assertEqual(fields["inputs"]["confirmed_by"], "fixture.builder")
        self.assertEqual(fields["definition"]["confirmed_by"], "fixture.admin")
        self.assertNotIn("内部包文件正文", str(fields))
        person = store.authenticate_session(reviewer.cookies.get("ontology_session"), "r2-test")[0]
        with use_context(replace(person, readonly=True)), closing(db.connect()) as conn:
            entity = db.entity_json(conn.execute("SELECT * FROM entities WHERE id=?", (ident,)).fetchone())
            self.assertEqual(gates.check_entity(conn, entity)["fields"], [])
        result = self.request(
            reviewer,
            "POST",
            f"/api/skills/{ident}/action",
            json={
                "action": "publish",
                "expected_version": 4,
                "reason": "完成专用审核",
                "waivers": {"QG1": "独立核对方法后接受原文缺口"},
            },
        )
        self.assertEqual(result["entity"]["status"], "published")
        self.assertTrue(result["gates"]["passed"])
        first = result["gates"]["checks"][0]
        self.assertEqual(first["status"], "waived")
        self.assertIn("手工工作方法", first["application_reason"])
        self.assertEqual(first["waiver_reason"], "独立核对方法后接受原文缺口")
        self.assertEqual({row["action"] for row in result["gate_history"]}, {"申请发布检查豁免", "发布检查豁免"})
        generic = reviewer.post(f"/api/v2/reviews/{ident}:decide", json={"decision": "approve_publish", "version": 5})
        self.assertEqual(generic.status_code, 409, generic.text)

    def test_existing_independent_waiver_capability_still_requires_specialized_publication(self) -> None:
        admin, _, reviewer, detail = self.ready()
        ident = detail["entity"]["id"]
        body = {"entity_id": ident, "version": 4, "reason": "独立核对", "waivers": {"QG1": "独立核对并接受缺口"}}
        denied = admin.post("/api/v2/capabilities/quality.waive_gates:invoke", json=body)
        self.assertEqual(denied.status_code, 403, denied.text)
        result = self.invoke(reviewer, "quality.waive_gates", body)
        self.assertTrue(result["passed"])
        generic = reviewer.post(f"/api/v2/reviews/{ident}:decide", json={"decision": "approve_publish", "version": 4})
        self.assertEqual(generic.status_code, 403, generic.text)
        published = self.request(
            reviewer,
            "POST",
            f"/api/skills/{ident}/action",
            json={"action": "publish", "expected_version": 4, "reason": "完成技能专用审核"},
        )
        self.assertEqual(published["entity"]["status"], "published")
        self.assertTrue(published["gates"]["passed"])

    def test_independent_denial_returns_visible_receipt_and_requires_renewal(self) -> None:
        _, builder, reviewer, detail = self.ready()
        ident = detail["entity"]["id"]
        returned = self.request(
            reviewer,
            "POST",
            f"/api/skills/{ident}/action",
            json={
                "action": "return",
                "expected_version": 4,
                "reason": "请补充独立核对记录",
                "waiver_denials": {"QG1": "现有说明不足以接受出处缺口"},
            },
        )
        self.assertEqual(returned["entity"]["status"], "returned")
        self.assertFalse(returned["gates"]["ready_to_submit"])
        self.assertEqual(returned["gates"]["checks"][0]["denial_reason"], "现有说明不足以接受出处缺口")
        blocked = builder.post(
            f"/api/skills/{ident}/action", json={"action": "submit", "expected_version": 5, "reason": "原申请直接重提"}
        )
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.invoke(
            builder,
            "element.update_draft",
            {"entity_id": ident, "version": 5, "waiver_applications": {"QG1": "已补充独立核对记录，重新申请"}},
        )
        renewed = self.request(
            builder,
            "POST",
            f"/api/skills/{ident}/action",
            json={"action": "submit", "expected_version": 6, "reason": "重新提交"},
        )
        self.assertEqual(renewed["gates"]["checks"][0]["denial_reason"], "")
        self.assertTrue(any(row["action"] == "拒绝发布检查豁免" for row in renewed["gate_history"]))

    def test_invalid_roles_versions_and_decisions_never_record_waivers(self) -> None:
        admin, builder, reviewer, detail = self.ready()
        ident = detail["entity"]["id"]
        baseline = {"action": "publish", "expected_version": 4, "reason": "核对", "waivers": {"QG1": "接受缺口"}}
        attempts = [
            (admin, baseline, 403),
            (builder, baseline, 403),
            (reviewer, {**baseline, "expected_version": 3}, 409),
            (reviewer, {**baseline, "waiver_denials": {"QG1": "不能接受"}}, 422),
            (reviewer, {**baseline, "action": "return", "waiver_denials": {"QG1": "同项冲突"}}, 422),
            (reviewer, {**baseline, "waivers": {"QG1": " "}}, 422),
            (reviewer, {**baseline, "waivers": {"QG3": "没有此项有效申请"}}, 409),
            *((reviewer, {**baseline, "waivers": {code: "硬门禁不能豁免"}}, 422) for code in ("QG2", "QG4", "QG5")),
        ]
        for actor, body, status in attempts:
            with self.subTest(body=body):
                response = actor.post(f"/api/skills/{ident}/action", json=body)
                self.assertEqual(response.status_code, status, response.text)
        current = self.request(reviewer, "GET", f"/api/skills/{ident}")
        self.assertEqual(current["entity"]["version"], 4)
        self.assertEqual(current["entity"]["status"], "review")
        self.assertEqual(current["gates"]["checks"][0]["status"], "fail")
        self.assertEqual([row["action"] for row in current["gate_history"]], ["申请发布检查豁免"])

    def test_hard_gate_and_late_failure_roll_back_per_check_decisions(self) -> None:
        _, builder, reviewer, detail = self.ready()
        ident = detail["entity"]["id"]
        duplicate = self.request(
            builder, "POST", "/api/skills", json={"title": detail["entity"]["title"], "summary": "同名但不同的业务定义"}
        )
        body = {"action": "publish", "expected_version": 4, "reason": "核对", "waivers": {"QG1": "接受手工方法说明"}}
        blocked = reviewer.post(f"/api/skills/{ident}/action", json=body)
        self.assertEqual(blocked.status_code, 409, blocked.text)
        current = self.request(reviewer, "GET", f"/api/skills/{ident}")
        self.assertEqual(current["gates"]["checks"][1]["status"], "fail")
        self.assertEqual([row["action"] for row in current["gate_history"]], ["申请发布检查豁免"])
        self.request(
            builder,
            "POST",
            f"/api/skills/{duplicate['entity']['id']}/action",
            json={"action": "archive", "expected_version": 1, "reason": "不用同名草稿"},
        )
        with (
            patch("backend.skills._history_event", side_effect=RuntimeError("synthetic late persistence failure")),
            self.assertLogs("backend.security.middleware", level="ERROR"),
        ):
            failed = reviewer.post(f"/api/skills/{ident}/action", json=body)
        self.assertEqual(failed.status_code, 500, failed.text)
        current = self.request(reviewer, "GET", f"/api/skills/{ident}")
        self.assertEqual(current["entity"]["version"], 4)
        self.assertEqual(current["entity"]["status"], "review")
        self.assertEqual(current["gates"]["checks"][0]["status"], "fail")
        self.assertEqual([row["action"] for row in current["gate_history"]], ["申请发布检查豁免"])
