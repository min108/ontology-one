"""Scoped export descriptor and exclusively offline restoration binding."""

import zipfile
from urllib.parse import urlencode

from pydantic import Field

from backend.capabilities.context import CapabilityContext
from backend.capabilities.read import EmptyInput
from backend.capabilities.registry import capability
from backend.core.errors import ApiError, NextAction, StrictModel
from backend.maintenance import require_maintenance
from backend.version import APP_VERSION


class ExportOutput(StrictModel):
    download_url: str
    filename: str


class RestoreInput(StrictModel):
    archive: str = Field(default="", max_length=4096)
    reason: str = Field(default="", max_length=500)


class RestoreOutput(StrictModel):
    tenant_id: str
    rows: int
    files: int


@capability(
    id="backup.export",
    version="1.0",
    title="导出工作区备份",
    summary="生成当前机构的备份下载入口，下载时再次核验身份和机构。",
    domain="backup",
    intent="admin",
    effect="read",
    confirm="L3",
    permissions=["admin.settings"],
    assistants=[],
    mcp=False,
    input=EmptyInput,
    output=ExportOutput,
    preconditions=[],
    idempotent=True,
    audit="metadata",
    limits={"timeout_s": 30, "max_result_bytes": 4096},
)
def export(ctx: CapabilityContext, body: EmptyInput) -> ExportOutput:
    # Deferred binary binding: no archive or ticket is retained between requests.
    return ExportOutput(
        download_url="/api/v2/system/backup?" + urlencode({"workspace_id": ctx.principal.tenant_id}),
        filename=f"Ontology-One-{APP_VERSION}-机构备份.zip",
    )


@capability(
    id="backup.restore",
    version="1.0",
    title="离线恢复工作区",
    summary="停止共享存储上的全部服务后，由持有独占维护锁的离线程序恢复同机构空工作区。",
    domain="backup",
    intent="admin",
    effect="state_change",
    confirm="L3",
    permissions=["admin.settings"],
    assistants=[],
    mcp=False,
    input=RestoreInput,
    output=RestoreOutput,
    preconditions=[],
    idempotent=False,
    audit="metadata",
    limits={"timeout_s": 86400, "max_result_bytes": 4096},
)
def restore(ctx: CapabilityContext, body: RestoreInput) -> RestoreOutput:
    if ctx.binding != "offline":
        raise ApiError(
            "PRECONDITION_FAILED",
            "恢复只能离线执行。请停止共享存储上的全部平台与任务进程，再按备份与升级页的指引恢复。",
            next_actions=[NextAction(label="查看离线恢复说明", href="/admin/backup")],
        )
    try:
        require_maintenance()
    except RuntimeError:
        raise ApiError("MAINTENANCE_LOCKED") from None
    if not body.archive or not 2 <= len(body.reason.strip()) <= 500:
        raise ApiError("VALIDATION_FAILED")
    # The binding is assigned by trusted local code, never by JSON input. The
    # original validator and restore function enforce ownership/empty workspace.
    from scripts import restore_backup

    with zipfile.ZipFile(body.archive) as archive:
        restore_backup.validate_archive(archive)
        with archive.open(restore_backup.SNAPSHOT) as stream:
            snapshot = restore_backup._load_json(stream)
        restore_backup.validate_snapshot(snapshot, ctx.principal.tenant_id)
        return RestoreOutput.model_validate(
            restore_backup.restore_snapshot(snapshot, archive, ctx.principal, body.reason.strip())
        )
