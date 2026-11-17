"""Accepted changeset content never silently adopts an unseen external draft revision."""

import os
import unittest
from contextlib import closing
from unittest.mock import MagicMock

from backend import db, materials
from backend.domains import build
from backend.security import store
from backend.security.context import use_context
from tests.r2 import test_build as construction


class ExplicitDates(unittest.TestCase):
    def test_historical_dates_do_not_become_effective_dates(self) -> None:
        conn = MagicMock()
        candidate = {"kind": "data", "content": "付款事项"}
        cases = (
            ("公司自2020年1月1日起开展试点。生效日期：2026-11-17。", "2026-11-17"),
            ("自2020年1月1日起施行旧办法。生效日期：2026年11月17日。", "2026-11-17"),
            ("公司自2020年1月1日起开展试点。", None),
            ("本制度自2026年11月17日起生效。", "2026-11-17"),
            ("本办法于2026-11-17施行。", "2026-11-17"),
        )
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(build.enrich_candidate(conn, candidate, text).get("valid_from"), expected)


@unittest.skipUnless(os.getenv("RUN_JOBS_MYSQL_TESTS") == "1", "opt-in changeset versions/MySQL")
class ChangesetVersionsMySQL(unittest.TestCase):
    setUpClass = classmethod(construction.BuildMySQL.setUpClass.__func__)
    tearDownClass = classmethod(construction.BuildMySQL.tearDownClass.__func__)
    login = construction.BuildMySQL.login
    invoke = construction.BuildMySQL.invoke
    proposal = construction.BuildMySQL.proposal

    def test_external_edit_blocks_old_submission_then_explicit_refresh_rebases(self) -> None:
        builder = self.login("fixture.builder")
        proposal = self.proposal(builder)
        item = proposal["items"][0]
        selected = {"changeset_id": proposal["id"], "item_ids": [item["id"]]}
        self.invoke(builder, "changeset.decide_items", {**selected, "decision": "accepted"})
        ident = self.invoke(builder, "changeset.apply", {"changeset_id": proposal["id"]})["entity_ids"][0]
        entity = builder.get("/api/entities/" + ident).json()
        changed_content = entity["payload"]["content"] + "外部编辑补充核对附件。"
        self.invoke(
            builder,
            "element.update_draft",
            {
                "entity_id": ident,
                "version": 1,
                "payload": {**entity["payload"], "content": changed_content},
                "reason": "要素页补充内容",
            },
        )
        blocked = builder.post(
            "/api/v2/capabilities/changeset.apply_and_submit:invoke", json={"changeset_id": proposal["id"]}
        )
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertIn("刷新", blocked.text)
        self.assertEqual(builder.get("/api/entities/" + ident).json()["status"], "draft")
        current = self.invoke(builder, "changeset.get", {"changeset_id": proposal["id"]})
        self.assertFalse(current["can_submit"])
        self.assertTrue(current["submit_blockers"])
        reflected = next(row for row in current["items"] if row["id"] == item["id"])
        self.assertEqual(reflected["after_data"]["content"], changed_content)
        self.assertEqual((reflected.get("applied_version"), reflected.get("applied_status")), (2, "draft"))
        edit = {key: reflected["after_data"][key] for key in ("title", "kind", "content")}
        stale = builder.post(
            "/api/v2/capabilities/changeset.decide_items:invoke",
            json={
                **selected,
                "decision": "edited",
                "edit": {**edit, "expected_version": 1},
            },
        )
        self.assertEqual(stale.status_code, 409, stale.text)
        self.invoke(
            builder,
            "changeset.decide_items",
            {
                **selected,
                "decision": "edited",
                "edit": {**edit, "expected_version": 2},
            },
        )
        self.assertEqual(builder.get("/api/entities/" + ident).json()["payload"]["content"], changed_content)
        applied = self.invoke(builder, "changeset.apply_and_submit", {"changeset_id": proposal["id"]})
        repeated = self.invoke(builder, "changeset.apply_and_submit", {"changeset_id": proposal["id"]})
        self.assertEqual(applied["entity_ids"], repeated["entity_ids"])
        self.assertEqual(builder.get("/api/entities/" + ident).json()["version"], 4)

    def test_returned_workflow_can_resubmit_unchanged_then_edit_current_version(self) -> None:
        builder, reviewer = self.login("fixture.builder"), self.login("fixture.reviewer")
        proposal = self.proposal(builder)
        item = proposal["items"][0]
        selected = {"changeset_id": proposal["id"], "item_ids": [item["id"]]}
        self.invoke(builder, "changeset.decide_items", {**selected, "decision": "accepted"})
        ident = self.invoke(builder, "changeset.apply_and_submit", {"changeset_id": proposal["id"]})["entity_ids"][0]
        returned = reviewer.post(
            f"/api/v2/reviews/{ident}:decide", json={"decision": "return", "version": 2, "reason": "再次核对"}
        )
        self.assertEqual(returned.status_code, 200, returned.text)
        self.invoke(builder, "changeset.apply_and_submit", {"changeset_id": proposal["id"]})
        self.assertEqual(builder.get("/api/entities/" + ident).json()["version"], 4)
        returned = reviewer.post(
            f"/api/v2/reviews/{ident}:decide", json={"decision": "return", "version": 4, "reason": "补充说明"}
        )
        self.assertEqual(returned.status_code, 200, returned.text)
        current = self.invoke(builder, "changeset.get", {"changeset_id": proposal["id"]})
        reflected = next(row for row in current["items"] if row["id"] == item["id"])
        self.assertEqual((reflected.get("applied_version"), reflected.get("applied_status")), (5, "returned"))
        edit = {key: reflected["after_data"][key] for key in ("title", "kind", "content")}
        edit["content"] += "补充办理说明。"
        self.invoke(
            builder,
            "changeset.decide_items",
            {**selected, "decision": "edited", "edit": {**edit, "expected_version": 5}},
        )
        self.invoke(builder, "changeset.apply_and_submit", {"changeset_id": proposal["id"]})
        saved = builder.get("/api/entities/" + ident).json()
        self.assertEqual((saved["status"], saved["version"]), ("review", 7))
        self.assertEqual(saved["payload"]["content"], edit["content"])

    def test_changed_nonrule_to_rule_suggests_tenant_or_named_target_without_early_confirmation(self) -> None:
        builder = self.login("fixture.builder")
        person = store.authenticate_session(builder.cookies.get("ontology_session"), "r4-kind-change")[0]
        created = builder.post(
            "/api/entities",
            json={
                "kind": "object_type",
                "title": "命名付款对象",
                "payload": {"schema": "object_type/1", "properties": []},
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        named_target = created.json()
        for named in (False, True):
            with self.subTest(named=named):
                proposal = self.proposal(builder)
                item = proposal["items"][0]
                after = item["after_data"]
                with use_context(person), closing(db.connect()) as conn, conn:
                    target = named_target if named else None
                    expected = target["id"] if target else person.tenant_id
                    if named:
                        self.assertIsNotNone(target, "Named-target scenario must have a local organizational target")
                    after = {key: value for key, value in after.items() if not key.startswith("applies_to_")}
                    after["kind"] = "data"
                    conn.execute(
                        "UPDATE changeset_items SET target_kind='data',after_data=? WHERE id=?",
                        (materials.dump(after), item["id"]),
                    )
                edit = {key: after[key] for key in ("title", "content")}
                edit.update(kind="rule", scope=target["title"] if target else "本机构")
                changed = self.invoke(
                    builder,
                    "changeset.decide_items",
                    {"changeset_id": proposal["id"], "item_ids": [item["id"]], "decision": "edited", "edit": edit},
                )
                reflected = next(row for row in changed["items"] if row["id"] == item["id"])
                self.assertEqual(reflected["after_data"].get("applies_to_id"), expected)
                self.assertIsNone(reflected["applied_entity_id"])
                ident = self.invoke(builder, "changeset.apply_and_submit", {"changeset_id": proposal["id"]})[
                    "entity_ids"
                ][0]
                with use_context(person), closing(db.connect()) as conn:
                    links = conn.execute(
                        "SELECT target,status FROM relations WHERE source=? AND type='applies_to'", (ident,)
                    ).fetchall()
                self.assertEqual([(row["target"], row["status"]) for row in links], [(expected, "confirmed")])
