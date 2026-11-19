"""Settings, promotion and release actions share the live tenant and review workflow."""

from collections.abc import Callable
from contextlib import closing

from pydantic import BaseModel

from backend.capabilities.context import CapabilityContext
from backend.capabilities.registry import capability
from backend.capabilities.release_models import (
    ModuleMove,
    ModuleMoveResult,
    ModuleResult,
    ModuleSave,
    ReleaseCreate,
    ReleaseDetail,
    ReleaseDiff,
    ReleasePublication,
    ReleasePublish,
    ReleaseSubmit,
    ReleaseTarget,
)
from backend.core.errors import ApiError, StrictModel
from backend.security.permissions import PermissionScope

RECEIPT_BYTES = 8 * 1024 * 1024


def _receipt[T: ReleaseDetail](model: type[T], value: dict) -> T:
    detail = model.model_validate(value)
    if len(detail.model_dump_json().encode()) <= RECEIPT_BYTES:
        return detail
    # The full detail remains available from the tenant-authorized read endpoint.
    counts = {"items": len(detail.items), "gates": len(detail.gates)}
    result = detail.model_copy(
        update={
            "truncated": True,
            "detail_url": f"/api/v2/releases/{detail.id}",
            "omitted_counts": counts,
            "items": [],
            "gates": [],
        }
    )
    if len(result.model_dump_json().encode()) <= RECEIPT_BYTES:
        return result
    counts = {
        **counts,
        "missing_dependencies": len(detail.missing_dependencies),
        **{"diff." + key: len(ids) for key, ids in detail.diff.model_dump().items()},
    }
    return result.model_copy(
        update={
            "diff": ReleaseDiff(added=[], changed=[], removed=[], promoted=[]),
            "missing_dependencies": [],
            "omitted_counts": counts,
        }
    )


def _owner_for_target(ctx: CapabilityContext, body: BaseModel) -> str:
    if isinstance(body, ReleaseSubmit):
        query = "SELECT m.owner_user_id FROM ont_modules m JOIN ont_releases r ON r.module_id=m.id WHERE r.id=?"
        ident = body.release_id
    else:
        query = "SELECT owner_user_id FROM ont_modules WHERE id=?"
        if isinstance(body, ModuleMove):
            # Promotion is a decision of the public part's owner.
            ident = "MOD-DEFAULT"
        elif isinstance(body, (ModuleSave, ReleaseCreate)):
            ident = body.module_id
        else:
            raise ApiError("PERMISSION_DENIED")
    with closing(ctx.connection_factory()) as conn:
        row = conn.execute(query, (ident,)).fetchone()
    if row is None:
        raise ApiError("NOT_FOUND")
    return row["owner_user_id"] or ""


def _owner_scope(ctx: CapabilityContext, body: BaseModel) -> PermissionScope:
    return PermissionScope(ctx.principal.tenant_id, module_owner_id=_owner_for_target(ctx, body))


def _action(
    ident: str, title: str, model: type[StrictModel], output: type[StrictModel], level: str, *, builder: bool = False
) -> Callable:
    review = level == "L3"
    return capability(
        id=ident,
        version="1.0",
        title=title,
        summary=title + "，按当前机构的分工办理。",
        domain=ident.split(".")[0],
        intent="govern" if review else "build",
        effect="write_draft" if level == "L1" else "state_change",
        confirm=level,
        permissions=["review.decide" if review else "module.manage"],
        owner_scope=None if review else _owner_scope,
        transactional=True,
        assistants=["builder"] if builder else [],
        mcp=False,
        input=model,
        output=output,
        preconditions=[],
        idempotent=False,
        audit="metadata",
        limits={"timeout_s": 120, "max_result_bytes": RECEIPT_BYTES},
    )


@_action("module.save", "保存本体设置", ModuleSave, ModuleResult, "L1", builder=True)
def save(ctx: CapabilityContext, body: ModuleSave) -> ModuleResult:
    from backend.kernel import modules

    changes = body.model_dump(exclude={"module_id", "tenant_id"}, exclude_unset=True)
    conn = ctx.require_transaction()
    return ModuleResult.model_validate(modules.save(conn, ctx.principal, body.module_id, changes))


@_action("module.move_elements", "调整内容归属", ModuleMove, ModuleMoveResult, "L2")
def move(ctx: CapabilityContext, body: ModuleMove) -> ModuleMoveResult:
    from backend.kernel import modules

    conn = ctx.require_transaction()
    return ModuleMoveResult.model_validate(
        modules.move_elements(conn, ctx.principal, body.source_module_id, body.target_module_id, body.entity_ids)
    )


@_action("release.create_draft", "创建发布版本草稿", ReleaseCreate, ReleaseDetail, "L1", builder=True)
def create(ctx: CapabilityContext, body: ReleaseCreate) -> ReleaseDetail:
    from backend.kernel import releases

    conn = ctx.require_transaction()
    return _receipt(
        ReleaseDetail,
        releases.create_draft(conn, ctx.principal, body.module_id, version=body.version, changelog=body.changelog),
    )


@_action("release.submit", "提交发布版本审核", ReleaseSubmit, ReleaseDetail, "L2", builder=True)
def submit(ctx: CapabilityContext, body: ReleaseSubmit) -> ReleaseDetail:
    from backend.kernel import releases

    conn = ctx.require_transaction()
    return _receipt(ReleaseDetail, releases.submit(conn, ctx.principal, body.release_id, version=body.version))


@_action("release.publish", "审核并发布版本", ReleasePublish, ReleasePublication, "L3")
def publish(ctx: CapabilityContext, body: ReleasePublish) -> ReleasePublication:
    from backend.infra.release_snapshot import enqueue_snapshot
    from backend.kernel import releases

    conn = ctx.require_transaction()
    if body.background:
        return _receipt(ReleasePublication, enqueue_snapshot(conn, ctx.principal, body.release_id))
    return _receipt(ReleasePublication, releases.publish(conn, ctx.principal, body.release_id))


@_action("release.withdraw", "撤回发布版本", ReleaseTarget, ReleaseDetail, "L3")
def withdraw(ctx: CapabilityContext, body: ReleaseTarget) -> ReleaseDetail:
    from backend.kernel import releases

    conn = ctx.require_transaction()
    return _receipt(ReleaseDetail, releases.withdraw(conn, ctx.principal, body.release_id))
