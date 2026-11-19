"""Authenticated reads of institution settings and published history."""

from contextlib import closing

from fastapi import APIRouter

from backend import db
from backend.capabilities.context import TenantTarget
from backend.capabilities.release_models import ModuleId, ModuleResult, ReleaseDetail, ReleaseId, ReleaseList
from backend.security.context import require_context

router = APIRouter()


@router.get("/modules/{module_id}", response_model=ModuleResult)
def settings(module_id: ModuleId, tenant_id: str | None = None) -> ModuleResult:
    from backend.kernel import releases

    person = require_context()
    TenantTarget.require_local(person, tenant_id)
    with db.business_readonly(), closing(db.connect()) as conn:
        return ModuleResult.model_validate(releases.readable_module(conn, person, module_id))


@router.get("/modules/{module_id}/releases", response_model=ReleaseList)
def list_releases(module_id: ModuleId, tenant_id: str | None = None) -> ReleaseList:
    from backend.kernel import releases

    person = require_context()
    TenantTarget.require_local(person, tenant_id)
    with db.business_readonly(), closing(db.connect()) as conn:
        items = releases.list_releases(conn, person, module_id)
        return ReleaseList(items=items, total=len(items))


@router.get("/releases/{release_id}", response_model=ReleaseDetail)
def release_detail(release_id: ReleaseId, tenant_id: str | None = None) -> ReleaseDetail:
    from backend.kernel import releases

    person = require_context()
    TenantTarget.require_local(person, tenant_id)
    with db.business_readonly(), closing(db.connect()) as conn:
        return ReleaseDetail.model_validate(releases.get_release(conn, person, release_id))
