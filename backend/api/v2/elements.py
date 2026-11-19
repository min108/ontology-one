"""Catalog REST bindings share the same capability gate as assistants."""

from fastapi import APIRouter, HTTPException

from backend.api.v2.build import call
from backend.capabilities.element_models import ElementDetailResult, ElementListResult
from backend.capabilities.semantic_models import TraceResult

router = APIRouter()


@router.get("/elements", response_model=ElementListResult)
def elements(q: str = "", kind: str = "", page: int = 1, page_size: int = 25) -> ElementListResult:
    return call("element.list", {"q": q, "kind": kind, "page": page, "page_size": page_size})


@router.get("/elements/{id}", response_model=ElementDetailResult)
def element(id: str) -> ElementDetailResult:
    return call("element.get", {"entity_id": id})


@router.get("/elements/{id}/passage", response_model=TraceResult)
def passage(
    id: str,
    chunk_id: str | None = None,
    anchor_id: str | None = None,
    source_version: str | None = None,
    citing_entity_id: str | None = None,
    citing_relation_id: str | None = None,
) -> TraceResult:
    if not (chunk_id or anchor_id):
        raise HTTPException(422, "请指定原文片段。")
    return call(
        "evidence.trace",
        {
            "entity_id": id,
            "chunk_id": chunk_id,
            "anchor_id": anchor_id,
            "source_version": source_version,
            "citing_entity_id": citing_entity_id,
            "citing_relation_id": citing_relation_id,
        },
    )
