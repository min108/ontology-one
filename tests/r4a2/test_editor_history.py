"""D18: authors and submitters share one release-to-release review boundary.

The same authenticated author is excluded from approval, publication and waiver
in both review bindings. An independent person can publish; the next release
opens a separate authorship period. Historic status-only operations leave
unreadable expressions and their evidence intact.
"""

import io
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from openpyxl import Workbook

from backend.domains import reviews
from tests.r2 import test_build as construction


class HistoricalEditorReceipts(unittest.TestCase):
    def test_legacy_approval_can_publish_when_the_submitter_is_unrecorded_but_known_authors_remain_excluded(
        self,
    ) -> None:
        conn = Mock()
        conn.execute.return_value.fetchone.return_value = None
        entity = {"id": "legacy", "kind": "data", "status": "approved", "payload": {}}
        history = [
            {"action": "审核通过", "actor": "原审核人"},
            {"action": "修订", "actor": "修订员"},
            {"action": "创建", "actor": "原编录员"},
        ]
        reviewer = SimpleNamespace(user_id="reviewer", username="新审核人", readonly=False, role="reviewer")
        with (
            patch.object(reviews, "_activities", return_value=history),
            patch.object(reviews.gates, "check_entity", return_value={}),
            patch.object(reviews, "require_context", return_value=reviewer),
        ):
            self.assertTrue(reviews.review_info(conn, entity)["can_decide"])
            self.assertFalse(reviews.review_info(conn, {**entity, "status": "review"})["can_decide"])
            reviewer.username = "原编录员"
            self.assertFalse(reviews.review_info(conn, entity)["can_decide"])

    def test_archive_of_a_historic_release_only_preserves_edits_made_after_publication(self) -> None:
        conn = Mock()
        for actions, authors in (
            ([("创建", "原编录员")], []),
            ([("提出关联", "补充关联的人"), ("发布成果", "历史审核人"), ("创建", "原编录员")], ["补充关联的人"]),
        ):
            with self.subTest(actions=actions):
                history = [{"action": action, "actor": actor} for action, actor in actions]
                released = {"id": "legacy", "status": "published", "payload": {"r2_review": {"editor_ids": ["old"]}}}
                with patch.object(reviews, "_activities", return_value=history):
                    reviews.keep_release_boundary(conn, released, "archived")
                    released["status"] = "archived"
                    meta = reviews.record_editor(released, "current", conn=conn)
                self.assertEqual(meta["editor_ids"], ["current"])
                self.assertEqual(meta.get("legacy_actors", []), authors)

    def test_first_relation_on_an_existing_pending_record_keeps_its_actual_legacy_submission(self) -> None:
        conn = Mock()
        entity = {"id": "pending", "kind": "data", "status": "review", "payload": {}}
        history = [{"action": "提交审核", "actor": "历史经办人", "created_at": "2026-11-17 08:00:00"}]
        with patch.object(reviews, "_activities", return_value=history):
            reviews.record_relation_editor(conn, entity, "new")
            reviews.record_relation_editor(conn, entity, "another")
        meta = entity["payload"]["r2_review"]
        self.assertEqual(meta["submitted_name"], "历史经办人")
        self.assertEqual(meta["submitted_at"], history[0]["created_at"])
        self.assertEqual(meta["legacy_actors"], ["历史经办人"])
        self.assertEqual(meta["editor_ids"], ["new", "another"])


