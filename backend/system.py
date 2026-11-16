"""Local MySQL workspace export. Model credentials are never included."""

import json
import shutil
import tempfile
import zipfile
from contextlib import closing
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from .version import APP_VERSION
from .db import Connection, connect, now
from .storage import workspace_root, scoped_path, open_file
from .security.context import require_context
from .security.store import assert_principal_current

router = APIRouter()


@router.get("/system/release")
def release():
    p = assert_principal_current(require_context())
    if not (p.home_tenant_id == "hq" and p.is_superadmin and p.role == "admin"):
        from fastapi import HTTPException

        raise HTTPException(403, "仅总部超级管理员可导出源码安装包。")
    from scripts.package_release import create_release

    folder = Path(tempfile.mkdtemp(prefix="ontology-release-"))
    try:
        archive = folder / "release.zip"
        create_release(archive)
        return FileResponse(
            archive,
            media_type="application/zip",
            filename=f"Ontology-One-{APP_VERSION}-平台源码.zip",
            background=BackgroundTask(shutil.rmtree, folder, ignore_errors=True),
        )
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise


# Every tenant table must have an explicit retention/export decision. Future
# rebuildable caches and optional conversation tables belong here as introduced.
TABLE_CLASSIFICATION = dict.fromkeys(
    (
        "changesets",
        "changeset_items",
        "recipe_runs",
        "artifacts",
        "artifact_versions",
        "artifact_access_log",
        "activities",
        "application_findings",
        "archived_reading_drafts",
        "asset_import_batches",
        "chunk_anchors",
        "chunks",
        "dataset_ai_reviews",
        "dataset_import_receipts",
        "dataset_mapping_versions",
        "dataset_row_sources",
        "dataset_rows",
        "dataset_sheets",
        "demo_installations",
        "entities",
        "entity_access",
        "entity_ownership",
        "jobs",
        "mappings",
        "reading_drafts",
        "reading_versions",
        "relations",
        "skill_checks",
        "skill_documents",
        "skill_revisions",
        "source_anchors",
        "source_connections",
        "source_grants",
        "source_mapping_versions",
        "source_mappings",
        "source_resources",
        "source_schema_versions",
        "source_versions",
        "special_deliveries",
        "versions",
        "workspace_meta",
    ),
    "export",
)
TABLE_CLASSIFICATION["schema_migrations"] = "deployment"
EXPORTED_TABLES = frozenset(name for name, category in TABLE_CLASSIFICATION.items() if category == "export")


def export_snapshot(conn: Connection, target: Path, tenant_id: str) -> None:
    """Portable tenant snapshot; caller supplies an authorized/offline connection."""
    existing = {
        r[0]
        for r in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema=DATABASE() AND table_type='BASE TABLE'"
        )
    }
    if existing - TABLE_CLASSIFICATION.keys():
        raise RuntimeError("机构库存在尚未登记备份分类的表，导出已停止。")
    tables = sorted(existing & EXPORTED_TABLES)
    with target.open("w", encoding="utf-8") as stream:
        stream.write('{"format":"ontology-mysql-workspace-v1","database":"MySQL",')
        stream.write(json.dumps("created_at") + ":" + json.dumps(now()) + ",")
        stream.write(json.dumps("source_tenant") + ":" + json.dumps(tenant_id) + ",")
        stream.write('"tables":{')
        for index, table in enumerate(tables):
            if index:
                stream.write(",")
            stream.write(json.dumps(table) + ":[")
            rows = conn.execute(f"SELECT * FROM `{table}`")
            first = True
            while True:
                batch = rows.fetchmany(1000)
                if not batch:
                    break
                for row in batch:
                    if not first:
                        stream.write(",")
                    stream.write(json.dumps(dict(row), ensure_ascii=False, default=str))
                    first = False
            stream.write("]")
        stream.write("}}")


def export_mysql_snapshot(target):
    with closing(connect()) as conn:
        export_snapshot(conn, target, require_context().tenant_id)


def export_workspace_backup(conn: Connection, tenant_id: str, root: Path, target: Path) -> None:
    """Request-independent original workspace archive for offline upgrades."""
    from .platform_io import reject_reparse_points

    root, target = Path(root), Path(target)
    with tempfile.TemporaryDirectory(prefix=".workspace-export-", dir=target.parent) as folder:
        snapshot = Path(folder) / "mysql-workspace.json"
        export_snapshot(conn, snapshot, tenant_id)
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=5) as archive:
            archive.write(snapshot, "data/mysql-workspace.json")
            base = root / "data" / "uploads"
            reject_reparse_points(base)
            if base.exists():
                for path in sorted(base.rglob("*")):
                    reject_reparse_points(path)
                    if path.is_file():
                        archive.write(path, path.relative_to(root).as_posix())
            archive.writestr(
                "backup-info.json",
                json.dumps(
                    {
                        "platform": "集团本体平台",
                        "version": APP_VERSION,
                        "database": "MySQL",
                        "created_at": now(),
                        "contains": ["当前机构业务表快照", "当前机构上传原件"],
                        "excluded": ["AI接口密钥与运行配置", "外部数据源凭据", "源端查询结果"],
                        "restore": "停止全部 API 与 worker 后使用 scripts/restore_backup.py 离线恢复。",
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )


@router.get("/system/backup")
def backup():
    principal = assert_principal_current(require_context())
    root = workspace_root()
    folder = Path(tempfile.mkdtemp(prefix="ontology-backup-"))
    try:
        snapshot = folder / "mysql-workspace.json"
        export_mysql_snapshot(snapshot)
        archive = folder / "workspace.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=5) as z:
            z.write(snapshot, "data/mysql-workspace.json")
            base = root / "data" / "uploads"
            if base.exists():
                for path in base.rglob("*"):
                    relative = path.relative_to(root).as_posix()
                    safe = scoped_path(relative)
                    if safe.is_file():
                        with open_file(safe, "rb") as source, z.open(relative, "w") as destination:
                            shutil.copyfileobj(source, destination, 256 * 1024)
            assert_principal_current(principal)
            z.writestr(
                "backup-info.json",
                json.dumps(
                    {
                        "platform": "集团本体平台",
                        "version": APP_VERSION,
                        "database": "MySQL",
                        "created_at": now(),
                        "contains": ["当前机构业务表快照", "当前机构上传原件"],
                        "excluded": ["AI接口密钥与运行配置", "外部数据源凭据", "源端查询结果"],
                        "restore": "停止共享存储上的全部API与worker，使用 scripts/restore_backup.py backup.zip --tenant-id <本机构ID> --maintenance --reason <恢复原因>，交互验证管理员身份后恢复同机构空工作区及原件。",
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
        return FileResponse(
            archive,
            media_type="application/zip",
            filename=f"Ontology-One-{APP_VERSION}-机构备份.zip",
            background=BackgroundTask(shutil.rmtree, folder, ignore_errors=True),
        )
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
