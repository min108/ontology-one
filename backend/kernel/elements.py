"""Versioned element writes and read-model hooks in the caller's transaction.

API bindings retain specialized-domain guards. This service owns validation,
review identity, metadata, snapshots and derived links; it never commits.
"""

import json
import uuid
from copy import deepcopy

from fastapi import HTTPException

from backend import db
from backend.asset_kinds import canonical_entity, canonical_kind
from backend.kernel import rules
from backend.kernel.kinds import get_kind, persisted_kind, requires_quality_gates
from backend.kernel.metadata import SNAPSHOT_META_FIELDS, get_meta, sync_meta
from backend.kernel.schemas import validate_payload
from backend.security.context import require_context

PUBLIC_META_FIELDS = frozenset({"valid_from", "valid_to", "element_key", "provider_note"})
STATUS_LABELS = {
    "review": "提交审核",
    "approved": "审核通过",
    "returned": "退回补充",
    "published": "发布成果",
    "draft": "发起修订",
    "archived": "归档停用",
    "completed": "完成任务",
}


def _writable() -> None:
    if require_context().readonly:
        raise HTTPException(403, "当前为只读范围，不能修改内容。")


def _reason(value: object, default: str) -> str:
    value = default if value is None else value
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(422, "请填写修改原因。")
    return value.strip()


def check_version(old: dict, changes: dict) -> None:
    if "expected_version" in changes and (
        type(changes["expected_version"]) is not int or changes["expected_version"] != old["version"]
    ):
        raise HTTPException(409, "内容已更新，请刷新后再操作。")


def _meta_updates(body: dict) -> dict:
    updates = body.get("meta", {})
    if not isinstance(updates, dict) or set(updates) - PUBLIC_META_FIELDS:
        raise HTTPException(422, "包含不允许修改的要素附加字段。")
    return updates


def _target(conn: db.Connection, ident: object, kinds: set[str] | None = None) -> dict:
    if not isinstance(ident, str) or not ident:
        raise HTTPException(422, "请填写有效的关联对象。")
    target = db.entity_json(conn.execute("SELECT * FROM entities WHERE id=? FOR UPDATE", (ident,)).fetchone())
    if not target or target["status"] == "archived" or target["payload"].get("active_asset") in (False, 0):
        raise HTTPException(422, "关联对象不存在或已成为历史内容。")
    if kinds and target["kind"] not in kinds:
        raise HTTPException(422, "关联对象的种类不正确。")
    return target


def _validate_references(conn: db.Connection, entity: dict) -> None:
    """Resolve references only inside the caller's authorized tenant connection."""
    from backend.kernel.relations import TARGET_KINDS, derived_edges

    payload, kind = entity["payload"], entity["kind"]
    for edge in derived_edges(entity):
        _target(conn, edge["target"], TARGET_KINDS[edge["type"]])
    if kind == "object_type":
        if payload.get("is_interface"):
            raise HTTPException(422, "通用对象接口只能在集团核心中创建和维护。")
        seen = {entity["id"]}
        parent = payload.get("extends")
        while parent:
            if parent in seen:
                raise HTTPException(422, "业务对象的继承关系不能成环。")
            seen.add(parent)
            parent = _target(conn, parent, {"object_type"})["payload"].get("extends")
        own = {prop["key"]: prop for prop in payload.get("properties", [])}
        for ident in payload.get("implements", []):
            interface = _target(conn, ident, {"object_type"})["payload"]
            if not interface.get("is_interface"):
                raise HTTPException(422, "实现关系必须指向通用对象接口。")
            for prop in interface.get("properties", []):
                if prop.get("required") and (
                    prop["key"] not in own
                    or own[prop["key"]].get("datatype") != prop.get("datatype")
                    or not own[prop["key"]].get("required")
                ):
                    raise HTTPException(422, "请先补齐通用对象接口要求的必备属性及其类型。")
    elif kind == "link_type" and payload.get("join"):
        for endpoint, field in (("source_type", "source_property"), ("target_type", "target_property")):
            target = _target(conn, payload.get(endpoint), {"object_type"})
            if payload["join"].get(field) not in {prop["key"] for prop in target["payload"].get("properties", [])}:
                raise HTTPException(422, "关联条件必须指向对应业务对象的已有属性。")
    elif kind == "term":
        for ident in payload.get("refers_to", []):
            _target(conn, ident)
    elif kind == "position" and payload.get("reports_to"):
        _target(conn, payload["reports_to"], {"position"})
    elif kind == "process":
        for step in payload.get("steps", []):
            for basis in step.get("basis", []):
                if basis.get("material_id"):
                    _target(conn, basis["material_id"], {"material"})


