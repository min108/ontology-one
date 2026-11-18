"""Catalog response schemas preserve existing dictionaries and safe extension fields."""

from typing import Annotated, NotRequired

from pydantic import BeforeValidator, ConfigDict, JsonValue
from typing_extensions import TypedDict

from backend.capabilities.build_models import ReadText, legacy_source_refs
from backend.capabilities.gate_models import GateResult
from backend.core.errors import StrictModel


def legacy_payload(value: object) -> object:
    if isinstance(value, dict) and "source_refs" in value:
        return {**value, "source_refs": legacy_source_refs(value["source_refs"])}
    return value


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
    detail: ReadText
    created_at: str


class ElementEntity(TypedDict):
    __pydantic_config__ = ConfigDict(extra="allow")

    id: str
    title: str
    kind: str
    status: str
    version: int
    domain: str | None
    payload: Annotated[dict[str, JsonValue], BeforeValidator(legacy_payload)]
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
    text: NotRequired[str]
    header: NotRequired[dict[str, JsonValue]]
    rules: NotRequired[list[str]]
    sources: NotRequired[list[str]]


class ApplicabilityOption(TypedDict):
    id: str
    title: str
    kind: str


class ElementDetail(TypedDict):
    __pydantic_config__ = ConfigDict(extra="allow")

    entity: ElementEntity
    card: ElementCard
    versions: list[ElementVersion]
    can_edit: bool
    gates: NotRequired[GateResult]
    applicability_options: NotRequired[list[ApplicabilityOption]]


class ElementDetailResult(StrictModel):
    data: ElementDetail
