"""Check the shared capability boundary for every changed A.1/A.2 business writer."""

import unittest
from unittest.mock import patch

from backend.api.v2.routes import registry
from backend.capabilities.context import CapabilityContext
from backend.core.errors import ApiError
from backend.security import policy
from tests.security.test_permissions import principal


class WritePermissions(unittest.TestCase):
    def test_readonly_and_missing_business_permission_stop_each_writer(self) -> None:
        writers = {
            "recipe.start": "build.edit",
            "changeset.decide_items": "build.edit",
            "changeset.confirm_facts": "build.edit",
            "changeset.retain_source": "build.edit",
            "changeset.apply": "build.edit",
            "changeset.apply_and_submit": "build.submit",
            "element.create_draft": "build.edit",
            "element.update_draft": "build.edit",
            "element.start_revision": "build.edit",
            "element.restore_version": "build.edit",
            "element.discard_draft": "build.edit",
            "element.submit_draft": "build.submit",
            "review.decide": "review.decide",
            "quality.waive_gates": "review.decide",
            "relation.propose": "build.edit",
            "relation.decide": "review.decide",
        }
        with patch("backend.capabilities.registry.store.append_audit"):
            for capability, permission in writers.items():
                self.assertIn(capability, registry.entries)
                self.assertEqual(registry.entries[capability].permissions, (permission,))
                for read_only in (False, True):
                    context = CapabilityContext(
                        principal(readonly=read_only), permissions=frozenset({permission} if read_only else ())
                    )
                    with self.subTest(capability=capability, readonly=read_only):
                        with self.assertRaises(ApiError) as error:
                            registry.invoke(capability, context, {})
                        self.assertEqual(error.exception.code, "PERMISSION_DENIED")

    def test_legacy_write_adapters_reject_readonly_even_for_an_admin(self) -> None:
        endpoints = (
            ("POST", "/api/entities"),
            ("PATCH", "/api/entities/item"),
            ("POST", "/api/entities/item/action"),
            ("POST", "/api/entities/item/restore"),
            ("PATCH", "/api/relations/edge"),
            ("POST", "/api/materials/material/candidates/candidate/adopt"),
            ("POST", "/api/asset-import/data"),
            ("POST", "/api/experience/session/candidates/candidate/review"),
            ("POST", "/api/authority/assets"),
            ("PUT", "/api/authority/assets/item"),
            ("POST", "/api/skills"),
            ("POST", "/api/skills/import"),
            ("PUT", "/api/skills/item"),
            ("POST", "/api/skills/item/action"),
            ("POST", "/api/skills/item/test"),
            ("POST", "/api/datasets/table/dictionary-drafts"),
            ("POST", "/api/special-projects/project/candidates/candidate/review"),
            ("POST", "/api/placement/item/confirm"),
        )
        for method, path in endpoints:
            with self.subTest(method=method, path=path):
                self.assertTrue(policy.authorize(principal("admin"), method, path, {"action": "approve"}).allowed)
                result = policy.authorize(principal("admin", readonly=True), method, path, {"action": "approve"})
                self.assertFalse(result.allowed)
                self.assertEqual(result.code, 403)
