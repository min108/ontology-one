"""Canonical kind metadata, through the shared capability authorization gate."""

from fastapi import APIRouter

from backend.api.v2.build import call
from backend.api.v2.models import ListResponse
from backend.capabilities.read import Kind

router = APIRouter()


@router.get("/meta/kinds", response_model=ListResponse[Kind])
def kinds() -> ListResponse[Kind]:
    return call("meta.kinds", {})
