"""D19 ownership, promotion, and local dependencies on real tenant databases."""

import os
import secrets
import threading
import unittest
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

from backend import db
from backend.api.v1 import core
from backend.core.errors import ApiError
from backend.kernel import modules, relations
from backend.security import store
from backend.security.context import Principal, use_context
from tests.r2 import test_build as construction

COMMON = "MOD-DEFAULT"


@unittest.skipUnless(os.getenv("RUN_JOBS_MYSQL_TESTS") == "1", "opt-in B1 MySQL tests")
class InstitutionModules(unittest.TestCase):
    setUpClass = classmethod(construction.BuildMySQL.setUpClass.__func__)
    tearDownClass = classmethod(construction.BuildMySQL.tearDownClass.__func__)
    login = construction.BuildMySQL.login

    def person(self, username: str) -> Principal:
        token = store.login(username, "SyntheticFixture9!", "127.0.0.1", "b1-ownership")
        return store.authenticate_session(token, "b1-ownership")[0]

    def setUp(self) -> None:
        self.admin = self.person("fixture.admin")
        self.builder = self.person("fixture.builder")
        self.reviewer = self.person("fixture.reviewer")
        self.save(self.admin, COMMON, owner_user_id=None)

    @staticmethod
    def save(person: Principal, ident: str, **changes: object) -> dict:
        with use_context(person), closing(db.connect()) as conn, conn:
            return modules.save(conn, person, ident, changes)

    @staticmethod
    def move(person: Principal, origin: str, target: str, ids: list[str]) -> dict:
        with use_context(person), closing(db.connect()) as conn, conn:
            return modules.move_elements(conn, person, origin, target, ids)

    def scene(self, *members: str, owner: Principal | None = None, module_type: str = "scenario") -> str:
        # D19 only: fixtures may establish an existing scenario and its initial membership.
        suffix = secrets.token_hex(4)
        ident = "MOD-" + suffix
        stamp = db.now()
        with use_context(self.admin), closing(db.connect()) as conn, conn:
            conn.execute(
                "INSERT INTO ont_modules(id,module_key,module_type,name,domain_code,owner_user_id,"
                "created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    ident,
                    "scn_" + suffix,
                    module_type,
                    "合同核验",
                    "contract",
                    (owner or self.admin).user_id,
                    self.admin.user_id,
                    stamp,
                    stamp,
                ),
            )
            for eid in members:
                conn.execute("UPDATE entity_meta SET module_id=? WHERE entity_id=?", (ident, eid))
        return ident

    def asset(self, **changes: object) -> str:
        with use_context(self.builder):
            return core._create_entity({"kind": "knowledge", "title": "合同核验 " + secrets.token_hex(4), **changes})[
                "id"
            ]

    def query(self, sql: str, args: tuple = (), person: Principal | None = None) -> list[dict]:
        with use_context(person or self.admin), closing(db.connect()) as conn:
            return [dict(row) for row in conn.execute(sql, args)]

    def denied(self, code: str, function: Callable, *args: object, **kwargs: object) -> None:
        with self.assertRaises(ApiError) as failure:
            function(*args, **kwargs)
        self.assertEqual(failure.exception.code, code)

    def test_settings_check_existing_current_owner_and_assignee_in_live_workspace(self) -> None:
        foreign = self.person("r2.other")
        scene = self.scene(owner=self.builder)
        self.denied("PERMISSION_DENIED", self.save, self.builder, COMMON, owner_user_id=self.builder.user_id)
        self.denied("PERMISSION_DENIED", self.save, self.reviewer, scene, scope_note="拒绝")
        self.denied("NOT_FOUND", self.save, foreign, scene, name="别的机构")
        self.denied("NOT_FOUND", self.save, self.admin, "MOD-absent", name="空白场景")
        self.denied("NOT_FOUND", self.save, self.admin, self.scene(module_type="group_core"), name="集团核心")
        self.denied("NOT_FOUND", self.save, self.admin, scene, owner_user_id=foreign.user_id)
        self.denied("VALIDATION_FAILED", self.save, self.admin, scene, domain_code="not_a_domain")
        self.denied("VALIDATION_FAILED", self.save, self.admin, COMMON, domain_code="finance")
        self.denied("VALIDATION_FAILED", self.save, self.admin, COMMON, name="自由改写的机构名")
        self.denied("VALIDATION_FAILED", self.save, self.admin, scene, current_release_id="RLS-forged")
        first = self.save(self.builder, scene, name="付款确认", domain_code="payment", scope_note="核对付款要求")
        self.assertEqual(
            (first["name"], first["domain_code"], first["module_type"]), ("付款确认", "payment", "scenario")
        )
        second = self.save(self.admin, scene, owner_user_id=self.admin.user_id, expected_updated_at=first["updated_at"])
        self.assertEqual(second["owner_user_id"], self.admin.user_id)
        self.denied("PERMISSION_DENIED", self.save, self.builder, scene, name="原负责人")
        self.denied(
            "VERSION_CONFLICT", self.save, self.admin, scene, expected_updated_at=first["updated_at"], name="旧页面"
        )
        inactive = store.create_user(
            self.admin,
            username="b1.inactive",
            display_name="停用的建设人员",
            tenant_id=self.admin.tenant_id,
            role="modeler",
            password="SyntheticFixture9!",
            must_change_password=False,
        )
        store.update_user(self.admin, inactive, {"is_active": False})
        self.denied("NOT_FOUND", self.save, self.admin, scene, owner_user_id=inactive)

    def test_workspace_uses_live_directory_id_and_readonly_connections_fail_before_locking(self) -> None:
        token = store.login("fixture.superadmin", "SyntheticFixture9!", "127.0.0.1", "ownership-scope")
        headquarters = store.authenticate_session(token, "ownership-scope")[0]
        self.assertIsNone(store.get_tenant(headquarters.tenant_id)["org_id"])
        root = self.save(headquarters, COMMON, owner_user_id=headquarters.user_id)
        self.assertEqual(root["owner_user_id"], headquarters.user_id)
        store.switch_workspace(headquarters, self.admin.tenant_id)
        local = store.authenticate_session(token, "ownership-scope")[0]
        self.assertEqual((local.tenant_id, local.home_tenant_id), (self.admin.tenant_id, headquarters.tenant_id))
        self.denied("NOT_FOUND", self.save, local, COMMON, owner_user_id=headquarters.user_id)
        assigned = self.save(local, COMMON, owner_user_id=self.builder.user_id)
        self.assertEqual(assigned["owner_user_id"], self.builder.user_id)
        with use_context(local), db.tenant_connection(local.tenant_id) as conn:
            self.denied("PERMISSION_DENIED", modules.save, conn, local, COMMON, {"owner_user_id": None})
        other = self.person("r2.other")
        with use_context(local), closing(db.connect()) as conn:
            self.denied("NOT_FOUND", modules.save, conn, other, COMMON, {"scope_note": "错误工作区"})
        self.assertEqual(
            self.query("SELECT owner_user_id FROM ont_modules WHERE id=?", (COMMON,)),
            [{"owner_user_id": self.builder.user_id}],
        )

    def test_promotion_preserves_content_history_and_protected_superseded_preferences(self) -> None:
        asset = self.asset(payload={"source_refs": [{"material_id": "original", "locator": "第二条"}]})
        origin = self.scene(asset, owner=self.builder)
        before = self.query("SELECT * FROM entities WHERE id=?", (asset,))
        history = self.query("SELECT * FROM versions WHERE entity_id=? ORDER BY id", (asset,))
        metadata = self.query("SELECT * FROM entity_meta WHERE entity_id=?", (asset,))[0]
        self.save(self.builder, origin, preferences={"notes": "保留来源标记"})
        self.denied("PERMISSION_DENIED", self.move, self.builder, origin, COMMON, [asset])
        self.denied("PERMISSION_DENIED", self.move, self.reviewer, origin, COMMON, [asset])
        self.denied("NOT_FOUND", self.move, self.person("r2.other"), origin, COMMON, [asset])
        self.denied("NOT_FOUND", self.move, self.admin, origin, COMMON, [asset, "unknown"])
        self.denied("PRECONDITION_FAILED", self.move, self.admin, origin, origin, [asset])
        self.denied("PRECONDITION_FAILED", self.move, self.admin, COMMON, origin, [asset])
        self.assertEqual(
            self.query("SELECT module_id FROM entity_meta WHERE entity_id=?", (asset,))[0]["module_id"], origin
        )
        owner = self.save(self.admin, COMMON, owner_user_id=self.builder.user_id)
        self.assertEqual(owner["owner_user_id"], self.builder.user_id)
        result = self.move(self.builder, origin, COMMON, [asset])
        self.assertEqual(
            result, {"source_module_id": origin, "target_module_id": COMMON, "entity_ids": [asset], "moved_count": 1}
        )
        after = self.query("SELECT * FROM entity_meta WHERE entity_id=?", (asset,))[0]
        self.assertEqual(after["module_id"], COMMON)
        self.assertEqual(
            {k: v for k, v in metadata.items() if k not in {"module_id", "updated_at"}},
            {k: v for k, v in after.items() if k not in {"module_id", "updated_at"}},
        )
        self.assertEqual(self.query("SELECT * FROM entities WHERE id=?", (asset,)), before)
        self.assertEqual(self.query("SELECT * FROM versions WHERE entity_id=? ORDER BY id", (asset,)), history)
        self.assertEqual(
            self.query("SELECT depends_on_tenant,depends_on_module FROM ont_module_deps WHERE module_id=?", (origin,)),
            [{"depends_on_tenant": self.admin.tenant_id, "depends_on_module": COMMON}],
        )
        self.denied("PRECONDITION_FAILED", self.move, self.admin, origin, COMMON, [asset])
        for forged in ({"_promoted_entity_ids": ["fake"]}, {"promoted_ids": ["fake"]}, {"updated_by": "system"}):
            self.denied("VALIDATION_FAILED", self.save, self.builder, origin, preferences=forged)
        saved = self.save(self.builder, origin, preferences={})
        self.assertNotIn(asset, str(saved["preferences"]))
        with use_context(self.admin), closing(db.connect()) as conn:
            self.assertEqual(modules.promoted_ids(conn, origin), {asset})

    def test_relation_dependencies_use_confirmed_direct_endpoints_and_preserve_cycles(self) -> None:
        left, right, unrelated = self.asset(), self.asset(), self.asset()
        source, target = self.scene(left), self.scene(right)
        third = self.scene(unrelated)
        with use_context(self.builder):
            forward = relations.propose(left, right, "references", "付款步骤复用合同检查表")
            backward = relations.propose(right, left, "references", "合同验收需要付款结论")
            pending = relations.propose(left, unrelated, "references", "暂待核对")
        self.save(self.admin, source, register_dependencies=True)
        self.assertEqual(self.query("SELECT * FROM ont_module_deps WHERE module_id=?", (source,)), [])
        with use_context(self.reviewer):
            for edge in (forward, backward):
                core._confirm_relation(edge["id"], {"status": "confirmed", "reason": "核实双向引用"})
            core._confirm_relation(pending["id"], {"status": "rejected", "reason": "属于无关材料"})
        for origin, expected in ((source, target), (target, source)):
            deps = self.query(
                "SELECT depends_on_tenant,depends_on_module FROM ont_module_deps WHERE module_id=?", (origin,)
            )
            self.assertEqual(deps, [{"depends_on_tenant": self.admin.tenant_id, "depends_on_module": expected}])
        self.assertEqual(self.query("SELECT * FROM ont_module_deps WHERE module_id=?", (third,)), [])
        self.move(self.admin, target, COMMON, [right])
        self.assertEqual(
            {r["depends_on_module"] for r in self.query("SELECT * FROM ont_module_deps WHERE module_id=?", (source,))},
            {COMMON, target},
        )
        self.save(self.admin, source, register_dependencies=True)
        with use_context(self.admin), closing(db.connect()) as conn:
            self.assertEqual(modules.missing_reference_dependencies(conn, source, self.admin.tenant_id, [right]), [])

    def test_repair_discovers_only_historic_confirmed_local_references(self) -> None:
        member, referenced, unrelated = self.asset(), self.asset(), self.asset()
        with use_context(self.builder):
            known = relations.propose(member, referenced, "references", "复用合同确认记录")
            relations.propose(member, unrelated, "references", "候选检查表")
        with use_context(self.reviewer):
            core._confirm_relation(known["id"], {"status": "confirmed", "reason": "查验合同确认记录"})
        # The existing scene fixture contains a genuine, already-confirmed edge, predating registration.
        origin, target = self.scene(member), self.scene(referenced)
        other = self.scene(unrelated)
        sql = "SELECT depends_on_tenant,depends_on_module FROM ont_module_deps WHERE module_id=?"
        self.assertEqual(self.query(sql, (origin,)), [])
        with use_context(self.admin), closing(db.connect()) as conn:
            self.assertEqual(
                modules.missing_reference_dependencies(conn, origin, self.admin.tenant_id, [referenced]), [target]
            )
        self.denied("VALIDATION_FAILED", self.save, self.admin, origin, register_dependencies=[other])
        for _ in range(2):
            self.save(self.admin, origin, register_dependencies=True)
            self.assertEqual(
                self.query(sql, (origin,)), [{"depends_on_tenant": self.admin.tenant_id, "depends_on_module": target}]
            )

    def test_concurrent_promotions_retain_both_ids_after_stale_and_fresh_preference_writes(self) -> None:
        ids = [self.asset(), self.asset()]
        scene = self.scene(*ids)
        before = self.save(self.admin, scene, preferences={"notes": "逐项核验"})
        together = threading.Barrier(2)

        def promote(eid: str) -> dict:
            together.wait(timeout=10)
            return self.move(self.admin, scene, COMMON, [eid])

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(promote, ids))
        self.assertEqual([r["entity_ids"] for r in results], [[eid] for eid in ids])
        self.denied(
            "VERSION_CONFLICT", self.save, self.admin, scene, expected_updated_at=before["updated_at"], preferences=None
        )
        with use_context(self.admin), closing(db.connect()) as conn:
            self.assertEqual(modules.get_module(conn, scene)["preferences"], {"notes": "逐项核验"})
        self.save(self.admin, scene, preferences=None)
        with use_context(self.admin), closing(db.connect()) as conn:
            self.assertEqual(modules.promoted_ids(conn, scene), set(ids))
            self.assertEqual(modules.get_module(conn, scene)["preferences"], {})

    def test_batch_collision_and_dedicated_work_records_fail_without_partial_promotion(self) -> None:
        key = "duplicate_" + secrets.token_hex(4)
        moving = self.asset(meta={"element_key": key})
        normal = self.asset()
        client = self.login("fixture.builder")
        response = client.post("/api/skills", json={"title": "核对技能", "summary": "检查合同附件"})
        self.assertEqual(response.status_code, 200, response.text)
        skill = response.json()["entity"]["id"]
        authority = client.post(
            "/api/authority/assets",
            json={"title": "分工确认", "spec": {"type": "position", "org_ids": [self.admin.tenant_id]}},
        )
        self.assertEqual(authority.status_code, 200, authority.text)
        norm = authority.json()["entity"]["id"]
        project = client.post("/api/special-projects", json={"title": "付款专项", "scope": "核对合同附件"})
        self.assertEqual(project.status_code, 200, project.text)
        project_id = project.json()["project"]["id"]
        matter = client.post(f"/api/special-projects/{project_id}/matters", json={"title": "附件缺项"})
        self.assertEqual(matter.status_code, 200, matter.text)
        origin = self.scene(moving, normal, skill, norm)
        self.asset(meta={"element_key": key})
        self.denied("PRECONDITION_FAILED", self.move, self.admin, origin, COMMON, [normal, moving])
        self.denied("PRECONDITION_FAILED", self.move, self.admin, origin, COMMON, [normal, skill])
        self.denied("VALIDATION_FAILED", self.move, self.admin, origin, COMMON, [normal, norm])
        self.denied("VALIDATION_FAILED", self.move, self.admin, origin, COMMON, [normal, matter.json()["entity"]["id"]])
        self.denied("PRECONDITION_FAILED", self.move, self.admin, origin, COMMON, [normal, "DEMO07-MATERIAL-RULES"])
        self.denied("PERMISSION_DENIED", self.move, self.admin, origin, COMMON, [normal, self.admin.tenant_id])
        self.assertEqual(
            {
                r["module_id"]
                for r in self.query(
                    "SELECT module_id FROM entity_meta WHERE entity_id IN (?,?,?,?)", (normal, moving, skill, norm)
                )
            },
            {origin},
        )
        with use_context(self.admin), closing(db.connect()) as conn:
            self.assertEqual(modules.promoted_ids(conn, origin), set())
