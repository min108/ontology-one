"""Directory management contracts expose stable identifiers, never database names."""

from typing import Literal, Self

from fastapi import APIRouter
from pydantic import Field, model_validator

from backend.api.v2.models import ListResponse
from backend.core.errors import StrictModel
from backend.security.context import require_context

router = APIRouter()
Category = Literal["headquarters", "resident", "headquarters_department", "subsidiary"]


class Organization(StrictModel):
    id: str
    name: str
    category: Category
    org_id: str | None
    status: str
    parent_id: str | None = None
    short_name: str = ""
    org_code: str | None = None
    sort_order: int = 0
    level: int = 0
    is_demo: bool = False
    mirror_of: str | None = None
    disabled_at: str | None = None
    disabled_reason: str = ""


class OrgManageInput(StrictModel):
    action: Literal["create", "update", "disable", "enable"]
    org_id: str = Field(default="", max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    category: Category | None = None
    parent_id: str | None = Field(default=None, max_length=64)
    short_name: str | None = Field(default=None, max_length=120)
    org_code: str | None = Field(default=None, max_length=120)
    sort_order: int | None = Field(default=None, ge=0, le=1000000)
    reason: str | None = Field(default=None, min_length=2, max_length=500)

    @model_validator(mode="after")
    def valid_operation(self) -> Self:
        if self.action == "create" and (not self.name or not self.name.strip() or self.category is None or self.org_id):
            raise ValueError("新增机构须填写名称与类别。")
        if self.action != "create" and not self.org_id:
            raise ValueError("请选择机构。")
        if self.action in {"enable", "disable"} and self.model_fields_set - {"action", "org_id", "reason"}:
            raise ValueError("启停机构不接受其他变更。")
        return self


@router.get("/orgs", response_model=ListResponse[Organization])
def orgs() -> ListResponse[Organization]:
    from backend.security.directory import visible_directory

    items = visible_directory(require_context())
    return ListResponse[Organization](items=items, total=len(items), page_size=max(1, min(200, len(items))))
