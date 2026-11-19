"""The derived index follows committed business writes and current confirmed readings."""

import asyncio
import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import db, jobs, provenance
from backend.api.v2.orgs import OrgManageInput
from backend.core.errors import ApiError
from backend.infra import search_job
from backend.infra.search_index import EVIDENCE_SEPARATOR, SearchChanges, chunk_doc_id, public_card_text, rebuild_index
from backend.kernel import elements
from backend.security import directory, store
from backend.security.context import Principal, use_context
from tests.r2 import test_build as construction


class SearchWriteSet(unittest.TestCase):
    def test_canonical_documents_and_parameterized_legacy_writes(self) -> None:
        changes = SearchChanges()
        changes.observe("INSERT INTO entities VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", ("A", *range(11)))
        changes.observe("UPDATE entities SET version=version+1,updated_at=? WHERE id=?", ("now", "B"))
        changes.observe(
            "UPDATE entity_meta SET module_id=?,updated_at=? WHERE module_id=? AND entity_id IN (?,?)",
            ("part", "date", "old", "C", "D"),
        )
        changes.observe("INSERT INTO reading_versions VALUES(?,?,?,?,?,?,?)", ("read", "M", "v", 1, "[]", "why", "now"))
        changes.observe("UPDATE jobs SET progress=? WHERE id=?", (100, "job"))
        self.assertEqual(changes.entities, {"A", "B", "C", "D", "M"})
        self.assertFalse(changes.full)
        changes.observe("DELETE FROM entities WHERE source_kind=?", ("demo",))
        self.assertTrue(changes.full)
        self.assertEqual(chunk_doc_id("piece"), "chunk:piece")
        self.assertLessEqual(len(chunk_doc_id("a" * 255)), 200)
        self.assertNotEqual(chunk_doc_id("a" * 254 + "b"), chunk_doc_id("a" * 255))

    def test_own_business_card_does_not_copy_private_reference_labels(self) -> None:
        card = public_card_text(
            {
                "id": "R",
                "kind": "rule",
                "title": "付款规则",
                "summary": "付款前核对原件。",
                "status": "published",
                "version": 2,
                "payload": {"source_refs": [{"kind": "statement", "quote": "私有草稿文字"}]},
                "relations": {
                    "outgoing": [
                        {
                            "status": "confirmed",
                            "source_title": "付款规则",
                            "target_title": "隐秘草稿名称",
                            "type": "references",
                        }
                    ]
                },
            }
        )
        self.assertIn("付款前核对原件", card)
        self.assertNotIn("私有草稿文字", card)
        self.assertNotIn("隐秘草稿名称", card)

    def test_nested_source_labels_in_business_steps_never_enter_the_shared_card(self) -> None:
        private = "审批人员上传的个人报告"
        private_id = "ART-64553fa877bc823b"
        card = public_card_text(
            {
                "id": "review-process",
                "kind": "knowledge",
                "title": "交接要点",
                "payload": {
                    "steps": [
                        {"title": "先检查双方签名"},
                        {"artifact_id": private_id, "title": private},
                        {"target_entity_id": "draft-receipt", "title": "草稿材料缓存的名称"},
                        {"kind": "report", "name": private},
                        private_id,
                    ]
                },
            }
        )
        self.assertIn("先检查双方签名", card)
        for hidden in [private, private_id, "草稿材料缓存的名称", "draft-receipt"]:
            self.assertNotIn(hidden, card)


