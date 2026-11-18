"""Typed read contracts keep legacy extension fields while documenting business data."""

from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, JsonValue

from backend.capabilities.gate_models import GateResult


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


class FieldProvenance(ReadModel):
    origin: str
    quote: str = ""
    actor_id: str = ""
    confirmed_by: str = ""
    confirmed_at: str = ""
    anchor_id: str = ""
    source_version: str = ""


class DocumentFact(FieldProvenance):
    value: str = ""


class DocumentFacts(ReadModel):
    valid_from: DocumentFact
    scope: DocumentFact


class ExtractionSummary(ReadModel):
    retried_batches: int = 0
    discarded_candidates: int = 0
    accepted_candidates: int = 0
    failed_batches: int = 0
    fully_local: bool = True


class ProposalApplicabilityOption(ReadModel):
    id: str
    title: str
    kind: str


class Handoff(ReadModel):
    href: str
    label: str


class Candidate(ReadModel):
    id: str
    kind: Literal["rule", "data", "knowledge", "skill"]
    title: str
    content: str = ""
    definition: str = ""
    scope: str = ""
    conditions: list[str] = Field(default_factory=list)
    exceptions: str = ""
    valid_from: str = ""
    valid_to: str = ""
    unit: str = ""
    grain: str = ""
    boundary: str = ""
    judgement: dict[str, JsonValue] | None = None
    applies_to_id: str = ""
    applies_to_title: str = ""
    field_provenance: dict[str, FieldProvenance] = Field(default_factory=dict)
    waiver_applications: dict[str, str] = Field(default_factory=dict)
    bulk_confirmation_blocked: bool = False


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
    applied_version: int | None = None
    applied_status: str | None = None
    processed_elsewhere: bool = False
    gates: GateResult | None = None
    ready_for_bulk_accept: bool = False
    can_submit: bool = False
    submit_blockers: list[str] = Field(default_factory=list)
    handoff: Handoff | None = None


class Proposal(ReadModel):
    can_submit: bool
    id: str
    title: str
    summary: str
    status: str
    items: list[ProposalItem]
    source_text: str
    source_material_id: str
    submit_blockers: list[str] = Field(default_factory=list)
    document_facts: DocumentFacts = Field(
        default_factory=lambda: DocumentFacts(
            valid_from=DocumentFact(origin="document"), scope=DocumentFact(origin="document")
        )
    )
    extraction_summary: ExtractionSummary = Field(default_factory=ExtractionSummary)
    applicability_options: list[ProposalApplicabilityOption] = Field(default_factory=list)


class ProposalResult(ReadModel):
    data: Proposal


class RemainingItem(ReadModel):
    item_id: str
    title: str
    reasons: list[str]


class ApplyData(ReadModel):
    id: str
    entity_ids: list[str]
    submitted: bool
    submitted_entity_ids: list[str] = Field(default_factory=list)
    submitted_count: int = 0
    remaining_items: list[RemainingItem] = Field(default_factory=list)
    remaining_count: int = 0


class ApplyResult(ReadModel):
    data: ApplyData


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
    gates: GateResult | None = None


class ReviewResult(ReadModel):
    data: Review


class ReviewListData(ReadModel):
    items: list[Review]
    total: int


class ReviewListResult(ReadModel):
    data: ReviewListData
