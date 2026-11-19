"""Published originals are readable in bounded pages through the signed semantic gateway."""

import json
import os
import unittest
from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient

from backend import db, main, provenance
from backend.mcp_tools import dispatch_tool
from backend.security import applications
from backend.security.context import use_context
from tests.r4b import test_semantic_actions as semantic


@unittest.skipUnless(os.getenv("RUN_JOBS_MYSQL_TESTS") == "1", "signed original material reads on MySQL")
class MaterialReaderHTTP(unittest.TestCase):
    setUpClass = classmethod(semantic.SemanticActionsHTTP.setUpClass.__func__)
    login = semantic.SemanticActionsHTTP.login
    get = semantic.SemanticActionsHTTP.get
    receipt = semantic.SemanticActionsHTTP.receipt
    identity = semantic.SemanticActionsHTTP.identity
    business = semantic.SemanticActionsHTTP.business
    evidence = semantic.SemanticActionsHTTP.evidence
    invoke = semantic.SemanticActionsHTTP.invoke
    publish_asset = semantic.SemanticActionsHTTP.publish_asset
    asset = semantic.SemanticActionsHTTP.asset
    rejected = semantic.SemanticActionsHTTP.rejected

    def upload(self, client: TestClient, filename: str, text: str) -> str:
        return self.receipt(
            client.post("/api/materials/upload", files={"file": (filename, text.encode(), "text/plain")})
        )["id"]

    def publish(self, builder: TestClient, reviewer: TestClient, ident: str) -> None:
        for actor, action in ((builder, "submit"), (reviewer, "approve"), (reviewer, "publish")):
            self.receipt(
                actor.post(
                    "/api/entities/" + ident + "/action", json={"action": action, "reason": "核对并公开本份原件"}
                )
            )

    def test_signed_whole_original_pages_and_role_boundaries(self) -> None:
        builder, reviewer, analyst, viewer = (
            self.login("b1." + role) for role in ("modeler", "reviewer", "analyst", "viewer")
        )
        foreign = self.login("fixture.builder")
        short = "原文" + "a" * 72 + "😀"
        self.assertEqual(len(short.encode()), 82)
        ident = self.upload(builder, "原件收讫.txt", short)
        path = "/api/v2/materials/" + ident + "/source"
        self.assertEqual(self.get(builder, path)["text"], short)
        for reader in (viewer, analyst, foreign):
            self.rejected(reader.get(path), 404, "NOT_FOUND")
        self.publish(builder, reviewer, ident)
        decoded = self.get(viewer, path)
        self.assertEqual(
            set(decoded),
            {"material_id", "title", "status", "source_version", "version", "locator", "text", "offset", "total"},
        )
        self.assertEqual(
            (decoded["material_id"], decoded["title"], decoded["status"], decoded["version"]),
            (ident, "原件收讫", "published", 1),
        )
        self.assertEqual((decoded["text"], decoded["offset"], decoded["total"]), (short, 0, len(short)))
        self.assertEqual(decoded, self.get(analyst, path, source_version=decoded["source_version"]))
        self.assertEqual(
            decoded,
            self.business(viewer, "evidence.trace", {"entity_id": ident, "source_page": {"offset": 0}})["source_page"],
        )
        self.rejected(foreign.get(path), 404, "NOT_FOUND")
        self.rejected(viewer.get("/api/materials/" + ident + "/source"), 403)
        self.rejected(viewer.get(path, params={"source_version": "an-unbound-version"}), 404, "NOT_FOUND")
        self.rejected(viewer.get(path, params={"tenant_id": self.identity(foreign).tenant_id}), 422)
        self.rejected(viewer.get(path, params={"offset": -1}), 422)
        self.rejected(viewer.get(path, params={"offset": len(short) + 1}), 422)
        self.assertEqual(self.get(viewer, path, offset=len(short))["text"], "")

        large = "首段\n" + "仓😀e\u0301原件" * 20600 + "\n唯一末尾𠀀"
        self.assertGreater(len(large.encode()), 285 * 1024)
        mid = self.upload(builder, "整卷交接记录.txt", large)
        self.publish(builder, reviewer, mid)
        whole = "/api/v2/materials/" + mid + "/source"
        admin = self.login("r2.other")
        self.assertEqual(self.get(admin, whole)["text"], large[:4096])
        legacy_path = "/api/materials/" + mid + "/source"
        original = self.get(builder, legacy_path)
        self.assertEqual(original, self.get(admin, legacy_path))
        self.assertEqual(original["text"], large)
        self.assertEqual(original["version"], 1)
        pages = []
        for start in range(0, len(large), 4096):
            page = self.get(viewer, whole, offset=start, source_version=original["source_version"])
            self.assertEqual((page["offset"], page["total"]), (start, len(large)))
            self.assertEqual(page["text"], large[start : start + 4096])
            pages.append(page["text"])
        self.assertEqual("".join(pages), original["text"])
        for client in (viewer, admin, builder):
            detail = self.get(client, "/api/v2/elements/" + mid)["data"]
            self.assertNotIn("original_text", detail["entity"]["payload"])
            self.assertNotIn(large, json.dumps(detail, ensure_ascii=False))
        self.receipt(reviewer.post("/api/entities/" + mid + "/action", json={"action": "archive"}))
        self.rejected(viewer.get(whole, params={"offset": 4096, "source_version": original["source_version"]}), 404)

    def test_reading_saved_legacy_text_and_tenant_local_identifiers(self) -> None:
        reader, other, reviewer = self.login("b1.viewer"), self.login("fixture.builder"), self.login("b1.reviewer")
        owner = self.login("b1.modeler")
        fixture = json.loads((Path(__file__).resolve().parents[1] / "fixtures/v070/dept-01.json").read_text())
        source = fixture["tables"]["source_versions"][0]
        migrated = self.get(other, "/api/v2/materials/" + source["material_id"] + "/source")
        self.assertEqual(
            (migrated["source_version"], migrated["version"], migrated["text"]),
            (source["id"], source["version"], source["text"]),
        )
        for actor, original in ((owner, source["text"]), (other, "另一家机构的电子归档原件")):
            self.receipt(
                actor.post(
                    "/api/entities",
                    json={
                        "id": "legacy-original",
                        "kind": "material",
                        "title": "历史原件",
                        "payload": {"original_text": original, "original_text_before_v3": "未公开的旧稿"},
                    },
                )
            )
        self.publish(owner, reviewer, "legacy-original")
        path = "/api/v2/materials/legacy-original/source"
        first = self.get(reader, path)
        self.assertEqual(first["text"], source["text"][:4096])
        self.assertIsNone(first["version"])
        self.assertEqual(first["total"], len(source["text"]))
        self.assertEqual(self.get(reader, path, source_version=first["source_version"]), first)
        self.assertEqual(self.get(other, path)["text"], "另一家机构的电子归档原件")
        self.rejected(other.get(path, params={"source_version": first["source_version"]}), 404)

    def test_reading_unicode_offsets_inside_a_verified_current_passage(self) -> None:
        builder, reviewer, reader = (self.login("b1." + role) for role in ("modeler", "reviewer", "viewer"))
        anchor = self.evidence(builder)
        self.publish(builder, reviewer, anchor["material_id"])
        path = "/api/v2/materials/" + anchor["material_id"] + "/source"
        selectors = {k: anchor[k] for k in ("chunk_id", "anchor_id", "source_version")}
        selected = self.get(reader, path, **selectors, offset=3)
        self.assertEqual(
            (selected["text"], selected["offset"], selected["total"]), (anchor["quote"][3:], 3, len(anchor["quote"]))
        )
        self.assertEqual(selected["locator"], anchor["locator"])
        self.rejected(reader.get(path, params={**selectors, "source_version": "wrong"}), 404)
        self.rejected(reader.get(path, params={**selectors, "offset": len(anchor["quote"]) + 1}), 422)

    def test_historical_citation_pages_stop_at_the_end_of_the_saved_excerpt(self) -> None:
        owner, reviewer, reader = (self.login("b1." + role) for role in ("modeler", "reviewer", "viewer"))
        selected = "仓😀e\u0301库交接必须逐项核验。"
        whole = self.evidence(owner, "历史原件的私有前记。" + selected + "另纸备注不能随该引文打开。")
        mid = whole["material_id"]
        with use_context(self.identity(owner)), closing(db.connect()) as conn, conn:
            anchor = provenance.normalize_source_ref(conn, mid, {"chunk_id": whole["chunk_id"], "quote": selected})
        cited = self.asset(owner, reviewer, "归档交接的签收份数", anchor)
        self.publish(owner, reviewer, mid)
        current = "现行原件采用新式交接登记簿。"
        with use_context(self.identity(owner)), closing(db.connect()) as conn, conn:
            provenance.ensure_source_version(conn, mid, current)
        path = "/api/v2/materials/" + mid + "/source"
        self.assertEqual(self.get(reader, path)["text"], current)
        selectors = {k: anchor[k] for k in ("anchor_id", "source_version")}
        self.rejected(reader.get(path, params=selectors), 404)
        self.rejected(reader.get(path, params={"source_version": anchor["source_version"]}), 404)
        selectors["citing_entity_id"] = cited["id"]
        page = self.get(reader, path, **selectors, offset=1)
        self.assertEqual((page["offset"], page["total"], page["text"]), (1, len(selected), selected[1:]))
        self.rejected(
            reader.get(path, params={**selectors, "chunk_id": whole["chunk_id"], "anchor_id": whole["anchor_id"]}), 404
        )
        self.rejected(reader.get(path, params={**selectors, "offset": len(selected) + 1}), 422)

    def test_registered_application_retains_metadata_only_access(self) -> None:
        owner, reviewer, admin = self.login("b1.modeler"), self.login("b1.reviewer"), self.login("r2.other")
        anchor = self.evidence(owner)
        asset = self.asset(owner, reviewer, "应用目录的交接记录", anchor)
        self.publish(owner, reviewer, anchor["material_id"])
        token = self.receipt(
            admin.post(
                "/api/integrations/applications",
                json={
                    "name": "材料公开范围核对",
                    "delegate_user_id": self.identity(owner).user_id,
                    "tool_scopes": ["ontology_discover"],
                },
            )
        )["credential"]["token"]
        external = TestClient(main.app)
        self.addCleanup(external.close)
        response = external.get(
            "/api/v2/materials/" + anchor["material_id"] + "/source", headers={"Authorization": "Bearer " + token}
        )
        self.rejected(response, 401)
        application = applications.authenticate_application(token, "material-publication-proof")
        with use_context(application):
            metadata = dispatch_tool("ontology_discover", {"query": asset["id"]}, "material-publication-proof")
            discovered = dispatch_tool(
                "ontology_discover", {"query": anchor["material_id"]}, "material-publication-proof"
            )
        self.assertEqual([e["id"] for e in metadata.data["entities"]], [asset["id"]])
        self.assertFalse(discovered.data["entities"])
        self.assertNotIn(anchor["quote"], metadata.model_dump_json())
