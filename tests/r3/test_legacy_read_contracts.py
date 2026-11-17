"""R3.1 legacy values remain readable without weakening writes or evidence checks."""

import json
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
from backend.capabilities.build import CandidatePatch
from backend.capabilities.build_models import Evidence
from backend.capabilities.reviews import ReviewDecision
from backend.domains.build import confirm_applicability
from backend.infra import migrations
from backend.security import store
from backend.security.context import use_context
from tests.fixtures.v070.load import load_fixture
from tests.r2 import test_build as construction

TEXT_SOURCE = "历史付款制度：付款前核对合同。"
DICT_SOURCE = {"title": "旧台账", "excerpt": "核对付款附件", "legacy_note": "保留扩展字段"}
MIXED_SOURCES = [TEXT_SOURCE, DICT_SOURCE]


class LegacyWriteContracts(unittest.TestCase):
    def test_read_compatibility_does_not_accept_invalid_new_writes_or_verified_evidence(self) -> None:
        for model, body in (
            (ReviewDecision, {"entity_id": "one", "decision": "return", "version": 1, "reason": None}),
            (
                ReviewDecision,
                {"entity_id": "one", "decision": "return", "version": 1, "source_refs": MIXED_SOURCES},
            ),
            (CandidatePatch, {"kind": "rule", "title": "付款规则", "content": None}),
            (CandidatePatch, {"kind": "rule", "title": "付款规则", "content": {"text": "付款前核对"}}),
            (Evidence, TEXT_SOURCE),
            (Evidence, DICT_SOURCE),
        ):
            with self.subTest(model=model.__name__, body=body), self.assertRaises(ValidationError):
                model.model_validate(body)


