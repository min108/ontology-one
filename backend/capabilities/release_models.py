"""Public settings, promotion, and release contracts."""

from typing import Annotated, Literal, Self

from pydantic import Field, JsonValue, StrictBool, model_validator

from backend.capabilities.context import TenantTarget
from backend.core.errors import StrictModel

ModuleId = Annotated[str, Field(min_length=1, max_length=64)]
ReleaseId = Annotated[str, Field(min_length=1, max_length=64)]
EntityId = Annotated[str, Field(min_length=1, max_length=191)]
Version = Annotated[str, Field(max_length=20, pattern=r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")]


class ModuleSave(TenantTarget):
    module_id: ModuleId
    name: str | None = Field(default=None, min_length=1, max_length=255)
    domain_code: str | None = Field(default=None, min_length=1, max_length=64)
    owner_user_id: str | None = Field(default=None, min_length=1, max_length=64)
    scope_note: str | None = Field(default=None, max_length=4000)
    preferences: dict[str, JsonValue] | None = None
    register_dependencies: StrictBool = False
    expected_updated_at: str | None = Field(default=None, min_length=1, max_length=40)


class ModuleResult(StrictModel):
    id: str
    module_key: str
    module_type: Literal["org_common", "scenario", "group_core"]
    name: str
    domain_code: str
    owner_user_id: str | None
    scope_note: str | None
    status: Literal["active", "deprecated", "withdrawn"]
    sharing_level: int = Field(ge=0, le=3)
    preferences: dict[str, JsonValue]
    current_release_id: str | None
    created_by: str
    created_at: str
    updated_at: str


class ModuleMove(TenantTarget):
    source_module_id: ModuleId
    target_module_id: ModuleId
    entity_ids: list[EntityId] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def distinct_ids(self) -> Self:
        if len(set(self.entity_ids)) != len(self.entity_ids) or self.source_module_id == self.target_module_id:
            raise ValueError("请选择不同的归属，并核对内容编号。")
        return self


class ModuleMoveResult(StrictModel):
    source_module_id: str
    target_module_id: str
    entity_ids: list[str]
    moved_count: int = Field(ge=1)


class ReleaseCreate(TenantTarget):
    module_id: ModuleId = "MOD-DEFAULT"
    version: Version | None = None
    changelog: str = Field(default="", max_length=4000)


class ReleaseTarget(TenantTarget):
    release_id: ReleaseId


class ReleaseSubmit(ReleaseTarget):
    version: Version | None = None


class ReleasePublish(ReleaseTarget):
    background: StrictBool = False


class ReleaseSummary(StrictModel):
    id: str
    module_id: str
    version: str
    status: Literal["draft", "candidate", "published", "withdrawn"]
    bump: Literal["major", "minor", "patch"]
    changelog: str
    manifest_hash: str
    created_by: str
    reviewed_by: str | None
    submitted_by: str | None
    created_at: str
    published_at: str | None
    current_release_id: str | None
    suggested_version: str
    previous_release_id: str | None
    member_count: int = Field(ge=0)
    ref_count: int = Field(ge=0)
    no_changes: bool
    missing_dependencies: list[str]


class ReleaseDiff(StrictModel):
    added: list[str]
    changed: list[str]
    removed: list[str]
    promoted: list[str]


class ReleaseItem(StrictModel):
    entity_id: str
    entity_version: int
    kind: str
    snapshot: dict[str, JsonValue]
    content_hash: str
    item_role: Literal["member", "ref"]


class ReleaseCheck(StrictModel):
    code: str
    label: str
    status: Literal["pass", "fail", "waived", "not_applicable"]
    reason: str


class ReleaseGate(StrictModel):
    entity_id: str
    passed: bool
    blocking: bool
    checks: list[ReleaseCheck]


class ReleaseDetail(ReleaseSummary):
    diff: ReleaseDiff
    items: list[ReleaseItem]
    gates: list[ReleaseGate]
    truncated: bool = False
    detail_url: str | None = None
    omitted_counts: dict[str, int] = Field(default_factory=dict)


class ReleasePublication(ReleaseDetail):
    job_id: str | None = None


class ReleaseList(StrictModel):
    items: list[ReleaseSummary]
    total: int = Field(ge=0)
