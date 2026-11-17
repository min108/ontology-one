"""R4 release checks exercised through real authenticated HTTP and stored MySQL rows."""

import copy
import json
import os
import unittest
from contextlib import closing
from unittest.mock import patch

from fastapi.testclient import TestClient
from httpx import Response

from backend import db, materials
from backend.kernel import gates
from backend.security import store
from backend.security.context import Principal, use_context
from tests.r2 import test_build as construction


@unittest.skipUnless(os.getenv("RUN_JOBS_MYSQL_TESTS") == "1", "opt-in gate workflow/MySQL integration")
class GateWorkflowsMySQL(unittest.TestCase):
    setUpClass = classmethod(construction.BuildMySQL.setUpClass.__func__)
    tearDownClass = classmethod(construction.BuildMySQL.tearDownClass.__func__)
    login = construction.BuildMySQL.login
    invoke = construction.BuildMySQL.invoke
    proposal = construction.BuildMySQL.proposal

    def principal(self, client: TestClient) -> Principal:
        return store.authenticate_session(client.cookies.get("ontology_session"), "r4-gates")[0]

    def draft(self, client: TestClient) -> tuple[dict, dict, str]:
        proposal = self.proposal(client)
        item = proposal["items"][0]
        self.invoke(
            client,
            "changeset.decide_items",
            {
                "changeset_id": proposal["id"],
                "item_ids": [item["id"]],
                "decision": "accepted",
            },
        )
        result = self.invoke(client, "changeset.apply", {"changeset_id": proposal["id"]})
        ident = result["entity_ids"][0]
        self.assertTrue(self.check(client, ident)["passed"])
        return proposal, item, ident

    def entity(self, client: TestClient, ident: str) -> dict:
        response = client.get("/api/entities/" + ident)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def check(self, client: TestClient, ident: str) -> dict:
        return self.invoke(client, "quality.check_gates", {"entity_id": ident})

    def payload(self, client: TestClient, ident: str, value: dict) -> None:
        # Model legacy corruption or an independently changed upstream proof.
        with use_context(self.principal(client)), closing(db.connect()) as conn, conn:
            conn.execute("UPDATE entities SET payload=? WHERE id=?", (materials.dump(value), ident))

    def action(self, client: TestClient, ident: str, action: str) -> Response:
        return client.post("/api/entities/" + ident + "/action", json={"action": action, "reason": "核对门禁"})

    def assert_blocked(self, response: Response, client: TestClient, ident: str, status: str, version: int) -> None:
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("还差", response.text)
        entity = self.entity(client, ident)
        self.assertEqual((entity["status"], entity["version"]), (status, version))

    def test_submit_review_publish_block_in_legacy_and_capability_paths(self) -> None:
        builder, reviewer = self.login("fixture.builder"), self.login("fixture.reviewer")
        proposal, _, ident = self.draft(builder)
        original = self.entity(builder, ident)["payload"]
        broken = {**original, "definition": ""}
        self.payload(builder, ident, broken)
        self.assert_blocked(self.action(builder, ident, "submit"), builder, ident, "draft", 1)
        response = builder.post(
            "/api/v2/capabilities/changeset.apply_and_submit:invoke", json={"changeset_id": proposal["id"]}
        )
        self.assert_blocked(response, builder, ident, "draft", 1)
        self.payload(builder, ident, original)
        self.assertEqual(self.action(builder, ident, "submit").status_code, 200)
        reviewed = self.entity(builder, ident)["payload"]
        self.payload(builder, ident, {**reviewed, "source_refs": []})
        self.assert_blocked(self.action(reviewer, ident, "approve"), builder, ident, "review", 2)
        response = reviewer.post(f"/api/v2/reviews/{ident}:decide", json={"decision": "approve_publish", "version": 2})
        self.assert_blocked(response, builder, ident, "review", 2)
        self.payload(builder, ident, reviewed)
        self.assertEqual(self.action(reviewer, ident, "approve").status_code, 200)
        self.payload(builder, ident, {**reviewed, "source_refs": []})
        self.assert_blocked(self.action(reviewer, ident, "publish"), builder, ident, "approved", 3)

    def test_combined_review_rolls_back_approval_if_publish_gate_fails(self) -> None:
        builder, reviewer = self.login("fixture.builder"), self.login("fixture.reviewer")
        _, _, ident = self.draft(builder)
        self.assertEqual(self.action(builder, ident, "submit").status_code, 200)
        enforce = gates.enforce
        checked = []

        def invalidate_before_publish(conn: db.Connection, entity: dict) -> dict:
            checked.append(entity["status"])
            if entity["status"] == "approved":
                conn.execute("DELETE FROM relations WHERE source=? AND type='applies_to'", (ident,))
            return enforce(conn, entity)

        with patch.object(gates, "enforce", side_effect=invalidate_before_publish):
            response = reviewer.post(
                f"/api/v2/reviews/{ident}:decide", json={"decision": "approve_publish", "version": 2}
            )
        self.assert_blocked(response, builder, ident, "review", 2)
        self.assertEqual(checked, ["review", "approved"])
        self.assertTrue(self.check(builder, ident)["passed"])

    def test_reviewer_waivers_persist_reasons_and_allow_independent_release(self) -> None:
        builder, reviewer = self.login("fixture.builder"), self.login("fixture.reviewer")
        _, _, ident = self.draft(builder)
        old = self.entity(builder, ident)
        self.payload(builder, ident, {**old["payload"], "source_refs": [], "exceptions": ""})
        reasons = {"QG1": "遗留原文待补，按现有审批记录核对。", "QG3": "例外情况另行人工核对。"}
        result = self.invoke(
            reviewer,
            "quality.waive_gates",
            {
                "entity_id": ident,
                "version": 1,
                "reason": "记录审查结论",
                "waivers": reasons,
            },
        )
        self.assertTrue(result["passed"])
        self.assertEqual({c["code"]: c["waiver_reason"] for c in result["checks"] if c["status"] == "waived"}, reasons)
        self.assertEqual(self.action(builder, ident, "submit").status_code, 200)
        response = reviewer.post(f"/api/v2/reviews/{ident}:decide", json={"decision": "approve_publish", "version": 2})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.entity(builder, ident)["status"], "published")
        with use_context(self.principal(builder)), closing(db.connect()) as conn:
            saved = json.loads(
                conn.execute("SELECT gates FROM entity_meta WHERE entity_id=?", (ident,)).fetchone()["gates"]
            )
            history = [
                dict(row)
                for row in conn.execute(
                    "SELECT detail FROM activities WHERE entity_id=? AND action='发布检查豁免'", (ident,)
                )
            ]
        self.assertEqual({code: item["reason"] for code, item in saved["waivers"].items()}, reasons)
        self.assertTrue(all(item["by"] == self.principal(reviewer).user_id for item in saved["waivers"].values()))
        self.assertTrue(all(any(reason in item["detail"] for item in history) for reason in reasons.values()))
        self.invoke(
            builder,
            "element.start_revision",
            {
                "entity_id": ident,
                "version": 4,
                "reason": "调整适用日期",
            },
        )
        self.invoke(
            builder,
            "element.update_draft",
            {
                "entity_id": ident,
                "version": 5,
                "meta": {"valid_from": "2026-11-19"},
                "reason": "更新后须重新审查",
            },
        )
        changed = self.check(builder, ident)
        self.assertEqual({c["code"] for c in changed["checks"] if c["status"] == "fail"}, {"QG1", "QG3"})
        self.assert_blocked(self.action(builder, ident, "submit"), builder, ident, "draft", 6)

    def test_forbidden_and_blank_waivers_never_persist(self) -> None:
        builder, reviewer = self.login("fixture.builder"), self.login("fixture.reviewer")
        _, _, ident = self.draft(builder)
        for waiver in ({"QG2": "理由"}, {"QG4": "理由"}, {"QG5": "理由"}, {"QG1": " "}):
            with self.subTest(waiver=waiver):
                response = reviewer.post(
                    "/api/v2/capabilities/quality.waive_gates:invoke",
                    json={
                        "entity_id": ident,
                        "version": 1,
                        "reason": "尝试豁免",
                        "waivers": waiver,
                    },
                )
                self.assertEqual(response.status_code, 422, response.text)
        with use_context(self.principal(builder)), closing(db.connect()) as conn:
            value = conn.execute("SELECT gates FROM entity_meta WHERE entity_id=?", (ident,)).fetchone()["gates"]
        self.assertFalse(json.loads(value or "{}").get("waivers"))
        self.assertEqual(self.entity(builder, ident)["version"], 1)

    def test_metadata_editor_cannot_waive_or_review_another_persons_submission(self) -> None:
        builder, editor, reviewer = (
            self.login("fixture.builder"),
            self.login("fixture.admin"),
            self.login("fixture.reviewer"),
        )
        _, _, ident = self.draft(builder)
        self.invoke(
            editor,
            "element.update_draft",
            {
                "entity_id": ident,
                "version": 1,
                "meta": {"valid_from": "2026-11-18"},
                "reason": "核对生效日期",
            },
        )
        self.assertEqual(
            self.entity(builder, ident)["payload"]["r2_review"]["last_editor_id"], self.principal(editor).user_id
        )
        self.assertEqual(self.action(builder, ident, "submit").status_code, 200)
        for path, body in (
            (f"/api/entities/{ident}/action", {"action": "approve"}),
            (f"/api/v2/reviews/{ident}:decide", {"decision": "approve_publish", "version": 3}),
            (
                "/api/v2/capabilities/quality.waive_gates:invoke",
                {"entity_id": ident, "version": 3, "reason": "自行豁免", "waivers": {"QG3": "自行认可"}},
            ),
        ):
            response = editor.post(path, json=body)
            self.assertEqual(response.status_code, 403, response.text)
        response = reviewer.post(f"/api/v2/reviews/{ident}:decide", json={"decision": "approve_publish", "version": 3})
        self.assertEqual(response.status_code, 200, response.text)

    def test_tampered_and_foreign_sources_block_without_revoking_published_results(self) -> None:
        builder, foreign, reviewer = (
            self.login("fixture.builder"),
            self.login("r2.other"),
            self.login("fixture.reviewer"),
        )
        _, _, ident = self.draft(builder)
        outside = self.proposal(foreign)["items"][0]["evidence"]
        original = self.entity(builder, ident)["payload"]
        tampered = copy.deepcopy(original["source_refs"])
        tampered[0]["quote"] += "不存在的原文"
        for refs in (tampered, outside):
            with self.subTest(refs=refs):
                self.payload(builder, ident, {**original, "source_refs": refs})
                check = self.check(builder, ident)
                self.assertEqual(next(c["status"] for c in check["checks"] if c["code"] == "QG1"), "fail")
                self.assert_blocked(self.action(builder, ident, "submit"), builder, ident, "draft", 1)
        self.payload(builder, ident, original)
        self.assertEqual(self.action(builder, ident, "submit").status_code, 200)
        released = reviewer.post(f"/api/v2/reviews/{ident}:decide", json={"decision": "approve_publish", "version": 2})
        self.assertEqual(released.status_code, 200, released.text)
        published = self.entity(builder, ident)
        self.payload(builder, ident, {**published["payload"], "source_refs": tampered})
        self.assertFalse(self.check(builder, ident)["passed"])
        self.assertEqual(
            (self.entity(builder, ident)["status"], self.entity(builder, ident)["version"]), ("published", 4)
        )

    def test_repair_applied_item_reuses_same_draft_and_submits_it(self) -> None:
        builder = self.login("fixture.builder")
        proposal, item, ident = self.draft(builder)
        after = item["after_data"]
        body = {"changeset_id": proposal["id"], "item_ids": [item["id"]], "decision": "edited"}
        required = {key: after[key] for key in ("title", "kind", "content")}
        incomplete = self.invoke(builder, "changeset.decide_items", {**body, "edit": {**required, "exceptions": ""}})
        self.assertFalse(incomplete["can_submit"])
        self.assertTrue(incomplete["submit_blockers"])
        response = builder.post(
            "/api/v2/capabilities/changeset.apply_and_submit:invoke", json={"changeset_id": proposal["id"]}
        )
        self.assert_blocked(response, builder, ident, "draft", 2)
        repaired = self.invoke(
            builder, "changeset.decide_items", {**body, "edit": {**required, "exceptions": "无例外"}}
        )
        row = next(row for row in repaired["items"] if row["id"] == item["id"])
        self.assertEqual(row["applied_entity_id"], ident)
        self.assertTrue(row["gates"]["passed"])
        self.assertTrue(repaired["can_submit"])
        self.assertEqual(repaired["submit_blockers"], [])
        result = self.invoke(builder, "changeset.apply_and_submit", {"changeset_id": proposal["id"]})
        self.assertEqual(result["entity_ids"], [ident])
        with use_context(self.principal(builder)), closing(db.connect()) as conn:
            count = conn.execute(
                "SELECT COUNT(*) n FROM entities "
                "WHERE JSON_UNQUOTE(JSON_EXTRACT(payload,'$.r2_review.changeset_id'))=?",
                (proposal["id"],),
            ).fetchone()["n"]
        self.assertEqual(count, 1)
        self.assertEqual((self.entity(builder, ident)["status"], self.entity(builder, ident)["version"]), ("review", 4))
