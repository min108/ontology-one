"""Shared org management capability."""

from backend.api.v2.orgs import OrgManageInput
from backend.api.v2.users import MutationOutput
from backend.capabilities.context import CapabilityContext
from backend.capabilities.management import management
from backend.security import directory


@management("org.manage", "管理机构目录", "admin.org", OrgManageInput)
def manage_org(ctx: CapabilityContext, body: OrgManageInput) -> MutationOutput:
    return MutationOutput(id=directory.manage(ctx.principal, body))
