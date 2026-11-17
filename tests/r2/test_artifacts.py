"""Report trust boundary, numerical checks and safe, reproducible exports."""

import copy
import io
import json
import os
import secrets
import unittest
import zipfile
from types import SimpleNamespace
from unittest.mock import Mock, patch

from docx import Document
from fastapi import HTTPException
from pydantic import ValidationError

from backend.agent_runtime import SESSIONS, _configuration_signature
from backend.capabilities.artifacts import ArtifactCreate
from backend.domains.artifacts import render, service
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
            # R2.1 explicitly excludes bare numbers, calendar dates and labels.
            "时限为99。",
            "办理30日，另加99。",
            "自2025-07-01起。",
            "来源版本：99。",
            "第 1 条、第2款、第 3 章。\n1. 办理30日。",
            "1、办理30日。\n（2）金额10000元。\n来源版本：1。",
        ):
            value["blocks"][0]["text"] = text
            render.verify(value)
        for text in (
            "时限九十九日。",
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

    def test_quantity_scales_units_and_money_only_tolerance(self) -> None:
        value = spec()
        for text in ("30天。", "30日。", "金额10050元。"):
            value["blocks"][0]["text"] = text
            render.verify(value)
        for text in ("30万家。", "30家。", "30倍。", "需核对2项。", "30.1日。", "金额10051元。"):
            value["blocks"][0]["text"] = text
            with self.subTest(text=text), self.assertRaises(HTTPException):
                render.verify(value)
        value["citations"]["c1"]["quote"] += "共有300000家、200项，限额三十万元，比例30倍。"
        for text in ("30万家。", "三十万家。", "200项。", "30万元。", "300000元。", "30倍。"):
            value["blocks"][0]["text"] = text
            render.verify(value)
        for text in ("30家。", "200.5项。", "199项。", "300001家。", "30.1倍。"):
            value["blocks"][0]["text"] = text
            with self.subTest(text=text), self.assertRaises(HTTPException):
                render.verify(value)
        self.assertEqual(render.quantities("30万家"), [(300000, "家")])
        self.assertEqual(render.quantities("30万"), [(300000, "")])
        self.assertEqual(render.quantities("2026年、2025/07/01、7月1日、第 1 条、第2款、99"), [])

    def test_actual_references_are_resolved_per_paragraph_and_table(self) -> None:
        value = spec()
        value["citations"]["c1"].update(entity_id="rule_first", title="付款办理规则")
        value["citations"]["c2"] = {
            **value["citations"]["c1"],
            "entity_id": "rule_second",
            "title": "复核办理规则",
            "source_title": "复核管理制度",
            "material_id": "material_second",
            "quote": "复核应在60日内完成。",
        }
        answer = (
            "应在30日内办理。[依据](/elements/rule_first)\n\n"
            "按《复核办理规则》应在60天内复核。\n\n"
            "|事项|时限（日）|依据|\n|---|---|---|\n|付款|30|rule_first|"
        )
        value["blocks"] = render.blocks_from_answer(answer, value["citations"])
        self.assertEqual([block["citations"] for block in value["blocks"]], [["c1"], ["c2"], ["c1"]])
        render.verify(value)
        for index in (0, 2):
            wrong = copy.deepcopy(value)
            if index == 0:
                wrong["blocks"][0]["text"] = "付款时限60日。"
            else:
                wrong["blocks"][2]["rows"][0][1] = "60"
            with self.subTest(index=index), self.assertRaises(HTTPException):
                render.verify(wrong)
        uncited_table = copy.deepcopy(value)
        uncited_table["blocks"] = render.blocks_from_answer(
            "应在30日办理，依据rule_first。\n\n|时限（日）|\n|---|\n|30|", value["citations"]
        )
        self.assertEqual(uncited_table["blocks"][1]["citations"], [])
        with self.assertRaises(HTTPException):
            render.verify(uncited_table)
        self.assertEqual(render.cited_sources("依据《复核管理制度》", value["citations"]), ["c2"])
        self.assertEqual(render.cited_sources("rule_first_extra", value["citations"]), [])
        value["blocks"] = render.blocks_from_answer("无引用仍声称30日。", value["citations"])
        self.assertEqual(value["blocks"][0]["citations"], [])
        with self.assertRaises(HTTPException):
            render.verify(value)
        value["citations"]["c2"]["title"] = value["citations"]["c1"]["title"]
        self.assertEqual(render.cited_sources("《付款办理规则》", value["citations"]), [])

    def test_chinese_fractional_scales_and_column_units(self) -> None:
        for text, expected in (("三十点五万元", 305000), ("零点八万元", 8000), ("壹点伍万元", 15000)):
            with self.subTest(text=text):
                self.assertEqual(render.quantities(text), [(expected, "元")])
                value = spec()
                value["citations"]["c1"]["quote"] += f"金额{expected}元。"
                value["blocks"][0]["text"] = text
                render.verify(value)
                value["citations"]["c1"]["quote"] = "30日内办理，金额80000元。"
                with self.assertRaises(HTTPException):
                    render.verify(value)
        self.assertEqual(render.quantities("三十万"), [(300000, "")])
        self.assertEqual(render.quantities("三十点五万"), [(305000, "")])
        self.assertEqual(render.quantities("三十点五万", "家"), [(305000, "家")])
        self.assertEqual(render.quantities("30万", "家"), [(300000, "家")])
        self.assertEqual(render.quantities("30万", "万元"), [(300000, "元")])
        self.assertEqual(render.quantities("2026", "年"), [])
        value = spec()
        value["citations"]["c1"]["quote"] += "共有300000家。"
        value["blocks"][1].update(columns=["数量（家）", "年份"], rows=[["30万", "2026"]])
        render.verify(value)
        value["blocks"][0]["text"] = "共有三十万。"
        with self.assertRaises(HTTPException):
            render.verify(value)
        value["citations"]["c1"]["quote"] += "规模30万。"
        render.verify(value)

    def test_table_rows_cannot_borrow_each_others_sources(self) -> None:
        value = spec()
        value["citations"]["c1"].update(entity_id="rule_first", title="付款规则")
        value["citations"]["c2"] = {
            **value["citations"]["c1"],
            "entity_id": "rule_second",
            "title": "复核规则",
            "quote": "复核时限60日。",
        }
        answer = "|事项|时限（日）|依据|\n|---|---|---|\n|付款|30|rule_first|\n|复核|60|rule_second|"
        value["blocks"] = render.blocks_from_answer(answer, value["citations"])
        self.assertEqual(value["blocks"][0]["row_citations"], [["c1"], ["c2"]])
        render.verify(value)
        legacy = copy.deepcopy(value)
        legacy["blocks"][0].pop("row_citations")
        render.verify(legacy)  # Existing saved tables remain renderable.
        for wrong_answer in (
            answer.replace("|付款|30|", "|付款|60|"),
            answer.replace("|复核|60|", "|复核|30|"),
            answer.replace("|复核|60|rule_second|", "|复核|30|无引用|"),
        ):
            value["blocks"] = render.blocks_from_answer(wrong_answer, value["citations"])
            with self.subTest(answer=wrong_answer), self.assertRaises(HTTPException):
                render.verify(value)

    def test_material_name_comes_from_verified_material_not_entity_payload(self) -> None:
        conn = Mock()
        checked = {
            "material_id": "material_a",
            "quote": "30日办理。",
            "anchor_id": "anchor_a",
            "source_version": "source_a",
        }
        conn.execute.return_value.fetchone.side_effect = [
            {"id": "rule_a", "title": "成果名称", "version": 1, "payload": json.dumps({"source_refs": [checked]})},
            {"title": "原始制度名称"},
        ]
        with (
            patch.object(service, "require_context", return_value=SimpleNamespace(tenant_id="hq")),
            patch.object(service.provenance, "normalize_source_ref", return_value=checked),
        ):
            sources = service._citations(
                conn, {"provenance": [{"kind": "ontology", "tenant_id": "hq", "entity_id": "rule_a", "version": 1}]}
            )
        self.assertEqual(sources["c1"]["source_title"], "原始制度名称")
        self.assertEqual(render.cited_sources("依据《原始制度名称》", sources), ["c1"])
        self.assertEqual(conn.execute.call_args.args[1], ("material_a",))

    def test_word_filters_xml_controls_in_every_text_location(self) -> None:
        value = spec()
        bad = "\x00\x01\x08\x0b\x0c\x0e\x1f\ud800\ufffe\uffff"
        unsupported = copy.deepcopy(value)
        unsupported["blocks"][0]["text"] = "99\x00日。"
        with self.assertRaises(HTTPException):
            render.docx(unsupported)
        value["title"] += bad
        value["source_label"] += bad
        value["blocks"][0]["text"] += bad + "保留\t制表符\n换行"
        value["blocks"][1]["columns"][0] += bad
        value["blocks"][1]["rows"][0][0] += bad
        for key in ("title", "quote", "locator", "path"):
            value["citations"]["c1"][key] += bad
        binary = render.docx(value)
        document = Document(io.BytesIO(binary))
        self.assertIn("制度报告", document.paragraphs[0].text)
        self.assertEqual(len(document.tables), 1)
        self.assertIn("保留\t制表符\n换行", "\n".join(p.text for p in document.paragraphs))
        with zipfile.ZipFile(io.BytesIO(binary)) as archive:
            xml = archive.read("word/document.xml").decode()
            for character in bad:
                self.assertNotIn(character, xml)

    def test_environment_key_never_persisted_even_when_verified(self) -> None:
        from backend import ai
        from backend import skill_harness as harness

        with (
            patch.dict(os.environ, {"DEEPSEEK_API_KEY": "synthetic-r2-memory-only", "ONTOLOGY_MODEL_FROM_ENV": "1"}),
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
                "answer": (
                    f"付款前必须核对合同约定及附件。[依据](/elements/{entity_id})\n\n"
                    f"|事项|要求|依据|\n|---|---|---|\n|付款|核对合同约定及附件|{entity_id}|"
                ),
                "provenance": [
                    {"kind": "ontology", "tenant_id": person.tenant_id, "entity_id": entity_id, "version": 4}
                ],
                "_report_configuration_signature": _configuration_signature(),
                "report_source_kind": "mock",
            }
        body = {"session_id": session["id"], "run_id": run_id}
        other = reviewer.post("/api/v2/capabilities/artifact.create:invoke", json=body)
        self.assertEqual(other.status_code, 404)
        with use_context(person):
            referenced_answer = session["runs"][run_id]["answer"]
            session["runs"][run_id]["answer"] = "付款前必须核对合同约定及附件。"
        missing = builder.post("/api/v2/capabilities/artifact.create:invoke", json=body)
        self.assertEqual(missing.status_code, 422)
        self.assertEqual(missing.json()["error"]["code"], "EVIDENCE_NOT_FOUND")
        self.assertEqual(missing.json()["error"]["details"]["code"], "CITATION_UNVERIFIED")
        self.assertTrue(missing.json()["error"]["details"]["issues"])
        with use_context(person):
            session["runs"][run_id]["answer"] = referenced_answer
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
            session["runs"][run_id]["answer"] = f"付款时限为99日，依据{entity_id}。"
        blocked = builder.post("/api/v2/capabilities/artifact.create:invoke", json=body)
        self.assertEqual(blocked.status_code, 422)
        self.assertEqual(blocked.json()["error"]["code"], "NUMERIC_CHECK_FAILED")
        self.assertEqual(blocked.json()["error"]["details"]["code"], "NUMERIC_UNVERIFIED")
        self.assertIn("99日", blocked.json()["error"]["details"]["issues"][0])
        with use_context(person):
            session["runs"][run_id] = original
            session["runs"][run_id]["provenance"][0]["version"] = 3
        blocked = builder.post("/api/v2/capabilities/artifact.create:invoke", json=body)
        self.assertEqual(blocked.status_code, 409)
        SESSIONS.sessions.pop(session["id"], None)
