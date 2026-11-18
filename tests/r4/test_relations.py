"""Stored derived-edge behavior and existing security guards remain observable."""

import hashlib
import json
import os
import sqlite3
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from backend.api.v1 import core
from backend.capabilities.context import CapabilityContext
from backend.capabilities.registry import registry
from backend.capabilities.relations import RelationDecision, decide
from backend.capabilities.semantic import DescribeInput, describe
from backend.core.errors import ApiError
from backend.kernel.relations import derived_edges, propose, sync_derived
from backend.relation_semantics import RELATION_SEMANTICS, semantic_relation
from tests.r2 import test_build as build_tests
from tests.security.test_permissions import principal


class SQLiteTransaction:
    """Exercise SQL/result behavior locally; MySQL concurrency is covered separately."""

    def __init__(self) -> None:
        self.raw = sqlite3.connect(":memory:")
        self.raw.row_factory = sqlite3.Row
        self.raw.executescript(
            "CREATE TABLE entities(id TEXT PRIMARY KEY,kind TEXT,title TEXT,version INT,"
            "status TEXT,payload TEXT,updated_at TEXT);"
            ""
            "CREATE TABLE relations(id TEXT PRIMARY KEY,source TEXT,target TEXT,type TEXT,"
            "label TEXT,status TEXT,payload TEXT);"
            " CREATE TABLE versions(id INTEGER PRIMARY KEY,entity_id TEXT,version INT,"
            "snapshot TEXT,created_at TEXT,reason TEXT);"
        )

    def execute(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        return self.raw.execute(sql.replace(" FOR UPDATE", ""), args)

    def close(self) -> None:
        pass

    def commit(self) -> None:
        self.raw.commit()

    def __enter__(self) -> "SQLiteTransaction":
        self.raw.__enter__()
        return self

    def __exit__(self, *args: object) -> bool:
        return self.raw.__exit__(*args)

    def entity(self, ident: str, kind: str, payload: dict | None = None) -> None:
        self.execute(
            "INSERT INTO entities(id,kind,title,version,status,payload) VALUES(?,?,?,?,?,?)",
            (ident, kind, ident, 1, "draft", json.dumps(payload or {})),
        )
        self.raw.commit()


class DerivedRelations(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = SQLiteTransaction()
        self.addCleanup(self.conn.raw.close)
        for ident, kind in (
            ("O", "object_type"),
            ("O2", "object_type"),
            ("D", "data"),
            ("R", "rule"),
            ("S", "skill"),
            ("K", "knowledge"),
            ("P", "position"),
            ("ORG", "organization"),
            ("PTY", "party"),
        ):
            self.conn.entity(ident, kind)

    def test_old_45_types_unchanged(self) -> None:
        keys = [
            "derived_from",
            "derived_from_evidence",
            "sourced_from",
            "references_source",
            "references_material",
            "anchored_in",
            "based_on",
            "uses_data",
            "requires_data",
            "references_data",
            "references_skill",
            "implements_rule",
            "uses_asset",
            "uses_method",
            "produces",
            "applies_in_demo",
            "explains",
            "informs",
            "summarizes",
            "member_of",
            "applies_to",
            "belongs_to",
            "owned_by",
            "coordinated_by",
            "collaborates_with",
            "contains",
            "contains_matter",
            "includes_knowledge",
            "handles",
            "raised_in",
            "maps_to",
            "supported_by",
            "supports",
            "supports_matter",
            "explains_matter",
            "contradicts_matter",
            "gap_for_matter",
            "reviews",
            "inspects",
            "related_case",
            "references",
            "related_to",
            "refines",
            "supersedes",
            "merged_into",
        ]
        actual = {key: RELATION_SEMANTICS[key] for key in keys}

        def digest(value: dict) -> str:
            return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

        self.assertEqual(len(keys), 45)
        self.assertEqual(digest(actual), "f577c214585e4ed155d14abcd117fab58198f84277c900384f4e7612cf483e73")
        with self.assertRaises(AssertionError):
            self.assertEqual(
                digest({**actual, "uses_method": ("运用", "", False, "structure")}),
                "f577c214585e4ed155d14abcd117fab58198f84277c900384f4e7612cf483e73",
            )

    def test_all_nonprocess_derivation_fields(self) -> None:
        cases = [
            (
                "object_type",
                {"properties": [{"data_id": "D"}], "implements": ["O2"], "extends": "O2"},
                {"has_property", "implements", "extends"},
            ),
            ("link_type", {"source_type": "O", "target_type": "O2"}, {"link_source", "link_target"}),
            ("metric", {"object_type": "O"}, {"measures"}),
            ("rule", {"judgement": {"applies_to": {"object_type": "O"}}}, {"governs"}),
            ("person", {"position_id": "P"}, {"holds_position"}),
            ("position", {"org_id": "ORG"}, {"position_in"}),
            ("party", {"roles": [{"org_id": "ORG", "role": "supplier"}]}, {"plays_role"}),
        ]
        for kind, payload, expected in cases:
            with self.subTest(kind=kind):
                actual = {edge["type"] for edge in derived_edges({"id": "E", "kind": kind, "payload": payload})}
                self.assertEqual(actual, expected)
                with self.assertRaises(AssertionError):
                    self.assertEqual(set(), expected)

    def test_process_stores_five_types_and_step_keys(self) -> None:
        step = {
            "key": "S1",
            "actors": [{"position_id": "P"}],
            "basis": [{"rule_id": "R"}],
            "constraints": [{"rule_id": "R"}],
            "inputs": [{"data_id": "D"}],
            "outputs": [{"object_type": "O"}],
            "decision": {"skill_id": "S"},
        }
        entity = {"id": "PROC", "kind": "process", "payload": {"steps": [step, {**step, "key": "S2"}]}}
        result = sync_derived(self.conn, entity)
        self.assertEqual(len(result), 10)
        rows = [dict(row) for row in self.conn.execute("SELECT * FROM relations")]
        self.assertEqual({json.loads(row["payload"])["step_key"] for row in rows}, {"S1", "S2"})
        self.assertTrue(
            all(row["status"] == "confirmed" and json.loads(row["payload"])["derived"] is True for row in rows)
        )
        with self.assertRaises(AssertionError):
            self.assertEqual(
                {
                    json.loads(row["payload"])["step_key"]
                    for row in rows
                    if json.loads(row["payload"])["step_key"] != "S2"
                },
                {"S1", "S2"},
            )

    def test_sync_is_idempotent_preserves_ids(self) -> None:
        entity = {"id": "O", "kind": "object_type", "payload": {"properties": [{"data_id": "D"}]}}
        first = sync_derived(self.conn, entity)
        before = self.conn.raw.total_changes
        second = sync_derived(self.conn, entity)
        self.assertEqual(first, second)
        self.assertEqual(self.conn.raw.total_changes, before)
        with self.assertRaises(AssertionError):
            self.assertEqual([{**second[0], "id": "replaced"}], first)

    def test_sync_removes_stale_only_owned_edges(self) -> None:
        entity = {"id": "O", "kind": "object_type", "payload": {"properties": [{"data_id": "D"}]}}
        sync_derived(self.conn, entity)
        self.conn.execute(
            "INSERT INTO relations(id,source,target,type,label,status,payload) "
            "VALUES('manual','O','D','references','参考','rejected','{}')"
        )
        sync_derived(self.conn, {**entity, "payload": {}})
        actual = [row["id"] for row in self.conn.execute("SELECT * FROM relations")]
        self.assertEqual(actual, ["manual"])
        with self.assertRaises(AssertionError):
            self.assertEqual([], actual)

    def test_missing_foreign_tenant_reference_fails_before_changes(self) -> None:
        entity = {
            "id": "O",
            "kind": "object_type",
            "payload": {"properties": [{"data_id": "D"}, {"data_id": "other-tenant-data"}]},
        }
        with self.assertRaises(HTTPException) as error:
            sync_derived(self.conn, entity)
        self.assertEqual(error.exception.status_code, 422)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM relations").fetchone()[0], 0)

    def test_wrong_kind_reference_is_rejected(self) -> None:
        with self.assertRaises(HTTPException):
            sync_derived(self.conn, {"id": "O", "kind": "object_type", "payload": {"properties": [{"data_id": "R"}]}})
        self.assertEqual(self.conn.execute("SELECT count(*) FROM relations").fetchone()[0], 0)

    def test_extends_cannot_create_a_cycle(self) -> None:
        self.conn.execute("UPDATE entities SET payload=? WHERE id='O2'", (json.dumps({"extends": "O"}),))
        with self.assertRaises(HTTPException):
            sync_derived(self.conn, {"id": "O", "kind": "object_type", "payload": {"extends": "O2"}})
        self.assertEqual(self.conn.execute("SELECT count(*) FROM relations").fetchone()[0], 0)

    def test_implements_requires_interface_and_required_properties(self) -> None:
        entity = {"id": "O", "kind": "object_type", "payload": {"implements": ["O2"]}}
        with self.assertRaises(HTTPException):
            sync_derived(self.conn, entity)
        self.conn.execute(
            "UPDATE entities SET payload=? WHERE id='O2'",
            (
                json.dumps(
                    {"is_interface": True, "properties": [{"key": "amount", "datatype": "decimal", "required": True}]}
                ),
            ),
        )
        with self.assertRaises(HTTPException):
            sync_derived(self.conn, entity)
        entity["payload"]["properties"] = [{"key": "amount", "datatype": "decimal"}]
        self.assertEqual(sync_derived(self.conn, entity)[0]["type"], "implements")

    def test_note_only_actor_does_not_invent_relationship(self) -> None:
        self.assertEqual(
            derived_edges(
                {
                    "id": "PROC",
                    "kind": "process",
                    "payload": {"steps": [{"key": "S1", "actors": [{"note": "经办人待确认"}]}]},
                }
            ),
            [],
        )

    def test_process_method_reading_is_contextual(self) -> None:
        row = {"source": "a", "target": "b", "type": "uses_method"}
        self.assertEqual(semantic_relation(row, "process")["family"], "structure")
        self.assertEqual(semantic_relation(row, "matter")["family"], "application")

    def test_derived_relation_work_entry_requires_element_edit(self) -> None:
        row = {"source": "O", "payload": json.dumps({"derived": True})}
        entry = core.relation_work_entry(self.conn, row)
        self.assertEqual(entry["href"], "/assets/O")
        self.assertIn("随要素一起审核", entry["reason"])

    def test_derived_relation_decision_does_not_change_status(self) -> None:
        edge = sync_derived(
            self.conn, {"id": "O", "kind": "object_type", "payload": {"properties": [{"data_id": "D"}]}}
        )[0]
        self.conn.commit()
        with patch.object(core, "connect", return_value=self.conn), self.assertRaises(HTTPException):
            core._confirm_relation(edge["id"], {"status": "rejected", "reason": "直接编辑尝试"})
        self.assertEqual(
            self.conn.execute("SELECT status FROM relations WHERE id=?", (edge["id"],)).fetchone()[0], "confirmed"
        )

    def test_proposal_creates_candidate_and_snapshot(self) -> None:
        from backend.security.context import use_context

        builder = principal("modeler")
        with (
            use_context(builder),
            patch("backend.kernel.relations.db.connect", return_value=self.conn),
            patch("backend.skills.guard_generic_entity") as guard,
            patch("backend.kernel.relations.db.log"),
        ):
            result = propose("O", "D", "references", "口径相关")
        self.assertEqual(result["payload"]["proposed_by"], builder.user_id)
        row = self.conn.execute("SELECT * FROM relations WHERE id=?", (result["id"],)).fetchone()
        self.assertEqual(row["status"], "candidate")
        self.assertEqual(json.loads(row["payload"])["proposal_reason"], "口径相关")
        self.assertEqual(self.conn.execute("SELECT version FROM entities WHERE id='O'").fetchone()[0], 2)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM versions").fetchone()[0], 1)
        self.assertEqual(guard.call_count, 2)
        with self.assertRaises(AssertionError):
            self.assertEqual("confirmed", row["status"])

    def test_proposal_cannot_forge_derived_type(self) -> None:
        with self.assertRaises(HTTPException):
            propose("O", "D", "has_property", "试图绕过载荷")

    def test_proposal_cannot_bypass_managed_skill(self) -> None:
        with (
            patch("backend.kernel.relations.db.connect", return_value=self.conn),
            patch("backend.skills.guard_generic_entity", side_effect=HTTPException(409, "技能包受保护")),
            self.assertRaises(HTTPException),
        ):
            propose("O", "S", "references", "尝试变更")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM relations").fetchone()[0], 0)

    def test_proposal_cannot_bypass_special_workflow(self) -> None:
        self.conn.entity("task", "task")
        with patch("backend.kernel.relations.db.connect", return_value=self.conn), self.assertRaises(HTTPException):
            propose("O", "task", "references", "尝试变更")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM relations").fetchone()[0], 0)

    def test_proposal_cross_tenant_id_is_not_found(self) -> None:
        with (
            patch("backend.kernel.relations.db.connect", return_value=self.conn),
            self.assertRaises(HTTPException) as error,
        ):
            propose("O", "other-tenant-item", "references", "跨机构尝试")
        self.assertEqual(error.exception.status_code, 404)


class CapabilityBoundaries(unittest.TestCase):
    def test_proposal_and_decision_default_deny_without_permission(self) -> None:
        for ident in ("relation.propose", "relation.decide"):
            with (
                self.subTest(ident=ident),
                patch("backend.capabilities.registry.store.append_audit"),
                self.assertRaises(ApiError) as error,
            ):
                registry.invoke(ident, CapabilityContext(principal("viewer"), permissions=frozenset()), {})
            self.assertEqual(error.exception.code, "PERMISSION_DENIED")

    def test_mutation_readonly_denied_even_with_permission(self) -> None:
        for ident in ("relation.propose", "relation.decide"):
            with (
                self.subTest(ident=ident),
                patch("backend.capabilities.registry.store.append_audit"),
                self.assertRaises(ApiError) as error,
            ):
                registry.invoke(
                    ident,
                    CapabilityContext(principal(readonly=True), permissions=frozenset({"build.edit", "review.decide"})),
                    {},
                )
            self.assertEqual(error.exception.code, "PERMISSION_DENIED")

    def test_assistant_cannot_make_review_decision(self) -> None:
        ctx = CapabilityContext(
            principal(), permissions=frozenset({"review.decide"}), binding="tool", assistant="builder"
        )
        with patch("backend.capabilities.registry.store.append_audit"), self.assertRaises(ApiError) as error:
            registry.invoke("relation.decide", ctx, {"relation_id": "r", "status": "confirmed", "reason": "采纳"})
        self.assertEqual(error.exception.code, "PERMISSION_DENIED")

    def test_decide_delegates_to_legacy_guarded_handler(self) -> None:
        with patch.object(core, "_confirm_relation", return_value={"ok": True}) as legacy:
            result = decide(
                CapabilityContext(principal()), RelationDecision(relation_id="r", status="confirmed", reason="原文一致")
            )
        legacy.assert_called_once_with("r", {"status": "confirmed", "reason": "原文一致"})
        self.assertEqual(result.data, {"ok": True})

    def test_semantic_describe_uses_authorized_result_and_citation(self) -> None:
        from types import SimpleNamespace

        data = {
            "entity": {"id": "E", "version": 3, "payload": {"definition": "已授权"}},
            "card": {"one_liner": "已授权"},
        }
        with patch("backend.capabilities.semantic.get_element", return_value=SimpleNamespace(data=data)) as get:
            result = describe(CapabilityContext(principal()), DescribeInput(entity_id="E"))
        get.assert_called_once()
        self.assertEqual(result.citation, "[[cite:el:E@3]]")
        self.assertIsNone(result.structure)
        with self.assertRaises(AssertionError):
            self.assertEqual("[[cite:el:E@2]]", result.citation)

    def test_semantic_describe_cannot_bypass_authorization(self) -> None:
        with (
            patch("backend.capabilities.semantic.get_element", side_effect=ApiError("NOT_FOUND")),
            self.assertRaises(ApiError),
        ):
            describe(CapabilityContext(principal()), DescribeInput(entity_id="private"))


@unittest.skipUnless(os.getenv("RUN_JOBS_MYSQL_TESTS") == "1", "opt-in MySQL integration test")
class RelationsMySQL(unittest.TestCase):
    setUpClass = classmethod(build_tests.BuildMySQL.setUpClass.__func__)
    tearDownClass = classmethod(build_tests.BuildMySQL.tearDownClass.__func__)
    login = build_tests.BuildMySQL.login

    def test_candidate_proposal_and_review_persist(self) -> None:
        from contextlib import closing

        from backend import db
        from backend.security import store
        from backend.security.context import use_context

        builder, reviewer = self.login("fixture.builder"), self.login("fixture.reviewer")
        ids = []
        for kind in ("rule", "data"):
            response = builder.post("/api/entities", json={"kind": kind, "title": "R4关联" + kind})
            self.assertEqual(response.status_code, 200, response.text)
            ids.append(response.json()["id"])
        response = builder.post(
            "/api/v2/capabilities/relation.propose:invoke",
            json={"source": ids[0], "target": ids[1], "type": "references", "reason": "对应原文口径"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        ident = response.json()["data"]["id"]
        person = store.authenticate_session(builder.cookies.get("ontology_session"), "r2-test")[0]
        with use_context(person), closing(db.connect()) as conn:
            before = conn.execute("SELECT status,payload FROM relations WHERE id=?", (ident,)).fetchone()
            self.assertEqual(before["status"], "candidate")
            self.assertEqual(json.loads(before["payload"])["proposal_reason"], "对应原文口径")
        denied = builder.post(
            "/api/v2/capabilities/relation.decide:invoke",
            json={"relation_id": ident, "status": "confirmed", "reason": "与原文一致"},
        )
        self.assertEqual(denied.status_code, 403, denied.text)
        accepted = reviewer.post(
            "/api/v2/capabilities/relation.decide:invoke",
            json={"relation_id": ident, "status": "confirmed", "reason": "与原文一致"},
        )
        self.assertEqual(accepted.status_code, 200, accepted.text)
        with use_context(person), closing(db.connect()) as conn:
            after = conn.execute("SELECT status,payload FROM relations WHERE id=?", (ident,)).fetchone()
            self.assertEqual(after["status"], "confirmed")
            self.assertEqual(json.loads(after["payload"])["review_history"][-1]["reason"], "与原文一致")
            self.assertEqual(conn.execute("SELECT version FROM entities WHERE id=?", (ids[0],)).fetchone()[0], 3)
        with self.assertRaises(AssertionError):
            self.assertEqual(before["status"], after["status"])
        other = self.login("r2.other")
        denied = other.post(
            "/api/v2/capabilities/relation.propose:invoke",
            json={"source": ids[0], "target": ids[1], "type": "references", "reason": "跨机构尝试"},
        )
        self.assertEqual(denied.status_code, 404, denied.text)

    def test_mysql_derived_relation_cannot_be_reviewed_independently(self) -> None:
        from contextlib import closing

        from backend import db
        from backend.security import store
        from backend.security.context import use_context

        builder, reviewer = self.login("fixture.builder"), self.login("fixture.reviewer")
        response = builder.post("/api/entities", json={"kind": "data", "title": "R4派生数据项"})
        self.assertEqual(response.status_code, 200, response.text)
        target = response.json()["id"]
        person = store.authenticate_session(builder.cookies.get("ontology_session"), "r2-test")[0]
        entity = {"id": "R4-OT", "kind": "object_type", "payload": {"properties": [{"data_id": target}]}}
        with use_context(person), closing(db.connect()) as conn, conn:
            conn.execute(
                "INSERT INTO entities(id,kind,title,summary,domain,org_id,status,source_kind,payload,"
                "created_at,updated_at,version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    entity["id"],
                    "object_type",
                    "R4业务对象",
                    "",
                    "",
                    "dept-01",
                    "draft",
                    "manual",
                    json.dumps(entity["payload"]),
                    db.now(),
                    db.now(),
                    1,
                ),
            )
            edge = sync_derived(conn, entity)[0]
            self.assertEqual(sync_derived(conn, entity)[0]["id"], edge["id"])
        response = reviewer.patch(
            "/api/relations/" + edge["id"], json={"status": "rejected", "reason": "独立变更派生关联"}
        )
        self.assertEqual(response.status_code, 400, response.text)
        with use_context(person), closing(db.connect()) as conn:
            row = conn.execute("SELECT status,payload FROM relations WHERE id=?", (edge["id"],)).fetchone()
            self.assertEqual(row["status"], "confirmed")
            self.assertIs(json.loads(row["payload"])["derived"], True)
