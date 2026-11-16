"""Reports are the application assistant's explicitly authorized output writes."""

from collections.abc import Callable

from pydantic import Field

from backend.capabilities.build import BuildResult
from backend.capabilities.context import CapabilityContext
from backend.capabilities.registry import capability
from backend.core.errors import StrictModel
from backend.domains.artifacts import service


class ArtifactCreate(StrictModel):
    session_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    run_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    title: str = Field(default="", max_length=255)


class ArtifactGet(StrictModel):
    artifact_id: str = Field(pattern=r"^ART-[a-f0-9]{16}$")


class ArtifactRender(ArtifactGet):
    expected_version: int = Field(ge=1)


class ArtifactList(StrictModel):
    pass


def declaration(ident: str, title: str, model: type[StrictModel], read: bool = False) -> Callable:
    return capability(
        id=ident,
        version="1.0",
        title=title,
        summary=title + "，成果默认仅本人可见。",
        domain="artifact",
        intent="read" if read else "generate",
        effect="read" if read else "write_draft",
        confirm="L0" if read else "L1",
        permissions=["artifact.read" if read else "artifact.create"],
        assistants=["analyst"],
        mcp=False,
        input=model,
        output=BuildResult,
        preconditions=[],
        idempotent=read,
        audit="metadata",
        limits={"timeout_s": 30, "max_result_bytes": 2097152},
    )


@declaration("artifact.create", "生成报告", ArtifactCreate)
def create(ctx: CapabilityContext, body: ArtifactCreate) -> BuildResult:
    return BuildResult(data=service.create(body.session_id, body.run_id, body.title))


@declaration("artifact.render", "再次生成报告", ArtifactRender)
def render(ctx: CapabilityContext, body: ArtifactRender) -> BuildResult:
    return BuildResult(data=service.regenerate(body.artifact_id, body.expected_version))


@declaration("artifact.get", "查看报告", ArtifactGet, True)
def get(ctx: CapabilityContext, body: ArtifactGet) -> BuildResult:
    return BuildResult(data=service.get(body.artifact_id))


@declaration("artifact.list", "我的报告", ArtifactList, True)
def list_reports(ctx: CapabilityContext, body: ArtifactList) -> BuildResult:
    return BuildResult(data=service.list_reports())