@unittest.skipUnless(os.getenv("RUN_JOBS_MYSQL_TESTS") == "1", "opt-in MySQL search projection tests")
class SearchIndexMySQL(unittest.TestCase):
    setUpClass = classmethod(construction.BuildMySQL.setUpClass.__func__)
    tearDownClass = classmethod(construction.BuildMySQL.tearDownClass.__func__)
    login = construction.BuildMySQL.login
    invoke = construction.BuildMySQL.invoke

    def person(self, client: TestClient) -> Principal:
        return store.authenticate_session(client.cookies.get(store.COOKIE_NAME), "index-actions")[0]

    def docs(self, client: TestClient, ident: str | None = None) -> dict[str, dict]:
        with use_context(self.person(client)), closing(db.connect()) as conn:
            return {
                r["doc_id"]: dict(r)
                for r in conn.execute(
                    "SELECT * FROM search_index" + (" WHERE entity_id=?" if ident else "") + " ORDER BY doc_id",
                    (ident,) if ident else (),
                )
            }

    def create(self, client: TestClient, title: str, **changes: object) -> dict:
        return self.invoke(client, "element.create_draft", {"kind": "knowledge", "title": title, **changes})["entity"]

    def material(self, client: TestClient, body: str) -> str:
        response = client.post("/api/materials/upload", files={"file": ("交接原件.txt", body.encode(), "text/plain")})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["id"]

    def confirm(self, client: TestClient, material: str) -> dict:
        path = "/api/materials/" + material
        preview = client.post(path + "/segment-preview", json={"mode": "default"})
        self.assertEqual(preview.status_code, 200, preview.text)
        accepted = client.post(path + "/segment-confirm", json={"preview_id": preview.json()["id"]})
        self.assertEqual(accepted.status_code, 200, accepted.text)
        chunk = client.get(path + "/chunks").json()["items"][0]
        return client.get(path + "/source", params={"chunk_id": chunk["id"]}).json()["focus"]

    def test_save_status_index_failure_rolls_back_business_write_and_reuses_connection(self) -> None:
        admin = self.login("fixture.admin")
        asset = self.create(admin, "签收簿核对", summary="两名接收人分别核对签收。")
        key = "el:" + asset["id"]
        first = self.docs(admin, asset["id"])[key]
        self.assertIn("两名接收人", first["body"])
        self.assertEqual((first["status"], first["module_id"]), ("draft", "MOD-DEFAULT"))
        with use_context(self.person(admin)), closing(db.connect()) as conn:
            with (
                self.assertRaisesRegex(RuntimeError, "injected projection failure"),
                patch(
                    "backend.infra.search_index._store_rows", side_effect=RuntimeError("injected projection failure")
                ),
                conn,
            ):
                old = db.entity_json(
                    conn.execute("SELECT * FROM entities WHERE id=? FOR UPDATE", (asset["id"],)).fetchone()
                )
                elements.update_draft(conn, old, {"title": "未落库的名称"})
            self.assertEqual(
                conn.execute("SELECT title FROM entities WHERE id=?", (asset["id"],)).fetchone()[0], asset["title"]
            )
            self.assertEqual(dict(conn.execute("SELECT * FROM search_index WHERE doc_id=?", (key,)).fetchone()), first)
            with self.assertRaisesRegex(RuntimeError, "cancel creation"), conn:
                aborted = elements.create_draft(conn, {"kind": "knowledge", "title": "尚未提交的签收单"})
                raise RuntimeError("cancel creation")
            self.assertIsNone(conn.execute("SELECT id FROM entities WHERE id=?", (aborted["id"],)).fetchone())
            self.assertIsNone(
                conn.execute("SELECT doc_id FROM search_index WHERE entity_id=?", (aborted["id"],)).fetchone()
            )
        edited = self.invoke(
            admin,
            "element.update_draft",
            {
                "entity_id": asset["id"],
                "version": asset["version"],
                "title": "回执核验",
                "summary": "依据盖章回执交接文件。",
            },
        )["entity"]
        after = self.docs(admin, asset["id"])[key]
        self.assertEqual(after["title"], edited["title"])
        self.assertIn("盖章回执", after["body"])
        self.assertNotIn("两名接收人", after["body"])
        self.assertNotIn(first["title"], after["body"])

    def test_confirmed_reading_replaces_old_chunks_and_superseded_citations(self) -> None:
        admin = self.login("fixture.admin")
        text = "档案移交时登记签收单份数，交接双方核对接收日期。"
        material = self.material(admin, text)
        self.assertEqual(set(self.docs(admin, material)), {"el:" + material})
        focus = self.confirm(admin, material)
        first = chunk_doc_id(focus["chunk_id"])
        self.assertEqual(self.docs(admin, material)[first]["body"], text)
        asset = self.create(
            admin,
            "档案交接检查",
            kind="data",
            summary="检查经办人登记情况。",
            payload={"definition": "经办人有签名的单据份数", "source_refs": [focus]},
        )
        key = "el:" + asset["id"]
        public, tail = self.docs(admin, asset["id"])[key]["body"].split(EVIDENCE_SEPARATOR)
        self.assertNotIn(text, public)
        self.assertEqual(tail, text)
        second = chunk_doc_id(self.confirm(admin, material)["chunk_id"])
        self.assertNotEqual(first, second)
        docs = self.docs(admin, material)
        self.assertEqual(set(docs), {"el:" + material, second})
        self.assertEqual(docs[second]["body"], text)
        # The old original anchor remains valid across a new reading of the same original.
        self.assertIn(text, self.docs(admin, asset["id"])[key]["body"])
        with use_context(self.person(admin)), closing(db.connect()) as conn, conn:
            self.assertIsNotNone(conn.execute("SELECT id FROM chunks WHERE id=?", (focus["chunk_id"],)).fetchone())
            provenance.ensure_source_version(conn, material, "档案移交以两份电子回执分别签收。")
        self.assertEqual(set(self.docs(admin, material)), {"el:" + material})
        self.assertNotIn(text, self.docs(admin, asset["id"])[key]["body"])
        self.assertNotIn(EVIDENCE_SEPARATOR, self.docs(admin, asset["id"])[key]["body"])
        with use_context(self.person(admin)), closing(db.connect()) as conn:
            self.assertIsNotNone(
                conn.execute("SELECT id FROM source_anchors WHERE id=?", (focus["anchor_id"],)).fetchone()
            )

    def test_authorized_lease_rebuild_reproduces_every_field_and_cannot_cross_workspace(self) -> None:
        admin, builder, other = (self.login(name) for name in ("fixture.admin", "fixture.builder", "r2.other"))
        asset = self.create(
            admin,
            "调拨单收讫登记",
            kind="term",
            payload={"schema": "term/1", "definition": "调拨单已签收的登记", "aliases": ["调拨回单", "收讫凭据"]},
        )
        self.assertEqual(self.docs(admin, asset["id"])["el:" + asset["id"]]["aliases"], "调拨回单\n收讫凭据")
        expected = self.docs(admin)
        other_expected = self.docs(other)
        path = "/api/v2/system/search-index:rebuild"
        self.assertEqual(builder.post(path, json={}).status_code, 403)
        self.assertEqual(admin.post(path, json={"tenant_id": self.person(other).tenant_id}).status_code, 422)
        with use_context(self.person(admin)), closing(db.connect()) as conn, conn:
            conn.execute("DELETE FROM search_index")
        self.assertEqual(self.docs(admin), {})
        response = admin.post(path, json={})
        self.assertEqual(response.status_code, 200, response.text)
        accepted = response.json()
        self.assertEqual(accepted["status"], "queued")
        self.assertEqual(admin.post(path).json()["job_id"], accepted["job_id"])
        with use_context(self.person(admin)), self.assertRaises(ApiError):
            search_job.run_rebuild(accepted["job_id"], self.person(admin).tenant_id, [])
        self.assertEqual(other.get("/api/jobs/" + accepted["job_id"]).status_code, 404)
        self.assertTrue(asyncio.run(jobs.run_one()))
        job = admin.get("/api/jobs/" + accepted["job_id"])
        self.assertEqual(job.status_code, 200, job.text)
        self.assertEqual(job.json()["status"], "completed", job.text)
        counts = job.json()["result"]
        self.assertEqual(counts["element_count"], sum(x["doc_type"] == "element" for x in expected.values()))
        self.assertEqual(counts["chunk_count"], sum(x["doc_type"] == "chunk" for x in expected.values()))
        self.assertEqual(counts["requested_by"], self.person(admin).user_id)
        self.assertEqual(self.docs(admin), expected)
        self.assertEqual(self.docs(other), other_expected)

    def test_rebuild_waits_for_inflight_writer_and_subsequent_writer_uses_current_state(self) -> None:
        admin = self.login("fixture.admin")
        person = self.person(admin)
        asset = self.create(admin, "转运回执")
        key = "el:" + asset["id"]
        waiting, acquired = threading.Event(), threading.Event()

        def rebuild() -> dict[str, int]:
            with use_context(person), closing(db.connect()) as conn, conn:
                conn.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
                waiting.set()
                conn.lock_search_rebuild()
                acquired.set()
                return rebuild_index(conn)

        with ThreadPoolExecutor(max_workers=1) as worker, use_context(person), closing(db.connect()) as writer:
            with writer:
                old = db.entity_json(
                    writer.execute("SELECT * FROM entities WHERE id=? FOR UPDATE", (asset["id"],)).fetchone()
                )
                elements.update_draft(writer, old, {"title": "已复核的转运回执"})
                result = worker.submit(rebuild)
                self.assertTrue(waiting.wait(5))
                self.assertFalse(acquired.wait(0.1))
            self.assertGreater(result.result(timeout=15)["element_count"], 0)
        self.assertEqual(self.docs(admin, asset["id"])[key]["title"], "已复核的转运回执")
        # Establish a prior REPEATABLE READ snapshot, then update metadata after a
        # second writer commits. The projection must include the current business title.
        with use_context(person), closing(db.connect()) as delayed, delayed:
            delayed.execute("SELECT * FROM entities WHERE id=?", (asset["id"],)).fetchone()
            with closing(db.connect()) as fresh, fresh:
                old = db.entity_json(
                    fresh.execute("SELECT * FROM entities WHERE id=? FOR UPDATE", (asset["id"],)).fetchone()
                )
                elements.update_draft(fresh, old, {"title": "终审核验后的回执"})
            delayed.execute("UPDATE entity_meta SET valid_from=? WHERE entity_id=?", ("2026-10-01", asset["id"]))
        self.assertEqual(self.docs(admin, asset["id"])[key]["title"], "终审核验后的回执")

    def test_directory_rename_uses_target_workspace_and_rolls_back_together(self) -> None:
        hq, dept = self.login("fixture.superadmin"), self.login("fixture.admin")
        hq_before = self.docs(hq)
        id = self.person(dept).tenant_id
        with use_context(self.person(hq)):
            directory.manage(self.person(hq), OrgManageInput(action="update", org_id=id, name="凭证审计处"))
            with (
                self.assertRaisesRegex(RuntimeError, "directory fault"),
                patch("backend.infra.search_index._store_rows", side_effect=RuntimeError("directory fault")),
            ):
                directory.manage(self.person(hq), OrgManageInput(action="update", org_id=id, name="回滚处"))
        self.assertEqual(store.get_tenant(id)["name"], "凭证审计处")
        self.assertEqual(self.docs(dept, id)["el:" + id]["title"], "凭证审计处")
        self.assertEqual(self.docs(hq), hq_before)

    def test_parallel_writers_can_commit_independent_new_documents(self) -> None:
        person = self.person(self.login("fixture.admin"))
        barrier = threading.Barrier(3)

        def create(number: int) -> str:
            with use_context(person), closing(db.connect()) as conn, conn:
                asset = elements.create_draft(
                    conn,
                    {
                        "kind": "knowledge",
                        "title": "并行签发的回执" + str(number),
                        "id": "B2-PARALLEL-DOC-" + str(number),
                    },
                )
                barrier.wait(timeout=8)
                return asset["id"]

        with ThreadPoolExecutor(max_workers=3) as workers:
            futures = [workers.submit(create, n) for n in range(3)]
            ids = [future.result(timeout=15) for future in futures]
        with use_context(person), closing(db.connect()) as conn:
            for ident in ids:
                self.assertIsNotNone(
                    conn.execute("SELECT doc_id FROM search_index WHERE doc_id=?", ("el:" + ident,)).fetchone()
                )

    def test_concurrent_original_revisions_keep_independent_materials_writable(self) -> None:
        admin = self.login("fixture.admin")
        person = self.person(admin)
        ids = [self.material(admin, f"清点第{n}批交付的凭证。") for n in range(2)]
        barrier = threading.Barrier(2)

        def revise(material: str) -> None:
            with use_context(person), closing(db.connect()) as conn, conn:
                conn.execute("SELECT id FROM entities WHERE id=? FOR UPDATE", (material,)).fetchone()
                provenance.ensure_source_version(conn, material, "重新按原件核对凭证。")
                barrier.wait(timeout=5)

        with ThreadPoolExecutor(max_workers=2) as workers:
            for result in [workers.submit(revise, material) for material in ids]:
                result.result(timeout=15)
        for material in ids:
            self.assertEqual(set(self.docs(admin, material)), {"el:" + material})
