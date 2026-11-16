import unittest
from dataclasses import replace
from unittest.mock import patch

from backend.security.context import Principal
from backend.security.permissions import ALL_PERMISSIONS, PermissionScope, permissions_for


def principal(role: str = "admin", **changes: object) -> Principal:
    return replace(Principal("u", "user", "sub-01", "sub-01", role, False, "s", 1, "test_db"), **changes)


class PermissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.grants = patch("backend.security.store.authorized_read_tenants", return_value=[{"id": "sub-01"}])
        self.grants.start()
        self.addCleanup(self.grants.stop)

    def test_matrix(self) -> None:
        expected = {
            "source.submit": "admin modeler reviewer analyst",
            "build.edit": "admin modeler",
            "build.submit": "admin modeler",
            "review.decide": "admin reviewer",
            "module.manage": "admin",
            "scenario.propose": "admin modeler reviewer analyst",
            "scenario.manage": "admin",
            "scenario.build": "admin",
            "scenario.contribute": "admin",
            "agent.scenario.use": "admin",
            "source.manage": "admin",
            "agent.builder.use": "admin modeler",
            "agent.analyst.use": "admin modeler reviewer analyst viewer",
            "artifact.create": "admin modeler reviewer analyst viewer",
            "artifact.read": "admin modeler reviewer analyst viewer",
            "feedback.create": "admin modeler reviewer analyst viewer",
            "draft.read": "admin modeler reviewer",
            "data.restricted.read": "",
            "admin.users": "admin",
            "admin.model": "admin",
            "admin.demo": "admin",
            "admin.settings": "admin",
            "audit.read": "",
            "admin.org": "",
            "federation.steward": "",
            "federation.read": "",
        }
        self.assertEqual(set(expected), ALL_PERMISSIONS)
        for role in ("admin", "modeler", "reviewer", "analyst", "viewer"):
            actual = permissions_for(principal(role))
            for key, roles in expected.items():
                with self.subTest(role=role, permission=key):
                    self.assertEqual(key in actual, role in roles.split())

    def test_unknown_role(self) -> None:
        self.assertEqual(permissions_for(principal("superuser")), set())

    def test_headquarters_superadmin(self) -> None:
        self.assertTrue(
            {"audit.read", "admin.org"} <= permissions_for(principal(home_tenant_id="hq", is_superadmin=True))
        )

    def test_superadmin_flag_outside_headquarters(self) -> None:
        self.assertNotIn("admin.org", permissions_for(principal(is_superadmin=True)))

    def test_headquarters_without_superadmin(self) -> None:
        self.assertNotIn("audit.read", permissions_for(principal(home_tenant_id="hq")))

    def test_group_steward_headquarters_only(self) -> None:
        for home, allowed in [("hq", True), ("sub-01", False)]:
            self.assertEqual(
                "federation.steward"
                in permissions_for(principal("reviewer", home_tenant_id=home, duties=("group_steward",))),
                allowed,
            )

    def test_federation_requires_cross_tenant_grant(self) -> None:
        with patch("backend.security.store.authorized_read_tenants", return_value=[{"id": "sub-02"}]):
            self.assertIn("federation.read", permissions_for(principal("viewer")))

    def test_scope_owner_permissions(self) -> None:
        scope = PermissionScope("sub-01", "SCN-A", "owner")
        actual = permissions_for(principal("analyst"), scope)
        self.assertTrue({"scenario.manage", "scenario.build", "scenario.contribute", "agent.scenario.use"} <= actual)
        self.assertFalse({"review.decide", "module.manage", "admin.users"} & actual)

    def test_scope_cobuilder_permissions(self) -> None:
        actual = permissions_for(principal("reviewer"), PermissionScope("sub-01", "SCN-A", "builder"))
        self.assertIn("scenario.build", actual)
        self.assertNotIn("scenario.manage", actual)

    def test_viewer_scope_cannot_build(self) -> None:
        actual = permissions_for(principal("viewer"), PermissionScope("sub-01", "SCN-A", "owner"))
        self.assertNotIn("scenario.build", actual)
        self.assertIn("scenario.contribute", actual)

    def test_scope_cannot_cross_tenant(self) -> None:
        actual = permissions_for(principal("analyst"), PermissionScope("sub-02", "SCN-A", "owner"))
        self.assertNotIn("scenario.build", actual)

    def test_scope_grants_do_not_escape(self) -> None:
        person = principal("modeler")
        permissions_for(person, PermissionScope("sub-01", "SCN-A", "owner"))
        self.assertNotIn("scenario.manage", permissions_for(person))

    def test_restricted_requires_explicit_grant(self) -> None:
        self.assertNotIn("data.restricted.read", permissions_for(principal()))

    def test_legacy_roles_unchanged(self) -> None:
        from backend.security import policy, store

        self.assertEqual(set(policy.ROLES), {"admin", "modeler", "reviewer"})
        self.assertEqual(set(store.ROLES), {"admin", "modeler", "reviewer"})

    def test_readonly_permissions_exclude_writes_and_drafts(self) -> None:
        actual = permissions_for(principal(readonly=True))
        self.assertLessEqual(actual, {"agent.analyst.use", "artifact.read", "federation.read"})
        self.assertNotIn("draft.read", actual)

    def test_scope_explicit_restricted_grant(self) -> None:
        scope = PermissionScope("sub-01", restricted_read=True)
        self.assertIn("data.restricted.read", permissions_for(principal("analyst"), scope))
        self.assertNotIn("data.restricted.read", permissions_for(principal("viewer"), scope))

    def test_module_owner_is_not_scenario_owner(self) -> None:
        scope = PermissionScope("sub-01", module_owner_id="u")
        self.assertIn("module.manage", permissions_for(principal("modeler"), scope))
        self.assertNotIn("module.manage", permissions_for(principal("analyst"), scope))
