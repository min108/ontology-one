"""Recipe contracts and the tenant-local, independently reviewed publication path."""

import asyncio
import os
import secrets
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend import db, main
from backend.capabilities.build import DecideItems, RecipeStart
from backend.domains.build import confidence, run_recipe
from backend.infra import migrations
from backend.security import store
from backend.security.context import use_context
from tests.fixtures.v070.load import load_fixture


class BuildContracts(unittest.TestCase):
    def test_confidence_is_deterministic_and_local_or_missing_quotes_are_doubt(self) -> None:
        self.assertEqual(confidence(exact=True, complete=True, unique=True, local=False), (0.9, "sure"))
        self.assertEqual(confidence(exact=True, complete=False, unique=False, local=False), (0.7, "check"))
        for local, exact in ((True, True), (False, False)):
            score, tier = confidence(exact=exact, complete=True, unique=True, local=local)
            self.assertLess(score, 0.5)
            self.assertEqual(tier, "doubt")

    def test_contract_rejects_forged_identity_and_unsupported_recipe(self) -> None:
        for body in ({"material_id": "x", "tenant_id": "hq"}, {"material_id": "x", "recipe": "F"}):
            with self.assertRaises(ValidationError):
                RecipeStart.model_validate(body)
        with self.assertRaises(ValidationError):
            DecideItems.model_validate({"changeset_id": "x", "item_ids": [], "decision": "accepted"})


