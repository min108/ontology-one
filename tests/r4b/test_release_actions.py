"""BA2-BA6 through authenticated HTTP and independent reads of the tenant's frozen receipts."""

import hashlib
import json
import os
import secrets
import unittest
from contextlib import closing
from dataclasses import replace

from fastapi.testclient import TestClient
from httpx import Response

from backend import db
from backend.api.v2.routes import registry
from backend.capabilities.context import CapabilityContext
from backend.core.errors import ApiError
from backend.security import store
from backend.security.context import Principal, use_context
from backend.security.permissions import permissions_for
from tests.r2 import test_build as construction
from tests.r4b.release_worker_actions import ReleaseWorkerActions

COMMON = "MOD-DEFAULT"
WRITERS = {
    "module.save",
    "module.move_elements",
    "release.create_draft",
    "release.submit",
    "release.publish",
    "release.withdraw",
}


@unittest.skipUnless(os.getenv("RUN_JOBS_MYSQL_TESTS") == "1", "opt-in B1 release HTTP/MySQL actions")
class ReleaseActionsHTTP(ReleaseWorkerActions, unittest.TestCase):
    login = construction.BuildMySQL.login
    invoke = construction.BuildMySQL.invoke

    @classmethod
    def setUpClass(cls) -> None:
        construction.BuildMySQL.setUpClass.__func__(cls)
        cls.addClassCleanup(construction.BuildMySQL.tearDownClass.__func__, cls)
        token = store.login("fixture.superadmin", "SyntheticFixture9!", "127.0.0.1", "release-fixture")
        admin = store.authenticate_session(token, "release-fixture")[0]
        for name, role in (
            ("b1.modeler", "modeler"),
            ("b1.nonowner", "modeler"),
            ("b1.reviewer", "reviewer"),
            ("b1.analyst", "analyst"),
            ("b1.viewer", "viewer"),
        ):
            legacy_account = role in {"analyst", "viewer"}
            ident = store.create_user(
                admin,
                username=name,
                display_name=name,
                tenant_id="sub-01",
                role="modeler" if legacy_account else role,
                password="SyntheticFixture9!",
                must_change_password=False,
            )
            if legacy_account:
                # Account administration still offers three roles. These two control
                # fixtures represent pre-provisioned accounts, before their first login.
                with store.control_connection() as conn, conn.cursor() as cursor:
                    cursor.execute("UPDATE sec_users SET role=%s WHERE id=%s", (role, ident))

    def identity(self, client: TestClient) -> Principal:
        return store.authenticate_session(client.cookies.get(store.COOKIE_NAME), "b1-action")[0]

    def receipt(self, response: Response) -> dict:
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def get(self, client: TestClient, path: str, **params: object) -> dict:
        return self.receipt(client.get(path, params=params))

    def business(self, client: TestClient, capability: str, body: dict) -> dict:
        return self.receipt(client.post(f"/api/v2/capabilities/{capability}:invoke", json=body))

    def rejected(self, response: Response, status: int, code: str = "") -> None:
        self.assertEqual(response.status_code, status, response.text)
        if code:
            self.assertEqual(response.json()["error"]["code"], code, response.text)

    def evidence(self, client: TestClient) -> dict:
        text = "移交时按份登记交接单和交接清册，档案接收人与经办人分别核对。"
        upload = self.receipt(
            client.post("/api/materials/upload", files={"file": ("交接登记说明.txt", text.encode(), "text/plain")})
        )
        path = "/api/materials/" + upload["id"]
        preview = self.receipt(client.post(path + "/segment-preview", json={"mode": "default"}))
        self.receipt(client.post(path + "/segment-confirm", json={"preview_id": preview["id"]}))
        chunks = self.get(client, path + "/chunks")["items"]
        (chunk,) = chunks
        focus = self.get(client, path + "/source", chunk_id=chunk["id"])["focus"]
        self.assertEqual(focus["quote"], text)
        self.assertTrue(focus["anchor_id"])
        return focus

    def publish_asset(self, builder: TestClient, reviewer: TestClient, draft: dict) -> dict:
        submitted = self.invoke(
            builder,
            "element.submit_draft",
            {"entity_id": draft["id"], "version": draft["version"], "reason": "送审核对"},
        )
        self.assertEqual(submitted["entity"]["status"], "review")
        self.assertTrue(submitted["gates"]["passed"], submitted["gates"])
        published = self.receipt(
            reviewer.post(
                "/api/v2/reviews/" + draft["id"] + ":decide",
                json={"version": submitted["entity"]["version"], "decision": "approve_publish"},
            )
        )["data"]["entity"]
        self.assertEqual(published["status"], "published")
        return published

    def asset(self, builder: TestClient, reviewer: TestClient, title: str, evidence: dict) -> dict:
        draft = self.invoke(
            builder,
            "element.create_draft",
            {
                "kind": "data",
                "title": title,
                "summary": "按交接清册登记。",
                "payload": {
                    "definition": title + "的逐笔登记数",
                    "unit": "份",
                    "grain": "每次交接",
                    "source_refs": [evidence],
                },
            },
        )["entity"]
        return self.publish_asset(builder, reviewer, draft)

    def detail(self, client: TestClient, release_id: str) -> dict:
        return self.get(client, "/api/v2/releases/" + release_id)

    def frozen(self, client: TestClient, release_id: str) -> dict:
        with use_context(self.identity(client)), closing(db.connect()) as conn:
            return {
                row["entity_id"]: {**dict(row), "snapshot": json.loads(row["snapshot"])}
                for row in conn.execute(
                    "SELECT entity_id,entity_version,kind,snapshot,content_hash,item_role FROM ont_release_items "
                    "WHERE release_id=? ORDER BY entity_id",
                    (release_id,),
                )
            }

    def freeze(self, client: TestClient, candidate: dict) -> dict:
        self.assertEqual(self.frozen(client, candidate["id"]), {})
        published = self.business(client, "release.publish", {"release_id": candidate["id"]})
        stored = self.frozen(client, candidate["id"])
        items = {item["entity_id"]: item for item in self.detail(client, candidate["id"])["items"]}
        self.assertEqual(stored, items)
        manifest = hashlib.sha256("".join(stored[key]["content_hash"] for key in sorted(stored)).encode()).hexdigest()
        self.assertEqual(published["manifest_hash"], manifest)
        self.assertEqual(published["status"], "published")
        self.assertEqual(published["reviewed_by"], self.identity(client).user_id)
        self.assertEqual(self.get(client, f"/api/v2/modules/{COMMON}")["current_release_id"], candidate["id"])
        return published

    def release(self, owner: TestClient, reviewer: TestClient, draft: dict) -> dict:
        submitted = self.business(owner, "release.submit", {"release_id": draft["id"]})
        self.assertEqual(submitted["status"], "candidate")
        self.assertEqual(submitted["submitted_by"], self.identity(owner).user_id)
        return self.freeze(reviewer, submitted)

    def test_common_publication_versions_and_withdrawal(self) -> None:
        admin, owner, reviewer = (self.login("fixture." + name) for name in ("admin", "builder", "reviewer"))
        self.business(admin, "module.save", {"module_id": COMMON, "owner_user_id": self.identity(owner).user_id})
        evidence = self.evidence(owner)
        asset = self.asset(owner, reviewer, "交接单份数", evidence)
        catalog = self.get(admin, "/api/entities", asset_only=True, status="published", page_size=200)
        expected_members = {row["id"] for row in catalog["items"]}
        self.assertEqual(len(expected_members), catalog["total"])
        first = self.business(admin, "release.create_draft", {"module_id": COMMON})
        self.assertEqual((first["version"], first["suggested_version"], first["status"]), ("1.0.0", "1.0.0", "draft"))
        self.assertEqual(
            {item["entity_id"] for item in first["items"] if item["item_role"] == "member"}, expected_members
        )
        self.assertEqual(set(first["diff"]["added"]), expected_members)
        self.assertEqual(first["member_count"], len(expected_members))
        self.assertEqual({row["entity_id"] for row in first["gates"]}, expected_members)
        self.assertIn("FIXTURE-KNOWLEDGE-001", expected_members)
        for report in first["gates"]:
            actual = self.invoke(admin, "quality.check_gates", {"entity_id": report["entity_id"]})
            self.assertEqual(report["passed"], actual["passed"], report["entity_id"])
            self.assertEqual(
                {c["code"]: c["status"] for c in report["checks"]}, {c["code"]: c["status"] for c in actual["checks"]}
            )
        self.assertTrue(any(not row["passed"] for row in first["gates"]), "Historical gate failures must stay visible")
        self.assertFalse(any(row["blocking"] for row in first["gates"]))
        candidate = self.business(owner, "release.submit", {"release_id": first["id"]})
        self.rejected(
            admin.post("/api/v2/capabilities/release.publish:invoke", json={"release_id": first["id"]}),
            403,
            "SEPARATION_OF_DUTIES",
        )
        self.assertEqual(self.detail(reviewer, first["id"])["status"], "candidate")
        first = self.freeze(reviewer, candidate)
        first_items = self.frozen(reviewer, first["id"])
        self.assertEqual(
            {key for key, value in first_items.items() if value["item_role"] == "member"}, expected_members
        )
        for row in catalog["items"]:
            self.assertEqual(first_items[row["id"]]["entity_version"], row["version"])
            self.assertEqual(first_items[row["id"]]["snapshot"]["title"], row["title"])
        self.assertEqual(first_items[asset["id"]]["snapshot"]["summary"], asset["summary"])
        self.assertEqual(first["member_count"], len(expected_members))
        repeated = self.business(owner, "release.create_draft", {"module_id": COMMON})
        self.assertTrue(repeated["no_changes"])
        self.assertEqual((repeated["id"], repeated["manifest_hash"]), (first["id"], first["manifest_hash"]))
        self.assertEqual(self.get(admin, f"/api/v2/modules/{COMMON}/releases")["total"], 1)

        revision = self.invoke(
            owner,
            "element.start_revision",
            {"entity_id": asset["id"], "version": asset["version"], "reason": "修订登记说明"},
        )["entity"]
        changed = self.invoke(
            owner,
            "element.update_draft",
            {"entity_id": asset["id"], "version": revision["version"], "summary": "按签收后的交接清册登记。"},
        )["entity"]
        self.publish_asset(owner, reviewer, changed)
        patch = self.business(owner, "release.create_draft", {"module_id": COMMON})
        self.assertEqual(
            (patch["bump"], patch["suggested_version"], patch["diff"]["changed"]), ("patch", "1.0.1", [asset["id"]])
        )
        patch = self.release(owner, reviewer, patch)
        self.assertNotEqual(patch["manifest_hash"], first["manifest_hash"])
        self.assertEqual(self.frozen(reviewer, first["id"]), first_items)
        self.assertEqual(self.frozen(reviewer, patch["id"])[asset["id"]]["snapshot"]["summary"], changed["summary"])

        extra = self.asset(owner, reviewer, "签收清册份数", evidence)
        minor = self.business(owner, "release.create_draft", {"module_id": COMMON})
        self.assertEqual(
            (minor["bump"], minor["suggested_version"], minor["diff"]["added"]), ("minor", "1.1.0", [extra["id"]])
        )
        minor = self.release(owner, reviewer, minor)
        archived = self.receipt(
            reviewer.post(
                f"/api/entities/{extra['id']}/action",
                json={"action": "archive", "expected_version": extra["version"], "reason": "清册停止使用"},
            )
        )
        self.assertEqual(archived["status"], "archived")
        major = self.business(owner, "release.create_draft", {"module_id": COMMON})
        self.assertEqual(
            (major["bump"], major["suggested_version"], major["diff"]["removed"]), ("major", "2.0.0", [extra["id"]])
        )
        down = owner.post(
            "/api/v2/capabilities/release.submit:invoke", json={"release_id": major["id"], "version": "1.9.9"}
        )
        self.rejected(down, 422, "VALIDATION_FAILED")
        self.assertEqual(self.detail(owner, major["id"])["status"], "draft")
        major = self.release(owner, reviewer, major)
        self.assertEqual(major["version"], "2.0.0")
        self.assertNotIn(extra["id"], self.frozen(reviewer, major["id"]))
        self.assertIn(extra["id"], self.frozen(reviewer, minor["id"]))

        added = self.asset(owner, reviewer, "移交附件份数", evidence)
        later = self.business(owner, "release.create_draft", {"module_id": COMMON})
        self.assertEqual((later["suggested_version"], later["diff"]["added"]), ("2.1.0", [added["id"]]))
        raised = self.business(owner, "release.submit", {"release_id": later["id"], "version": "3.0.0"})
        self.assertEqual(raised["version"], "3.0.0")
        final = self.freeze(reviewer, raised)
        frozen = self.frozen(reviewer, final["id"])
        path = f"/api/v2/releases/{final['id']}/items/{asset['id']}"
        self.rejected(admin.patch(path, json={"snapshot": {"title": "未经审核的题名"}}), 403)
        self.rejected(admin.post("/api/v2/capabilities/release.submit:invoke", json={"release_id": final["id"]}), 409)
        self.assertEqual(self.frozen(reviewer, final["id"]), frozen)
        withdrawn = self.business(reviewer, "release.withdraw", {"release_id": final["id"]})
        self.assertEqual((withdrawn["status"], withdrawn["current_release_id"]), ("withdrawn", major["id"]))
        self.assertEqual(self.get(admin, f"/api/v2/modules/{COMMON}")["current_release_id"], major["id"])
        historical = self.detail(reviewer, final["id"])
        self.assertEqual((historical["status"], historical["manifest_hash"]), ("withdrawn", final["manifest_hash"]))
        self.assertEqual({item["entity_id"]: item for item in historical["items"]}, frozen)
        self.assertEqual(self.frozen(reviewer, first["id"]), first_items)

    def scene_fixture(self, admin: TestClient, members: list[str]) -> str:
        # D19: scenario creation and capture are a later work package. The fixture
        # assigns only existing, normally published assets to their initial scene.
        key = secrets.token_hex(4)
        scene = "MOD-" + key
        person = self.identity(admin)
        with use_context(person), closing(db.connect()) as conn, conn:
            conn.execute(
                "INSERT INTO ont_modules(id,module_key,module_type,name,domain_code,created_by,created_at,updated_at) "
                "VALUES(?,?,'scenario',?,'general',?,?,?)",
                (scene, "scn_" + key, "交接档案核对", person.user_id, db.now(), db.now()),
            )
            for ident in members:
                count = conn.execute(
                    "UPDATE entity_meta SET module_id=? WHERE entity_id=? AND module_id=?",
                    (scene, ident, COMMON),
                ).rowcount
                self.assertEqual(count, 1)
        return scene

    def historical_scene(self, admin: TestClient, reviewer: TestClient, draft: dict) -> None:
        # A pre-existing launched scenario is the BA5 input. All hashes, versions,
        # snapshots and roles here come from its real first draft, before promotion.
        with use_context(self.identity(admin)), closing(db.connect()) as conn, conn:
            for item in draft["items"]:
                conn.execute(
                    "INSERT INTO ont_release_items(release_id,entity_id,entity_version,kind,snapshot,content_hash,"
                    "item_role) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (
                        draft["id"],
                        item["entity_id"],
                        item["entity_version"],
                        item["kind"],
                        json.dumps(item["snapshot"], ensure_ascii=False),
                        item["content_hash"],
                        item["item_role"],
                    ),
                )
            conn.execute(
                "UPDATE ont_releases SET status='published',reviewed_by=?,published_at=?, "
                "stats=JSON_REMOVE(stats,'$.preview_items') WHERE id=? AND status='draft'",
                (self.identity(reviewer).user_id, db.now(), draft["id"]),
            )
            conn.execute("UPDATE ont_modules SET current_release_id=? WHERE id=?", (draft["id"], draft["module_id"]))

    def test_incoming_scene_term_resolves_in_the_public_snapshot_without_a_reverse_dependency(self) -> None:
        admin, builder, reviewer = (self.login(name) for name in ("r2.other", "b1.modeler", "b1.reviewer"))
        evidence = self.evidence(builder)

        def term(title: str, uses: list[str]) -> dict:
            payload = {
                "schema": "term/1",
                "definition": title + "的业务口径",
                "source_refs": [evidence],
                "refers_to": uses,
            }
            draft = self.invoke(builder, "element.create_draft", {"kind": "term", "title": title, "payload": payload})[
                "entity"
            ]
            return self.publish_asset(builder, reviewer, draft)

        public = term("已收讫记录", [])
        source = term("现场交接登记", [])
        edge = self.invoke(
            builder,
            "relation.propose",
            {
                "source": source["id"],
                "target": public["id"],
                "type": "references",
                "reason": "场景登记采用公共收讫口径",
            },
        )
        self.invoke(
            reviewer,
            "relation.decide",
            {"relation_id": edge["id"], "status": "confirmed", "reason": "已核对术语间的单向引用"},
        )
        scene = self.scene_fixture(admin, [source["id"]])
        preview = self.business(admin, "release.create_draft", {"module_id": scene})
        common = self.business(admin, "release.create_draft", {"module_id": COMMON})
        self.assertIn(COMMON, preview["missing_dependencies"])
        self.assertNotIn(scene, common["missing_dependencies"])
        incoming = next(item for item in common["items"] if item["entity_id"] == source["id"])
        self.assertEqual(
            (incoming["item_role"], set(incoming["snapshot"])), ("ref", {"id", "title", "kind", "version"})
        )
        for module in (COMMON, scene):
            self.business(admin, "module.save", {"module_id": module, "register_dependencies": True})
        self.assertEqual(self.detail(admin, preview["id"])["missing_dependencies"], [])
        self.assertNotIn(scene, self.detail(admin, common["id"])["missing_dependencies"])
        with use_context(self.identity(admin)), closing(db.connect()) as conn:
            pairs = {
                (row["module_id"], row["depends_on_module"])
                for row in conn.execute(
                    "SELECT module_id,depends_on_module FROM ont_module_deps WHERE module_id=? OR depends_on_module=?",
                    (scene, scene),
                )
            }
        self.assertIn((scene, COMMON), pairs)
        self.assertNotIn((COMMON, scene), pairs)

    def test_scene_promotion_preserves_member_history_and_minimizes_external_refs(self) -> None:
        admin, builder, reviewer = (self.login(name) for name in ("r2.other", "b1.nonowner", "b1.reviewer"))
        evidence = self.evidence(builder)
        promoted, staying, external, pending = (
            self.asset(builder, reviewer, title, evidence)
            for title in ("归档移交份数", "接收清册份数", "领用清册份数", "临时登记份数")
        )
        for target, confirm in ((external, True), (pending, False)):
            relation = self.invoke(
                builder,
                "relation.propose",
                {"source": staying["id"], "target": target["id"], "type": "references_data", "reason": "清册互相核对"},
            )
            if confirm:
                self.invoke(
                    reviewer,
                    "relation.decide",
                    {"relation_id": relation["id"], "status": "confirmed", "reason": "已核对清册之间的关联"},
                )
        scene = self.scene_fixture(admin, [promoted["id"], staying["id"]])
        initial = self.business(admin, "release.create_draft", {"module_id": scene})
        self.assertEqual(initial["suggested_version"], "1.0.0")
        self.assertEqual(
            {item["entity_id"]: item["item_role"] for item in initial["items"]},
            {promoted["id"]: "member", staying["id"]: "member", external["id"]: "ref"},
        )
        self.assertEqual({row["entity_id"] for row in initial["gates"]}, {promoted["id"], staying["id"]})
        self.assertEqual(initial["missing_dependencies"], [COMMON])
        self.business(admin, "module.save", {"module_id": scene, "register_dependencies": True})
        refreshed = self.business(admin, "release.create_draft", {"module_id": scene})
        self.assertEqual((refreshed["id"], refreshed["manifest_hash"]), (initial["id"], initial["manifest_hash"]))
        self.assertEqual(refreshed["missing_dependencies"], [])
        self.assertEqual(self.detail(admin, initial["id"])["missing_dependencies"], [])
        self.historical_scene(admin, reviewer, refreshed)
        original = self.frozen(admin, initial["id"])
        self.assertEqual(original[promoted["id"]]["item_role"], "member")
        original_asset = self.get(admin, "/api/entities/" + promoted["id"])
        move = {"source_module_id": scene, "target_module_id": COMMON, "entity_ids": [promoted["id"]]}
        self.rejected(builder.post("/api/v2/capabilities/module.move_elements:invoke", json=move), 403)
        self.assertEqual(
            self.invoke(admin, "element.get", {"entity_id": promoted["id"]})["entity"]["_meta"]["module_id"], scene
        )
        moved = self.business(admin, "module.move_elements", move)
        self.assertEqual((moved["moved_count"], moved["entity_ids"]), (1, [promoted["id"]]))
        asset = self.invoke(admin, "element.get", {"entity_id": promoted["id"]})["entity"]
        self.assertEqual(asset["_meta"]["module_id"], COMMON)
        for field in ("id", "version", "kind", "title", "summary", "domain", "status", "payload"):
            self.assertEqual(asset[field], original_asset[field], field)
        next_draft = self.business(admin, "release.create_draft", {"module_id": scene})
        self.assertEqual((next_draft["suggested_version"], next_draft["bump"]), ("1.1.0", "minor"))
        self.assertEqual(next_draft["diff"]["promoted"], [promoted["id"]])
        self.assertEqual(next_draft["diff"]["removed"], [])
        self.assertEqual((next_draft["member_count"], next_draft["ref_count"]), (1, 2))
        self.assertEqual({row["entity_id"] for row in next_draft["gates"]}, {staying["id"]})
        items = {item["entity_id"]: item for item in next_draft["items"]}
        self.assertEqual(set(items), {staying["id"], external["id"], promoted["id"]})
        for target in (promoted, external):
            self.assertEqual(items[target["id"]]["item_role"], "ref")
            self.assertEqual(
                items[target["id"]]["snapshot"], {key: target[key] for key in ("id", "kind", "title", "version")}
            )
        self.assertEqual(items[staying["id"]]["item_role"], "member")
        self.assertEqual(self.frozen(reviewer, initial["id"]), original)
        self.assertEqual(self.get(admin, "/api/v2/modules/" + scene)["current_release_id"], initial["id"])
        self.rejected(
            admin.post("/api/v2/capabilities/release.submit:invoke", json={"release_id": next_draft["id"]}), 409
        )
        absent = "MOD-" + secrets.token_hex(4)
        self.rejected(
            admin.post("/api/v2/capabilities/module.save:invoke", json={"module_id": absent, "name": "空白场景"}), 404
        )
        self.rejected(
            admin.post("/api/v2/capabilities/module.create:invoke", json={"module_id": absent, "name": "空白场景"}), 404
        )
        self.rejected(admin.get("/api/v2/modules/" + absent), 404)

    def test_capabilities_apply_six_roles_resource_ownership_and_tenant_scope(self) -> None:
        users = {
            "admin": "r2.other",
            "modeler": "b1.modeler",
            "nonowner": "b1.nonowner",
            "reviewer": "b1.reviewer",
            "analyst": "b1.analyst",
            "viewer": "b1.viewer",
            "superadmin": "fixture.superadmin",
        }
        clients = {role: self.login(name) for role, name in users.items()}
        admin, owner, reviewer = (clients[role] for role in ("admin", "modeler", "reviewer"))
        hq = clients["superadmin"]
        self.receipt(hq.post("/api/auth/workspace", json={"tenant_id": "sub-01"}))
        hq.headers["X-Workspace-Id"] = "sub-01"
        for name, client in clients.items():
            person = self.identity(client)
            self.assertEqual(person.tenant_id, "sub-01")
            self.assertEqual(person.role, {"superadmin": "admin", "nonowner": "modeler"}.get(name, name))
            self.assertEqual(person.is_superadmin, name == "superadmin")
        for role in ("analyst", "viewer"):
            self.rejected(clients[role].get("/api/entities"), 403)
        foreign = self.login("fixture.admin")
        authorized = {"admin", "superadmin", "modeler"}
        reviewers = {"admin", "superadmin", "reviewer"}
        permission_denials: set[str] = set()

        def check_denials(capability: str, allowed: set[str], body: dict) -> None:
            for role in clients.keys() - allowed:
                with self.subTest(capability=capability, role=role):
                    self.rejected(clients[role].post(f"/api/v2/capabilities/{capability}:invoke", json=body), 403)
            permission_denials.add(capability)

        self.business(admin, "module.save", {"module_id": COMMON, "owner_user_id": self.identity(owner).user_id})
        setting = {"module_id": COMMON, "scope_note": "核对权限时的临时说明"}
        initial_note = self.get(admin, f"/api/v2/modules/{COMMON}")["scope_note"]
        check_denials("module.save", authorized, setting)
        self.assertEqual(self.get(admin, f"/api/v2/modules/{COMMON}")["scope_note"], initial_note)
        for role in sorted(authorized):
            note = "由" + role + "确认本机构清册范围"
            self.business(clients[role], "module.save", {"module_id": COMMON, "scope_note": note})
            self.assertEqual(self.get(admin, f"/api/v2/modules/{COMMON}")["scope_note"], note)

        evidence = self.evidence(owner)
        member_ids = [
            self.asset(owner, reviewer, title, evidence)["id"] for title in ("装箱份数", "交册份数", "复核份数")
        ]
        scene = self.scene_fixture(admin, member_ids)
        for role in ("analyst", "viewer"):
            self.rejected(clients[role].get(f"/api/v2/modules/{scene}"), 404, "NOT_FOUND")
            self.rejected(clients[role].get(f"/api/v2/modules/{scene}/releases"), 404, "NOT_FOUND")
        self.business(
            admin, "module.save", {"module_id": scene, "owner_user_id": self.identity(clients["nonowner"]).user_id}
        )
        # Each modeler manages their named part only. The scenario owner still
        # needs the public part's owner to approve the promotion below.
        scene_settings = {"module_id": scene, "scope_note": "只管理本场景的交接范围"}
        self.rejected(owner.post("/api/v2/capabilities/module.save:invoke", json=scene_settings), 403)
        settings = self.business(clients["nonowner"], "module.save", scene_settings)
        self.assertEqual(settings["scope_note"], scene_settings["scope_note"])
        move = {"source_module_id": scene, "target_module_id": COMMON, "entity_ids": [member_ids[0]]}
        check_denials("module.move_elements", authorized, move)
        for role, ident in zip(sorted(authorized), member_ids, strict=True):
            moved = self.business(clients[role], "module.move_elements", {**move, "entity_ids": [ident]})
            self.assertEqual(moved["entity_ids"], [ident])
            self.assertEqual(
                self.invoke(admin, "element.get", {"entity_id": ident})["entity"]["_meta"]["module_id"], COMMON
            )

        draft_input = {"module_id": COMMON}
        check_denials("release.create_draft", authorized, draft_input)
        publications = []
        for i, (creator, approver) in enumerate(
            (("modeler", "superadmin"), ("admin", "reviewer"), ("superadmin", "admin"))
        ):
            if i:
                self.asset(owner, reviewer, f"第{i}批登记份数", evidence)
            draft = self.business(clients[creator], "release.create_draft", draft_input)
            release_input = {"release_id": draft["id"]}
            self.assertEqual(draft["created_by"], self.identity(clients[creator]).user_id)
            if i == 0:
                for role in ("admin", "modeler", "nonowner", "reviewer", "superadmin"):
                    self.assertEqual(self.detail(clients[role], draft["id"])["status"], "draft")
                for role in ("analyst", "viewer"):
                    self.rejected(clients[role].get("/api/v2/releases/" + draft["id"]), 404)
                    visible = self.get(clients[role], f"/api/v2/modules/{COMMON}/releases")["items"]
                    self.assertNotIn(draft["id"], [row["id"] for row in visible])
                check_denials("release.submit", authorized, release_input)
                self.assertEqual(self.detail(admin, draft["id"])["status"], "draft")
            candidate = self.business(clients[creator], "release.submit", release_input)
            self.assertEqual(candidate["submitted_by"], self.identity(clients[creator]).user_id)
            if i == 0:
                for role in ("analyst", "viewer"):
                    self.rejected(clients[role].get("/api/v2/releases/" + draft["id"]), 404, "NOT_FOUND")
                check_denials("release.publish", reviewers, release_input)
                self.assertEqual(self.detail(admin, draft["id"])["status"], "candidate")
                self.assertEqual(self.frozen(admin, draft["id"]), {})
            published = self.freeze(clients[approver], candidate)
            publications.append(published)

        published = publications[-1]
        target = {"release_id": published["id"]}
        check_denials("release.withdraw", reviewers, target)
        self.assertEqual(permission_denials, WRITERS)
        for name, client in clients.items():
            with self.subTest(release=published["id"], role=name):
                self.assertEqual(self.detail(client, published["id"])["status"], "published")
                listed = self.get(client, f"/api/v2/modules/{COMMON}/releases")["items"]
                self.assertIn(published["id"], [row["id"] for row in listed])

        # The resource id alone and an explicit foreign scope must both be opaque.
        foreign_inputs = {
            "module.save": {"module_id": scene, "scope_note": "外机构修改"},
            "module.move_elements": move,
            "release.create_draft": {"module_id": scene},
            "release.submit": target,
            "release.publish": target,
            "release.withdraw": target,
        }
        for cap, body in foreign_inputs.items():
            with self.subTest(foreign=cap):
                self.rejected(foreign.post(f"/api/v2/capabilities/{cap}:invoke", json=body), 404, "NOT_FOUND")
                # The same resource exists in this session. Ignoring the supplied
                # foreign scope would reach a real local row.
                erroneous_scope = {**body, "tenant_id": "dept-01"}
                self.rejected(admin.post(f"/api/v2/capabilities/{cap}:invoke", json=erroneous_scope), 404, "NOT_FOUND")
        for path in (
            f"/api/v2/modules/{scene}",
            f"/api/v2/modules/{scene}/releases",
            "/api/v2/releases/" + published["id"],
        ):
            self.rejected(foreign.get(path), 404, "NOT_FOUND")
        self.rejected(foreign.get(f"/api/v2/modules/{COMMON}", params={"tenant_id": "sub-01"}), 404, "NOT_FOUND")
        self.assertEqual(self.detail(admin, published["id"])["status"], "published")

        # An internal read-only identity can still carry an administrator's earlier
        # permissions. Exercise the dispatcher independently of the six HTTP logins.
        person = self.identity(admin)
        readonly = CapabilityContext(replace(person, readonly=True), permissions=frozenset(permissions_for(person)))
        for cap, body in foreign_inputs.items():
            with self.subTest(readonly=cap):
                self.assertIn(cap, registry.entries)
                with self.assertRaises(ApiError) as rejection:
                    registry.invoke(cap, readonly, body)
                self.assertEqual(rejection.exception.code, "PERMISSION_DENIED")
        for version, actor in zip(reversed(publications), ("reviewer", "admin", "superadmin"), strict=True):
            withdrawn = self.business(clients[actor], "release.withdraw", {"release_id": version["id"]})
            self.assertEqual(withdrawn["status"], "withdrawn")
            self.assertEqual(self.detail(admin, version["id"])["manifest_hash"], version["manifest_hash"])
        for role in ("viewer", "analyst"):
            history = self.detail(clients[role], published["id"])
            self.assertEqual((history["status"], history["manifest_hash"]), ("withdrawn", published["manifest_hash"]))
            self.assertIn(member_ids[0], {item["entity_id"] for item in history["items"]})
        self.assertIsNone(self.get(admin, f"/api/v2/modules/{COMMON}")["current_release_id"])
