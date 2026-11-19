"""A signed search hit opens a card with exactly the citations its reader can follow."""

import json
import os
import unittest
from contextlib import closing
from dataclasses import replace

from backend import db
from backend.api.v2.routes import registry
from backend.capabilities.context import CapabilityContext
from backend.core.errors import ApiError
from backend.mcp_tools import dispatch_tool
from backend.security import applications
from backend.security.context import use_context
from backend.security.permissions import permissions_for
from tests.r4b import test_semantic_actions as semantic


@unittest.skipUnless(os.getenv("RUN_JOBS_MYSQL_TESTS") == "1", "signed card, citation and graph fixture")
class PublishedElementDetail(unittest.TestCase):
    setUpClass = classmethod(semantic.SemanticActionsHTTP.setUpClass.__func__)
    login = semantic.SemanticActionsHTTP.login
    identity = semantic.SemanticActionsHTTP.identity
    evidence = semantic.SemanticActionsHTTP.evidence
    publish_asset = semantic.SemanticActionsHTTP.publish_asset
    create = semantic.SemanticActionsHTTP.create
    receipt = semantic.SemanticActionsHTTP.receipt
    invoke = semantic.SemanticActionsHTTP.invoke
    business = semantic.SemanticActionsHTTP.business
    get = semantic.SemanticActionsHTTP.get

    def test_register_alias_keeps_an_archived_source_through_independent_publication(self) -> None:
        editor, reviewer, viewer = (self.login("b1." + role) for role in ("modeler", "reviewer", "viewer"))
        current = self.evidence(editor)
        historical = self.evidence(editor, "原交接办法要求经办人保留一份签收回单，以备查档。")
        refs = [current, historical]
        draft = self.create(
            editor,
            "data",
            "清册签收份数",
            {
                "definition": "每次交接登记的份数",
                "unit": "份",
                "grain": "每次交接",
                "aliases": "原有称呼",
                "source_refs": refs,
            },
        )
        published = self.publish_asset(editor, reviewer, draft)
        ident = published["id"]
        raw_path = "/api/entities/" + ident
        detail_path = "/api/v2/elements/" + ident
        save_path = "/api/v2/capabilities/element.update_draft:invoke"
        original = self.get(editor, raw_path)["payload"]
        self.assertEqual(original["source_refs"], refs)
        self.assertNotEqual(current["material_id"], historical["material_id"])
        archived = self.receipt(
            reviewer.post(
                "/api/entities/" + historical["material_id"] + "/action",
                json={"action": "archive", "reason": "改用本期交接办法"},
            )
        )
        self.assertEqual(archived["status"], "archived")
        self.assertEqual(
            [ref["anchor_id"] for ref in self.get(editor, detail_path)["data"]["entity"]["payload"]["source_refs"]],
            [current["anchor_id"]],
        )
        revision = self.invoke(
            editor,
            "element.start_revision",
            {"entity_id": ident, "version": published["version"], "reason": "登记清册日常称呼"},
        )["entity"]
        self.assertEqual(revision["status"], "draft")
        change = {"entity_id": ident, "version": revision["version"], "add_alias": "交接回单数"}
        saved = self.invoke(editor, "element.update_draft", change)["entity"]
        self.assertEqual(saved["payload"]["aliases"], ["原有称呼", "交接回单数"])
        self.assertEqual([ref["anchor_id"] for ref in saved["payload"]["source_refs"]], [current["anchor_id"]])

        def assert_persisted(status: str) -> None:
            actual = self.get(editor, raw_path)
            self.assertEqual(actual["status"], status)
            self.assertEqual(actual["payload"]["aliases"], ["原有称呼", "交接回单数"])
            self.assertEqual(actual["payload"]["source_refs"], refs)
            for key, value in original.items():
                if key not in {"aliases", "r2_review"}:
                    self.assertEqual(actual["payload"][key], value, key)

        assert_persisted("draft")
        next_change = {**change, "version": saved["version"], "add_alias": "另一个名称"}
        for client, body, status in (
            (editor, {**change, "add_alias": "过期客户端名称"}, 409),
            (editor, {**next_change, "add_alias": " 交接回单数 "}, 409),
            (editor, {**next_change, "add_alias": "  "}, 422),
            (editor, {**next_change, "payload": {"source_refs": []}}, 422),
            (viewer, next_change, 403),
            (self.login("fixture.builder"), next_change, 404),
        ):
            with self.subTest(status=status, keys=body.keys()):
                response = client.post(save_path, json=body)
                self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(self.get(editor, raw_path)["version"], saved["version"])
        submitted = self.invoke(
            editor,
            "element.submit_draft",
            {"entity_id": ident, "version": saved["version"], "reason": "请独立复核业务别名"},
        )["entity"]
        assert_persisted("review")
        rejected = editor.post(save_path, json={**next_change, "version": submitted["version"]})
        self.assertEqual(rejected.status_code, 409, rejected.text)
        decision = {"version": submitted["version"], "decision": "approve_publish"}
        self.assertEqual(editor.post("/api/v2/reviews/" + ident + ":decide", json=decision).status_code, 403)
        self.receipt(reviewer.post("/api/v2/reviews/" + ident + ":decide", json=decision))
        assert_persisted("published")
        latest = self.get(editor, raw_path)
        rejected = editor.post(save_path, json={**next_change, "version": latest["version"]})
        self.assertEqual(rejected.status_code, 409, rejected.text)
        self.assertEqual(self.get(editor, raw_path)["version"], latest["version"])

    def test_partial_rule_edit_protects_hidden_nested_data_and_keeps_explicit_replacement(self) -> None:
        editor, reviewer = (self.login("b1." + role) for role in ("modeler", "reviewer"))
        current = self.evidence(editor)
        previous = self.evidence(editor, "交接以前使用纸质回单。")
        refs = [current, previous]
        original = self.create(
            editor,
            "rule",
            "交接回单核对",
            {
                "source_refs": refs,
                "definition": "核对交接份数。",
                "judgement": {
                    "condition": {"oxl": "true"},
                    "requirement": {"oxl": "true"},
                    "source_refs": refs,
                    "prior_record": {"material_id": previous["material_id"]},
                },
            },
        )
        ident = original["id"]
        path = "/api/entities/" + ident
        mutation = "/api/v2/capabilities/element.update_draft:invoke"
        self.receipt(
            reviewer.post(
                "/api/entities/" + previous["material_id"] + "/action",
                json={"action": "archive", "reason": "停用旧式回单"},
            )
        )
        visible = self.get(editor, "/api/v2/elements/" + ident)["data"]["entity"]["payload"]["judgement"]
        self.assertEqual([item["anchor_id"] for item in visible["source_refs"]], [current["anchor_id"]])
        self.assertNotIn("prior_record", visible)
        fields = {"entity_id": ident, "version": original["version"], "payload_fields": {"scope": "交接清册"}}
        person = self.identity(editor)
        readonly = CapabilityContext(replace(person, readonly=True), permissions=frozenset(permissions_for(person)))
        for changes in (fields, {"entity_id": ident, "version": original["version"], "add_alias": "回单规定"}):
            with self.assertRaises(ApiError) as denied:
                registry.invoke("element.update_draft", readonly, changes)
            self.assertEqual(denied.exception.code, "PERMISSION_DENIED")
        saved = self.invoke(editor, "element.update_draft", fields)["entity"]
        self.assertEqual(self.get(editor, path)["payload"]["source_refs"], refs)
        version = saved["version"]
        for invalid, status in (
            ({"payload_fields": {"source_refs": []}}, 422),
            ({"payload": {}, "payload_fields": {"scope": "相互冲突"}}, 422),
            ({"payload_fields": {"judgement": {**visible, "source_refs": []}}}, 409),
        ):
            response = editor.post(mutation, json={"entity_id": ident, "version": version, **invalid})
            self.assertEqual(response.status_code, status, response.text)
            self.assertEqual(self.get(editor, path)["version"], version)
        saved = self.invoke(
            editor,
            "element.update_draft",
            {
                "entity_id": ident,
                "version": version,
                "payload_fields": {"judgement": {**visible, "requirement": {"oxl": "false"}}},
            },
        )["entity"]
        payload = self.get(editor, path)["payload"]
        self.assertEqual((payload["scope"], payload["judgement"]["requirement"]["oxl"]), ("交接清册", "false"))
        self.assertEqual(payload["source_refs"], refs)
        self.assertEqual(payload["judgement"]["source_refs"], refs)
        self.assertEqual(payload["judgement"]["prior_record"], {"material_id": previous["material_id"]})
        # Legacy callers can still explicitly replace a complete draft.
        self.invoke(
            editor,
            "element.update_draft",
            {
                "entity_id": ident,
                "version": saved["version"],
                "payload": {"definition": "清册归集说明"},
            },
        )
        replaced = self.get(editor, path)["payload"]
        self.assertNotIn("judgement", replaced)
        self.assertNotIn("source_refs", replaced)
        self.assertEqual(replaced["definition"], "清册归集说明")
        project = self.receipt(editor.post("/api/special-projects", json={"title": "档案审核", "scope": "检查档案"}))[
            "project"
        ]
        matter = self.receipt(
            editor.post("/api/special-projects/" + project["id"] + "/matters", json={"title": "交接档案核对"})
        )["entity"]
        for edit in ({"add_alias": "交接档案"}, {"payload_fields": {"definition": "交接复核"}}):
            denied = editor.post(mutation, json={"entity_id": matter["id"], "version": matter["version"], **edit})
            self.assertEqual(denied.status_code, 422, denied.text)
            self.assertIn("专项事项与依据", denied.json()["error"]["message"])
            self.assertEqual(self.get(editor, "/api/entities/" + matter["id"])["version"], matter["version"])

    def test_search_hit_card_relations_and_citations_follow_source_publication(self) -> None:
        owner, reviewer, viewer, analyst = (
            self.login("b1." + role) for role in ("modeler", "reviewer", "viewer", "analyst")
        )
        admin = self.login("r2.other")
        anchor = self.evidence(owner)
        source = self.get(owner, "/api/entities/" + anchor["material_id"])
        obj = self.create(
            owner,
            "object_type",
            "签收凭据",
            {
                "schema": "object_type/1",
                "definition": "每张交接凭据",
                "properties": [
                    {"key": "number", "name": "登记编号", "datatype": "string", "note": "每张凭据的唯一登记编号"}
                ],
                "identity": {"properties": ["number"]},
                "source_refs": [anchor],
            },
        )
        obj = self.publish_asset(owner, reviewer, obj)
        misquoted = "附录误抄的文字不能充当原件"
        review_note = "交接审核的内部备注"
        rule = self.create(
            owner,
            "rule",
            "签收登记核对规定",
            {
                "definition": "办理登记前核对凭据编号。",
                "scope": "本机构签收事项",
                "conditions": "凭据送达时",
                "exceptions": "无例外",
                "obligation": "逐项核对",
                "valid_from": "2026-11-01",
                "article_sections": [{"title": "阶段说明", "source_refs": [{**anchor, "quote": misquoted}]}],
                "source_refs": [anchor],
            },
        )

        def link(target: dict, typ: str) -> dict:
            rel = self.invoke(
                owner,
                "relation.propose",
                {"source": rule["id"], "target": target["id"], "type": typ, "reason": review_note},
            )
            self.invoke(
                reviewer, "relation.decide", {"relation_id": rel["id"], "status": "confirmed", "reason": "已核对两端"}
            )
            return rel

        governed = link(obj, "applies_to")
        private_source = link(source, "derived_from")
        rule = self.publish_asset(owner, reviewer, self.get(owner, "/api/entities/" + rule["id"]))
        detail = "/api/v2/elements/" + rule["id"]
        secret_values = [
            anchor["material_id"],
            anchor["source_version"],
            anchor["anchor_id"],
            anchor["quote"],
            source["title"],
        ]
        for client in (viewer, analyst):
            (hit,) = self.business(client, "semantic.search", {"q": rule["id"]})["items"]
            self.assertEqual(hit["href"], "/ontology/elements/" + rule["id"])
            self.assertEqual(self.get(client, "/api/v2/elements", q=rule["id"])["data"]["items"][0]["source_title"], "")
            from_card = self.get(client, detail)["data"]
            self.assertEqual(self.invoke(client, "element.get", {"entity_id": rule["id"]}), from_card)
            self.assertFalse(from_card["card"]["sources"])
            self.assertFalse(from_card["can_edit"])
            self.assertEqual(from_card["gates"], self.invoke(client, "quality.check_gates", {"entity_id": rule["id"]}))
            fields = {field["key"]: field for field in from_card["gates"]["fields"]}
            self.assertEqual(fields["scope"]["value"], "本机构签收事项")
            self.assertEqual(fields["subject:" + obj["id"]]["value"], obj["title"])
            self.assertEqual(fields["subject:" + obj["id"]]["href"], "/ontology/elements/" + obj["id"])
            self.assertIn(governed["id"], [r["id"] for r in from_card["entity"]["relations"]["outgoing"]])
            self.assertNotIn(private_source["id"], [r["id"] for r in from_card["entity"]["relations"]["outgoing"]])
            self.assertEqual(from_card["entity"]["_meta"]["card_text"], from_card["card"]["text"])
            governed_card = self.get(client, "/api/v2/elements/" + obj["id"])["data"]
            self.assertTrue(any(rule["title"] in r for r in governed_card["card"]["rules"]))
            description = self.business(
                client, "semantic.describe", {"entity_id": rule["id"], "include_structure": True}
            )
            self.assertEqual(description["card"], from_card["card"])
            view = json.dumps(
                [
                    from_card,
                    description,
                    governed_card,
                    self.business(client, "evidence.trace", {"entity_id": rule["id"]}),
                ],
                ensure_ascii=False,
            )
            for value in [*secret_values, misquoted, review_note]:
                self.assertNotIn(value, view)
        private = self.get(owner, detail)["data"]
        self.assertTrue(
            any(ref["anchor_id"] == anchor["anchor_id"] for ref in private["entity"]["payload"]["source_refs"])
        )
        self.assertTrue(private["versions"])
        self.assertTrue(private["gates"]["fields"])
        self.assertIn(private_source["id"], [r["id"] for r in private["entity"]["relations"]["outgoing"]])
        foreign = self.login("fixture.builder")
        self.assertEqual(foreign.get(detail).status_code, 404)
        readonly = replace(self.identity(admin), readonly=True)
        ctx = CapabilityContext(readonly, permissions=frozenset(permissions_for(self.identity(admin))))
        guarded = registry.invoke("element.get", ctx, {"entity_id": rule["id"]}).model_dump_json()
        for value in secret_values:
            self.assertNotIn(value, guarded)
        # Promote only this material through its actual submit / independent review flow.
        for client, action in ((owner, "submit"), (reviewer, "approve"), (reviewer, "publish")):
            self.receipt(
                client.post(
                    "/api/entities/" + source["id"] + "/action", json={"action": action, "reason": "核准公开原件"}
                )
            )
        for reader in (viewer, analyst, owner):
            public = self.get(reader, detail)["data"]
            (citation,) = public["entity"]["payload"]["source_refs"]
            (original,) = self.business(reader, "evidence.trace", {"entity_id": rule["id"]})["sources"]
            for field in ("material_id", "anchor_id", "source_version", "quote"):
                self.assertEqual(citation[field], original[field])
            self.assertEqual(
                self.get(reader, "/api/v2/elements", q=rule["id"])["data"]["items"][0]["source_title"], source["title"]
            )
            self.assertIn(private_source["id"], [r["id"] for r in public["entity"]["relations"]["outgoing"]])
            self.assertTrue(public["card"]["sources"])
            if reader in (viewer, analyst):
                self.assertNotIn(misquoted, json.dumps(public, ensure_ascii=False))
                self.assertFalse(public["entity"]["payload"]["article_sections"][0]["source_refs"])

        # A historical reference can carry locally resolvable ids while explicitly
        # naming another institution. Its cached citation has no local authority.
        with use_context(self.identity(owner)), closing(db.connect()) as conn, conn:
            conn.execute(
                "UPDATE entities SET payload=JSON_SET(payload,'$.source_refs[0].tenant_id',?) WHERE id=?",
                (self.identity(foreign).tenant_id, obj["id"]),
            )
        for reader in (viewer, analyst, owner):
            self.assertFalse(self.business(reader, "evidence.trace", {"entity_id": obj["id"]})["sources"])
            self.assertFalse(
                self.get(reader, "/api/v2/elements/" + obj["id"])["data"]["entity"]["payload"]["source_refs"]
            )

        # The delegated external application still sees only published entity metadata.
        response = self.receipt(
            admin.post(
                "/api/integrations/applications",
                json={
                    "name": "核对凭据查询",
                    "delegate_user_id": self.identity(owner).user_id,
                    "tool_scopes": ["ontology_explain"],
                },
            )
        )
        token = response["credential"]["token"]
        application = applications.authenticate_application(token, "public-card-proof")
        with use_context(application):
            result = dispatch_tool("ontology_explain", {"entity_id": rule["id"]}, "public-card-proof")
        self.assertEqual(result.data["entity"]["id"], rule["id"])
        for value in secret_values:
            self.assertNotIn(value, result.model_dump_json())
