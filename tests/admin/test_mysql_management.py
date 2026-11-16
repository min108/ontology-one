"""Actual configuration/export/offline restore through one isolated historical deployment."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import tempfile
import unittest
import zipfile
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import db, main
from backend.capabilities.context import CapabilityContext
from backend.capabilities.registry import registry
from backend.infra import migrations
from backend.maintenance import runtime_guard
from backend.security import store
from backend.security.permissions import permissions_for
from scripts.restore_backup import RestoreError
from tests.fixtures.v070.load import load_fixture


@unittest.skipUnless(os.getenv("RUN_JOBS_MYSQL_TESTS") == "1", "opt-in MySQL integration test")
class ManagementMySQL(unittest.TestCase):
    def test_real_configuration_archive_and_exclusive_empty_workspace_restore(self) -> None:
        namespace = "ontology_r18_" + secrets.token_hex(6)
        Path("work").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir="work") as directory:
            root = Path(directory).absolute()
            with patch.dict(
                os.environ,
                {
                    "MYSQL_DATABASE": namespace,
                    "ONTOLOGY_STORAGE_ROOT": str(root / "tenants"),
                    "ONTOLOGY_BACKUP_DIR": str(root / "backups"),
                },
            ):
                names = load_fixture()
                try:
                    migrations.run_migrations()
                    client = TestClient(main.app)
                    login = client.post(
                        "/api/auth/login", json={"username": "fixture.admin", "password": "SyntheticFixture9!"}
                    )
                    self.assertEqual(login.status_code, 200, login.text)
                    client.headers.update({"X-CSRF-Token": login.json()["csrf_token"], "X-Workspace-Id": "dept-01"})
                    secret = "synthetic-r18-never-export"
                    config = {"base_url": "https://model.test/anthropic", "model": "synthetic", "api_key": secret}
                    response = client.post("/api/v2/capabilities/model.configure:invoke", json=config)
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertNotIn(secret, response.text)
                    old_status = client.get("/api/skills/harness/status").json()
                    self.assertTrue(old_status["configured"])
                    for key in ("base_url", "model", "configured", "verified"):
                        self.assertEqual(response.json()[key], old_status[key])
                    original = client.get("/api/system/backup?workspace_id=dept-01")
                    exported = client.get("/api/v2/system/backup?workspace_id=dept-01")
                    self.assertEqual(
                        exported.status_code, 200, exported.text[:100] if exported.status_code != 200 else ""
                    )
                    old_zip, new_zip = root / "original.zip", root / "export.zip"
                    old_zip.write_bytes(original.content)
                    new_zip.write_bytes(exported.content)
                    with zipfile.ZipFile(old_zip) as old, zipfile.ZipFile(new_zip) as new:
                        self.assertEqual(old.namelist(), new.namelist())
                        self.assertTrue(any(name.startswith("data/uploads/") for name in new.namelist()))
                        for name in new.namelist():
                            self.assertNotIn(secret.encode(), new.read(name))
                            if name.endswith("mysql-workspace.json"):
                                left, right = json.loads(old.read(name)), json.loads(new.read(name))
                                left.pop("created_at")
                                right.pop("created_at")
                                self.assertEqual(left, right)
                            elif name != "backup-info.json":
                                self.assertEqual(old.read(name), new.read(name))
                        snapshot = json.loads(new.read("data/mysql-workspace.json"))
                    with zipfile.ZipFile(new_zip) as archive:
                        self.assertFalse(any("runtime" in name for name in archive.namelist()))
                    cookie = client.cookies.get("ontology_session")
                    person = store.authenticate_session(cookie, "offline-r18")[0]
                    ctx = CapabilityContext(person, binding="offline", permissions=frozenset(permissions_for(person)))
                    body = {"archive": str(new_zip), "reason": "隔离环境恢复验收"}
                    # Wrong institution refuses the original archive before touching a DB.
                    with runtime_guard(shared=False), self.assertRaises(RestoreError):
                        registry.invoke(
                            "backup.restore", replace(ctx, principal=replace(person, tenant_id="sub-01")), body
                        )
                    with runtime_guard(shared=False), self.assertRaises(RestoreError):
                        registry.invoke("backup.restore", ctx, body)
                    # Simulated loss affects only our newly created, exact owned tenant database.
                    self.assertEqual(person.database_name, names["dept-01"])
                    with closing(db._raw_connection("information_schema")) as raw:
                        db.Connection(raw).execute(f"DROP DATABASE `{names['dept-01']}`")
                    shutil.rmtree(root / "tenants/dept-01/data/uploads")
                    with runtime_guard(shared=False):
                        restored = registry.invoke("backup.restore", ctx, body)
                    self.assertGreater(restored.files, 0)
                    self.assertGreater(restored.rows, 0)
                    with closing(db._raw_connection(names["dept-01"])) as raw:
                        conn = db.Connection(raw)
                        count = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
                        self.assertEqual(count, len(snapshot["tables"]["entities"]))
                    with zipfile.ZipFile(new_zip) as archive:
                        for name in archive.namelist():
                            if name.startswith("data/uploads/"):
                                self.assertEqual((root / "tenants/dept-01" / name).read_bytes(), archive.read(name))
                    with store.control_connection() as control, control.cursor() as cursor:
                        cursor.execute("SELECT details FROM sec_audit")
                        self.assertNotIn(secret, str(cursor.fetchall()))
                    client.close()
                finally:
                    with closing(db._raw_connection("information_schema")) as raw:
                        conn = db.Connection(raw)
                        for name in names.values():
                            self.assertTrue(name.startswith(namespace))
                            conn.execute(f"DROP DATABASE IF EXISTS `{name}`")
