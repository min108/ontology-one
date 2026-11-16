"""HQ-only audit read binding; old institution audit remains compatible."""

from datetime import datetime
from typing import Annotated, Self

from fastapi import APIRouter, Query
from pydantic import Field, JsonValue, model_validator

from backend.api.v2.models import ListResponse
from backend.core.errors import StrictModel
from backend.security.context import Principal, require_context

router = APIRouter()


class AuditInput(StrictModel):
    page: int = Field(default=1, ge=1, le=1000000)
    page_size: int = Field(default=30, ge=1, le=100)
    actor_id: str | None = Field(default=None, max_length=64)
    tenant_id: str | None = Field(default=None, max_length=64)
    action: str | None = Field(default=None, max_length=100)
    outcome: str | None = Field(default=None, max_length=32)
    since: datetime | None = None
    until: datetime | None = None

    @model_validator(mode="after")
    def valid_range(self) -> Self:
        if any(value is not None and value.tzinfo is None for value in (self.since, self.until)):
            raise ValueError("时间须包含时区。")
        if self.since and self.until and self.since > self.until:
            raise ValueError("开始时间不能晚于结束时间。")
        return self


class AuditRow(StrictModel):
    id: int
    created_at: str
    actor_id: str | None
    actor_username: str | None
    tenant_id: str | None
    action: str
    resource: str
    outcome: str
    request_id: str
    details: dict[str, JsonValue]


class AuditOutput(ListResponse[AuditRow]):
    pass


def query_audit(principal: Principal, body: AuditInput) -> dict:
    from backend.security import store

    store.require_admin(principal, superadmin=True)
    return store.audit_records(
        principal, body.page, body.page_size, filters=body.model_dump(exclude={"page", "page_size"}, exclude_none=True)
    )


@router.get("/audit", response_model=AuditOutput)
def audit(filters: Annotated[AuditInput, Query()]) -> AuditOutput:
    return AuditOutput.model_validate(query_audit(require_context(), filters))