def _prepare(conn: db.Connection, entity: dict, *, legacy: bool = False) -> dict:
    result = deepcopy(entity)
    title = result.get("title")
    if not isinstance(title, str) or not title.strip():
        raise HTTPException(400, "请填写名称。")
    result["title"] = title.strip()
    try:
        validate_payload(result["kind"], result["payload"], allow_legacy=legacy)
        if result["kind"] == "rule":
            rules.validate_judgement_shape(result["payload"])
        _validate_references(conn, result)
        if result["kind"] in {"rule", "metric"}:
            from backend.kernel.gates import expression_context

            result["payload"] = rules.prepare_payload(
                result["kind"], result["payload"], **expression_context(conn, result)
            )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return result


def _preserve_review(conn: db.Connection, old: dict, payload: dict, changes: dict) -> dict:
    from backend.domains.reviews import preserve_review_identity

    result = {key: value for key, value in payload.items() if key != "r2_review"}
    if old["payload"].get("r2_review") or persisted_kind(old["kind"]).governed:
        return preserve_review_identity(old, result, changes, conn=conn)
    return result


def _confirm_sentence(old: dict, payload: dict, changes: dict) -> None:
    def expressions(value: dict) -> list[dict]:
        judgement = value.get("judgement")
        judgement = judgement if isinstance(judgement, dict) else {}
        candidates = [value.get("formula"), judgement.get("condition"), judgement.get("requirement")]
        exceptions = judgement.get("exceptions")
        candidates.extend(exceptions if isinstance(exceptions, list) else [])
        return [item for item in candidates if isinstance(item, dict)]

    if changes.get("sentence_confirmed") is True:
        return
    before, after = expressions(old["payload"]), expressions(payload)
    for previous, current in zip(before, after, strict=False):
        if previous.get("oxl") == current.get("oxl") and previous.get("sentence") != current.get("sentence"):
            raise HTTPException(409, "句子与判定式可能不一致，请核对后重新确认。")


def snapshot(conn: db.Connection, entity: dict, reason: str) -> dict:
    reason = _reason(reason, "保留版本")
    result = deepcopy(entity)
    meta = get_meta(conn, entity["id"]) or sync_meta(conn, entity)
    result["_meta"] = {key: meta.get(key) for key in SNAPSHOT_META_FIELDS}
    conn.execute(
        "INSERT INTO versions(entity_id,version,snapshot,created_at,reason) VALUES(?,?,?,?,?)",
        (entity["id"], entity["version"], json.dumps(result, ensure_ascii=False), db.now(), reason),
    )
    return result


def sync_after_write(
    conn: db.Connection, entity: dict, meta_updates: dict | None = None, *, content_changed: bool = True
) -> dict:
    """Shared adoption/write hook; B-stage search indexing attaches here too."""
    from backend.graph import invalidate_graph_cache
    from backend.kernel.cards import cache_card
    from backend.kernel.relations import sync_derived

    try:
        meta = sync_meta(conn, entity, meta_updates)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    if content_changed:
        sync_derived(conn, entity)
    cache_card(conn, entity)
    invalidate_graph_cache(require_context().tenant_id)
    return {**entity, "_meta": get_meta(conn, entity["id"]) or meta}


