"""R3 U8 decision tables: allow and reject cases precede implementation."""

import asyncio
import json
import os
import unittest
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import HTTPException
from starlette.requests import Request

from backend import db, jobs, materials
from backend.api.v2.errors import api_error
from backend.core.errors import ApiError
from backend.domains import build, reviews
from backend.domains.artifacts import render
from backend.security import store
from backend.security.context import use_context
from tests.r2 import test_build as construction
from tests.r2.test_artifacts import spec


class DecisionTables(unittest.TestCase):
    def test_candidate_processing_table(self) -> None:
        # Exactly five eligible and five ineligible examples; missing status is legacy pending.
        allowed = (
            {},
            {"status": "pending"},
            {"status": "pending", "entity_id": ""},
            {"status": "pending", "merged_into": None},
            {"status": "pending", "id": "one"},
        )
        blocked = (
            {"status": "adopted"},
            {"status": "rejected"},
            {"status": "merged"},
            {"status": "pending", "entity_id": "existing"},
            {"status": "unknown"},
        )
        for candidate in allowed:
            with self.subTest(allowed=candidate):
                self.assertFalse(build.processed_elsewhere(candidate))
        for candidate in blocked:
            with self.subTest(blocked=candidate):
                self.assertTrue(build.processed_elsewhere(candidate))

    def test_numeric_verification_table_remains_strict(self) -> None:
        allowed = ("30日内办理", "1万元", "10000元", "第99条", "2026年11月17日")
        blocked = ("31日内办理", "2万元", "30万家", "30倍", "30小时")
        for answer, accepted in [(value, True) for value in allowed] + [(value, False) for value in blocked]:
            value = spec()
            value["blocks"][0]["text"] = answer
            with self.subTest(answer=answer, accepted=accepted):
                if accepted:
                    render.verify(value)
                else:
                    with self.assertRaises(HTTPException):
                        render.verify(value)

    def test_legacy_actor_table_compares_both_id_and_username(self) -> None:
        person = SimpleNamespace(user_id="user-1", username="editor")
        for actor, expected in (("user-1", True), ("editor", True), ("other", False), ("", False), (None, False)):
            with self.subTest(actor=actor):
                self.assertEqual(reviews.is_same_person(person, actor), expected)

    def test_legacy_api_error_shape_is_preserved(self) -> None:
        for path, legacy in (("/api/entities/one/action", True), ("/api/v2/reviews/one:decide", False)):
            request = Request({"type": "http", "path": path, "headers": []})
            result = asyncio.run(api_error(request, ApiError("SEPARATION_OF_DUTIES")))
            payload = json.loads(result.body)
            self.assertEqual(result.status_code, 403)
            self.assertEqual(set(payload), {"detail"} if legacy else {"error"})
            if legacy:
                self.assertIsInstance(payload["detail"], str)

    def test_review_authority_actions_forward_current_version(self) -> None:
        entity = {
            "id": "rule",
            "kind": "rule",
            "version": 7,
            "status": "review",
            "payload": {"authority_spec": {}, "source_refs": [{"material_id": "mat"}]},
        }
        conn = Mock()
        conn.__enter__ = Mock(return_value=conn)
        conn.__exit__ = Mock(return_value=False)
        with (
            patch.object(reviews.db, "connect", return_value=conn),
            patch.object(reviews.db, "entity_json", return_value=entity),
            patch.object(reviews, "review_info", return_value={"can_decide": True}),
            patch.object(reviews, "get", return_value={}),
            patch.object(reviews.provenance, "normalize_source_ref"),
            patch("backend.api.v1.core.entity_action_in_transaction") as action,
        ):
            reviews.decide("rule", "approve_publish", 7, "")
            self.assertEqual(action.call_args_list[0].args[2]["expected_version"], 7)
            self.assertEqual(action.call_args_list[1].args[2]["expected_version"], 8)


