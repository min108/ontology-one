"""Additive element metadata; callers own transactions and authorization."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import UTC, date, datetime

from backend import db
from backend.kernel.kinds import get_kind

SNAPSHOT_META_FIELDS = ("module_id", "element_key", "valid_from", "valid_to", "sensitivity", "owner_user_id")
EDITABLE_FIELDS = frozenset((*SNAPSHOT_META_FIELDS, "subtype", "provider_note", "gates", "card_text"))
SENSITIVITIES = {"公开": "public", "内部": "internal", "敏感": "sensitive", "受限": "restricted"}


def _payload(entity: Mapping) -> dict:
    value = entity.get("payload") or {}
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ValueError("要素内容必须为对象。")
    return value


def _date(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    try:
        if len(value) == 10:
            return date.fromisoformat(value).isoformat()
        return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return None


def defaults(entity: Mapping) -> dict:
    """Derive safe migration defaults without changing an old entity or snapshot."""
    kind = get_kind(entity["kind"])
    payload = _payload(entity)
    subtype = None
    if kind.kind == "material":
        subtype = (
            "regulation"
            if entity.get("source_kind") == "regulation" or payload.get("source_kind") == "regulation"
            else "interview"
            if payload.get("record_type") == "experience_session"
            else "other"
        )
    elif kind.kind == "rule":
        subtype = "authority" if "authority_spec" in payload else "general"
    elif kind.kind == "project":
        subtype = "special" if payload.get("project_mode") == "special_review" else "scenario"
    sensitivity = payload.get("敏感级别", payload.get("sensitivity", "internal"))
    sensitivity = SENSITIVITIES.get(sensitivity, sensitivity) if isinstance(sensitivity, str) else "internal"
    if sensitivity not in {"public", "internal", "sensitive", "restricted"}:
        sensitivity = "internal"
    return {
        "entity_id": entity["id"],
        "pillar": kind.pillar,
        "subtype": subtype,
        "module_id": "MOD-DEFAULT" if kind.module_eligible else None,
        "element_key": None,
        "owner_user_id": None,
        "provider_note": None,
        "valid_from": _date(payload.get("生效日期", payload.get("valid_from"))) if kind.kind == "rule" else None,
        "valid_to": _date(payload.get("失效日期", payload.get("valid_to"))) if kind.kind == "rule" else None,
        "sensitivity": sensitivity,
        "gates": None,
        "card_text": None,
        "updated_at": entity.get("updated_at") or db.now(),
    }


def get_meta(conn: db.Connection, entity_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM entity_meta WHERE entity_id=?", (entity_id,)).fetchone()
    if row is None:
        return None
    result = dict(row)
    if isinstance(result.get("gates"), str):
        result["gates"] = json.loads(result["gates"])
    return result


def _validate(meta: dict, entity: Mapping) -> None:
    kind = get_kind(entity["kind"])
    if kind.module_eligible and not meta.get("module_id"):
        raise ValueError("业务资产必须有所属公共部分或场景本体。")
    if not kind.module_eligible and meta.get("module_id") is not None:
        raise ValueError("该种类不归入场景本体。")
    if not isinstance(meta["sensitivity"], str) or meta["sensitivity"] not in {
        "public",
        "internal",
        "sensitive",
        "restricted",
    }:
        raise ValueError("敏感级别无效。")
    if meta.get("element_key") is not None and (
        not isinstance(meta["element_key"], str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,190}", meta["element_key"])
    ):
        raise ValueError("业务标识须使用小写字母、数字和下划线。")
    for name in ("valid_from", "valid_to"):
        if meta[name] is not None and _date(meta[name]) is None:
            raise ValueError("有效日期格式无效。")
    if meta["valid_from"] and meta["valid_to"]:
        start, end = (datetime.fromisoformat(_date(meta[name])) for name in ("valid_from", "valid_to"))
        start = start.replace(tzinfo=UTC) if start.tzinfo is None else start.astimezone(UTC)
        end = end.replace(tzinfo=UTC) if end.tzinfo is None else end.astimezone(UTC)
        if start > end:
            raise ValueError("有效期间的结束时间不能早于开始时间。")
    subtypes = {
        "material": {"regulation", "report", "interview", "workpaper", "other"},
        "rule": {"authority", "general"},
        "project": {"scenario", "special"},
    }
    if kind.kind in subtypes and (not isinstance(meta["subtype"], str) or meta["subtype"] not in subtypes[kind.kind]):
        raise ValueError("要素分类无效。")
    for name, limit in (("module_id", 64), ("owner_user_id", 64), ("provider_note", 500), ("subtype", 32)):
        if meta.get(name) is not None and (not isinstance(meta[name], str) or len(meta[name]) > limit):
            raise ValueError("要素附加信息格式无效或过长。")
    if meta.get("card_text") is not None and (not isinstance(meta["card_text"], str) or len(meta["card_text"]) > 1200):
        raise ValueError("业务语言卡文字不能超过 1200 字。")


def sync_meta(conn: db.Connection, entity: Mapping, updates: Mapping | None = None) -> dict:
    """Synchronize in the caller's entity transaction, preserving user metadata."""
    values = defaults(entity)
    current = get_meta(conn, entity["id"])
    if current:
        values.update(current)
        values["pillar"] = get_kind(entity["kind"]).pillar
        if entity["kind"] in {"rule", "project"}:
            values["subtype"] = defaults(entity)["subtype"]
    if updates:
        if set(updates) - EDITABLE_FIELDS:
            raise ValueError("包含不允许修改的要素附加字段。")
        values.update(updates)
    _validate(values, entity)
    values["updated_at"] = db.now()
    encoded = {
        **values,
        "gates": json.dumps(values["gates"], ensure_ascii=False) if values["gates"] is not None else None,
    }
    columns = tuple(encoded)
    if current:
        changed = [name for name in columns if name != "entity_id"]
        conn.execute(
            "UPDATE entity_meta SET " + ",".join(name + "=?" for name in changed) + " WHERE entity_id=?",
            (*(encoded[name] for name in changed), entity["id"]),
        )
    else:
        conn.execute(
            "INSERT INTO entity_meta(" + ",".join(columns) + ") VALUES(" + ",".join("?" for _ in columns) + ")",
            tuple(encoded.values()),
        )
    return values


def backfill_meta(conn: db.Connection) -> int:
    """Only fill missing rows. The legacy tables and existing metadata are untouched."""
    rows = conn.execute(
        "SELECT e.* FROM entities e LEFT JOIN entity_meta m ON m.entity_id=e.id WHERE m.entity_id IS NULL ORDER BY e.id"
    ).fetchall()
    inserted = 0
    for row in rows:
        values = defaults(dict(row))
        columns = tuple(values)
        cursor = conn.execute(
            "INSERT IGNORE INTO entity_meta(" + ",".join(columns) + ") VALUES(" + ",".join("?" for _ in columns) + ")",
            tuple(values.values()),
        )
        inserted += cursor.rowcount
    return inserted
