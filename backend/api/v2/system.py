"""Management adapters. No restore HTTP route and no server paths in metadata."""

import json
import os

from fastapi import APIRouter, Request
from starlette.responses import FileResponse, Response

from backend import skill_harness, system
from backend.capabilities.context import CapabilityContext
from backend.capabilities.model import ModelStatus
from backend.capabilities.registry import registry
from backend.core.brand import BRAND
from backend.core.errors import ApiError, StrictModel
from backend.infra import backup, migrations
from backend.platform_io import reject_reparse_points
from backend.security import store
from backend.security.context import require_context
from backend.security.permissions import permissions_for
from backend.version import APP_VERSION

router = APIRouter(prefix="/system")
ZIP_RESPONSES = {200: {"content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}}}}


def context() -> CapabilityContext:
    person = store.assert_principal_current(require_context())
    return CapabilityContext(person, permissions=frozenset(permissions_for(person)))


def require_admin() -> CapabilityContext:
    ctx = context()
    if "admin.settings" not in ctx.permissions or ctx.principal.readonly:
        raise ApiError("PERMISSION_DENIED")
    return ctx


@router.get("/model", response_model=ModelStatus)
def model_status() -> ModelStatus:
    context()
    return ModelStatus.public(skill_harness.harness_status())


@router.post("/model:test")
async def test_model(request: Request) -> dict:
    ctx = require_admin()
    if ctx.principal.tenant_id != ctx.principal.home_tenant_id:
        raise ApiError("PERMISSION_DENIED", "当前为跨机构运维。模型连接验证须从账号所属机构发起。")
    result = await skill_harness.test_configuration(request)
    return {"ok": bool(result.get("ok")), "status": ModelStatus.public(result.get("status", {})).model_dump()}


@router.get("/backup", response_class=FileResponse, responses=ZIP_RESPONSES)
def download_backup() -> Response:
    ctx = require_admin()
    registry.invoke("backup.export", ctx, {})
    return system.backup()


@router.get("/release", response_class=FileResponse, responses=ZIP_RESPONSES)
def download_release() -> Response:
    ctx = require_admin()
    if not (ctx.principal.home_tenant_id == "hq" and ctx.principal.is_superadmin):
        raise ApiError("PERMISSION_DENIED")
    return system.release()


class MigrationSummary(StrictModel):
    current: int
    pending: list[str]


class SystemMetadata(StrictModel):
    product_name: str
    product_name_en: str
    version: str
    runtime_mode: str
    control: MigrationSummary
    workspace: MigrationSummary


def summary(kind: str, database: str) -> MigrationSummary:
    value = migrations.migration_status(kind, database)
    return MigrationSummary(current=value["current"], pending=value["pending"])


@router.get("/metadata", response_model=SystemMetadata)
def metadata() -> SystemMetadata:
    person = context().principal
    tenant = store.get_tenant(person.tenant_id)
    if not tenant or tenant.get("status") != "active":
        raise ApiError("TENANT_DISABLED")
    return SystemMetadata(
        product_name=BRAND["product_name"],
        product_name_en=BRAND["product_name_en"],
        version=APP_VERSION,
        runtime_mode="windows" if os.name == "nt" else "server",
        control=summary("control", store._config()["database"]),
        workspace=summary("tenant", tenant["db_name"]),
    )


class UpgradeBackup(StrictModel):
    created_at: str
    control_version: int
    verified: bool


class BackupList(StrictModel):
    items: list[UpgradeBackup]


@router.get("/backups", response_model=BackupList)
def backups() -> BackupList:
    require_admin()
    root = backup.backup_root()
    reject_reparse_points(root)
    items = []
    for folder in sorted(root.glob("upgrade-*"), reverse=True):
        try:
            reject_reparse_points(folder)
            reject_reparse_points(folder / "manifest.json")
            if not folder.is_dir():
                continue
            # First restrict ownership; the full verifier checks all bound files,
            # trusted schema and archives before declaring a complete backup.
            if (folder / "manifest.json").stat().st_size > 1024 * 1024:
                continue
            raw = (folder / "manifest.json").read_bytes()
            manifest = json.loads(raw)
            if not isinstance(manifest, dict):
                continue
            if manifest.get("control_database") != store._config()["database"]:
                continue
            verified, snapshots = backup.verify_upgrade_backup(folder)
            history = snapshots[verified["control_database"]]["tables"].get("schema_migrations", [])
            items.append(
                UpgradeBackup(
                    created_at=str(verified.get("created_at", "")), control_version=len(history), verified=True
                )
            )
        except (OSError, ValueError, RuntimeError):
            continue
    return BackupList(items=items)