def _enforce_identity_status(conn: db.Connection, entity: dict) -> None:
    # Identity editors keep their existing direct-status authority, but a write
    # into (or remaining in) a released/review state must satisfy QG2 and QG5.
    # Reads never revoke legacy published content; restoring content is a draft.
    if (
        requires_quality_gates(entity["kind"])
        and not get_kind(entity["kind"]).governed
        and entity["status"] in {"review", "approved", "published"}
    ):
        from backend.kernel.gates import enforce

        enforce(conn, entity)


def create_draft(conn: db.Connection, body: dict) -> dict:
    _writable()
    kind = canonical_kind(body.get("kind", "knowledge"))
    try:
        definition = get_kind(kind)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    if definition.governed and body.get("status", "draft") != "draft":
        raise HTTPException(400, "新建资产请先保存为草稿，再提交审核。")
    ident = body.get("id") or definition.id_prefix + uuid.uuid4().hex[:10]
    if conn.execute("SELECT id FROM entities WHERE id=?", (ident,)).fetchone():
        raise HTTPException(409, "该编号已存在。")
    stamp = db.now()
    entity = {
        "id": ident,
        "kind": kind,
        "title": body.get("title", ""),
        "summary": body.get("summary", ""),
        "domain": body.get("domain", "综合管理"),
        "org_id": db.current_org_id(),
        "status": body.get("status", "draft"),
        "source_kind": body.get("source_kind", "user"),
        "payload": body.get("payload", {}),
        "created_at": stamp,
        "updated_at": stamp,
        "version": 1,
    }
    entity = _prepare(conn, entity)
    entity["payload"].pop("r2_review", None)
    if definition.governed:
        from backend.domains.reviews import record_editor

        identity = record_editor(entity, require_context().user_id)
        identity["changeset_id"] = ""
        identity.pop("submitted_by", None)
        identity.pop("submitted_at", None)
    updates = _meta_updates(body)
    conn.execute(
        "INSERT INTO entities(id,kind,title,summary,domain,org_id,status,source_kind,payload,"
        "created_at,updated_at,version) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        tuple(json.dumps(value, ensure_ascii=False) if key == "payload" else value for key, value in entity.items()),
    )
    entity = sync_after_write(conn, entity, updates)
    from backend.kernel.gate_fields import record_editor_changes

    record_editor_changes(conn, entity, None)
    snapshot(conn, entity, "创建草稿")
    _enforce_identity_status(conn, entity)
    db.log(conn, ident, "创建", entity["title"])
    return entity


def update_draft(conn: db.Connection, old: dict, changes: dict) -> dict:
    _writable()
    check_version(old, changes)
    reason = _reason(changes.get("reason"), "内容修订")
    updates = _meta_updates(changes)
    from backend.domains.reviews import record_editor
    from backend.kernel.gate_fields import field_values, record_editor_changes

    previous_fields = field_values(conn, old, get_meta(conn, old["id"]) or {})
    entity = deepcopy(old)
    if "kind" in changes and changes["kind"] != old["kind"]:
        try:
            allowed = get_kind(changes["kind"]).governed and persisted_kind(old["kind"]).governed
        except ValueError:
            allowed = False
        if old["status"] not in {"draft", "returned"} or not allowed:
            raise HTTPException(422, "仅未提交的业务草稿可在核对后改种类。")
        entity["kind"] = changes["kind"]
    for key in ("title", "summary", "domain", "status", "payload"):
        if key in changes:
            entity[key] = deepcopy(changes[key])
    if persisted_kind(old["kind"]).governed:
        if entity["status"] not in {old["status"], "draft"}:
            raise HTTPException(400, "请通过提交、审核和发布操作变更资产状态。")
        if old["status"] in {"published", "approved", "review"} and any(
            key in changes for key in ("title", "summary", "domain", "org_id", "payload", "meta", "waiver_applications")
        ):
            entity["status"] = "draft"
    entity = _prepare(conn, entity, legacy=True)
    _confirm_sentence(old, entity["payload"], changes)
    entity["payload"] = _preserve_review(conn, old, entity["payload"], changes)
    current_meta = get_meta(conn, old["id"]) or {}
    if entity["payload"].get("r2_review") and any(current_meta.get(key) != value for key, value in updates.items()):
        record_editor({**old, "payload": entity["payload"]}, require_context().user_id, conn=conn)
    snapshot(conn, old, reason)
    entity.update(version=old["version"] + 1, updated_at=db.now())
    conn.execute(
        "UPDATE entities SET kind=?,title=?,summary=?,domain=?,status=?,payload=?,version=?,updated_at=? WHERE id=?",
        (
            entity["kind"],
            entity["title"],
            entity["summary"],
            entity["domain"],
            entity["status"],
            json.dumps(entity["payload"], ensure_ascii=False),
            entity["version"],
            entity["updated_at"],
            entity["id"],
        ),
    )
    entity = sync_after_write(conn, entity, updates)
    record_editor_changes(conn, entity, previous_fields)
    if "waiver_applications" in changes:
        from backend.kernel.gates import apply_for_waiver

        apply_for_waiver(conn, entity, changes["waiver_applications"])
    _enforce_identity_status(conn, entity)
    db.log(conn, entity["id"], "修订", reason)
    return entity


