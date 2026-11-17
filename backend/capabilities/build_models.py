"""Typed read contracts keep legacy extension fields while documenting business data."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ReadModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class Evidence(ReadModel):
    material_id: str
    anchor_id: str
    source_version: str
    quote: str
    start: int = 0
    end: int = 0
    locator: str = ""


class Candidate(ReadModel):
    id: str
    kind: Literal["rule", "data", "knowledge", "skill"]
    title: str
    content: str = ""


class ItemValidation(ReadModel):
    level: str
    issues: list[str] = Field(default_factory=list)


class ProposalItem(ReadModel):
    id: str
    tier: Literal["sure", "check", "doubt"]
    decision: str
    one_liner: str
    rationale: str
    content_origin: str
    after_data: Candidate
    evidence: list[Evidence]
    validation: ItemValidation
    duplicate_of: str | None = None
    applied_entity_id: str | None = None
    processed_elsewhere: bool = False


class Proposal(ReadModel):
    can_submit: bool
    id: str
    title: str
    summary: str
    status: str
    items: list[ProposalItem]
    source_text: str
    source_material_id: str


class ProposalResult(ReadModel):
    data: Proposal


class Recipe(ReadModel):
    id: str
    title: str
    status: str
    stage_message: str
    changeset_id: str | None = None
    progress: dict[str, int] = Field(default_factory=dict)
    used_model: int = 0


class RecipeResult(ReadModel):
    data: Recipe


class EntityPayload(ReadModel):
    content: str | None = None
    definition: str | None = None
    conditions: list[str] = Field(default_factory=list)
    source_refs: list[Evidence] = Field(default_factory=list)


class ReviewEntity(ReadModel):
    id: str
    title: str
    summary: str
    kind: str
    status: str
    version: int
    payload: EntityPayload


class Review(ReadModel):
    entity: ReviewEntity
    submitter: str
    submitted_at: str
    can_decide: bool
    blocked_reason: str
    source_refs: list[Evidence]


class ReviewResult(ReadModel):
    data: Review


class ReviewListData(ReadModel):
    items: list[Review]
    total: int


class ReviewListResult(ReadModel):
    data: ReviewListData
