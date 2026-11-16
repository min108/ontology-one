"""Review inbox over existing entities, preserving legacy action implementations."""

import json
from contextlib import closing

from backend import db, provenance
from backend.core.errors import ApiError
from backend.security.context import require_context

ASSETS = ("rule", "data", "knowledge", "skill")
WORKFLOW_ACTIONS = ("提交审核", "审核通过", "发布成果", "退回补充", "发起修订", "归档停用")


def mark_draft(conn: db.Connection, entity: dict, editor_id: str, changeset_id: str) -> None:
    if entity["status"] != "draft":
        raise ApiError("ITEM_CHANGED")
    entity["payload"]["r2_review"] = {"changeset_id": changeset_id, "last_editor_id": editor_id}
    conn.execute(
        "UPDATE entities SET payload=? WHERE id=?", (json.dumps(entity["payload"], ensure_ascii=False), entity["id"])
    )


def preserve_review_identity(old: dict, payload: dict) -> dict:
    # This metadata is owned by the service, even when the legacy editor replaces a payload.
    meta = dict(old["payload"]["r2_review"])
    meta.update(last_editor_id=require_context().user_id, submitted_by=None, submitted_at=None)
    return {**payload, "r2_review": meta}


def guard_marked_action(conn: db.Connection, entity: dict, body: dict) -> None:
    meta = entity["payload"].get("r2_review")
    if not meta:
        return
    person, action = require_context(), body.get("action")
    if action == "submit":
        if entity["status"] not in {"draft", "returned"}:
            raise ApiError("VERSION_CONFLICT")
        meta.update(submitted_by=person.user_id, submitted_name=person.username, submitted_at=db.now())
        conn.execute(
            "UPDATE entities SET payload=? WHERE id=?",
            (json.dumps(entity["payload"], ensure_ascii=False), entity["id"]),
        )
    if action in {"approve", "publish", "return"}:
        if person.role not in {"admin", "reviewer"} or person.readonly:
            raise ApiError("PERMISSION_DENIED")
        if person.user_id in {meta.get("submitted_by"), meta.get("last_editor_id")}:
            raise ApiError("SEPARATION_OF_DUTIES")
        if not meta.get("submitted_by"):
            raise ApiError("PRECONDITION_FAILED", "这项内容需要先由建设人员提交审核。")
        if action != "return":
            refs = entity["payload"].get("source_refs", [])
            if not refs:
                raise ApiError("EVIDENCE_NOT_FOUND")
            for ref in refs:
                provenance.normalize_source_ref(conn, ref["material_id"], ref)


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
    blocked = person.user_id in {meta.get("submitted_by"), meta.get("last_editor_id")} or person.username in {
        submitter,
        editor.get("actor", ""),
    }
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
        else "提交人与最后编辑人需要交由其他审核人员审核。"
        if blocked
        else "历史记录缺少提交人，请由建设人员重新提交。"
        if not submitter
        else "",
        "source_refs": entity["payload"].get("source_refs", []),
        "history": actions[:30],
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
            "SELECT * FROM entities WHERE status='review' AND kind IN ('rule','data','knowledge','skill') "
            + ("AND kind=? " if kind else "")
            + "ORDER BY updated_at DESC LIMIT 200",
            (kind,) if kind else (),
        )
        items = [review_info(conn, db.entity_json(row)) for row in rows]
        return {"items": items, "total": len(items)}


def decide(ident: str, decision: str, version: int, reason: str) -> dict:
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
        if decision == "return":
            if not reason.strip():
                raise ApiError("VALIDATION_FAILED", "请填写需要补充或修改的内容。")
            entity_action_in_transaction(conn, ident, {"action": "return", "reason": reason.strip()})
        else:
            refs = entity["payload"].get("source_refs", [])
            if not refs:
                raise ApiError("EVIDENCE_NOT_FOUND")
            for ref in refs:
                provenance.normalize_source_ref(conn, ref["material_id"], ref)
            entity_action_in_transaction(conn, ident, {"action": "approve", "reason": reason or "核对内容与原文后通过"})
            entity_action_in_transaction(conn, ident, {"action": "publish", "reason": "独立审核通过并发布"})
    return get(ident)
