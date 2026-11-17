"""Stored A2 results, legacy boundary guards and counterexamples on real SQL rows."""

import json
import sqlite3
import unittest
from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch

from fastapi import HTTPException

from backend import db
from backend.api.v1 import core
from backend.core.errors import ApiError
from backend.kernel import elements
from backend.kernel.metadata import get_meta
from backend.security.context import use_context
from tests.security.test_permissions import principal

# Save specification: five permitted forms and five blocked forms.
SAVE_ALLOWED = [
    ("data", {}),
    (
        "object_type",
        {
            "schema": "object_type/1",
            "properties": [{"key": "amount", "name": "金额", "datatype": "decimal", "note": "合同总额"}],
        },
    ),
    ("term", {"schema": "term/1", "definition": "供应主体"}),
    ("position", {"schema": "position/1", "duties": "核对材料"}),
    ("party", {"schema": "party/1", "party_type": "supplier"}),
]
SAVE_BLOCKED = [
    {"title": "  "},
    {"kind": "object_type", "payload": {"schema": "object_type/1", "extends": "MISSING"}},
    {"kind": "object_type", "payload": {"schema": "object_type/1", "is_interface": True}},
    {"meta": {"owner_user_id": "forged"}},
    {"kind": "metric", "payload": {"schema": "metric/1", "object_type": "MISSING"}},
]


class Connection:
    def __init__(self) -> None:
        self.raw = sqlite3.connect(":memory:", check_same_thread=False)
        self.raw.row_factory = sqlite3.Row
        self.raw.executescript("""
            CREATE TABLE entities(id TEXT PRIMARY KEY,kind TEXT,title TEXT,summary TEXT,domain TEXT,org_id TEXT,
                status TEXT,source_kind TEXT,payload TEXT,created_at TEXT,updated_at TEXT,version INTEGER);
            CREATE TABLE relations(id TEXT PRIMARY KEY,source TEXT,target TEXT,type TEXT,label TEXT,
                status TEXT,payload TEXT);
            CREATE TABLE versions(id INTEGER PRIMARY KEY,entity_id TEXT,version INTEGER,snapshot TEXT,
                created_at TEXT,reason TEXT);
            CREATE TABLE activities(id INTEGER PRIMARY KEY,entity_id TEXT,action TEXT,detail TEXT,
                actor TEXT,created_at TEXT);
            CREATE TABLE skill_documents(entity_id TEXT PRIMARY KEY);
            CREATE TABLE entity_ownership(entity_id TEXT PRIMARY KEY,tenant_id TEXT,created_by TEXT,
                created_at TEXT,updated_by TEXT,updated_at TEXT);
            CREATE TABLE entity_meta(entity_id TEXT PRIMARY KEY,pillar TEXT,subtype TEXT,
                module_id TEXT,element_key TEXT,
                owner_user_id TEXT,provider_note TEXT,valid_from TEXT,valid_to TEXT,sensitivity TEXT,
                gates TEXT,card_text TEXT,updated_at TEXT,
                UNIQUE(module_id,element_key));
        """)

    def execute(self, statement: str, parameters: tuple = ()) -> sqlite3.Cursor:
        statement = statement.replace(" FOR UPDATE", "")
        statement = statement.replace(
            "ON DUPLICATE KEY UPDATE updated_by=VALUES(updated_by),updated_at=VALUES(updated_at)",
            "ON CONFLICT(entity_id) DO UPDATE SET updated_by=excluded.updated_by,updated_at=excluded.updated_at",
        )
        return self.raw.execute(statement, parameters)

    def close(self) -> None:
        pass

    def commit(self) -> None:
        self.raw.commit()

    def __enter__(self) -> "Connection":
        self.raw.__enter__()
        return self

    def __exit__(self, *args: object) -> bool:
        return self.raw.__exit__(*args)


