"""Session-bound read adapters for the semantic kernel."""

from contextlib import closing

from pydantic import Field, JsonValue

from backend.capabilities.context import CapabilityContext
from backend.capabilities.elements import ElementGet, get_element
from backend.capabilities.read import read_capability, search_restrictions
from backend.capabilities.semantic_models import (
    ImpactResult,
    NeighborInput,
    NeighborResult,
    ResolveInput,
    ResolveResult,
    SemanticTarget,
    TraceInput,
    TraceResult,
)
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


@read_capability(
    "semantic.resolve", "按名称或编号找内容", ResolveInput, ResolveResult, assistants=["builder", "analyst"], mcp=True
)
def resolve(ctx: CapabilityContext, body: ResolveInput) -> ResolveResult:
    from backend.kernel import semantic_graph

    with closing(ctx.connection_factory()) as conn:
        return ResolveResult.model_validate(
            semantic_graph.resolve(conn, body.text, body.expected_kind, body.limit, **search_restrictions(ctx))
        )


@read_capability(
    "semantic.neighbors", "查看关联内容", NeighborInput, NeighborResult, assistants=["builder", "analyst"], mcp=True
)
def neighbors(ctx: CapabilityContext, body: NeighborInput) -> NeighborResult:
    from backend.kernel import semantic_graph
    from backend.security.visibility import scope_for

    with closing(ctx.connection_factory()) as conn:
        scope = scope_for(conn, **search_restrictions(ctx))
        return NeighborResult.model_validate(
            semantic_graph.neighbors(conn, scope, body.entity_id, body.depth, body.family)
        )


@read_capability(
    "impact.analyze", "分析变更影响", SemanticTarget, ImpactResult, assistants=["builder"], permissions=["draft.read"]
)
def analyze(ctx: CapabilityContext, body: SemanticTarget) -> ImpactResult:
    from backend.kernel import semantic_graph
    from backend.security.visibility import scope_for

    with closing(ctx.connection_factory()) as conn:
        scope = scope_for(conn, **search_restrictions(ctx))
        return ImpactResult.model_validate(semantic_graph.analyze(conn, scope, body.entity_id))


@read_capability("evidence.trace", "追溯依据", TraceInput, TraceResult, assistants=["builder", "analyst"], mcp=True)
def trace(ctx: CapabilityContext, body: TraceInput) -> TraceResult:
    from backend.kernel import semantic_evidence
    from backend.security.visibility import scope_for

    with closing(ctx.connection_factory()) as conn:
        scope = scope_for(conn, **search_restrictions(ctx))
        return TraceResult.model_validate(
            semantic_evidence.trace(conn, scope, **body.model_dump(exclude={"tenant_id"}))
        )
