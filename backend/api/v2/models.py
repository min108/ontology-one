from pydantic import Field

from backend.core.errors import StrictModel


class ListResponse[T](StrictModel):
    items: list[T]
    total: int = Field(ge=0)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=30, ge=1, le=200)
