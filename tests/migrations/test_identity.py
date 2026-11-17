"""R1 identity migration and lifecycle, using isolated real historical databases."""

import copy
import json
import os
import unittest
from contextlib import closing
from dataclasses import replace
from unittest.mock import patch

from fastapi import HTTPException

from backend import db
from backend.api.v2.audit import AuditInput, query_audit
from backend.api.v2.orgs import OrgManageInput
from backend.infra import backup, migrations
from backend.infra.recovery_schema import normalized_ddl, validate_schema
from backend.maintenance import runtime_guard
from backend.security import directory, preferences, store
from backend.security.context import use_context
from tests.migrations import test_integration as migration_tests


@unittest.skipUnless(os.environ.get("RUN_JOBS_MYSQL_TESTS") == "1", "opt-in MySQL integration test")
class IdentityMigration(unittest.TestCase):
    setUp = migration_tests.MigrationMySQL.setUp
    tearDown = migration_tests.MigrationMySQL.tearDown
    fixture = migration_tests.MigrationMySQL.fixture

    def prepare(self) -> dict:
        names = self.fixture()
        migrations.run_migrations()
        return names

    def login(self, username: str = "fixture.superadmin", password: str = "SyntheticFixture9!") -> tuple:
        raw = store.login(username, password, "127.0.0.1", "identity-test")
        return store.authenticate_session(raw, "identity-test")[0], raw

    def create_org(self, **changes: object) -> str:
        p, _ = self.login()
        return directory.manage(
            p,
            OrgManageInput(action="create", name="New organization", category="subsidiary", parent_id="hq", **changes),
        )

    def test_directory_migration_preserves_identity(self) -> None:
        self.fixture()
        before = store.list_tenants()
        migrations.run_migrations()
        after = store.list_tenants()
        self.assertEqual(len(before), len(after))
        for old, new in zip(before, after, strict=True):
            self.assertEqual(old, {key: new[key] for key in old})
            self.assertEqual(new["short_name"], old["name"][:120])
            self.assertEqual(new["parent_id"], None if old["id"] == "hq" else "hq")
            self.assertEqual(new["level"], 0 if old["id"] == "hq" else 1)
            self.assertFalse(new["is_demo"])
            self.assertIsNone(new["mirror_of"])
            self.assertIsNone(new["disabled_at"])
            self.assertEqual(new["disabled_reason"], "")
            self.assertIsNone(new["org_code"])
            self.assertEqual(new["sort_order"], 0)

    def test_duties_are_empty_and_projected(self) -> None:
        self.prepare()
        p, raw = self.login()
        self.assertEqual(p.duties, ())
        with store.control_connection() as conn, conn.cursor() as cur:
            cur.execute("UPDATE sec_users SET duties=%s WHERE id=%s", (json.dumps(["unknown"]), p.user_id))
        p = store.authenticate_session(raw, "duties")[0]
        self.assertEqual(p.duties, ("unknown",))
        from backend.security.permissions import permissions_for

        self.assertNotIn("federation.steward", permissions_for(p))

    def test_preferences_persist_only_for_self(self) -> None:
        self.prepare()
        p, _ = self.login()
        other, _ = self.login("fixture.admin")
        self.assertEqual(preferences.write_preference(p, "view_mode", "pro"), {"view_mode": "pro"})
        self.assertEqual(preferences.read_preferences(other), {})
        self.assertEqual(preferences.read_preferences(p), {"view_mode": "pro"})
        with self.assertRaises(HTTPException):
            preferences.write_preference(p, "view_mode", "invalid")
        with store.control_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT details FROM sec_audit WHERE actor_id=%s AND action='preference_update'", (p.user_id,))
            self.assertEqual([json.loads(row["details"]) for row in cur.fetchall()], [{"pref_key": "view_mode"}])
        with patch.object(store, "_audit", side_effect=RuntimeError("audit failed")), self.assertRaises(RuntimeError):
            preferences.write_preference(p, "view_mode", "simple")
        self.assertEqual(preferences.read_preferences(p), {"view_mode": "pro"})

    def test_old_password_flag_is_cleared(self) -> None:
        names = self.fixture()
        before = backup.snapshot_database(names["control"])["tables"]["sec_users"]
        self.assertTrue(any(row["must_change_password"] for row in before))
        migrations.run_migrations()
        after = backup.snapshot_database(names["control"])["tables"]["sec_users"]
        expected = {row["id"]: {**row, "must_change_password": 0} for row in before}
        for row in after:
            self.assertEqual({key: row[key] for key in before[0]}, expected[row["id"]])
        p, raw = self.login("fixture.change-password")
        self.assertFalse(p.must_change_password)
        payload = store.session_payload(p, raw)
        self.assertFalse(payload["must_change_password"])
        self.assertFalse(payload["user"]["must_change_password"])

    def test_application_options_keep_flagged_active_local_candidates_and_manager_guards(self) -> None:
        from backend.security import applications

        self.prepare()
        manager, _ = self.login("fixture.admin")
        with store.control_connection() as conn, conn.cursor() as cur:
            cur.execute("UPDATE sec_users SET must_change_password=1 WHERE username='fixture.builder'")
            cur.execute("UPDATE sec_users SET is_active=0 WHERE username='fixture.change-password'")
            cur.execute("SELECT username,home_tenant_id,is_active,must_change_password FROM sec_users")
            rows = {row["username"]: row for row in cur.fetchall()}
        self.assertEqual(rows["fixture.builder"]["must_change_password"], 1)
        self.assertEqual(rows["fixture.builder"]["home_tenant_id"], manager.tenant_id)
        self.assertTrue(rows["fixture.builder"]["is_active"])
        self.assertFalse(rows["fixture.change-password"]["is_active"])
        self.assertNotEqual(rows["fixture.superadmin"]["home_tenant_id"], manager.tenant_id)
        builder, _ = self.login("fixture.builder")
        with use_context(manager):
            options = applications.application_options()
            self.assertEqual(
                {user["username"] for user in options["users"]},
                {"fixture.admin", "fixture.builder", "fixture.reviewer"},
            )
            issued = applications.create_application(
                applications.ApplicationCreate(
                    name="Options guard fixture", delegate_user_id=builder.user_id, tool_scopes=["ontology_query"]
                )
            )
        application = applications.authenticate_application(issued["credential"]["token"], "options-test")
        self.assertIsNotNone(application)
        self.assertEqual(application.identity_kind, "application")
        for denied in (builder, application):
            with self.subTest(identity=denied.identity_kind), use_context(denied):
                with self.assertRaises(HTTPException) as error:
                    applications.application_options()
                self.assertEqual(error.exception.status_code, 403)

    def test_fresh_and_upgraded_control_schema_match(self) -> None:
        names = self.prepare()
        current = backup.snapshot_database(names["control"])
        validate_schema(current, "control")
        self.assertEqual(migrations.migration_status("control", names["control"])["current"], 6)
        self.assertEqual(
            [m.name for m in migrations.discover("control")],
            [
                "0001_baseline",
                "0002_directory",
                "0003_duties",
                "0004_preferences",
                "0005_password_advice",
                "0006_job_schedule",
            ],
        )
        self.assertEqual(len(current["columns"]["sec_tenants"]), 16)

    def test_repeated_migration_preserves_all_rows(self) -> None:
        names = self.prepare()
        before = backup.snapshot_database(names["control"])["hashes"]
        self.assertEqual(migrations.run_migrations()["migrated"], [])
        self.assertEqual(before, backup.snapshot_database(names["control"])["hashes"])

    def test_new_structure_backup_restores_exactly(self) -> None:
        names = self.prepare()
        before = backup.snapshot_database(names["control"])
        with runtime_guard(shared=False):
            path = backup.create_upgrade_backup(store.list_tenants())
            backup.restore_upgrade_backup(path)
        after = backup.snapshot_database(names["control"])
        self.assertEqual(
            {k: v for k, v in before.items() if k != "ddl"}, {k: v for k, v in after.items() if k != "ddl"}
        )
        self.assertEqual(
            {k: normalized_ddl(v) for k, v in before["ddl"].items()},
            {k: normalized_ddl(v) for k, v in after["ddl"].items()},
        )
        migrations.validate_runtime()

    def test_old_structure_restore_requires_upgrade_again(self) -> None:
        names = self.fixture()
        before = backup.snapshot_database(names["control"])
        result = migrations.run_migrations()
        with runtime_guard(shared=False):
            backup.restore_upgrade_backup(result["backup"])
        self.assertEqual(before, backup.snapshot_database(names["control"]))
        with self.assertRaises(migrations.MigrationError):
            migrations.validate_runtime()

    def test_directory_file_cannot_rename_existing(self) -> None:
        self.prepare()
        before = store.list_tenants()
        with patch.object(store, "_directory", side_effect=AssertionError("Must not reseed")):
            store.initialize_control()
        self.assertEqual(before, store.list_tenants())

    def test_new_org_is_ready_before_active(self) -> None:
        self.prepare()
        ident = self.create_org()
        row = store.get_tenant(ident)
        self.assertRegex(ident, r"^org-[0-9a-f]{8}$")
        self.assertEqual(row["status"], "active")
        self.assertEqual(migrations.migration_status("tenant", row["db_name"])["current"], 4)
        with closing(db._raw_connection(row["db_name"])) as raw:
            self.assertEqual(
                db.Connection(raw).execute("SELECT title FROM entities WHERE id=?", (ident,)).fetchone()[0], row["name"]
            )

    def test_new_org_account_can_login_without_password_gate(self) -> None:
        self.prepare()
        ident = self.create_org()
        p, _ = self.login()
        store.create_user(
            p,
            username="new.account",
            display_name="New",
            tenant_id=ident,
            role="admin",
            password="NewAccountPassword9!",
            must_change_password=True,
        )
        account, raw = self.login("new.account", "NewAccountPassword9!")
        self.assertEqual(account.tenant_id, ident)
        self.assertFalse(store.session_payload(account, raw)["must_change_password"])

    def test_org_failure_stays_nonlogin_and_does_not_block_runtime(self) -> None:
        self.prepare()
        p, _ = self.login()
        with (
            patch.object(migrations, "provision_tenant", side_effect=RuntimeError("failure")),
            self.assertRaises(HTTPException),
        ):
            directory.manage(p, OrgManageInput(action="create", name="Failed", category="subsidiary"))
        row = next(row for row in store.list_tenants() if row["name"] == "Failed")
        self.assertEqual(row["status"], "provision_failed")
        with self.assertRaises(migrations.MigrationError):
            migrations.provision_tenant(row["id"])
        migrations.validate_runtime()
        self.assertNotIn(row["id"], {t["id"] for t in store.authorized_read_tenants(p)})

    def test_disable_blocks_login_and_federation_enable_restores(self) -> None:
        self.prepare()
        p, _ = self.login()
        directory.manage(p, OrgManageInput(action="disable", org_id="dept-01", reason="Test disable"))
        with self.assertRaises(HTTPException):
            self.login("fixture.admin")
        self.assertNotIn("dept-01", {t["id"] for t in store.authorized_read_tenants(p)})
        directory.manage(p, OrgManageInput(action="enable", org_id="dept-01"))
        self.assertEqual(self.login("fixture.admin")[0].home_tenant_id, "dept-01")

    def test_headquarters_cannot_be_disabled(self) -> None:
        self.prepare()
        p, _ = self.login()
        with self.assertRaises(HTTPException):
            directory.manage(p, OrgManageInput(action="disable", org_id="hq"))
        self.assertEqual(store.get_tenant("hq")["status"], "active")

    def test_non_hq_admin_cannot_manage_directory(self) -> None:
        self.prepare()
        p, _ = self.login("fixture.admin")
        with self.assertRaises(HTTPException):
            directory.manage(p, OrgManageInput(action="create", name="No", category="subsidiary"))

    def test_rename_atomically_records_root_version(self) -> None:
        self.prepare()
        p, _ = self.login()
        before = store.get_tenant("dept-01")
        with use_context(p):
            directory.manage(p, OrgManageInput(action="update", org_id="dept-01", name="Renamed"))
        after = store.get_tenant("dept-01")
        self.assertEqual((before["id"], before["db_name"]), (after["id"], after["db_name"]))
        with closing(db._raw_connection(after["db_name"])) as raw:
            conn = db.Connection(raw)
            self.assertEqual(conn.execute("SELECT title FROM entities WHERE id='dept-01'").fetchone()[0], "Renamed")
            snapshot = json.loads(
                conn.execute(
                    "SELECT snapshot FROM versions WHERE entity_id='dept-01' ORDER BY id DESC LIMIT 1"
                ).fetchone()[0]
            )
            self.assertEqual(snapshot["title"], before["name"])
            self.assertEqual(snapshot["owner_tenant_id"], "dept-01")
            self.assertEqual(snapshot["owner_org_id"], before["org_id"])

    def test_rename_failure_rolls_back_both_databases(self) -> None:
        self.prepare()
        p, _ = self.login()
        row = store.get_tenant("dept-01")
        before = backup.snapshot_database(row["db_name"])["hashes"]
        with patch.object(store, "_audit", side_effect=RuntimeError("audit failed")), self.assertRaises(RuntimeError):
            directory.manage(p, OrgManageInput(action="update", org_id="dept-01", name="Rollback"))
        self.assertEqual(store.get_tenant("dept-01")["name"], row["name"])
        self.assertEqual(before, backup.snapshot_database(row["db_name"])["hashes"])

    def test_cycle_rejected_and_hierarchy_does_not_grant(self) -> None:
        self.prepare()
        ident = self.create_org()
        p, _ = self.login()
        directory.manage(p, OrgManageInput(action="update", org_id=ident, parent_id="dept-01"))
        with self.assertRaises(HTTPException):
            directory.manage(p, OrgManageInput(action="update", org_id="dept-01", parent_id=ident))
        local, _ = self.login("fixture.admin")
        self.assertNotIn(ident, {t["id"] for t in store.authorized_read_tenants(local)})

    def test_password_advice_follows_reset_and_self_change(self) -> None:
        self.prepare()
        p, _ = self.login()
        uid = store.create_user(
            p,
            username="advice.user",
            display_name="Advice",
            tenant_id="dept-01",
            role="modeler",
            password="InitialPassword9!",
        )
        target, raw = self.login("advice.user", "InitialPassword9!")
        self.assertTrue(store.session_payload(target, raw)["password_change_recommended"])
        raw = store.change_password(target, "InitialPassword9!", "PersonalPassword9!", "127.0.0.1", "test")
        target = store.authenticate_session(raw, "")[0]
        self.assertFalse(store.session_payload(target, raw)["password_change_recommended"])
        store.update_user(p, uid, {"password": "ResetPassword9!"})
        target, raw = self.login("advice.user", "ResetPassword9!")
        self.assertTrue(store.session_payload(target, raw)["password_change_recommended"])
        self.assertFalse(store.session_payload(target, raw)["must_change_password"])

    def test_session_retains_boolean_permissions_and_roles(self) -> None:
        self.prepare()
        p, raw = self.login()
        data = store.session_payload(p, raw)
        self.assertEqual(set(data["permissions"]), {"manage_users", "manage_grants", "can_edit", "can_review"})
        self.assertTrue(all(type(value) is bool for value in data["permissions"].values()))
        self.assertEqual({r["id"] for r in data["roles"]}, {"admin", "modeler", "reviewer"})
        self.assertIn("admin.org", data["permission_keys"])

    def test_audit_new_hq_only_old_local_behavior_preserved(self) -> None:
        self.prepare()
        local, _ = self.login("fixture.admin")
        self.assertIsInstance(store.audit_records(local)["items"], list)
        with self.assertRaises(HTTPException):
            query_audit(local, AuditInput())
        p, _ = self.login()
        result = query_audit(p, AuditInput(actor_id=p.user_id, action="login", outcome="success"))
        self.assertEqual(result["page_size"], 30)
        for row in result["items"]:
            self.assertEqual((row["actor_id"], row["action"], row["outcome"]), (p.user_id, "login", "success"))

    def test_new_roles_rejected_by_store_create_and_update(self) -> None:
        self.prepare()
        p, _ = self.login()
        target, _ = self.login("fixture.admin")
        for role in ("viewer", "analyst"):
            with self.subTest(role=role), self.assertRaises(HTTPException):
                store.create_user(
                    p, username="forbidden", display_name="", tenant_id="hq", role=role, password="ForbiddenPassword9!"
                )
            with self.subTest(role=role), self.assertRaises(HTTPException):
                store.update_user(p, target.user_id, {"role": role})

    def test_readonly_pref_write_is_denied(self) -> None:
        self.prepare()
        p, _ = self.login()
        with self.assertRaises(HTTPException):
            preferences.write_preference(replace(p, readonly=True), "view_mode", "pro")

    def test_recovery_history_tampering_rejected(self) -> None:
        names = self.prepare()
        value = backup.snapshot_database(names["control"])
        for change in ("gap", "checksum", "name"):
            tampered = copy.deepcopy(value)
            rows = tampered["tables"]["schema_migrations"]
            if change == "gap":
                rows.pop(1)
            elif change == "checksum":
                rows[1]["checksum"] = "0" * 64
            else:
                rows[1]["name"] = "0002_other"
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_schema(tampered, "control")

    def test_new_schema_cannot_claim_legacy_version(self) -> None:
        names = self.prepare()
        value = backup.snapshot_database(names["control"])
        value["tables"]["schema_migrations"] = value["tables"]["schema_migrations"][:1]
        with self.assertRaises(ValueError):
            validate_schema(value, "control")

    def test_intermediate_trusted_versions_restore_under_current_code(self) -> None:
        names = self.fixture()
        with closing(db._raw_connection(names["control"])) as raw:
            conn = db.Connection(raw)
            conn.execute(migrations.HISTORY)
            for migration in migrations.discover("control"):
                migration.up(conn)
                conn.execute(
                    "INSERT INTO schema_migrations(version,name,checksum,applied_at) VALUES(?,?,?,?)",
                    (migration.version, migration.name, migration.checksum, db.now()),
                )
                conn.commit()
                value = backup.snapshot_database(names["control"])
                validate_schema(value, "control")
                if migration.version in (1, 2, 3, 4):
                    with runtime_guard(shared=False):
                        path = backup.create_upgrade_backup(store.list_tenants())
                        backup.restore_upgrade_backup(path)
                    after = backup.snapshot_database(names["control"])
                    self.assertEqual(value["hashes"], after["hashes"])
                    self.assertEqual(value["columns"], after["columns"])
                    self.assertEqual(value["triggers"], after["triggers"])
                    with self.assertRaises(migrations.MigrationError):
                        migrations.validate_runtime()

    def test_pending_partial_structure_is_never_a_trusted_backup(self) -> None:
        self.prepare()
        p, _ = self.login()

        def partially_create(ident: str, **kwargs: object) -> None:
            row = store.get_tenant(ident)
            with closing(db._raw_connection(row["db_name"], create=True)) as raw:
                db.Connection(raw).execute("CREATE TABLE incomplete(id INT)")
            raise RuntimeError("partial DDL")

        with (
            patch.object(migrations, "provision_tenant", side_effect=partially_create),
            self.assertRaises(HTTPException),
        ):
            directory.manage(p, OrgManageInput(action="create", name="Partial", category="subsidiary"))
        migrations.validate_runtime()
        with runtime_guard(shared=False), self.assertRaisesRegex(RuntimeError, "尚未登记备份分类"):
            backup.create_upgrade_backup(store.list_tenants())

    def test_active_partial_database_blocks_startup(self) -> None:
        self.prepare()
        tenant = store.get_tenant("dept-02")
        with closing(db._raw_connection(tenant["db_name"], create=True)) as raw:
            db.Connection(raw).execute("CREATE TABLE incomplete(id INT)")
        with self.assertRaises(migrations.MigrationError):
            migrations.validate_runtime()

    def test_password_flag_does_not_bypass_live_store_guards(self) -> None:
        self.prepare()
        p, _ = self.login("fixture.admin")
        flagged = replace(p, must_change_password=True)
        self.assertEqual(store.assert_principal_current(flagged, require_write=True).user_id, p.user_id)
        self.assertEqual(store.assert_job_principal_current(flagged, require_write=True).user_id, p.user_id)
        with store.authorized_control_connection(flagged, mode="standard") as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
        with self.assertRaises(HTTPException):
            store.assert_principal_current(replace(flagged, readonly=True), require_write=True)
        for changed in (
            replace(flagged, auth_version=p.auth_version + 1),
            replace(flagged, tenant_id="other"),
            replace(flagged, role="modeler"),
            replace(flagged, duties=("unknown",)),
        ):
            for check in (store.assert_principal_current, store.assert_job_principal_current):
                for write in (False, True):
                    with self.subTest(changed=changed, write=write), self.assertRaises(HTTPException):
                        check(changed, require_write=write)
            with self.assertRaises(HTTPException), store.authorized_control_connection(changed, mode="standard"):
                pass
        with (
            self.assertRaises(HTTPException),
            store.authorized_control_connection(p, mode="standard") as conn,
            conn.cursor() as cur,
        ):
            cur.execute("UPDATE sec_users SET duties=%s WHERE id=%s", (json.dumps(["unknown"]), p.user_id))
        self.assertEqual(store.assert_principal_current(p).duties, ())
        hq, _ = self.login()
        store.update_user(hq, p.user_id, {"is_active": False})
        for check in (store.assert_principal_current, store.assert_job_principal_current):
            with self.assertRaises(HTTPException):
                check(flagged, require_write=True)
        with self.assertRaises(HTTPException), store.authorized_control_connection(flagged, mode="standard"):
            pass

    def test_directory_visibility_and_atomic_role_updates(self) -> None:
        self.prepare()
        p, _ = self.login()
        local, raw = self.login("fixture.admin")
        self.assertEqual([row["id"] for row in directory.visible_directory(local)], ["dept-01"])
        self.assertNotIn("db_name", directory.visible_directory(p)[0])
        store.update_user(p, local.user_id, {"display_name": "Updated"})
        self.assertIsNone(store.authenticate_session(raw, "revoked"))

    def test_v2_http_routes_enforce_csrf_scope_and_shared_results(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from backend.api.v2.errors import install_errors
        from backend.api.v2.routes import router
        from backend.security.middleware import SecurityMiddleware

        self.prepare()
        p, raw = self.login()
        app = FastAPI()
        app.include_router(router)
        app.add_middleware(SecurityMiddleware)
        install_errors(app)
        with TestClient(app, base_url="http://127.0.0.1:8765") as client:
            client.cookies.set("ontology_session", raw)
            self.assertEqual(client.put("/api/v2/prefs/view_mode", json={"value": "pro"}).status_code, 403)
            client.headers.update({"x-csrf-token": store.csrf_token(raw), "origin": "http://127.0.0.1:8765"})
            result = client.get("/api/v2/users")
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()["items"], store.list_users(p))
            self.assertEqual(client.get("/api/v2/grants").json()["items"], store.list_grants(p))
            self.assertEqual(
                client.put("/api/v2/prefs/view_mode", json={"value": "pro"}).json(),
                {"preferences": {"view_mode": "pro"}},
            )
            self.assertEqual(
                client.put("/api/v2/prefs/view_mode", json={"value": "simple", "user_id": "forged"}).status_code, 422
            )
            self.assertEqual(client.get("/api/v2/users", headers={"X-Workspace-Id": "dept-01"}).status_code, 409)
            result = client.post(
                "/api/v2/capabilities/user.manage:invoke",
                json={
                    "action": "create",
                    "create": {
                        "username": "via.capability",
                        "tenant_id": "dept-01",
                        "role": "reviewer",
                        "password": "CapabilityPassword9!",
                    },
                },
            )
            self.assertEqual(result.status_code, 200, result.text)
            self.assertTrue(result.json()["ok"])
            self.assertEqual(self.login("via.capability", "CapabilityPassword9!")[0].role, "reviewer")
            result = client.get("/api/v2/audit", params={"action": "user_create"})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertTrue(all(row["action"] == "user_create" for row in result.json()["items"]))
            _local, raw = self.login("fixture.admin")
            client.cookies.set("ontology_session", raw)
            client.headers["x-csrf-token"] = store.csrf_token(raw)
            for route in ("grants", "audit"):
                self.assertEqual(client.get("/api/v2/" + route).status_code, 403)
            self.assertEqual(
                client.post(
                    "/api/v2/capabilities/org.manage:invoke", json={"action": "disable", "org_id": "hq"}
                ).status_code,
                403,
            )

    def test_restore_succeeds_without_set_any_definer_privilege(self) -> None:
        import secrets

        names = self.prepare()
        before = backup.snapshot_database(names["control"])
        username = "r17_restore_" + secrets.token_hex(5)
        password = "Restore9!" + secrets.token_hex(16)
        pattern = self.namespace.replace("_", "\\_") + "%"
        with closing(db._raw_connection("information_schema")) as raw:
            conn = db.Connection(raw)
            conn.execute("CREATE USER ?@'%' IDENTIFIED BY ?", (username, password))
            conn.execute(f"GRANT ALL PRIVILEGES ON `{pattern}`.* TO ?@'%'", (username,))
        with closing(db._raw_connection("information_schema")) as raw:
            conn = db.Connection(raw)
            original_trust = conn.execute("SELECT @@GLOBAL.log_bin_trust_function_creators").fetchone()[0]
            conn.execute("SET GLOBAL log_bin_trust_function_creators=1")
        try:
            with runtime_guard(shared=False):
                path = backup.create_upgrade_backup(store.list_tenants())
                with patch.dict(os.environ, {"MYSQL_USER": username, "MYSQL_PASSWORD": password}):
                    with closing(db._raw_connection("information_schema")) as raw:
                        grants = str([tuple(row) for row in db.Connection(raw).execute("SHOW GRANTS")])
                        self.assertNotIn("SET_ANY_DEFINER", grants)
                        self.assertNotIn("SUPER", grants)
                    backup.restore_upgrade_backup(path)
            after = backup.snapshot_database(names["control"])
            self.assertEqual(before["hashes"], after["hashes"])
            self.assertEqual(before["columns"], after["columns"])
            self.assertEqual(
                {key: normalized_ddl(value) for key, value in before["triggers"].items()},
                {key: normalized_ddl(value) for key, value in after["triggers"].items()},
            )
        finally:
            with closing(db._raw_connection("information_schema")) as raw:
                conn = db.Connection(raw)
                conn.execute("SET GLOBAL log_bin_trust_function_creators=?", (original_trust,))
                conn.execute("DROP USER ?@'%'", (username,))

    def test_hq_directory_http_preserves_legacy_null_org_id(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from backend.api.v2.errors import install_errors
        from backend.api.v2.routes import router
        from backend.security.middleware import SecurityMiddleware

        self.prepare()
        _principal, raw = self.login()
        app = FastAPI()
        app.include_router(router)
        app.add_middleware(SecurityMiddleware)
        install_errors(app)
        with TestClient(app, base_url="http://127.0.0.1:8765") as client:
            client.cookies.set("ontology_session", raw)
            response = client.get("/api/v2/orgs")
            self.assertEqual(response.status_code, 200, response.text)
            hq = next(row for row in response.json()["items"] if row["id"] == "hq")
            self.assertIsNone(hq["org_id"])
            self.assertEqual(hq["name"], store.get_tenant("hq")["name"])
            self.assertEqual(response.json()["total"], len(store.list_tenants()))

    def test_hq_rename_and_rollback_keep_real_root_and_legacy_catalog(self) -> None:
        self.prepare()
        person, raw_token = self.login()
        store.switch_workspace(person, "dept-01")
        person = store.authenticate_session(raw_token, "cross-workspace-rename")[0]
        self.assertEqual(person.tenant_id, "dept-01")
        before = store.get_tenant("hq")
        self.assertIsNone(before["org_id"])
        with use_context(person):
            directory.manage(person, OrgManageInput(action="update", org_id="hq", name="Renamed headquarters"))
        after = store.get_tenant("hq")
        self.assertEqual(
            (before["id"], before["org_id"], before["db_name"]), (after["id"], after["org_id"], after["db_name"])
        )
        with closing(db._raw_connection(after["db_name"])) as raw:
            conn = db.Connection(raw)
            entity = conn.execute("SELECT * FROM entities WHERE id='hq'").fetchone()
            self.assertEqual(entity["title"], "Renamed headquarters")
            self.assertEqual(entity["org_id"], "hq")
            history = json.loads(
                conn.execute("SELECT snapshot FROM versions WHERE entity_id='hq' ORDER BY id DESC LIMIT 1").fetchone()[
                    0
                ]
            )
            self.assertEqual(history["title"], before["name"])
            self.assertEqual((history["owner_tenant_id"], history["owner_org_id"]), ("hq", "hq"))
        hashes = backup.snapshot_database(after["db_name"])["hashes"]
        with (
            use_context(person),
            patch.object(store, "_audit", side_effect=RuntimeError("audit failed")),
            self.assertRaises(RuntimeError),
        ):
            directory.manage(person, OrgManageInput(action="update", org_id="hq", name="Must roll back"))
        self.assertEqual(store.get_tenant("hq"), after)
        self.assertEqual(backup.snapshot_database(after["db_name"])["hashes"], hashes)
