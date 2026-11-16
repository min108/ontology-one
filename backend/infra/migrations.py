"""Checksum-pinned migrations and fail-closed offline upgrade coordination."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import re
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from backend import db
from backend.maintenance import runtime_guard
from backend.platform_io import atomic_write

ROOT = Path(__file__).resolve().parents[2] / "migrations"
HISTORY = """CREATE TABLE IF NOT EXISTS schema_migrations (
    version INT PRIMARY KEY, name VARCHAR(191) NOT NULL,
    checksum CHAR(64) NOT NULL, applied_at VARCHAR(40) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"""


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    checksum: str
    up: Callable


def discover(kind: str, root: Path | None = None) -> list[Migration]:
    if kind not in {"control", "tenant"}:
        raise MigrationError("未知的数据库迁移序列。")
    folder = (root or ROOT) / kind
    if not folder.is_dir():
        raise MigrationError("迁移文件缺失，请检查安装包。")
    result = []
    for path in sorted(folder.glob("*.py")):
        if path.name == "__init__.py":
            continue
        match = re.fullmatch(r"(\d{4})_([a-z][a-z0-9_]*)\.py", path.name)
        if not match or int(match[1]) != len(result) + 1:
            raise MigrationError("迁移编号重复、不连续或文件名无效。")
        source = path.read_bytes()
        checksum = hashlib.sha256(source).hexdigest()
        tree = ast.parse(source, filename=str(path))
        if not any(isinstance(node, ast.FunctionDef) and node.name == "up" for node in tree.body):
            raise MigrationError("迁移文件缺少执行函数。")

        def invoke(conn: db.Connection, checked_source: bytes = source, file: Path = path) -> object:
            spec = importlib.util.spec_from_file_location("ontology_migration_" + file.stem, file)
            module = importlib.util.module_from_spec(spec)
            # Imports and module initialization also happen only after backup.
            # Execute exact checked bytes, never a timestamp/size-matched .pyc.
            exec(compile(checked_source, str(file), "exec"), module.__dict__)
            return module.up(conn)

        result.append(Migration(int(match[1]), path.stem, checksum, invoke))
    if not result:
        raise MigrationError("迁移序列缺少基线文件，请检查安装包。")
    return result


def pending(sequence: list[Migration], applied: list[dict]) -> list[Migration]:
    for index, row in enumerate(sorted(applied, key=lambda r: r["version"]), 1):
        if row["version"] != index or index > len(sequence):
            raise MigrationError("数据库迁移记录不连续或高于当前程序版本。")
        migration = sequence[index - 1]
        if row["name"] != migration.name or row["checksum"] != migration.checksum:
            raise MigrationError("已执行的迁移文件被修改，升级已停止。")
    return sequence[len(applied) :]


def state_path() -> Path:
    storage = Path(
        os.environ.get("ONTOLOGY_STORAGE_ROOT", str(Path(__file__).resolve().parents[2] / "data" / "tenants"))
    )
    return storage.absolute().parent / ".ontology-upgrade-state.json"


def write_state(value: dict) -> None:
    atomic_write(state_path(), json.dumps(value, ensure_ascii=False, sort_keys=True).encode())


def read_state() -> dict | None:
    path = state_path()
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text())
        if not isinstance(value, dict) or value.get("status") not in {"in_progress", "failed", "restoring", "restored"}:
            raise ValueError()
        return value
    except (OSError, ValueError) as exc:
        raise MigrationError("升级状态无法校验，请先完成离线恢复。") from exc


def require_healthy_state() -> None:
    state = read_state()
    if state and state["status"] != "restored":
        raise MigrationError("上次升级尚未完成，请先从升级前备份恢复。")


def database_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_]{1,64}", value):
        raise MigrationError("数据库目录名称无效。")
    return value


def table_names(database: str) -> list[str]:
    with closing(db._raw_connection("information_schema")) as raw:
        conn = db.Connection(raw)
        return [
            r[0]
            for r in conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema=? AND table_type='BASE TABLE' ORDER BY table_name",
                (database_name(database),),
            )
        ]


def migration_status(kind: str, database: str, root: Path | None = None) -> dict:
    sequence = discover(kind, root)
    tables = table_names(database)
    applied = []
    if "schema_migrations" in tables:
        with closing(db._raw_connection(database)) as raw:
            applied = [
                dict(r)
                for r in db.Connection(raw).execute(
                    "SELECT version,name,checksum,applied_at FROM schema_migrations ORDER BY version"
                )
            ]
    remaining = pending(sequence, applied)
    return {"current": len(applied), "pending": [m.name for m in remaining], "tables": tables}


def migrate_database(
    kind: str, database: str, *, tenant_id: str | None = None, root: Path | None = None, fresh_only: bool = False
) -> list[str]:
    sequence = discover(kind, root)
    with closing(db._raw_connection(database_name(database), create=True)) as raw:
        conn = db.Connection(raw)
        conn.tenant_id = tenant_id
        lock = "ontology-migration-" + hashlib.sha256(database.encode()).hexdigest()[:40]
        if conn.execute("SELECT GET_LOCK(?,30)", (lock,)).fetchone()[0] != 1:
            raise MigrationError("该工作区正在迁移，请稍后重试。")
        try:
            tables = table_names(database)
            applied = (
                [
                    dict(r)
                    for r in conn.execute(
                        "SELECT version,name,checksum,applied_at FROM schema_migrations ORDER BY version"
                    )
                ]
                if "schema_migrations" in tables
                else []
            )
            remaining = pending(sequence, applied)
            if fresh_only and remaining and tables:
                raise MigrationError("已有工作区需要离线升级，请先停止服务并运行迁移工具。")
            if not remaining:
                return []
            conn.execute(HISTORY)
            completed = []
            for migration in remaining:
                migration.up(conn)
                conn.execute(
                    "INSERT INTO schema_migrations(version,name,checksum,applied_at) VALUES(?,?,?,?)",
                    (migration.version, migration.name, migration.checksum, db.now()),
                )
                conn.commit()
                completed.append(migration.name)
            return completed
        finally:
            conn.execute("SELECT RELEASE_LOCK(?)", (lock,))


def provision_tenant(tenant_id: str) -> None:
    """Only a directory-owned, wholly empty database can be created online."""
    from backend.security.store import get_tenant

    require_healthy_state()
    tenant = get_tenant(tenant_id)
    if not tenant or tenant["status"] != "active":
        raise MigrationError("机构不存在或已停用。")
    migrate_database("tenant", tenant["db_name"], tenant_id=tenant_id, fresh_only=True)


def validate_runtime() -> None:
    """Read-only startup gate; absent tenant databases remain lazy."""
    from backend.security import store

    require_healthy_state()
    control = store._config()["database"]
    if migration_status("control", control)["pending"]:
        raise MigrationError("账户目录需要升级，请先运行迁移工具。")
    for tenant in store.list_tenants():
        status = migration_status("tenant", tenant["db_name"])
        if status["tables"] and status["pending"]:
            raise MigrationError("机构工作区需要升级，请先运行迁移工具。")
    # Queue schema is already verified; the worker's legacy lazy guard must not
    # issue CREATE TABLE on its first claim in a newly started process.
    from backend import jobs

    config = db.mysql_config()
    jobs._schema_ready.add((config["host"], config["port"], config["database"]))


def run_migrations(
    provision_all: bool = False, progress: Callable[[], None] | None = None, finalize: object = None
) -> dict:
    """One exclusive lease spans inspection, verified backup and all migrations."""
    from backend.infra.backup import create_upgrade_backup
    from backend.security import store

    completed_units = 0

    def report(phase: object, completed: object = 0, total: object = 0) -> None:
        nonlocal completed_units
        if phase == "backup":
            completed_units = min(completed_units + 1, 8999)
        elif phase == "tenants" and total:
            completed_units = max(completed_units, 9000 + completed * 999 // total)
        if progress:
            progress(phase, completed_units, 10000)

    with runtime_guard(shared=False):
        control = store._config()["database"]
        previous = read_state()
        # An interrupted first installation has no pre-existing data to restore.
        # Only the offline entry may resume that explicitly recorded case; every
        # runtime process still rejects the persistent incomplete state.
        fresh_resume = bool(
            previous
            and previous.get("fresh_install") is True
            and previous.get("database") == control
            and previous.get("backup") is None
            and previous["status"] in {"failed", "in_progress"}
        )
        if not fresh_resume:
            require_healthy_state()
        control_status = migration_status("control", control)
        if "sec_tenants" not in control_status["tables"] and not fresh_resume:
            prefix = control.removesuffix("_control")[:32] + "_t_"
            with closing(db._raw_connection("information_schema")) as raw:
                orphan = (
                    db.Connection(raw)
                    .execute(
                        "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE LEFT(SCHEMA_NAME,?)=? LIMIT 1",
                        (len(prefix), prefix),
                    )
                    .fetchone()
                )
            if orphan:
                raise MigrationError("账户目录缺失但仍存在机构库，请先恢复完整备份，不能按全新安装继续。")
        tenants = store.list_tenants() if "sec_tenants" in control_status["tables"] else []
        statuses = [(tenant, migration_status("tenant", tenant["db_name"])) for tenant in tenants]
        needs_backup = not fresh_resume and (
            bool(control_status["tables"] and control_status["pending"])
            or any(status["tables"] and status["pending"] for _, status in statuses)
        )
        backup = None
        if needs_backup:
            report("backup")
            backup = create_upgrade_backup(tenants, progress=lambda: report("backup"))
        must_migrate = bool(control_status["pending"]) or any(
            status["pending"] and (status["tables"] or provision_all) for _, status in statuses
        )
        if not must_migrate:
            if finalize:
                finalize()
            if fresh_resume:
                state_path().unlink()
            return {"backup": None, "migrated": []}
        state = {
            "status": "in_progress",
            "database": control,
            "backup": str(backup) if backup else None,
            "started_at": db.now(),
            "fresh_install": fresh_resume or not control_status["tables"],
        }
        write_state(state)
        migrated = []
        try:
            report("control")
            if migrate_database("control", control):
                migrated.append(control)
            report("federation")
            report("queue")
            tenants = store.list_tenants()
            selected = [t for t in tenants if provision_all or table_names(t["db_name"])]
            report("tenants", 0, len(selected))
            for index, tenant in enumerate(selected, 1):
                if migrate_database("tenant", tenant["db_name"], tenant_id=tenant["id"]):
                    migrated.append(tenant["db_name"])
                report("tenants", index, len(selected))
            if finalize:
                finalize()
            state_path().unlink()
            return {"backup": str(backup) if backup else None, "migrated": migrated}
        except BaseException:
            write_state({**state, "status": "failed", "failed_at": db.now()})
            raise
