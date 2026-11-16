import hashlib
import unittest
from dataclasses import replace
from unittest.mock import patch

from fastapi.testclient import TestClient
from httpx import Response

from backend import main
from tests.security.test_permissions import principal


class InvokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.person = principal()
        self.client = TestClient(main.app, raise_server_exceptions=False)
        self.client.cookies.set("ontology_session", "a" * 32)
        self.client.headers["X-CSRF-Token"] = "csrf"
        self.patches = [
            patch(
                "backend.security.store.authenticate_session",
                side_effect=lambda *_: (self.person, hashlib.sha256(b"csrf").hexdigest()),
            ),
            patch("backend.security.store.append_audit"),
            patch("backend.security.store.authorized_read_tenants", return_value=[{"id": "sub-01"}]),
            patch(
                "backend.security.store.list_tenants",
                return_value=[{"id": "sub-01", "name": "机构", "category": "subsidiary", "db_name": "secret"}],
            ),
            patch("backend.api.v1.core.entities", return_value={"items": [], "total": 0, "page": 1, "page_size": 25}),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.addCleanup(self.client.close)

    def invoke(self, name: str, body: dict | None = None) -> Response:
        return self.client.post("/api/v2/capabilities/" + name + ":invoke", json=body or {})

    def test_five_capabilities(self) -> None:
        for name in ("session.whoami", "meta.kinds", "org.tree", "agent.readiness", "semantic.search"):
            with self.subTest(name=name):
                response = self.invoke(name)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertTrue(response.headers["X-Request-Id"])

    def test_unknown_capability_returns_404(self) -> None:
        self.assertEqual(self.invoke("missing.read").json()["error"]["code"], "NOT_FOUND")

    def test_request_cannot_impersonate(self) -> None:
        for key in ("principal", "tenant_id", "actor", "permissions", "database_name"):
            response = self.invoke("semantic.search", {key: "forged"})
            self.assertEqual(response.status_code, 422)

    def test_csrf_required(self) -> None:
        del self.client.headers["X-CSRF-Token"]
        response = self.invoke("meta.kinds")
        self.assertEqual(response.status_code, 403)
        self.assertTrue(response.json()["error"]["next_actions"])

    def test_forged_tenant_header(self) -> None:
        self.client.headers["X-Tenant-Id"] = "sub-02"
        self.assertEqual(self.invoke("meta.kinds").status_code, 403)

    def test_workspace_conflict(self) -> None:
        self.client.headers["X-Workspace-Id"] = "sub-02"
        self.assertEqual(self.invoke("meta.kinds").json()["error"]["code"], "WORKSPACE_CHANGED")

    def test_unauthenticated(self) -> None:
        self.client.cookies.clear()
        self.assertEqual(self.invoke("meta.kinds").json()["error"]["code"], "UNAUTHENTICATED")

    def test_unknown_v2_route_denied(self) -> None:
        self.assertEqual(self.client.get("/api/v2/unregistered").json()["error"]["code"], "PERMISSION_DENIED")

    def test_legacy_error_format(self) -> None:
        self.client.cookies.clear()
        data = self.client.get("/api/entities").json()
        self.assertIn("detail", data)
        self.assertNotIn("error", data)

    def test_internal_error_is_safe(self) -> None:
        with patch("backend.security.store.list_tenants", side_effect=RuntimeError("secret body")):
            response = self.invoke("org.tree")
        self.assertEqual(response.json()["error"]["code"], "INTERNAL_ERROR")
        self.assertNotIn("secret body", response.text)
        self.assertEqual(response.json()["error"]["request_id"], response.headers["X-Request-Id"])

    def test_directory_excludes_database_names(self) -> None:
        self.assertNotIn("secret", self.invoke("org.tree").text)

    def test_new_role_can_read_v2_but_not_v1(self) -> None:
        for role in ("viewer", "analyst"):
            self.person = replace(self.person, role=role)
            self.assertEqual(self.invoke("meta.kinds").status_code, 200)
            self.assertEqual(self.client.get("/api/entities").status_code, 403)

    def test_readonly_can_invoke_all_read_capabilities(self) -> None:
        self.person = replace(self.person, readonly=True)
        with patch(
            "backend.federation.search",
            return_value={
                "items": [],
                "total": 0,
                "page": 1,
                "page_size": 25,
                "partial": False,
            },
        ):
            for name in ("session.whoami", "meta.kinds", "org.tree", "agent.readiness", "semantic.search"):
                self.assertEqual(self.invoke(name).status_code, 200)

    def test_conflict_has_unified_error(self) -> None:
        from backend.core.errors import ApiError

        with patch("backend.security.store.list_tenants", side_effect=ApiError("VERSION_CONFLICT")):
            response = self.invoke("org.tree")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "VERSION_CONFLICT")
        self.assertTrue(response.json()["error"]["next_actions"])

    def test_validation_does_not_echo_secrets(self) -> None:
        response = self.invoke("semantic.search", {"q": {"password": "private-value"}})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("private-value", response.text)
