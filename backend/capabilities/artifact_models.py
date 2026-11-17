"""Generated report presentation contracts; export provenance is server-owned."""

from typing import Literal

from pydantic import Field

from backend.capabilities.build_models import ReadModel


class ArtifactSource(ReadModel):
    title: str
    quote: str
    version: int
    path: str
    locator: str = ""
    source_title: str = ""
    material_id: str = ""
    anchor_id: str = ""
    source_version: str = ""


class ArtifactBlock(ReadModel):
    type: Literal["paragraph", "table"]
    text: str = ""
    columns: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    citations: list[str]


class ArtifactRevision(ReadModel):
    count: Literal[1] = 1
    before: str
    after: str
    before_configuration_changed: bool = False


class ArtifactSpec(ReadModel):
    blocks: list[ArtifactBlock]
    citations: dict[str, ArtifactSource]
    source_label: str
    revision: ArtifactRevision | None = None


class ArtifactVersion(ReadModel):
    version: int
    created_at: str
    change_note: str


class ArtifactSummary(ReadModel):
    id: str
    title: str
    source_kind: str
    current_version: int
    created_at: str


class ArtifactDetail(ArtifactSummary):
    spec: ArtifactSpec
    versions: list[ArtifactVersion]


class ArtifactResult(ReadModel):
    data: ArtifactDetail


class ArtifactListData(ReadModel):
    items: list[ArtifactSummary]


class ArtifactListResult(ReadModel):
    data: ArtifactListData


class AgentReportRun(ReadModel):
    id: str
    status: str
    answer: str = ""
    revision_of: str | None = None
    report_revision_id: str | None = None
