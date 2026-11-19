"""Historical passage deep links use the visible citing subject and its exact saved range."""

import os
import unittest
from contextlib import closing
from urllib.parse import parse_qsl, urlsplit

from fastapi.testclient import TestClient
from httpx import Response

from backend import db, provenance
from backend.core.errors import ApiError
from backend.kernel import gate_fields
from backend.security import applications, visibility
from backend.security.context import use_context
from tests.r4b import test_release_actions as release

TRACE = "evidence.trace"


@unittest.skipUnless(os.getenv("RUN_JOBS_MYSQL_TESTS") == "1", "requires isolated signed MySQL fixture")
class HistoricalPassagesHTTP(unittest.TestCase):
    setUpClass = classmethod(release.ReleaseActionsHTTP.setUpClass.__func__)
    login = release.ReleaseActionsHTTP.login
    identity = release.ReleaseActionsHTTP.identity
    invoke = release.ReleaseActionsHTTP.invoke
    business = release.ReleaseActionsHTTP.business
    get = release.ReleaseActionsHTTP.get
    receipt = release.ReleaseActionsHTTP.receipt
    rejected = release.ReleaseActionsHTTP.rejected
    evidence = release.ReleaseActionsHTTP.evidence
    publish_asset = release.ReleaseActionsHTTP.publish_asset
    asset = release.ReleaseActionsHTTP.asset

    def setUp(self) -> None:
        self.owner, self.reviewer, self.viewer = (
            self.login("b1." + role) for role in ("modeler", "reviewer", "viewer")
        )

    def publish_material(self, mid: str) -> None:
        for actor, action in ((self.owner, "submit"), (self.reviewer, "approve"), (self.reviewer, "publish")):
            result = self.receipt(
                actor.post(f"/api/entities/{mid}/action", json={"action": action, "reason": "核对原文"})
            )
        self.assertEqual(result["status"], "published")

    def narrow(self, whole: dict, text: str) -> dict:
        with use_context(self.identity(self.owner)), closing(db.connect()) as conn, conn:
            result = provenance.normalize_source_ref(
                conn, whole["material_id"], {"chunk_id": whole["chunk_id"], "quote": text}
            )
        self.assertNotEqual(result["anchor_id"], whole["anchor_id"])
        return result

    def new_version(self, whole: dict) -> str:
        # The same production version writer used on material/experience revisions.
        with use_context(self.identity(self.owner)), closing(db.connect()) as conn, conn:
            result = provenance.ensure_source_version(conn, whole["material_id"], "本次改版采用签字移交及复核记录。")
        self.assertNotEqual(result["id"], whole["source_version"])
        self.assertGreater(result["version"], 1)
        return result["id"]

    def target(self, ref: dict, **kwargs: str) -> dict:
        return {"anchor_id": ref["anchor_id"], "source_version": ref["source_version"], **kwargs}

    def passage(self, client: TestClient, mid: str, **params: str) -> Response:
        return client.get(f"/api/v2/elements/{mid}/passage", params=params)

    def open_trace_link(self, reader: TestClient, subject: dict, ref: dict) -> None:
        traced = self.business(reader, TRACE, subject)
        (source,) = traced["sources"]
        for key in ("material_id", "source_version", "anchor_id", "quote", "start", "end"):
            self.assertEqual(source[key], ref[key])
        url = urlsplit(source["href"])
        self.assertEqual(url.path, "/materials/" + ref["material_id"])
        params = dict(parse_qsl(url.query))
        params["anchor_id"] = params.pop("anchor")
        # Follow the actual href parameters through precisely the passage route the UI uses.
        resolved = self.receipt(self.passage(reader, ref["material_id"], **params))["sources"]
        self.assertEqual(
            [(a["quote"], a["start"], a["end"]) for a in resolved], [(ref["quote"], ref["start"], ref["end"])]
        )
        self.assertEqual(params, self.target(ref, **{"citing_" + key: val for key, val in subject.items()}))

    def assert_hidden(self, client: TestClient, mid: str, params: dict, quote: str) -> None:
        response = self.passage(client, mid, **params)
        self.rejected(response, 404, "NOT_FOUND")
        self.assertNotIn(quote, response.text)

    def propose(self, source: str, target: str, typ: str = "references_source") -> str:
        return self.invoke(
            self.owner, "relation.propose", {"source": source, "target": target, "type": typ, "reason": "核对材料出处"}
        )["id"]

    def decide(self, rid: str, status: str) -> None:
        self.invoke(self.reviewer, "relation.decide", {"relation_id": rid, "status": status, "reason": "独立逐字核对"})

    def test_published_entity_links_narrow_history_for_author_and_viewer(self) -> None:
        whole = self.evidence(self.owner, "旧版留存附注。办理前须核对交接凭据。收尾另附人事备注。")
        mid = whole["material_id"]
        bound = self.narrow(whole, "办理前须核对交接凭据。")
        asset = self.asset(self.owner, self.reviewer, "交接凭据的复核份数", bound)
        self.publish_material(mid)
        self.receipt(self.passage(self.viewer, mid, **self.target(whole)))
        self.new_version(whole)
        for reader in (self.viewer, self.owner):
            self.open_trace_link(reader, {"entity_id": asset["id"]}, bound)
            self.assert_hidden(reader, mid, self.target(bound), bound["quote"])
            self.assert_hidden(reader, mid, self.target(whole, citing_entity_id=asset["id"]), whole["quote"])
            self.assert_hidden(
                reader, mid, {"chunk_id": whole["chunk_id"], "citing_entity_id": asset["id"]}, whole["quote"]
            )
        self.rejected(
            self.passage(
                self.viewer, mid, **self.target(bound, citing_entity_id=asset["id"], citing_relation_id="rel-unrelated")
            ),
            422,
        )

    def test_confirmed_relation_link_rechecks_both_endpoints_and_actual_binding(self) -> None:
        whole = self.evidence(self.owner, "关系核对簿引言。接收人比对移交清册。备注书写在最后。")
        mid = whole["material_id"]
        bound = self.narrow(whole, "接收人比对移交清册。")
        asset = self.asset(self.owner, self.reviewer, "清册核对项数", bound)
        self.publish_material(mid)
        edge = self.propose(asset["id"], mid)
        unrelated = self.propose(asset["id"], mid, "related_to")
        self.decide(unrelated, "confirmed")
        self.new_version(whole)
        args = self.target(bound, citing_relation_id=edge)
        self.assert_hidden(self.viewer, mid, args, bound["quote"])
        self.decide(edge, "confirmed")
        for reader in (self.owner, self.viewer):
            self.open_trace_link(reader, {"relation_id": edge}, bound)
            self.assert_hidden(reader, mid, self.target(bound, citing_relation_id=unrelated), bound["quote"])
        self.decide(edge, "rejected")
        self.assert_hidden(self.viewer, mid, args, bound["quote"])
        self.assert_hidden(self.owner, mid, args, bound["quote"])

    def test_adopted_experience_relation_keeps_its_saved_quote_after_a_new_signed_turn(self) -> None:
        narrative = "经验交换由保管员逐份核对原签收清册，并由各方标注办结日期。"
        session = self.receipt(
            self.owner.post(
                "/api/experience/sessions",
                json={
                    "title": "经验交换的办理原话",
                    "context": narrative,
                    "mode": "structured",
                },
            )
        )["entity"]
        mid = session["id"]
        content = {
            "title": "交换清册核对",
            "summary": "逐份登记。",
            "content": narrative,
            "boundary": "仅用于纸质清册。",
            "questions": "",
        }
        draft = self.receipt(self.owner.post(f"/api/experience/{mid}/adopt", json=content))["entity"]
        ref = draft["payload"]["source_refs"][0]
        changed = self.invoke(
            self.owner,
            "element.update_draft",
            {
                "entity_id": draft["id"],
                "version": draft["version"],
                "payload": {
                    **draft["payload"],
                    "definition": "保管员逐份核对原签收清册。",
                    "conditions": "交换纸质清册时",
                    "boundary": content["boundary"],
                },
            },
        )["entity"]
        self.publish_asset(self.owner, self.reviewer, changed)
        updated = self.receipt(
            self.owner.post(
                f"/api/experience/{mid}/turns",
                json={
                    "content": "补充说明由接收组保留至事项办结。",
                    "dimension": "free",
                    "mode": "structured",
                },
            )
        )["entity"]
        self.assertNotEqual(updated["payload"]["source_version"], ref["source_version"])
        self.publish_material(mid)
        with use_context(self.identity(self.owner)), closing(db.connect()) as conn:
            row = conn.execute(
                "SELECT id,payload FROM relations WHERE source=? AND target=? AND type='derived_from'",
                (draft["id"], mid),
            ).fetchone()
            self.assertIn(ref["anchor_id"], row["payload"])
            rid = row["id"]
        self.open_trace_link(self.viewer, {"relation_id": rid}, ref)
        self.open_trace_link(self.viewer, {"entity_id": draft["id"]}, ref)
        self.assert_hidden(self.viewer, mid, self.target(ref), narrative)

    def test_wrong_material_version_citer_claim_tenant_and_revoked_entity(self) -> None:
        text = "遇交接异议需要保存办理原件及凭据。"
        ref, other = self.evidence(self.owner, text), self.evidence(self.owner, text)
        mid = ref["material_id"]
        asset = self.asset(self.owner, self.reviewer, "原件实收份数", ref)
        wrong_asset = self.asset(self.owner, self.reviewer, "另一份原件实收份数", other)
        ids_only = self.invoke(
            self.owner,
            "element.create_draft",
            {
                "kind": "knowledge",
                "title": "仅留索引的旧稿",
                "summary": "待办理",
                "payload": {
                    "source_refs": [
                        {key: ref[key] for key in ("material_id", "chunk_id", "anchor_id", "source_version")}
                    ]
                },
            },
        )["entity"]
        forged = self.invoke(
            self.owner,
            "element.create_draft",
            {
                "kind": "knowledge",
                "title": "旧稿中的失配引文",
                "summary": "核对原有引文",
                "payload": {
                    "source_refs": [
                        {**ref, "source_version": other["source_version"]},
                        {**ref, "material_id": other["material_id"]},
                        {**ref, "quote": "错误文字"},
                        {**ref, "chunk_id": other["chunk_id"]},
                        {**ref, "source_chunk_id": other["chunk_id"]},
                        {**ref, "source_id": other["material_id"]},
                        {**ref, "tenant_id": "dept-01"},
                        {**ref, "artifact_id": "ART-1234567890abcdef"},
                    ]
                },
            },
        )["entity"]
        self.publish_material(mid)
        self.publish_material(other["material_id"])
        self.open_trace_link(self.owner, {"entity_id": ids_only["id"]}, ref)
        newest = self.new_version(ref)
        args = self.target(ref, citing_entity_id=asset["id"])
        self.assertEqual(self.business(self.owner, TRACE, {"entity_id": ids_only["id"]})["sources"], [])
        self.assert_hidden(self.owner, mid, {**args, "citing_entity_id": ids_only["id"]}, text)
        self.assertEqual(self.business(self.owner, TRACE, {"entity_id": forged["id"]})["sources"], [])
        self.assert_hidden(self.owner, mid, {**args, "citing_entity_id": forged["id"]}, text)
        self.assert_hidden(self.viewer, mid, {**args, "citing_entity_id": wrong_asset["id"]}, text)
        self.assert_hidden(self.viewer, mid, {**args, "source_version": newest}, text)
        self.assert_hidden(self.viewer, mid, {**args, "chunk_id": other["chunk_id"]}, text)
        self.assert_hidden(self.viewer, other["material_id"], args, text)
        self.assert_hidden(self.login("fixture.builder"), mid, args, text)
        self.open_trace_link(self.viewer, {"entity_id": asset["id"]}, ref)
        self.receipt(
            self.reviewer.post(f"/api/entities/{asset['id']}/action", json={"action": "archive", "reason": "停止使用"})
        )
        self.assert_hidden(self.viewer, mid, args, text)
        self.assert_hidden(self.owner, mid, args, text)

    def test_governance_field_uses_current_visible_value_and_separately_published_material(self) -> None:
        ref = self.evidence(self.owner, "仓库签收由保管人逐笔复核。")
        unrelated = self.evidence(self.owner, "计数口径由系统逐笔记录。")
        asset = self.asset(self.owner, self.reviewer, "仓库签收件数", unrelated)
        # Field provenance is separate from payload.source_refs. Use the same trusted
        # recorder as extraction/adoption and leave the ordinary evidence pointed elsewhere.
        with use_context(self.identity(self.owner)), closing(db.connect()) as conn, conn:
            row = conn.execute("SELECT * FROM entities WHERE id=?", (asset["id"],)).fetchone()
            gate_fields.record(
                conn, db.entity_json(row), {"definition": {**ref, "source": "source", "source_quote": ref["quote"]}}
            )
        mid = ref["material_id"]
        self.new_version(ref)
        args = self.target(ref, citing_entity_id=asset["id"])
        self.assert_hidden(self.viewer, mid, args, ref["quote"])
        self.publish_material(mid)
        for reader in (self.owner, self.viewer):
            (source,) = self.receipt(self.passage(reader, mid, **args))["sources"]
            self.assertEqual(source["quote"], ref["quote"])
        revision = self.invoke(
            self.owner,
            "element.start_revision",
            {
                "entity_id": asset["id"],
                "version": asset["version"],
                "reason": "更换核对字段",
            },
        )["entity"]
        changed = self.invoke(
            self.owner,
            "element.update_draft",
            {
                "entity_id": asset["id"],
                "version": revision["version"],
                "payload": {**revision["payload"], "definition": "按入库回执登记的件数。"},
            },
        )["entity"]
        self.publish_asset(self.owner, self.reviewer, changed)
        self.assert_hidden(self.viewer, mid, args, ref["quote"])
        self.receipt(
            self.reviewer.post(f"/api/entities/{mid}/action", json={"action": "archive", "reason": "停止公开材料"})
        )
        self.assert_hidden(self.viewer, mid, args, ref["quote"])

    def test_external_application_and_metadata_grant_cannot_fetch_cited_text(self) -> None:
        ref = self.evidence(self.owner, "委托查阅限于卡片登记信息。")
        mid = ref["material_id"]
        asset = self.asset(self.owner, self.reviewer, "委托登记项数", ref)
        self.publish_material(mid)
        self.new_version(ref)
        args = self.target(ref, citing_entity_id=asset["id"])
        self.open_trace_link(self.viewer, {"entity_id": asset["id"]}, ref)
        admin = self.login("r2.other")
        registered = self.receipt(
            admin.post(
                "/api/integrations/applications",
                json={
                    "name": "查阅登记卡",
                    "delegate_user_id": self.identity(self.owner).user_id,
                    "tool_scopes": ["ontology_query"],
                },
            )
        )
        credential = applications.authenticate_application(registered["credential"]["token"], "historical-passage")
        self.assertEqual(credential.identity_kind, "application")
        from backend.kernel.semantic_evidence import readable_anchor

        for principal in (credential, self.identity(self.owner)):
            with use_context(principal), db.business_readonly(), closing(db.connect()) as conn:
                scope = visibility.scope_for(conn, metadata_only=principal.identity_kind == "session")
                self.assertTrue(scope.metadata_only)
                with self.assertRaises(ApiError) as exc:
                    readable_anchor(conn, scope, mid, **args)
                self.assertEqual(exc.exception.code, "NOT_FOUND")
