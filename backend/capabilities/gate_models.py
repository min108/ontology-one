"""Shared publication checks, usable by every UI and capability binding."""

from typing import Literal

from pydantic import Field

from backend.core.errors import StrictModel


class GateCheck(StrictModel):
    code: Literal["QG1", "QG2", "QG3", "QG4", "QG5"]
    label: str
    status: Literal["pass", "fail", "waived", "not_applicable"]
    reason: str
    can_waive: bool
    waiver_reason: str = ""


class GateResult(StrictModel):
    passed: bool
    checks: list[GateCheck]
    blockers: list[str] = Field(default_factory=list)


class GateWaivers(StrictModel):
    QG1: str | None = Field(default=None, min_length=1, max_length=500)
    QG3: str | None = Field(default=None, min_length=1, max_length=500)
