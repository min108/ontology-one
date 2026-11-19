"""Structured cross-record references used by visibility and shared search cards."""

import re
from collections.abc import Iterator

REFERENCE_KEYS = {
    "entity_id": "owner",
    "material_id": "owner",
    "dataset_id": "owner",
    "source_id": "owner",
    "source_entity_id": "owner",
    "target_id": "owner",
    "target_entity_id": "owner",
    "chunk_id": "chunk",
    "source_chunk_id": "chunk",
    "anchor_id": "anchor",
    "span_id": "anchor",
    "source_version": "version",
    "revision_id": "version",
}
PRIVATE_KEYS = {"artifact", "artifacts", "artifact_id", "artifact_ids", "report_id", "report_ids"}
PRIVATE_ID = re.compile(r"ART-[0-9a-f]{16}", re.I)


def reference_keys(record: dict, in_sources: bool) -> Iterator[tuple[str, str, str]]:
    for key, value in record.items():
        base = key.removesuffix("s") if isinstance(value, list) else key
        group = REFERENCE_KEYS.get(base)
        if (
            not group
            and in_sources
            and key == "id"
            and not ({"material_id", "dataset_id", "source_id"} & record.keys())
        ):
            group = "owner"
        if group:
            for ident in value if isinstance(value, list) else (value,):
                if isinstance(ident, str) and ident:
                    yield key, group, ident


def own_card_payload(payload: dict) -> dict:
    """A shared document may include the owner's fields, without another object's cached labels or private IDs."""
    skip = object()

    def clean(value: object, *, root: bool = False) -> object:
        if isinstance(value, list):
            return [item for child in value if (item := clean(child)) is not skip]
        if not isinstance(value, dict):
            return skip if isinstance(value, str) and PRIVATE_ID.fullmatch(value) else value
        pointers = {key for key, _, _ in reference_keys(value, False)}
        if not root and (
            pointers
            or PRIVATE_KEYS & value.keys()
            or "tenant_id" in value
            or value.get("kind") in ("artifact", "report")
        ):
            return skip
        result = {}
        for key, child in value.items():
            if key in {"source_refs", "quote"} or key in PRIVATE_KEYS or key in pointers:
                continue
            item = clean(child)
            if item is not skip:
                result[key] = item
        return result

    return clean(payload, root=True)
