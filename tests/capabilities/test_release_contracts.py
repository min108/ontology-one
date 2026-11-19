"""Ownership, independent bindings, validation and durable result limits."""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from backend.api.v2.routes import registry
from backend.capabilities.context import CapabilityContext
from backend.capabilities.registry import Registry
from backend.capabilities.release_models import ReleasePublication
from backend.capabilities.releases import _receipt
from backend.core.errors import ApiError, StrictModel
from backend.security import policy
from backend.security.context import Principal
from backend.security.permissions import permissions_for
from tests.security.test_permissions import principal

ACTIONS = {
    "module.save": {"module_id": "MOD-DEFAULT", "owner_user_id": "u"},
    "module.move_elements": {"source_module_id": "MOD-S", "target_module_id": "MOD-DEFAULT", "entity_ids": ["asset"]},
    "release.create_draft": {"module_id": "MOD-DEFAULT"},
    "release.submit": {"release_id": "RLS-A"},
    "release.publish": {"release_id": "RLS-A"},
    "release.withdraw": {"release_id": "RLS-A"},
}


class ReleaseContracts(unittest.TestCase):
    def setUp(self) -> None:
        grants = patch("backend.security.store.authorized_read_tenants", return_value=[])
        audit = patch("backend.security.store.append_audit")
        for mocked in (grants, audit):
            mocked.start()
            self.addCleanup(mocked.stop)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.database = Path(folder.name) / "ownership.sqlite"
        with closing(self.connect()) as conn, conn:
            conn.executescript(
                "CREATE TABLE ont_modules(id TEXT PRIMARY KEY, owner_user_id TEXT);"
                "CREATE TABLE ont_releases(id TEXT PRIMARY KEY, module_id TEXT);"
                "INSERT INTO ont_modules(id,owner_user_id) VALUES('MOD-DEFAULT','actual-owner'),('MOD-S','u');"
                "INSERT INTO ont_releases(id,module_id) VALUES('RLS-A','MOD-DEFAULT');"
            )

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database)
        conn.row_factory = sqlite3.Row
        return conn

    def context(self, person: Principal, **options: object) -> CapabilityContext:
        return CapabilityContext(
            person, permissions=frozenset(permissions_for(person)), connection_factory=self.connect, **options
        )

    def test_only_stored_target_owner_receives_scoped_permission(self) -> None:
        for ident in ("module.save", "module.move_elements", "release.create_draft", "release.submit"):
            with (
                self.subTest(ident=ident),
                patch("backend.capabilities.registry.check_all", side_effect=RuntimeError("admitted")),
            ):
                with self.assertRaises(ApiError) as caught:
                    registry.invoke(ident, self.context(principal("modeler", user_id="u")), ACTIONS[ident])
                self.assertEqual(caught.exception.code, "PERMISSION_DENIED")
                with self.assertRaisesRegex(RuntimeError, "admitted"):
                    registry.invoke(ident, self.context(principal("modeler", user_id="actual-owner")), ACTIONS[ident])
        # Authored settings request and scene ownership cannot grant institution-wide authority.
        self.assertNotIn("module.manage", permissions_for(principal("modeler", user_id="actual-owner")))
        for ident, body in (
            ("module.save", {"module_id": "missing", "name": "Another"}),
            ("release.submit", {"release_id": "missing"}),
        ):
            with self.subTest(ident=ident), self.assertRaises(ApiError) as caught:
                registry.invoke(ident, self.context(principal("modeler")), body)
            self.assertEqual(caught.exception.code, "NOT_FOUND")

    def test_foreign_readonly_and_untrusted_bindings(self) -> None:
        for ident, body in ACTIONS.items():
            for role in ("admin", "modeler", "reviewer", "analyst", "viewer"):
                with self.subTest(ident=ident, role=role):
                    with self.assertRaises(ApiError) as caught:
                        registry.invoke(ident, self.context(principal(role)), {**body, "tenant_id": "foreign-tenant"})
                    self.assertEqual(caught.exception.code, "NOT_FOUND")
            for person, binding, code in (
                (principal(readonly=True), "rest", "PERMISSION_DENIED"),
                (principal(readonly=True, tenant_id="foreign-tenant"), "rest", "NOT_FOUND"),
                (principal(identity_kind="application"), "rest", "PERMISSION_DENIED"),
                (principal(), "mcp", "PERMISSION_DENIED"),
            ):
                with self.subTest(ident=ident, binding=binding), self.assertRaises(ApiError) as caught:
                    registry.invoke(ident, self.context(person, binding=binding), body)
                self.assertEqual(caught.exception.code, code)
            declared = registry.entries[ident]
            with self.subTest(ident=ident, binding="builder"), self.assertRaises(ApiError) as caught:
                registry.invoke(ident, self.context(principal(), binding="tool", assistant="builder"), body)
            self.assertEqual(
                caught.exception.code,
                "CONFIRMATION_REQUIRED" if "builder" in declared.assistants else "PERMISSION_DENIED",
            )

    def test_request_cannot_supply_actor_status_items_or_ambiguous_types(self) -> None:
        for ident, extra in (
            ("module.save", {"created_by": "u"}),
            ("module.save", {"register_dependencies": "true"}),
            ("module.move_elements", {"entity_ids": ["a", "a"]}),
            ("release.publish", {"background": "true"}),
            ("release.publish", {"reviewed_by": "u"}),
            ("release.withdraw", {"status": "published"}),
            ("release.submit", {"version": "1.0.00"}),
            ("release.create_draft", {"version": 1}),
            ("release.create_draft", {"items": []}),
        ):
            with self.subTest(ident=ident, extra=extra), self.assertRaises(ApiError) as caught:
                registry.invoke(ident, self.context(principal()), {**ACTIONS[ident], **extra})
            self.assertEqual(caught.exception.code, "VALIDATION_FAILED")

    def test_new_session_roles_only_access_their_own_auth_routes_and_existing_v2_policy(self) -> None:
        for role in ("analyst", "viewer"):
            person = principal(role)
            for method, path in (
                ("GET", "/api/auth/session"),
                ("GET", "/api/auth/me"),
                ("POST", "/api/auth/logout"),
                ("POST", "/api/auth/password"),
                ("GET", "/api/auth/sessions"),
                ("DELETE", "/api/auth/sessions/own"),
            ):
                with self.subTest(role=role, path=path):
                    self.assertTrue(policy.authorize(person, method, path).allowed)
            for method, path in (
                ("GET", "/api/auth/unregistered"),
                ("GET", "/api/admin/users"),
                ("GET", "/api/federation/links"),
                ("GET", "/api/entities"),
                ("POST", "/api/v2/unregistered"),
            ):
                with self.subTest(role=role, path=path):
                    self.assertFalse(policy.authorize(person, method, path).allowed)

    def test_gateway_rolls_back_a_result_rejected_after_writing(self) -> None:
        class Entry(StrictModel):
            name: str

        reg = Registry()

        @reg.capability(
            id="test.atomic",
            version="1.0",
            title="保存",
            summary="保存",
            domain="test",
            intent="build",
            effect="write_draft",
            confirm="L1",
            permissions=["build.edit"],
            assistants=[],
            mcp=False,
            input=Entry,
            output=Entry,
            preconditions=[],
            idempotent=False,
            audit="none",
            limits={"max_result_bytes": 50},
            transactional=True,
        )
        def write(ctx: CapabilityContext, body: Entry) -> Entry:
            ctx.require_transaction().execute("INSERT INTO ont_modules(id,owner_user_id) VALUES(?,?)", (body.name, "u"))
            return body

        ctx = self.context(principal())
        oversized = "x" * 60
        with self.assertRaises(ApiError) as caught:
            reg.invoke("test.atomic", ctx, {"name": oversized})
        self.assertEqual(caught.exception.code, "IMPORT_LIMIT_EXCEEDED")
        reg.invoke("test.atomic", ctx, {"name": "saved"})
        with closing(self.connect()) as conn:
            self.assertEqual(
                conn.execute("SELECT id FROM ont_modules WHERE id IN (?,?)", (oversized, "saved")).fetchall()[0][0],
                "saved",
            )
            self.assertIsNone(conn.execute("SELECT id FROM ont_modules WHERE id=?", (oversized,)).fetchone())

    def test_large_receipt_keeps_identity_counts_and_complete_read_address(self) -> None:
        value = {
            "id": "RLS-A",
            "module_id": "MOD-DEFAULT",
            "version": "1.0.0",
            "status": "candidate",
            "bump": "major",
            "changelog": "",
            "manifest_hash": "a" * 64,
            "created_by": "creator",
            "reviewed_by": None,
            "submitted_by": "submitter",
            "created_at": "2026-01-01",
            "published_at": None,
            "current_release_id": None,
            "previous_release_id": None,
            "suggested_version": "1.0.0",
            "member_count": 1,
            "ref_count": 0,
            "no_changes": False,
            "missing_dependencies": [],
            "diff": {"added": ["member"], "changed": [], "removed": [], "promoted": []},
            "items": [
                {
                    "entity_id": "member",
                    "entity_version": 1,
                    "kind": "knowledge",
                    "snapshot": {"title": "文" * 3000},
                    "content_hash": "b" * 64,
                    "item_role": "member",
                }
            ],
            "gates": [],
            "job_id": "job",
        }
        self.assertFalse(_receipt(ReleasePublication, value).truncated)
        with patch("backend.capabilities.releases.RECEIPT_BYTES", 1000):
            result = _receipt(ReleasePublication, value)
            self.assertTrue(result.truncated)
            self.assertEqual(
                (result.detail_url, result.job_id, result.manifest_hash, result.member_count),
                ("/api/v2/releases/RLS-A", "job", "a" * 64, 1),
            )
            self.assertEqual(
                (result.items, result.omitted_counts, result.diff.added), ([], {"items": 1, "gates": 0}, ["member"])
            )
            large_diff = {**value, "diff": {**value["diff"], "added": ["large" * 500]}}
            compact = _receipt(ReleasePublication, large_diff)
            self.assertEqual((compact.diff.added, compact.omitted_counts["diff.added"]), ([], 1))
            self.assertLess(len(compact.model_dump_json().encode()), 1000)
