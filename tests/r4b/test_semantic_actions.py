"""BA6 and BA11 through signed HTTP sessions on isolated production MySQL."""

import json
import os
import unittest
from dataclasses import replace

from fastapi.testclient import TestClient

from backend.api.v2.routes import registry
from backend.capabilities.context import CapabilityContext
from backend.core.errors import ApiError
from backend.security.permissions import permissions_for
from tests.r2 import test_build as construction
from tests.r4b import test_release_actions as release_actions

SEARCH = "semantic.search"
RESOLVE = "semantic.resolve"
NEIGHBORS = "semantic.neighbors"
TRACE = "evidence.trace"
IMPACT = "impact.analyze"


@unittest.skipUnless(os.getenv("RUN_JOBS_MYSQL_TESTS") == "1", "opt-in semantic HTTP/MySQL actions")
class SemanticActionsHTTP(unittest.TestCase):
    setUpClass = classmethod(release_actions.ReleaseActionsHTTP.setUpClass.__func__)
    login = construction.BuildMySQL.login
    invoke = construction.BuildMySQL.invoke
    proposal = construction.BuildMySQL.proposal
    identity = release_actions.ReleaseActionsHTTP.identity
    get = release_actions.ReleaseActionsHTTP.get
    receipt = release_actions.ReleaseActionsHTTP.receipt
    business = release_actions.ReleaseActionsHTTP.business
    rejected = release_actions.ReleaseActionsHTTP.rejected
    evidence = release_actions.ReleaseActionsHTTP.evidence
    publish_asset = release_actions.ReleaseActionsHTTP.publish_asset
    scene_fixture = release_actions.ReleaseActionsHTTP.scene_fixture
    asset = release_actions.ReleaseActionsHTTP.asset

    def create(self, client: TestClient, kind: str, title: str, payload: dict) -> dict:
        return self.invoke(
            client, "element.create_draft", {"kind": kind, "title": title, "summary": title, "payload": payload}
        )["entity"]

    def test_BA11_semantic_readings_provenance_and_direction(self) -> None:
        owner = self.login("b1.modeler")
        support = self.evidence(owner)
        data = self.create(owner, "data", "支出登记行号", {"unit": "笔", "grain": "每次登记"})
        obj = self.create(
            owner,
            "object_type",
            "月末费用交接单",
            {
                "schema": "object_type/1",
                "aliases": ["待报销交接簿"],
                "properties": [{"key": "number", "name": "登记行号", "datatype": "integer", "data_id": data["id"]}],
                "identity": {"properties": ["number"]},
                "source_refs": [support],
            },
        )
        rule = self.create(
            owner,
            "rule",
            "费用交接完整性核验",
            {
                "judgement": {
                    "applies_to": {"object_type": obj["id"]},
                    "condition": {"oxl": "number > 0"},
                    "requirement": {"oxl": "number < 999"},
                },
                "source_refs": [support],
            },
        )
        process = self.create(
            owner,
            "process",
            "月末处理流程",
            {
                "schema": "process/1",
                "steps": [{"key": "check", "name": "交接前核对", "constraints": [{"rule_id": rule["id"]}]}],
            },
        )
        for text, expected in ((obj["title"], "title"), ("待报销交接簿", "alias"), (obj["id"], "id")):
            matches = self.business(owner, RESOLVE, {"text": text, "expected_kind": "object_type"})["items"]
            self.assertEqual(
                (matches[0]["id"], matches[0]["match"], matches[0]["confidence"]), (obj["id"], expected, "high")
            )
            self.assertFalse(self.business(owner, RESOLVE, {"text": text, "expected_kind": "position"})["items"])
        partial = self.business(owner, RESOLVE, {"text": "费用交接", "expected_kind": "object_type"})["items"]
        self.assertEqual([(i["id"], i["match"], i["confidence"]) for i in partial], [(obj["id"], "partial", "low")])
        prefix = self.business(owner, RESOLVE, {"text": "待报销", "expected_kind": "object_type"})["items"]
        self.assertEqual([(i["id"], i["match"], i["confidence"]) for i in prefix], [(obj["id"], "prefix", "medium")])
        # Written into a data definition, but it is neither a name nor a recorded alias.
        self.assertFalse(self.business(owner, RESOLVE, {"text": "每次登记", "expected_kind": "data"})["items"])
        first = self.business(owner, NEIGHBORS, {"entity_id": rule["id"], "depth": 1})
        second = self.business(owner, NEIGHBORS, {"entity_id": rule["id"], "depth": 2})
        self.assertEqual({r["id"] for r in first["nodes"]}, {rule["id"], obj["id"], process["id"]})
        self.assertEqual({r["id"] for r in second["nodes"]}, {rule["id"], obj["id"], data["id"], process["id"]})
        read = next(edge for edge in second["relations"] if edge["type"] == "has_property")
        self.assertEqual(
            (read["source"], read["target"], read["label"], read["reading"]),
            (obj["id"], data["id"], "具有数据项", obj["title"] + "具有数据项" + data["title"]),
        )
        family = self.business(owner, NEIGHBORS, {"entity_id": obj["id"], "depth": 2, "family": "structure"})
        self.assertEqual({e["id"] for e in family["nodes"]}, {obj["id"], data["id"]})
        self.rejected(
            owner.post("/api/v2/capabilities/semantic.neighbors:invoke", json={"entity_id": obj["id"], "depth": 3}), 422
        )
        impact = self.business(owner, IMPACT, {"entity_id": obj["id"]})
        self.assertEqual([i["id"] for i in impact["direct"]], [rule["id"]])
        self.assertEqual([i["id"] for i in impact["indirect"]], [process["id"]])
        self.assertEqual([e["type"] for e in impact["indirect"][0]["path"]], ["governs", "follows_rule"])
        self.assertFalse(impact["truncated"])
        traced = self.business(owner, TRACE, {"entity_id": rule["id"]})
        self.assertEqual((traced["subject_id"], traced["entity_version"]), (rule["id"], rule["version"]))
        (source,) = traced["sources"]
        for key in ("material_id", "source_version", "anchor_id", "start", "end", "quote"):
            self.assertEqual(source[key], support[key])
        original = self.get(
            owner, f"/api/materials/{support['material_id']}/source", source_version=support["source_version"]
        )
        self.assertEqual(source["version"], original["version"])
        self.assertEqual(source["quote"], original["text"][source["start"] : source["end"]])
        self.assertEqual(
            [s["id"] for s in source["path"]],
            [support["material_id"], support["source_version"], support["anchor_id"], rule["id"]],
        )
        self.assertIn(support["anchor_id"], source["href"])
        self.assertIn(support["source_version"], source["href"])

    def test_trace_ignores_rebound_anchors_and_reveals_a_quote_after_separate_source_publication(self) -> None:
        owner, reviewer, viewer = (self.login(n) for n in ("b1.modeler", "b1.reviewer", "b1.viewer"))
        bound, other = self.evidence(owner), self.evidence(owner)
        asset = self.asset(owner, reviewer, "双人签收登记", bound)
        self.assertFalse(self.business(viewer, TRACE, {"entity_id": asset["id"]})["sources"])
        for actor, action in ((owner, "submit"), (reviewer, "approve"), (reviewer, "publish")):
            value = self.receipt(
                actor.post(
                    "/api/entities/" + bound["material_id"] + "/action",
                    json={"action": action, "reason": "独立核对材料出处"},
                )
            )
        self.assertEqual(value["status"], "published")
        public = self.business(viewer, TRACE, {"entity_id": asset["id"]})
        self.assertEqual(
            [(r["anchor_id"], r["quote"]) for r in public["sources"]], [(bound["anchor_id"], bound["quote"])]
        )
        # Even two materials with byte-for-byte equal passages have different
        # immutable identities. Old saved claims can contain contradictory keys.
        forged_refs = [
            {**bound, "material_id": other["material_id"]},
            {**bound, "span_id": other["span_id"]},
            {**bound, "chunk_id": other["chunk_id"]},
            {**bound, "source_version": other["source_version"]},
            {**bound, "quote": "并非原文"},
            {**bound, "span": {"start": bound["start"] + 1, "end": bound["end"]}},
            {**bound, "kind": "note"},
        ]
        claim = self.create(owner, "knowledge", "旧稿中的错引", {"source_refs": forged_refs})
        self.assertEqual(self.business(owner, TRACE, {"entity_id": claim["id"]})["sources"], [])
        for bad_body in (
            {},
            {"entity_id": asset["id"], "receipt_id": "mixed"},
            {"entity_id": asset["id"], "actor": "forged"},
        ):
            self.rejected(
                owner.post("/api/v2/capabilities/evidence.trace:invoke", json=bad_body), 422, "VALIDATION_FAILED"
            )

    def test_trace_uses_real_recipe_adoption_and_preserves_unpublished_source_boundary(self) -> None:
        owner, reviewer, viewer = (self.login(n) for n in ("b1.modeler", "b1.reviewer", "b1.viewer"))
        proposal = self.proposal(owner)
        self.invoke(
            owner,
            "changeset.decide_items",
            {
                "changeset_id": proposal["id"],
                "item_ids": [item["id"] for item in proposal["items"]],
                "decision": "accepted",
            },
        )
        applied = self.invoke(owner, "changeset.apply_and_submit", {"changeset_id": proposal["id"]})
        ident = applied["entity_ids"][0]
        review = self.get(reviewer, "/api/v2/reviews/" + ident)["data"]
        self.receipt(
            reviewer.post(
                "/api/v2/reviews/" + ident + ":decide",
                json={"version": review["entity"]["version"], "decision": "approve_publish"},
            )
        )
        trace = self.business(owner, TRACE, {"entity_id": ident})
        self.assertTrue(trace["sources"])
        steps = trace["sources"][0]["path"]
        self.assertIn(proposal["id"], [s["id"] for s in steps if s["kind"] == "changeset"])
        self.assertIn("recipe", [s["kind"] for s in steps])
        self.assertIn("item", [s["kind"] for s in steps])
        graph = self.business(owner, NEIGHBORS, {"entity_id": ident})
        edge = next(r for r in graph["relations"] if r["type"] == "derived_from")
        self.assertTrue(edge["reading"].endswith(self.get(owner, "/api/entities/" + ident)["title"]))
        relation = self.business(owner, TRACE, {"relation_id": edge["id"]})
        self.assertEqual(relation["sources"][0]["anchor_id"], trace["sources"][0]["anchor_id"])
        hidden = self.business(viewer, TRACE, {"entity_id": ident})
        self.assertEqual(hidden["sources"], [])
        self.assertNotIn(trace["sources"][0]["source_version"], json.dumps(hidden, ensure_ascii=False))
        self.rejected(
            viewer.post("/api/v2/capabilities/evidence.trace:invoke", json={"relation_id": edge["id"]}),
            404,
            "NOT_FOUND",
        )

    def test_BA6_six_identities_all_five_reads_and_foreign_target(self) -> None:
        names = {
            "admin": "r2.other",
            "modeler": "b1.modeler",
            "reviewer": "b1.reviewer",
            "analyst": "b1.analyst",
            "viewer": "b1.viewer",
            "superadmin": "fixture.superadmin",
        }
        clients = {role: self.login(name) for role, name in names.items()}
        superadmin = clients["superadmin"]
        self.receipt(superadmin.post("/api/auth/workspace", json={"tenant_id": "sub-01"}))
        superadmin.headers["X-Workspace-Id"] = "sub-01"
        owner, reviewer = clients["modeler"], clients["reviewer"]
        evidence = self.evidence(owner)
        published = self.create(
            owner,
            "data",
            "签章交接金额",
            {"definition": "交接单的签章金额", "unit": "元", "grain": "每张单据", "source_refs": [evidence]},
        )
        published = self.publish_asset(owner, reviewer, published)
        hidden = self.create(owner, "knowledge", "保密校验批注", {})
        rel = self.invoke(
            owner,
            "relation.propose",
            {"source": published["id"], "target": hidden["id"], "type": "related_to", "reason": "交接核对"},
        )
        self.invoke(
            reviewer, "relation.decide", {"relation_id": rel["id"], "status": "confirmed", "reason": "已检查两端"}
        )
        bodies = {
            SEARCH: {"q": published["title"]},
            RESOLVE: {"text": published["id"]},
            NEIGHBORS: {"entity_id": published["id"], "depth": 2},
            TRACE: {"entity_id": published["id"]},
            IMPACT: {"entity_id": published["id"]},
        }
        foreign = self.login("fixture.builder")
        for role, client in clients.items():
            private = role in {"admin", "modeler", "reviewer", "superadmin"}
            for cap, body in bodies.items():
                url = "/api/v2/capabilities/" + cap + ":invoke"
                with self.subTest(role=role, cap=cap):
                    response = client.post(url, json=body)
                    if cap == IMPACT and not private:
                        self.rejected(response, 403, "PERMISSION_DENIED")
                    else:
                        value = self.receipt(response)
                        if cap in {SEARCH, RESOLVE}:
                            self.assertEqual(value["items"][0]["id"], published["id"])
                        if cap == NEIGHBORS:
                            self.assertEqual(hidden["id"] in {n["id"] for n in value["nodes"]}, private)
                            self.assertEqual(rel["id"] in {e["id"] for e in value["relations"]}, private)
                        if cap == TRACE:
                            self.assertEqual(bool(value["sources"]), private)
                        if not private:
                            self.assertNotIn(hidden["id"], response.text)
                            self.assertNotIn(evidence["anchor_id"], response.text)
                    self.rejected(client.post(url, json={**body, "tenant_id": "dept-01"}), 404, "NOT_FOUND")
            self.assertEqual(bool(self.business(client, RESOLVE, {"text": hidden["id"]})["items"]), private)
        for cap, body in bodies.items():
            response = foreign.post("/api/v2/capabilities/" + cap + ":invoke", json=body)
            if cap in {SEARCH, RESOLVE}:
                self.assertNotIn(published["id"], [i["id"] for i in self.receipt(response)["items"]])
            else:
                self.rejected(response, 404, "NOT_FOUND")
        person = self.identity(clients["admin"])
        for mode in ("rest", "mcp"):
            ctx = CapabilityContext(
                replace(person, readonly=(mode == "rest")), permissions=frozenset(permissions_for(person)), binding=mode
            )
            for cap, body in bodies.items():
                with self.subTest(binding=mode, cap=cap):
                    if cap == IMPACT:
                        with self.assertRaises(ApiError) as denied:
                            registry.invoke(cap, ctx, body)
                        self.assertEqual(denied.exception.code, "PERMISSION_DENIED")
                    else:
                        data = registry.invoke(cap, ctx, body).model_dump_json()
                        self.assertNotIn(hidden["id"], data)
                        self.assertNotIn(evidence["anchor_id"], data)

    def test_impact_reports_only_institution_dependencies_registered_for_the_source(self) -> None:
        admin, owner, reviewer = (self.login(n) for n in ("r2.other", "b1.modeler", "b1.reviewer"))
        evidence = self.evidence(owner)
        source, consumer = (self.asset(owner, reviewer, name, evidence) for name in ("岗位核对份数", "交接凭据数量"))
        relation = self.invoke(
            owner,
            "relation.propose",
            {"source": consumer["id"], "target": source["id"], "type": "references_data", "reason": "核对时引用该口径"},
        )
        self.invoke(
            reviewer, "relation.decide", {"relation_id": relation["id"], "status": "confirmed", "reason": "已核对"}
        )
        scene = self.scene_fixture(admin, [consumer["id"]])
        before = self.business(owner, IMPACT, {"entity_id": source["id"]})
        self.assertNotIn(scene, [part["id"] for part in before["dependent_modules"]])
        self.business(admin, "module.save", {"module_id": scene, "register_dependencies": True})
        after = self.business(owner, IMPACT, {"entity_id": source["id"]})
        self.assertIn({"id": scene, "name": "交接档案核对", "min_version": None}, after["dependent_modules"])
        self.assertIn(consumer["id"], [node["id"] for node in after["direct"]])
        upstream = self.business(owner, IMPACT, {"entity_id": consumer["id"]})
        self.assertNotIn(source["id"], [node["id"] for node in upstream["direct"]])
        self.assertFalse(upstream["dependent_modules"])

    def test_numeric_query_receipt_rechecks_actor_tenant_published_mapping_and_revoked_source_grant(self) -> None:
        admin, builder, reviewer = (self.login("fixture." + role) for role in ("admin", "builder", "reviewer"))
        # The old fixture already contains a locally executed demo ledger. Publish its
        # historical draft mapping and run the regular source query with a scoped actor.
        source_id = "DEMO07-SOURCE-LEDGER"
        base = "/api/sources/" + source_id
        grants = {role: self.identity(actor).user_id for role, actor in (("builder", builder), ("reviewer", reviewer))}
        for user_id in grants.values():
            self.receipt(admin.put(base + "/grants", json={"user_id": user_id, "discover": True, "query": True}))
        mapping = next(m for m in self.get(admin, base)["mappings"] if m["status"] == "draft")
        path = base + "/mappings/" + mapping["id"]
        for actor, transition in ((builder, "submit"), (reviewer, "publish")):
            mapping = self.receipt(
                actor.post(
                    path + "/action",
                    json={
                        "action": transition,
                        "expected_version": mapping["version"],
                        "reason": "核对台账结构与原登记口径",
                    },
                )
            )
        query = builder.post(path + "/query", json={"limit": 2})
        result = self.receipt(query)
        self.assertTrue(result["rows"])
        receipt = query.headers["X-Request-Id"]
        expected = {
            "source_id": source_id,
            "mapping_id": mapping["id"],
            "mapping_version": result["mapping_version"],
            "source_version": 1,
            "schema_hash": result["schema_hash"],
            "row_count": len(result["rows"]),
        }
        self.assertEqual(self.business(builder, TRACE, {"receipt_id": receipt})["query"], expected)
        url = "/api/v2/capabilities/evidence.trace:invoke"
        for different in (reviewer, admin, self.login("b1.modeler")):
            self.rejected(different.post(url, json={"receipt_id": receipt}), 404, "NOT_FOUND")
        self.rejected(builder.post(url, json={"receipt_id": "no-such-receipt"}), 404, "NOT_FOUND")
        user_id = grants["builder"]
        self.receipt(admin.put(base + "/grants", json={"user_id": user_id, "discover": True, "query": False}))
        self.rejected(builder.post(url, json={"receipt_id": receipt}), 404, "NOT_FOUND")
        self.receipt(admin.put(base + "/grants", json={"user_id": user_id, "discover": True, "query": True}))
        self.assertEqual(self.business(builder, TRACE, {"receipt_id": receipt})["query"], expected)
        self.receipt(
            builder.post(
                path + "/action",
                json={"action": "revise", "expected_version": mapping["version"], "reason": "修订台账说明"},
            )
        )
        self.rejected(builder.post(url, json={"receipt_id": receipt}), 404, "NOT_FOUND")
