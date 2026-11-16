import sqlite3
import unittest
from unittest.mock import patch

from backend.api.v1 import core
from backend.capabilities.context import CapabilityContext
from backend.capabilities.read import SearchInput, semantic_search
from backend.db import Row
from tests.security.test_permissions import principal


class SearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = lambda cur, row: Row(
            dict(zip([item[0] for item in cur.description], row, strict=True))
        )
        self.connection.execute(
            "CREATE TABLE entities(id TEXT, kind TEXT, title TEXT, summary TEXT, domain TEXT, "
            "org_id TEXT, status TEXT, source_kind TEXT, payload TEXT, created_at TEXT, "
            "updated_at TEXT, version INTEGER)"
        )
        for ident, status in [("DRAFT", "draft"), ("LIVE", "published")]:
            self.connection.execute(
                "INSERT INTO entities(id,kind,title,summary,domain,org_id,status,source_kind,"
                "payload,created_at,updated_at,version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (ident, "data", "Example", "", "", "sub-01", status, "user", "{}", "2026", "2026", 1),
            )

        # The legacy function owns close(); keep the in-memory fixture for two calls.
        class Fixture:
            def __enter__(inner) -> sqlite3.Connection:
                return self.connection.__enter__()

            def __exit__(inner, *args: object) -> bool:
                return self.connection.__exit__(*args)

            def execute(inner, *args: object) -> sqlite3.Cursor:
                return self.connection.execute(*args)

            def close(inner) -> None:
                pass

        self.factory = patch.object(core, "connect", return_value=Fixture())
        self.factory.start()
        self.addCleanup(self.factory.stop)
        self.addCleanup(self.connection.close)

    def test_authorized_results_match_legacy(self) -> None:
        ctx = CapabilityContext(principal(), permissions=frozenset({"draft.read"}))
        self.assertEqual(semantic_search(ctx, SearchInput()).model_dump(), core.search())

    def test_viewer_only_sees_published(self) -> None:
        result = semantic_search(CapabilityContext(principal("viewer")), SearchInput())
        self.assertEqual([item["id"] for item in result.items], ["LIVE"])
        self.assertEqual(result.total, 1)

    def test_analyst_only_sees_published(self) -> None:
        result = semantic_search(CapabilityContext(principal("analyst")), SearchInput())
        self.assertEqual([item["status"] for item in result.items], ["published"])

    def test_readonly_search_uses_federation_visibility(self) -> None:
        response = {"items": [], "total": 0, "page": 1, "page_size": 25, "partial": False}
        with patch("backend.federation.search", return_value=response) as search:
            result = semantic_search(CapabilityContext(principal(readonly=True)), SearchInput())
        self.assertEqual(result.total, 0)
        search.assert_called_once_with(q="", kind="", status="published", tenant_id="sub-01", page=1, page_size=25)
