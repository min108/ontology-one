"""D11/D15 release decisions: public kernel writes, stored results and counterexamples."""

import os
import unittest
from dataclasses import replace

from fastapi.testclient import TestClient

from backend import db
from backend.core.errors import ApiError
from backend.kernel import elements, gates
from backend.kernel.metadata import get_meta
from backend.security.context import use_context
from tests.r2 import test_build as construction
from tests.r4 import test_elements


class SubmissionTable(unittest.TestCase):
    def test_five_eligible_and_five_blocked_cases(self) -> None:
        def result(failed: list[int]) -> dict:
            return {
                "checks": [
                    {"code": f"QG{i}", "status": "fail" if i in failed else "pass", "can_waive": i in {1, 3}}
                    for i in range(1, 6)
                ]
            }

        for failed, applications in (
            ([], {}),
            ([1], {"QG1": "待补原件"}),
            ([3], {"QG3": "人工核对条件"}),
            ([1, 3], {"QG1": "待补原件", "QG3": "人工核对条件"}),
            ([], {"QG1": "说明"}),
        ):
            with self.subTest(failed=failed):
                self.assertTrue(gates.submission_eligibility(result(failed), applications))
        for failed, applications in (
            ([1], {}),
            ([3], {"QG3": " "}),
            ([2], {"QG2": "不能豁免"}),
            ([4], {"QG4": "不能豁免"}),
            ([5], {"QG5": "不能豁免"}),
        ):
            with self.subTest(failed=failed):
                self.assertFalse(gates.submission_eligibility(result(failed), applications))


class ReviewWrites(unittest.TestCase):
    setUp = test_elements.ElementWrites.setUp
    create = test_elements.ElementWrites.create

    def current(self, ident: str) -> dict:
        return db.entity_json(self.conn.execute("SELECT * FROM entities WHERE id=?", (ident,)).fetchone())

    def draft(self) -> dict:
        return self.create(kind="data", payload={"definition": "经核对的合同金额", "unit": "元", "grain": "每份合同"})

    def test_application_submits_but_independent_decision_is_required_to_publish(self) -> None:
        entity = self.draft()
        with self.conn:
            result = gates.apply_for_waiver(self.conn, entity, {"QG1": "手工登记，暂缺可定位原件"})
        self.assertTrue(result["ready_to_submit"])
        self.assertFalse(result["passed"])
        self.assertEqual(result["checks"][0]["status"], "fail")
        with self.conn:
            entity = elements.transition(self.conn, entity, "review", "申请核对")
        reviewer = replace(self.editor, user_id="other", username="other", role="reviewer")
        with use_context(reviewer), self.conn:
            with self.assertRaises(ApiError):
                elements.transition(self.conn, entity, "approved", "尚未豁免")
            self.assertTrue(gates.waive(self.conn, entity, {"QG1": "核对业务记录后接受，原件后补"})["passed"])
            entity = elements.transition(self.conn, entity, "approved", "独立审核通过")
            entity = elements.transition(self.conn, entity, "published", "发布")
        self.assertEqual(self.current(entity["id"])["status"], "published")
        details = [
            row[0] for row in self.conn.execute("SELECT detail FROM activities WHERE entity_id=?", (entity["id"],))
        ]
        self.assertTrue(any("手工登记" in text for text in details))
        self.assertTrue(any("核对业务记录" in text for text in details))

    def test_content_edit_invalidates_application_and_hides_stale_text(self) -> None:
        entity = self.draft()
        with self.conn:
            gates.apply_for_waiver(self.conn, entity, {"QG1": "此内容的申请"})
            entity = elements.update_draft(self.conn, entity, {"summary": "修订正文", "reason": "补充说明"})
        result = gates.check_entity(self.conn, entity)
        self.assertFalse(result["ready_to_submit"])
        self.assertFalse(result["checks"][0].get("application_reason"))
        self.assertIn("此内容的申请", str(get_meta(self.conn, entity["id"])["gates"]))

    def test_all_revision_editors_are_excluded_after_second_editor_submits(self) -> None:
        entity = self.draft()
        second = replace(self.editor, user_id="second", username="second")
        with use_context(second), self.conn:
            entity = elements.update_draft(self.conn, entity, {"summary": "另一位编辑补充标点。"})
            gates.apply_for_waiver(self.conn, entity, {"QG1": "缺原件申请"})
            entity = elements.transition(self.conn, entity, "review", "提交")
        with self.conn, self.assertRaisesRegex(ApiError, "其他审核"):
            gates.waive(self.conn, entity, {"QG1": "原作者不得豁免"})
        with self.conn, self.assertRaises(ApiError):
            elements.transition(self.conn, entity, "approved", "原作者不得通过")
        self.assertEqual(set(entity["payload"]["r2_review"]["editor_ids"]), {self.editor.user_id, "second"})

    def test_archived_duplicate_does_not_block_new_content(self) -> None:
        old = self.draft()
        with self.conn:
            elements.transition(self.conn, old, "archived", "不用此草稿")
        entity = self.create(kind="data", payload={"definition": "新的业务口径", "unit": "元", "grain": "每份合同"})
        result = gates.check_entity(self.conn, entity)
        self.assertEqual(result["checks"][1]["status"], "pass")

    def test_precise_missing_exception_reason(self) -> None:
        entity = {
            "kind": "rule",
            "title": "流程",
            "payload": {"definition": "流程规则", "scope": "本机构", "conditions": "办理时"},
        }
        result = gates.evaluate_checks(
            entity, {"valid_from": "2026-01-01"}, {"evidence": True, "relations": True, "snapshot": True}
        )
        reason = result["checks"][2]["reason"]
        self.assertIn("例外", reason)
        self.assertNotIn("生效日期", reason)
        self.assertNotIn("适用范围", reason)


