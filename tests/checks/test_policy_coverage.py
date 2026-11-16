"""A route becomes accessible only after an explicit policy review."""

import unittest

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
        from types import SimpleNamespace

        self.assertTrue(default_deny_errors(lambda *_args: SimpleNamespace(allowed=True)))
