"""Trusted context supplied by bindings, never parsed from a request body."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from backend.db import Connection, connect
from backend.security.context import Principal


def ignore_progress(stage: str, message: str) -> None:
    """Default progress sink for synchronous REST requests."""


@dataclass(frozen=True, slots=True)
class CapabilityContext:
    principal: Principal
    binding: Literal["rest", "tool", "mcp", "recipe"] = "rest"
    run_id: str | None = None
    idempotency_key: str | None = None
    emit: Callable[[str, str], None] = ignore_progress
    connection_factory: Callable[[], Connection] = connect
    permissions: frozenset[str] = field(default_factory=frozenset)
    assistant: str = ""
