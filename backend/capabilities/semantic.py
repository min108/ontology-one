"""Read a deterministic description through the existing authorized detail service."""

from pydantic import Field, JsonValue

from backend.capabilities.context import CapabilityContext
from backend.capabilities.elements import ElementGet, get_element
from backend.capabilities.read import read_capability
from backend.core.errors import StrictModel


class DescribeInput(ElementGet):
    include_structure: bool = False


class DescribeResult(StrictModel):
    entity_id: str
    version: int
    card: dict[str, JsonValue]
    citation: str
    structure: dict[str, JsonValue] | None = Field(default=None)


@read_capability(
    "semantic.describe", "查看业务语言卡", DescribeInput, DescribeResult, assistants=["builder", "analyst"]
)
def describe(ctx: CapabilityContext, body: DescribeInput) -> DescribeResult:
    detail = get_element(ctx, ElementGet(entity_id=body.entity_id)).data
    entity = detail["entity"]
    return DescribeResult(
        entity_id=entity["id"],
        version=entity["version"],
        card=detail["card"],
        citation=f"[[cite:el:{entity['id']}@{entity['version']}]]",
        structure=entity["payload"] if body.include_structure else None,
    )
