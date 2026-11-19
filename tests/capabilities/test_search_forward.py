import sqlite3
import unittest
from contextlib import nullcontext
from unittest.mock import patch

from backend.api.v1 import core
from backend.capabilities.context import CapabilityContext
from backend.capabilities.read import SearchInput, semantic_search
from backend.core.errors import ApiError
from backend.db import Row
from backend.security.context import get_context, use_context
from tests.security.test_permissions import principal


class SearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.addCleanup(self.connection.close)
        self.connection.row_factory = lambda cur, row: Row(
            dict(zip([item[0] for item in cur.description], row, strict=True))
        )
        self.connection.create_function("DATABASE", 0, lambda: "test_db")
        self.connection.executescript(
            "CREATE TABLE entities(id TEXT, kind TEXT, title TEXT, summary TEXT, domain TEXT, "
            "org_id TEXT, status TEXT, source_kind TEXT, payload TEXT, created_at TEXT, updated_at TEXT, "
            "version INTEGER"
            ");"
            "CREATE TABLE search_index(doc_id TEXT,doc_type TEXT,entity_id TEXT,kind TEXT,pillar TEXT,module_id TEXT);"
            "CREATE TABLE entity_access(entity_id TEXT,visibility TEXT);"
        )
        for ident, status in [("DRAFT", "draft"), ("LIVE", "published")]:
            self.connection.execute(
                "INSERT INTO entities VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (ident, "data", "Example", "", "", "sub-01", status, "user", '{"unit":"笔"}', "2026", "2026", 1),
            )
            self.connection.execute(
                "INSERT INTO search_index VALUES(?,'element',?,'data','asset','MOD-DEFAULT')", ("el:" + ident, ident)
            )

        class Fixture(nullcontext):
            def __init__(inner) -> None:
                super().__init__(inner)
                inner.principal = get_context()
                inner.readonly = True

            def execute(inner, *args: object) -> sqlite3.Cursor:
                return self.connection.execute(*args)

            def close(inner) -> None:
                pass

        self.factory = Fixture
        self.enterContext(
            patch("backend.security.visibility.assert_execution_principal_current", side_effect=lambda p: p)
        )
        self.enterContext(patch.object(core, "connect", side_effect=Fixture))
        self.enterContext(patch("backend.security.store.authorized_read_tenants", return_value=[{"id": "sub-01"}]))

    def search(self, role: str, *, readonly: bool = False, **filters: str) -> dict:
        person = principal(role, readonly=readonly)
        with use_context(person):
            ctx = CapabilityContext(person, permissions=frozenset({"draft.read"}), connection_factory=self.factory)
            return semantic_search(ctx, SearchInput(**filters)).model_dump()

    def test_authorized_results_match_legacy(self) -> None:
        with use_context(principal()):
            legacy = core.search()
        modern = self.search("admin")
        expected_fields = {
            "id",
            "kind",
            "title",
            "summary",
            "domain",
            "org_id",
            "status",
            "source_kind",
            "payload",
            "created_at",
            "updated_at",
            "version",
            "owner_tenant_id",
            "owner_org_id",
        }
        self.assertEqual(set(legacy["items"][0]), expected_fields)
        comparable = {**modern, "items": [{k: item[k] for k in expected_fields} for item in modern["items"]]}
        self.assertEqual(comparable, legacy)
        self.assertEqual(legacy["items"][0]["payload"]["unit"], "笔")
        self.assertEqual((modern["total"], modern["page"], modern["page_size"]), (2, 1, 25))
        self.assertEqual([r["id"] for r in modern["items"]], [r["id"] for r in legacy["items"]])
        self.assertEqual([r["doc_id"] for r in modern["items"]], ["el:DRAFT", "el:LIVE"])

    def test_viewer_only_sees_published(self) -> None:
        result = self.search("viewer")
        self.assertEqual([item["id"] for item in result["items"]], ["LIVE"])
        self.assertEqual(result["total"], 1)

    def test_analyst_only_sees_published(self) -> None:
        result = self.search("analyst")
        self.assertEqual([item["status"] for item in result["items"]], ["published"])
        self.assertEqual(self.search("analyst", status="draft")["total"], 0)

    def test_readonly_uses_published_scope_even_with_stale_draft_permission(self) -> None:
        result = self.search("admin", readonly=True)
        self.assertEqual([item["id"] for item in result["items"]], ["LIVE"])
        self.assertEqual(result["total"], 1)

    def test_readonly_search_uses_federation_visibility(self) -> None:
        person = principal("modeler", readonly=True, home_tenant_id="hq")
        grant = {"id": person.tenant_id, "db_name": person.database_name, "visibility": "published"}
        ctx = CapabilityContext(person, connection_factory=self.factory)
        with use_context(person), patch("backend.security.store.can_read_tenant", return_value=grant) as check:
            self.assertEqual(semantic_search(ctx, SearchInput()).total, 0)
            self.connection.executemany("INSERT INTO entity_access VALUES(?,'headquarters')", [("DRAFT",), ("LIVE",)])
            result = semantic_search(ctx, SearchInput())
            self.assertEqual([item["id"] for item in result.items], ["LIVE"])
            self.assertTrue(all(call.args == (person, person.tenant_id) for call in check.call_args_list))
            self.assertGreaterEqual(check.call_count, 2)

    def test_connection_identity_must_match_scope(self) -> None:
        bound = self.factory()  # No session was attached at acquisition.
        person = principal()
        with use_context(person), self.assertRaises(ApiError) as mismatch:
            semantic_search(CapabilityContext(person, connection_factory=lambda: bound), SearchInput())
        self.assertEqual(mismatch.exception.code, "PERMISSION_DENIED")
