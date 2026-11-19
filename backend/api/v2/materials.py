"""The material reader uses the same authenticated evidence capability as assistants."""

from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import Field

from backend.api.v2.build import call
from backend.capabilities.material_models import MAX_OFFSET, MaterialId, MaterialPageRequest, MaterialSourcePage

router = APIRouter()


class ReadingQuery(MaterialPageRequest):
    # HTTP query parameters arrive as strings. The capability itself accepts integer offsets.
    offset: int = Field(default=0, ge=0, le=MAX_OFFSET)


@router.get("/materials/{id}/source", response_model=MaterialSourcePage)
def source(id: MaterialId, query: Annotated[ReadingQuery, Query()]) -> MaterialSourcePage:
    return call("evidence.trace", {"entity_id": id, "source_page": query.model_dump()}).source_page
