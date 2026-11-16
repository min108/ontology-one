"""REST binding: contracts are composed from the startup-time registry."""

import importlib
from functools import reduce
from operator import or_

from fastapi import APIRouter, Request
from pydantic import BaseModel

from backend.api.v2.system import router as system_router
from backend.capabilities.context import CapabilityContext
from backend.capabilities.registry import registry
from backend.core.errors import ErrorResponse
from backend.security.context import require_context
from backend.security.permissions import permissions_for

importlib.import_module("backend.capabilities.read")
importlib.import_module("backend.capabilities.model")
importlib.import_module("backend.capabilities.backup")
router = APIRouter(prefix="/api/v2")

router.include_router(system_router)
CapabilityInput = reduce(or_, (item.input for item in registry.entries.values()))
CapabilityOutput = reduce(or_, (item.output for item in registry.entries.values()))
ERROR_RESPONSES = {code: {"model": ErrorResponse} for code in (401, 403, 404, 409, 422, 500, 503)}


@router.post("/capabilities/{id}:invoke", response_model=CapabilityOutput, responses=ERROR_RESPONSES)
def invoke(id: str, body: CapabilityInput, request: Request) -> BaseModel:
    person = require_context()
    ctx = CapabilityContext(
        person, permissions=frozenset(permissions_for(person)), idempotency_key=request.headers.get("Idempotency-Key")
    )
    return registry.invoke(id, ctx, body.model_dump(exclude_unset=True))