@unittest.skipUnless(os.getenv("RUN_JOBS_MYSQL_TESTS") == "1", "opt-in MySQL integration test")
class BuildMySQL(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        cls.namespace = "ontology_r2_" + secrets.token_hex(5)
        cls.env = patch.dict(
            os.environ,
            {
                "MYSQL_DATABASE": cls.namespace,
                "ONTOLOGY_STORAGE_ROOT": str(root / "tenants"),
                "ONTOLOGY_BACKUP_DIR": str(root / "backups"),
            },
        )
        cls.env.start()
        cls.names = load_fixture()
        migrations.run_migrations()
        token = store.login("fixture.superadmin", "SyntheticFixture9!", "127.0.0.1", "r2-test")
        owner = store.authenticate_session(token, "r2-test")[0]
        store.create_user(
            owner,
            username="r2.other",
            display_name="合成隔离机构管理员",
            tenant_id="sub-01",
            role="admin",
            password="SyntheticFixture9!",
            must_change_password=False,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        with closing(db._raw_connection("information_schema")) as raw:
            conn = db.Connection(raw)
            for name in cls.names.values():
                if not name.startswith(cls.namespace):
                    raise AssertionError("Unexpected database")
                conn.execute(f"DROP DATABASE `{name}`")
        cls.env.stop()
        cls.temp.cleanup()

    def login(self, username: str) -> TestClient:
        client = TestClient(main.app)
        response = client.post("/api/auth/login", json={"username": username, "password": "SyntheticFixture9!"})
        self.assertEqual(response.status_code, 200, response.text)
        client.headers.update(
            {"X-CSRF-Token": response.json()["csrf_token"], "X-Workspace-Id": response.json()["workspace"]["tenant_id"]}
        )
        self.addCleanup(client.close)
        return client

    def invoke(self, client: TestClient, ident: str, body: dict) -> dict:
        response = client.post(f"/api/v2/capabilities/{ident}:invoke", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["data"]

    def proposal(self, client: TestClient) -> dict:
        response = client.post(
            "/api/materials/upload",
            files={
                "file": (
                    "【演示】付款核对制度.txt",
                    "第一条 付款前必须核对合同约定及附件。\n第二条 累计付款不得超过合同金额。".encode(),
                    "text/plain",
                )
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        run = self.invoke(client, "recipe.start", {"material_id": response.json()["id"]})
        person = store.authenticate_session(client.cookies.get("ontology_session"), "r2-test")[0]
        with use_context(person), patch("backend.domains.build.model_available", return_value=False):
            asyncio.run(run_recipe(run["id"]))
        result = self.invoke(client, "recipe.status", {"run_id": run["id"]})
        self.assertEqual(result["status"], "partial")
        changeset = self.invoke(client, "changeset.get", {"changeset_id": result["changeset_id"]})
        self.assertTrue(changeset["items"])
        for item in changeset["items"]:
            self.assertEqual(item["tier"], "doubt")
            self.assertTrue(item["evidence"][0]["anchor_id"])
        return changeset

    def test_local_recipe_apply_retry_and_independent_review(self) -> None:
        builder = self.login("fixture.builder")
        reviewer = self.login("fixture.reviewer")
        changeset = self.proposal(builder)
        body = {"changeset_id": changeset["id"]}
        self.invoke(
            builder,
            "changeset.decide_items",
            {**body, "item_ids": [x["id"] for x in changeset["items"]], "decision": "accepted"},
        )
        applied = self.invoke(builder, "changeset.apply_and_submit", body)
        repeated = self.invoke(builder, "changeset.apply_and_submit", body)
        self.assertEqual(applied["entity_ids"], repeated["entity_ids"])
        self.assertTrue(applied["entity_ids"])
        ident = applied["entity_ids"][0]
        denied = builder.post(
            "/api/v2/reviews/" + ident + ":decide", json={"decision": "approve_publish", "version": 2}
        )
        self.assertEqual(denied.status_code, 403)
        detail = reviewer.get("/api/v2/reviews/" + ident)
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertTrue(detail.json()["data"]["submitter"])
        response = reviewer.post(
            "/api/v2/reviews/" + ident + ":decide", json={"decision": "approve_publish", "version": 2}
        )
        self.assertEqual(response.status_code, 200, response.text)
        entity = reviewer.get("/api/entities/" + ident).json()
        self.assertEqual(entity["status"], "published")
        self.assertEqual(entity["version"], 4)
        self.assertTrue(entity["payload"]["source_refs"])

    def test_migration_preserves_column_contract(self) -> None:
        from backend.infra.backup import snapshot_database
        from backend.infra.recovery_schema import validate_schema

        before = snapshot_database(self.names["dept-01"])
        self.assertEqual(migrations.run_migrations()["migrated"], [])
        after = snapshot_database(self.names["dept-01"])
        self.assertEqual(before["hashes"], after["hashes"])
        self.assertEqual(len(after["columns"]["entities"]), 12)
        self.assertEqual(len(after["columns"]["relations"]), 7)
        self.assertTrue({"changesets", "changeset_items", "recipe_runs"} <= set(after["tables"]))
        validate_schema(after, "tenant")

    def test_cross_tenant_and_read_role_cannot_decide(self) -> None:
        builder = self.login("fixture.builder")
        reviewer = self.login("fixture.reviewer")
        changeset = self.proposal(builder)
        body = {"changeset_id": changeset["id"], "item_ids": [changeset["items"][0]["id"]], "decision": "accepted"}
        denied = reviewer.post("/api/v2/capabilities/changeset.decide_items:invoke", json=body)
        self.assertEqual(denied.status_code, 403)
        admin = self.login("r2.other")
        denied = admin.get("/api/v2/changesets/" + changeset["id"])
        self.assertIn(denied.status_code, (403, 404))

    def test_admin_cannot_review_own_submission_even_through_legacy_action(self) -> None:
        admin, reviewer = self.login("fixture.admin"), self.login("fixture.reviewer")
        proposal = self.proposal(admin)
        self.invoke(
            admin,
            "changeset.decide_items",
            {"changeset_id": proposal["id"], "item_ids": [proposal["items"][0]["id"]], "decision": "accepted"},
        )
        ident = self.invoke(admin, "changeset.apply_and_submit", {"changeset_id": proposal["id"]})["entity_ids"][0]
        denied = admin.post(f"/api/v2/reviews/{ident}:decide", json={"decision": "approve_publish", "version": 2})
        self.assertEqual(denied.status_code, 403)
        denied = admin.post(f"/api/entities/{ident}/action", json={"action": "approve"})
        self.assertEqual(denied.status_code, 403)
        missing = reviewer.post(f"/api/v2/reviews/{ident}:decide", json={"decision": "return", "version": 2})
        self.assertEqual(missing.status_code, 422)
        returned = reviewer.post(
            f"/api/v2/reviews/{ident}:decide", json={"decision": "return", "version": 2, "reason": "请补充适用条件。"}
        )
        self.assertEqual(returned.status_code, 200, returned.text)
        self.assertEqual(returned.json()["data"]["entity"]["status"], "returned")

    def test_unlocated_quote_cannot_be_accepted(self) -> None:
        builder = self.login("fixture.builder")
        proposal = self.proposal(builder)
        person = store.authenticate_session(builder.cookies.get("ontology_session"), "r2-test")[0]
        item = proposal["items"][0]
        with use_context(person), closing(db.connect()) as conn, conn:
            conn.execute(
                "UPDATE changeset_items SET evidence='[]',validation=? WHERE id=?",
                ('{"level":"error","issues":["原文不匹配"]}', item["id"]),
            )
        failed = builder.post(
            "/api/v2/capabilities/changeset.decide_items:invoke",
            json={"changeset_id": proposal["id"], "item_ids": [item["id"]], "decision": "accepted"},
        )
        self.assertEqual(failed.status_code, 422)
