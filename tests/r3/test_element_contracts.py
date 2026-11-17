"""R3 catalog source metadata stays scoped and uses generated read contracts."""

import json
import os
import unittest
from contextlib import closing
from copy import deepcopy
from unittest.mock import MagicMock, patch

from backend import db
from backend.capabilities.context import CapabilityContext
from backend.capabilities.elements import ElementList, list_elements
from tests.r2 import test_build as construction
from tests.security.test_permissions import principal


def row() -> dict:
    return {
        "id": "rule-1",
        "title": "付款规则",
        "kind": "rule",
        "status": "published",
        "summary": "付款前核对",
        "version": 2,
        "updated_at": "2026-11-17T01:00:00Z",
        "payload": {
            "source_refs": [{"material_id": "material-1"}],
            "private_internal": "never a list column",
        },
    }


class ElementSourceContract(unittest.TestCase):
    def test_local_list_returns_material_title_and_removes_all_payload(self) -> None:
        item = row()
        connection = MagicMock()
        connection.execute.return_value = [{"id": "material-1", "title": "付款制度"}]
        with (
            patch("backend.capabilities.elements.core.entities", return_value={"items": [item], "total": 1}),
            patch("backend.capabilities.elements.db.connect", return_value=connection),
        ):
            result = list_elements(CapabilityContext(principal(), permissions=frozenset()), ElementList())
        self.assertEqual(result.data["items"][0]["source_title"], "付款制度")
        self.assertNotIn("payload", result.data["items"][0])
        sql, params = connection.execute.call_args.args
        self.assertIn("kind='material'", sql)
        self.assertIn("active_asset", sql)
        self.assertEqual(params, ["material-1"])

    def test_missing_or_unavailable_source_never_falls_back_to_an_identifier(self) -> None:
        connection = MagicMock()
        connection.execute.return_value = []
        with (
            patch("backend.capabilities.elements.core.entities", return_value={"items": [row()], "total": 1}),
            patch("backend.capabilities.elements.db.connect", return_value=connection),
        ):
            result = list_elements(CapabilityContext(principal(), permissions=frozenset()), ElementList())
        self.assertEqual(result.data["items"][0]["source_title"], "")
        self.assertNotIn("material-1", str(result.model_dump()))

    def test_readonly_list_only_uses_gateway_metadata_never_local_database(self) -> None:
        item = row()
        item.pop("payload")
        item["tenant_name"] = "已授权机构"
        ctx = CapabilityContext(principal(readonly=True), permissions=frozenset())
        with (
            patch(
                "backend.capabilities.elements.federation.search",
                return_value={"items": [item], "total": 1, "page": 1, "page_size": 25, "partial": False},
            ) as gateway,
            patch("backend.capabilities.elements.db.connect") as database,
        ):
            result = list_elements(ctx, ElementList())
        database.assert_not_called()
        self.assertEqual(result.data["items"][0]["source_title"], "已授权机构")
        self.assertEqual(gateway.call_args.kwargs["tenant_id"], ctx.principal.tenant_id)
        self.assertEqual(gateway.call_args.kwargs["status"], "published")

    def test_malformed_and_explicit_cross_tenant_references_are_not_resolved(self) -> None:
        for refs in (
            None,
            "material-1",
            [{"material_id": None}],
            [{"material_id": 42}],
            [{"material_id": "material-1", "tenant_id": "another-tenant"}],
        ):
            item = deepcopy(row())
            item["payload"]["source_refs"] = refs
            with (
                self.subTest(refs=refs),
                patch("backend.capabilities.elements.core.entities", return_value={"items": [item], "total": 1}),
                patch("backend.capabilities.elements.db.connect") as database,
            ):
                result = list_elements(CapabilityContext(principal(), permissions=frozenset()), ElementList())
            database.assert_not_called()
            self.assertEqual(result.data["items"][0]["source_title"], "")


@unittest.skipUnless(os.getenv("RUN_JOBS_MYSQL_TESTS") == "1", "opt-in MySQL integration test")
class ElementSourceMySQL(unittest.TestCase):
    setUpClass = classmethod(construction.BuildMySQL.setUpClass.__func__)
    tearDownClass = classmethod(construction.BuildMySQL.tearDownClass.__func__)
    login = construction.BuildMySQL.login

    def test_catalog_source_metadata_and_typed_draft_detail(self) -> None:
        builder = self.login("fixture.builder")
        other = self.login("r2.other")
        uploaded = builder.post(
            "/api/materials/upload", files={"file": ("R3来源目录制度.txt", "付款前核对合同".encode(), "text/plain")}
        )
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        material_id = uploaded.json()["id"]
        created = builder.post(
            "/api/entities",
            json={"kind": "rule", "title": "R3来源目录草稿", "payload": {"definition": "付款前核对合同"}},
        )
        self.assertEqual(created.status_code, 200, created.text)
        ident = created.json()["id"]
        with closing(db.Connection(db._raw_connection(self.names["dept-01"]))) as conn, conn:
            conn.execute(
                "UPDATE entities SET payload=json_set(payload,'$.source_refs',CAST(? AS JSON)) WHERE id=?",
                (json.dumps([{"material_id": material_id}]), ident),
            )
        listing = builder.get("/api/v2/elements", params={"q": "R3来源目录草稿"})
        self.assertEqual(listing.status_code, 200, listing.text)
        item = listing.json()["data"]["items"][0]
        self.assertIn("R3来源目录制度", item["source_title"])
        self.assertNotIn("payload", item)
        detail = builder.get("/api/v2/elements/" + ident)
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()["data"]["entity"]["status"], "draft")
        self.assertEqual(other.get("/api/v2/elements/" + ident).status_code, 404)
        self.assertEqual(other.get("/api/v2/elements", params={"q": "R3来源目录草稿"}).json()["data"]["total"], 0)
        with closing(db.Connection(db._raw_connection(self.names["dept-01"]))) as conn, conn:
            conn.execute("UPDATE entities SET payload=json_set(payload,'$.active_asset',0) WHERE id=?", (material_id,))
        hidden_source = builder.get("/api/v2/elements", params={"q": "R3来源目录草稿"})
        self.assertEqual(hidden_source.status_code, 200, hidden_source.text)
        self.assertEqual(hidden_source.json()["data"]["items"][0]["source_title"], "")
