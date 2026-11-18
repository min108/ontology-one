"""A.1 D12/D13 actions use real APIs and ordinary immutable source documents."""

import asyncio
import os
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from backend import db
from backend.domains import build
from backend.security import store
from backend.security.context import use_context
from tests.r2 import test_build as construction

MATERIALS = Path(__file__).resolve().parents[2] / "docs/blueprint/rounds/r4a1-materials"


@unittest.skipUnless(os.getenv("RUN_JOBS_MYSQL_TESTS") == "1", "opt-in real changeset MySQL contracts")
class BuildWorkflow(unittest.TestCase):
    setUpClass = classmethod(construction.BuildMySQL.setUpClass.__func__)
    tearDownClass = classmethod(construction.BuildMySQL.tearDownClass.__func__)
    login = construction.BuildMySQL.login
    invoke = construction.BuildMySQL.invoke

    def proposal(self, client: object, name: str = "plain") -> dict:
        response = client.post(
            "/api/materials/upload",
            files={"file": (f"{name}.txt", (MATERIALS / f"regulation-{name}.txt").read_bytes(), "text/plain")},
        )
        self.assertEqual(response.status_code, 200, response.text)
        run = self.invoke(client, "recipe.start", {"material_id": response.json()["id"]})
        person = store.authenticate_session(client.cookies.get("ontology_session"), "a1-build")[0]
        with use_context(person), patch.object(build, "model_available", return_value=False):
            asyncio.run(build.run_recipe(run["id"]))
        run = self.invoke(client, "recipe.status", {"run_id": run["id"]})
        return self.invoke(client, "changeset.get", {"changeset_id": run["changeset_id"]})

    def confirm(self, client: object, proposal: dict, excluded: set | None = None, **extra: object) -> dict:
        items = [item for item in proposal["items"] if item["id"] not in (excluded or set())]
        return self.invoke(
            client,
            "changeset.confirm_facts",
            {
                "changeset_id": proposal["id"],
                "confirm_no_exceptions_item_ids": [
                    item["id"] for item in items if not item["after_data"].get("exceptions")
                ],
                "confirm_unconditional_item_ids": [
                    item["id"] for item in items if not item["after_data"].get("conditions")
                ],
                **extra,
            },
        )

    def accept(self, client: object, proposal: dict, ids: list[str] | None = None) -> dict:
        return self.invoke(
            client,
            "changeset.decide_items",
            {
                "changeset_id": proposal["id"],
                "item_ids": ids or [item["id"] for item in proposal["items"]],
                "decision": "accepted",
            },
        )

    def test_date_once_bulk_actor_provenance_and_partial_submission(self) -> None:
        client = self.login("fixture.builder")
        proposal = self.proposal(client, "repeal")
        self.assertEqual(proposal["document_facts"]["valid_from"]["value"], "")
        self.assertTrue(all(not item["after_data"]["valid_from"] for item in proposal["items"]))
        excluded = proposal["items"][0]["id"]
        missing_conditions = {item["id"] for item in proposal["items"] if not item["after_data"].get("conditions")}
        proposal = self.confirm(client, proposal, {excluded}, valid_from="2026-02-01")
        for item in proposal["items"]:
            self.assertEqual(item["after_data"]["valid_from"], "2026-02-01")
            if item["id"] != excluded and item["id"] in missing_conditions:
                self.assertEqual(item["after_data"]["field_provenance"]["conditions"]["origin"], "bulk_confirmation")
                self.assertTrue(item["after_data"]["field_provenance"]["conditions"]["actor_id"])
        proposal = self.accept(client, proposal)
        self.assertTrue(proposal["can_submit"])
        self.assertTrue(proposal["submit_blockers"])
        result = self.invoke(client, "changeset.apply_and_submit", {"changeset_id": proposal["id"]})
        self.assertGreater(result["submitted_count"], 0)
        self.assertEqual(result["remaining_count"], 1)
        self.assertEqual(result["remaining_items"][0]["item_id"], excluded)
        reopened = self.invoke(client, "changeset.get", {"changeset_id": proposal["id"]})
        pending = next(item for item in reopened["items"] if item["id"] == excluded)
        self.assertEqual(pending["applied_status"], "draft")
        self.assertTrue(all(item["applied_status"] == "review" for item in reopened["items"] if item["id"] != excluded))
        person = store.authenticate_session(client.cookies.get("ontology_session"), "a1-build")[0]
        with use_context(person), closing(db.connect()) as conn:
            row = conn.execute(
                "SELECT gates FROM entity_meta WHERE entity_id=?", (result["submitted_entity_ids"][0],)
            ).fetchone()
            self.assertIn("batch_confirmation", row["gates"])
        # Reversing expected success to all-or-nothing must fail.
        self.assertNotEqual(result["submitted_count"], len(proposal["items"]))

    def test_external_edit_keeps_its_version_while_other_items_submit_once(self) -> None:
        client = self.login("fixture.builder")
        proposal = self.confirm(client, self.proposal(client))
        selected = [item["id"] for item in proposal["items"] if item["gates"]["passed"]][:2]
        self.assertEqual(len(selected), 2)
        self.accept(client, proposal, selected)
        request = {"changeset_id": proposal["id"]}
        first, second = self.invoke(client, "changeset.apply", request)["entity_ids"]
        changed = self.invoke(
            client,
            "element.update_draft",
            {"entity_id": second, "version": 1, "title": "另一入口补充的制度标题", "reason": "外部编辑"},
        )["entity"]
        state = self.invoke(client, "changeset.get", request)
        self.assertTrue(state["can_submit"])
        self.assertTrue(next(item for item in state["items"] if item["applied_entity_id"] == second)["stale_draft"])
        submitted = self.invoke(client, "changeset.apply_and_submit", request)
        self.assertEqual(submitted["submitted_entity_ids"], [first])
        self.assertEqual(submitted["submitted_count"], 1)
        self.assertEqual(submitted["remaining_count"], 1)
        self.assertIn("刷新", submitted["remaining_items"][0]["reasons"][0])
        self.assertEqual(client.get("/api/entities/" + first).json()["status"], "review")
        current = client.get("/api/entities/" + second).json()
        self.assertEqual(
            (current["status"], current["version"], current["title"]), ("draft", changed["version"], changed["title"])
        )
        again = client.post("/api/v2/capabilities/changeset.apply_and_submit:invoke", json=request)
        self.assertEqual(again.status_code, 409, again.text)

    def test_cleared_subject_is_a_repairable_draft_and_second_submission_counts_only_new_items(self) -> None:
        client = self.login("fixture.builder")
        proposal = self.confirm(client, self.proposal(client))
        ready = [item for item in proposal["items"] if item["gates"]["passed"]][:3]
        self.assertEqual(len(ready), 3)
        queued, incomplete, later = ready
        request = {"changeset_id": proposal["id"]}
        self.accept(client, proposal, [queued["id"]])
        fields = {key: incomplete["after_data"][key] for key in ("title", "content", "kind")}
        self.invoke(
            client,
            "changeset.decide_items",
            {**request, "decision": "edited", "item_ids": [incomplete["id"]], "edit": {**fields, "applies_to_id": ""}},
        )
        first = self.invoke(client, "changeset.apply_and_submit", request)
        self.assertEqual(
            (first["submitted_count"], first["remaining_count"], first["pending_count"]),
            (1, 1, len(proposal["items"]) - 2),
        )
        current = self.invoke(client, "changeset.get", request)
        kept = next(item for item in current["items"] if item["id"] == incomplete["id"])
        self.assertEqual(kept["applied_status"], "draft")
        self.assertEqual(next(check["status"] for check in kept["gates"]["checks"] if check["code"] == "QG4"), "fail")
        self.accept(client, proposal, [later["id"]])
        second = self.invoke(client, "changeset.apply_and_submit", request)
        self.assertEqual(
            (second["submitted_count"], second["remaining_count"], second["pending_count"]),
            (1, 1, len(proposal["items"]) - 3),
        )
        self.assertTrue(set(first["submitted_entity_ids"]).isdisjoint(second["submitted_entity_ids"]))

    def test_rest_partial_edits_preserve_facts_and_can_read_an_old_malformed_value(self) -> None:
        client = self.login("fixture.builder")
        proposal = self.confirm(client, self.proposal(client))
        item = proposal["items"][0]
        original = item["after_data"]
        path = "/api/v2/changesets/" + proposal["id"]
        edit = {key: original[key] for key in ("title", "content", "kind")}
        edit["title"] += "（核对版）"
        for changes in (edit, {**edit, "waiver_applications": None}):
            saved = client.post(
                path + "/items:bulk-decide",
                json={"changeset_id": proposal["id"], "item_ids": [item["id"]], "decision": "edited", "edit": changes},
            )
            self.assertEqual(saved.status_code, 200, saved.text)
            data = next(row["after_data"] for row in saved.json()["data"]["items"] if row["id"] == item["id"])
            for key in ("scope", "conditions", "valid_from", "applies_to_id"):
                self.assertEqual(data[key], original[key])
            self.assertEqual(data["waiver_applications"], {})
        ident = self.invoke(client, "changeset.apply", {"changeset_id": proposal["id"]})["entity_ids"][0]
        # Model a corrupt historical record; this deliberately makes its gate fail.
        person = store.authenticate_session(client.cookies.get("ontology_session"), "a1-build")[0]
        with use_context(person), closing(db.connect()) as conn, conn:
            conn.execute(
                "UPDATE entities SET payload=JSON_SET(payload,'$.judgement','旧判定文本') WHERE id=?", (ident,)
            )
            conn.execute(
                "UPDATE changeset_items SET after_data=JSON_SET(after_data,'$.waiver_applications',CAST('null' AS JSON)) WHERE id=?",
                (item["id"],),
            )
        reopened = client.get(path)
        self.assertEqual(reopened.status_code, 200, reopened.text)
        row = next(r for r in reopened.json()["data"]["items"] if r["id"] == item["id"])
        self.assertEqual(row["after_data"]["waiver_applications"], {})
        self.assertIsNone(row["after_data"]["judgement"])
        self.assertIn("原有判定式", " ".join(row["validation"]["issues"]))
        self.assertEqual(next(check["status"] for check in row["gates"]["checks"] if check["code"] == "QG3"), "fail")

    def test_applied_draft_can_change_kind_and_be_discarded(self) -> None:
        client = self.login("fixture.builder")
        proposal = self.confirm(client, self.proposal(client))
        item = proposal["items"][0]
        self.accept(client, proposal, [item["id"]])
        adopted = self.invoke(client, "changeset.apply", {"changeset_id": proposal["id"]})
        reopened = self.invoke(client, "changeset.get", {"changeset_id": proposal["id"]})
        item = next(value for value in reopened["items"] if value["id"] == item["id"])
        edit = {
            key: item["after_data"].get(key, "")
            for key in ("title", "content", "scope", "definition", "exceptions", "valid_from", "applies_to_id")
        }
        edit.update(kind="knowledge", boundary="供合同付款办理人员核对使用。", expected_version=item["applied_version"])
        changed = self.invoke(
            client,
            "changeset.decide_items",
            {"changeset_id": proposal["id"], "item_ids": [item["id"]], "decision": "edited", "edit": edit},
        )
        current = next(value for value in changed["items"] if value["id"] == item["id"])
        self.assertEqual(current["after_data"]["kind"], "knowledge")
        self.assertGreater(current["applied_version"], item["applied_version"])
        discarded = self.invoke(
            client,
            "changeset.decide_items",
            {"changeset_id": proposal["id"], "item_ids": [item["id"]], "decision": "rejected"},
        )
        current = next(value for value in discarded["items"] if value["id"] == item["id"])
        self.assertEqual(current["applied_entity_id"], adopted["entity_ids"][0])
        self.assertEqual(current["applied_status"], "archived")
        self.assertFalse(current["can_submit"])
        self.assertFalse(discarded["submit_blockers"])

    def test_bulk_ready_skips_failed_items_and_invalid_edits_do_not_persist(self) -> None:
        client = self.login("fixture.builder")
        proposal = self.proposal(client)
        selected = self.invoke(
            client,
            "changeset.decide_items",
            {
                "changeset_id": proposal["id"],
                "item_ids": [item["id"] for item in proposal["items"]],
                "decision": "accepted",
                "only_ready": True,
            },
        )
        self.assertTrue(all(item["decision"] == "pending" for item in selected["items"]))
        item = proposal["items"][0]
        response = client.post(
            "/api/v2/capabilities/changeset.decide_items:invoke",
            json={
                "changeset_id": proposal["id"],
                "item_ids": [item["id"]],
                "decision": "edited",
                "edit": {
                    "title": "bad edit",
                    "content": item["after_data"]["content"],
                    "kind": "rule",
                    "judgement": {"applies_to": None},
                },
            },
        )
        self.assertEqual(response.status_code, 422, response.text)
        after = self.invoke(client, "changeset.get", {"changeset_id": proposal["id"]})
        self.assertEqual(after["items"][0]["after_data"]["title"], item["after_data"]["title"])

    def test_changeset_application_allows_only_review_submission_not_release(self) -> None:
        client, reviewer = self.login("fixture.builder"), self.login("fixture.reviewer")
        proposal = self.proposal(client)
        item = proposal["items"][0]
        edited = self.invoke(
            client,
            "changeset.decide_items",
            {
                "changeset_id": proposal["id"],
                "item_ids": [item["id"]],
                "decision": "edited",
                "edit": {
                    "title": item["after_data"]["title"],
                    "content": item["after_data"]["content"],
                    "kind": "rule",
                    "waiver_applications": {"QG3": "原制度未说明附加条件和例外，请独立核对。"},
                },
            },
        )
        self.assertTrue(edited["can_submit"])
        self.assertFalse(edited["items"][0]["gates"]["passed"])
        applied = self.invoke(client, "changeset.apply_and_submit", {"changeset_id": proposal["id"]})
        self.assertEqual(applied["submitted_count"], 1)
        ident = applied["submitted_entity_ids"][0]
        detail = reviewer.get("/api/v2/reviews/" + ident)
        self.assertEqual(detail.status_code, 200, detail.text)
        check = next(check for check in detail.json()["data"]["gates"]["checks"] if check["code"] == "QG3")
        self.assertEqual(check["status"], "fail")
        self.assertIn("原制度未说明", check["application_reason"])
        denied = reviewer.post(f"/api/v2/reviews/{ident}:decide", json={"decision": "approve_publish", "version": 2})
        self.assertEqual(denied.status_code, 409, denied.text)
        self.assertEqual(client.get("/api/entities/" + ident).json()["status"], "review")

    def test_batch_confirmation_cannot_overwrite_existing_evidence_or_cross_plan(self) -> None:
        client = self.login("fixture.builder")
        proposal = self.confirm(client, self.proposal(client))
        item = proposal["items"][0]
        for extra, code in (
            ({"confirm_no_exceptions_item_ids": [item["id"]]}, 409),
            ({"confirm_no_exceptions_item_ids": ["CSI-other-plan"]}, 404),
            ({"valid_from": "2026-02-30"}, 422),
        ):
            response = client.post(
                "/api/v2/capabilities/changeset.confirm_facts:invoke", json={"changeset_id": proposal["id"], **extra}
            )
            self.assertEqual(response.status_code, code, response.text)
        after = self.invoke(client, "changeset.get", {"changeset_id": proposal["id"]})
        self.assertEqual(after["document_facts"], proposal["document_facts"])
        self.assertEqual(after["items"][0]["after_data"], item["after_data"])

    def test_adopted_skill_can_become_rule_without_losing_identity_or_editors(self) -> None:
        first, second = self.login("fixture.admin"), self.login("fixture.builder")
        proposal = self.confirm(first, self.proposal(first))
        item = proposal["items"][0]
        after = item["after_data"]
        self.invoke(
            first,
            "changeset.decide_items",
            {
                "changeset_id": proposal["id"],
                "item_ids": [item["id"]],
                "decision": "edited",
                "edit": {"title": after["title"], "content": after["content"], "kind": "skill"},
            },
        )
        adopted = self.invoke(second, "changeset.apply", {"changeset_id": proposal["id"]})
        ident = adopted["entity_ids"][0]
        self.assertEqual(second.get("/api/entities/" + ident).json()["kind"], "skill")
        edit = {
            key: after[key]
            for key in ("title", "content", "scope", "conditions", "exceptions", "valid_from", "applies_to_id")
        }
        edit.update(kind="rule", expected_version=1)
        changed = self.invoke(
            second,
            "changeset.decide_items",
            {"changeset_id": proposal["id"], "item_ids": [item["id"]], "decision": "edited", "edit": edit},
        )
        current = next(value for value in changed["items"] if value["id"] == item["id"])
        self.assertEqual(current["applied_entity_id"], ident)
        self.assertEqual(current["after_data"]["kind"], "rule")
        submitted = self.invoke(second, "changeset.apply_and_submit", {"changeset_id": proposal["id"]})
        self.assertEqual(submitted["submitted_entity_ids"], [ident])
        rejected = first.post(f"/api/v2/reviews/{ident}:decide", json={"decision": "approve_publish", "version": 3})
        self.assertEqual(rejected.status_code, 403, rejected.text)
        self.assertEqual(second.get("/api/entities/" + ident).json()["status"], "review")
