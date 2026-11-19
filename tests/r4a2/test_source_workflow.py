"""AC5/6 exercise the real upload, selection, draft, and independent review reads."""

import asyncio
import os
import unittest
from contextlib import nullcontext
from dataclasses import replace
from typing import Literal
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.capabilities.context import CapabilityContext
from backend.capabilities.registry import registry
from backend.core.errors import ApiError
from backend.domains import build
from backend.security import store
from backend.security.context import use_context
from tests.r2 import test_build as construction
from tests.r4a1 import test_build_source_review as source_review


@unittest.skipUnless(os.getenv("RUN_JOBS_MYSQL_TESTS") == "1", "opt-in A.2 source facts/MySQL")
class SourceWorkflow(unittest.TestCase):
    setUpClass = classmethod(construction.BuildMySQL.setUpClass.__func__)
    tearDownClass = classmethod(construction.BuildMySQL.tearDownClass.__func__)
    login = construction.BuildMySQL.login
    invoke = construction.BuildMySQL.invoke
    synthetic_proposal = source_review.SourceReviewMySQL.synthetic_proposal

    def proposal(self, client: TestClient, text: str, *, legacy: bool | Literal["a2"] = False) -> dict:
        uploaded = client.post("/api/materials/upload", files={"file": ("制度沿革.txt", text.encode(), "text/plain")})
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        run = self.invoke(client, "recipe.start", {"material_id": uploaded.json()["id"]})
        person = store.authenticate_session(client.cookies.get("ontology_session"), "source-facts")[0]

        current_facts = build.anchored_document_facts

        def old_file_facts(conn: object, material_id: str, source: dict) -> dict:
            if legacy == "a2":
                stored = current_facts(conn, material_id, source)
                stored.pop("recognition_version")
                stored["valid_from_candidates"] = stored["valid_from_candidates"][:1]
                stored["valid_from"] = dict(stored["valid_from_candidates"][0])
                stored["scope"]["value"] = "公司各部门"
                return stored
            recognized = build.document_facts(source["text"])
            old = {key: dict(recognized[key], origin="document") for key in ("scope", "valid_from")}
            old["valid_from"] = dict(recognized["valid_from_candidates"][0], origin="document")
            for fact in old.values():
                fact.pop("level", None)
            return old

        with (
            use_context(person),
            patch.object(build, "model_available", return_value=False),
            patch.object(build, "anchored_document_facts", side_effect=old_file_facts) if legacy else nullcontext(),
        ):
            asyncio.run(build.run_recipe(run["id"]))
        status = self.invoke(client, "recipe.status", {"run_id": run["id"]})
        return self.invoke(client, "changeset.get", {"changeset_id": status["changeset_id"]})

    def test_selection_rechecks_immutable_quotes_and_survives_bulk_and_adoption(self) -> None:
        client = self.login("fixture.builder")
        text = (
            "财务检查办法\n\n第一条 本办法适用于本部与分支机构。\n"
            "第二条 如遇系统故障，经办人应报告；紧急救助事项除外。\n"
            "第三条 出纳人员应删除外部共享文件。\n"
            "第四条 本制度于2018年5月1日实施，至今已修订三次。\n"
            "第五条 本次修订版自2026年1月1日起施行。"
        )
        proposal = self.proposal(client, text)
        facts = proposal["document_facts"]
        self.assertFalse(facts["valid_from"]["value"])
        opts = facts["valid_from_candidates"]
        self.assertEqual([entry["value"] for entry in opts], ["2018-05-01", "2026-01-01"])
        self.assertGreater(len(proposal["items"]), 1)
        matches = [item for item in proposal["items"] if "如遇系统故障" in item["after_data"]["content"]]
        self.assertEqual(len(matches), 1)
        rule = matches[0]
        before = {f["key"]: f for f in rule["gates"]["fields"]}
        self.assertEqual(before["conditions"]["source"], "system")
        self.assertEqual(before["exceptions"]["source"], "system")
        self.assertTrue(before["conditions"]["source_quote"])
        self.assertTrue(before["exceptions"]["source_quote"])
        req = {"changeset_id": proposal["id"]}
        path = f"/api/v2/changesets/{proposal['id']}/facts:confirm"
        for user, status in [("r2.other", 404), ("fixture.reviewer", 403)]:
            unauthorized = self.login(user).post(path, json={**req, "valid_from_candidate_id": opts[-1]["anchor_id"]})
            self.assertEqual(unauthorized.status_code, status, unauthorized.text)
        principal = store.authenticate_session(client.cookies.get("ontology_session"), "source-facts")[0]
        readonly = replace(principal, readonly=True)
        with use_context(readonly), self.assertRaises(ApiError) as denied:
            registry.invoke(
                "changeset.confirm_facts",
                CapabilityContext(readonly, permissions=frozenset({"build.edit"})),
                {**req, "valid_from_candidate_id": opts[-1]["anchor_id"]},
            )
        self.assertEqual(denied.exception.code, "PERMISSION_DENIED")
        missing_csrf = self.login("fixture.builder")
        del missing_csrf.headers["X-CSRF-Token"]
        response = missing_csrf.post(path, json={**req, "valid_from_candidate_id": opts[-1]["anchor_id"]})
        self.assertEqual(response.status_code, 403, response.text)
        other = self.proposal(client, "发放管理办法\n第一条 本办法自2029年3月1日起施行。")
        other_anchor = other["document_facts"]["valid_from_candidates"][0]["anchor_id"]
        for invalid in [
            {"valid_from_candidate_id": other_anchor},
            {"valid_from_candidate_id": rule["evidence"][0]["anchor_id"]},
            {"valid_from_candidate_id": opts[-1]["anchor_id"], "valid_from": "2027-01-01"},
        ]:
            reply = client.post(path, json={**req, **invalid})
            self.assertEqual(reply.status_code, 422, reply.text)
        reread = self.invoke(client, "changeset.get", req)
        self.assertEqual(reread["document_facts"], facts)
        self.assertEqual([i["after_data"] for i in reread["items"]], [i["after_data"] for i in proposal["items"]])
        chosen = opts[-1]
        saved = self.invoke(client, "changeset.confirm_facts", {**req, "valid_from_candidate_id": chosen["anchor_id"]})
        selected = saved["document_facts"]["valid_from"]
        for key in ("value", "quote", "start", "end", "source_version", "material_id", "anchor_id"):
            self.assertEqual(selected[key], chosen[key])
        self.assertEqual(text[selected["start"] : selected["end"]], selected["quote"])
        self.assertEqual(selected["origin"], "manual")
        self.assertEqual(selected["method"], "selection")
        self.assertEqual(selected["confirmed_by"], "fixture.builder")
        self.assertTrue(selected["actor_id"])
        self.assertTrue(selected["confirmed_at"])
        self.assertEqual(saved["document_facts"]["scope"], facts["scope"])
        self.assertEqual(saved["document_facts"]["valid_from_candidates"], opts)
        for item in saved["items"]:
            self.assertEqual(item["after_data"]["valid_from"], chosen["value"])
            self.assertEqual(item["after_data"]["field_provenance"]["valid_from"], selected)
        ordinary = next(i for i in saved["items"] if "删除外部" in i["after_data"]["content"])
        self.assertEqual(ordinary["after_data"]["exceptions"], "")
        saved = self.invoke(
            client, "changeset.confirm_facts", {**req, "confirm_no_exceptions_item_ids": [ordinary["id"]]}
        )
        self.assertEqual(saved["document_facts"]["valid_from"], selected)
        self.invoke(client, "changeset.decide_items", {**req, "decision": "accepted", "item_ids": [rule["id"]]})
        result = self.invoke(client, "changeset.apply_and_submit", req)
        self.assertEqual(result["submitted_count"], 1)
        review = self.invoke(
            self.login("fixture.reviewer"), "review.get", {"entity_id": result["submitted_entity_ids"][0]}
        )
        fields = {f["key"]: f for f in review["gates"]["fields"]}
        date = fields["valid_from"]
        self.assertEqual(
            (date["source"], date["method"], date["source_quote"], date["confirmed_by"]),
            ("editor", "selection", chosen["quote"], "fixture.builder"),
        )
        for key in ("conditions", "exceptions"):
            self.assertEqual(
                (fields[key]["source"], fields[key]["source_quote"]), ("system", before[key]["source_quote"])
            )
        subject = next(f for f in fields.values() if f["key"].startswith("subject:"))
        self.assertEqual(subject["source"], "system")

    def test_manual_date_can_replace_a_selection_without_keeping_its_anchor(self) -> None:
        client = self.login("fixture.builder")
        proposal = self.proposal(client, "附件二：原规定摘录\n第一条 本规定自2017年9月1日起施行。")
        req = {"changeset_id": proposal["id"]}
        opt = proposal["document_facts"]["valid_from_candidates"][0]
        self.assertEqual(proposal["document_facts"]["valid_from"]["value"], "")
        selected = self.invoke(client, "changeset.confirm_facts", {**req, "valid_from_candidate_id": opt["anchor_id"]})
        self.assertEqual(selected["document_facts"]["valid_from"]["quote"], opt["quote"])
        saved = self.invoke(client, "changeset.confirm_facts", {**req, "valid_from": "2027-06-01"})
        facts = saved["document_facts"]
        self.assertEqual(facts["valid_from_candidates"], [opt])
        self.assertEqual(facts["valid_from"]["value"], "2027-06-01")
        self.assertEqual(facts["valid_from"]["confirmed_by"], "fixture.builder")
        self.assertEqual(facts["valid_from"]["quote"], "")
        self.assertFalse(facts["valid_from"].get("method"))

    def test_preupgrade_pending_proposal_offers_its_own_dates_and_preserves_manual_input(self) -> None:
        client = self.login("fixture.builder")
        text = (
            "集团差旅制度\n第一条 本制度于2018年5月1日实施，至今已修订三次。\n"
            "第二条 本次修订版自2026年1月1日起施行。\n第三条 财务人员应核验凭证。"
        )
        proposal = self.proposal(client, text, legacy=True)
        req = {"changeset_id": proposal["id"]}
        facts = proposal["document_facts"]
        self.assertFalse(facts["valid_from"]["value"])
        self.assertEqual([c["value"] for c in facts["valid_from_candidates"]], ["2018-05-01", "2026-01-01"])
        self.assertTrue(all(not i["after_data"]["valid_from"] for i in proposal["items"]))
        intended = facts["valid_from_candidates"][1]
        saved = self.invoke(
            client, "changeset.confirm_facts", {**req, "valid_from_candidate_id": intended["candidate_id"]}
        )
        choice = saved["document_facts"]["valid_from"]
        self.assertTrue(choice["anchor_id"])
        self.assertEqual(choice["quote"], intended["quote"])
        self.assertEqual(choice["source_version"], intended["source_version"])
        self.assertEqual(choice["value"], "2026-01-01")
        self.assertEqual(choice["origin"], "manual")

    def test_pending_a2_proposal_rechecks_ambiguous_dates_and_scope_from_immutable_text(self) -> None:
        client = self.login("fixture.builder")
        text = (
            "采购管理办法\n第一条 本办法自2018年1月1日起施行。\n"
            "第二条 本办法适用于公司各部门，但不适用于海外分支机构。\n第三条 财务部门应核验付款凭证。\n"
            "第四条 本办法修订后自2026年1月1日起施行。"
        )
        proposal = self.proposal(client, text, legacy="a2")
        facts = proposal["document_facts"]
        self.assertFalse(facts["valid_from"]["value"])
        options = facts["valid_from_candidates"]
        self.assertEqual([row["value"] for row in options], ["2018-01-01", "2026-01-01"])
        self.assertEqual(facts["scope"]["value"], "公司各部门，但不适用于海外分支机构")
        self.assertGreater(len(proposal["items"]), 0)
        for item in proposal["items"]:
            self.assertFalse(item["after_data"].get("valid_from"))
        updated = self.invoke(
            client,
            "changeset.confirm_facts",
            {
                "changeset_id": proposal["id"],
                "valid_from_candidate_id": options[1]["candidate_id"],
            },
        )
        choice = updated["document_facts"]["valid_from"]
        self.assertEqual((choice["origin"], choice["value"]), ("manual", "2026-01-01"))
        self.assertIn("修订后", choice["quote"])
        self.assertTrue(choice["anchor_id"])

    def test_bulk_adoption_keeps_system_applicability_and_model_literal_sources_separate(self) -> None:
        client = self.login("fixture.builder")
        text = "本制度适用于账款核对。若凭证丢失，经办人应报告，抢险事项除外。本制度自2028年1月1日起施行。"
        proposal = self.synthetic_proposal(
            client, "rule", text, {"conditions": ["若凭证丢失"], "scope": "账款核对", "exceptions": "抢险事项除外"}
        )
        req = {"changeset_id": proposal["id"]}
        item = proposal["items"][0]
        self.assertTrue(item["ready_for_bulk_accept"])
        accepted = self.invoke(
            client,
            "changeset.decide_items",
            {**req, "decision": "accepted", "only_ready": True, "item_ids": [item["id"]]},
        )
        current = next(i for i in accepted["items"] if i["id"] == item["id"])
        self.assertEqual(current["decision"], "accepted")
        self.assertEqual(current["after_data"]["field_provenance"]["applies_to_id"]["origin"], "system")
        result = self.invoke(client, "changeset.apply_and_submit", req)
        self.assertEqual(result["submitted_count"], 1)
        review = self.invoke(
            self.login("fixture.reviewer"), "review.get", {"entity_id": result["submitted_entity_ids"][0]}
        )
        values = {f["key"]: f for f in review["gates"]["fields"]}
        target = next(f for f in values.values() if f["key"].startswith("subject:"))
        self.assertEqual(target["source"], "system")
        self.assertFalse(target["confirmed_by"])
        for key in ("scope", "conditions", "exceptions"):
            self.assertEqual(values[key]["source"], "source")
            self.assertTrue(values[key]["source_quote"])
        self.assertEqual(values["valid_from"]["source"], "system")

    def test_mixed_conditions_keep_each_origin_after_adoption(self) -> None:
        client = self.login("fixture.builder")
        text = (
            "本办法适用于票据交接。若原件遗失，经办人应报告；超过限额的，须经批准。"
            "但紧急事项可先行办理。本办法自2029年1月1日起施行。"
        )
        result = self.synthetic_proposal(client, "rule", text, {"conditions": ["若原件遗失"]})
        item = result["items"][0]
        req = {"changeset_id": result["id"]}
        self.invoke(client, "changeset.decide_items", {**req, "decision": "accepted", "item_ids": [item["id"]]})
        adopted = self.invoke(client, "changeset.apply", req)
        gates = self.invoke(client, "quality.check_gates", {"entity_id": adopted["entity_ids"][0]})
        fields = {f["key"]: f for f in gates["fields"]}
        conditions = fields["conditions"]
        self.assertEqual(conditions["source"], "system")
        details = {part["value"]: part for part in conditions["parts"]}
        self.assertEqual(details["若原件遗失"]["source"], "source")
        self.assertEqual(details["超过限额的"]["source"], "system")
        for value, part in details.items():
            self.assertTrue(part["anchor_id"])
            self.assertIn(value, part["source_quote"])
