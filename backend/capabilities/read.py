"""R1 read capabilities. Search forwards the existing implementation and filters in SQL."""

from collections.abc import Callable

from pydantic import Field, JsonValue

from backend import federation
from backend.api.v1 import core
from backend.api.v2.models import ListResponse
from backend.capabilities.context import CapabilityContext
from backend.capabilities.registry import capability
from backend.core.errors import StrictModel
from backend.security import store

KINDS = {
    "organization": "单位 / 部门",
    "person": "人员",
    "material": "材料",
    "dataset": "数据台账",
    "data": "数据项",
    "rule": "规则",
    "skill": "技能",
    "knowledge": "知识经验",
    "project": "场景工作室 / 专项整理",
    "task": "任务",
    "matter": "核查事项",
    "evidence": "依据引用",
    "issue": "问题线索",
    "case": "案例",
    "analysis": "分析记录",
    "collection": "知识专栏",
}


class EmptyInput(StrictModel):
    pass


class IdentityOutput(StrictModel):
    user_id: str
    username: str
    tenant_id: str
    home_tenant_id: str
    role: str
    permissions: list[str]


class Kind(StrictModel):
    id: str
    name: str


class Organization(StrictModel):
    id: str
    name: str
    category: str


class ReadinessCheck(StrictModel):
    id: str
    label: str
    ready: bool


class ReadinessOutput(StrictModel):
    complete: bool = False
    checks: list[ReadinessCheck]
    message: str


class SearchInput(StrictModel):
    q: str = Field(default="", max_length=500)


class SearchOutput(ListResponse[dict[str, JsonValue]]):
    pass


def read_capability(
    ident: str,
    title: str,
    input_model: type[StrictModel],
    output_model: type[StrictModel],
    *,
    assistants: list[str] | None = None,
) -> Callable:
    return capability(
        id=ident,
        version="1.0",
        title=title,
        summary=title + "，遵守当前账户的查看范围。",
        domain=ident.split(".")[0],
        intent="read",
        effect="read",
        confirm="L0",
        permissions=[],
        assistants=assistants or [],
        mcp=False,
        input=input_model,
        output=output_model,
        preconditions=[],
        idempotent=True,
        audit="metadata",
        limits={"timeout_s": 30, "max_result_bytes": 262144},
    )


@read_capability("session.whoami", "当前身份", EmptyInput, IdentityOutput)
def whoami(ctx: CapabilityContext, body: EmptyInput) -> IdentityOutput:
    person = ctx.principal
    return IdentityOutput(
        user_id=person.user_id,
        username=person.username,
        tenant_id=person.tenant_id,
        home_tenant_id=person.home_tenant_id,
        role=person.role,
        permissions=sorted(ctx.permissions),
    )


@read_capability("meta.kinds", "种类元数据", EmptyInput, ListResponse[Kind])
def kinds(ctx: CapabilityContext, body: EmptyInput) -> ListResponse[Kind]:
    return ListResponse[Kind](items=[Kind(id=key, name=name) for key, name in KINDS.items()], total=len(KINDS))


@read_capability("org.tree", "机构树", EmptyInput, ListResponse[Organization])
def organization_tree(ctx: CapabilityContext, body: EmptyInput) -> ListResponse[Organization]:
    rows = store.list_tenants()
    items = [Organization(id=row["id"], name=row["name"], category=row["category"]) for row in rows]
    return ListResponse[Organization](items=items, total=len(items))


@read_capability("agent.readiness", "就绪诊断", EmptyInput, ReadinessOutput)
def readiness(ctx: CapabilityContext, body: EmptyInput) -> ReadinessOutput:
    return ReadinessOutput(
        checks=[
            ReadinessCheck(id="session", label="已登录当前机构", ready=True),
            ReadinessCheck(id="capabilities", label="内容读取服务可用", ready=True),
        ],
        message="当前仅检查登录与内容读取服务；模型与助手运行环境检查将在后续版本提供。",
    )


@read_capability("semantic.search", "检索内容", SearchInput, SearchOutput, assistants=["builder", "analyst"])
def semantic_search(ctx: CapabilityContext, body: SearchInput) -> SearchOutput:
    if ctx.principal.readonly:
        result = federation.search(
            q=body.q, kind="", status="published", tenant_id=ctx.principal.tenant_id, page=1, page_size=25
        )
        if result["partial"]:
            from backend.core.errors import ApiError

            raise ApiError("RUNTIME_UNAVAILABLE")
        return SearchOutput.model_validate({key: result[key] for key in ("items", "total", "page", "page_size")})
    result = (
        core.search(q=body.q)
        if "draft.read" in ctx.permissions
        else core.entities(q=body.q, page_size=25, status="published")
    )
    return SearchOutput.model_validate(result)
