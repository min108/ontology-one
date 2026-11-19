"""R2 construction contracts; all callers pass the same permission gateway."""

from collections.abc import Callable
from typing import Literal

from pydantic import Field, JsonValue

from backend.capabilities.context import CapabilityContext
from backend.capabilities.gate_models import GateWaivers
from backend.capabilities.registry import capability
from backend.core.errors import StrictModel
from backend.domains import build


class BuildResult(StrictModel):
    data: dict[str, JsonValue]


class RecipeStart(StrictModel):
    material_id: str = Field(min_length=1, max_length=191)
    recipe: Literal["A"] = "A"


class RecipeStatus(StrictModel):
    run_id: str = Field(min_length=1, max_length=64)


class ChangesetGet(StrictModel):
    changeset_id: str = Field(min_length=1, max_length=64)


class RetainSource(ChangesetGet):
    source_version: str = Field(min_length=1, max_length=191)
    anchor_id: str = Field(min_length=1, max_length=191)


class CandidatePatch(StrictModel):
    expected_version: int | None = Field(default=None, ge=1)
    title: str = Field(min_length=1, max_length=250)
    content: str = Field(min_length=1, max_length=16000)
    kind: Literal["rule", "data", "knowledge", "skill"]
    definition: str = Field(default="", max_length=4000)
    scope: str = Field(default="", max_length=4000)
    conditions: list[str] = Field(default_factory=list, max_length=50)
    exceptions: str = Field(default="", max_length=4000)
    valid_from: str = Field(default="", max_length=40)
    valid_to: str = Field(default="", max_length=40)
    unit: str = Field(default="", max_length=100)
    grain: str = Field(default="", max_length=1000)
    boundary: str = Field(default="", max_length=4000)
    judgement: dict[str, JsonValue] | None = None
    applies_to_id: str = Field(default="", max_length=191)
    waiver_applications: GateWaivers | None = None


class DecideItems(ChangesetGet):
    item_ids: list[str] = Field(min_length=1, max_length=200)
    decision: Literal["accepted", "edited", "rejected", "pending"]
    edit: CandidatePatch | None = None
    only_ready: bool = False
    expected_versions: dict[str, int] = Field(default_factory=dict, max_length=200)


class ConfirmFacts(ChangesetGet):
    valid_from_candidate_id: str | None = Field(default=None, min_length=1, max_length=191)
    valid_from: str | None = Field(default=None, max_length=10)
    confirm_no_exceptions_item_ids: list[str] = Field(default_factory=list, max_length=200)
    confirm_unconditional_item_ids: list[str] = Field(default_factory=list, max_length=200)
    expected_versions: dict[str, int] = Field(default_factory=dict, max_length=200)


def declaration(ident: str, title: str, model: type[StrictModel], permission: str, *, read: bool = False) -> Callable:
    return capability(
        id=ident,
        version="1.0",
        title=title,
        summary=title + "，只处理当前机构的内容。",
        domain=ident.split(".")[0],
        intent="read" if read else "build",
        effect="read" if read else "write_draft",
        confirm="L0" if read else "L2" if ident.endswith("apply_and_submit") else "L1",
        permissions=[permission],
        assistants=["builder"],
        mcp=False,
        input=model,
        output=BuildResult,
        preconditions=[],
        idempotent=read,
        audit="metadata",
        limits={"timeout_s": 90, "max_result_bytes": 2097152},
    )


@declaration("recipe.start", "开始整理", RecipeStart, "build.edit")
def recipe_start(ctx: CapabilityContext, body: RecipeStart) -> BuildResult:
    return BuildResult(data=build.start(body.material_id))


@declaration("recipe.status", "查看整理进度", RecipeStatus, "draft.read", read=True)
def recipe_status(ctx: CapabilityContext, body: RecipeStatus) -> BuildResult:
    return BuildResult(data=build.status(body.run_id))


@declaration("changeset.get", "查看建设方案", ChangesetGet, "draft.read", read=True)
def changeset_get(ctx: CapabilityContext, body: ChangesetGet) -> BuildResult:
    return BuildResult(data=build.get(body.changeset_id))


@declaration("changeset.decide_items", "核对建设方案", DecideItems, "build.edit")
def decide_items(ctx: CapabilityContext, body: DecideItems) -> BuildResult:
    return BuildResult(
        data=build.decide(
            body.changeset_id,
            body.item_ids,
            body.decision,
            body.edit.model_dump(exclude_unset=True) if body.edit else None,
            only_ready=body.only_ready,
            expected_versions=body.expected_versions,
        )
    )


@declaration("changeset.apply", "采纳为草稿", ChangesetGet, "build.edit")
def apply(ctx: CapabilityContext, body: ChangesetGet) -> BuildResult:
    return BuildResult(data=build.apply(body.changeset_id, submit=False))


@declaration("changeset.apply_and_submit", "采纳并提交审核", ChangesetGet, "build.submit")
def apply_and_submit(ctx: CapabilityContext, body: ChangesetGet) -> BuildResult:
    return BuildResult(data=build.apply(body.changeset_id, submit=True))


@declaration("changeset.confirm_facts", "确认文件事实与缺项", ConfirmFacts, "build.edit")
def confirm_facts(ctx: CapabilityContext, body: ConfirmFacts) -> BuildResult:
    return BuildResult(data=build.confirm_facts(body.changeset_id, body.model_dump(exclude_unset=True)))


@declaration("changeset.retain_source", "按原文保留待核对条目", RetainSource, "build.edit")
def retain_source(ctx: CapabilityContext, body: RetainSource) -> BuildResult:
    return BuildResult(data=build.retain_source(body.changeset_id, body.anchor_id, body.source_version))
