"""Bounded semantic retrieval cards. Original files and asset payloads belong to detail reads."""

import json
from urllib.parse import quote

from backend import db
from backend.security.reference_fields import own_card_payload

# Scalar summary fields used by existing authorized asset clients. Source files,
# source blocks, operational data and nested business records are never selected.
_SCALARS = (
    "schema",
    "definition",
    "unit",
    "grain",
    "data_type",
    "rule_type",
    "effective_from",
    "effective_to",
    "aliases",
)
_METADATA = (
    "kind",
    "domain",
    "org_id",
    "status",
    "source_kind",
    "created_at",
    "updated_at",
    "version",
    "pillar",
    "module_id",
)
_DISPLAY = ("title", "summary", "highlight")


def _bytes(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())


def card_payloads(conn: db.Connection, ids: list[str]) -> dict[str, dict]:
    if not ids:
        return {}
    # Both MySQL and SQLite -> return JSON, preserving scalar types. Extract
    # bounded fields in SQL so page reads never materialize an uploaded file.
    cols = ",".join(f"SUBSTR(e.payload->'$.{key}',1,257) AS `{key}`" for key in _SCALARS)
    marks = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT e.id,{cols} FROM entities e WHERE e.id IN ({marks}) AND e.kind NOT IN ('material','dataset')", ids
    )
    result = {}
    for row in rows:
        payload = {}
        for key in _SCALARS:
            raw = row[key]
            if raw is None or len(raw) > 256:
                continue
            value = json.loads(raw)
            if isinstance(value, (str, int, float, bool)) or (
                key == "aliases"
                and isinstance(value, list)
                and len(value) <= 8
                and all(isinstance(v, str) for v in value)
            ):
                payload[key] = value
        result[row["id"]] = own_card_payload(payload)
    return result


def empty_identity_width_sql() -> str:
    # This adds one aggregate to the existing empty-query count. JSON_QUOTE
    # includes control/quote escaping, HEX measures UTF-8 bytes on both DBs.
    def width(expr: str) -> str:
        return f"COALESCE(LENGTH(HEX(JSON_QUOTE(CAST({expr} AS CHAR))))/2,4)"

    columns = ["e." + key for key in _METADATA if key not in {"pillar", "module_id"}] + ["i.pillar", "i.module_id"]
    # An element carries its id, doc_id, entity_id and escaped navigation URL.
    return (
        "COALESCE(MAX("
        + "+".join([f"3*{width('e.id')}", "3*LENGTH(HEX(e.id))/2", *(width(col) for col in columns)])
        + "),0)"
    )


def ranked_identity_width(row: dict) -> int:
    entity, doc = row["entity_id"], row["doc_id"]
    size = sum(_bytes(row[field]) for field in _METADATA) + _bytes(entity) + _bytes(doc) + len(quote(entity, safe=""))
    if row["doc_type"] == "element":
        return size + _bytes(entity)
    if doc.startswith("chunk:"):
        chunk = doc[6:]
        return size + 2 * _bytes(chunk) + len(quote(chunk, safe=""))
    # The index hashes legacy ids longer than 194 chars. The stored native
    # chunks.id column allows 255 Unicode characters, each up to 12 URL bytes.
    return size + 255 * (6 + 6 + 12)


def page_limit(requested: int, budget: int, max_identity: int, tenant_id: str, total: int) -> int:
    # Includes key names, fixed URL portions, two tenant fields, score, and
    # enough room per row for readable labels and evidence. A pathological
    # legacy id can shrink the *whole query's* page size. Its offsets remain stable.
    minimum = max_identity + 2 * _bytes(tenant_id) + 640
    fitting = max(1, (budget - 512) // minimum)
    return requested if fitting >= min(requested, total) else fitting


def _text(value: str | None, budget: int) -> str | None:
    if value is None or (len(value) <= budget and _bytes(value) <= budget):
        return value
    low, high = 0, min(len(value), budget)
    while low < high:
        middle = (low + high + 1) // 2
        if _bytes(value[:middle] + "…") <= budget:
            low = middle
        else:
            high = middle - 1
    return value[:low] + "…" if budget >= 5 else ""


def fit_cards(items: list[dict], budget: int) -> None:
    """Keep all selected identities, navigation and ordering within the registry's serialized byte limit."""
    if not items:
        return
    # A small result page is still a collection of previews.
    for item in items:
        for field, size in (("title", 640), ("summary", 960), ("highlight", 720)):
            item[field] = _text(item.get(field), size)
        item["match_terms"] = [t for t in item["match_terms"] if t in (item["highlight"] or "").casefold()]
    if _bytes(items) <= budget - 512:
        return
    # Charge opaque identities at their actual JSON cost, then divide the
    # remaining display space fairly. Full pages keep their requested items.
    base = [
        {**item, **dict.fromkeys(_DISPLAY, ""), "match_terms": [], **({"payload": {}} if "payload" in item else {})}
        for item in items
    ]
    share = (budget - 512 - _bytes(base)) // len(items)
    for item in items:
        item["title"] = _text(item["title"], share // 4)
        item["summary"] = _text(item["summary"], share // 4)
        excerpt = item["highlight"] or ""
        highlight = _text(excerpt, share // 3) or ""
        terms = item["match_terms"]
        if terms and not any(term in highlight.casefold() for term in terms):
            start = min(excerpt.casefold().index(term) for term in terms)
            highlight = _text(excerpt[start:], share // 3) or ""
        item["highlight"] = highlight
        display_terms = [term for term in terms if term in highlight.casefold()]
        while display_terms and _bytes(display_terms) > share // 12:
            display_terms.pop()
        item["match_terms"] = display_terms
        if "payload" in item:
            payload = {}
            for key, value in item["payload"].items():
                if _bytes({**payload, key: value}) <= share // 12:
                    payload[key] = value
            item["payload"] = payload