# These tests use actual authenticated HTTP and MySQL, never SQL to satisfy gates.


@unittest.skipUnless(os.getenv("RUN_JOBS_MYSQL_TESTS") == "1", "opt-in A.1 HTTP/MySQL workflow")
class ReviewHTTP(unittest.TestCase):
    setUpClass = classmethod(construction.BuildMySQL.setUpClass.__func__)
    tearDownClass = classmethod(construction.BuildMySQL.tearDownClass.__func__)
    login = construction.BuildMySQL.login
    invoke = construction.BuildMySQL.invoke

    def manual(self, client: TestClient) -> dict:
        import secrets

        return self.invoke(
            client,
            "element.create_draft",
            {
                "kind": "data",
                "title": "手工合同台账" + secrets.token_hex(4),
                "payload": {"definition": "人工登记并逐笔核对的合同金额", "unit": "元", "grain": "每份合同"},
            },
        )["entity"]

    def test_new_capability_application_review_publish_and_audit(self) -> None:
        builder, reviewer = self.login("fixture.builder"), self.login("fixture.reviewer")
        entity = self.manual(builder)
        denied = builder.post(
            "/api/v2/capabilities/element.submit_draft:invoke",
            json={"entity_id": entity["id"], "version": 1, "reason": "提交"},
        )
        self.assertEqual(denied.status_code, 409, denied.text)
        entity = self.invoke(
            builder,
            "element.update_draft",
            {"entity_id": entity["id"], "version": 1, "waiver_applications": {"QG1": "手工登记，暂无可定位原件"}},
        )["entity"]
        result = self.invoke(
            builder,
            "element.submit_draft",
            {"entity_id": entity["id"], "version": entity["version"], "reason": "申请核对"},
        )
        self.assertEqual(result["entity"]["status"], "review")
        self.assertFalse(result["gates"]["passed"])
        self.assertTrue(result["gates"]["ready_to_submit"])
        current = reviewer.get("/api/v2/reviews/" + entity["id"])
        self.assertEqual(current.status_code, 200, current.text)
        self.assertEqual(current.json()["data"]["gates"]["checks"][0]["application_reason"], "手工登记，暂无可定位原件")
        response = reviewer.post(
            f"/api/v2/reviews/{entity['id']}:decide",
            json={
                "decision": "approve_publish",
                "version": 3,
                "waivers": {"QG1": "已独立核对登记记录，接受补原件安排"},
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["data"]["entity"]["status"], "published")
        detail = self.invoke(builder, "element.get", {"entity_id": entity["id"]})
        self.assertTrue(detail["gates"]["passed"])
        self.assertNotIn("gates", detail["entity"].get("_meta", {}))
        self.assertTrue(any("暂无可定位原件" in str(a["detail"]) for a in detail["entity"]["activities"]))

    def test_legacy_edit_submit_keeps_all_authors_out_of_review(self) -> None:
        author, editor, reviewer = (
            self.login("fixture.admin"),
            self.login("fixture.builder"),
            self.login("fixture.reviewer"),
        )
        entity = self.manual(author)
        response = editor.patch(
            "/api/entities/" + entity["id"],
            json={
                "summary": "由另一人补充说明。",
                "expected_version": 1,
                "waiver_applications": {"QG1": "手工原始登记待附"},
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        response = editor.post(
            f"/api/entities/{entity['id']}/action",
            json={"action": "submit", "expected_version": 2, "reason": "核对后提交"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        for path, body in (
            (f"/api/entities/{entity['id']}/action", {"action": "approve", "expected_version": 3}),
            (
                f"/api/v2/reviews/{entity['id']}:decide",
                {"decision": "approve_publish", "version": 3, "waivers": {"QG1": "自审"}},
            ),
        ):
            with self.subTest(path=path):
                denied = author.post(path, json=body)
                self.assertEqual(denied.status_code, 403, denied.text)
        decision = reviewer.post(
            f"/api/v2/reviews/{entity['id']}:decide",
            json={"decision": "approve_publish", "version": 3, "waivers": {"QG1": "独立核对并接受说明"}},
        )
        self.assertEqual(decision.status_code, 200, decision.text)
        self.assertEqual(decision.json()["data"]["entity"]["status"], "published")

    def test_denial_returns_and_requires_a_new_application(self) -> None:
        builder, reviewer = self.login("fixture.builder"), self.login("fixture.reviewer")
        entity = self.manual(builder)
        self.invoke(
            builder,
            "element.submit_draft",
            {"entity_id": entity["id"], "version": 1, "reason": "提交", "waiver_applications": {"QG1": "原件后补"}},
        )
        response = reviewer.post(
            f"/api/v2/reviews/{entity['id']}:decide",
            json={
                "decision": "return",
                "version": 2,
                "reason": "请补充业务核对记录",
                "waiver_denials": {"QG1": "缺少足以接受缺口的业务记录"},
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()["data"]
        self.assertEqual(result["entity"]["status"], "returned")
        self.assertFalse(result["gates"]["ready_to_submit"])
        self.assertIn("业务记录", result["gates"]["checks"][0]["denial_reason"])
        denied = builder.post(
            "/api/v2/capabilities/element.submit_draft:invoke",
            json={"entity_id": entity["id"], "version": 3, "reason": "再次提交"},
        )
        self.assertEqual(denied.status_code, 409, denied.text)
