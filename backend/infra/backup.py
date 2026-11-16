"""Verified full-deployment snapshots under the existing offline maintenance lock.

Tenant ZIPs remain individually restorable. Full recovery also preserves the
control catalog, immutable audit history, migration history and absent databases.
Only upload originals are copied; credentials and encryption keys never are.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
import zipfile
from collections.abc import Callable
from contextlib import closing
from pathlib import Path, PurePosixPath

from backend import db
from backend.infra.recovery_schema import validate_schema
from backend.maintenance import require_maintenance
from backend.platform_io import atomic_write, ensure_private_directory, reject_reparse_points

FORMAT = "ontology-upgrade-backup-v1"


class BackupError(RuntimeError):
    pass


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def backup_root() -> Path:
    return Path(
        os.environ.get("ONTOLOGY_BACKUP_DIR", str(Path(__file__).resolve().parents[2] / "data" / "backups"))
    ).absolute()


def prune_upgrade_backups(root: Path, control: str, keep: int = 3) -> None:
    """Retention never owns another deployment sharing the backup volume."""
    completed = []
    for path in sorted(root.glob("upgrade-*"), reverse=True):
        if not path.is_dir():
            continue
        try:
            manifest = json.loads((path / "manifest.json").read_text())
        except (OSError, ValueError):
            continue
        if isinstance(manifest, dict) and manifest.get("control_database") == control:
            completed.append(path)
    for old in completed[keep:]:
        verify_upgrade_backup(old)
        shutil.rmtree(old)


def snapshot_database(database: str, progress: Callable[[], None] | None = None) -> dict:
    from backend.infra.migrations import database_name

    value = {"database": database_name(database), "tables": {}, "ddl": {}, "columns": {}, "triggers": {}, "hashes": {}}
    with closing(db._raw_connection(database)) as raw:
        conn = db.Connection(raw)
        names = [r[0] for r in conn.execute("SHOW TABLES")]
        for name in names:
            database_name(name)
            rows = sorted([dict(row) for row in conn.execute(f"SELECT * FROM `{name}`")], key=canonical)
            value["tables"][name] = rows
            value["ddl"][name] = conn.execute(f"SHOW CREATE TABLE `{name}`").fetchone()[1]
            value["columns"][name] = [r[0] for r in conn.execute(f"SHOW COLUMNS FROM `{name}`")]
            value["hashes"][name] = {"rows": len(rows), "sha256": hashlib.sha256(canonical(rows).encode()).hexdigest()}
            if progress:
                progress()
        for row in conn.execute("SHOW TRIGGERS"):
            name = row["Trigger"]
            database_name(name)
            value["triggers"][name] = conn.execute(f"SHOW CREATE TRIGGER `{name}`").fetchone()[2]
    return value


def _validate_snapshot(value: dict, expected_database: str, kind: str = "tenant") -> None:
    from backend.infra.migrations import database_name

    if (
        value.get("database") != expected_database
        or set(value["tables"]) != set(value["ddl"])
        or set(value["tables"]) != set(value["columns"])
        or set(value["tables"]) != set(value["hashes"])
    ):
        raise BackupError("备份数据库或表清单不一致。")
    validate_schema(value, kind)
    for table, rows in value["tables"].items():
        database_name(table)
        columns = value["columns"][table]
        for column in columns:
            database_name(column)
        expected = value["hashes"][table]
        if rows != sorted(rows, key=canonical) or expected != {
            "rows": len(rows),
            "sha256": hashlib.sha256(canonical(rows).encode()).hexdigest(),
        }:
            raise BackupError("备份行数或内容摘要不一致。")


def verify_upgrade_backup(path: Path) -> tuple[dict, dict]:
    """Verify every member before recovery can issue any destructive SQL."""
    path = Path(path).absolute()
    reject_reparse_points(path)
    try:
        manifest = json.loads((path / "manifest.json").read_text())
        if (
            not isinstance(manifest, dict)
            or manifest.get("format") != FORMAT
            or not isinstance(manifest.get("files"), dict)
        ):
            raise BackupError("升级备份格式无效。")
        expected = set(manifest["files"]) | {"manifest.json"}
        actual = {p.relative_to(path).as_posix() for p in path.rglob("*") if p.is_file()}
        if expected != actual:
            raise BackupError("升级备份成员不完整或包含额外文件。")
        for name, digest in manifest["files"].items():
            relative = PurePosixPath(name)
            if relative.is_absolute() or ".." in relative.parts or "\\" in name:
                raise BackupError("升级备份路径无效。")
            member = path.joinpath(*relative.parts)
            reject_reparse_points(member)
            if digest_file(member) != digest:
                raise BackupError("升级备份文件校验失败。")
        snapshots, entries, artifacts = {}, {}, set()
        for entry in manifest["databases"]:
            if set(entry) != {"tenant_id", "database", "snapshot"}:
                raise BackupError("升级备份数据库条目无效。")
            ident = entry["tenant_id"]
            if ident is not None and (
                not isinstance(ident, str) or ident == "control" or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", ident)
            ):
                raise BackupError("升级备份机构编号无效。")
            if ident in entries or entry["database"] in {e["database"] for e in entries.values()}:
                raise BackupError("升级备份包含重复机构或数据库。")
            entries[ident] = entry
            if entry["snapshot"] is None:
                continue
            snapshot_name = (ident or "control") + ".json"
            if entry["snapshot"] != snapshot_name or snapshot_name not in manifest["files"]:
                raise BackupError("升级备份缺少数据库快照。")
            artifacts.add(snapshot_name)
            value = json.loads((path / snapshot_name).read_text())
            _validate_snapshot(value, entry["database"], "control" if ident is None else "tenant")
            snapshots[entry["database"]] = value
            if ident is not None:
                archive_name = ident + ".backup"
                if archive_name not in manifest["files"]:
                    raise BackupError("已开通机构缺少原件备份包，拒绝恢复。")
                artifacts.add(archive_name)
                from backend.system import EXPORTED_TABLES
                from scripts.restore_backup import validate_archive, validate_snapshot

                with zipfile.ZipFile(path / archive_name) as archive:
                    validate_archive(archive)
                    portable = json.loads(archive.read("data/mysql-workspace.json"))
                    validate_snapshot(portable, ident)
                    expected_tables = {
                        table: rows for table, rows in value["tables"].items() if table in EXPORTED_TABLES
                    }
                    actual_tables = {table: sorted(rows, key=canonical) for table, rows in portable["tables"].items()}
                    if expected_tables != actual_tables:
                        raise BackupError("机构便携备份与完整数据库快照不一致。")
        control = manifest["control_database"]
        if None not in entries or entries[None]["database"] != control or control not in snapshots:
            raise BackupError("升级备份缺少唯一控制库。")
        catalog = snapshots[control]["tables"]["sec_tenants"]
        directory = {}
        for tenant in catalog:
            ident = tenant["id"]
            expected_database = (
                control.removesuffix("_control")[:32]
                + "_t_"
                + hashlib.sha256((control + ":" + ident).encode()).hexdigest()[:24]
            )
            if ident in directory or tenant["db_name"] != expected_database:
                raise BackupError("备份机构数据库不符合完整目录派生规则。")
            directory[ident] = expected_database
        if {ident: entry["database"] for ident, entry in entries.items() if ident is not None} != directory:
            raise BackupError("升级备份与完整机构目录不一致。")
        if set(manifest["files"]) != artifacts:
            raise BackupError("升级备份包含未绑定的成员。")
        return manifest, snapshots
    except (KeyError, TypeError, ValueError, OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise BackupError("升级备份无法完整校验。") from exc


def create_upgrade_backup(tenants: list[dict], progress: Callable[[], None] | None = None) -> Path:
    require_maintenance()
    from backend.infra.migrations import state_path, table_names
    from backend.security import store
    from backend.system import export_workspace_backup

    root = backup_root()
    ensure_private_directory(root)
    storage = state_path().parent / "tenants"
    storage = Path(os.environ.get("ONTOLOGY_STORAGE_ROOT", str(storage)))
    name = "upgrade-" + db.now().replace(":", "").replace(".", "-") + "-" + uuid.uuid4().hex[:8]
    staging = Path(tempfile.mkdtemp(prefix=".upgrade-", dir=root))
    target = root / name
    manifest = {
        "format": FORMAT,
        "created_at": db.now(),
        "control_database": store._config()["database"],
        "databases": [],
        "files": {},
    }
    try:
        entries = [(None, manifest["control_database"]), *[(t["id"], t["db_name"]) for t in tenants]]
        for tenant_id, database in entries:
            present = bool(table_names(database))
            snapshot_name = (tenant_id or "control") + ".json" if present else None
            manifest["databases"].append({"tenant_id": tenant_id, "database": database, "snapshot": snapshot_name})
            if not present:
                if tenant_id:
                    uploads = storage / tenant_id / "data" / "uploads"
                    reject_reparse_points(uploads)
                    if uploads.exists() and any(uploads.iterdir()):
                        raise BackupError("未开通机构存在原件，请先核对存储，升级已停止。")
                continue
            value = snapshot_database(database, progress)
            atomic_write(staging / snapshot_name, (canonical(value) + "\n").encode())
            if tenant_id:
                with closing(db._raw_connection(database)) as raw:
                    export_workspace_backup(
                        db.Connection(raw), tenant_id, storage / tenant_id, staging / (tenant_id + ".backup")
                    )
            if progress:
                progress()
        for file in staging.iterdir():
            manifest["files"][file.name] = digest_file(file)
        atomic_write(staging / "manifest.json", (canonical(manifest) + "\n").encode())
        verify_upgrade_backup(staging)
        staging.rename(target)
        # Never prune an incomplete/newly failed backup or unrelated directories.
        prune_upgrade_backups(root, manifest["control_database"])
        return target
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def restore_upgrade_backup(path: Path) -> dict:
    require_maintenance()
    from backend.infra.migrations import database_name, state_path, write_state
    from backend.security import store

    path = Path(path).absolute()
    manifest, snapshots = verify_upgrade_backup(path)
    control = store._config()["database"]
    if manifest["control_database"] != control or control not in snapshots:
        raise BackupError("升级备份不属于当前部署，拒绝覆盖。")
    catalog = snapshots[control]["tables"].get("sec_tenants", [])
    owned = {t["db_name"] for t in catalog} | {control}
    if {entry["database"] for entry in manifest["databases"]} != owned:
        raise BackupError("升级备份与服务端机构目录不一致。")
    # verify_upgrade_backup already validated every full derived tenant name.
    for database in owned:
        database_name(database)
    storage = Path(os.environ.get("ONTOLOGY_STORAGE_ROOT", str(state_path().parent / "tenants")))
    for tenant in catalog:
        ident = tenant["id"]
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", ident):
            raise BackupError("备份机构目录无效。")
        try:
            reject_reparse_points(storage / ident / "data" / "uploads")
        except OSError as exc:
            raise BackupError("原件恢复路径包含不安全的链接，拒绝修改数据库和文件。") from exc
    write_state({"status": "restoring", "database": control, "backup": str(path)})
    try:
        with closing(db._raw_connection("information_schema")) as raw:
            conn = db.Connection(raw)
            conn.execute("SET FOREIGN_KEY_CHECKS=0")
            for entry in manifest["databases"]:
                database = entry["database"]
                conn.execute(f"DROP DATABASE IF EXISTS `{database}`")
                if entry["snapshot"] is None:
                    continue
                value = snapshots[database]
                trusted = validate_schema(value, "control" if entry["tenant_id"] is None else "tenant")
                conn.execute(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
                conn.execute(f"USE `{database}`")
                for table in value["ddl"]:
                    # No SQL statement from the backup is ever executed.
                    conn.execute(trusted["ddl"][table])
                    columns = value["columns"][table]
                    rows = value["tables"][table]
                    if rows:
                        conn.executemany(
                            f"INSERT INTO `{table}` ("
                            + ",".join("`" + c + "`" for c in columns)
                            + ") VALUES ("
                            + ",".join(["?"] * len(columns))
                            + ")",
                            [[row[c] for c in columns] for row in rows],
                        )
                    counter = re.search(r"\bAUTO_INCREMENT=(\d+)\b", value["ddl"][table])
                    if counter:
                        conn.execute(f"ALTER TABLE `{table}` AUTO_INCREMENT={int(counter[1])}")
                for statement in trusted["triggers"].values():
                    conn.execute(statement)
                conn.commit()
            conn.execute("SET FOREIGN_KEY_CHECKS=1")
        for tenant in catalog:
            uploads = storage / tenant["id"] / "data" / "uploads"
            reject_reparse_points(uploads)
            if uploads.exists():
                shutil.rmtree(uploads)
            archive_path = path / (tenant["id"] + ".backup")
            if archive_path.exists():
                with zipfile.ZipFile(archive_path) as archive:
                    for member in archive.infolist():
                        if member.is_dir() or not member.filename.startswith("data/uploads/"):
                            continue
                        target = storage / tenant["id"] / member.filename
                        ensure_private_directory(target.parent)
                        atomic_write(target, archive.read(member))
        for database, value in snapshots.items():
            restored = snapshot_database(database)
            _validate_snapshot(restored, database, "control" if database == control else "tenant")
            if set(restored["ddl"]) != set(value["ddl"]):
                raise BackupError("恢复后结构与备份不一致，平台继续保持维护状态。")
            if restored["hashes"] != value["hashes"]:
                raise BackupError("恢复后数据与备份不一致，平台继续保持维护状态。")
        db._READY.clear()
        write_state({"status": "restored", "database": control, "backup": str(path), "restored_at": db.now()})
        return {"databases": len(snapshots), "backup": str(path)}
    except BaseException:
        write_state({"status": "failed", "database": control, "backup": str(path), "operation": "restore"})
        raise
