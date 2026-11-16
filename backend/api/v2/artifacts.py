"""Authenticated report metadata and binary downloads."""

from fastapi import APIRouter, Response

from backend.api.v2.build import call
from backend.capabilities.artifacts import ArtifactRender
from backend.capabilities.build import BuildResult
from backend.domains.artifacts import service

router = APIRouter()


@router.get("/artifacts", response_model=BuildResult)
def list_reports() -> BuildResult:
    return call("artifact.list", {})


@router.get("/artifacts/{id}", response_model=BuildResult)
def get(id: str) -> BuildResult:
    return call("artifact.get", {"artifact_id": id})


@router.post("/artifacts/{id}:render", response_model=BuildResult)
def render(id: str, body: ArtifactRender) -> BuildResult:
    from fastapi import HTTPException

    if body.artifact_id != id:
        raise HTTPException(422, "报告编号不一致。")
    return call("artifact.render", body.model_dump())


@router.get("/artifacts/{id}/versions/{version}/files/{format}")
def download(id: str, version: int, format: str) -> Response:
    content = service.download(id, version, format)
    media = (
        "text/html; charset=utf-8"
        if format == "html"
        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    return Response(
        content,
        media_type=media,
        headers={
            "Content-Disposition": f'attachment; filename="{id}-v{version}.{format}"',
            "Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )
