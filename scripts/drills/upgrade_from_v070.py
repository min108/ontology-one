"""Disposable real-MySQL upgrade/failure/recovery drill against actual 1359118."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main() -> None:
    from backend import db
    from backend.infra import backup, migrations
    from backend.maintenance import runtime_guard
    from tests.fixtures.v070.load import load_fixture

    parser = argparse.ArgumentParser()
    parser.add_argument("--v070-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = args.v070_source.resolve()
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
    if not commit.startswith("1359118") or (source / "VERSION").read_text().strip() != "0.7.0":
        raise RuntimeError("Drill requires the actual accepted 1359118 checkout")
    namespace = "ontology_r12drill_" + secrets.token_hex(5)
    work = ROOT / "work"
    work.mkdir(exist_ok=True)
    report = {"source_commit": commit, "namespace": namespace, "stages": []}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="upgrade-drill-", dir=work) as temporary:
        folder = Path(temporary)
        environment = {
            "MYSQL_DATABASE": namespace,
            "ONTOLOGY_STORAGE_ROOT": str(folder / "tenants"),
            "ONTOLOGY_BACKUP_DIR": str(folder / "backups"),
            "ONTOLOGY_WORKER_ENABLED": "0",
        }
        with patch.dict(os.environ, environment):
            os.environ.pop("ONTOLOGY_MYSQL_URL", None)
            process = None
            try:
                names = load_fixture()
                before = {name: backup.snapshot_database(name)["hashes"] for name in names.values()}
                report["stages"].append("loaded_original_v070")
                first = migrations.run_migrations()
                migrations.validate_runtime()
                report["stages"].append("migrated_and_validated")
                sequence = folder / "migrations"
                shutil.copytree(migrations.ROOT, sequence)
                next_version = migrations.discover("tenant")[-1].version + 1
                injected_name = f"{next_version:04d}_injected_failure"
                (sequence / "tenant" / f"{injected_name}.py").write_text(
                    "def up(conn):\n"
                    "    conn.execute('CREATE TABLE injected_partial (id INT PRIMARY KEY)')\n"
                    "    if conn.execute('SELECT 1').fetchall():\n"
                    "        raise RuntimeError('deliberate upgrade drill failure')\n"
                )
                with patch.object(migrations, "ROOT", sequence):
                    try:
                        migrations.run_migrations()
                    except RuntimeError as exc:
                        if str(exc) != "deliberate upgrade drill failure":
                            raise
                    else:
                        raise AssertionError("Injected migration did not fail")
                    partial = [
                        ident for ident, name in names.items() if "injected_partial" in migrations.table_names(name)
                    ]
                    state = migrations.read_state()
                    if len(partial) != 1 or not state or state["status"] != "failed" or not state.get("backup"):
                        raise AssertionError("Missing partial tenant DDL or durable failed-upgrade backup state")
                    status = migrations.migration_status("tenant", names[partial[0]])
                    if status["current"] != next_version - 1 or status["pending"] != [injected_name]:
                        raise AssertionError("An incomplete migration was recorded as applied")
                    report["injected_failure"] = {
                        "migration": injected_name,
                        "tenant": partial[0],
                        "state": state["status"],
                    }
                # Even the installed migration sequence must refuse restart after the injected runner disappears.
                try:
                    migrations.validate_runtime()
                except migrations.MigrationError:
                    pass
                else:
                    raise AssertionError("Failed deployment was allowed to start")
                report["stages"].append("failed_upgrade_blocked_restart")
                with runtime_guard(shared=False):
                    backup.restore_upgrade_backup(first["backup"])
                after = {name: backup.snapshot_database(name)["hashes"] for name in names.values()}
                if after != before:
                    raise AssertionError("Restored rows differ from original 0.7.0")
                report["stages"].append("restored_original_hashes")
                with socket.socket() as port_probe:
                    port_probe.bind(("127.0.0.1", 0))
                    port = port_probe.getsockname()[1]
                with (folder / "legacy-api.log").open("w") as log:
                    process = subprocess.Popen(
                        [
                            sys.executable,
                            "-m",
                            "uvicorn",
                            "backend.main:app",
                            "--host",
                            "127.0.0.1",
                            "--port",
                            str(port),
                            "--no-access-log",
                        ],
                        cwd=source,
                        env=os.environ.copy(),
                        stdout=log,
                        stderr=subprocess.STDOUT,
                    )
                    deadline = time.monotonic() + 30
                    while time.monotonic() < deadline:
                        if process.poll() is not None:
                            raise RuntimeError("Original API exited before becoming healthy")
                        try:
                            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as response:
                                health = json.load(response)
                            if health.get("ok") and health.get("version") == "0.7.0":
                                report["health"] = health
                                break
                        except OSError:
                            time.sleep(0.1)
                    else:
                        raise RuntimeError("Original API health check timed out")
                report["stages"].append("original_v070_process_healthy")
                report["passed"] = True
            finally:
                if process is not None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                with closing(db._raw_connection("information_schema")) as raw:
                    conn = db.Connection(raw)
                    names = [
                        row[0]
                        for row in conn.execute(
                            "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE LEFT(SCHEMA_NAME,?)=?",
                            (len(namespace), namespace),
                        )
                    ]
                    for name in names:
                        if not name.startswith(namespace):
                            raise RuntimeError("Refusing cleanup outside drill namespace")
                        conn.execute(f"DROP DATABASE `{name}`")
                args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
