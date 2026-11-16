"""Catalog card contracts and published-only/tenant boundaries."""

import os
import unittest
from contextlib import closing
from unittest.mock import patch

from backend import db
from backend.capabilities.context import CapabilityContext
from backend.capabilities.elements import ElementGet, ElementList, get_element, list_elements
from backend.core.errors import ApiError
from backend.kernel.cards import build_card
from backend.security import store
from backend.security.context import use_context
from tests.r2 import test_build as build_tests
from tests.security.test_permissions import principal


class CardContract(unittest.TestCase):
    def test_specific_templates_and_literal_model_like_content(self) -> None:
        rule = {
            "id": "r",
            "kind": "rule",
            "title": "付款",
            "summary": "先核对。再付款。",
            "payload": {"scope": "全部合同", "conditions": ["验收合格"], "obligation": "30日内申请"},
        }
        card = build_card(rule)
        self.assertEqual(card["one_liner"], "先核对。")
        self.assertIn("适用范围：全部合同", card["points"])
        self.assertIn("要求：30日内申请", card["points"])
        self.assertEqual(card, build_card(rule))
        data = build_card(
            {
                "kind": "data",
                "summary": "金额",
                "payload": {"definition": "<script>literal</script>", "unit": "元", "constraints": "含税"},
            }
        )
        self.assertEqual(data["one_liner"], "<script>literal</script>")
        self.assertIn("单位：元", data["points"])
        self.assertLessEqual(len(card["text"]), 1200)

    def test_readonly_detail_uses_federation_and_rejects_draft(self) -> None:
        ctx = CapabilityContext(principal(readonly=True), permissions=frozenset())
        with patch(
            "backend.capabilities.elements.federation.entity_detail",
            return_value={"entity": {"kind": "rule", "status": "draft"}, "relations": {}},
        ) as read:
            with self.assertRaises(ApiError) as denied:
                get_element(ctx, ElementGet(entity_id="r"))
            self.assertEqual(denied.exception.code, "NOT_FOUND")
            read.assert_called_once_with(ctx.principal.tenant_id, "r")


@unittest.skipUnless(os.getenv("RUN_JOBS_MYSQL_TESTS") == "1", "opt-in MySQL integration test")
class CatalogMySQL(unittest.TestCase):
    setUpClass = classmethod(build_tests.BuildMySQL.setUpClass.__func__)
    tearDownClass = classmethod(build_tests.BuildMySQL.tearDownClass.__func__)
    login = build_tests.BuildMySQL.login

    def test_catalog_filters_private_drafts_and_cross_tenant_ids(self) -> None:
        builder = self.login("fixture.builder")
        other = self.login("r2.other")
        response = builder.post(
            "/api/entities",
            json={"kind": "rule", "title": "R2目录不可公开草稿", "payload": {"definition": "draft secret"}},
        )
        self.assertEqual(response.status_code, 200, response.text)
        ident = response.json()["id"]
        self.assertEqual(builder.get("/api/v2/elements/" + ident).status_code, 200)
        for client in (other,):
            self.assertEqual(client.get("/api/v2/elements/" + ident).status_code, 404)
            result = client.get("/api/v2/elements", params={"q": "R2目录不可公开草稿"})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()["data"]["total"], 0)
        person = store.authenticate_session(builder.cookies.get("ontology_session"), "r2-test")[0]
        # Real fixture data with a capability scope lacking draft.read.
        with use_context(person):
            ctx = CapabilityContext(person, permissions=frozenset())
            with self.assertRaises(ApiError) as denied:
                get_element(ctx, ElementGet(entity_id=ident))
            self.assertEqual(denied.exception.code, "NOT_FOUND")
            self.assertEqual(list_elements(ctx, ElementList(q="R2目录不可公开草稿")).data["total"], 0)
        # An inactive item remains unavailable even to editors through this current catalog.
        with closing(db.Connection(db._raw_connection(self.names["dept-01"]))) as conn, conn:
            conn.execute("UPDATE entities SET payload=json_set(payload,'$.active_asset',0) WHERE id=?", (ident,))
        self.assertEqual(builder.get("/api/v2/elements/" + ident).status_code, 404)
        self.assertEqual(builder.get("/api/v2/elements", params={"kind": "material"}).status_code, 422)
