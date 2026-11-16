"""Shared user management capability."""

from backend.api.v2.users import MutationOutput, UserManageInput
from backend.capabilities.context import CapabilityContext
from backend.capabilities.management import management
from backend.security import store


@management("user.manage", "管理账号", "admin.users", UserManageInput)
def manage_user(ctx: CapabilityContext, body: UserManageInput) -> MutationOutput:
    if body.action == "create":
        ident = store.create_user(ctx.principal, **body.create.model_dump())
    else:
        ident = body.user_id
        store.update_user(ctx.principal, ident, body.changes.model_dump(exclude_none=True, exclude_unset=True))
    return MutationOutput(id=ident)
