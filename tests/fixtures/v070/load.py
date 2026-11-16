"""Load original 0.7.0 snapshots into a fresh MYSQL_DATABASE namespace.

No current schema initializer or migration is called. Set MYSQL_* and
ONTOLOGY_STORAGE_ROOT, then execute this file using either release's Python.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import stat
import sys
import zipfile
from pathlib import Path, PurePosixPath

import pymysql

FIXTURE = Path(__file__).resolve().parent


def load_fixture(fixture: Path = FIXTURE) -> dict[str, str]:
    from backend.db import mysql_config

    fixture = Path(fixture)
    manifest = json.loads((fixture / "manifest.json").read_text())
    if (
        manifest.get("source_commit") != "1359118dd672431dcd0cde2eb81f2b0f994eef37"
        or manifest.get("version") != "0.7.0"
    ):
        raise ValueError("Historical fixture must identify the accepted 0.7.0 source")
    required = {"control.json", "hq.json", "dept-01.json", "sub-01.json", "originals.backup", "workspace-v070.backup"}
    if set(manifest["files"]) != required or manifest["tenants"] != ["hq", "dept-01", "sub-01"]:
        raise ValueError("Historical fixture artifact set is incomplete")
    for name, digest in manifest["files"].items():
        if Path(name).name != name or hashlib.sha256((fixture / name).read_bytes()).hexdigest() != digest:
            raise ValueError("Historical fixture integrity check failed")
    config = mysql_config()
    namespace = config.pop("database")
    if not re.fullmatch(r"[A-Za-z0-9_]{1,48}", namespace):
        raise ValueError("Invalid isolated fixture namespace")
    config.update(charset="utf8mb4", autocommit=False)
    control = namespace[:48] + "_control"
    values = {name: json.loads((fixture / (name + ".json")).read_text()) for name in ["control", *manifest["tenants"]]}
    # The stdlib validator is installation-owned, not loaded from fixture input.
    # This works even while the actual 0.7.0 backend is active on sys.path.
    validator_path = FIXTURE.parents[2] / "backend" / "infra" / "recovery_schema.py"
    spec = importlib.util.spec_from_file_location("fixture_recovery_schema", validator_path)
    recovery = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(recovery)
    schemas = {}
    for name, value in values.items():
        schemas[name] = recovery.validate_schema(value, "control" if name == "control" else "tenant")
        for table, rows in value["tables"].items():
            encoded = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
            if manifest["tables"][name][table] != {
                "rows": len(rows),
                "sha256": hashlib.sha256(encoded.encode()).hexdigest(),
            }:
                raise ValueError("Historical fixture row checksum mismatch")
    originals = {}
    with zipfile.ZipFile(fixture / "originals.backup") as archive:
        if sum(info.file_size for info in archive.infolist()) > 128 * 1024**2:
            raise ValueError("Historical fixture originals exceed size limit")
        for member in archive.infolist():
            relative = PurePosixPath(member.filename)
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or "\\" in member.filename
                or len(relative.parts) < 4
                or relative.parts[0] not in manifest["tenants"]
                or relative.parts[1:3] != ("data", "uploads")
                or member.filename in originals
                or member.is_dir()
                or stat.S_ISLNK(member.external_attr >> 16)
            ):
                raise ValueError("Invalid historical original path")
            originals[member.filename] = archive.read(member)
    databases = {"control": control}
    for tenant in values["control"]["tables"]["sec_tenants"]:
        digest = hashlib.sha256((control + ":" + tenant["id"]).encode()).hexdigest()[:24]
        tenant["db_name"] = namespace[:32] + "_t_" + digest
        if tenant["id"] in manifest["tenants"]:
            databases[tenant["id"]] = tenant["db_name"]
    storage = Path(os.environ["ONTOLOGY_STORAGE_ROOT"])
    if storage.exists() and any(storage.iterdir()):
        raise ValueError("Fixture storage must be empty")
    with pymysql.connect(**config) as conn, conn.cursor() as cursor:
        for database in databases.values():
            cursor.execute("SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s", (database,))
            if cursor.fetchone():
                raise ValueError("Fixture target database already exists")
        for name, database in databases.items():
            value = values[name]
            cursor.execute(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
            cursor.execute(f"USE `{database}`")
            cursor.execute("SET FOREIGN_KEY_CHECKS=0")
            for table, ddl in value["ddl"].items():
                cursor.execute(schemas[name]["ddl"][table])
                rows = value["tables"][table]
                columns = value["columns"][table]
                if rows:
                    cursor.executemany(
                        f"INSERT INTO `{table}` ("
                        + ",".join("`" + c + "`" for c in columns)
                        + ") VALUES ("
                        + ",".join(["%s"] * len(columns))
                        + ")",
                        [[row[c] for c in columns] for row in rows],
                    )
                counter = re.search(r"\bAUTO_INCREMENT=(\d+)\b", ddl)
                if counter:
                    cursor.execute(f"ALTER TABLE `{table}` AUTO_INCREMENT={int(counter[1])}")
            for statement in schemas[name]["triggers"].values():
                cursor.execute(statement)
            cursor.execute("SET FOREIGN_KEY_CHECKS=1")
            conn.commit()
    storage.mkdir(parents=True, mode=0o700, exist_ok=True)
    for name, content in originals.items():
        target = storage.joinpath(*PurePosixPath(name).parts)
        target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        target.write_bytes(content)
        target.chmod(0o600)
    return databases


if __name__ == "__main__":
    sys.path.insert(0, str(Path.cwd()))
    print(json.dumps(load_fixture(), ensure_ascii=False))