class ElementWrites(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = Connection()
        self.addCleanup(self.conn.raw.close)
        self.editor = principal()
        self.enterContext(use_context(self.editor))
        self.enterContext(patch.object(core, "connect", return_value=self.conn))

    def create(self, kind: str = "knowledge", **changes: object) -> dict:
        body = {"kind": kind, "title": "业务内容", "payload": {}, **changes}
        with self.conn:
            return elements.create_draft(self.conn, body)

    def read(self, ident: str) -> dict:
        return db.entity_json(self.conn.execute("SELECT * FROM entities WHERE id=?", (ident,)).fetchone())

    def test_save_specification_table_and_atomic_rejection(self) -> None:
        for kind, payload in SAVE_ALLOWED:
            with self.subTest(kind=kind):
                created = self.create(kind, payload=payload)
                self.assertEqual(self.read(created["id"])["payload"], created["payload"])
                self.assertEqual(get_meta(self.conn, created["id"])["entity_id"], created["id"])
        before = self.conn.execute("SELECT count(*) FROM entities").fetchone()[0]
        for changes in SAVE_BLOCKED:
            with self.subTest(changes=changes), self.assertRaises(HTTPException), self.conn:
                elements.create_draft(self.conn, {"kind": "knowledge", "title": "业务内容", "payload": {}, **changes})
            self.assertEqual(self.conn.execute("SELECT count(*) FROM entities").fetchone()[0], before)

    def test_initial_version_metadata_and_card_are_stored(self) -> None:
        created = self.create(meta={"valid_from": "2026-01-01", "element_key": "definition"})
        saved = json.loads(
            self.conn.execute("SELECT snapshot FROM versions WHERE entity_id=?", (created["id"],)).fetchone()[0]
        )
        self.assertEqual(saved["version"], 1)
        self.assertEqual(saved["_meta"]["valid_from"], "2026-01-01")
        self.assertEqual(saved["_meta"]["module_id"], "MOD-DEFAULT")
        self.assertIn("业务内容", get_meta(self.conn, created["id"])["card_text"])
        self.assertEqual(self.conn.execute("SELECT action FROM activities").fetchone()[0], "创建")
        broken = deepcopy(saved)
        broken.pop("_meta")
        with self.assertRaises(AssertionError):
            self.assertIn("_meta", broken)

    def test_group_create_and_derived_links(self) -> None:
        data = self.create("data", title="金额")
        obj = self.create(
            "object_type",
            payload={
                "schema": "object_type/1",
                "properties": [
                    {"key": "amount", "name": "金额", "datatype": "decimal", "data_id": data["id"]},
                ],
            },
        )
        self.create(
            "link_type",
            payload={
                "schema": "link_type/1",
                "source_type": obj["id"],
                "target_type": obj["id"],
                "join": {"source_property": "amount", "target_property": "amount"},
            },
        )
        self.create(
            "metric", payload={"schema": "metric/1", "object_type": obj["id"], "formula": {"oxl": "amount * 2"}}
        )
        rule = self.create(
            "rule",
            payload={
                "judgement": {
                    "applies_to": {"object_type": obj["id"]},
                    "condition": {"oxl": "amount > 1"},
                    "requirement": {"oxl": "amount < 10"},
                }
            },
        )
        self.create(
            "process",
            payload={
                "schema": "process/1",
                "steps": [{"key": "S1", "name": "核对", "constraints": [{"rule_id": rule["id"]}]}],
            },
        )
        self.assertEqual(self.conn.execute("SELECT count(*) FROM relations").fetchone()[0], 6)
        self.assertEqual(self.read(rule["id"])["payload"]["judgement"]["condition"]["sentence"], "金额 大于 1")
        edges = [tuple(row) for row in self.conn.execute("SELECT source,target,type FROM relations")]
        self.assertIn((obj["id"], data["id"], "has_property"), edges)
        with self.assertRaises(AssertionError):
            self.assertIn((obj["id"], "wrong", "has_property"), edges)

    def test_update_version_snapshot_and_identity_spoofing(self) -> None:
        created = self.create(payload={"r2_review": {"last_editor_id": "forged"}})
        self.assertEqual(created["payload"]["r2_review"]["last_editor_id"], self.editor.user_id)
        second = replace(self.editor, user_id="second", username="second")
        with use_context(second), self.conn:
            changed = elements.update_draft(
                self.conn,
                self.read(created["id"]),
                {
                    "title": "修订标题",
                    "expected_version": 1,
                    "payload": {"r2_review": {"last_editor_id": "forged"}},
                },
            )
        self.assertEqual(changed["version"], 2)
        self.assertEqual(changed["payload"]["r2_review"]["last_editor_id"], "second")
        self.assertEqual(self.read(created["id"])["title"], "修订标题")
        prior = json.loads(self.conn.execute("SELECT snapshot FROM versions ORDER BY id DESC LIMIT 1").fetchone()[0])
        self.assertEqual(prior["title"], "业务内容")

    def test_metadata_edit_updates_last_editor_and_invalidates_submission(self) -> None:
        created = self.create()
        second = replace(self.editor, user_id="metadata-editor")
        with use_context(second), self.conn:
            updated = elements.update_draft(self.conn, self.read(created["id"]), {"meta": {"valid_from": "2026-01-01"}})
        self.assertEqual(updated["payload"]["r2_review"]["last_editor_id"], "metadata-editor")
        self.assertIsNone(updated["payload"]["r2_review"]["submitted_by"])

    def test_conflicting_version_blank_name_and_blank_reason_leave_rows_unchanged(self) -> None:
        created = self.create()
        for changes in ({"expected_version": 9}, {"expected_version": True}, {"title": " "}, {"reason": " "}):
            with self.subTest(changes=changes), self.assertRaises(HTTPException), self.conn:
                elements.update_draft(self.conn, self.read(created["id"]), changes)
            self.assertEqual(self.read(created["id"])["version"], 1)
            self.assertEqual(self.conn.execute("SELECT count(*) FROM versions").fetchone()[0], 1)

    def test_restore_preserves_current_identity_and_ownership(self) -> None:
        created = self.create(meta={"valid_from": "2026-01-01"})
        saved = json.loads(self.conn.execute("SELECT snapshot FROM versions").fetchone()[0])
        with self.conn:
            elements.update_draft(
                self.conn, self.read(created["id"]), {"title": "新标题", "meta": {"valid_from": "2026-06-01"}}
            )
        saved["_meta"]["owner_user_id"] = "forged-owner"
        with self.conn:
            restored = elements.restore(self.conn, self.read(created["id"]), saved, "核对原版后还原")
        self.assertEqual((restored["title"], restored["status"], restored["version"]), ("业务内容", "draft", 3))
        self.assertEqual(get_meta(self.conn, created["id"])["valid_from"], "2026-01-01")
        self.assertIsNone(get_meta(self.conn, created["id"])["owner_user_id"])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM versions").fetchone()[0], 3)

    def test_v1_create_update_and_restore_use_kernel(self) -> None:
        item = core._create_entity({"kind": "term", "title": "术语", "payload": {"schema": "term/1"}})
        changed = core._edit_entity(item["id"], {"title": "新术语", "expected_version": 1})
        self.assertEqual(changed["version"], 2)
        with use_context(replace(self.editor, role="admin")):
            restored = core._restore_entity(item["id"], {"version": 1, "reason": "恢复原含义", "expected_version": 2})
        self.assertEqual((restored["title"], restored["version"]), ("术语", 3))
        self.assertIsNotNone(get_meta(self.conn, item["id"]))

    def test_submit_gate_failure_rolls_back_review_marker_and_version(self) -> None:
        created = self.create()
        with (
            patch("backend.kernel.gates.enforce", side_effect=ApiError("GATE_NOT_PASSED")),
            self.assertRaises(ApiError),
            self.conn,
        ):
            core.entity_action_in_transaction(self.conn, created["id"], {"action": "submit", "expected_version": 1})
        actual = self.read(created["id"])
        self.assertEqual((actual["status"], actual["version"]), ("draft", 1))
        self.assertNotIn("submitted_by", actual["payload"]["r2_review"])

    def test_submit_then_same_editor_rejected_and_other_reviewer_approved(self) -> None:
        created = self.create(payload={"source_refs": [{"material_id": "material"}]})
        with patch("backend.kernel.gates.enforce"), self.conn:
            submitted = core.entity_action_in_transaction(self.conn, created["id"], {"action": "submit"})
        self.assertEqual(submitted["status"], "review")
        with self.assertRaises(ApiError), self.conn:
            core.entity_action_in_transaction(self.conn, created["id"], {"action": "approve"})
        reviewer = replace(self.editor, user_id="independent", username="independent", role="reviewer")
        with (
            use_context(reviewer),
            patch("backend.kernel.gates.enforce"),
            patch("backend.provenance.normalize_source_ref"),
            self.conn,
        ):
            approved = core.entity_action_in_transaction(
                self.conn, created["id"], {"action": "approve", "expected_version": 2}
            )
        self.assertEqual((approved["status"], approved["version"]), ("approved", 3))

    def test_inheritance_cycle_and_invalid_join_rejected(self) -> None:
        parent = self.create("object_type", payload={"schema": "object_type/1"})
        child = self.create("object_type", payload={"schema": "object_type/1", "extends": parent["id"]})
        with self.assertRaises(HTTPException), self.conn:
            elements.update_draft(
                self.conn, self.read(parent["id"]), {"payload": {"schema": "object_type/1", "extends": child["id"]}}
            )
        with self.assertRaises(HTTPException):
            self.create(
                "link_type",
                payload={
                    "schema": "link_type/1",
                    "source_type": parent["id"],
                    "target_type": child["id"],
                    "join": {"source_property": "missing", "target_property": "missing"},
                },
            )

    def test_sentence_change_requires_explicit_confirmation(self) -> None:
        created = self.create(
            "rule", payload={"judgement": {"condition": {"oxl": "true"}, "requirement": {"oxl": "true"}}}
        )
        payload = deepcopy(created["payload"])
        payload["judgement"]["condition"]["sentence"] = "人工重新说明"
        with self.assertRaises(HTTPException), self.conn:
            elements.update_draft(self.conn, self.read(created["id"]), {"payload": payload})
        with self.conn:
            changed = elements.update_draft(
                self.conn, self.read(created["id"]), {"payload": payload, "sentence_confirmed": True}
            )
        self.assertEqual(changed["payload"]["judgement"]["condition"]["sentence"], "人工重新说明")

    def test_readonly_and_legacy_specialized_guards_remain(self) -> None:
        created = self.create()
        with use_context(replace(self.editor, readonly=True)), self.assertRaises(HTTPException):
            elements.update_draft(self.conn, created, {"title": "forbidden"})
        for body in (
            {"kind": "organization"},
            {"kind": "person"},
            {"kind": "matter"},
            {"kind": "rule", "payload": {"authority_spec": {"scope": "x"}}},
        ):
            with self.subTest(body=body), self.assertRaises(HTTPException):
                core._create_entity({"title": "禁止绕过", **body})
        with self.assertRaises(HTTPException):
            core._create_entity(
                {"kind": "term", "title": "术语", "status": "published", "payload": {"schema": "term/1"}}
            )
