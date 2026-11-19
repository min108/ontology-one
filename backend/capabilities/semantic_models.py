"""Read-only semantic service requests and business projections."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from backend.capabilities.context import TenantTarget
from backend.core.errors import StrictModel

ResourceId = Annotated[str, Field(min_length=1, max_length=191)]
Family = Literal[
    "",
    "structure",
    "provenance",
    "input",
    "formation",
    "application",
    "interpretation",
    "organization",
    "curation",
    "evidence",
    "reference",
    "evolution",
]


class ResolveInput(TenantTarget):
    text: str = Field(min_length=1, max_length=500)
    expected_kind: str = Field(default="", max_length=64)
    limit: int = Field(default=10, ge=1, le=30, strict=True)


class SemanticTarget(TenantTarget):
    entity_id: ResourceId


class NeighborInput(SemanticTarget):
    family: Family = ""
    depth: int = Field(default=1, ge=1, le=2, strict=True)


class TraceInput(TenantTarget):
    entity_id: ResourceId | None = None
    relation_id: ResourceId | None = None
    receipt_id: ResourceId | None = None
    chunk_id: Annotated[str, Field(min_length=1, max_length=255)] | None = None
    anchor_id: ResourceId | None = None
    source_version: ResourceId | None = None

    @model_validator(mode="after")
    def one_target(self) -> Self:
        if sum(value is not None for value in (self.entity_id, self.relation_id, self.receipt_id)) != 1:
            raise ValueError("请指定一个要素、关联或查询回执。")
        if (self.chunk_id or self.anchor_id or self.source_version) and not (
            self.entity_id and (self.chunk_id or self.anchor_id)
        ):
            raise ValueError("原文定位须指定材料和原文片段。")
        return self


class SemanticNode(StrictModel):
    id: str
    title: str
    kind: str
    status: str
    version: int


class ResolvedNode(SemanticNode):
    match: Literal["id", "alias", "title", "prefix", "partial"]
    confidence: Literal["high", "medium", "low"]


class ResolveResult(StrictModel):
    items: list[ResolvedNode]


class SemanticEdge(StrictModel):
    id: str
    source: str
    target: str
    type: str
    label: str
    status: str
    family: str
    family_label: str
    original_source: str
    original_target: str
    reading: str


class NeighborResult(StrictModel):
    entity_id: str
    nodes: list[SemanticNode]
    relations: list[SemanticEdge]
    truncated: bool


class AffectedNode(SemanticNode):
    path: list[SemanticEdge]


class DependentPart(StrictModel):
    id: str
    name: str
    min_version: str | None


class ImpactResult(StrictModel):
    entity_id: str
    direct: list[AffectedNode]
    indirect: list[AffectedNode]
    dependent_modules: list[DependentPart]
    truncated: bool


class TraceStep(StrictModel):
    kind: str
    id: str
    label: str


class TraceSource(StrictModel):
    material_id: str
    title: str
    source_version: str
    version: int
    anchor_id: str
    start: int
    end: int
    quote: str
    locator: str
    href: str
    path: list[TraceStep]


class QueryEvidence(StrictModel):
    source_id: str
    mapping_id: str
    mapping_version: int
    source_version: int | None
    schema_hash: str
    row_count: int


class TraceResult(StrictModel):
    subject_id: str
    subject_type: Literal["entity", "relation", "receipt"]
    entity_version: int | None = None
    sources: list[TraceSource]
    query: QueryEvidence | None = None
    truncated: bool = False
