"""Business fields and value-bound provenance shared by release surfaces."""

import json

from backend import db
from backend.kernel.cards import plain
from backend.kernel.metadata import get_meta, sync_meta
from backend.security.context import require_context

FIELD_LABELS = {
    "definition": "一句话定义",
    "unit": "单位",
    "grain": "粒度",
    "scope": "适用范围",
    "conditions": "适用条件",
    "exceptions": "例外",
    "valid_from": "生效日期",
    "valid_to": "失效日期",
    "usage_conditions": "使用条件",
    "boundary": "使用边界",
    "subjects": "适用主体或对象",
    "inputs": "输入要求",
    "outputs": "输出要求",
    "steps": "操作步骤",
}
SOURCES = {"source", "system", "editor", "batch_confirmation", "legacy", "missing"}


SOURCE_DETAILS = ("actor_id", "method", "confirmed_at", "material_id", "source_version", "anchor_id", "parts")


def field_values(conn: db.Connection, entity: dict, meta: dict) -> dict[str, str]:
    payload = entity.get("payload") or {}
    keys = ["definition"]
    if entity["kind"] in {"data", "metric"}:
        keys += ["unit", "grain"]
    if entity["kind"] == "rule":
        keys += ["scope", "conditions", "exceptions", "valid_from", "valid_to", "subjects"]
    if entity["kind"] == "knowledge":
        keys += ["usage_conditions", "boundary"]
    if entity["kind"] == "skill":
        keys += ["inputs", "outputs", "steps", "usage_conditions"]
    result = {key: plain(meta.get(key) if key.startswith("valid_") else payload.get(key)) for key in keys}
    if "usage_conditions" in result:
        result["usage_conditions"] = plain(payload.get("usage_conditions") or payload.get("conditions"))
    if entity["kind"] == "skill":
        for key in ("inputs", "outputs"):
            value = payload.get(key)
            if isinstance(value, dict):
                # These are the declared schema facts used by the gates, not
                # package files, test inputs or executable configuration.
                result[key] = json.dumps(value, ensure_ascii=False, sort_keys=True)
        steps = payload.get("steps")
        if isinstance(steps, list):
            result["steps"] = "；".join(
                "：".join(filter(None, (plain(step.get("title")), plain(step.get("instruction")))))
                if isinstance(step, dict)
                else plain(step)
                for step in steps
            )
    if "subjects" in result:
        result["subjects"] = "、".join(row["title"] for row in subjects(conn, entity))
    return result


def subjects(conn: db.Connection, entity: dict) -> list[dict]:
    return [
        dict(row)
        for row in conn.execute(
            "SELECT DISTINCT e.id,e.title,e.kind FROM relations r JOIN entities e ON e.id=r.target "
            "WHERE r.source=? AND r.type IN ('applies_to','governs') AND r.status IN ('confirmed','source_verified') "
            "ORDER BY e.id",
            (entity["id"],),
        )
    ]


def project(conn: db.Connection, entity: dict, meta: dict) -> list[dict]:
    from backend.kernel.gates import _object

    provenance = _object(meta.get("gates")).get("field_sources", {})
    result = []
    for key, value in field_values(conn, entity, meta).items():
        record = provenance.get(key, {}) if isinstance(provenance, dict) else {}
        current = isinstance(record, dict) and record.get("value") == value
        source = record.get("source") if current else "legacy"
        source = source if source in SOURCES else "legacy"
        result.append(
            {
                "key": key,
                "label": FIELD_LABELS[key],
                "value": value,
                "source": source if value else "missing",
                "source_quote": record.get("source_quote", "") if current else "",
                "confirmed_by": record.get("confirmed_by", "") if current else "",
                **{key: record[key] for key in SOURCE_DETAILS if current and record.get(key)},
                "href": "",
            }
        )
    subject_record = next((item for item in result if item["key"] == "subjects"), None)
    if subject_record:
        result.remove(subject_record)
        related = subjects(conn, entity)
        if related:
            result.extend(
                {
                    **subject_record,
                    "key": "subject:" + item["id"],
                    "value": item["title"],
                    "href": "/assets/" + item["id"],
                }
                for item in related
            )
        else:
            result.append(subject_record)
    return result


def record(conn: db.Connection, entity: dict, sources: dict) -> None:
    """Trusted services only; public payloads cannot write this side-table state."""
    from backend.kernel.gates import _object

    meta = get_meta(conn, entity["id"]) or sync_meta(conn, entity)
    values = field_values(conn, entity, meta)
    stored = _object(meta.get("gates"))
    records = stored.setdefault("field_sources", {})
    for key, source in sources.items():
        if key not in values or not isinstance(source, dict) or source.get("source") not in SOURCES - {"missing"}:
            raise ValueError("门禁字段来源格式不正确。")
        records[key] = {
            "value": values[key],
            "source": source["source"],
            "source_quote": str(source.get("source_quote") or ""),
            "confirmed_by": str(source.get("confirmed_by") or ""),
            "at": db.now(),
            **{key: source[key] for key in SOURCE_DETAILS if source.get(key)},
        }
    conn.execute(
        "UPDATE entity_meta SET gates=? WHERE entity_id=?", (json.dumps(stored, ensure_ascii=False), entity["id"])
    )


def record_editor_changes(conn: db.Connection, entity: dict, previous: dict[str, str] | None) -> None:
    values = field_values(conn, entity, get_meta(conn, entity["id"]) or {})
    record(
        conn,
        entity,
        {
            key: {"source": "editor", "confirmed_by": require_context().username}
            for key, value in values.items()
            if value and (previous or {}).get(key) != value
        },
    )
