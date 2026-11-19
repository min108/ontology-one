"""D12 independent-review corrections via source-only rules and real persistence."""

import asyncio
import os
import unittest
from unittest.mock import patch

from backend import materials
from backend.domains import build
from backend.security import store
from backend.security.context import use_context
from tests.r2 import test_build as construction
from tests.r4a1 import test_build_workflow as workflow


@unittest.skipUnless(os.getenv("RUN_JOBS_MYSQL_TESTS") == "1", "opt-in D12 source provenance/MySQL")
class SourceReviewMySQL(unittest.TestCase):
    setUpClass = classmethod(construction.BuildMySQL.setUpClass.__func__)
    tearDownClass = classmethod(construction.BuildMySQL.tearDownClass.__func__)
    login = construction.BuildMySQL.login
    invoke = construction.BuildMySQL.invoke
    proposal = workflow.BuildWorkflow.proposal

    def test_immutable_documents_cannot_confirm_explicit_branch_as_absent(self) -> None:
        client = self.login("fixture.builder")
        for name, clause, field, request in [
            ("plain", "资料不齐全的", "exceptions", "confirm_no_exceptions_item_ids"),
            ("history", "发现虚假票据的", "conditions", "confirm_unconditional_item_ids"),
        ]:
            with self.subTest(document=name):
                proposal = self.proposal(client, name)
                item = next(item for item in proposal["items"] if clause in item["after_data"]["content"])
                self.assertTrue(item["after_data"][field])
                response = client.post(
                    "/api/v2/capabilities/changeset.confirm_facts:invoke",
                    json={"changeset_id": proposal["id"], request: [item["id"]]},
                )
                self.assertEqual(response.status_code, 422, response.text)
                self.assertIn("原文中已写明", response.text)
                saved = self.invoke(client, "changeset.get", {"changeset_id": proposal["id"]})
                current = next(value for value in saved["items"] if value["id"] == item["id"])
                self.assertEqual(current["after_data"], item["after_data"])

    def synthetic_proposal(self, client: object, kind: str, text: str, fields: dict, *, quote: str = "") -> dict:
        """Mock only the model boundary, then run the normal source/adoption services."""
        uploaded = client.post("/api/materials/upload", files={"file": ("来源核对.txt", text.encode(), "text/plain")})
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        run = self.invoke(client, "recipe.start", {"material_id": uploaded.json()["id"]})

        async def extracted(
            material_id: str, chunks: list[dict], context: dict, *, diagnostics: dict
        ) -> tuple[list[dict], dict]:
            self.assertIsInstance(diagnostics, dict)
            chunk = next(part for part in chunks if quote in part["content"])
            title = "字段出处核对" + kind + text[:12]
            return [
                {
                    "id": materials.candidate_id(material_id, kind, title, [chunk["id"]]),
                    "kind": kind,
                    "title": title,
                    "content": quote or chunk["content"],
                    "source_chunk_ids": [chunk["id"]],
                    "source_quotes": [{"chunk_id": chunk["id"], "quote": quote or chunk["content"]}],
                    "status": "pending",
                    "extraction_mode": "online",
                    "questions": [],
                    **fields,
                }
            ], {}

        person = store.authenticate_session(client.cookies.get("ontology_session"), "source-review")[0]
        with (
            use_context(person),
            patch.object(build, "model_available", return_value=True),
            patch.object(materials, "online_candidates", side_effect=extracted),
        ):
            asyncio.run(build.run_recipe(run["id"]))
        ready = self.invoke(client, "recipe.status", {"run_id": run["id"]})
        proposal = self.invoke(client, "changeset.get", {"changeset_id": ready["changeset_id"]})
        self.assertFalse(proposal["extraction_summary"]["fully_local"])
        self.assertEqual(proposal["items"][0]["content_origin"], "source")
        self.assertEqual(proposal["items"][0]["after_data"]["kind"], kind)
        return proposal

    def test_invented_model_measurement_cannot_pass_or_be_labeled_source(self) -> None:
        client = self.login("fixture.builder")
        proposal = self.synthetic_proposal(
            client,
            "data",
            "登记付款办理情况。",
            {"definition": "每笔必须收十万元", "unit": "美元", "grain": "每日每部门"},
        )
        item = proposal["items"][0]
        self.assertEqual(item["after_data"]["definition"], "登记付款办理情况")
        self.assertEqual(item["after_data"]["unit"], "")
        self.assertEqual(item["after_data"]["grain"], "")
        self.assertFalse(item["ready_for_bulk_accept"])
        self.assertEqual(next(check["status"] for check in item["gates"]["checks"] if check["code"] == "QG2"), "fail")
        self.invoke(
            client,
            "changeset.decide_items",
            {"changeset_id": proposal["id"], "item_ids": [item["id"]], "decision": "accepted"},
        )
        result = self.invoke(client, "changeset.apply_and_submit", {"changeset_id": proposal["id"]})
        self.assertEqual(result["submitted_count"], 0)
        self.assertEqual(result["remaining_count"], 1)
        gates = self.invoke(client, "quality.check_gates", {"entity_id": result["entity_ids"][0]})
        facts = {fact["key"]: fact for fact in gates["fields"]}
        for key in ("unit", "grain"):
            self.assertEqual(facts[key]["source"], "missing")
            self.assertEqual(facts[key]["value"], "")

    def test_explicit_exemptions_and_conditions_reject_negative_confirmation_on_reread(self) -> None:
        client = self.login("fixture.builder")
        for text, key, request, expected in [
            ("除紧急抢救外一律按顺序处理。", "exceptions", "confirm_no_exceptions_item_ids", "原文中已写明"),
            ("所有申请均须复核，抢险救灾无需复核。", "exceptions", "confirm_no_exceptions_item_ids", "原文中已写明"),
            ("一般事项逐级办理，紧急事项走绿色通道。", "exceptions", "confirm_no_exceptions_item_ids", "请先逐条核对"),
            (
                "所有申请须经复核，抢险救灾情形可免予复核。",
                "exceptions",
                "confirm_no_exceptions_item_ids",
                "原文中已写明",
            ),
            (
                "若发现虚假票据，财务部门一律退回报销申请。",
                "conditions",
                "confirm_unconditional_item_ids",
                "原文中已写明",
            ),
            ("若申请资料尚待核实。", "conditions", "confirm_unconditional_item_ids", "请先逐条核对"),
        ]:
            with self.subTest(text=text):
                proposal = self.synthetic_proposal(client, "rule", text, {})
                item = proposal["items"][0]
                after = item["after_data"]
                if after[key]:
                    self.assertEqual(after["field_provenance"][key]["origin"], "system")
                    self.assertIn(after["field_provenance"][key]["quote"], text)
                    self.assertIn(key, after["source_declared_fields"])
                else:
                    self.assertTrue(after["bulk_confirmation_blocked"])
                response = client.post(
                    "/api/v2/capabilities/changeset.confirm_facts:invoke",
                    json={"changeset_id": proposal["id"], request: [item["id"]]},
                )
                self.assertEqual(response.status_code, 422, response.text)
                self.assertIn(expected, response.text)
                saved = self.invoke(client, "changeset.get", {"changeset_id": proposal["id"]})
                self.assertEqual(saved["items"][0]["after_data"], after)

    def test_manual_clear_does_not_make_a_declared_source_field_eligible_for_bulk_confirmation(self) -> None:
        client = self.login("fixture.builder")
        text = "所有申请均须复核，抢险救灾无需复核。"
        proposal = self.synthetic_proposal(client, "rule", text, {})
        item = proposal["items"][0]
        fields = {key: item["after_data"][key] for key in ("title", "content", "kind")}
        updated = self.invoke(
            client,
            "changeset.decide_items",
            {
                "changeset_id": proposal["id"],
                "item_ids": [item["id"]],
                "decision": "edited",
                "edit": {**fields, "exceptions": ""},
            },
        )
        after = updated["items"][0]["after_data"]
        self.assertEqual(after["exceptions"], "")
        self.assertEqual(after["source_declared_fields"], ["exceptions"])
        self.assertFalse(after["bulk_confirmation_blocked"])
        response = client.post(
            "/api/v2/capabilities/changeset.confirm_facts:invoke",
            json={"changeset_id": proposal["id"], "confirm_no_exceptions_item_ids": [item["id"]]},
        )
        self.assertEqual(response.status_code, 422, response.text)
        saved = self.invoke(client, "changeset.get", {"changeset_id": proposal["id"]})
        self.assertEqual(saved["items"][0]["after_data"], after)

    def test_supported_fields_persist_individual_source_and_knowledge_mapping(self) -> None:
        client = self.login("fixture.builder")
        cases = [
            (
                "data",
                "付款台账按份登记，以元计量。",
                {"definition": "付款台账", "unit": "元", "grain": "按份登记"},
                {"definition": "付款台账", "unit": "元", "grain": "按份登记"},
            ),
            (
                "knowledge",
                "办理付款时核对台账，仅供合同付款核对使用。",
                {"definition": "核对台账", "conditions": ["办理付款时"], "boundary": "仅供合同付款核对使用"},
                {"definition": "核对台账", "usage_conditions": "办理付款时", "boundary": "仅供合同付款核对使用"},
            ),
        ]
        for kind, text, fields, expected in cases:
            with self.subTest(kind=kind):
                proposal = self.synthetic_proposal(client, kind, text, fields)
                item = proposal["items"][0]
                preview = {fact["key"]: fact for fact in item["gates"]["fields"]}
                for key, value in expected.items():
                    self.assertEqual((preview[key]["value"], preview[key]["source"]), (value, "source"))
                    self.assertIn(value, preview[key]["source_quote"])
                self.invoke(
                    client,
                    "changeset.decide_items",
                    {"changeset_id": proposal["id"], "item_ids": [item["id"]], "decision": "accepted"},
                )
                adopted = self.invoke(client, "changeset.apply", {"changeset_id": proposal["id"]})
                gates = self.invoke(client, "quality.check_gates", {"entity_id": adopted["entity_ids"][0]})
                facts = {fact["key"]: fact for fact in gates["fields"]}
                for key, value in expected.items():
                    self.assertEqual(facts[key]["value"], value)
                    self.assertEqual(facts[key]["source"], "source")
                    self.assertIn(value, facts[key]["source_quote"])
                    self.assertIn(facts[key]["source_quote"], text)
