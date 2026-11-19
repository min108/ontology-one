"""Read capability declarations and the authenticated semantic search binding."""

from collections.abc import Callable
from contextlib import closing

from pydantic import Field, JsonValue

from backend.api.v2.models import ListResponse
from backend.capabilities.context import CapabilityContext, TenantTarget
from backend.capabilities.registry import capability
from backend.core.errors import StrictModel
from backend.kernel.kinds import KINDS as KIND_REGISTRY
from backend.kernel.kinds import list_kinds
from backend.security import store

READ_RESULT_BYTES = 262144

KINDS = {key: item.name for key, item in KIND_REGISTRY.items()}


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
    kind: str
    name: str
    pillar: str
    family: str | None
    icon: str
    color_token: str
    id_prefix: str
    payload_schema: str | None
    governed: bool
    module_eligible: bool
    card_template: str


class Organization(StrictModel):
    id: str
    name: str
    category: str
    parent_id: str | None = None
    sort_order: int = 0
    level: int = 0


class ReadinessCheck(StrictModel):
    id: str
    label: str
    ready: bool


class ReadinessOutput(StrictModel):
    complete: bool = False
    checks: list[ReadinessCheck]
    message: str


class SearchInput(TenantTarget):
    q: str = Field(default="", max_length=500)
    kind: str = Field(default="", max_length=64)
    pillar: str = Field(default="", max_length=16)
    module_id: str = Field(default="", max_length=64)
    status: str = Field(default="", max_length=64)
    page: int = Field(default=1, ge=1, le=10000, strict=True)
    page_size: int = Field(default=25, ge=1, le=100, strict=True)


class SearchOutput(ListResponse[dict[str, JsonValue]]):
    pass


def read_capability(
    ident: str,
    title: str,
    input_model: type[StrictModel],
    output_model: type[StrictModel],
    *,
    assistants: list[str] | None = None,
    permissions: list[str] | None = None,
    mcp: bool = False,
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
        permissions=permissions or [],
        assistants=assistants or [],
        mcp=mcp,
        input=input_model,
        output=output_model,
        preconditions=[],
        idempotent=True,
        audit="metadata",
        limits={"timeout_s": 30, "max_result_bytes": READ_RESULT_BYTES},
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
    return ListResponse[Kind](items=[Kind(id=item["kind"], **item) for item in list_kinds()], total=len(KINDS))


@read_capability("org.tree", "机构树", EmptyInput, ListResponse[Organization])
def organization_tree(ctx: CapabilityContext, body: EmptyInput) -> ListResponse[Organization]:
    rows = store.list_tenants()
    items = [
        Organization(**{key: row[key] for key in Organization.model_fields if key in row})
        for row in rows
        if row.get("status", "active") == "active"
    ]
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


def search_restrictions(ctx: CapabilityContext) -> dict[str, bool]:
    return {
        "published_only": "draft.read" not in ctx.permissions or ctx.binding == "mcp",
        "metadata_only": ctx.binding == "mcp",
    }


@read_capability("semantic.search", "检索内容", SearchInput, SearchOutput, assistants=["builder", "analyst"], mcp=True)
def semantic_search(ctx: CapabilityContext, body: SearchInput) -> SearchOutput:
    from backend.kernel.search import search

    with closing(ctx.connection_factory()) as conn:
        return SearchOutput.model_validate(
            search(
                conn,
                **body.model_dump(exclude={"tenant_id"}),
                legacy_payload=ctx.binding != "mcp",
                card_bytes=READ_RESULT_BYTES,
                **search_restrictions(ctx),
            )
        )
