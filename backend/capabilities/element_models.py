"""Catalog response schemas preserve existing dictionaries and safe extension fields."""

from typing import NotRequired

from pydantic import ConfigDict, JsonValue
from typing_extensions import TypedDict

from backend.core.errors import StrictModel


class ElementListItem(TypedDict):
    __pydantic_config__ = ConfigDict(extra="allow")

    id: str
    title: str
    kind: str
    status: str
    summary: NotRequired[str | None]
    version: NotRequired[int]
    updated_at: NotRequired[str | None]
    source_title: NotRequired[str]


class ElementListData(TypedDict):
    __pydantic_config__ = ConfigDict(extra="allow")

    items: list[ElementListItem]
    total: int
    page: NotRequired[int]
    page_size: NotRequired[int]


class ElementListResult(StrictModel):
    data: ElementListData


class ElementActivity(TypedDict):
    __pydantic_config__ = ConfigDict(extra="allow")

    id: str | int
    action: str
    detail: str
    created_at: str


class ElementEntity(TypedDict):
    __pydantic_config__ = ConfigDict(extra="allow")

    id: str
    title: str
    kind: str
    status: str
    version: int
    domain: str | None
    payload: dict[str, JsonValue]
    activities: list[ElementActivity]


class ElementVersion(TypedDict):
    __pydantic_config__ = ConfigDict(extra="allow")

    id: str | int
    version: int
    created_at: str
    reason: NotRequired[str | None]
    change_note: NotRequired[str | None]


class ElementCard(TypedDict):
    __pydantic_config__ = ConfigDict(extra="allow")

    one_liner: str
    points: list[str]
    relations: list[str]
    usage: str


class ElementDetail(TypedDict):
    __pydantic_config__ = ConfigDict(extra="allow")

    entity: ElementEntity
    card: ElementCard
    versions: list[ElementVersion]
    can_edit: bool


class ElementDetailResult(StrictModel):
    data: ElementDetail
