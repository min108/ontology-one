"""R1-8 contracts written before adapters: real gateway, no permissive network stubs."""

import hashlib
import unittest
from dataclasses import replace
from unittest.mock import patch

from fastapi.testclient import TestClient
from httpx import Response as ClientResponse
from starlette.responses import Response

from backend import main
from backend.capabilities.context import CapabilityContext
from backend.capabilities.registry import registry
from backend.core.errors import ApiError
from tests.security.test_permissions import principal


class ManagementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.person = principal("admin")
        self.client = TestClient(main.app, raise_server_exceptions=False)
        self.client.cookies.set("ontology_session", "a" * 32)
        self.client.headers.update({"X-CSRF-Token": "csrf", "X-Workspace-Id": self.person.tenant_id})
        for item in (
            patch(
                "backend.security.store.authenticate_session",
                side_effect=lambda *_: (self.person, hashlib.sha256(b"csrf").hexdigest()),
            ),
            patch("backend.security.store.assert_principal_current", side_effect=lambda p: p),
            patch("backend.security.store.append_audit"),
            patch("backend.security.store.authorized_read_tenants", return_value=[]),
        ):
            item.start()
            self.addCleanup(item.stop)
        self.addCleanup(self.client.close)

    def invoke(self, ident: str, body: dict | None = None) -> ClientResponse:
        return self.client.post(f"/api/v2/capabilities/{ident}:invoke", json=body or {})

    def test_exact_eight_registry(self) -> None:
        self.assertEqual(
            set(registry.entries),
            {
                "session.whoami",
                "meta.kinds",
                "org.tree",
                "agent.readiness",
                "semantic.search",
                "model.configure",
                "backup.export",
                "backup.restore",
            },
        )

    def test_configuration_calls_original_business_function_without_echo(self) -> None:
        with patch(
            "backend.skill_harness._save_configuration",
            return_value={"configured": True, "model": "test", "api_key": "DO-NOT-ECHO"},
        ) as save:
            response = self.invoke(
                "model.configure",
                {"base_url": "https://model.test/anthropic", "model": "test", "api_key": "DO-NOT-ECHO"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(save.call_args.args[0].api_key, "DO-NOT-ECHO")
        self.assertNotIn("DO-NOT-ECHO", response.text)

    def test_configuration_clear_key_is_forwarded(self) -> None:
        with patch("backend.skill_harness._save_configuration", return_value={"configured": False}) as save:
            response = self.invoke(
                "model.configure", {"base_url": "https://model.test/anthropic", "model": "test", "clear_key": True}
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(save.call_args.args[0].clear_key)

    def test_invalid_secret_never_appears_in_validation_or_audit(self) -> None:
        with patch("backend.security.store.append_audit") as audit:
            response = self.invoke("model.configure", {"api_key": {"nested": "DO-NOT-ECHO"}})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("DO-NOT-ECHO", response.text + str(audit.call_args_list))

    def test_all_three_capabilities_deny_non_admin(self) -> None:
        for role in ("modeler", "reviewer", "analyst", "viewer"):
            self.person = replace(self.person, role=role)
            for ident in ("model.configure", "backup.export", "backup.restore"):
                self.assertEqual(self.invoke(ident).status_code, 403, (role, ident))

    def test_readonly_administrator_cannot_write_or_export(self) -> None:
        self.person = replace(self.person, readonly=True)
        for ident in ("model.configure", "backup.export", "backup.restore"):
            self.assertEqual(self.invoke(ident).status_code, 403)

    def test_original_roles_can_read_model_status(self) -> None:
        with patch(
            "backend.skill_harness.harness_status", return_value={"configured": False, "api_key": "DO-NOT-ECHO"}
        ):
            for role in ("modeler", "reviewer", "admin"):
                self.person = replace(self.person, role=role)
                response = self.client.get("/api/v2/system/model")
                self.assertEqual(response.status_code, 200, response.text)
                self.assertNotIn("DO-NOT-ECHO", response.text)

    def test_cross_institution_validation_rejected_before_call(self) -> None:
        self.person = replace(self.person, home_tenant_id="hq", is_superadmin=True)
        with patch("backend.skill_harness.test_configuration") as verify:
            response = self.client.post("/api/v2/system/model:test")
        self.assertEqual(response.status_code, 403, response.text)
        verify.assert_not_called()

    def test_same_institution_verification_uses_original_runtime(self) -> None:
        with patch(
            "backend.skill_harness.test_configuration", return_value={"ok": True, "status": {"verified": True}}
        ) as verify:
            response = self.client.post("/api/v2/system/model:test")
        self.assertEqual(response.status_code, 200, response.text)
        verify.assert_called_once()

    def test_online_restore_rejected_before_any_file_access(self) -> None:
        with patch("scripts.restore_backup.restore_snapshot") as restore:
            response = self.invoke("backup.restore", {"archive": "/outside/file.zip", "reason": "演练"})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"]["code"], "PRECONDITION_FAILED")
        self.assertIn("离线", response.json()["error"]["message"])
        restore.assert_not_called()

    def test_export_descriptor_binds_current_workspace(self) -> None:
        response = self.invoke("backup.export")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["download_url"], f"/api/v2/system/backup?workspace_id={self.person.tenant_id}")
        self.assertNotIn("path", response.text)

    def test_binary_export_uses_original_archive_service(self) -> None:
        with patch("backend.system.backup", return_value=Response(b"archive", media_type="application/zip")) as export:
            response = self.client.get(f"/api/v2/system/backup?workspace_id={self.person.tenant_id}")
        self.assertEqual(response.content, b"archive")
        export.assert_called_once()

    def test_binary_export_rejects_stale_workspace(self) -> None:
        with patch("backend.system.backup") as export:
            response = self.client.get("/api/v2/system/backup?workspace_id=another")
        self.assertEqual(response.status_code, 409)
        export.assert_not_called()

    def test_source_export_requires_hq_superadmin(self) -> None:
        with patch("backend.system.release", return_value=Response(b"source")) as export:
            self.assertEqual(self.client.get("/api/v2/system/release").status_code, 403)
            self.person = replace(self.person, home_tenant_id="hq", is_superadmin=True)
            self.assertEqual(self.client.get("/api/v2/system/release").status_code, 200)
        export.assert_called_once()

    def test_offline_binding_requires_exclusive_lock(self) -> None:
        ctx = CapabilityContext(self.person, binding="offline", permissions=frozenset({"admin.settings"}))
        with patch("backend.security.store.append_audit"), self.assertRaises(ApiError) as raised:
            registry.invoke("backup.restore", ctx, {"archive": "/outside/file.zip", "reason": "演练"})
        self.assertEqual(raised.exception.code, "MAINTENANCE_LOCKED")

    def test_new_read_route_does_not_relax_unknown_policy(self) -> None:
        self.assertEqual(self.client.get("/api/v2/system/unknown").status_code, 403)

    def test_model_validation_requires_csrf(self) -> None:
        del self.client.headers["X-CSRF-Token"]
        self.assertEqual(self.client.post("/api/v2/system/model:test").status_code, 403)

    def test_metadata_exposes_versions_without_database_or_paths(self) -> None:
        with (
            patch("backend.security.store.get_tenant", return_value={"status": "active", "db_name": "PRIVATE_DB"}),
            patch("backend.security.store._config", return_value={"database": "PRIVATE_CONTROL"}),
            patch(
                "backend.infra.migrations.migration_status",
                return_value={"current": 1, "pending": [], "tables": ["private"]},
            ),
        ):
            response = self.client.get("/api/v2/system/metadata")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["workspace"], {"current": 1, "pending": []})
        self.assertNotIn("PRIVATE", response.text)
        self.assertNotIn("private", response.text)

    def test_backup_list_binds_deployment_and_does_not_expose_paths(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory(dir="work") as folder:
            root = Path(folder)
            for name, control in (("upgrade-1", "ours"), ("upgrade-2", "others")):
                (root / name).mkdir()
                (root / name / "manifest.json").write_text(json.dumps({"control_database": control}))
            snapshot = {"tables": {"schema_migrations": [{"version": 1}]}}
            with (
                patch("backend.infra.backup.backup_root", return_value=root),
                patch("backend.security.store._config", return_value={"database": "ours"}),
                patch(
                    "backend.infra.backup.verify_upgrade_backup",
                    return_value=({"created_at": "2026-01-01", "control_database": "ours"}, {"ours": snapshot}),
                ) as verify,
            ):
                response = self.client.get("/api/v2/system/backups")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(len(response.json()["items"]), 1)
            self.assertNotIn(folder, response.text)
            self.assertNotIn("ours", response.text)
            verify.assert_called_once_with(root / "upgrade-1")

    def test_recipe_binding_cannot_restore_even_with_maintenance_lock(self) -> None:
        ctx = CapabilityContext(self.person, binding="recipe", permissions=frozenset({"admin.settings"}))
        with (
            patch("backend.security.store.append_audit"),
            patch("scripts.restore_backup.restore_snapshot") as restore,
            self.assertRaises(ApiError) as raised,
        ):
            registry.invoke("backup.restore", ctx, {"archive": "/outside/file.zip", "reason": "演练"})
        self.assertEqual(raised.exception.code, "PRECONDITION_FAILED")
        restore.assert_not_called()

    def test_sensitive_body_is_stream_bounded_before_validation(self) -> None:
        with patch("backend.skill_harness._save_configuration") as save:
            response = self.client.post(
                "/api/v2/capabilities/model.configure:invoke",
                content=iter([b'{"api_key":"', b"x" * 17000, b'"}']),
                headers={"Content-Type": "application/json"},
            )
        self.assertEqual(response.status_code, 413, response.text)
        self.assertEqual(response.json()["error"]["code"], "UPLOAD_REJECTED")
        save.assert_not_called()

    def test_sensitive_body_requires_json_content_type(self) -> None:
        response = self.client.post(
            "/api/v2/capabilities/model.configure:invoke", content=b"{}", headers={"Content-Type": "text/plain"}
        )
        self.assertEqual(response.status_code, 415)

    def test_every_online_binding_is_ineligible_for_restore(self) -> None:
        for binding in ("rest", "tool", "mcp", "recipe"):
            with self.subTest(binding=binding), patch("scripts.restore_backup.restore_snapshot") as restore:
                ctx = CapabilityContext(
                    self.person, binding=binding, permissions=frozenset({"admin.settings"}), assistant="builder"
                )
                with self.assertRaises(ApiError):
                    registry.invoke("backup.restore", ctx, {"archive": "/not-opened.zip", "reason": "演练"})
                restore.assert_not_called()
