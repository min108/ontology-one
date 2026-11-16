"""Report trust boundary, numerical checks and safe, reproducible exports."""

import copy
import io
import os
import secrets
import unittest
import zipfile
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError

from backend.agent_runtime import SESSIONS, _configuration_signature
from backend.capabilities.artifacts import ArtifactCreate
from backend.domains.artifacts import render
from backend.security import store
from backend.security.context import use_context
from tests.r2 import test_build as construction


def spec() -> dict:
    return {
        "title": "<script>alert('x')</script> 制度报告",
        "source_label": "模拟模型演练结果",
        "blocks": [
            {"type": "paragraph", "text": "应在30日内办理，金额不超过1万元。", "citations": ["c1"]},
            {
                "type": "table",
                "columns": ["项目", "时限（日）"],
                "rows": [['=HYPERLINK("https://example.invalid")', "30"]],
                "citations": ["c1"],
            },
        ],
        "citations": {
            "c1": {
                "title": "<img src=x onerror=alert(1)>",
                "quote": "应在30日内办理，金额不超过10000元。",
                "version": 1,
                "locator": "第一条",
                "path": "/materials/demo?anchor=one",
            }
        },
    }


class Renderers(unittest.TestCase):
    def test_safe_html_word_injection_and_official_layout(self) -> None:
        value = spec()
        html = render.html(value).decode()
        self.assertNotIn("<script>", html)
        self.assertNotIn("<img ", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("Content-Security-Policy", html)
        self.assertIn('href="#c1"', html)
        binary = render.docx(value)
        self.assertEqual(binary, render.docx(value))
        with zipfile.ZipFile(io.BytesIO(binary)) as archive:
            document = archive.read("word/document.xml").decode()
            self.assertIn("HYPERLINK", document)  # Literal text survives.
            self.assertNotIn('w:instr="HYPERLINK', document)
            self.assertIn('w:anchor="c1"', document)
            self.assertIn('w:line="600"', document)
            self.assertIn('w:eastAsia="仿宋_GB2312"', document)
            self.assertIn('w:top="2098"', document)
            self.assertIn('w:instr="PAGE"', archive.read("word/footer1.xml").decode())

    def test_numbers_block_in_paragraph_title_and_table(self) -> None:
        for location in ("paragraph", "table", "title"):
            value = spec()
            if location == "paragraph":
                value["blocks"][0]["text"] = "办理时限为99日。"
            elif location == "table":
                value["blocks"][1]["rows"][0][1] = "99"
            else:
                value["title"] = "办理时限99日的说明"
            with self.subTest(location=location), self.assertRaises(HTTPException) as failed:
                render.html(value)
            self.assertEqual(failed.exception.status_code, 422)
            self.assertEqual(failed.exception.detail["code"], "NUMERIC_UNVERIFIED")
        value = spec()
        value["blocks"][0]["text"] = "金额不超过1.005万元。"
        render.verify(value)
        value["blocks"][0]["text"] = "金额不超过1.006万元。"
        with self.assertRaises(HTTPException):
            render.verify(value)

    def test_contract_and_table_shape(self) -> None:
        with self.assertRaises(ValidationError):
            ArtifactCreate.model_validate({"session_id": "a" * 32, "run_id": "b" * 32, "answer": "forged"})
        blocks = render.blocks_from_answer("# 汇报\n\n|事项|要求|\n|---|---|\n|办理|30日|\n\n结束。")
        self.assertEqual([b["type"] for b in blocks], ["paragraph", "table", "paragraph"])
        with self.assertRaises(HTTPException):
            render.blocks_from_answer("|a|b|\n|---|---|\n|x|")

    def test_chinese_bare_and_mixed_numbers_and_dates(self) -> None:
        value = spec()
        value["citations"]["c1"]["quote"] += " 自2026年7月1日起，费率为0.8%。"
        for text in (
            "应在三十日内办理，不超过壹万元。",
            "费率百分之零点八。",
            "1. 办理时限为30。[c1]",
            "自2026-07-01起。",
            "1、办理30日。\n（2）金额10000元。\n来源版本：1。",
        ):
            value["blocks"][0]["text"] = text
            render.verify(value)
        for text in (
            "时限九十九日。",
            "时限为99。",
            "办理30日，另加99。",
            "自2025-07-01起。",
            "来源版本：99。",
            "事项有2项。",
        ):
            value["blocks"][0]["text"] = text
            with self.subTest(text=text), self.assertRaises(HTTPException):
                render.verify(value)
        value["blocks"][0]["text"] = "核对原文。"
        value["blocks"][1]["rows"][0][1] = "30日及99"
        with self.assertRaises(HTTPException):
            render.verify(value)
        value["blocks"][1]["columns"][1] = "金额（万元）"
        value["blocks"][1]["rows"][0][1] = "1"
        render.verify(value)

    def test_environment_key_never_persisted_even_when_verified(self) -> None:
        from backend import ai
        from backend import skill_harness as harness

        with (
            patch.dict(os.environ, {"DEEPSEEK_API_KEY": "synthetic-r2-memory-only"}),
            patch.object(harness, "_principal"),
            patch.object(harness, "harness_status", return_value={}),
            patch.object(harness, "write_bytes") as write,
        ):
            value = harness._configuration()
            self.assertEqual(value["api_key"], "synthetic-r2-memory-only")
            harness._mark_verified(None, value["revision"])
            self.assertTrue(harness._configuration()["verified"])
            with self.assertRaises(HTTPException):
                harness._save_configuration(harness.HarnessConfiguration())
            with patch.object(ai, "require_admin"), self.assertRaises(HTTPException):
                ai._save_configuration(ai.AIConfiguration())
            write.assert_not_called()


@unittest.skipUnless(os.getenv("RUN_JOBS_MYSQL_TESTS") == "1", "opt-in MySQL integration test")
class ArtifactMySQL(unittest.TestCase):
    setUpClass = classmethod(construction.BuildMySQL.setUpClass.__func__)
    tearDownClass = classmethod(construction.BuildMySQL.tearDownClass.__func__)
    login = construction.BuildMySQL.login
    invoke = construction.BuildMySQL.invoke
    proposal = construction.BuildMySQL.proposal

    def test_private_reports_versions_and_tenant_boundaries(self) -> None:
        builder, reviewer = self.login("fixture.builder"), self.login("fixture.reviewer")
        changeset = self.proposal(builder)
        self.invoke(
            builder,
            "changeset.decide_items",
            {"changeset_id": changeset["id"], "item_ids": [changeset["items"][0]["id"]], "decision": "accepted"},
        )
        entity_id = self.invoke(builder, "changeset.apply_and_submit", {"changeset_id": changeset["id"]})["entity_ids"][
            0
        ]
        response = reviewer.post(
            f"/api/v2/reviews/{entity_id}:decide", json={"decision": "approve_publish", "version": 2}
        )
        self.assertEqual(response.status_code, 200, response.text)
        person = store.authenticate_session(builder.cookies.get("ontology_session"), "r2-test")[0]
        with use_context(person):
            session = SESSIONS.create(person, "【演示】付款制度报告")
            run_id = secrets.token_hex(16)
            session["runs"][run_id] = {
                "id": run_id,
                "status": "completed",
                "answer": "付款前必须核对合同约定及附件。\n\n|事项|要求|\n|---|---|\n|付款|核对合同约定及附件|",
                "provenance": [
                    {"kind": "ontology", "tenant_id": person.tenant_id, "entity_id": entity_id, "version": 4}
                ],
                "_report_configuration_signature": _configuration_signature(),
                "report_source_kind": "mock",
            }
        body = {"session_id": session["id"], "run_id": run_id}
        other = reviewer.post("/api/v2/capabilities/artifact.create:invoke", json=body)
        self.assertEqual(other.status_code, 404)
        report = self.invoke(builder, "artifact.create", body)
        self.assertEqual(report["source_kind"], "mock")
        ident = report["id"]
        for client in (reviewer, self.login("r2.other")):
            self.assertEqual(client.get(f"/api/v2/artifacts/{ident}").status_code, 404)
            self.assertEqual(client.get(f"/api/v2/artifacts/{ident}/versions/1/files/html").status_code, 404)
        for format in ("html", "docx"):
            response = builder.get(f"/api/v2/artifacts/{ident}/versions/1/files/{format}")
            self.assertEqual(response.status_code, 200, response.text[:300] if format == "html" else "Word response")
            self.assertIn("attachment", response.headers["content-disposition"])
        regenerated = self.invoke(builder, "artifact.render", {"artifact_id": ident, "expected_version": 1})
        self.assertEqual(regenerated["current_version"], 2)
        stale = builder.post(f"/api/v2/artifacts/{ident}:render", json={"artifact_id": ident, "expected_version": 1})
        self.assertEqual(stale.status_code, 409)
        with use_context(person):
            original = copy.deepcopy(session["runs"][run_id])
            session["runs"][run_id]["answer"] = "付款时限为99日。"
        blocked = builder.post("/api/v2/capabilities/artifact.create:invoke", json=body)
        self.assertEqual(blocked.status_code, 422)
        with use_context(person):
            session["runs"][run_id] = original
            session["runs"][run_id]["provenance"][0]["version"] = 3
        blocked = builder.post("/api/v2/capabilities/artifact.create:invoke", json=body)
        self.assertEqual(blocked.status_code, 409)
        SESSIONS.sessions.pop(session["id"], None)
