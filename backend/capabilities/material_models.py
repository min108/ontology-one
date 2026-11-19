"""The original is paged by Unicode character count within the requested text."""

from typing import Annotated

from pydantic import Field

from backend.core.errors import StrictModel
from backend.kernel.material_source import PAGE_CHARACTERS

MAX_OFFSET = 2**32 - 1  # A MySQL LONGTEXT cannot contain more characters.
MaterialId = Annotated[str, Field(min_length=1, max_length=191)]
ChunkId = Annotated[str, Field(min_length=1, max_length=255)]


class MaterialPageRequest(StrictModel):
    offset: int = Field(default=0, ge=0, le=MAX_OFFSET, strict=True)
    chunk_id: ChunkId | None = None
    anchor_id: MaterialId | None = None
    source_version: MaterialId | None = None
    citing_entity_id: MaterialId | None = None
    citing_relation_id: MaterialId | None = None


class MaterialSourcePage(StrictModel):
    material_id: str
    title: str
    status: str
    source_version: str
    version: int | None
    locator: str
    text: str = Field(max_length=PAGE_CHARACTERS)
    offset: int = Field(ge=0)
    total: int = Field(ge=0)
