"""Candidate relation capabilities preserve the existing review workflow guards."""

from collections.abc import Callable
from typing import Literal

from pydantic import Field

from backend.api.v1 import core
from backend.capabilities.build import BuildResult
from backend.capabilities.context import CapabilityContext
from backend.capabilities.registry import capability
from backend.core.errors import StrictModel
from backend.kernel import relations


class RelationPropose(StrictModel):
    source: str = Field(min_length=1, max_length=191)
    target: str = Field(min_length=1, max_length=191)
    type: str = Field(min_length=1, max_length=80)
    reason: str = Field(min_length=1, max_length=2000)


class RelationDecision(StrictModel):
    relation_id: str = Field(min_length=1, max_length=191)
    status: Literal["confirmed", "rejected"]
    reason: str = Field(min_length=1, max_length=2000)


def _declaration(ident: str, title: str, model: type[StrictModel], *, review: bool = False) -> Callable:
    return capability(
        id=ident,
        version="1.0",
        title=title,
        summary=title + "，保留专属工作面的保护与核对记录。",
        domain="relation",
        intent="govern" if review else "build",
        effect="state_change" if review else "propose",
        confirm="L3" if review else "L1",
        permissions=["review.decide" if review else "build.edit"],
        assistants=[] if review else ["builder"],
        mcp=False,
        input=model,
        output=BuildResult,
        preconditions=[],
        idempotent=False,
        audit="metadata",
        limits={"timeout_s": 30, "max_result_bytes": 262144},
    )


@_declaration("relation.propose", "提出关联", RelationPropose)
def propose(ctx: CapabilityContext, body: RelationPropose) -> BuildResult:
    return BuildResult(data=relations.propose(body.source, body.target, body.type, body.reason))


@_declaration("relation.decide", "审定关联", RelationDecision, review=True)
def decide(ctx: CapabilityContext, body: RelationDecision) -> BuildResult:
    return BuildResult(data=core._confirm_relation(body.relation_id, {"status": body.status, "reason": body.reason}))
