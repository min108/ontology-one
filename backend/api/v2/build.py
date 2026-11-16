"""Thin bindings for construction and review capabilities."""

from fastapi import APIRouter

from backend.capabilities.build import BuildResult, ChangesetGet, DecideItems, RecipeStart
from backend.capabilities.context import CapabilityContext
from backend.capabilities.registry import registry
from backend.capabilities.reviews import ReviewDecision
from backend.security.context import require_context
from backend.security.permissions import permissions_for

router = APIRouter()


def call(ident: str, body: dict) -> BuildResult:
    person = require_context()
    return registry.invoke(ident, CapabilityContext(person, permissions=frozenset(permissions_for(person))), body)


@router.post("/recipes", response_model=BuildResult)
def start_recipe(body: RecipeStart) -> BuildResult:
    return call("recipe.start", body.model_dump())


@router.get("/recipes/{id}", response_model=BuildResult)
def get_recipe(id: str) -> BuildResult:
    return call("recipe.status", {"run_id": id})


@router.get("/changesets/{id}", response_model=BuildResult)
def get_changeset(id: str) -> BuildResult:
    return call("changeset.get", {"changeset_id": id})


@router.post("/changesets/{id}/items:bulk-decide", response_model=BuildResult)
def decide_items(id: str, body: DecideItems) -> BuildResult:
    from backend.core.errors import ApiError

    if body.changeset_id != id:
        raise ApiError("VALIDATION_FAILED")
    return call("changeset.decide_items", body.model_dump())


@router.post("/changesets/{id}:apply", response_model=BuildResult)
def apply(id: str) -> BuildResult:
    return call("changeset.apply", ChangesetGet(changeset_id=id).model_dump())


@router.post("/changesets/{id}:apply-and-submit", response_model=BuildResult)
def submit(id: str) -> BuildResult:
    return call("changeset.apply_and_submit", {"changeset_id": id})


@router.get("/reviews", response_model=BuildResult)
def list_reviews(kind: str = "") -> BuildResult:
    return call("review.list", {"kind": kind})


@router.get("/reviews/{id}", response_model=BuildResult)
def get_review(id: str) -> BuildResult:
    return call("review.get", {"entity_id": id})


class ReviewBody(ReviewDecision):
    entity_id: str = ""


@router.post("/reviews/{id}:decide", response_model=BuildResult)
def decide_review(id: str, body: ReviewBody) -> BuildResult:
    from backend.core.errors import ApiError

    if body.entity_id and body.entity_id != id:
        raise ApiError("VALIDATION_FAILED")
    return call("review.decide", {**body.model_dump(), "entity_id": id})