def transition(conn: db.Connection, old: dict, target: str, reason: str, actor: str = "") -> dict:
    _writable()
    if target not in STATUS_LABELS:
        raise HTTPException(400, "不支持此操作。")
    reason = _reason(reason, STATUS_LABELS[target])
    from backend.domains.reviews import guard_marked_action, keep_release_boundary, record_editor
    from backend.kernel import gates

    actions = {"review": "submit", "approved": "approve", "published": "publish", "returned": "return"}
    if target in actions:
        guard_marked_action(conn, old, {"action": actions[target], "reason": reason})
    if requires_quality_gates(old["kind"]) and target in {"review", "approved", "published"}:
        if target == "review":
            gates.enforce(conn, old, stage="submit")
        else:
            gates.enforce(conn, old)
    snapshot(conn, old, reason)
    entity = deepcopy(old)
    if target == "draft" and persisted_kind(old["kind"]).governed:
        record_editor({**old, "payload": entity["payload"]}, require_context().user_id, conn=conn)
    if persisted_kind(old["kind"]).governed:
        keep_release_boundary(conn, entity, target)
    entity.update(status=target, version=old["version"] + 1, updated_at=db.now())
    conn.execute(
        "UPDATE entities SET status=?,payload=?,version=?,updated_at=? WHERE id=?",
        (
            entity["status"],
            json.dumps(entity["payload"], ensure_ascii=False),
            entity["version"],
            entity["updated_at"],
            entity["id"],
        ),
    )
    entity = sync_after_write(conn, entity, content_changed=False)
    _enforce_identity_status(conn, entity)
    db.log(conn, entity["id"], STATUS_LABELS[target], reason, actor)
    return entity


def restore(conn: db.Connection, old: dict, saved: dict, reason: str) -> dict:
    _writable()
    reason = _reason(reason, "还原历史版本")
    saved = canonical_entity(saved)
    if saved.get("id") != old["id"] or saved.get("kind") != old["kind"]:
        raise HTTPException(422, "历史版本与当前内容不匹配。")
    changes = {key: saved.get(key, old.get(key)) for key in ("title", "summary", "domain", "payload")}
    changes.update(status="draft", reason="还原前快照：" + reason, sentence_confirmed=True)
    # Module/owner/sensitivity assignments are current authorization, not content
    # supplied by an old snapshot. Restore only publicly editable business dates.
    changes["meta"] = {key: value for key, value in saved.get("_meta", {}).items() if key in PUBLIC_META_FIELDS}
    result = update_draft(conn, old, changes)
    db.log(conn, old["id"], "还原历史版本", f"还原版本{saved.get('version')}为草稿：{reason}")
    return result
