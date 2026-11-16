"""Real MySQL losslessness, serial execution, backup and failure recovery."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import tempfile
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from backend import db
from backend.infra import backup, migrations
from backend.maintenance import runtime_guard
from backend.security import store
from tests.fixtures.v070.load import load_fixture


@unittest.skipUnless(os.environ.get("RUN_JOBS_MYSQL_TESTS") == "1", "opt-in MySQL integration test")
class MigrationMySQL(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.namespace = "ontology_r12test_" + secrets.token_hex(5)
        self.env = patch.dict(
            os.environ,
            {
                "MYSQL_DATABASE": self.namespace,
                "ONTOLOGY_STORAGE_ROOT": str(self.root / "tenants"),
                "ONTOLOGY_BACKUP_DIR": str(self.root / "backups"),
            },
        )
        self.env.start()
        self.url = os.environ.pop("ONTOLOGY_MYSQL_URL", None)

    def tearDown(self) -> None:
        with closing(db._raw_connection("information_schema")) as raw:
            conn = db.Connection(raw)
            names = [
                r[0]
                for r in conn.execute(
                    "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE LEFT(SCHEMA_NAME,?)=?",
                    (len(self.namespace), self.namespace),
                )
            ]
            for name in names:
                self.assertTrue(name.startswith(self.namespace))
                conn.execute(f"DROP DATABASE `{name}`")
        self.env.stop()
        if self.url is not None:
            os.environ["ONTOLOGY_MYSQL_URL"] = self.url
        self.temp.cleanup()

    def fixture(self) -> dict[str, str]:
        return load_fixture()

    def hashes(self, names: object) -> dict:
        return {name: backup.snapshot_database(name)["hashes"] for name in names}

    def test_baseline_preserves_every_old_row_and_column(self) -> None:
        databases = self.fixture()
        before = self.hashes(databases.values())
        migrations.run_migrations()
        for name in databases.values():
            after = backup.snapshot_database(name)
            self.assertIn("schema_migrations", after["tables"])
            for table, digest in before[name].items():
                self.assertEqual(after["hashes"][table], digest, (name, table))
            if name != databases["control"]:
                self.assertEqual(len(after["columns"]["entities"]), 12)
                self.assertEqual(len(after["columns"]["relations"]), 7)

    def test_second_execution_has_no_change_or_backup(self) -> None:
        names = self.fixture()
        migrations.run_migrations()
        before = self.hashes(names.values())
        with patch.object(backup, "create_upgrade_backup", side_effect=AssertionError("No backup on no-op")):
            self.assertEqual(migrations.run_migrations()["migrated"], [])
        self.assertEqual(before, self.hashes(names.values()))

    def test_backup_failure_prevents_all_schema_changes(self) -> None:
        names = self.fixture()
        before = self.hashes(names.values())
        with (
            patch.object(backup, "create_upgrade_backup", side_effect=OSError("synthetic failure")),
            self.assertRaises(OSError),
        ):
            migrations.run_migrations()
        self.assertEqual(before, self.hashes(names.values()))

    def test_backup_recovery_restores_all_old_tables(self) -> None:
        names = self.fixture()
        before = self.hashes(names.values())
        result = migrations.run_migrations()
        with runtime_guard(shared=False):
            backup.restore_upgrade_backup(result["backup"])
        self.assertEqual(before, self.hashes(names.values()))
        self.assertEqual(migrations.read_state()["status"], "restored")

    def test_backup_keeps_latest_three(self) -> None:
        self.fixture()
        with runtime_guard(shared=False):
            paths = [backup.create_upgrade_backup(store.list_tenants()) for _ in range(4)]
        self.assertFalse(paths[0].exists())
        self.assertTrue(all(path.exists() for path in paths[1:]))

    def test_tampering_rejected_before_recovery(self) -> None:
        names = self.fixture()
        result = migrations.run_migrations()
        before = self.hashes(names.values())
        (Path(result["backup"]) / "control.json").write_text("{}")
        with runtime_guard(shared=False), self.assertRaises(backup.BackupError):
            backup.restore_upgrade_backup(result["backup"])
        self.assertEqual(before, self.hashes(names.values()))

    def test_new_tenant_gets_all_migrations(self) -> None:
        self.fixture()
        migrations.run_migrations()
        tenant = store.get_tenant("dept-02")
        self.assertEqual(migrations.table_names(tenant["db_name"]), [])
        migrations.provision_tenant("dept-02")
        self.assertEqual(migrations.migration_status("tenant", tenant["db_name"])["current"], 1)
        self.assertIn("demo_installations", migrations.table_names(tenant["db_name"]))

    def test_existing_legacy_tenant_cannot_upgrade_online(self) -> None:
        self.fixture()
        with self.assertRaises(migrations.MigrationError):
            migrations.provision_tenant("dept-01")

    def test_current_runtime_gate_performs_no_ddl(self) -> None:
        names = self.fixture()
        migrations.run_migrations()
        before = self.hashes(names.values())
        migrations.validate_runtime()
        self.assertEqual(before, self.hashes(names.values()))

    def test_failure_stops_before_later_tenant_and_blocks_restart(self) -> None:
        names = self.fixture()
        tenants = [t for t in store.list_tenants() if t["id"] in {"hq", "dept-01", "sub-01"}]
        target = tenants[1]["db_name"]
        later = tenants[2]["db_name"]
        before = backup.snapshot_database(later)["hashes"]
        original = migrations.migrate_database

        def fail(kind: str, database: str, **kwargs: object) -> list[str]:
            if database == target:
                raise RuntimeError("synthetic tenant failure")
            return original(kind, database, **kwargs)

        with patch.object(migrations, "migrate_database", side_effect=fail), self.assertRaises(RuntimeError):
            migrations.run_migrations()
        self.assertEqual(before, backup.snapshot_database(later)["hashes"])
        with self.assertRaises(migrations.MigrationError):
            migrations.validate_runtime()
        state = migrations.read_state()
        self.assertEqual(state["status"], "failed")
        with self.assertRaises(migrations.MigrationError):
            migrations.run_migrations()
        with runtime_guard(shared=False):
            backup.restore_upgrade_backup(state["backup"])
        migrations.run_migrations()
        migrations.validate_runtime()
        self.assertTrue(names)

    def test_fresh_install_control_and_lazy_tenant(self) -> None:
        with patch.object(
            backup, "create_upgrade_backup", side_effect=AssertionError("Fresh install must not back up")
        ):
            migrations.run_migrations()
        migrations.provision_tenant("hq")
        migrations.validate_runtime()
        self.assertEqual(migrations.migration_status("control", store._config()["database"])["current"], 1)

    def test_interrupted_fresh_install_resumes_offline(self) -> None:
        with (
            patch.object(migrations, "migrate_database", side_effect=RuntimeError("first-install interruption")),
            self.assertRaises(RuntimeError),
        ):
            migrations.run_migrations()
        self.assertTrue(migrations.read_state()["fresh_install"])
        with self.assertRaises(migrations.MigrationError):
            migrations.validate_runtime()
        migrations.run_migrations()
        migrations.validate_runtime()
        self.assertIsNone(migrations.read_state())

    def test_missing_control_catalog_cannot_reclassify_existing_tenants_as_fresh(self) -> None:
        name = self.namespace[:32] + "_t_" + "e" * 24
        with closing(db._raw_connection(name, create=True)) as raw:
            conn = db.Connection(raw)
            conn.execute("CREATE TABLE preserved(id INT PRIMARY KEY)")
            conn.execute("INSERT INTO preserved(id) VALUES(1)")
            conn.commit()
        before = backup.snapshot_database(name)["hashes"]
        with self.assertRaises(migrations.MigrationError):
            migrations.run_migrations()
        self.assertEqual(before, backup.snapshot_database(name)["hashes"])
        self.assertEqual(migrations.table_names(store._config()["database"]), [])

    def test_fresh_and_upgraded_tenant_schemas_match(self) -> None:
        names = self.fixture()
        migrations.run_migrations()
        migrations.provision_tenant("dept-02")

        def structure(database: str) -> dict:
            with closing(db._raw_connection(database)) as raw:
                conn = db.Connection(raw)
                return {
                    "columns": [
                        dict(row)
                        for row in conn.execute(
                            "SELECT TABLE_NAME,COLUMN_NAME,ORDINAL_POSITION,COLUMN_DEFAULT,IS_NULLABLE,"
                            "COLUMN_TYPE,CHARACTER_SET_NAME,COLLATION_NAME,EXTRA,GENERATION_EXPRESSION "
                            "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
                            "ORDER BY TABLE_NAME,ORDINAL_POSITION"
                        )
                    ],
                    "indexes": [
                        dict(row)
                        for row in conn.execute(
                            "SELECT TABLE_NAME,INDEX_NAME,NON_UNIQUE,SEQ_IN_INDEX,COLUMN_NAME,COLLATION,SUB_PART,"
                            "NULLABLE,INDEX_TYPE,IS_VISIBLE,EXPRESSION FROM information_schema.STATISTICS "
                            "WHERE TABLE_SCHEMA=DATABASE() ORDER BY TABLE_NAME,INDEX_NAME,SEQ_IN_INDEX"
                        )
                    ],
                    "constraints": [
                        dict(row)
                        for row in conn.execute(
                            "SELECT TABLE_NAME,CONSTRAINT_NAME,COLUMN_NAME,ORDINAL_POSITION,REFERENCED_TABLE_NAME,"
                            "REFERENCED_COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE "
                            "WHERE TABLE_SCHEMA=DATABASE() ORDER BY TABLE_NAME,CONSTRAINT_NAME,ORDINAL_POSITION"
                        )
                    ],
                }

        self.assertEqual(structure(names["dept-01"]), structure(store.get_tenant("dept-02")["db_name"]))

    def test_original_v070_demo_backup_restores(self) -> None:
        from scripts.restore_backup import restore_snapshot

        names = self.fixture()
        migrations.run_migrations()
        raw_token = store.login("fixture.admin", "SyntheticFixture9!", "127.0.0.1", "fixture-restore")
        principal = store.authenticate_session(raw_token, "restore")[0]
        with closing(db._raw_connection("information_schema")) as raw:
            db.Connection(raw).execute(f"DROP DATABASE `{names['dept-01']}`")
        shutil.rmtree(self.root / "tenants" / "dept-01")
        fixture = Path(__file__).resolve().parents[1] / "fixtures" / "v070" / "workspace-v070.backup"
        with runtime_guard(shared=False), zipfile.ZipFile(fixture) as archive:
            snapshot = json.loads(archive.read("data/mysql-workspace.json"))
            result = restore_snapshot(snapshot, archive, principal, "合成旧版本恢复演练")
        self.assertGreater(result["rows"], 20)
        value = backup.snapshot_database(names["dept-01"])
        self.assertEqual(value["tables"]["demo_installations"], snapshot["tables"]["demo_installations"])
        self.assertIn("schema_migrations", value["tables"])

    def test_exports_match_original_tables_and_omit_migrations(self) -> None:
        from backend.system import export_snapshot

        names = self.fixture()
        before = backup.snapshot_database(names["dept-01"])["tables"]
        migrations.run_migrations()
        target = self.root / "export.json"
        with closing(db._raw_connection(names["dept-01"])) as raw:
            export_snapshot(db.Connection(raw), target, "dept-01")
        after = json.loads(target.read_text())["tables"]
        self.assertNotIn("schema_migrations", after)
        for table, rows in before.items():
            self.assertEqual(sorted(rows, key=backup.canonical), sorted(after[table], key=backup.canonical))

    def test_shared_runtime_lock_prevents_offline_upgrade(self) -> None:
        with runtime_guard(shared=True), self.assertRaises(RuntimeError):
            migrations.run_migrations()

    def test_backup_progress_never_regresses(self) -> None:
        self.fixture()
        progress = []
        migrations.run_migrations(progress=lambda *args: progress.append(args))
        self.assertGreater(sum(phase == "backup" for phase, _, _ in progress), 4)
        self.assertEqual({total for _, _, total in progress}, {10000})
        completed = [count for _, count, _ in progress]
        self.assertEqual(completed, sorted(completed))

    def test_concurrent_executors_apply_once(self) -> None:
        root = self.root / "sequence"
        (root / "tenant").mkdir(parents=True)
        (root / "tenant" / "0001_once.py").write_text(
            "def up(conn):\n    conn.execute('CREATE TABLE test_once (id INT PRIMARY KEY)')\n"
            "    conn.execute('INSERT INTO test_once(id) VALUES(1)')\n"
        )
        name = self.namespace + "_concurrent"
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: migrations.migrate_database("tenant", name, root=root), range(2)))
        self.assertEqual(sorted(len(result) for result in results), [0, 1])

    def test_failed_migration_stops_at_exact_version(self) -> None:
        root = self.root / "sequence"
        (root / "tenant").mkdir(parents=True)
        (root / "tenant" / "0001_first.py").write_text("def up(conn):\n    pass\n")
        (root / "tenant" / "0002_failure.py").write_text("def up(conn):\n    raise RuntimeError('synthetic')\n")
        (root / "tenant" / "0003_untouched.py").write_text(
            "def up(conn):\n    conn.execute('CREATE TABLE untouched(id INT)')\n"
        )
        name = self.namespace + "_failed"
        with self.assertRaises(RuntimeError):
            migrations.migrate_database("tenant", name, root=root)
        status = migrations.migration_status("tenant", name, root)
        self.assertEqual(status["current"], 1)
        self.assertNotIn("untouched", status["tables"])
