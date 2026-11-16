import re
import unittest

from backend import main
from backend.security import policy
from tests.security.test_permissions import principal


class CoverageTests(unittest.TestCase):
    def test_all_business_routes_are_registered(self) -> None:
        from backend.security.middleware import PUBLIC

        for path, methods in main.app.openapi()["paths"].items():
            if not path.startswith("/api/"):
                continue
            for method in methods:
                verb = method.upper()
                with self.subTest(method=verb, path=path):
                    if (verb, path) in PUBLIC or policy.is_control_path(path):
                        continue

                    def normalize(value: str) -> str:
                        return re.sub(r"\{[^}]+\}", "{parameter}", value)

                    canonical = normalize(path)
                    registered = (
                        verb in {"GET", "HEAD"} and canonical in {normalize(value) for value in policy.READ_PATHS}
                    ) or any(
                        verb == candidate[0] and canonical == normalize(candidate[1])
                        for candidate in policy.WRITE_RULES
                    )
                    registered = registered or any(
                        verb == candidate[0] and canonical == normalize(candidate[1]) for candidate in policy.V2_RULES
                    )
                    # The old proxy deliberately declares catch-all paths: every concrete route is still default-deny.
                    registered = registered or path in {"/api/agent/{path}", "/api/harness/runs{tail}"}
                    self.assertTrue(registered)

    def test_new_roles_are_denied_all_legacy_business_routes(self) -> None:
        for role in ("analyst", "viewer"):
            person = principal(role)
            for path in policy.READ_PATHS:
                self.assertFalse(policy.authorize(person, "GET", path).allowed)
            for method, path, _ in policy.WRITE_RULES:
                self.assertFalse(policy.authorize(person, method, path, {}).allowed)

    def test_v2_unregistered_is_default_deny(self) -> None:
        for role in ("admin", "modeler", "reviewer", "analyst", "viewer"):
            self.assertFalse(policy.authorize(principal(role), "GET", "/api/v2/new-route").allowed)

    def test_documentation_remains_admin_only(self) -> None:
        for role in ("admin", "modeler", "reviewer", "analyst", "viewer"):
            self.assertEqual(policy.authorize(principal(role), "GET", "/openapi.json").allowed, role == "admin")
