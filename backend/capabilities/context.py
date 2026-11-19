"""Trusted context supplied by bindings, never parsed from a request body."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from pydantic import Field

from backend.core.errors import ApiError, StrictModel
from backend.db import Connection, connect
from backend.security.context import Principal


def ignore_progress(stage: str, message: str) -> None:
    """Default progress sink for synchronous REST requests."""


@dataclass(frozen=True, slots=True)
class CapabilityContext:
    principal: Principal
    binding: Literal["rest", "tool", "mcp", "recipe", "offline"] = "rest"
    run_id: str | None = None
    idempotency_key: str | None = None
    emit: Callable[[str, str], None] = ignore_progress
    connection_factory: Callable[[], Connection] = connect
    permissions: frozenset[str] = field(default_factory=frozenset)
    assistant: str = ""
    transaction: Connection | None = None

    def require_transaction(self) -> Connection:
        if self.transaction is None:
            raise RuntimeError("Capability requires a gateway transaction")
        return self.transaction


class TenantTarget(StrictModel):
    """Optional target identity for resources whose identifier is shared across institutions."""

    tenant_id: str | None = Field(default=None, min_length=1, max_length=64)

    @staticmethod
    def require_local(principal: Principal, tenant_id: object) -> None:
        # The authenticated session selects the database. This value can only narrow a request.
        if (principal.readonly and principal.tenant_id != principal.home_tenant_id) or (
            isinstance(tenant_id, str) and tenant_id != principal.tenant_id
        ):
            raise ApiError("NOT_FOUND")
