"""Page-only review decisions and draft-visible inbox contracts."""

from collections.abc import Callable
from typing import Literal

from pydantic import Field

from backend.capabilities.build import BuildResult
from backend.capabilities.context import CapabilityContext
from backend.capabilities.registry import capability
from backend.core.errors import StrictModel
from backend.domains import reviews


class ReviewList(StrictModel):
    kind: Literal["", "data", "rule", "knowledge", "skill"] = ""


class ReviewGet(StrictModel):
    entity_id: str = Field(min_length=1, max_length=191)


class ReviewDecision(ReviewGet):
    decision: Literal["approve_publish", "return"]
    version: int = Field(ge=1)
    reason: str = Field(default="", max_length=500)


def declaration(ident: str, title: str, model: type[StrictModel], *, write: bool = False) -> Callable:
    return capability(
        id=ident,
        version="1.0",
        title=title,
        summary=title + "，保留审核人与原文记录。",
        domain="review",
        intent="govern" if write else "read",
        effect="state_change" if write else "read",
        confirm="L3" if write else "L0",
        permissions=["review.decide" if write else "draft.read"],
        assistants=[],
        mcp=False,
        input=model,
        output=BuildResult,
        preconditions=[],
        idempotent=not write,
        audit="metadata",
        limits={"timeout_s": 60, "max_result_bytes": 2097152},
    )


@declaration("review.list", "待审核内容", ReviewList)
def list_reviews(ctx: CapabilityContext, body: ReviewList) -> BuildResult:
    return BuildResult(data=reviews.inbox(body.kind))


@declaration("review.get", "查看审核内容", ReviewGet)
def get_review(ctx: CapabilityContext, body: ReviewGet) -> BuildResult:
    return BuildResult(data=reviews.get(body.entity_id))


@declaration("review.decide", "办理审核", ReviewDecision, write=True)
def decide_review(ctx: CapabilityContext, body: ReviewDecision) -> BuildResult:
    return BuildResult(data=reviews.decide(body.entity_id, body.decision, body.version, body.reason))
