"""Catalog REST bindings share the same capability gate as assistants."""

from fastapi import APIRouter

from backend.api.v2.build import call
from backend.capabilities.build import BuildResult

router = APIRouter()


@router.get("/elements", response_model=BuildResult)
def elements(q: str = "", kind: str = "", page: int = 1, page_size: int = 25) -> BuildResult:
    return call("element.list", {"q": q, "kind": kind, "page": page, "page_size": page_size})


@router.get("/elements/{id}", response_model=BuildResult)
def element(id: str) -> BuildResult:
    return call("element.get", {"entity_id": id})
