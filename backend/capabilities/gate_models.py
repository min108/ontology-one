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
    application_reason: str = ""
    application_by: str = ""
    application_at: str = ""
    denial_reason: str = ""


class GateFieldPart(StrictModel):
    value: str
    source: Literal["source", "system"]
    source_quote: str
    material_id: str = ""
    source_version: str = ""
    anchor_id: str = ""


class GateField(StrictModel):
    key: str
    label: str
    value: str
    parts: list[GateFieldPart] = Field(default_factory=list)
    source: Literal["source", "system", "editor", "batch_confirmation", "legacy", "missing"]
    source_quote: str = ""
    method: Literal["selection"] | None = None
    actor_id: str = ""
    confirmed_at: str = ""
    material_id: str = ""
    source_version: str = ""
    anchor_id: str = ""
    confirmed_by: str = ""
    href: str = ""


class GateResult(StrictModel):
    passed: bool
    ready_to_submit: bool = False
    submission_blockers: list[str] = Field(default_factory=list)
    fields: list[GateField] = Field(default_factory=list)
    hard_constraint: bool = False
    checks: list[GateCheck]
    blockers: list[str] = Field(default_factory=list)


class GateWaivers(StrictModel):
    QG1: str | None = Field(default=None, min_length=1, max_length=500)
    QG3: str | None = Field(default=None, min_length=1, max_length=500)
