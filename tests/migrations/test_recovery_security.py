"""Recomputed-checksum hostile backups never execute SQL or escape storage."""

from __future__ import annotations

import hashlib
import json
import shutil
import unittest
import zipfile
from collections.abc import Callable
from contextlib import closing

from backend import db
from backend.infra import backup
from backend.maintenance import runtime_guard
from backend.security import store
from tests.fixtures.v070.load import FIXTURE, load_fixture
from tests.migrations import test_integration as integration


@unittest.skipUnless(integration.os.environ.get("RUN_JOBS_MYSQL_TESTS") == "1", "opt-in MySQL integration test")
class RecoverySecurity(unittest.TestCase):
    def setUp(self) -> None:
        integration.MigrationMySQL.setUp(self)
        self.names = load_fixture()
        self.sentinel = self.namespace[:32] + "_t_" + "f" * 24
        with closing(db._raw_connection(self.sentinel, create=True)) as raw:
            conn = db.Connection(raw)
            conn.execute("CREATE TABLE sentinel (id INT PRIMARY KEY, content VARCHAR(100))")
            conn.execute("INSERT INTO sentinel(id,content) VALUES(1,'outside directory must survive')")
            conn.commit()
        with runtime_guard(shared=False):
            self.archive = backup.create_upgrade_backup(store.list_tenants())
        self.before = self.fingerprints()

    def tearDown(self) -> None:
        integration.MigrationMySQL.tearDown(self)

    def fingerprints(self) -> dict:
        return {name: backup.snapshot_database(name)["hashes"] for name in [*self.names.values(), self.sentinel]}

    def rewrite_manifest(self, operation: Callable[[dict], None] | None = None) -> None:
        path = self.archive / "manifest.json"
        value = json.loads(path.read_text())
        if operation:
            operation(value)
        for name in value["files"]:
            value["files"][name] = backup.digest_file(self.archive / name)
        path.write_text(backup.canonical(value))

    def rewrite_snapshot(self, filename: str, operation: Callable[[dict], None]) -> None:
        path = self.archive / filename
        value = json.loads(path.read_text())
        operation(value)
        for table, rows in value["tables"].items():
            rows.sort(key=backup.canonical)
            value["hashes"][table] = {
                "rows": len(rows),
                "sha256": hashlib.sha256(backup.canonical(rows).encode()).hexdigest(),
            }
        path.write_text(backup.canonical(value))
        self.rewrite_manifest()

    def refused_without_changes(self) -> None:
        uploads = self.root / "tenants" / "dept-01" / "data" / "uploads"
        originals = {
            file.relative_to(uploads).as_posix(): file.read_bytes() for file in uploads.rglob("*") if file.is_file()
        }
        with runtime_guard(shared=False), self.assertRaises(backup.BackupError):
            backup.restore_upgrade_backup(self.archive)
        self.assertEqual(self.before, self.fingerprints())
        self.assertEqual(
            originals,
            {file.relative_to(uploads).as_posix(): file.read_bytes() for file in uploads.rglob("*") if file.is_file()},
        )

    def test_recomputed_trigger_sql_cannot_drop_outside_database(self) -> None:
        self.rewrite_snapshot(
            "control.json",
            lambda value: value["triggers"].update(sec_audit_no_update=f"DROP DATABASE `{self.sentinel}`"),
        )
        self.refused_without_changes()

    def test_recomputed_ddl_sql_is_rejected(self) -> None:
        self.rewrite_snapshot(
            "dept-01.json",
            lambda value: value["ddl"].update(entities=value["ddl"]["entities"] + f"; DROP DATABASE `{self.sentinel}`"),
        )
        self.refused_without_changes()

    def test_missing_audit_trigger_is_rejected(self) -> None:
        self.rewrite_snapshot("control.json", lambda value: value["triggers"].pop("sec_audit_no_delete"))
        self.refused_without_changes()

    def test_missing_required_tenant_archive_is_rejected(self) -> None:
        (self.archive / "dept-01.backup").unlink()
        self.rewrite_manifest(lambda value: value["files"].pop("dept-01.backup"))
        self.refused_without_changes()

    def test_same_prefix_directory_redirection_is_rejected(self) -> None:
        def redirect(value: dict) -> None:
            for tenant in value["tables"]["sec_tenants"]:
                if tenant["id"] == "dept-01":
                    tenant["db_name"] = self.sentinel

        self.rewrite_snapshot("control.json", redirect)
        self.rewrite_snapshot("dept-01.json", lambda value: value.update(database=self.sentinel))
        self.rewrite_manifest(
            lambda value: next(entry for entry in value["databases"] if entry["tenant_id"] == "dept-01").update(
                database=self.sentinel
            )
        )
        self.refused_without_changes()

    def test_duplicate_directory_entries_are_rejected(self) -> None:
        self.rewrite_manifest(lambda value: value["databases"].append(dict(value["databases"][0])))
        self.refused_without_changes()

    def test_wrong_portable_source_tenant_is_rejected(self) -> None:
        path = self.archive / "dept-01.backup"
        with zipfile.ZipFile(path) as archive:
            members = {name: archive.read(name) for name in archive.namelist()}
        value = json.loads(members["data/mysql-workspace.json"])
        value["source_tenant"] = "sub-01"
        members["data/mysql-workspace.json"] = backup.canonical(value).encode()
        with zipfile.ZipFile(path, "w") as archive:
            for name, content in members.items():
                archive.writestr(name, content)
        self.rewrite_manifest()
        self.refused_without_changes()

    def test_upload_parent_symlink_is_rejected_before_database_changes(self) -> None:
        data = self.root / "tenants" / "dept-01" / "data"
        actual = data.with_name("original-data")
        data.rename(actual)
        outside = self.root / "outside"
        (outside / "uploads").mkdir(parents=True)
        protected = outside / "uploads" / "sentinel.txt"
        protected.write_text("outside originals must survive")
        data.symlink_to(outside, target_is_directory=True)
        try:
            with runtime_guard(shared=False), self.assertRaises(backup.BackupError):
                backup.restore_upgrade_backup(self.archive)
            self.assertEqual(protected.read_text(), "outside originals must survive")
            self.assertEqual(self.before, self.fingerprints())
        finally:
            data.unlink()
            actual.rename(data)

    def test_fixture_loader_rejects_recomputed_trigger_sql(self) -> None:
        modified = self.root / "fixture"
        shutil.copytree(FIXTURE, modified)
        path = modified / "control.json"
        value = json.loads(path.read_text())
        value["triggers"]["sec_audit_no_update"] = f"DROP DATABASE `{self.sentinel}`"
        path.write_text(backup.canonical(value))
        manifest_path = modified / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["files"]["control.json"] = backup.digest_file(path)
        manifest_path.write_text(backup.canonical(manifest))
        with self.assertRaises(ValueError):
            load_fixture(modified)
        self.assertEqual(self.before, self.fingerprints())
