"""Shared grant management capability."""

from backend.api.v2.grants import GrantManageInput
from backend.api.v2.users import MutationOutput
from backend.capabilities.context import CapabilityContext
from backend.capabilities.management import management
from backend.security import store


@management("grant.manage", "管理跨机构授权", "admin.org", GrantManageInput)
def manage_grant(ctx: CapabilityContext, body: GrantManageInput) -> MutationOutput:
    if body.action == "set":
        ident = store.grant_access(ctx.principal, **body.grant.model_dump())
    else:
        ident = body.grant_id
        store.revoke_grant(ctx.principal, ident)
    return MutationOutput(id=ident)
