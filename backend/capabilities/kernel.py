"""Semantic kernel bindings: all mutations retain legacy domain guards."""

from collections.abc import Callable
from contextlib import closing

from pydantic import Field, JsonValue

from backend import db
from backend.api.v1 import core
from backend.capabilities.context import CapabilityContext
from backend.capabilities.element_models import ElementDetailResult
from backend.capabilities.elements import ElementGet, get_element
from backend.capabilities.gate_models import GateResult, GateWaivers
from backend.capabilities.read import read_capability
from backend.capabilities.registry import capability
from backend.core.errors import ApiError, StrictModel
from backend.kernel import gates


class ElementMetadata(StrictModel):
    valid_from: str | None = Field(default=None, max_length=40)
    valid_to: str | None = Field(default=None, max_length=40)
    element_key: str | None = Field(default=None, max_length=191, pattern=r"^[a-z][a-z0-9_]*$")
    provider_note: str | None = Field(default=None, max_length=500)


class ElementCreate(StrictModel):
    kind: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=250)
    summary: str = Field(default="", max_length=16000)
    domain: str = Field(default="综合管理", max_length=255)
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    meta: ElementMetadata | None = None


class ElementPayloadFields(StrictModel):
    definition: str | None = None
    unit: str | None = None
    grain: str | None = None
    scope: str | None = None
    conditions: list[str] | None = None
    exceptions: str | None = None
    usage_conditions: str | None = None
    boundary: str | None = None
    applies_to_id: str | None = None
    judgement: dict[str, JsonValue] | None = None
    formula: dict[str, JsonValue] | None = None
    test_cases: list[dict[str, JsonValue]] | None = None


class ElementUpdate(ElementGet):
    kind: str | None = Field(default=None, min_length=1, max_length=64)
    waiver_applications: GateWaivers | None = None
    version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=250)
    summary: str | None = Field(default=None, max_length=16000)
    domain: str | None = Field(default=None, max_length=255)
    payload: dict[str, JsonValue] | None = None
    payload_fields: ElementPayloadFields | None = None
    add_alias: str | None = Field(default=None, min_length=1, max_length=250)
    meta: ElementMetadata | None = None
    reason: str = Field(default="补充草稿内容", min_length=1, max_length=500)
    sentence_confirmed: bool = False


class ElementAction(ElementGet):
    version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500)


class ElementSubmit(ElementAction):
    waiver_applications: GateWaivers | None = None


class ElementRestore(ElementAction):
    restore_version: int = Field(ge=1)


class RuleCaseResult(StrictModel):
    name: str
    expected: JsonValue
    actual: JsonValue
    passed: bool
    details: dict[str, JsonValue] | None = None
    error: str | None = None
    error_code: str | None = None


class RuleCasesData(StrictModel):
    passed: bool
    total: int
    passed_count: int
    results: list[RuleCaseResult]
    errors: list[str]


class RuleCasesResult(StrictModel):
    data: RuleCasesData


class QualityResult(StrictModel):
    data: GateResult


class WaiveGates(ElementAction):
    waivers: GateWaivers


def mutation(
    ident: str,
    title: str,
    model: type[StrictModel],
    output: type[StrictModel] = ElementDetailResult,
    permission: str = "build.edit",
) -> Callable:
    return capability(
        id=ident,
        version="1.0",
        title=title,
        summary=title + "，保留权限、版本与办理记录。",
        domain=ident.split(".")[0],
        intent="build",
        effect="state_change" if ident == "element.submit_draft" else "write_draft",
        confirm="L2" if ident == "element.submit_draft" else "L1",
        permissions=[permission],
        assistants=["builder"],
        mcp=False,
        input=model,
        output=output,
        preconditions=[],
        idempotent=False,
        audit="metadata",
        limits={"timeout_s": 60, "max_result_bytes": 2097152},
    )


@mutation("element.create_draft", "创建要素草稿", ElementCreate)
def create_draft(ctx: CapabilityContext, body: ElementCreate) -> ElementDetailResult:
    result = core._create_entity(body.model_dump(exclude_none=True))
    return get_element(ctx, ElementGet(entity_id=result["id"]))


