"""Explicit cross-organization grants; hierarchy never implies access."""

from typing import Literal, Self

from fastapi import APIRouter
from pydantic import Field, model_validator

from backend.api.v2.models import ListResponse
from backend.core.errors import StrictModel
from backend.identity import GrantCreate
from backend.security import store
from backend.security.context import require_context

router = APIRouter()


class Grant(StrictModel):
    id: str
    user_id: str
    tenant_id: str
    access: Literal["read_published", "read_private", "write"]
    reason: str
    created_by: str
    created_at: str
    username: str
    tenant_name: str


class GrantManageInput(StrictModel):
    action: Literal["set", "revoke"]
    grant_id: str = Field(default="", max_length=64)
    grant: GrantCreate | None = None

    @model_validator(mode="after")
    def valid_operation(self) -> Self:
        if (self.action == "set" and (self.grant is None or self.grant_id)) or (
            self.action == "revoke" and (not self.grant_id or self.grant is not None)
        ):
            raise ValueError("授权操作与输入不一致。")
        return self


@router.get("/grants", response_model=ListResponse[Grant])
def grants() -> ListResponse[Grant]:
    items = store.list_grants(require_context())
    return ListResponse[Grant](items=items, total=len(items), page_size=max(1, min(200, len(items))))
