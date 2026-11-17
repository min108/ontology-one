"""Specialized publication uses the real five checks without sacrificing package guards."""

import json
import os
import secrets
import unittest
from contextlib import closing
from unittest.mock import patch

from fastapi.testclient import TestClient
from httpx import Response

from backend import authority, db, skills
from backend.kernel.metadata import sync_meta
from backend.provenance import normalize_source_ref
from backend.security import store
from backend.security.context import use_context
from tests.r2 import test_build as build_tests
from tests.test_skills import MANIFEST


class SpecializedFacts(unittest.TestCase):
    def test_authority_mapping_requires_explicit_conditions_and_exceptions(self) -> None:
        spec = authority.Spec(purpose="真实目的", scope="适用范围", effective_from="2026-01-01").model_dump()
        result = authority.make_payload(spec)
        self.assertEqual(result["definition"], "真实目的")
        self.assertEqual(result["conditions"], "")
        self.assertEqual(result["exceptions"], "")
        with self.assertRaises(AssertionError):
            self.assertEqual(result["conditions"], "自动补写通过条件")
        spec.update(conditions="收到申请时", exceptions="原文规定的特殊情况")
        result = authority.make_payload(spec)
        self.assertEqual(result["conditions"], "收到申请时")
        self.assertEqual(result["exceptions"], "原文规定的特殊情况")

    def test_skill_projection_reads_actual_manifest_and_preserves_evidence(self) -> None:
        ref = {"material_id": "M", "quote": "原话"}
        manifest = {**MANIFEST, "source_refs": [ref]}
        document = {
            "files": {"SKILL.md": "---\nname: test-skill\ndescription: 已声明的业务方法\n---\n# 办理方法\n"},
            "manifest": manifest,
        }
        result = skills.gate_payload({"payload": {"managed_skill": True}}, document)
        self.assertEqual(result["definition"], "已声明的业务方法")
        self.assertEqual(result["steps"], manifest["steps"])
        self.assertEqual(result["source_refs"], [ref])
        with self.assertRaises(AssertionError):
            self.assertEqual(result["source_refs"], [])