@mutation("element.update_draft", "修改要素草稿", ElementUpdate)
def update_draft(ctx: CapabilityContext, body: ElementUpdate) -> ElementDetailResult:
    changes = body.model_dump(exclude_unset=True, exclude_none=True, exclude={"entity_id", "version"})
    changes["expected_version"] = body.version
    changes.setdefault("reason", body.reason)
    core._edit_entity(body.entity_id, changes, repair_gates=True)
    return get_element(ctx, ElementGet(entity_id=body.entity_id))


def _action(body: ElementAction, action: str, allowed: set[str]) -> None:
    with closing(db.connect()) as conn, conn:
        entity = db.entity_json(
            conn.execute("SELECT * FROM entities WHERE id=? FOR UPDATE", (body.entity_id,)).fetchone()
        )
        if not entity:
            raise ApiError("NOT_FOUND")
        if entity["version"] != body.version:
            raise ApiError("VERSION_CONFLICT")
        if entity["status"] not in allowed or entity["kind"] not in gates.ASSETS:
            raise ApiError("PRECONDITION_FAILED", "当前状态不能执行此操作。")
        core.entity_action_in_transaction(
            conn, body.entity_id, {"action": action, "reason": body.reason, "expected_version": body.version}
        )


@mutation("element.submit_draft", "提交要素审核", ElementSubmit, permission="build.submit")
def submit_draft(ctx: CapabilityContext, body: ElementSubmit) -> ElementDetailResult:
    changes = {"action": "submit", "expected_version": body.version, "reason": body.reason}
    if body.waiver_applications is not None:
        changes["waiver_applications"] = body.waiver_applications.model_dump(exclude_none=True)
    core._entity_action(body.entity_id, changes)
    return get_element(ctx, ElementGet(entity_id=body.entity_id))


@mutation("element.start_revision", "发起要素修订", ElementAction)
def start_revision(ctx: CapabilityContext, body: ElementAction) -> ElementDetailResult:
    _action(body, "revise", {"published", "approved"})
    return get_element(ctx, ElementGet(entity_id=body.entity_id))


@mutation("element.restore_version", "还原历史版本为草稿", ElementRestore)
def restore_version(ctx: CapabilityContext, body: ElementRestore) -> ElementDetailResult:
    core._restore_entity(
        body.entity_id, {"version": body.restore_version, "expected_version": body.version, "reason": body.reason}
    )
    return get_element(ctx, ElementGet(entity_id=body.entity_id))


@mutation("element.discard_draft", "放弃要素草稿", ElementAction)
def discard_draft(ctx: CapabilityContext, body: ElementAction) -> ElementDetailResult:
    _action(body, "archive", {"draft", "returned"})
    return get_element(ctx, ElementGet(entity_id=body.entity_id))


@read_capability("quality.check_gates", "检查发布条件", ElementGet, QualityResult, assistants=["builder", "analyst"])
def check_gates(ctx: CapabilityContext, body: ElementGet) -> QualityResult:
    detail = get_element(ctx, body)
    return QualityResult(data=detail.data["gates"])


@read_capability(
    "rule.test_cases", "运行规则与口径用例", ElementGet, RuleCasesResult, assistants=["builder", "analyst"]
)
def test_cases(ctx: CapabilityContext, body: ElementGet) -> RuleCasesResult:
    if ctx.principal.readonly:
        raise ApiError("PERMISSION_DENIED")
    # Authorize the element before reading any referenced object declarations.
    detail = get_element(ctx, body)
    with closing(db.connect()) as conn:
        return RuleCasesResult(data=gates.run_cases(conn, detail.data["entity"]))


@mutation("quality.waive_gates", "记录发布检查豁免", WaiveGates, QualityResult, "review.decide")
def waive_gates(ctx: CapabilityContext, body: WaiveGates) -> QualityResult:
    with closing(db.connect()) as conn, conn:
        entity = db.entity_json(
            conn.execute("SELECT * FROM entities WHERE id=? FOR UPDATE", (body.entity_id,)).fetchone()
        )
        if not entity:
            raise ApiError("NOT_FOUND")
        if entity["version"] != body.version:
            raise ApiError("VERSION_CONFLICT")
        return QualityResult(data=gates.waive(conn, entity, body.waivers.model_dump(exclude_none=True)))
