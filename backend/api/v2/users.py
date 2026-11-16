"""Account reads and mutation contracts shared with the existing identity service."""

from typing import Literal, Self

from fastapi import APIRouter
from pydantic import Field, model_validator

from backend.api.v2.models import ListResponse
from backend.core.errors import StrictModel
from backend.identity import UserCreate, UserUpdate
from backend.security import store
from backend.security.context import require_context

router = APIRouter()


class MutationOutput(StrictModel):
    id: str
    ok: Literal[True] = True


class User(StrictModel):
    id: str
    username: str
    display_name: str
    home_tenant_id: str
    role: Literal["admin", "modeler", "reviewer"]
    is_active: bool
    is_superadmin: bool
    must_change_password: Literal[False] = False
    auth_version: int
    tenant_name: str


class UserManageInput(StrictModel):
    action: Literal["create", "update"]
    user_id: str = Field(default="", max_length=64)
    create: UserCreate | None = None
    changes: UserUpdate | None = None

    @model_validator(mode="after")
    def valid_operation(self) -> Self:
        if self.action == "create":
            valid = self.create is not None and not self.user_id and self.changes is None
        else:
            valid = (
                bool(self.user_id)
                and self.create is None
                and self.changes is not None
                and bool(self.changes.model_dump(exclude_unset=True, exclude_none=True))
            )
        if not valid:
            raise ValueError("账号操作与输入不一致。")
        return self


@router.get("/users", response_model=ListResponse[User])
def users() -> ListResponse[User]:
    items = store.list_users(require_context())
    return ListResponse[User](items=items, total=len(items), page_size=max(1, min(200, len(items))))
