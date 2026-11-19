"""AC7. A discarded model quote can recover only an explicitly listed, immutable source."""

import asyncio
import json
import os
import unittest
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import db, provenance
from backend.api.v2.routes import registry
from backend.capabilities.context import CapabilityContext
from backend.core.errors import ApiError
from backend.domains import build
from backend.domains.build_sources import proposal_sources
from backend.security import store
from backend.security.context import use_context
from tests.r2 import test_build as construction
from tests.security.test_permissions import principal

SOURCE = (
    "第一条 本规定适用于本机构办理的付款业务。\n"
    "第二条 财务人员须对照原始合同核实付款申请。\n"
    "第三条 每月最后一个工作日应汇总付款记录。\n"
    "第四条 经办部门应当保存书面申请及审批意见。\n"
    "第五条 本规定自2026年1月1日起施行。"
)
RECOVER = SOURCE.splitlines()[1]
NO_CANDIDATE = SOURCE.splitlines()[2]
MIXED = SOURCE.splitlines()[3]


def mixed_reply(messages: list[dict], **_options: object) -> dict:
    chunks = json.loads(messages[-1]["content"])["chunks"]
    candidates = []
    for chunk in chunks:
        text = chunk["content"].strip()
        if text == NO_CANDIDATE:
            continue
        candidate = {
            "kind": "rule",
            "title": text,
            "content": text,
            "source_chunk_ids": [chunk["id"]],
            "source_quotes": [{"chunk_id": chunk["id"], "quote": text}],
        }
        if text == MIXED:
            candidates.append(deepcopy(candidate))
        if text in {RECOVER, MIXED}:
            candidate.update(title="不能信任的模型名称", content="不能信任的模型正文")
            candidate["source_quotes"][0]["quote"] = "模型改写了这段引文。"
        candidates.append(candidate)
    return {
        "coverage": [{"chunk_id": chunk["id"], "reason": "模型认为已经整理"} for chunk in chunks],
        "candidates": candidates,
    }


class RetentionBoundary(unittest.TestCase):
    def test_readonly_even_with_build_permission_and_tool_without_human_confirmation_cannot_retain(self) -> None:
        for ctx, code in (
            (CapabilityContext(principal(readonly=True), permissions=frozenset({"build.edit"})), "PERMISSION_DENIED"),
            (
                CapabilityContext(
                    principal(), permissions=frozenset({"build.edit"}), binding="tool", assistant="builder"
                ),
                "CONFIRMATION_REQUIRED",
            ),
        ):
            with (
                self.subTest(expected=code),
                patch("backend.capabilities.registry.store.append_audit"),
                self.assertRaises(ApiError) as error,
            ):
                registry.invoke("changeset.retain_source", ctx, {})
            self.assertEqual(error.exception.code, code)


