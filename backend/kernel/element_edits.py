"""Resolve narrow element-form edits against the locked row and the editor's visible fields."""

from fastapi import HTTPException

from backend import db
from backend.kernel.elements import check_version
from backend.kernel.kinds import persisted_kind
from backend.security import visibility

_MISSING = object()


def _apply_visible_edit(stored: object, visible: object, updated: object) -> object:
    if updated == visible:
        return stored
    if isinstance(stored, dict) and isinstance(visible, dict) and isinstance(updated, dict):
        result = dict(stored)
        for key in visible.keys() | updated.keys():
            value = _apply_visible_edit(
                stored.get(key, _MISSING), visible.get(key, _MISSING), updated.get(key, _MISSING)
            )
            if value is _MISSING:
                result.pop(key, None)
            else:
                result[key] = value
        return result
    if stored != visible:
        # Replacing a whole list or a hidden object would erase entries the editor never saw.
        raise HTTPException(409, "此字段包含当前不可见的历史内容，无法整体替换。")
    return updated


def draft_field_changes(conn: db.Connection, old: dict, changes: dict) -> dict:
    check_version(old, changes)
    if old["status"] not in {"draft", "returned"}:
        raise HTTPException(409, "请先发起修订，再保存草稿。")
    if "payload" in changes:
        raise HTTPException(422, "请分别办理完整内容替换和指定字段修改。")
    payload = dict(old["payload"])
    if "add_alias" in changes:
        if changes.keys() - {"add_alias", "expected_version", "reason"} or not persisted_kind(old["kind"]).governed:
            raise HTTPException(422, "请单独为业务资产草稿登记别名。")
        name = changes["add_alias"].strip()
        if not name:
            raise HTTPException(422, "请填写新别名。")
        names = payload.get("aliases")
        if not isinstance(names, list):
            names = [] if names is None else [names]
        if any(isinstance(existing, str) and existing.strip().casefold() == name.casefold() for existing in names):
            raise HTTPException(409, "该别名已登记。")
        payload["aliases"] = [*names, name]
    else:
        fields = changes["payload_fields"]
        stored = {key: payload[key] for key in fields if key in payload}
        (visible,) = visibility.project_payloads(visibility.scope_for(conn), [stored], legacy_notes=True)
        for key, updated in fields.items():
            payload[key] = _apply_visible_edit(stored.get(key, _MISSING), visible.get(key, _MISSING), updated)
    return {
        **{key: value for key, value in changes.items() if key not in {"add_alias", "payload_fields"}},
        "payload": payload,
    }
