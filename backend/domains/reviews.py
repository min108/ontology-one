"""Review inbox over existing entities, preserving legacy action implementations."""

import json
import uuid
from contextlib import closing

from backend import db
from backend.core.errors import ApiError
from backend.kernel import gates
from backend.security.context import Principal, require_context

ASSETS = tuple(sorted(gates.ASSETS))
WORKFLOW_ACTIONS = (
    "提交审核",
    "审核通过",
    "发布成果",
    "退回补充",
    "发起修订",
    "归档停用",
    "发布检查豁免",
    "申请发布检查豁免",
    "拒绝发布检查豁免",
    "关联核对",
)


def record_editor(entity: dict, editor_id: str, *, fresh_revision: bool = False) -> dict:
    previous = entity["payload"].get("r2_review")
    meta = dict(previous) if isinstance(previous, dict) else {}
    if fresh_revision or not meta.get("revision_id"):
        meta["revision_id"] = uuid.uuid4().hex
        editors = [] if fresh_revision else [meta.get("last_editor_id")]
    else:
        editors = list(meta.get("editor_ids") or [])
    meta.update(
        editor_ids=list(dict.fromkeys([item for item in [*editors, editor_id] if item])),
        last_editor_id=editor_id,
        submitted_by=None,
        submitted_at=None,
    )
    entity["payload"]["r2_review"] = meta
    return meta


def mark_draft(
    conn: db.Connection, entity: dict, editor_id: str, changeset_id: str, *, editor_ids: list[str] | None = None
) -> None:
    if entity["status"] != "draft":
        raise ApiError("ITEM_CHANGED")
    meta = record_editor(entity, editor_id)
    meta["changeset_id"] = changeset_id
    meta["editor_ids"] = list(dict.fromkeys([*meta["editor_ids"], *(editor_ids or [])]))
    conn.execute(
        "UPDATE entities SET payload=? WHERE id=?", (json.dumps(entity["payload"], ensure_ascii=False), entity["id"])
    )


def preserve_review_identity(old: dict, payload: dict, changes: dict | None = None) -> dict:
    meta = dict(old["payload"].get("r2_review") or {})
    result = {**payload, "r2_review": meta}
    content_changed = {k: v for k, v in payload.items() if k != "r2_review"} != {
        k: v for k, v in old["payload"].items() if k != "r2_review"
    } or any(
        (changes or {}).get(key, old.get(key)) != old.get(key)
        for key in ("kind", "title", "summary", "domain", "org_id")
    )
    if content_changed or not meta:
        record_editor(
            {**old, "payload": result},
            require_context().user_id,
            fresh_revision=old.get("status") in {"published", "approved", "archived"},
        )
    return result


def editor_ids(entity: dict) -> set[str]:
    meta = (entity.get("payload") or {}).get("r2_review") or {}
    return {
        value
        for value in [*(meta.get("editor_ids") or []), meta.get("last_editor_id"), meta.get("submitted_by")]
        if value
    }


def ensure_independent(entity: dict) -> None:
    person = require_context()
    if person.readonly or person.role not in {"admin", "reviewer"}:
        raise ApiError("PERMISSION_DENIED")
    if person.user_id in editor_ids(entity):
        raise ApiError("SEPARATION_OF_DUTIES", "本次修订的编辑人和提交人需由其他审核人员办理。")


def guard_marked_action(conn: db.Connection, entity: dict, body: dict) -> None:
    meta = entity["payload"].get("r2_review")
    person, action = require_context(), body.get("action")
    if not meta and action == "submit":
        from backend.kernel.kinds import persisted_kind

        if persisted_kind(entity["kind"]).governed:
            meta = record_editor(entity, person.user_id)
    if not meta:
        return
    if action == "submit":
        if entity["status"] not in {"draft", "returned"}:
            raise ApiError("VERSION_CONFLICT")
        meta.update(submitted_by=person.user_id, submitted_name=person.username, submitted_at=db.now())
        conn.execute(
            "UPDATE entities SET payload=? WHERE id=?",
            (json.dumps(entity["payload"], ensure_ascii=False), entity["id"]),
        )
    if action in {"approve", "publish", "return"}:
        ensure_independent(entity)
        if not meta.get("submitted_by"):
            raise ApiError("PRECONDITION_FAILED", "这项内容需要先由建设人员提交审核。")
        # The kernel checks all five gates after identity checks, including
        # verified evidence and the two explicitly permitted reviewer waivers.


def is_same_person(person: Principal, actor: str | None) -> bool:
    return bool(actor) and actor in {person.user_id, person.username}


