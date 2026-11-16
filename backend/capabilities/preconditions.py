"""Named server-owned preconditions with actionable failure responses."""

from collections.abc import Callable

from pydantic import BaseModel

from backend.capabilities.context import CapabilityContext
from backend.core.errors import ApiError

CHECKS: dict[str, Callable[[CapabilityContext, BaseModel], bool]] = {}


def register(name: str, check: Callable[[CapabilityContext, BaseModel], bool]) -> None:
    if name in CHECKS or not name:
        raise ValueError("Precondition name must be unique")
    CHECKS[name] = check


def check_all(names: tuple[str, ...], ctx: CapabilityContext, body: BaseModel) -> None:
    for name in names:
        if not CHECKS[name](ctx, body):
            raise ApiError("PRECONDITION_FAILED")