@unittest.skipUnless(os.getenv("RUN_JOBS_MYSQL_TESTS") == "1", "opt-in MySQL changeset contracts")
class UnformedSources(unittest.TestCase):
    setUpClass = classmethod(construction.BuildMySQL.setUpClass.__func__)
    tearDownClass = classmethod(construction.BuildMySQL.tearDownClass.__func__)
    login = construction.BuildMySQL.login
    invoke = construction.BuildMySQL.invoke

    def proposal(self, builder: TestClient, text: str = SOURCE, reply: Callable = mixed_reply) -> dict:
        uploaded = builder.post("/api/materials/upload", files={"file": ("付款规定.txt", text.encode(), "text/plain")})
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        run = self.invoke(builder, "recipe.start", {"material_id": uploaded.json()["id"]})
        person = store.authenticate_session(builder.cookies.get("ontology_session"), "a2-extraction")[0]
        with (
            use_context(person),
            patch.object(build, "model_available", return_value=True),
            patch("backend.ai.complete", side_effect=reply),
        ):
            asyncio.run(build.run_recipe(run["id"]))
        run = self.invoke(builder, "recipe.status", {"run_id": run["id"]})
        return self.invoke(builder, "changeset.get", {"changeset_id": run["changeset_id"]})

    def test_shared_segment_and_unknown_coverage_retain_only_verbatim_pending_candidates(self) -> None:
        builder = self.login("fixture.builder")
        proposal = self.proposal(builder)
        missing = {r["quote"].strip(): r for r in proposal["unformed_sources"]}
        self.assertEqual(set(missing), {RECOVER, NO_CANDIDATE, MIXED})
        self.assertEqual(proposal["extraction_summary"]["discarded_candidates"], 2)
        self.assertTrue(any(item["after_data"]["content"] == MIXED for item in proposal["items"]))
        self.assertNotIn("不能信任的模型", json.dumps(proposal, ensure_ascii=False))
        for text in (RECOVER, NO_CANDIDATE, MIXED):
            ref = missing[text]
            self.assertEqual(SOURCE[ref["start"] : ref["end"]], ref["quote"])
            args = {
                "changeset_id": proposal["id"],
                "anchor_id": ref["anchor_id"],
                "source_version": ref["source_version"],
            }
            saved = self.invoke(builder, "changeset.retain_source", args)
            self.assertNotIn(ref["anchor_id"], [r["anchor_id"] for r in saved["unformed_sources"]])
            saved_item = saved["items"][-1]
            self.assertEqual(saved_item["decision"], "pending")
            self.assertEqual(saved_item["tier"], "doubt")
            self.assertFalse(saved_item["ready_for_bulk_accept"])
            self.assertFalse(saved_item.get("applied_entity_id"))
            self.assertEqual(saved_item["after_data"]["content"], ref["quote"])
            self.assertEqual(saved_item["evidence"][0], ref)
            multiple = self.invoke(builder, "changeset.retain_source", args)
            self.assertEqual([row["id"] for row in multiple["items"]], [row["id"] for row in saved["items"]])

    def test_the_only_candidate_is_invalid_on_both_attempts_and_can_be_retained_once(self) -> None:
        builder = self.login("fixture.builder")
        proposal = self.proposal(builder, RECOVER)
        self.assertEqual(proposal["extraction_summary"]["retried_batches"], 1)
        self.assertEqual(proposal["extraction_summary"]["discarded_candidates"], 1)
        self.assertEqual(proposal["items"], [])
        self.assertEqual([ref["quote"] for ref in proposal["unformed_sources"]], [RECOVER])
        ref = proposal["unformed_sources"][0]
        saved = self.invoke(
            builder,
            "changeset.retain_source",
            {"changeset_id": proposal["id"], "source_version": ref["source_version"], "anchor_id": ref["anchor_id"]},
        )
        self.assertEqual([(item["tier"], item["decision"]) for item in saved["items"]], [("doubt", "pending")])
        self.assertEqual(saved["items"][0]["evidence"], [ref])

    def test_wrong_anchor_version_tenant_and_read_role_cannot_add_a_row(self) -> None:
        builder = self.login("fixture.builder")
        proposal = self.proposal(builder)
        ref = proposal["unformed_sources"][0]
        body = {"changeset_id": proposal["id"], "anchor_id": ref["anchor_id"], "source_version": ref["source_version"]}
        covered = next(item["evidence"][0] for item in proposal["items"] if "第一条" in item["evidence"][0]["quote"])
        api = "/api/v2/capabilities/changeset.retain_source:invoke"
        other = self.proposal(builder)
        for client, changes, status in [
            (builder, {"source_version": other["unformed_sources"][0]["source_version"]}, 409),
            (builder, {"anchor_id": other["unformed_sources"][0]["anchor_id"]}, 404),
            (builder, {"anchor_id": covered["anchor_id"]}, 404),
            (builder, {"quote": "客户端改写"}, 422),
            (self.login("fixture.reviewer"), {}, 403),
            (self.login("r2.other"), {}, 404),
        ]:
            with self.subTest(changes=changes, status=status):
                rejected = client.post(api, json={**body, **changes})
                self.assertEqual(rejected.status_code, status, rejected.text)
        after = builder.get("/api/v2/changesets/" + proposal["id"]).json()["data"]
        self.assertEqual(len(after["items"]), len(proposal["items"]))
        self.assertEqual(after["unformed_sources"], proposal["unformed_sources"])

    def test_adjacent_batch_context_can_reveal_gaps_on_either_side_of_its_boundary(self) -> None:
        builder = self.login("fixture.builder")
        lines = [f"第{n}条 经办部门应当审核专款的原始凭证并归档。" for n in range(1, 16)]

        def boundary_replies(messages: list[dict], **options: object) -> dict:
            result = mixed_reply(messages, **options)
            group = [row["content"] for row in result["candidates"]]
            for candidate in list(result["candidates"]):
                if candidate["content"] == lines[12] or (candidate["content"] == lines[11] and lines[0] not in group):
                    wrong = deepcopy(candidate)
                    wrong["title"] += "有问题的建议"
                    wrong["source_quotes"][0]["quote"] = "未出现在原文中的内容。"
                    result["candidates"].append(wrong)
            return result

        proposal = self.proposal(builder, "\n".join(lines), boundary_replies)
        self.assertEqual(proposal["extraction_summary"]["discarded_candidates"], 2)
        missing = proposal["unformed_sources"]
        self.assertEqual({ref["quote"].strip() for ref in missing}, set(lines[11:13]))
        material = builder.get("/api/materials/" + proposal["source_material_id"]).json()
        self.assertTrue(all(material["coverage"][ref["chunk_id"]]["status"] == "needs_review" for ref in missing))

    def test_old_proposal_uses_its_original_reading_even_after_the_material_is_resegmented(self) -> None:
        builder = self.login("fixture.builder")
        proposal = self.proposal(builder)
        material_id = proposal["source_material_id"]
        reading_url = "/api/materials/" + material_id + "/reading"
        reading = builder.get(reading_url).json()
        merged = builder.post(
            reading_url + "/operation",
            json={
                "revision": reading["revision"],
                "action": "merge",
                "segment_ids": [part["id"] for part in reading["segments"][:3]],
            },
        )
        self.assertEqual(merged.status_code, 200, merged.text)
        confirmed = builder.post(reading_url + "/confirm", json={"revision": merged.json()["revision"]})
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        ref = next(ref for ref in proposal["unformed_sources"] if ref["quote"].strip() == RECOVER)
        # A.1 stored no rejected source IDs. Its immutable reading proves only the
        # wholly uncovered paragraphs, including this one. A shared, covered
        # paragraph cannot retrospectively be identified as a discarded source.
        person = store.authenticate_session(builder.cookies.get("ontology_session"), "a2-extraction")[0]
        with use_context(person), closing(db.connect()) as conn:
            source = provenance.get_source_version(conn, material_id, ref["source_version"])
            legacy = {**proposal, "stats": {"extraction_summary": proposal["extraction_summary"]}}
            historical = proposal_sources(conn, legacy, source, proposal["items"])
        self.assertEqual({entry["quote"].strip() for entry in historical}, {RECOVER, NO_CANDIDATE})
        endpoint = "/api/v2/changesets/" + proposal["id"] + "/sources:retain"
        args = {"changeset_id": proposal["id"], "anchor_id": ref["anchor_id"], "source_version": ref["source_version"]}
        self.assertEqual(builder.post(endpoint, json={**args, "changeset_id": "different"}).status_code, 422)
        csrf = builder.headers.pop("X-CSRF-Token")
        self.assertEqual(builder.post(endpoint, json=args).status_code, 403)
        builder.headers["X-CSRF-Token"] = csrf
        saved = builder.post(endpoint, json=args)
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["data"]["items"][-1]["evidence"], [ref])
        self.assertNotEqual(confirmed.json()["segments"][0]["anchor_id"], ref["anchor_id"])

    def test_concurrent_retention_and_replay_after_adoption_create_one_candidate_and_draft(self) -> None:
        builder = self.login("fixture.builder")
        proposal = self.proposal(builder)
        ref = next(r for r in proposal["unformed_sources"] if r["quote"].strip() == RECOVER)
        args = {"changeset_id": proposal["id"], "anchor_id": ref["anchor_id"], "source_version": ref["source_version"]}
        with ThreadPoolExecutor(max_workers=2) as pool:
            clients = [self.login("fixture.builder") for _ in range(2)]
            results = list(pool.map(lambda client: self.invoke(client, "changeset.retain_source", args), clients))
        ids = []
        for saved in results:
            self.assertEqual(len(saved["items"]), len(proposal["items"]) + 1)
            ids.append(saved["items"][-1]["id"])
        self.assertEqual(ids[0], ids[1])
        self.invoke(
            builder,
            "changeset.decide_items",
            {"changeset_id": proposal["id"], "item_ids": ids[:1], "decision": "accepted"},
        )
        applied = self.invoke(builder, "changeset.apply", {"changeset_id": proposal["id"]})
        self.assertEqual(len(applied["entity_ids"]), 1)
        after = self.invoke(builder, "changeset.retain_source", args)
        self.assertEqual(len(after["items"]), len(proposal["items"]) + 1)
        self.assertEqual(after["items"][-1]["applied_entity_id"], applied["entity_ids"][0])
        detail = builder.get("/api/materials/" + proposal["source_material_id"]).json()
        retained = [row for row in detail["candidates"] if row["id"] == after["items"][-1]["after_data"]["id"]]
        self.assertEqual(len(retained), 1)
        self.assertEqual(retained[0]["entity_id"], applied["entity_ids"][0])
