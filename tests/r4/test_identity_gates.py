"""Identity publication gates through authenticated HTTP and real MySQL writes."""

import json
import os
import secrets
import unittest
from contextlib import closing

from fastapi.testclient import TestClient
from httpx import Response

from backend import db
from backend.kernel.kinds import get_kind, requires_quality_gates
from backend.security import store
from backend.security.context import use_context
from tests.r2 import test_build as construction


class IdentityGateClassification(unittest.TestCase):
    def test_gate_applicability_does_not_change_asset_classification(self) -> None:
        for kind in ("position", "party"):
            self.assertTrue(requires_quality_gates(kind))
            self.assertFalse(get_kind(kind).governed)
            self.assertFalse(get_kind(kind).module_eligible)
        self.assertTrue(requires_quality_gates("rule"))
        for kind in ("organization", "person", "material", "dataset", "task", "collection"):
            self.assertFalse(requires_quality_gates(kind))


@unittest.skipUnless(os.getenv("RUN_JOBS_MYSQL_TESTS") == "1", "opt-in identity gate HTTP/MySQL integration")
class IdentityGatesMySQL(unittest.TestCase):
    setUpClass = classmethod(construction.BuildMySQL.setUpClass.__func__)
    tearDownClass = classmethod(construction.BuildMySQL.tearDownClass.__func__)
    login = construction.BuildMySQL.login
    invoke = construction.BuildMySQL.invoke

    def payload(self, kind: str, *, valid: bool = True) -> dict:
        result = {"schema": kind + "/1"}
        if valid:
            result.update(definition="本机构登记的合成身份。")
            result.update({"org_id": "dept-01"} if kind == "position" else {"unified_code": "SYNTHETIC-IDENTITY"})
        return result

    def create(self, client: TestClient, kind: str, status: str, *, valid: bool = True) -> Response:
        ident = "IDENTITY-TEST-" + secrets.token_hex(6)
        return client.post(
            "/api/entities",
            json={
                "id": ident,
                "kind": kind,
                "title": ident,
                "status": status,
                "payload": self.payload(kind, valid=valid),
            },
        )

    def read(self, client: TestClient, ident: str) -> dict:
        response = client.get("/api/entities/" + ident)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def blocked(self, response: Response) -> None:
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("还差", response.text)

    def corrupt(self, client: TestClient, ident: str, *, missing_snapshot: bool = False, valid: bool = False) -> None:
        person = store.authenticate_session(client.cookies.get("ontology_session"), "identity-gates")[0]
        with use_context(person), closing(db.connect()) as conn, conn:
            if missing_snapshot:
                conn.execute("DELETE FROM versions WHERE entity_id=?", (ident,))
            else:
                kind = conn.execute("SELECT kind FROM entities WHERE id=?", (ident,)).fetchone()[0]
                conn.execute(
                    "UPDATE entities SET payload=? WHERE id=?", (json.dumps(self.payload(kind, valid=valid)), ident)
                )

    def test_direct_create_checks_identity_gates_and_rolls_back_every_write(self) -> None:
        admin = self.login("fixture.admin")
        for kind in ("position", "party"):
            for status in ("review", "approved", "published"):
                with self.subTest(kind=kind, status=status):
                    ident = "IDENTITY-BLOCKED-" + secrets.token_hex(6)
                    response = admin.post(
                        "/api/entities",
                        json={
                            "id": ident,
                            "kind": kind,
                            "title": ident,
                            "status": status,
                            "payload": self.payload(kind, valid=False),
                        },
                    )
                    self.blocked(response)
                    self.assertEqual(admin.get("/api/entities/" + ident).status_code, 404)
                    person = store.authenticate_session(admin.cookies.get("ontology_session"), "identity-gates")[0]
                    with use_context(person), closing(db.connect()) as conn:
                        for table, field in (
                            ("versions", "entity_id"),
                            ("entity_meta", "entity_id"),
                            ("activities", "entity_id"),
                        ):
                            count = conn.execute(f"SELECT count(*) FROM {table} WHERE {field}=?", (ident,)).fetchone()[
                                0
                            ]
                            self.assertEqual(count, 0)

    def test_valid_direct_publication_has_snapshot_and_needs_no_asset_only_gates(self) -> None:
        admin = self.login("fixture.admin")
        for kind in ("position", "party"):
            response = self.create(admin, kind, "published")
            self.assertEqual(response.status_code, 200, response.text)
            entity = response.json()
            self.assertEqual((entity["status"], entity["version"]), ("published", 1))
            self.assertNotIn("r2_review", entity["payload"])
            checks = self.invoke(admin, "quality.check_gates", {"entity_id": entity["id"]})["checks"]
            self.assertEqual(
                {check["code"]: check["status"] for check in checks},
                {
                    "QG1": "not_applicable",
                    "QG2": "pass",
                    "QG3": "not_applicable",
                    "QG4": "not_applicable",
                    "QG5": "pass",
                },
            )
            versions = admin.get("/api/entities/" + entity["id"] + "/versions").json()["items"]
            self.assertTrue(versions[0]["reason"].strip())
            self.assertEqual(versions[0]["snapshot"]["id"], entity["id"])
            # The blocking assertion demonstrably fails on a successful publication.
            with self.assertRaises(AssertionError):
                self.blocked(response)

    def test_direct_status_update_and_published_edit_cannot_bypass_checks(self) -> None:
        admin = self.login("fixture.admin")
        for kind in ("position", "party"):
            entity = self.create(admin, kind, "draft", valid=False).json()
            path = "/api/entities/" + entity["id"]
            self.blocked(admin.patch(path, json={"status": "published"}))
            self.assertEqual(
                (self.read(admin, entity["id"])["status"], self.read(admin, entity["id"])["version"]), ("draft", 1)
            )
            response = admin.patch(path, json={"status": "published", "payload": self.payload(kind)})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["status"], "published")
            self.blocked(admin.patch(path, json={"payload": self.payload(kind, valid=False)}))
            self.assertEqual(self.read(admin, entity["id"])["version"], 2)
            self.assertTrue(self.read(admin, entity["id"])["payload"]["definition"])

    def test_submit_approve_publish_actions_each_enforce_identity_checks(self) -> None:
        admin = self.login("fixture.admin")
        entity = self.create(admin, "party", "draft", valid=False).json()
        path = "/api/entities/" + entity["id"] + "/action"
        self.blocked(admin.post(path, json={"action": "submit"}))
        self.corrupt(admin, entity["id"], valid=True)
        self.assertEqual(admin.post(path, json={"action": "submit"}).status_code, 200)
        self.corrupt(admin, entity["id"])
        self.blocked(admin.post(path, json={"action": "approve"}))
        self.assertEqual(self.read(admin, entity["id"])["status"], "review")
        self.corrupt(admin, entity["id"], valid=True)
        self.assertEqual(admin.post(path, json={"action": "approve"}).status_code, 200)
        self.corrupt(admin, entity["id"])
        self.blocked(admin.post(path, json={"action": "publish"}))
        self.assertEqual(self.read(admin, entity["id"])["status"], "approved")

    def test_missing_snapshot_blocks_action_but_legacy_published_reads_remain(self) -> None:
        admin = self.login("fixture.admin")
        draft = self.create(admin, "party", "draft").json()
        self.corrupt(admin, draft["id"], missing_snapshot=True)
        response = admin.post("/api/entities/" + draft["id"] + "/action", json={"action": "submit"})
        self.blocked(response)
        self.assertIn("版本", response.text)
        legacy = self.create(admin, "party", "published").json()
        self.corrupt(admin, legacy["id"])
        self.corrupt(admin, legacy["id"], missing_snapshot=True)
        self.assertEqual(self.read(admin, legacy["id"])["status"], "published")
        self.assertFalse(self.invoke(admin, "quality.check_gates", {"entity_id": legacy["id"]})["passed"])
        self.assertEqual(self.read(admin, legacy["id"])["status"], "published")

    def test_restore_is_draft_and_existing_builder_publish_boundary_remains(self) -> None:
        admin, builder = self.login("fixture.admin"), self.login("fixture.builder")
        self.assertEqual(self.create(builder, "party", "published").status_code, 403)
        entity = self.create(admin, "party", "draft", valid=False).json()
        response = admin.patch(
            "/api/entities/" + entity["id"], json={"status": "published", "payload": self.payload("party")}
        )
        self.assertEqual(response.status_code, 200, response.text)
        response = admin.post(
            "/api/entities/" + entity["id"] + "/restore", json={"version": 1, "reason": "核对原始登记"}
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "draft")
        self.blocked(admin.patch("/api/entities/" + entity["id"], json={"status": "published"}))