@unittest.skipUnless(os.getenv("RUN_JOBS_MYSQL_TESTS") == "1", "opt-in MySQL integration test")
class RefinementsMySQL(unittest.TestCase):
    setUpClass = classmethod(construction.BuildMySQL.setUpClass.__func__)
    tearDownClass = classmethod(construction.BuildMySQL.tearDownClass.__func__)
    login = construction.BuildMySQL.login
    invoke = construction.BuildMySQL.invoke
    proposal = construction.BuildMySQL.proposal

    def test_existing_terminal_candidates_are_marked_and_do_not_block_other_adoption(self) -> None:
        client = self.login("fixture.builder")
        person = store.authenticate_session(client.cookies.get("ontology_session"), "r3-test")[0]
        for status in ("adopted", "merged", "rejected"):
            with self.subTest(status=status):
                proposal = self.proposal(client)
                self.assertGreaterEqual(len(proposal["items"]), 2)
                body = {"changeset_id": proposal["id"]}
                self.invoke(
                    client,
                    "changeset.decide_items",
                    {
                        **body,
                        "item_ids": [item["id"] for item in proposal["items"]],
                        "decision": "accepted",
                    },
                )
                with use_context(person), closing(db.connect()) as conn, conn:
                    material = materials.material(conn, proposal["source_material_id"])
                    candidate = materials.find_candidate(material, proposal["items"][0]["after_data"]["id"])
                    candidate["status"] = status
                    materials.save_payload(conn, material, material["payload"])
                refreshed = client.get("/api/v2/changesets/" + proposal["id"])
                self.assertEqual(refreshed.status_code, 200, refreshed.text)
                item = refreshed.json()["data"]["items"][0]
                self.assertTrue(item["processed_elsewhere"])
                self.assertEqual(item["decision"], "processed_elsewhere")
                result = self.invoke(client, "changeset.apply_and_submit", body)
                self.assertEqual(len(result["entity_ids"]), len(proposal["items"]) - 1)
                with use_context(person), closing(db.connect()) as conn:
                    material = materials.material(conn, proposal["source_material_id"])
                    candidate = materials.find_candidate(material, item["after_data"]["id"])
                    self.assertEqual(candidate["status"], status)

    def test_new_recipe_excludes_terminal_candidates(self) -> None:
        client = self.login("fixture.builder")
        person = store.authenticate_session(client.cookies.get("ontology_session"), "r3-test")[0]
        proposal = self.proposal(client)
        material_id = proposal["source_material_id"]
        with use_context(person), closing(db.connect()) as conn, conn:
            material = materials.material(conn, material_id)
            candidates = material["payload"]["candidates"]
            first = candidates[0]
            for index, status in enumerate(("rejected", "merged", "adopted")):
                candidates.append({**first, "id": "terminal-" + str(index), "status": status})
            materials.save_payload(conn, material, material["payload"])
            run = conn.execute("SELECT id FROM recipe_runs WHERE changeset_id=?", (proposal["id"],)).fetchone()
            conn.execute("UPDATE recipe_runs SET changeset_id=NULL WHERE id=?", (run["id"],))
        with use_context(person), patch("backend.domains.build.model_available", return_value=False):
            asyncio.run(build.run_recipe(run["id"]))
            result = build.get(build.status(run["id"])["changeset_id"])
        self.assertEqual(len(result["items"]), len(proposal["items"]))
        self.assertTrue(all(not item["after_data"]["id"].startswith("terminal-") for item in result["items"]))

    def test_missing_recipe_record_keeps_activity_tray_and_material_link(self) -> None:
        client = self.login("fixture.builder")
        person = store.authenticate_session(client.cookies.get("ontology_session"), "r3-test")[0]
        proposal = self.proposal(client)
        with use_context(person), closing(db.connect()) as conn, conn:
            run = conn.execute("SELECT id FROM recipe_runs WHERE changeset_id=?", (proposal["id"],)).fetchone()
            conn.execute("DELETE FROM recipe_runs WHERE id=?", (run["id"],))
        with use_context(person):
            summaries = jobs.harness_queue_summaries(person)
        item = next(item for item in summaries if item["id"] == run["id"])
        self.assertEqual(item["status"], "failed")
        self.assertEqual(item["href"], "/materials/" + proposal["source_material_id"])
        self.assertIn("原材料仍保留", item["message"])

    def test_new_and_legacy_cross_person_review_and_error_contract(self) -> None:
        editor, submitter, reviewer = (
            self.login("fixture.admin"),
            self.login("fixture.builder"),
            self.login("fixture.reviewer"),
        )
        proposal = self.proposal(editor)
        item = proposal["items"][0]
        body = {"changeset_id": proposal["id"], "item_ids": [item["id"]]}
        self.invoke(
            editor,
            "changeset.decide_items",
            {
                **body,
                "decision": "edited",
                "edit": {
                    "title": item["after_data"]["title"],
                    "kind": "rule",
                    "content": item["after_data"]["content"] + "核对附件。",
                },
            },
        )
        self.invoke(submitter, "changeset.decide_items", {**body, "decision": "accepted"})
        entity_id = self.invoke(submitter, "changeset.apply_and_submit", {"changeset_id": proposal["id"]})[
            "entity_ids"
        ][0]
        for path, body, envelope in (
            (f"/api/v2/reviews/{entity_id}:decide", {"decision": "approve_publish", "version": 2}, "error"),
            (f"/api/entities/{entity_id}/action", {"action": "approve"}, "detail"),
        ):
            rejected = editor.post(path, json=body)
            self.assertEqual(rejected.status_code, 403, rejected.text)
            self.assertEqual(set(rejected.json()), {envelope})
        allowed = reviewer.post(
            f"/api/v2/reviews/{entity_id}:decide", json={"decision": "approve_publish", "version": 2}
        )
        self.assertEqual(allowed.status_code, 200, allowed.text)
        self.assertEqual(allowed.json()["data"]["entity"]["status"], "published")

    def test_review_center_authority_rules_publish_and_return_with_version_guard(self) -> None:
        from backend.authority import Spec

        builder, reviewer = self.login("fixture.builder"), self.login("fixture.reviewer")
        person = store.authenticate_session(builder.cookies.get("ontology_session"), "r3-test")[0]
        for decision, expected in (("approve_publish", "published"), ("return", "returned")):
            with self.subTest(decision=decision):
                proposal = self.proposal(builder)
                self.invoke(
                    builder,
                    "changeset.decide_items",
                    {"changeset_id": proposal["id"], "item_ids": [proposal["items"][0]["id"]], "decision": "accepted"},
                )
                entity_id = self.invoke(builder, "changeset.apply_and_submit", {"changeset_id": proposal["id"]})[
                    "entity_ids"
                ][0]
                with use_context(person), closing(db.connect()) as conn, conn:
                    entity = db.entity_json(conn.execute("SELECT * FROM entities WHERE id=?", (entity_id,)).fetchone())
                    org = conn.execute("SELECT id FROM entities WHERE kind='organization' LIMIT 1").fetchone()["id"]
                    entity["payload"]["authority_spec"] = Spec(
                        type="position",
                        org_ids=[org],
                        purpose="核对付款",
                        scope="付款岗位",
                        basis="付款管理制度",
                        sections=[{"id": "duties", "title": "职责", "content": "核对附件"}],
                        source_refs=entity["payload"]["source_refs"],
                    ).model_dump()
                    conn.execute(
                        "UPDATE entities SET payload=? WHERE id=?", (materials.dump(entity["payload"]), entity_id)
                    )
                if decision == "approve_publish":
                    from backend.authority import guard_action

                    def reject_publish(conn: db.Connection, old: dict, body: dict) -> None:
                        if body["action"] == "publish":
                            raise HTTPException(422, "模拟发布核对失败")
                        return guard_action(conn, old, body)

                    with patch("backend.authority.guard_action", side_effect=reject_publish):
                        failed = reviewer.post(
                            f"/api/v2/reviews/{entity_id}:decide",
                            json={"decision": decision, "version": 2, "reason": "核对依据"},
                        )
                    self.assertEqual(failed.status_code, 422)
                    unchanged = reviewer.get("/api/entities/" + entity_id).json()
                    self.assertEqual((unchanged["status"], unchanged["version"]), ("review", 2))
                result = reviewer.post(
                    f"/api/v2/reviews/{entity_id}:decide",
                    json={"decision": decision, "version": 2, "reason": "核对依据"},
                )
                self.assertEqual(result.status_code, 200, result.text)
                self.assertEqual(result.json()["data"]["entity"]["status"], expected)
                self.assertEqual(result.json()["data"]["entity"]["version"], 4 if decision == "approve_publish" else 3)

    def test_long_document_limit_gives_batch_guidance_without_discarding_saved_items(self) -> None:
        client = self.login("fixture.builder")
        proposal = self.proposal(client)
        person = store.authenticate_session(client.cookies.get("ontology_session"), "r3-test")[0]
        with use_context(person), closing(db.connect()) as conn, conn:
            source = conn.execute(
                "SELECT id FROM source_versions WHERE material_id=? LIMIT 1", (proposal["source_material_id"],)
            ).fetchone()
            conn.execute("UPDATE source_versions SET text=? WHERE id=?", ("长文档" * 240000, source["id"]))
        response = client.get("/api/v2/changesets/" + proposal["id"])
        self.assertEqual(response.status_code, 413, response.text[:400])
        self.assertEqual(response.json()["error"]["code"], "IMPORT_LIMIT_EXCEEDED")
        for expected in ("2 MiB", "原文与已保存条目仍保留", "按章节", "分批上传"):
            self.assertIn(expected, response.json()["error"]["message"])
        with use_context(person), closing(db.connect()) as conn:
            count = conn.execute(
                "SELECT COUNT(*) n FROM changeset_items WHERE changeset_id=?", (proposal["id"],)
            ).fetchone()["n"]
        self.assertEqual(count, len(proposal["items"]))
