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
                "semantic.resolve",
                "semantic.neighbors",
                "evidence.trace",
                "impact.analyze",
                "recipe.start",
                "recipe.status",
                "changeset.get",
                "changeset.confirm_facts",
                "changeset.retain_source",
                "changeset.decide_items",
                "changeset.apply",
                "changeset.apply_and_submit",
                "review.list",
                "review.get",
                "review.decide",
                "artifact.create",
                "artifact.render",
                "artifact.get",
                "artifact.list",
                "element.list",
                "element.get",
                "element.submit_draft",
                "element.create_draft",
                "element.update_draft",
                "element.start_revision",
                "element.restore_version",
                "element.discard_draft",
                "relation.propose",
                "relation.decide",
                "semantic.describe",
                "quality.check_gates",
                "quality.waive_gates",
                "rule.test_cases",
                "module.save",
                "module.move_elements",
                "release.create_draft",
                "release.submit",
                "release.publish",
                "release.withdraw",
                "org.manage",
                "user.manage",
                "grant.manage",
                "audit.query",
                "model.configure",
                "backup.export",
                "backup.restore",
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

    def test_hq_directory_contract_preserves_legacy_null_org_id(self) -> None:
        import json
        from pathlib import Path

        from backend.api.v2.orgs import Organization

        fixture = json.loads((Path(__file__).parents[1] / "fixtures/v070/control.json").read_text())
        row = next(row for row in fixture["tables"]["sec_tenants"] if row["id"] == "hq")
        self.assertIsNone(row["org_id"])
        value = Organization.model_validate({key: row[key] for key in Organization.model_fields if key in row})
        self.assertIsNone(value.org_id)

    def test_hq_snapshot_uses_actual_root_id_and_entity_ownership(self) -> None:
        import json
        from pathlib import Path
        from unittest.mock import MagicMock

        from backend.security.context import use_context
        from backend.security.directory import _snapshot_root
        from tests.security.test_permissions import principal

        fixture = Path(__file__).parents[1] / "fixtures/v070"
        catalog = json.loads((fixture / "control.json").read_text())
        row = next(row for row in catalog["tables"]["sec_tenants"] if row["id"] == "hq")
        entity = next(
            row for row in json.loads((fixture / "hq.json").read_text())["tables"]["entities"] if row["id"] == "hq"
        )
        entity["org_id"] = "historical-business-group"
        cursor = MagicMock()
        cursor.fetchone.return_value = {"db_name": "control"}
        tenant = cursor.connection.cursor.return_value
        tenant.fetchone.return_value = entity
        from pymysql.err import ProgrammingError

        tenant.__enter__.return_value.execute.side_effect = ProgrammingError(1146, "pre-migration fixture")
        with use_context(principal(home_tenant_id="hq", tenant_id="dept-01", is_superadmin=True)):
            _snapshot_root(cursor, row, {"name": "Updated headquarters"}, "directory rename")
        self.assertEqual(
            [call.args for call in cursor.connection.select_db.call_args_list], [(row["db_name"],), ("control",)]
        )
        self.assertEqual(tenant.execute.call_args_list[0].args[1], ("hq",))
        snapshot = json.loads(tenant.execute.call_args_list[1].args[1][2])
        self.assertEqual(snapshot["owner_tenant_id"], "hq")
        self.assertEqual(snapshot["owner_org_id"], "hq")
        self.assertEqual(snapshot["org_id"], entity["org_id"])
        self.assertIsNone(row["org_id"])
