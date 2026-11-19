"""BA1 verifies institution provisioning and exact historical rows on MySQL."""

import copy
import hashlib
import json
import os
import shutil
import unittest
import zipfile
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from backend import db, system
from backend.api.v2.orgs import OrgManageInput
from backend.infra import backup, migrations, recovery_schema
from backend.maintenance import runtime_guard
from backend.security import directory, store
from backend.security.context import Principal
from scripts import restore_backup
from tests.fixtures.v070.load import FIXTURE
from tests.migrations import test_integration as foundation

LEGACY = ("entities", "relations", "versions", "activities")
ADDITIONS = {"ont_modules", "ont_module_deps", "ont_releases", "ont_release_items"}


def row_hashes(snapshot: dict, table: str) -> list[str]:
    return sorted(hashlib.sha256(backup.canonical(row).encode()).hexdigest() for row in snapshot["tables"][table])


@unittest.skipUnless(os.getenv("RUN_JOBS_MYSQL_TESTS") == "1", "opt-in MySQL integration test")
class InstitutionMigration(unittest.TestCase):
    setUp = foundation.MigrationMySQL.setUp
    tearDown = foundation.MigrationMySQL.tearDown
    fixture = foundation.MigrationMySQL.fixture

    def test_upgrade_preserves_all_four_legacy_tables_and_provisions_each_institution(self) -> None:
        names = self.fixture()
        before = {ident: backup.snapshot_database(name) for ident, name in names.items() if ident != "control"}
        migrations.run_migrations()
        for ident, old in before.items():
            with self.subTest(tenant=ident):
                current = backup.snapshot_database(names[ident])
                self.assertEqual(ADDITIONS & old["tables"].keys(), set())
                self.assertLessEqual(ADDITIONS, current["tables"].keys())
                self.assertEqual(len(current["columns"]["entities"]), 12)
                self.assertEqual(len(current["columns"]["relations"]), 7)
                for table in LEGACY:
                    self.assertEqual(len(current["tables"][table]), len(old["tables"][table]))
                    self.assertEqual(row_hashes(current, table), row_hashes(old, table))
                self.assertEqual(
                    [
                        (r["id"], r["module_key"], r["module_type"], r["name"], r["domain_code"])
                        for r in current["tables"]["ont_modules"]
                    ],
                    [("MOD-DEFAULT", "default", "org_common", store.get_tenant(ident)["name"] + "公共部分", "general")],
                )
        self.assertTrue(all(before["dept-01"]["tables"][table] for table in LEGACY))

    def upgrade(self) -> dict[str, str]:
        names = self.fixture()
        migrations.run_migrations()
        return names

    def principal(self, username: str = "fixture.admin") -> Principal:
        token = store.login(username, "SyntheticFixture9!", "127.0.0.1", "ba1")
        return store.authenticate_session(token, "ba1")[0]

    def clear_fixture_workspace(self, person: Principal) -> None:
        database = person.database_name
        self.assertTrue(database.startswith(self.namespace + "_t_"))
        self.assertEqual(database, store.get_tenant(person.tenant_id)["db_name"])
        with closing(db._raw_connection("information_schema")) as raw:
            db.Connection(raw).execute(f"DROP DATABASE `{database}`")
        shutil.rmtree(self.root / "tenants" / person.tenant_id)

    def restore_archive(self, path: Path, person: Principal) -> dict:
        with runtime_guard(shared=False), zipfile.ZipFile(path) as archive:
            snapshot = json.loads(archive.read(restore_backup.SNAPSHOT))
            return restore_backup.restore_snapshot(snapshot, archive, person, "核验机构恢复")

    def populate_storage_fixture(self, database: str) -> None:
        # These are storage-format fixtures, independent of the release authorization/lifecycle tests.
        # Use an actual 0.7 asset, a nonempty JSON snapshot and a scenario as the dependency target.
        with closing(db._raw_connection(database)) as raw:
            conn = db.Connection(raw)
            row = conn.execute(
                "SELECT e.id,e.kind,e.title,e.version FROM entities e JOIN entity_meta m ON m.entity_id=e.id "
                "WHERE m.pillar='asset' AND e.status='published' ORDER BY e.id LIMIT 1"
            ).fetchone()
            self.assertIsNotNone(row)
            snapshot = backup.canonical(dict(row))
            content_hash = hashlib.sha256(snapshot.encode()).hexdigest()
            manifest = hashlib.sha256(content_hash.encode()).hexdigest()
            when = db.now()
            conn.execute(
                "INSERT INTO ont_modules(id,module_key,module_type,name,domain_code,created_by,created_at,updated_at) "
                "VALUES('MOD-ca5e0001','scn_ca5e0001','scenario','工程结算','general','fixture',?,?)",
                (when, when),
            )
            conn.execute(
                "INSERT INTO ont_module_deps(module_id,depends_on_tenant,depends_on_module,min_version) "
                "VALUES('MOD-DEFAULT','dept-01','MOD-ca5e0001','1.0.0')"
            )
            conn.execute(
                "INSERT INTO ont_releases(id,module_id,version,status,bump,changelog,manifest_hash,stats,created_by,"
                "reviewed_by,created_at,published_at) VALUES('RLS-abc00001','MOD-DEFAULT','1.0.0','published',"
                "'major','首个版本',?,?,'fixture-builder','fixture-reviewer',?,?)",
                (manifest, '{"members":1}', when, when),
            )
            conn.execute(
                "INSERT INTO ont_release_items(release_id,entity_id,entity_version,kind,snapshot,content_hash) "
                "VALUES('RLS-abc00001',?,?,?,?,?)",
                (row["id"], row["version"], row["kind"], snapshot, content_hash),
            )
            conn.execute(
                "UPDATE ont_modules SET scope_note='工程结算范围',owner_user_id='fixture-owner',"
                "preferences=?,current_release_id='RLS-abc00001',updated_at=? WHERE id='MOD-DEFAULT'",
                ('{"language":"zh"}', when),
            )
            conn.commit()

    def test_new_directory_institution_is_ready_with_one_common_part_and_no_release(self) -> None:
        names = self.upgrade()
        principal = self.principal("fixture.superadmin")
        ident = directory.manage(principal, OrgManageInput(action="create", name="工程审计中心", category="subsidiary"))
        tenant = store.get_tenant(ident)
        self.assertEqual(tenant["status"], "active")
        self.assertNotIn(tenant["db_name"], names.values())
        snapshot = backup.snapshot_database(tenant["db_name"])
        (row,) = snapshot["tables"]["ont_modules"]
        self.assertEqual(
            (row["id"], row["name"], row["module_key"]), ("MOD-DEFAULT", "工程审计中心公共部分", "default")
        )
        self.assertIsNone(row["owner_user_id"])
        self.assertIsNone(row["current_release_id"])
        for table in ADDITIONS - {"ont_modules"}:
            self.assertEqual(snapshot["tables"][table], [])
        self.assertEqual(migrations.migration_status("tenant", tenant["db_name"])["current"], 5)
        migrations.provision_tenant(ident)
        self.assertEqual(backup.snapshot_database(tenant["db_name"])["hashes"], snapshot["hashes"])

    def test_renaming_institution_updates_only_its_common_part_and_preserves_user_work(self) -> None:
        names = self.upgrade()
        self.populate_storage_fixture(names["dept-01"])
        before = backup.snapshot_database(names["dept-01"])["tables"]["ont_modules"]
        other = backup.snapshot_database(names["hq"])["hashes"]["ont_modules"]
        principal = self.principal("fixture.superadmin")
        directory.manage(principal, OrgManageInput(action="update", org_id="dept-01", name="区域建设管理处"))
        after = backup.snapshot_database(names["dept-01"])["tables"]["ont_modules"]
        self.assertEqual(len(after), len(before))
        for saved in before:
            found = next(r for r in after if r["id"] == saved["id"])
            if saved["id"] == "MOD-DEFAULT":
                self.assertNotEqual(found["updated_at"], saved["updated_at"])
                self.assertEqual(found, {**saved, "name": "区域建设管理处公共部分", "updated_at": found["updated_at"]})
            else:
                self.assertEqual(found, saved)
        self.assertEqual(backup.snapshot_database(names["hq"])["hashes"]["ont_modules"], other)

    def test_rerun_and_direct_ddl_replay_preserve_history_and_edited_rows(self) -> None:
        database = self.upgrade()["dept-01"]
        self.populate_storage_fixture(database)
        before = backup.snapshot_database(database)
        with patch.object(backup, "create_upgrade_backup", side_effect=AssertionError("no-op must not back up")):
            self.assertEqual(migrations.run_migrations(), {"backup": None, "migrated": []})
        with closing(db._raw_connection(database)) as raw:
            conn = db.Connection(raw)
            migrations.discover("tenant")[4].up(conn)
            conn.commit()
        self.assertEqual(backup.snapshot_database(database)["hashes"], before["hashes"])
        self.assertTrue(all(before["hashes"][table]["rows"] > 0 for table in ADDITIONS))

    def test_all_legacy_restore_definitions_are_selected_from_real_continuous_history(self) -> None:
        names = self.fixture()
        sequence = self.root / "sequences"
        for kind, ident, registry_versions in (
            ("control", "control", (1, 2, 3, 4, 4, 7)),
            ("tenant", "dept-01", (1, 5, 6, 7, 8)),
        ):
            directory_path = sequence / kind
            directory_path.mkdir(parents=True)
            baseline = backup.snapshot_database(names[ident])
            self.assertEqual(recovery_schema.validate_schema(baseline, kind), recovery_schema.trusted_schema(kind, 1))
            for version, registry_version in enumerate(registry_versions, 1):
                (path,) = (migrations.ROOT / kind).glob(f"{version:04d}_*.py")
                shutil.copy2(path, directory_path / path.name)
                migrations.migrate_database(
                    kind, names[ident], tenant_id=ident if kind == "tenant" else None, root=sequence
                )
                snapshot = backup.snapshot_database(names[ident])
                with self.subTest(kind=kind, version=version):
                    self.assertEqual(
                        recovery_schema.validate_schema(snapshot, kind),
                        recovery_schema.trusted_schema(kind, registry_version),
                    )
                if kind == "tenant" and version < 5:
                    self.assertFalse(ADDITIONS & snapshot["tables"].keys())
                    snap = copy.deepcopy(snapshot)
                    v8 = recovery_schema.trusted_schema("tenant", 8)
                    snap["tables"]["ont_modules"] = []
                    for field in ("ddl", "columns"):
                        snap[field]["ont_modules"] = v8[field]["ont_modules"]
                    with self.assertRaises(ValueError):
                        recovery_schema.validate_schema(snap, kind)

    def test_v8_rejects_each_missing_table_altered_columns_ddl_and_forged_history(self) -> None:
        database = self.upgrade()["dept-01"]
        snapshot = backup.snapshot_database(database)
        self.assertEqual(
            recovery_schema.validate_schema(snapshot, "tenant"), recovery_schema.trusted_schema("tenant", 8)
        )
        for table in ADDITIONS:
            for mutation in ("table", "columns", "ddl"):
                bad = copy.deepcopy(snapshot)
                if mutation == "table":
                    for field in ("tables", "columns", "ddl"):
                        del bad[field][table]
                elif mutation == "columns":
                    bad["columns"][table] = bad["columns"][table][:-1]
                else:
                    bad["ddl"][table] = bad["ddl"][table].replace("varchar(64)", "varchar(63)", 1)
                with self.subTest(table=table, mutation=mutation), self.assertRaises(ValueError):
                    recovery_schema.validate_schema(bad, "tenant")
        for mutation in ("gap", "checksum", "name", "future", "v7"):
            bad = copy.deepcopy(snapshot)
            history = sorted(bad["tables"]["schema_migrations"], key=lambda r: r["version"])
            if mutation == "gap":
                history.pop(3)
            elif mutation == "checksum":
                history[-1]["checksum"] = "a" * 64
            elif mutation == "name":
                history[-1]["name"] = "0005_other"
            elif mutation == "future":
                history.append({**history[-1], "version": 6})
            else:
                history.pop()
            bad["tables"]["schema_migrations"] = history
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                recovery_schema.validate_schema(bad, "tenant")

    def test_schema_enforces_common_identity_and_dependency_release_item_uniqueness(self) -> None:
        database = self.upgrade()["dept-01"]
        self.populate_storage_fixture(database)
        with closing(db._raw_connection(database)) as raw:
            conn = db.Connection(raw)
            for sql in (
                "UPDATE ont_modules SET module_type='org_common' WHERE id='MOD-ca5e0001'",
                "UPDATE ont_modules SET domain_code='finance' WHERE id='MOD-DEFAULT'",
                "UPDATE ont_modules SET module_key='other' WHERE id='MOD-DEFAULT'",
                "UPDATE ont_modules SET module_key='default' WHERE id='MOD-ca5e0001'",
            ):
                with self.subTest(sql=sql), self.assertRaises(db.pymysql.MySQLError) as error:
                    conn.execute(sql)
                self.assertIn(error.exception.args[0], (1062, 3819))
            for table in ("ont_module_deps", "ont_releases", "ont_release_items"):
                columns = [r[0] for r in conn.execute(f"SHOW COLUMNS FROM {table}")]
                names = ",".join(columns)
                selection = names.replace("id,", "'RLS-abc00002' AS id,", 1) if table == "ont_releases" else names
                with self.subTest(table=table), self.assertRaises(db.pymysql.IntegrityError) as duplicate:
                    conn.execute(f"INSERT INTO {table}({names}) SELECT {selection} FROM {table}")
                self.assertEqual(duplicate.exception.args[0], 1062)
            conn.rollback()

    def test_full_v8_backup_restores_all_four_populated_tables_and_current_release(self) -> None:
        database = self.upgrade()["dept-01"]
        self.populate_storage_fixture(database)
        before = backup.snapshot_database(database)
        with runtime_guard(shared=False):
            archive = backup.create_upgrade_backup(store.list_tenants())
            _, checked = backup.verify_upgrade_backup(archive)
            for table in ADDITIONS:
                self.assertEqual(system.TABLE_CLASSIFICATION[table], "export")
                self.assertGreater(checked[database]["hashes"][table]["rows"], 0)
                self.assertEqual(checked[database]["hashes"][table], before["hashes"][table])
            with closing(db._raw_connection(database)) as raw:
                conn = db.Connection(raw)
                conn.execute("DELETE FROM ont_release_items WHERE release_id='RLS-abc00001'")
                conn.commit()
            self.assertNotEqual(
                backup.snapshot_database(database)["hashes"]["ont_release_items"], before["hashes"]["ont_release_items"]
            )
            backup.restore_upgrade_backup(archive)
        after = backup.snapshot_database(database)
        self.assertEqual(after["hashes"], before["hashes"])
        self.assertEqual(after["columns"], before["columns"])
        migrations.validate_runtime()

    def test_original_v070_portable_archive_restores_its_rows_originals_and_default_ownership(self) -> None:
        names = self.upgrade()
        principal = self.principal()
        self.clear_fixture_workspace(principal)
        path = FIXTURE / "workspace-v070.backup"
        result = self.restore_archive(path, principal)
        restored = backup.snapshot_database(names["dept-01"])
        with zipfile.ZipFile(path) as archive:
            original = json.loads(archive.read(restore_backup.SNAPSHOT))
            self.assertGreater(result["rows"], 100)
            self.assertGreater(result["files"], 0)
            for name in archive.namelist():
                if name.startswith("data/uploads/") and not name.endswith("/"):
                    self.assertEqual((self.root / "tenants" / "dept-01" / name).read_bytes(), archive.read(name))
            self.assertEqual(
                {r["id"] for r in restored["tables"]["entities"]}, {r["id"] for r in original["tables"]["entities"]}
            )
            # The established portable restore policy retains managed roots and puts skills back in draft.
            # Original history and non-skill assets survive that policy; full upgrade recovery is byte-exact.
            skills = {row["entity_id"] for row in original["tables"]["skill_documents"]}
            for table in ("versions", "activities"):
                self.assertLessEqual(set(row_hashes(original, table)), set(row_hashes(restored, table)))
            stable = {r["id"]: r for r in restored["tables"]["entities"]}
            for row in original["tables"]["entities"]:
                if row["id"] not in skills | {"dept-01"}:
                    self.assertEqual(stable[row["id"]], row)
            self.assertTrue(skills)
            self.assertTrue(all(stable[ident]["status"] == "draft" for ident in skills))
        (common,) = restored["tables"]["ont_modules"]
        self.assertEqual(
            (common["id"], common["name"]), ("MOD-DEFAULT", store.get_tenant("dept-01")["name"] + "公共部分")
        )
        self.assertEqual(
            {r["module_id"] for r in restored["tables"]["entity_meta"] if r["pillar"] == "asset"}, {"MOD-DEFAULT"}
        )
        self.assertEqual(restore_backup.schema_columns()["entities"], frozenset(restored["columns"]["entities"]))
        self.assertEqual(migrations.run_migrations()["migrated"], [])

    def test_populated_portable_v8_restores_frozen_rows_and_editable_common_metadata(self) -> None:
        names = self.upgrade()
        self.populate_storage_fixture(names["dept-01"])
        before = backup.snapshot_database(names["dept-01"])
        path = self.root / "institution.backup"
        with closing(db._raw_connection(names["dept-01"])) as raw:
            system.export_workspace_backup(db.Connection(raw), "dept-01", self.root / "tenants" / "dept-01", path)
        principal = self.principal()
        with self.assertRaises(restore_backup.RestoreError):
            self.restore_archive(path, principal)
        self.clear_fixture_workspace(principal)
        result = self.restore_archive(path, principal)
        self.assertGreater(result["files"], 0)
        after = backup.snapshot_database(names["dept-01"])
        for table in ADDITIONS:
            self.assertGreater(before["hashes"][table]["rows"], 0)
            self.assertEqual(after["tables"][table], before["tables"][table])
            self.assertEqual(after["hashes"][table], before["hashes"][table])

    def test_modified_seed_blocks_offline_restore_even_with_no_other_business_data(self) -> None:
        self.upgrade()
        principal = self.principal()
        self.clear_fixture_workspace(principal)
        migrations.provision_tenant("dept-01")
        with closing(db._raw_connection(principal.database_name)) as raw:
            conn = db.Connection(raw)
            conn.execute("UPDATE ont_modules SET scope_note='保留人工维护的范围' WHERE id='MOD-DEFAULT'")
            conn.commit()
        before = backup.snapshot_database(principal.database_name)["hashes"]
        with self.assertRaisesRegex(restore_backup.RestoreError, "公共部分"):
            self.restore_archive(FIXTURE / "workspace-v070.backup", principal)
        self.assertEqual(backup.snapshot_database(principal.database_name)["hashes"], before)

    def test_portable_v8_rejects_partial_ontology_tables_and_missing_or_duplicate_common(self) -> None:
        names = self.upgrade()
        assembled = self.root / "workspace.json"
        with closing(db._raw_connection(names["dept-01"])) as raw:
            system.export_snapshot(db.Connection(raw), assembled, "dept-01")
        original = json.loads(assembled.read_text())
        restore_backup.validate_snapshot(original, "dept-01")
        for table in ADDITIONS:
            bad = copy.deepcopy(original)
            del bad["tables"][table]
            with self.subTest(missing=table), self.assertRaises(restore_backup.RestoreError):
                restore_backup.validate_snapshot(bad, "dept-01")
        for mutation in ("missing", "duplicate", "key", "type", "id", "domain"):
            bad = copy.deepcopy(original)
            common = bad["tables"]["ont_modules"][0]
            if mutation == "missing":
                bad["tables"]["ont_modules"].clear()
            elif mutation == "duplicate":
                bad["tables"]["ont_modules"].append({**common, "id": "MOD-FAKE"})
            else:
                common[{"key": "module_key", "type": "module_type", "domain": "domain_code", "id": "id"}[mutation]] = (
                    "other"
                )
            with self.subTest(mutation=mutation), self.assertRaises(restore_backup.RestoreError):
                restore_backup.validate_snapshot(bad, "dept-01")
