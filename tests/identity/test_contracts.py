"""Identity API shapes are strict, compatible, and page-only for mutations."""

import unittest
from unittest.mock import patch

from pydantic import ValidationError

from backend.security.context import Principal


class IdentityContracts(unittest.TestCase):
    def test_registered_management_capabilities(self) -> None:
        from backend.api.v2.routes import router
        from backend.capabilities.registry import registry

        self.assertTrue(router.routes)
        self.assertEqual(
            set(registry.entries),
            {
                "meta.kinds",
                "session.whoami",
                "org.tree",
                "agent.readiness",
                "semantic.search",
                "org.manage",
                "user.manage",
                "grant.manage",
                "audit.query",
            },
        )
        for name in ("org.manage", "user.manage", "grant.manage"):
            item = registry.entries[name]
            self.assertEqual((item.effect, item.confirm, item.assistants, item.mcp), ("state_change", "L3", (), False))

    def test_org_creation_requires_name_and_category(self) -> None:
        from backend.api.v2.orgs import OrgManageInput

        with self.assertRaises(ValidationError):
            OrgManageInput(action="create")

    def test_org_delete_is_not_an_action(self) -> None:
        from backend.api.v2.orgs import OrgManageInput

        with self.assertRaises(ValidationError):
            OrgManageInput(action="delete", org_id="hq")

    def test_user_create_never_accepts_new_roles(self) -> None:
        from backend.api.v2.users import UserManageInput

        for role in ("viewer", "analyst"):
            with self.subTest(role=role), self.assertRaises(ValidationError):
                UserManageInput(
                    action="create",
                    create={"username": "sample", "tenant_id": "hq", "role": role, "password": "SyntheticPassword9!"},
                )

    def test_user_duties_not_mutable(self) -> None:
        from backend.api.v2.users import UserManageInput

        with self.assertRaises(ValidationError):
            UserManageInput(action="update", user_id="other", changes={"duties": ["group_steward"]})

    def test_user_update_requires_target(self) -> None:
        from backend.api.v2.users import UserManageInput

        with self.assertRaises(ValidationError):
            UserManageInput(action="update", changes={"display_name": "New"})

    def test_grant_set_requires_payload(self) -> None:
        from backend.api.v2.grants import GrantManageInput

        with self.assertRaises(ValidationError):
            GrantManageInput(action="set")

    def test_prefs_reject_caller_identity(self) -> None:
        from backend.api.v2.prefs import PreferenceInput

        with self.assertRaises(ValidationError):
            PreferenceInput(value="simple", user_id="other")

    def test_prefs_invalid_key_and_mode(self) -> None:
        from fastapi import HTTPException

        from backend.security.preferences import validate_preference

        for key, value in (
            ("unknown", "x"),
            ("view_mode", "professional"),
            ("density", "tiny"),
            ("autopilot_default", "automatic"),
        ):
            with self.subTest(key=key), self.assertRaises(HTTPException):
                validate_preference(key, value)

    def test_v2_old_password_flag_does_not_block(self) -> None:
        from backend.security.policy import authorize_v2

        person = Principal(
            user_id="u",
            username="u",
            home_tenant_id="hq",
            tenant_id="hq",
            role="admin",
            is_superadmin=True,
            session_id="s",
            auth_version=1,
            database_name="test",
            must_change_password=True,
        )
        with patch("backend.security.permissions.store.authorized_read_tenants", return_value=[]):
            self.assertTrue(authorize_v2(person, "POST", "/api/v2/capabilities/session.whoami:invoke").allowed)

    def test_restore_registry_does_not_require_foreign_definer(self) -> None:
        from backend.infra.recovery_schema import trusted_schema

        for version in (1, 2, 3, 4):
            for statement in trusted_schema("control", version)["triggers"].values():
                with self.subTest(version=version):
                    self.assertNotIn("DEFINER", statement.upper())
