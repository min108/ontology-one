"""A signed search hit opens a card with exactly the citations its reader can follow."""

import json
import os
import unittest
from contextlib import closing
from dataclasses import replace

from backend import db
from backend.api.v2.routes import registry
from backend.capabilities.context import CapabilityContext
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
