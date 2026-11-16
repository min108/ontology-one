"""Display preference REST exception: authenticated identity owns every row."""

from fastapi import APIRouter
from pydantic import JsonValue

from backend.core.errors import StrictModel
from backend.security.context import require_context
from backend.security.preferences import read_preferences, write_preference

router = APIRouter()


class PreferenceInput(StrictModel):
    value: JsonValue


class PreferencesOutput(StrictModel):
    preferences: dict[str, JsonValue]


@router.get("/prefs", response_model=PreferencesOutput)
def preferences() -> PreferencesOutput:
    return PreferencesOutput(preferences=read_preferences(require_context()))


@router.put("/prefs/{key}", response_model=PreferencesOutput)
def put_preference(key: str, body: PreferenceInput) -> PreferencesOutput:
    return PreferencesOutput(preferences=write_preference(require_context(), key, body.value))