def review_info(conn: db.Connection, entity: dict) -> dict:
    meta = entity["payload"].get("r2_review", {})
    actions = [
        dict(row)
        for row in conn.execute(
            "SELECT action,actor,created_at FROM activities WHERE entity_id=? ORDER BY id DESC", (entity["id"],)
        )
    ]
    submission = next((row for row in actions if row["action"] == "提交审核"), {})
    editor = next((row for row in actions if row["action"] not in WORKFLOW_ACTIONS), {})
    person = require_context()
    submitter = meta.get("submitted_name") or submission.get("actor", "")
    blocked = person.user_id in editor_ids(entity) or any(
        is_same_person(person, actor)
        for actor in (meta.get("submitted_by"), meta.get("last_editor_id"), submitter, editor.get("actor"))
    )
    package = bool(conn.execute("SELECT 1 FROM skill_documents WHERE entity_id=?", (entity["id"],)).fetchone())
    return {
        "entity": entity,
        "submitter": submitter or "历史记录未注明提交人",
        "submitted_at": meta.get("submitted_at") or submission.get("created_at", ""),
        "can_decide": bool(submitter)
        and not blocked
        and not package
        and person.role in {"admin", "reviewer"}
        and not person.readonly,
        "blocked_reason": "请在技能开发页完成技能包审核。"
        if package
        else "本次修订的编辑人和提交人需由其他审核人员办理。"
        if blocked
        else "历史记录缺少提交人，请由建设人员重新提交。"
        if not submitter
        else "",
        "source_refs": entity["payload"].get("source_refs", []),
        "history": actions[:30],
        "gates": gates.check_entity(conn, entity),
    }


def get(ident: str) -> dict:
    with closing(db.connect()) as conn:
        entity = db.entity_json(conn.execute("SELECT * FROM entities WHERE id=?", (ident,)).fetchone())
        if not entity or entity["kind"] not in ASSETS:
            raise ApiError("NOT_FOUND")
        return review_info(conn, entity)


def inbox(kind: str = "") -> dict:
    if kind and kind not in ASSETS:
        raise ApiError("VALIDATION_FAILED")
    with closing(db.connect()) as conn:
        rows = conn.execute(
            f"SELECT * FROM entities WHERE status='review' AND kind IN ({','.join('?' for _ in ASSETS)}) "
            + ("AND kind=? " if kind else "")
            + "ORDER BY updated_at DESC LIMIT 200",
            (*ASSETS, kind) if kind else ASSETS,
        )
        items = [review_info(conn, db.entity_json(row)) for row in rows]
        return {"items": items, "total": len(items)}


def decide(
    ident: str,
    decision: str,
    version: int,
    reason: str,
    *,
    waivers: dict | None = None,
    waiver_denials: dict | None = None,
) -> dict:
    from backend.api.v1.core import entity_action_in_transaction

    with closing(db.connect()) as conn, conn:
        entity = db.entity_json(conn.execute("SELECT * FROM entities WHERE id=? FOR UPDATE", (ident,)).fetchone())
        if not entity or entity["kind"] not in ASSETS:
            raise ApiError("NOT_FOUND")
        if entity["version"] != version or entity["status"] != "review":
            raise ApiError("VERSION_CONFLICT")
        info = review_info(conn, entity)
        if not info["can_decide"]:
            raise ApiError("SEPARATION_OF_DUTIES", info["blocked_reason"] or "需要由其他审核人员办理。")
        if waivers and waiver_denials and set(waivers) & set(waiver_denials):
            raise ApiError("VALIDATION_FAILED", "同一项申请不能同时豁免和拒绝。")
        if waiver_denials and decision != "return":
            raise ApiError("VALIDATION_FAILED", "拒绝豁免申请后请退回补充，不能发布。")
        if decision == "return":
            if waivers:
                gates.waive(conn, entity, waivers)
            if waiver_denials:
                gates.deny_applications(conn, entity, waiver_denials)
            if not reason.strip():
                raise ApiError("VALIDATION_FAILED", "请填写需要补充或修改的内容。")
            entity_action_in_transaction(
                conn, ident, {"action": "return", "reason": reason.strip(), "expected_version": version}
            )
        else:
            if waivers:
                gates.waive(conn, entity, waivers)
            entity_action_in_transaction(
                conn,
                ident,
                {"action": "approve", "reason": reason or "核对内容与原文后通过", "expected_version": version},
            )
            entity_action_in_transaction(
                conn, ident, {"action": "publish", "reason": "独立审核通过并发布", "expected_version": version + 1}
            )
    return get(ident)


def record_relation_editor(conn: db.Connection, entity: dict, editor_id: str) -> None:
    """A proposal author edits the revision; an independent relation decision does not."""
    from backend.kernel.kinds import persisted_kind

    if not persisted_kind(entity["kind"]).governed:
        return
    previous = dict(entity["payload"].get("r2_review") or {})
    submission = {key: previous[key] for key in ("submitted_by", "submitted_at", "submitted_name") if key in previous}
    meta = record_editor(entity, editor_id)
    meta.update(submission)
    conn.execute(
        "UPDATE entities SET payload=? WHERE id=?", (json.dumps(entity["payload"], ensure_ascii=False), entity["id"])
    )