@unittest.skipUnless(os.getenv("RUN_JOBS_MYSQL_TESTS") == "1", "opt-in MySQL integration test")
class LegacyReadMySQL(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        cls.namespace = "ontology_r31_legacy_" + secrets.token_hex(5)
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
        client = TestClient(main.app, raise_server_exceptions=False)
        self.addCleanup(client.close)
        response = client.post("/api/auth/login", json={"username": username, "password": "SyntheticFixture9!"})
        self.assertEqual(response.status_code, 200, response.text)
        client.headers.update(
            {"X-CSRF-Token": response.json()["csrf_token"], "X-Workspace-Id": response.json()["workspace"]["tenant_id"]}
        )
        return client

    invoke = construction.BuildMySQL.invoke

    def legacy_entity(self, sources: object = MIXED_SOURCES) -> tuple[TestClient, TestClient, dict]:
        builder, reviewer = self.login("fixture.builder"), self.login("fixture.reviewer")
        proposal = construction.BuildMySQL.proposal(self, builder)
        item = proposal["items"][0]
        candidate = item["after_data"]
        created = builder.post(
            "/api/entities",
            json={
                "kind": "rule",
                "title": "旧数据付款规则",
                "summary": None,
                "payload": {
                    "content": "付款前核对合同。",
                    "definition": "付款前核对合同。",
                    "scope": "本机构付款",
                    "conditions": ["付款前"],
                    "exceptions": "无例外",
                    "valid_from": "2026-11-17",
                    "source_refs": item["evidence"],
                },
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        ident = created.json()["id"]
        person = store.authenticate_session(builder.cookies.get("ontology_session"), "r31-legacy-test")[0]
        with use_context(person), closing(db.connect()) as conn, conn:
            confirm_applicability(conn, created.json(), candidate)
        # Submit genuinely valid content through the current workflow first.
        # The compatibility endpoint still accepts a null edit reason; the current
        # kernel supplies its default text, so the stored legacy null is seeded below.
        edited = builder.patch(f"/api/entities/{ident}", json={"reason": None})
        self.assertEqual(edited.status_code, 200, edited.text)
        submitted = builder.post(f"/api/entities/{ident}/action", json={"action": "submit", "reason": None})
        self.assertEqual(submitted.status_code, 200, submitted.text)
        entity = submitted.json()
        # Explicit historical-storage fixture: restore old source representation and
        # null activity/version reasons after submit. No state transition is bypassed.
        entity["payload"]["source_refs"] = sources
        revision = next(event for event in entity["activities"] if event["action"] == "修订")
        revision["detail"] = None
        with use_context(person), closing(db.connect()) as conn, conn:
            conn.execute(
                "UPDATE entities SET payload=? WHERE id=?",
                (json.dumps(entity["payload"], ensure_ascii=False), ident),
            )
            conn.execute("UPDATE activities SET detail=NULL WHERE entity_id=? AND action='修订'", (ident,))
            conn.execute(
                "UPDATE versions SET reason=NULL WHERE entity_id=? AND version=?",
                (ident, edited.json()["version"]),
            )
        persisted = builder.get("/api/entities/" + ident)
        self.assertEqual(persisted.status_code, 200, persisted.text)
        self.assertEqual(persisted.json(), entity)
        self.assertIsNone(entity["summary"])
        self.assertEqual(entity["payload"]["source_refs"], sources)
        self.assertIsNone(next(event for event in entity["activities"] if event["action"] == "修订")["detail"])
        return builder, reviewer, entity

    def assert_readable_review(self, review: dict, ident: str, expected_status: str = "review") -> None:
        entity = review["entity"]
        self.assertEqual((entity["id"], entity["status"], entity["summary"]), (ident, expected_status, ""))
        self.assertEqual(review["source_refs"], entity["payload"]["source_refs"])
        self.assertEqual(review["source_refs"][0]["excerpt"], TEXT_SOURCE)
        self.assertFalse(review["source_refs"][0].get("anchor_id"))
        self.assertFalse(review["source_refs"][0].get("material_id"))
        self.assertEqual(review["source_refs"][1]["title"], DICT_SOURCE["title"])
        self.assertEqual(review["source_refs"][1]["legacy_note"], DICT_SOURCE["legacy_note"])

    def test_review_list_reads_null_summary_and_mixed_legacy_sources(self) -> None:
        _, reviewer, entity = self.legacy_entity()
        response = reviewer.get("/api/v2/reviews")
        self.assertEqual(response.status_code, 200, response.text)
        items = response.json()["data"]["items"]
        self.assert_readable_review(next(item for item in items if item["entity"]["id"] == entity["id"]), entity["id"])

    def test_review_detail_reads_legacy_values_without_changing_storage(self) -> None:
        builder, reviewer, entity = self.legacy_entity()
        response = reviewer.get("/api/v2/reviews/" + entity["id"])
        self.assertEqual(response.status_code, 200, response.text)
        self.assert_readable_review(response.json()["data"], entity["id"])
        stored = builder.get("/api/entities/" + entity["id"])
        self.assertEqual(stored.status_code, 200, stored.text)
        self.assertEqual(stored.json(), entity)

    def test_return_response_is_readable_and_persists_the_decision_once(self) -> None:
        builder, reviewer, entity = self.legacy_entity()
        path = f"/api/v2/reviews/{entity['id']}:decide"
        response = reviewer.post(
            path, json={"decision": "return", "version": entity["version"], "reason": "请补充可核对的原文。"}
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assert_readable_review(response.json()["data"], entity["id"], "returned")
        stored = builder.get("/api/entities/" + entity["id"]).json()
        self.assertEqual((stored["status"], stored["version"]), ("returned", entity["version"] + 1))
        self.assertIsNone(stored["summary"])
        self.assertEqual(stored["payload"]["source_refs"], MIXED_SOURCES)
        returns = [event for event in stored["activities"] if event["action"] == "退回补充"]
        self.assertEqual([event["detail"] for event in returns], ["请补充可核对的原文。"])

    def test_element_detail_reads_null_activity_and_preserves_visible_source_text(self) -> None:
        builder, _, entity = self.legacy_entity()
        response = builder.get("/api/v2/elements/" + entity["id"])
        self.assertEqual(response.status_code, 200, response.text)
        detail = response.json()["data"]
        self.assertEqual(detail["card"]["one_liner"], "付款前核对合同。")
        refs = detail["entity"]["payload"]["source_refs"]
        self.assertEqual(refs, [{"excerpt": TEXT_SOURCE}, DICT_SOURCE])
        activity = next(event for event in detail["entity"]["activities"] if event["action"] == "修订")
        self.assertEqual(activity["detail"], "")
        self.assertTrue(any(version["reason"] is None for version in detail["versions"]))
        stored = builder.get("/api/entities/" + entity["id"]).json()
        self.assertEqual(stored, entity)

    def test_single_legacy_text_or_dictionary_source_is_readable(self) -> None:
        for source, expected in ((TEXT_SOURCE, {"excerpt": TEXT_SOURCE}), (DICT_SOURCE, DICT_SOURCE)):
            with self.subTest(source=source):
                builder, reviewer, entity = self.legacy_entity(source)
                review = reviewer.get("/api/v2/reviews/" + entity["id"])
                self.assertEqual(review.status_code, 200, review.text)
                refs = review.json()["data"]["source_refs"]
                self.assertEqual(len(refs), 1)
                for key, value in expected.items():
                    self.assertEqual(refs[0][key], value)
                detail = builder.get("/api/v2/elements/" + entity["id"])
                self.assertEqual(detail.status_code, 200, detail.text)
                self.assertEqual(detail.json()["data"]["entity"]["payload"]["source_refs"], [expected])

    def test_unverified_legacy_sources_and_invalid_decisions_still_cannot_publish(self) -> None:
        builder, reviewer, entity = self.legacy_entity()
        path = f"/api/v2/reviews/{entity['id']}:decide"
        review = reviewer.get("/api/v2/reviews/" + entity["id"])
        self.assertEqual(review.status_code, 200, review.text)
        gates = review.json()["data"]["gates"]
        self.assertFalse(gates["passed"])
        self.assertEqual([check["code"] for check in gates["checks"] if check["status"] == "fail"], ["QG1"])
        # R4 D2 replaces the evidence-only 422 with the shared gate conflict.
        # The legacy source must still be rejected, with every stored value intact.
        for body, status, code in (
            ({"decision": "approve_publish", "version": entity["version"]}, 409, "GATE_NOT_PASSED"),
            ({"decision": "return", "version": entity["version"], "reason": None}, 422, "VALIDATION_FAILED"),
            ({"decision": "return", "version": entity["version"], "reason": "  "}, 422, "VALIDATION_FAILED"),
            (
                {"decision": "return", "version": entity["version"], "reason": "补充", "source_refs": MIXED_SOURCES},
                422,
                "VALIDATION_FAILED",
            ),
        ):
            with self.subTest(body=body):
                response = reviewer.post(path, json=body)
                self.assertEqual(response.status_code, status, response.text)
                self.assertEqual(response.json()["error"]["code"], code)
                stored = builder.get("/api/entities/" + entity["id"])
                self.assertEqual(stored.status_code, 200, stored.text)
                self.assertEqual(stored.json(), entity)
