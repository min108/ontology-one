import unittest
from unittest.mock import patch

from backend.api.v2.routes import registry
from backend.capabilities.context import CapabilityContext
from backend.core.errors import ApiError
from backend.security.permissions import permissions_for
from tests.security.test_permissions import principal


class ContractTests(unittest.TestCase):
    def test_all_five_contracts_have_valid_models(self) -> None:
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
                "org.manage",
                "user.manage",
                "grant.manage",
                "audit.query",
            },
        )
        for item in registry.entries.values():
            for model in (item.input, item.output):
                self.assertEqual(model.model_json_schema()["additionalProperties"], False)
            mutation = item.id in {"org.manage", "user.manage", "grant.manage", "model.configure", "backup.restore"}
            self.assertEqual(item.effect, "state_change" if mutation else "read")
            self.assertEqual(item.confirm, "L3" if mutation or item.id == "backup.export" else "L0")
            self.assertEqual(item.tool_name, item.id.replace(".", "_"))

    def test_every_role_matches_declared_permissions(self) -> None:
        for role in ("admin", "modeler", "reviewer", "analyst", "viewer", "unknown"):
            person = principal(role)
            with patch("backend.security.store.authorized_read_tenants", return_value=[]):
                ctx = CapabilityContext(person, permissions=frozenset(permissions_for(person)))
            for item in registry.entries.values():
                with (
                    self.subTest(role=role, capability=item.id),
                    patch("backend.security.store.append_audit"),
                    patch("backend.capabilities.registry.check_all", side_effect=RuntimeError("admitted")),
                ):
                    # Stop at the business call after checking the declaration's permissions.
                    expected = (
                        RuntimeError if role != "unknown" and set(item.permissions) <= ctx.permissions else ApiError
                    )
                    with self.assertRaises(expected):
                        registry.invoke(
                            item.id,
                            ctx,
                            {
                                "model.configure": {"base_url": "https://model.test/anthropic", "model": "test"},
                                "user.manage": {
                                    "action": "update",
                                    "user_id": "target",
                                    "changes": {"display_name": "New"},
                                },
                                "org.manage": {"action": "disable", "org_id": "sub-01"},
                                "grant.manage": {"action": "revoke", "grant_id": "grant"},
                            }.get(item.id, {}),
                        )

    def test_kind_vocabulary_has_sixteen_entries(self) -> None:
        from backend.capabilities.read import KINDS

        self.assertEqual(len(KINDS), 16)
        self.assertEqual(KINDS["data"], "数据项")
        self.assertEqual(KINDS["analysis"], "分析记录")

    def test_kind_labels_share_the_generated_vocabulary(self) -> None:
        from backend.capabilities.read import KINDS
        from backend.core.terms import TERMS

        self.assertEqual(KINDS, {key: TERMS["kinds"][key] for key in KINDS})
