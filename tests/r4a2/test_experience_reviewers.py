"""Authenticated human contributors follow an experience through legacy adoption, splitting and merging."""

import asyncio
import json
import os
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime
from threading import Event
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from httpx import Response

from backend import db, experience, jobs
from backend.security import store
from backend.security.context import require_context, use_context
from tests.r2 import test_build as construction


@unittest.skipUnless(os.getenv("RUN_JOBS_MYSQL_TESTS") == "1", "opt-in MySQL experience review flows")
class ExperienceReviewers(unittest.TestCase):
    login = construction.BuildMySQL.login
    invoke = construction.BuildMySQL.invoke

    @classmethod
    def setUpClass(cls) -> None:
        construction.BuildMySQL.setUpClass.__func__(cls)
        cls.addClassCleanup(construction.BuildMySQL.tearDownClass.__func__, cls)
        token = store.login("fixture.superadmin", "SyntheticFixture9!", "127.0.0.1", "experience-test")
        person = store.authenticate_session(token, "experience-test")[0]
        for name in ("x.editor", "x.applier", "x.secondary", "x.observer"):
            store.create_user(
                person,
                username=name,
                display_name=name,
                tenant_id="dept-01",
                role="admin",
                password="SyntheticFixture9!",
                must_change_password=False,
            )

    def setUp(self) -> None:
        self.builder = self.login("fixture.builder")
        self.author, self.applier, self.secondary, self.observer = (
            self.login("x." + n) for n in ("editor", "applier", "secondary", "observer")
        )
        self.independent = self.login("fixture.reviewer")
        self.narrative = "交接纸质档案时，双方逐份核对交接清册，分别签收并注明日期。"
        self.base = {
            "title": "档案移交登记",
            "summary": "双人登记交接清册。",
            "content": self.narrative,
            "boundary": "仅适用于纸质卷宗。",
            "questions": "",
        }
        created = self.ok(
            self.builder.post(
                "/api/experience/sessions",
                json={"title": "核对档案的经验", "context": self.narrative, "mode": "structured"},
            )
        )
        self.sid = created["entity"]["id"]
        self.path = "/api/experience/" + self.sid

    def ok(self, response: Response) -> dict:
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def person(self, client: TestClient) -> str:
        return store.authenticate_session(client.cookies.get(store.COOKIE_NAME), "experience-test")[0].user_id

    def record(self) -> dict:
        return self.ok(self.builder.get(self.path))["entity"]

    def candidates(self) -> dict:
        return {item["id"]: item for item in self.record()["payload"]["knowledge_candidates"]}

    def adopt_legacy(self) -> dict:
        self.ok(self.author.patch(self.path + "/draft", json=self.base))
        return self.ok(self.builder.post(self.path + "/adopt", json=self.base))["entity"]

    def apply(self, operation: str, candidates: list[str], actor: TestClient) -> list[str]:
        proposal = self.propose(operation, candidates)
        return self.ok(actor.post(self.path + "/proposals/" + proposal["id"] + "/decision", json={"action": "apply"}))[
            "candidate_ids"
        ]

    def propose(self, operation: str, candidates: list[str], author: TestClient | None = None) -> dict:
        items = self.candidates()
        refs = items[candidates[0]]["source_refs"]
        outputs = [
            {
                "title": getattr(self, "proposal_prefix", "") + operation + "逐项核对",
                "summary": "按交接清册逐笔登记",
                "content": "核验交接清册的日期和两方签字。",
                "source_refs": refs,
                "boundary": "纸质凭单。",
            }
        ]
        if operation == "split":
            outputs.append({**outputs[0], "title": "交接日期复核", "content": "双方分别签字注明日期。"})
        fake = AsyncMock(return_value=json.dumps({"candidates": outputs}, ensure_ascii=False))
        with (
            patch("backend.experience.config", return_value={"api_key": "local-fixture"}),
            patch("backend.experience.complete", fake),
        ):
            proposal = self.ok(
                (author or self.builder).post(
                    self.path + "/proposals",
                    json={
                        "operation": operation,
                        "candidate_ids": candidates,
                        "instruction": "逐项核对原文再调整",
                        "expected_revisions": {i: items[i]["revision"] for i in candidates},
                    },
                )
            )["proposal"]
        self.assertEqual(fake.await_count, 1)
        return proposal

    def old_pending(self, ident: str) -> dict:
        """Keep all actual receipts and history while reproducing the pre-upgrade knowledge identity fields."""
        pending = self.submit(ident)
        principal = store.authenticate_session(self.builder.cookies.get(store.COOKIE_NAME), "legacy-pending")[0]
        with use_context(principal), closing(db.connect()) as conn, conn:
            payload = pending["payload"]
            payload["r2_review"].pop("experience_authorship_tracked", None)
            payload["r2_review"].pop("experience_draft_tracked", None)
            payload["r2_review"]["editor_ids"] = [self.person(self.builder)]
            conn.execute("UPDATE entities SET payload=? WHERE id=?", (json.dumps(payload), ident))
        return pending

    def review(self, candidate: str, client: TestClient, action: str, **changes: object) -> dict:
        item = self.candidates()[candidate]
        body = {key: item.get(key, "") for key in ("title", "summary", "boundary", "questions", "article_sections")}
        return self.ok(
            client.post(
                self.path + "/candidates/" + candidate + "/review",
                json={
                    **body,
                    "action": action,
                    "expected_revision": item["revision"],
                    "expected_asset_version": item.get("asset_version"),
                    **changes,
                },
            )
        )

    def asset(self, ident: str) -> dict:
        return self.ok(self.builder.get("/api/entities/" + ident))

    def edit_knowledge(self, client: TestClient, ident: str, definition: str) -> None:
        current = self.asset(ident)
        self.invoke(
            client,
            "element.update_draft",
            {
                "entity_id": ident,
                "version": current["version"],
                "payload": {**current["payload"], "definition": definition},
            },
        )

    def publish(self, ident: str, blocked: list[TestClient], independent: TestClient) -> None:
        self.decide(self.submit(ident), blocked, independent)

    def submit(self, ident: str) -> dict:
        draft = self.asset(ident)
        repaired = self.invoke(
            self.builder,
            "element.update_draft",
            {
                "entity_id": ident,
                "version": draft["version"],
                "payload": {
                    **draft["payload"],
                    "definition": "按原始签收单逐份登记的办理方法。",
                    "conditions": "纸质卷宗移交时使用。",
                    "boundary": "纸质卷宗。",
                },
            },
        )["entity"]
        pending = self.invoke(
            self.builder,
            "element.submit_draft",
            {"entity_id": ident, "version": repaired["version"], "reason": "送审核对"},
        )["entity"]
        self.assertEqual(pending["status"], "review")
        return pending

    def decide(self, pending: dict, blocked: list[TestClient], independent: TestClient) -> None:
        ident = pending["id"]
        for actor in blocked:
            self.assertEqual(
                actor.post("/api/entities/" + ident + "/action", json={"action": "approve"}).status_code, 403
            )
            denied = actor.post(
                "/api/v2/reviews/" + ident + ":decide",
                json={"decision": "approve_publish", "version": pending["version"]},
            )
            self.assertEqual(denied.status_code, 403, denied.text)
            self.assertEqual(denied.json()["error"]["code"], "SEPARATION_OF_DUTIES")
            via_capability = actor.post(
                "/api/v2/capabilities/review.decide:invoke",
                json={"entity_id": ident, "decision": "approve_publish", "version": pending["version"]},
            )
            self.assertEqual(via_capability.status_code, 403, via_capability.text)
            self.assertEqual(via_capability.json()["error"]["code"], "SEPARATION_OF_DUTIES")
        result = self.ok(
            independent.post(
                "/api/v2/reviews/" + ident + ":decide",
                json={"decision": "approve_publish", "version": pending["version"]},
            )
        )
        self.assertEqual(result["data"]["entity"]["status"], "published")

    def test_legacy_edits_no_op_and_reviewers_reset_after_publication(self) -> None:
        self.ok(self.author.patch(self.path + "/draft", json={**self.base, "summary": "按登记表核对交接人。"}))
        self.ok(self.secondary.patch(self.path + "/draft", json=self.base))
        self.ok(
            self.observer.patch(
                self.path + "/draft", json={**self.base, "draft_editor_ids": [self.person(self.observer)]}
            )
        )
        draft = self.ok(self.builder.post(self.path + "/adopt", json=self.base))["entity"]
        ident = draft["id"]
        self.assertEqual(
            set(draft["payload"]["r2_review"]["editor_ids"]),
            {self.person(x) for x in [self.author, self.secondary, self.builder]},
        )
        self.assertTrue(self.ok(self.observer.post(self.path + "/adopt", json=self.base))["already_adopted"])
        self.publish(ident, [self.author, self.secondary], self.observer)
        (cid,) = self.candidates()
        self.apply("revise", [cid], self.applier)
        self.review(cid, self.builder, "adopt")
        current = self.asset(ident)["payload"]["r2_review"]
        self.assertEqual(set(current["editor_ids"]), {self.person(self.applier), self.person(self.builder)})
        self.publish(ident, [self.applier], self.author)

    def test_pre_tracking_patch_uses_real_receipts_and_version_changes_to_recover_only_editors(self) -> None:
        self.ok(self.author.patch(self.path + "/draft", json=self.base))
        self.ok(self.observer.patch(self.path + "/draft", json=self.base))
        # Represent a previously saved draft: the original content, versions, authenticated
        # request starts/successes and actors are real. Only the newly introduced cache was absent.
        principal = store.authenticate_session(self.builder.cookies.get(store.COOKIE_NAME), "legacy-experience")[0]
        with use_context(principal), closing(db.connect()) as conn, conn:
            conn.execute(
                "UPDATE entities SET payload=JSON_REMOVE(payload,'$.draft.draft_editor_ids') WHERE id=?", (self.sid,)
            )
        draft = self.ok(self.builder.post(self.path + "/adopt", json=self.base))["entity"]
        self.assertEqual(
            set(draft["payload"]["r2_review"]["editor_ids"]), {self.person(self.author), self.person(self.builder)}
        )
        self.publish(draft["id"], [self.author], self.observer)

    def test_split_inherits_the_open_adopted_parent_and_manual_applier(self) -> None:
        self.adopt_legacy()
        (cid,) = self.candidates()
        note = "已核对签收人的两份签字。"
        self.review(cid, self.secondary, "save", reason=note)
        self.assertEqual(self.candidates()[cid]["review_note"], note)
        self.review(cid, self.observer, "save", reason=note)
        primary, child = self.apply("split", [cid], self.applier)
        self.assertEqual(primary, cid)
        result = self.review(child, self.builder, "adopt")
        editors = set(self.asset(result["entity_id"])["payload"]["r2_review"]["editor_ids"])
        self.assertEqual(editors, {self.person(x) for x in [self.author, self.builder, self.applier, self.secondary]})
        self.publish(result["entity_id"], [self.author, self.applier, self.secondary], self.observer)

    def test_upgrade_already_pending_knowledge_recovers_only_draft_editors_before_adoption(self) -> None:
        self.base["title"] = "存量交接单领用核验"
        draft = self.adopt_legacy()
        pending = self.submit(draft["id"])
        self.ok(self.secondary.patch(self.path + "/draft", json={**self.base, "summary": "下一轮再核对领用日期。"}))
        # Recreate the storage fields of an already-submitted pre-upgrade knowledge asset.
        principal = store.authenticate_session(self.builder.cookies.get(store.COOKIE_NAME), "legacy-pending")[0]
        with use_context(principal), closing(db.connect()) as conn, conn:
            conn.execute(
                "UPDATE entities SET payload=JSON_REMOVE(payload,'$.draft.draft_editor_ids') WHERE id=?", (self.sid,)
            )
            payload = pending["payload"]
            payload["r2_review"].pop("experience_authorship_tracked", None)
            payload["r2_review"]["editor_ids"] = [self.person(self.builder)]
            conn.execute("UPDATE entities SET payload=? WHERE id=?", (json.dumps(payload), pending["id"]))
        self.decide(pending, [self.author], self.secondary)

    def test_merge_carries_secondary_writers_and_generic_restore_cannot_rewrite_them(self) -> None:
        initial = self.adopt_legacy()
        (cid,) = self.candidates()
        primary, secondary = self.apply("split", [cid], self.applier)
        self.review(secondary, self.secondary, "save", summary="按日期分别核验各自的签收凭据。")
        current = self.record()
        payload = {**current["payload"], "knowledge_candidates": []}
        self.assertEqual(self.builder.patch("/api/entities/" + self.sid, json={"payload": payload}).status_code, 400)
        denied = self.builder.post(
            "/api/v2/capabilities/element.update_draft:invoke",
            json={
                "entity_id": self.sid,
                "version": current["version"],
                "payload": {**current["payload"], "record_type": "material"},
            },
        )
        self.assertEqual(denied.status_code, 422, denied.text)
        self.assertEqual(denied.json()["error"]["code"], "VALIDATION_FAILED")
        forged = self.builder.post("/api/entities", json={"kind": "material", "title": "伪造经验", "payload": payload})
        self.assertEqual(forged.status_code, 400, forged.text)
        v2_forged = self.builder.post(
            "/api/v2/capabilities/element.create_draft:invoke",
            json={"kind": "material", "title": "伪造记录", "payload": payload},
        )
        self.assertEqual(v2_forged.status_code, 422, v2_forged.text)
        normal = self.ok(
            self.builder.post(
                "/api/materials/upload", files={"file": ("独立原件.txt", "逐份签收纸质凭据。".encode(), "text/plain")}
            )
        )
        edited = self.ok(self.builder.patch("/api/entities/" + normal["id"], json={"title": "已查验的普通资料"}))
        self.assertEqual(edited["title"], "已查验的普通资料")
        restored = self.author.post(
            "/api/entities/" + self.sid + "/restore", json={"version": current["version"] - 1, "reason": "恢复旧稿"}
        )
        self.assertEqual(restored.status_code, 400, restored.text)
        v2_restore = self.author.post(
            "/api/v2/capabilities/element.restore_version:invoke",
            json={
                "entity_id": self.sid,
                "version": current["version"],
                "restore_version": current["version"] - 1,
                "reason": "恢复早期经验记录",
            },
        )
        self.assertEqual(v2_restore.status_code, 422, v2_restore.text)
        self.assertEqual(v2_restore.json()["error"]["code"], "VALIDATION_FAILED")
        fake_binding = {**initial["payload"], "experience_candidate_id": "another-candidate"}
        refused = self.author.patch("/api/entities/" + initial["id"], json={"payload": fake_binding})
        self.assertEqual(refused.status_code, 400, refused.text)
        without_refs = self.author.patch(
            "/api/entities/" + initial["id"], json={"payload": {**initial["payload"], "source_refs": []}}
        )
        self.assertEqual(without_refs.status_code, 400, without_refs.text)
        self.assertEqual(self.asset(initial["id"])["payload"]["experience_candidate_id"], cid)
        self.assertEqual(self.record()["version"], current["version"])
        self.assertEqual(self.apply("merge", [secondary, primary], self.observer), [primary])
        merged = self.review(primary, self.builder, "adopt")
        self.assertEqual(merged["entity_id"], initial["id"])
        self.publish(initial["id"], [self.author, self.applier, self.secondary, self.observer], self.independent)

    def test_pending_preupgrade_proposals_follow_split_revise_and_release_boundaries(self) -> None:
        self.base["title"] = "旧稿领单与盖章核验"
        self.proposal_prefix = "领单"
        source = self.adopt_legacy()
        self.edit_knowledge(self.observer, source["id"], "按批核对签收联和归档联。")
        self.publish(source["id"], [self.author, self.observer], self.independent)
        (parent,) = self.candidates()
        _, child = self.apply("split", [parent], self.applier)
        self.assertFalse(self.candidates()[child].get("legacy_draft"))
        self.apply("revise", [child], self.secondary)
        preview = self.propose("revise", [child], self.author)
        self.ok(self.observer.post(self.path + "/proposals/" + preview["id"] + "/decision", json={"action": "dismiss"}))
        adopted = self.review(child, self.builder, "adopt")
        pending = self.old_pending(adopted["entity_id"])
        self.apply("revise", [child], self.observer)
        prior_author = self.ok(self.author.get("/api/v2/reviews/" + pending["id"]))["data"]
        self.assertTrue(prior_author["can_decide"], prior_author)
        self.decide(pending, [self.applier, self.secondary], self.observer)

    def test_old_split_inherits_only_the_open_parent_knowledge_editors_at_the_split(self) -> None:
        self.base["title"] = "父知识通用修订并分册核对"
        self.proposal_prefix = "分册"
        parent = self.adopt_legacy()["id"]
        self.edit_knowledge(self.secondary, parent, "双人核对档案交接的签收原件。")
        (candidate,) = self.candidates()
        _, child = self.apply("split", [candidate], self.applier)
        self.edit_knowledge(self.observer, parent, "另行核对拆分后的目录复印件。")
        self.publish(parent, [self.author, self.secondary, self.observer], self.independent)
        adopted = self.review(child, self.builder, "adopt", title="分册签收凭条归位")
        pending = self.old_pending(adopted["entity_id"])
        self.decide(pending, [self.secondary, self.author, self.applier], self.observer)

    def test_overlapping_successful_identical_patches_use_business_actor_or_report_uncertainty(self) -> None:
        self.base["title"] = "并发原样签收登记"
        author = self.person(self.author)
        second = self.person(self.observer)
        acquired, waiting, second_committed = Event(), Event(), Event()
        original_lock, original_get = experience.session, experience.get_session

        def lock(conn: db.Connection, sid: str, *, lock: bool = False) -> dict:
            actor = require_context().user_id
            if lock and actor == second:
                waiting.set()
            record = original_lock(conn, sid, lock=lock)
            if lock and actor == author:
                acquired.set()
                self.assertTrue(waiting.wait(12))
            return record

        def after_commit(sid: str) -> dict:
            if require_context().user_id == author:
                self.assertTrue(second_committed.wait(12))
            else:
                second_committed.set()
            return original_get(sid)

        with (
            patch("backend.experience.session", lock),
            patch("backend.experience.get_session", after_commit),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            first_run = pool.submit(self.author.patch, self.path + "/draft", json=self.base)
            self.assertTrue(acquired.wait(12))
            second_run = pool.submit(self.observer.patch, self.path + "/draft", json=self.base)
            self.ok(first_run.result(20))
            self.ok(second_run.result(20))
        principal = store.authenticate_session(self.builder.cookies.get(store.COOKIE_NAME), "old-overlap")[0]
        with use_context(principal), closing(db.connect()) as conn, conn:
            versions = list(
                conn.execute(
                    "SELECT created_at,json_extract(snapshot,'$.payload.draft') draft FROM versions "
                    "WHERE entity_id=? AND reason='人工编辑经验草稿' ORDER BY version,id",
                    (self.sid,),
                )
            )
            first_version, second_version = versions
            self.assertFalse(json.loads(first_version["draft"] or "null"))
            self.assertEqual(json.loads(second_version["draft"])["content"], self.base["content"])
            conn.execute(
                "UPDATE entities SET payload=JSON_REMOVE(payload,'$.draft.draft_editor_ids',"
                "'$.draft.draft_authorship_tracked') WHERE id=?",
                (self.sid,),
            )
        with store.control_connection() as control, control.cursor() as cursor:
            cursor.execute(
                "SELECT s.actor_id,s.created_at started,f.created_at finished FROM sec_audit s JOIN sec_audit f "
                "ON s.request_id=f.request_id AND s.actor_id=f.actor_id AND s.resource=f.resource "
                "WHERE s.tenant_id=%s AND s.resource=%s AND s.action='business_request' AND f.action=s.action "
                "AND s.outcome='started' AND f.outcome='success' "
                "AND JSON_UNQUOTE(JSON_EXTRACT(s.details,'$.method'))='PATCH'",
                (principal.tenant_id, self.path + "/draft"),
            )
            receipts = list(cursor)
        self.assertEqual({r["actor_id"] for r in receipts}, {author, second})
        for receipt in receipts:
            self.assertLessEqual(receipt["started"], datetime.fromisoformat(first_version["created_at"]).timestamp())
            self.assertGreaterEqual(
                receipt["finished"], datetime.fromisoformat(second_version["created_at"]).timestamp()
            )
        asset = self.ok(self.builder.post(self.path + "/adopt", json=self.base))["entity"]
        self.assertEqual(set(asset["payload"]["r2_review"]["editor_ids"]), {author, self.person(self.builder)})
        pending = self.old_pending(asset["id"])
        detail_url = "/api/v2/reviews/" + asset["id"]
        self.assertFalse(self.ok(self.author.get(detail_url))["data"]["can_decide"])
        self.assertTrue(self.ok(self.observer.get(detail_url))["data"]["can_decide"])
        # An older archive lacks these two newer, transaction-local actor records. Both signed
        # requests and both business versions remain genuine, and their time windows fully overlap.
        with use_context(principal), closing(db.connect()) as conn, conn:
            conn.execute(
                "UPDATE activities SET actor='系统' WHERE entity_id=? "
                "AND action IN ('编辑经验草稿','原样保存经验草稿')",
                (self.sid,),
            )
        for actor in (self.author, self.observer):
            detail = self.ok(actor.get(detail_url))["data"]
            self.assertFalse(detail["can_decide"])
            self.assertIn("尚缺可核对的编辑人记录", detail["blocked_reason"])
            denied = actor.post(
                detail_url + ":decide", json={"decision": "approve_publish", "version": pending["version"]}
            )
            self.assertEqual(denied.status_code, 403, denied.text)
            self.assertIn("尚缺可核对的编辑人记录", denied.text)
        self.decide(pending, [], self.independent)

    def test_pending_preupgrade_merge_identifies_ancestor_and_secondary_manual_editors(self) -> None:
        self.base["title"] = "旧稿账册归并"
        self.proposal_prefix = "账册"
        source = self.adopt_legacy()
        (cid,) = self.candidates()
        primary, secondary = self.apply("split", [cid], self.applier)
        self.review(secondary, self.secondary, "save", summary="交接后须分别留存双方原件。")
        self.apply("merge", [secondary, primary], self.observer)
        self.review(primary, self.builder, "adopt")
        pending = self.old_pending(source["id"])
        self.decide(pending, [self.author, self.applier, self.secondary, self.observer], self.independent)

    def test_first_adoption_carries_prior_proposals_of_an_unadopted_preupgrade_candidate(self) -> None:
        self.base["title"] = "首次采纳的签收稿"
        self.proposal_prefix = "首次"
        self.adopt_legacy()
        (cid,) = self.candidates()
        _, child = self.apply("split", [cid], self.applier)
        self.apply("revise", [child], self.secondary)
        principal = store.authenticate_session(self.builder.cookies.get(store.COOKIE_NAME), "old-candidate")[0]
        with use_context(principal), closing(db.connect()) as conn, conn:
            row = conn.execute("SELECT payload FROM entities WHERE id=?", (self.sid,)).fetchone()
            payload = json.loads(row["payload"])
            candidate = next(item for item in payload["knowledge_candidates"] if item["id"] == child)
            candidate.pop("draft_editor_ids", None)
            candidate.pop("draft_authorship_tracked", None)
            conn.execute("UPDATE entities SET payload=? WHERE id=?", (json.dumps(payload), self.sid))
        adopted = self.review(child, self.builder, "adopt")
        self.publish(adopted["entity_id"], [self.author, self.applier, self.secondary], self.independent)

    def test_unattributed_confirmed_revision_keeps_independence_until_evidence_or_a_later_account(self) -> None:
        self.base["title"] = "旧稿签署存根"
        self.proposal_prefix = "存根"
        source = self.adopt_legacy()
        (cid,) = self.candidates()
        suggestion = self.propose("revise", [cid])
        person = store.authenticate_session(self.applier.cookies.get(store.COOKIE_NAME), "old-experience-import")[0]
        # Earlier imports retained business versions and activities without control-plane HTTP receipts.
        with use_context(person):
            experience.decide_proposal(self.sid, suggestion["id"], experience.ProposalDecision(action="apply"))
        self.review(cid, self.builder, "adopt")
        pending = self.old_pending(source["id"])
        detail_url = "/api/v2/reviews/" + pending["id"]
        blocked = self.ok(self.applier.get(detail_url))["data"]
        self.assertFalse(blocked["can_decide"])
        self.assertTrue(self.ok(self.independent.get(detail_url))["data"]["can_decide"])
        with use_context(person), closing(db.connect()) as conn, conn:
            # A partly retained historic copy: applied proposal with input/output ids survives; actor was unspecified.
            conn.execute("UPDATE activities SET actor='系统' WHERE entity_id=? AND action='确认经验调整'", (self.sid,))
            conn.execute(
                "UPDATE versions SET reason='旧版本',snapshot=JSON_REMOVE(snapshot,'$.payload.proposals') "
                "WHERE entity_id=? AND reason='人工采用AI修订建议'",
                (self.sid,),
            )
        for client in (self.applier, self.independent):
            detail = self.ok(client.get(detail_url))["data"]
            self.assertFalse(detail["can_decide"])
            self.assertIn("尚缺可核对的编辑人记录", detail["blocked_reason"])
            denied = client.post(
                detail_url + ":decide", json={"decision": "approve_publish", "version": pending["version"]}
            )
            self.assertEqual(denied.status_code, 403, denied.text)
            self.assertIn("尚缺可核对的编辑人记录", denied.text)
        admin = store.authenticate_session(self.author.cookies.get(store.COOKIE_NAME), "new-reviewer")[0]
        store.create_user(
            admin,
            username="x.new.reviewer",
            display_name="后期开通审核人",
            tenant_id=admin.tenant_id,
            role="reviewer",
            password="SyntheticFixture9!",
            must_change_password=False,
        )
        self.decide(pending, [], self.login("x.new.reviewer"))

    def test_experience_writers_require_local_writable_build_session_on_real_pending_records(self) -> None:
        self.base["title"] = "跨机构收件签署核对"
        self.adopt_legacy()
        (cid,) = self.candidates()
        ref = self.candidates()[cid]["source_refs"][0]
        update = {
            **self.base,
            "continuation_of": cid,
            "source_refs": [{"chunk_id": ref["chunk_id"], "quote": "分别签收并注明日期"}],
        }
        fake = AsyncMock(return_value=json.dumps({"candidates": [update]}, ensure_ascii=False))
        with (
            patch("backend.experience.config", return_value={"api_key": "local-fixture"}),
            patch("backend.experience.complete", fake),
        ):
            self.ok(self.builder.post(self.path + "/analyze", json={}))
            deadline = time.monotonic() + 15
            while not asyncio.run(jobs.run_one()):
                self.assertLess(time.monotonic(), deadline, "experience analysis job did not become available")
                time.sleep(0.1)
            self.assertEqual(fake.await_count, 1)
            (suggestion,) = self.candidates()[cid]["analysis_updates"]
            update_path = "/candidates/" + cid + "/updates/" + suggestion["id"]
            proposal = self.ok(self.builder.post(self.path + update_path))["proposal"]
            self.assertEqual(proposal["status"], "pending")
            before = self.record()
            foreign = self.login("r2.other")
            missing_csrf = self.login("fixture.builder")
            del missing_csrf.headers["X-CSRF-Token"]
            requests = (
                ("POST", "/draft", {}),
                ("PATCH", "/draft", self.base),
                ("POST", "/adopt", self.base),
                ("POST", "/candidates/" + cid + "/review", {**self.base, "action": "save"}),
                ("POST", "/proposals", {"candidate_ids": [cid], "instruction": "请逐项核对签收"}),
                ("POST", "/proposals/" + proposal["id"] + "/decision", {"action": "apply"}),
                ("POST", update_path, {}),
                ("POST", "/turns", {"content": self.narrative, "mode": "structured"}),
                ("POST", "/question", {"mode": "structured"}),
                ("POST", "/analyze", {}),
            )
            for method, suffix, body in requests:
                for client, status in [(foreign, 404), (self.independent, 403), (missing_csrf, 403)]:
                    with self.subTest(method=method, suffix=suffix, status=status):
                        denied = client.request(method, self.path + suffix, json=body)
                        self.assertEqual(denied.status_code, status, denied.text)
            self.assertEqual(self.record(), before)
        self.ok(self.secondary.patch(self.path + "/draft", json={**self.base, "summary": "新增交接记录核对。"}))
        rebuilt = self.ok(self.builder.post(self.path + "/draft"))["entity"]
        self.assertEqual(rebuilt["payload"]["draft"]["draft_editor_ids"], [self.person(self.secondary)])
