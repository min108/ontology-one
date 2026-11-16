"""A route becomes accessible only after an explicit policy review."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import APIRouter, FastAPI

from scripts.checks.check_policy_coverage import check_routes, default_deny_errors


class PolicyCoverageTests(unittest.TestCase):
    def test_registered_route_passes(self) -> None:
        app = FastAPI()
        app.add_api_route("/api/entities/{eid}", lambda: None, methods=["GET"])
        self.assertEqual(check_routes(app), [])

    def test_unknown_route_fails(self) -> None:
        app = FastAPI()
        app.add_api_route("/api/unregistered", lambda: None, methods=["GET"])
        self.assertIn("GET /api/unregistered", "\n".join(check_routes(app)))

    def test_method_is_not_inferred_from_get(self) -> None:
        app = FastAPI()
        app.add_api_route("/api/catalog", lambda: None, methods=["DELETE"])
        self.assertTrue(check_routes(app))

    def test_nested_hidden_route_fails(self) -> None:
        child = APIRouter()
        child.add_api_route("/unknown", lambda: None, include_in_schema=False)
        parent = APIRouter()
        parent.include_router(child, prefix="/nested")
        app = FastAPI()
        app.include_router(parent, prefix="/api")
        self.assertIn("/api/nested/unknown", "\n".join(check_routes(app)))

    def test_control_prefix_does_not_register_new_route(self) -> None:
        app = FastAPI()
        app.add_api_route("/api/admin/unreviewed", lambda: None)
        self.assertTrue(check_routes(app))

    def test_parameter_rule_does_not_register_new_static_endpoint(self) -> None:
        app = FastAPI()
        app.add_api_route("/api/entities/unreviewed", lambda: None)
        self.assertTrue(check_routes(app))

    def test_unknown_business_api_denied_for_every_role_and_method(self) -> None:
        self.assertEqual(default_deny_errors(), [])

    def test_new_websocket_transport_is_not_silently_skipped(self) -> None:
        app = FastAPI()
        app.add_api_websocket_route("/api/unreviewed-socket", lambda: None)
        self.assertTrue(check_routes(app))

    def test_permissive_authorizer_is_detected(self) -> None:
        self.assertTrue(default_deny_errors(lambda *_args: SimpleNamespace(allowed=True)))

    def test_registered_v2_route_passes_with_a_different_parameter_name(self) -> None:
        app = FastAPI()
        app.add_api_route("/api/v2/capabilities/{capability_id}:invoke", lambda: None, methods=["POST"])
        self.assertEqual(check_routes(app), [])

    def test_v2_registration_cannot_be_inferred_from_its_prefix(self) -> None:
        app = FastAPI()
        app.add_api_route("/api/v2/unreviewed", lambda: None, methods=["POST"])
        self.assertIn("POST /api/v2/unreviewed", "\n".join(check_routes(app)))

    def test_v2_registration_is_method_specific(self) -> None:
        app = FastAPI()
        app.add_api_route("/api/v2/capabilities/{id}:invoke", lambda: None, methods=["GET"])
        self.assertTrue(check_routes(app))

    def test_removed_v2_registration_fails(self) -> None:
        app = FastAPI()
        app.add_api_route("/api/v2/capabilities/{id}:invoke", lambda: None, methods=["POST"])
        with patch("backend.security.policy.V2_RULES", ()):
            self.assertTrue(check_routes(app))

    def test_legacy_read_table_cannot_register_a_v2_endpoint(self) -> None:
        app = FastAPI()
        app.add_api_route("/api/v2/unreviewed", lambda: None, methods=["GET"])
        with patch("backend.security.policy.READ_PATHS", ("/api/v2/unreviewed",)):
            self.assertTrue(check_routes(app))

    def test_v2_default_allow_is_detected_for_all_five_roles(self) -> None:
        with patch("backend.security.policy.authorize_v2", return_value=SimpleNamespace(allowed=True)):
            errors = default_deny_errors()
        self.assertTrue(errors)
        for role in ("admin", "modeler", "reviewer", "analyst", "viewer"):
            self.assertTrue(any(error.startswith(role + " ") and "/api/v2/" in error for error in errors))