@unittest.skipUnless(os.getenv("RUN_JOBS_MYSQL_TESTS") == "1", "opt-in release history HTTP/MySQL contracts")
class EditorHistoryHTTP(unittest.TestCase):
    setUpClass = classmethod(construction.BuildMySQL.setUpClass.__func__)
    tearDownClass = classmethod(construction.BuildMySQL.tearDownClass.__func__)
    login = construction.BuildMySQL.login
    invoke = construction.BuildMySQL.invoke
    proposal = construction.BuildMySQL.proposal

    def entity(self, client: TestClient, ident: str) -> dict:
        response = client.get("/api/entities/" + ident)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def action(self, client: TestClient, entity: dict, action: str, **extra: object) -> dict:
        response = client.post(
            f"/api/entities/{entity['id']}/action",
            json={"action": action, "expected_version": entity["version"], "reason": "核对当前记录", **extra},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return self.entity(client, entity["id"])

    def test_unpublished_archive_and_revision_keep_authors_until_next_release(self) -> None:
        first, second, reviewer = (self.login("fixture." + role) for role in ("admin", "builder", "reviewer"))
        proposal = self.proposal(first)
        item = proposal["items"][0]
        self.invoke(
            first,
            "changeset.decide_items",
            {"changeset_id": proposal["id"], "item_ids": [item["id"]], "decision": "accepted"},
        )
        ident = self.invoke(first, "changeset.apply", {"changeset_id": proposal["id"]})["entity_ids"][0]
        original = self.entity(first, ident)
        self.invoke(
            first,
            "changeset.decide_items",
            {"changeset_id": proposal["id"], "item_ids": [item["id"]], "decision": "rejected"},
        )
        archived = self.entity(first, ident)
        self.assertEqual(archived["status"], "archived")
        draft = self.action(second, archived, "revise")
        pending = self.action(second, draft, "submit")
        self.assertEqual(
            pending["payload"]["r2_review"]["revision_id"], original["payload"]["r2_review"]["revision_id"]
        )
        for path, body in (
            (f"/api/entities/{ident}/action", {"action": "approve", "expected_version": pending["version"]}),
            (f"/api/v2/reviews/{ident}:decide", {"decision": "approve_publish", "version": pending["version"]}),
        ):
            with self.subTest(path=path):
                rejected = first.post(path, json=body)
                self.assertEqual(rejected.status_code, 403, rejected.text)
                self.assertIn("其他审核人员", rejected.text)
                self.assertEqual(self.entity(first, ident)["version"], pending["version"])
        approved = self.action(reviewer, pending, "approve")
        published = self.action(reviewer, approved, "publish")
        self.assertEqual(published["status"], "published")
        archived = self.action(first, published, "archive")
        draft = self.action(second, archived, "revise")
        next_pending = self.action(second, draft, "submit")
        self.assertNotEqual(
            next_pending["payload"]["r2_review"]["revision_id"], original["payload"]["r2_review"]["revision_id"]
        )
        released = first.post(
            f"/api/v2/reviews/{ident}:decide", json={"decision": "approve_publish", "version": next_pending["version"]}
        )
        self.assertEqual(released.status_code, 200, released.text)
        self.assertEqual(self.entity(first, ident)["status"], "published")

    def test_submitting_without_edit_stays_in_history_after_return(self) -> None:
        first, builder, reviewer = (self.login("fixture." + role) for role in ("admin", "builder", "reviewer"))
        entity = self.invoke(
            builder,
            "element.create_draft",
            {
                "kind": "data",
                "title": "纸质登记份数",
                "payload": {"definition": "登记材料份数", "unit": "份", "grain": "每份材料"},
            },
        )["entity"]
        self.invoke(
            builder,
            "element.update_draft",
            {"entity_id": entity["id"], "version": 1, "waiver_applications": {"QG1": "纸质材料由审核人核实"}},
        )
        pending = self.action(first, self.entity(builder, entity["id"]), "submit")
        returned = self.action(reviewer, pending, "return")
        self.invoke(
            builder,
            "element.update_draft",
            {"entity_id": entity["id"], "version": returned["version"], "summary": "经办人员按每份登记。"},
        )
        pending = self.action(
            builder, self.entity(builder, entity["id"]), "submit", waiver_applications={"QG1": "纸质材料由审核人核实"}
        )
        for path, body in (
            (f"/api/entities/{entity['id']}/action", {"action": "approve", "expected_version": pending["version"]}),
            (
                "/api/v2/capabilities/quality.waive_gates:invoke",
                {
                    "entity_id": entity["id"],
                    "version": pending["version"],
                    "reason": "办理申请",
                    "waivers": {"QG1": "核对申请"},
                },
            ),
        ):
            denied = first.post(path, json=body)
            self.assertEqual(denied.status_code, 403, denied.text)
            self.assertEqual(self.entity(first, entity["id"])["status"], "review")
        result = reviewer.post(
            f"/api/v2/reviews/{entity['id']}:decide",
            json={"decision": "approve_publish", "version": pending["version"], "waivers": {"QG1": "已核对原件"}},
        )
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(self.entity(first, entity["id"])["status"], "published")

    def test_relation_proposals_on_a_released_item_open_and_keep_one_new_period(self) -> None:
        first, builder, reviewer = (self.login("fixture." + role) for role in ("admin", "builder", "reviewer"))
        proposal = self.proposal(builder)
        self.invoke(
            builder,
            "changeset.decide_items",
            {"changeset_id": proposal["id"], "item_ids": [proposal["items"][0]["id"]], "decision": "accepted"},
        )
        ident = self.invoke(builder, "changeset.apply", {"changeset_id": proposal["id"]})["entity_ids"][0]
        pending = self.action(builder, self.entity(builder, ident), "submit")
        released = self.action(reviewer, self.action(reviewer, pending, "approve"), "publish")
        previous = released["payload"]["r2_review"]["revision_id"]
        editor_ids = [client.get("/api/auth/session").json()["user"]["id"] for client in (first, builder)]
        for author, target in ((first, "DEMO07-FIELD-CONTRACT_ID"), (builder, "DEMO07-FIELD-AMOUNT_YUAN")):
            self.invoke(
                author,
                "relation.propose",
                {"source": ident, "target": target, "type": "related_to", "reason": "本次修订增加核对时参考的数据"},
            )
            updated = self.entity(author, ident)
            history = updated["payload"]["r2_review"]
            self.assertEqual(updated["status"], "published")
            self.assertNotEqual(history["revision_id"], previous)
            if author is first:
                new_period = history["revision_id"]
                self.assertEqual(history["editor_ids"], editor_ids[:1])
            else:
                self.assertEqual(history["revision_id"], new_period)
                self.assertEqual(set(history["editor_ids"]), set(editor_ids))
        pending_submission = reviewer.post(
            f"/api/entities/{ident}/action", json={"action": "publish", "expected_version": updated["version"]}
        )
        self.assertEqual(pending_submission.status_code, 409, pending_submission.text)
        self.assertIn("提交审核", pending_submission.text)
        self.assertEqual(self.entity(first, ident)["version"], updated["version"])
        archived = self.action(first, updated, "archive")
        draft = self.action(builder, archived, "revise")
        pending = self.action(builder, draft, "submit")
        self.assertEqual(pending["payload"]["r2_review"]["revision_id"], new_period)
        decision = {"decision": "approve_publish", "version": pending["version"]}
        denied = first.post(f"/api/v2/reviews/{ident}:decide", json=decision)
        self.assertEqual(denied.status_code, 403, denied.text)
        self.assertEqual(self.entity(first, ident)["version"], pending["version"])
        released = reviewer.post(f"/api/v2/reviews/{ident}:decide", json=decision)
        self.assertEqual(released.status_code, 200, released.text)
        self.assertEqual(self.entity(first, ident)["status"], "published")

    def test_excel_resolves_a_predeclared_link_and_records_the_uploader_on_its_source(self) -> None:
        author, builder, reviewer = (self.login("fixture." + role) for role in ("admin", "builder", "reviewer"))
        data_id, rule_id = "D.05.89.002", "R.05.89.001.T3"
        original = builder.post(
            "/api/entities",
            json={
                "id": data_id,
                "kind": "data",
                "title": "移交凭证数量",
                "payload": {
                    "definition": "登记凭证总数",
                    "unit": "份",
                    "grain": "每次交接",
                    "related_rules": [rule_id],
                },
            },
        )
        self.assertEqual(original.status_code, 200, original.text)
        book = Workbook()
        sheet = book.active
        sheet.append(["规则编码", "规则类型", "二级分类", "规则内容（法规原文·逐字）", "规范依据", "资产来源"])
        sheet.append(
            [rule_id, "T3 义务程序", "移交", "收到凭证后按本机构登记册核实签收记录。", "档案移交登记规定", "内部制度"]
        )
        contents = io.BytesIO()
        book.save(contents)
        book.close()
        uploaded = author.post("/api/asset-import/rule", files={"file": ("移交登记规则.xlsx", contents.getvalue())})
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        self.assertEqual(uploaded.json()["created"], 1)
        linked = self.entity(builder, data_id)
        self.assertEqual(linked["version"], 2)
        editor_ids = [client.get("/api/auth/session").json()["user"]["id"] for client in (author, builder)]
        self.assertEqual(set(linked["payload"]["r2_review"]["editor_ids"]), set(editor_ids))
        relation = next(link for link in linked["relations"]["outgoing"] if link["target"] == rule_id)
        self.assertEqual(json.loads(relation["payload"])["proposed_by"], editor_ids[0])
        path = "/api/relations/" + relation["id"]
        decision = {"status": "confirmed", "reason": "按登记表核实引用"}
        blocked = author.patch(path, json=decision)
        self.assertEqual(blocked.status_code, 403, blocked.text)
        accepted = reviewer.patch(path, json=decision)
        self.assertEqual(accepted.status_code, 200, accepted.text)
        pending = self.action(
            builder, self.entity(builder, data_id), "submit", waiver_applications={"QG1": "纸质登记簿留作核对"}
        )
        decision = {
            "decision": "approve_publish",
            "version": pending["version"],
            "waivers": {"QG1": "已核对纸质登记簿"},
        }
        blocked = author.post(f"/api/v2/reviews/{data_id}:decide", json=decision)
        self.assertEqual(blocked.status_code, 403, blocked.text)
        accepted = reviewer.post(f"/api/v2/reviews/{data_id}:decide", json=decision)
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertEqual(self.entity(reviewer, data_id)["status"], "published")
