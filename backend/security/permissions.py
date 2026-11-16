"""The sole permission-key calculator. Scope objects are built by server services."""

from dataclasses import dataclass

from fastapi import HTTPException

from backend.security import store
from backend.security.context import Principal

ROLES = frozenset({"admin", "modeler", "reviewer", "analyst", "viewer"})
COMMON = frozenset({"agent.analyst.use", "artifact.create", "artifact.read", "feedback.create"})
BUILD = frozenset({"build.edit", "build.submit", "agent.builder.use"})
ADMIN = frozenset(
    {
        "module.manage",
        "scenario.manage",
        "scenario.build",
        "scenario.contribute",
        "agent.scenario.use",
        "source.manage",
        "admin.users",
        "admin.model",
        "admin.demo",
        "admin.settings",
    }
)
ALL_PERMISSIONS = (
    COMMON
    | BUILD
    | ADMIN
    | frozenset(
        {
            "source.submit",
            "scenario.propose",
            "review.decide",
            "draft.read",
            "data.restricted.read",
            "audit.read",
            "admin.org",
            "federation.steward",
            "federation.read",
        }
    )
)


@dataclass(frozen=True, slots=True)
class PermissionScope:
    tenant_id: str
    scenario_id: str = ""
    membership: str = ""
    module_owner_id: str = ""
    restricted_read: bool = False


def permissions_for(principal: Principal, scope: PermissionScope | None = None) -> set[str]:
    if principal.role not in ROLES or principal.identity_kind != "session":
        return set()
    result = set(COMMON)
    if principal.role != "viewer":
        result.update({"source.submit", "scenario.propose"})
    if principal.role in {"admin", "modeler"}:
        result.update(BUILD)
    if principal.role in {"admin", "reviewer"}:
        result.add("review.decide")
    if principal.role in {"admin", "modeler", "reviewer"}:
        result.add("draft.read")
    if principal.role == "admin":
        result.update(ADMIN)
        if principal.home_tenant_id == "hq" and principal.is_superadmin:
            result.update({"audit.read", "admin.org"})
    if principal.home_tenant_id == "hq" and "group_steward" in principal.duties:
        result.add("federation.steward")
    try:
        grants = store.authorized_read_tenants(principal)
    except HTTPException:
        grants = []
    if any(row["id"] != principal.home_tenant_id for row in grants):
        result.add("federation.read")
    if scope is not None and scope.tenant_id == principal.tenant_id:
        if scope.restricted_read and principal.role != "viewer":
            result.add("data.restricted.read")
        if not scope.scenario_id and scope.module_owner_id == principal.user_id and principal.role == "modeler":
            result.add("module.manage")
        if scope.scenario_id and scope.membership in {"owner", "builder", "contributor"}:
            result.add("scenario.contribute")
            if principal.role != "viewer" and scope.membership in {"owner", "builder"}:
                result.update({"scenario.build", "agent.scenario.use"})
                if scope.membership == "owner":
                    result.add("scenario.manage")
    if principal.readonly:
        result.intersection_update({"agent.analyst.use", "artifact.read", "federation.read"})
    return result
