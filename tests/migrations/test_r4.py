"""R4-A1 migration results and deliberately failing structural counterexamples."""

import copy
import os
import unittest
from contextlib import closing
from unittest.mock import patch

from backend import db
from backend.infra import backup, migrations, recovery_schema
from backend.kernel.metadata import backfill_meta, defaults, get_meta, sync_meta
from backend.system import TABLE_CLASSIFICATION
from tests.migrations import test_integration as foundation


@unittest.skipUnless(os.getenv("RUN_JOBS_MYSQL_TESTS") == "1", "opt-in MySQL integration test")
class R4Migrations(unittest.TestCase):
    setUp = foundation.MigrationMySQL.setUp
    tearDown = foundation.MigrationMySQL.tearDown

    def upgrade(self) -> dict:
        names = foundation.MigrationMySQL.fixture(self)
        migrations.run_migrations()
        return names

    def test_legacy_rows_and_columns_unchanged(self) -> None:
        names = foundation.MigrationMySQL.fixture(self)
        before = backup.snapshot_database(names["dept-01"])
        migrations.run_migrations()
        after = backup.snapshot_database(names["dept-01"])
        for name in ("entities", "relations", "versions", "activities"):
            self.assertEqual(after["tables"][name], before["tables"][name])
            self.assertEqual(after["hashes"][name], before["hashes"][name])
        self.assertEqual(len(after["columns"]["entities"]), 12)
        self.assertEqual(len(after["columns"]["relations"]), 7)
        broken = copy.deepcopy(after)
        broken["tables"]["entities"][0]["title"] = "counterexample"
        with self.assertRaises(AssertionError):
            self.assertEqual(broken["tables"]["entities"], before["tables"]["entities"])
        broken["columns"]["entities"].append("unapproved")
        with self.assertRaises(AssertionError):
            self.assertEqual(len(broken["columns"]["entities"]), 12)

    def test_backfill_exactly_one_row_for_every_legacy_entity(self) -> None:
        names = self.upgrade()
        for scope in ("hq", "dept-01", "sub-01"):
            snap = backup.snapshot_database(names[scope])
            ids = [r["id"] for r in snap["tables"]["entities"]]
            meta = [r["entity_id"] for r in snap["tables"]["entity_meta"]]
            self.assertEqual(sorted(meta), sorted(ids))
            self.assertEqual(len(meta), len(set(meta)))
            with self.assertRaises(AssertionError):
                self.assertEqual(sorted(meta[:-1]), sorted(ids))

    def test_rerun_changes_neither_business_nor_metadata(self) -> None:
        names = self.upgrade()
        before = backup.snapshot_database(names["dept-01"])
        self.assertEqual(migrations.run_migrations()["migrated"], [])
        with closing(db._raw_connection(names["dept-01"])) as raw:
            conn = db.Connection(raw)
            self.assertEqual(backfill_meta(conn), 0)
            conn.commit()
        self.assertEqual(before["hashes"], backup.snapshot_database(names["dept-01"])["hashes"])

    def test_legacy_material_rule_and_project_classification(self) -> None:
        names = self.upgrade()
        cases = [
            ("material", {"source_kind": "regulation"}, "source", "regulation"),
            ("material", {"record_type": "experience_session"}, "source", "interview"),
            ("material", {}, "source", "other"),
            ("rule", {"authority_spec": {}}, "asset", "authority"),
            ("rule", {}, "asset", "general"),
            ("project", {"project_mode": "special_review"}, "event", "special"),
            ("project", {}, "event", "scenario"),
        ]
        with closing(db._raw_connection(names["dept-01"])) as raw:
            conn = db.Connection(raw)
            for index, (kind, payload, pillar, subtype) in enumerate(cases):
                entity = {"id": f"R4-CLASS-{index}", "kind": kind, "payload": payload}
                sync_meta(conn, entity)
                meta = get_meta(conn, entity["id"])
                self.assertEqual((meta["pillar"], meta["subtype"]), (pillar, subtype))
                self.assertEqual(meta["module_id"], "MOD-DEFAULT" if pillar == "asset" else None)
            conn.rollback()

    def test_dates_and_sensitivity_preserve_safe_defaults(self) -> None:
        names = self.upgrade()
        with closing(db._raw_connection(names["dept-01"])) as raw:
            conn = db.Connection(raw)
            entity = {
                "id": "R4-DATES",
                "kind": "rule",
                "payload": {"生效日期": "2026-01-01", "失效日期": "invalid", "敏感级别": "敏感"},
            }
            sync_meta(conn, entity)
            actual = get_meta(conn, entity["id"])
            self.assertEqual(actual["valid_from"], "2026-01-01")
            self.assertIsNone(actual["valid_to"])
            self.assertEqual(actual["sensitivity"], "sensitive")
            self.assertEqual(defaults({**entity, "payload": {"敏感级别": "unrecognized"}})["sensitivity"], "internal")
            conn.rollback()

    def test_repair_missing_meta_preserves_user_maintained_rows(self) -> None:
        names = self.upgrade()
        with closing(db._raw_connection(names["dept-01"])) as raw:
            conn = db.Connection(raw)
            entities = [dict(row) for row in conn.execute("SELECT * FROM entities ORDER BY id LIMIT 2")]
            sync_meta(conn, entities[0], {"provider_note": "人工补充", "sensitivity": "restricted"})
            saved = get_meta(conn, entities[0]["id"])
            conn.execute("DELETE FROM entity_meta WHERE entity_id=?", (entities[1]["id"],))
            self.assertEqual(backfill_meta(conn), 1)
            self.assertEqual(get_meta(conn, entities[0]["id"]), saved)
            self.assertIsNotNone(get_meta(conn, entities[1]["id"]))
            conn.rollback()

    def test_duplicate_module_key_rejected_without_business_rewrite(self) -> None:
        names = self.upgrade()
        with closing(db._raw_connection(names["dept-01"])) as raw:
            conn = db.Connection(raw)
            sync_meta(conn, {"id": "R4-A", "kind": "rule", "payload": {}}, {"element_key": "payment"})
            with self.assertRaises(db.pymysql.IntegrityError):
                sync_meta(conn, {"id": "R4-B", "kind": "rule", "payload": {}}, {"element_key": "payment"})
            self.assertIsNone(get_meta(conn, "R4-B"))
            conn.rollback()

    def test_tenant_meta_export_and_trusted_restore(self) -> None:
        names = self.upgrade()
        snap = backup.snapshot_database(names["dept-01"])
        self.assertEqual(TABLE_CLASSIFICATION["entity_meta"], "export")
        recovery_schema.validate_schema(snap, "tenant")
        self.assertNotIn("group_share", snap["columns"]["entity_meta"])
        bad = copy.deepcopy(snap)
        bad["ddl"]["entity_meta"] = bad["ddl"]["entity_meta"].replace("varchar(16)", "varchar(17)", 1)
        with self.assertRaises(ValueError):
            recovery_schema.validate_schema(bad, "tenant")

    def test_control_schedule_restores_exactly(self) -> None:
        names = self.upgrade()
        snap = backup.snapshot_database(names["control"])
        recovery_schema.validate_schema(snap, "control")
        self.assertEqual(
            snap["columns"]["job_schedule_state"],
            ["job_kind", "tenant_id", "next_due_at", "last_enqueued_at", "last_job_id", "last_status"],
        )
        bad = copy.deepcopy(snap)
        for key in ("tables", "columns", "ddl"):
            bad[key].pop("job_schedule_state")
        with self.assertRaises(ValueError):
            recovery_schema.validate_schema(bad, "control")

    def test_previous_restore_versions_remain_exact_and_reject_new_table(self) -> None:
        names = foundation.MigrationMySQL.fixture(self)
        before = backup.snapshot_database(names["dept-01"])
        recovery_schema.validate_schema(before, "tenant")
        migrations.run_migrations()
        after = backup.snapshot_database(names["dept-01"])
        for key in ("tables", "columns", "ddl"):
            before[key]["entity_meta"] = after[key]["entity_meta"]
        with self.assertRaises(ValueError):
            recovery_schema.validate_schema(before, "tenant")

    def test_unknown_or_partial_migration_history_cannot_select_new_schema(self) -> None:
        names = self.upgrade()
        snap = backup.snapshot_database(names["dept-01"])
        for alter in (lambda rows: rows.pop(1), lambda rows: rows[-1].update(checksum="0" * 64)):
            bad = copy.deepcopy(snap)
            alter(bad["tables"]["schema_migrations"])
            with self.assertRaises(ValueError):
                recovery_schema.validate_schema(bad, "tenant")

    def test_interrupted_backfill_rolls_back_and_is_repeatable(self) -> None:
        names = self.upgrade()
        with closing(db._raw_connection(names["dept-01"])) as raw:
            conn = db.Connection(raw)
            count = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
            conn.execute("DELETE FROM entity_meta")
            conn.commit()
            original = conn.execute
            writes = 0

            def fail_after_first(sql: str, params: object = ()) -> db.Cursor:
                nonlocal writes
                if sql.startswith("INSERT IGNORE INTO entity_meta"):
                    writes += 1
                    if writes == 2:
                        raise RuntimeError("synthetic interruption")
                return original(sql, params)

            with patch.object(conn, "execute", side_effect=fail_after_first), self.assertRaises(RuntimeError):
                backfill_meta(conn)
            conn.rollback()
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM entity_meta").fetchone()[0], 0)
            self.assertEqual(backfill_meta(conn), count)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM entity_meta").fetchone()[0], count)
            conn.commit()
