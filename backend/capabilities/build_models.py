"""Typed read contracts keep legacy extension fields while documenting business data."""

from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, JsonValue


def empty_legacy_text(value: object) -> object:
    """Only historical null text is normalized; other invalid values still fail."""
    return "" if value is None else value


ReadText = Annotated[str, BeforeValidator(empty_legacy_text)]


def legacy_source_refs(value: object) -> object:
    """Expose old free-text sources as readable, unverified excerpts without writing back."""
    if isinstance(value, (str, dict)):
        value = [value]
    if isinstance(value, list):
        return [{"excerpt": item} if isinstance(item, str) else item for item in value]
    return value


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
    progress: dict[str, int] | None = Field(default_factory=dict)
    used_model: int = 0


class RecipeResult(ReadModel):
    data: Recipe


class LegacyEvidence(ReadModel):
    """Read legacy dataset/import references without asserting a verified anchor."""

    id: str | None = None
    kind: str | None = None
    title: str | None = None
    material_id: str | None = None
    anchor_id: str | None = None
    source_version: str | int | None = None
    quote: str | None = None
    start: int | None = None
    end: int | None = None
    locator: str | None = None
    href: str | None = None
    chunk_id: str | None = None
    excerpt: str | None = None


LegacySourceRefs = Annotated[list[LegacyEvidence] | None, BeforeValidator(legacy_source_refs)]


class EntityPayload(ReadModel):
    # Legacy editors and importers intentionally preserve structured JSON values.
    # This read schema must describe them; write/anchor verification stays separate.
    content: JsonValue = None
    definition: JsonValue = None
    conditions: JsonValue = None
    source_refs: LegacySourceRefs = Field(default_factory=list)


class ReviewEntity(ReadModel):
    id: str
    title: str
    summary: ReadText
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
    source_refs: LegacySourceRefs


class ReviewResult(ReadModel):
    data: Review


class ReviewListData(ReadModel):
    items: list[Review]
    total: int


class ReviewListResult(ReadModel):
    data: ReviewListData