@unittest.skipUnless(os.getenv("RUN_JOBS_MYSQL_TESTS") == "1", "opt-in MySQL integration test")
class SpecializedGatesMySQL(unittest.TestCase):
    setUpClass = classmethod(build_tests.BuildMySQL.setUpClass.__func__)
    tearDownClass = classmethod(build_tests.BuildMySQL.tearDownClass.__func__)
    login = build_tests.BuildMySQL.login

    def source(self, client: TestClient) -> dict:
        quote = "【合成制度】本机构办理合成业务时逐项核对输入并形成结果，无其他例外，自2026年1月1日起生效。"
        response = client.post(
            "/api/materials/upload", files={"file": ("R4合成制度.txt", quote.encode(), "text/plain")}
        )
        self.assertEqual(response.status_code, 200, response.text)
        material_id = response.json()["id"]
        person = store.authenticate_session(client.cookies.get("ontology_session"), "r2-test")[0]
        with use_context(person), closing(db.connect()) as conn, conn:
            conn.execute("UPDATE entities SET source_kind='regulation' WHERE id=?", (material_id,))
            material = db.entity_json(conn.execute("SELECT * FROM entities WHERE id=?", (material_id,)).fetchone())
            sync_meta(conn, material, {"subtype": "regulation"})
            version = conn.execute("SELECT id FROM source_versions WHERE material_id=?", (material_id,)).fetchone()[0]
            return normalize_source_ref(
                conn, material_id, {"source_version": version, "quote": quote, "start": 0, "end": len(quote)}
            )

    def skill(
        self, client: TestClient, *, evidence: bool = True, declaration: bool = True, dependency: dict | None = None
    ) -> dict:
        name = "r4-specialized-" + secrets.token_hex(3)
        response = client.post("/api/skills", json={"title": name, "package_name": name})
        self.assertEqual(response.status_code, 200, response.text)
        detail = response.json()
        manifest = {
            **MANIFEST,
            "dependencies": [dependency] if dependency else [],
            "source_refs": [self.source(client)] if evidence else [],
            "dependency_note": "本合成技能仅使用显式输入，不依赖其他要素。" if declaration else "",
        }
        response = client.put(
            "/api/skills/" + detail["entity"]["id"],
            json={
                "expected_version": detail["entity"]["version"],
                "title": name,
                "files": detail["files"],
                "manifest": manifest,
                "reason": "明确保存完整合成业务方法",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        detail = response.json()
        response = client.post(
            "/api/skills/" + detail["entity"]["id"] + "/test", json={"expected_version": detail["entity"]["version"]}
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["outcome"], "passed")
        return detail

    def action(self, client: TestClient, detail: dict, action: str) -> Response:
        return client.post(
            "/api/skills/" + detail["entity"]["id"] + "/action",
            json={
                "expected_version": detail["entity"]["version"],
                "action": action,
                "reason": "独立核对合成方法与原文",
            },
        )

    def test_authorized_incomplete_skill_still_has_card_with_failed_package_gates(self) -> None:
        builder = self.login("fixture.builder")
        detail = self.skill(builder)
        ident = detail["entity"]["id"]
        person = store.authenticate_session(builder.cookies.get("ontology_session"), "r2-test")[0]
        with use_context(person), closing(db.connect()) as conn, conn:
            conn.execute("DELETE FROM skill_documents WHERE entity_id=?", (ident,))
            original = db.entity_json(conn.execute("SELECT * FROM entities WHERE id=?", (ident,)).fetchone())
            facts = skills.skill_gate_facts(conn, original)
            self.assertEqual(facts["payload"], original["payload"])
            self.assertIs(facts["tests"], False)
            self.assertIs(facts["relations"], False)
        response = builder.post("/api/v2/capabilities/element.get:invoke", json={"entity_id": ident})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()["data"]
        self.assertEqual(result["entity"]["id"], ident)
        self.assertTrue(result["card"]["one_liner"])
        outcomes = {item["code"]: item["status"] for item in result["gates"]["checks"]}
        self.assertEqual({code: outcomes[code] for code in ("QG3", "QG4")}, {"QG3": "fail", "QG4": "fail"})
        with self.assertRaises(AssertionError):
            self.assertEqual(outcomes["QG3"], "pass")
        # Specialized mutation and cross-tenant read guards keep their old behavior.
        response = self.action(builder, detail, "submit")
        self.assertEqual(response.status_code, 404, response.text)
        other = self.login("r2.other")
        response = other.post("/api/v2/capabilities/element.get:invoke", json={"entity_id": ident})
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(builder.get("/api/entities/" + ident).json()["status"], "draft")

    def test_skill_valid_facts_publish_and_independent_review_remains(self) -> None:
        builder, reviewer = self.login("fixture.builder"), self.login("fixture.reviewer")
        detail = self.skill(builder)
        submitted = self.action(builder, detail, "submit")
        self.assertEqual(submitted.status_code, 200, submitted.text)
        detail = submitted.json()
        denied = self.action(builder, detail, "publish")
        self.assertEqual(denied.status_code, 403, denied.text)
        result = self.action(reviewer, detail, "publish")
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["entity"]["status"], "published")
        with self.assertRaises(AssertionError):
            self.assertEqual(result.json()["entity"]["status"], "draft")

    def test_missing_skill_evidence_or_dependency_declaration_blocks_submit(self) -> None:
        builder = self.login("fixture.builder")
        for evidence, declaration in ((False, True), (True, False)):
            with self.subTest(evidence=evidence, declaration=declaration):
                detail = self.skill(builder, evidence=evidence, declaration=declaration)
                response = self.action(builder, detail, "submit")
                self.assertEqual(response.status_code, 409, response.text)
                failed = {
                    item["code"] for item in response.json()["detail"]["gates"]["checks"] if item["status"] == "fail"
                }
                self.assertIn("QG1" if not evidence else "QG4", failed)
                current = builder.get("/api/skills/" + detail["entity"]["id"]).json()
                self.assertEqual(current["entity"]["status"], "draft")

    def test_candidate_dependency_is_not_gate_confirmation(self) -> None:
        builder = self.login("fixture.builder")
        person = store.authenticate_session(builder.cookies.get("ontology_session"), "r2-test")[0]
        with use_context(person), closing(db.connect()) as conn:
            target = conn.execute(
                "SELECT id,kind,version FROM entities WHERE kind='data' AND status='published' LIMIT 1"
            ).fetchone()
        detail = self.skill(
            builder, dependency={"entity_id": target["id"], "kind": target["kind"], "version": target["version"]}
        )
        ident = detail["entity"]["id"]
        with use_context(person), closing(db.connect()) as conn, conn:
            row = conn.execute("SELECT * FROM relations WHERE source=?", (ident,)).fetchone()
            self.assertEqual(row["status"], "confirmed")
            self.assertIs(json.loads(row["payload"])["derived"], True)
            conn.execute("UPDATE relations SET status='candidate' WHERE id=?", (row["id"],))
        rejected = self.action(builder, detail, "submit")
        self.assertEqual(rejected.status_code, 409, rejected.text)
        self.assertIn(
            "QG4", {item["code"] for item in rejected.json()["detail"]["gates"]["checks"] if item["status"] == "fail"}
        )

    def complete_authority(self, builder: TestClient, title: str) -> dict:
        return {
            "title": title,
            "org_id": "dept-01",
            "spec": {
                "type": "position",
                "subtype": "合成职责",
                "purpose": "逐项核对合成业务输入",
                "basis": "R4合成制度",
                "scope": "本机构合成业务",
                "conditions": "办理所述合成业务时",
                "exceptions": "无其他例外",
                "effective_from": "2026-01-01",
                "org_ids": ["dept-01"],
                "sections": [{"id": "one", "title": "核对职责", "content": "逐项核对输入并形成结果"}],
                "source_refs": [self.source(builder)],
            },
        }

    def test_complete_authority_first_save_can_submit_without_intermediate_edit(self) -> None:
        builder, reviewer = self.login("fixture.builder"), self.login("fixture.reviewer")
        body = self.complete_authority(builder, "R4首次保存直接提交职责")
        response = builder.post("/api/authority/assets", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        entity = response.json()["entity"]
        ident = entity["id"]
        self.assertEqual(entity["version"], 1)
        person = store.authenticate_session(builder.cookies.get("ontology_session"), "r2-test")[0]
        with use_context(person), closing(db.connect()) as conn:
            row = conn.execute("SELECT version,snapshot,reason FROM versions WHERE entity_id=?", (ident,)).fetchone()
            self.assertIsNotNone(row)
            saved = json.loads(row["snapshot"])
            self.assertEqual(row["version"], 1)
            self.assertEqual(saved["payload"]["authority_spec"]["conditions"], body["spec"]["conditions"])
            self.assertEqual(saved["_meta"]["valid_from"], "2026-01-01")
            self.assertEqual(saved["status"], "draft")
            self.assertTrue(row["reason"].strip())
        for actor, action in ((builder, "submit"), (reviewer, "approve"), (reviewer, "publish")):
            response = actor.post(
                "/api/entities/" + ident + "/action",
                json={"action": action, "expected_version": entity["version"], "reason": "独立核对首次保存的完整规范"},
            )
            self.assertEqual(response.status_code, 200, response.text)
            entity = response.json()
        self.assertEqual(entity["status"], "published")
        with self.assertRaises(AssertionError):
            self.assertEqual(entity["status"], "draft")

    def test_skill_creation_preserves_initial_metadata_snapshot(self) -> None:
        builder = self.login("fixture.builder")
        response = builder.post("/api/skills", json={"title": "R4首次技能保存", "package_name": "r4-first-skill"})
        self.assertEqual(response.status_code, 200, response.text)
        entity = response.json()["entity"]
        person = store.authenticate_session(builder.cookies.get("ontology_session"), "r2-test")[0]
        with use_context(person), closing(db.connect()) as conn:
            rows = conn.execute(
                "SELECT version,snapshot,reason FROM versions WHERE entity_id=?", (entity["id"],)
            ).fetchall()
        self.assertEqual(len(rows), 1)
        saved = json.loads(rows[0]["snapshot"])
        self.assertEqual(saved["version"], 1)
        self.assertEqual(saved["status"], "draft")
        self.assertIn("_meta", saved)
        self.assertEqual(saved["payload"]["package_digest"], response.json()["digest"])
        self.assertTrue(rows[0]["reason"].strip())
        with self.assertRaises(AssertionError):
            self.assertEqual(len([]), 1)

    def test_initial_snapshot_failure_rolls_back_specialized_creation(self) -> None:
        builder = self.login("fixture.builder")
        title = "R4原子创建失败不得留存"
        body = self.complete_authority(builder, title)
        person = store.authenticate_session(builder.cookies.get("ontology_session"), "r2-test")[0]
        for endpoint, request in (
            ("/api/authority/assets", body),
            ("/api/skills", {"title": title, "package_name": "r4-rollback-skill"}),
        ):
            with (
                self.subTest(endpoint=endpoint),
                patch(
                    "backend.kernel.elements.snapshot", side_effect=RuntimeError("synthetic snapshot storage failure")
                ),
                self.assertLogs("backend.security.middleware", level="ERROR"),
            ):
                response = builder.post(endpoint, json=request)
            self.assertEqual(response.status_code, 500, response.text)
            with use_context(person), closing(db.connect()) as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM entities WHERE title=?", (title,)).fetchone()[0], 0)
                self.assertEqual(
                    conn.execute(
                        "SELECT count(*) FROM versions WHERE json_extract(snapshot,'$.title')=?", (title,)
                    ).fetchone()[0],
                    0,
                )

    def test_authority_actual_spec_facts_pass_and_missing_condition_blocks(self) -> None:
        builder, reviewer = self.login("fixture.builder"), self.login("fixture.reviewer")
        for condition in ("", "办理所述合成业务时"):
            with self.subTest(condition=condition):
                spec = {
                    "type": "position",
                    "subtype": "合成职责",
                    "purpose": "按合成制度核对输入",
                    "basis": "R4合成制度",
                    "scope": "本机构合成业务",
                    "conditions": condition,
                    "exceptions": "无其他例外",
                    "effective_from": "2026-01-01",
                    "org_ids": ["dept-01"],
                    "sections": [{"id": "one", "title": "核对职责", "content": "逐项核对输入并形成结果"}],
                    "source_refs": [self.source(builder)],
                }
                response = builder.post(
                    "/api/authority/assets", json={"title": "R4职责" + condition, "org_id": "dept-01", "spec": spec}
                )
                self.assertEqual(response.status_code, 200, response.text)
                detail = response.json()
                ident = detail["entity"]["id"]
                saved = builder.put(
                    "/api/authority/assets/" + ident,
                    json={
                        "title": detail["entity"]["title"],
                        "org_id": "dept-01",
                        "spec": spec,
                        "expected_version": detail["entity"]["version"],
                    },
                )
                self.assertEqual(saved.status_code, 200, saved.text)
                entity = saved.json()["entity"]
                for actor, action in ((builder, "submit"), (reviewer, "approve"), (reviewer, "publish")):
                    response = actor.post(
                        "/api/entities/" + ident + "/action",
                        json={"action": action, "expected_version": entity["version"], "reason": "独立审核已保存原文"},
                    )
                    self.assertEqual(response.status_code, 200 if condition else 409, response.text)
                    if not condition:
                        self.assertEqual(builder.get("/api/entities/" + ident).json()["status"], "draft")
                        break
                    entity = response.json()
                if condition:
                    self.assertEqual(entity["status"], "published")
