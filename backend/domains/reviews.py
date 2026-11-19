"""Review inbox over existing entities, preserving legacy action implementations."""

import json
import uuid
from collections.abc import Iterable, Mapping
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
    "撤回发布检查豁免申请",
    "拒绝发布检查豁免",
    "关联核对",
)


LEGACY_AUTHOR_ACTIONS = {
    "创建",
    "修订",
    "还原历史版本",
    "提交审核",
    "提出关联",
    "审定图谱关联",
    "Excel资产导入",
    "经验形成知识草稿",
    "经验稿修订知识",
    "新增字典草稿",
    "加载演示草稿",
    "采纳候选",
    "创建权责规范",
    "编辑权责规范",
    "创建技能草稿",
    "保存技能草稿",
    "提交技能审核",
    "申请发布检查豁免",
}
PUBLICATION_ACTIONS = {"发布成果", "发布技能版本"}
SEPARATION_MESSAGE = "本次修订的编辑人和提交人需由其他审核人员办理。"


def _legacy_edits(actions: list[dict]) -> tuple[set[str], bool]:
    """Return authenticated author names since the latest recorded publication."""
    actors = set()
    for row in actions:
        if row["action"] in PUBLICATION_ACTIONS:
            return actors, True
        if row["action"] in LEGACY_AUTHOR_ACTIONS and row["actor"]:
            actors.add(row["actor"])
    return actors, False


def _open_revision(entity: dict, conn: db.Connection | None) -> dict:
    """Only publication closes the authorship period. Existing releases keep their receipt."""
    previous = entity["payload"].get("r2_review")
    meta = dict(previous) if isinstance(previous, dict) else {}
    if meta.get("cycle_closed"):
        meta = {}
    elif "cycle_closed" not in meta and conn is not None and entity.get("id"):
        historic, released = _legacy_edits(_activities(conn, entity["id"]))
        if entity.get("status") == "published":
            # A.1 could append relationships after a release without changing
            # its published status. Only those later activities belong here.
            meta = {}
            if not released:
                historic.clear()
        elif released and not historic:
            meta = {}
        if historic:
            meta["legacy_actors"] = sorted(historic)
        if (
            entity["payload"].get("managed_skill")
            and entity.get("status") != "published"
            and (historic or not released)
        ):
            document = conn.execute(
                "SELECT last_editor_id,submitted_by FROM skill_documents WHERE entity_id=?", (entity["id"],)
            ).fetchone()
            if document:
                meta["editor_ids"] = [
                    *(meta.get("editor_ids") or []),
                    document["last_editor_id"],
                    document["submitted_by"],
                ]
    if not meta.get("revision_id"):
        meta["revision_id"] = uuid.uuid4().hex
    if conn is not None and entity.get("kind") == "knowledge" and entity["payload"].get("experience_candidate_id"):
        from backend.experience import adopted_legacy_editors

        meta["editor_ids"] = [*(meta.get("editor_ids") or []), *sorted(adopted_legacy_editors(conn, entity))]
        meta["experience_draft_tracked"] = True
    meta["cycle_closed"] = False
    meta["editor_ids"] = list(
        dict.fromkeys(
            value
            for value in [*(meta.get("editor_ids") or []), meta.get("last_editor_id"), meta.get("submitted_by")]
            if value
        )
    )
    entity["payload"]["r2_review"] = meta
    return meta


def record_editor(
    entity: dict, editor_id: str, *, conn: db.Connection | None = None, contributors: Iterable[str] = ()
) -> dict:
    meta = _open_revision(entity, conn)
    meta["editor_ids"] = list(dict.fromkeys([*meta["editor_ids"], *contributors, editor_id]))
    meta.pop("legacy_submitted_name", None)
    meta.update(last_editor_id=editor_id, submitted_by=None, submitted_name="", submitted_at=None)
    return meta


def record_submission(conn: db.Connection, entity: dict, person: Principal) -> None:
    meta = _open_revision(entity, conn)
    if person.user_id not in meta["editor_ids"]:
        meta["editor_ids"].append(person.user_id)
    meta.update(submitted_by=person.user_id, submitted_name=person.username, submitted_at=db.now())


def keep_release_boundary(conn: db.Connection, entity: dict, target: str) -> None:
    meta = entity["payload"].get("r2_review") or {}
    if target == "published":
        meta["cycle_closed"] = True
    elif target == "archived" and entity["status"] == "published" and "cycle_closed" not in meta:
        # Persist the known boundary before an upgrade-era release loses its
        # published status. Some such records have no publication activity.
        actors, released = _legacy_edits(_activities(conn, entity["id"]))
        if released and actors:
            meta = {"revision_id": uuid.uuid4().hex, "legacy_actors": sorted(actors), "cycle_closed": False}
        else:
            meta["cycle_closed"] = True
    else:
        return
    entity["payload"]["r2_review"] = meta


def mark_draft(
    conn: db.Connection, entity: dict, editor_id: str, changeset_id: str, *, editor_ids: list[str] | None = None
) -> None:
    if entity["status"] != "draft":
        raise ApiError("ITEM_CHANGED")
    meta = record_editor(entity, editor_id, conn=conn, contributors=editor_ids or ())
    meta["changeset_id"] = changeset_id
    conn.execute(
        "UPDATE entities SET payload=? WHERE id=?", (json.dumps(entity["payload"], ensure_ascii=False), entity["id"])
    )


def preserve_review_identity(
    old: dict, payload: dict, changes: dict | None = None, *, conn: db.Connection | None = None
) -> dict:
    meta = dict(old["payload"].get("r2_review") or {})
    result = {**payload, "r2_review": meta}
    content_changed = {k: v for k, v in payload.items() if k != "r2_review"} != {
        k: v for k, v in old["payload"].items() if k != "r2_review"
    } or any(
        (changes or {}).get(key, old.get(key)) != old.get(key)
        for key in ("kind", "title", "summary", "domain", "org_id")
    )
    if content_changed or not meta:
        record_editor({**old, "payload": result}, require_context().user_id, conn=conn)
    return result


def editor_ids(entity: dict) -> set[str]:
    meta = (entity.get("payload") or {}).get("r2_review") or {}
    return {
        value
        for value in [*(meta.get("editor_ids") or []), meta.get("last_editor_id"), meta.get("submitted_by")]
        if value
    }


def is_same_person(person: Principal, actor: str | None) -> bool:
    return bool(actor) and actor in {person.user_id, person.username}


def _activities(conn: db.Connection, ident: str) -> list[dict]:
    return [
        dict(row)
        for row in conn.execute(
            "SELECT action,actor,created_at FROM activities WHERE entity_id=? ORDER BY id DESC", (ident,)
        )
    ]


def is_review_participant(
    conn: db.Connection,
    entity: dict,
    person: Principal,
    *,
    actions: list[dict] | None = None,
    document: Mapping | None = None,
) -> bool:
    identity = entity["payload"].get("r2_review") or {}
    if person.user_id in editor_ids(entity) or any(
        is_same_person(person, name) for name in identity.get("legacy_actors", [])
    ):
        return True
    if entity.get("kind") == "knowledge" and entity["payload"].get("experience_candidate_id"):
        from backend.experience import adopted_legacy_editors

        if person.user_id in adopted_legacy_editors(conn, entity):
            return True
    if "cycle_closed" in identity:
        return False
    # Historic records can identify a person by username instead of stable ID.
    if document is None and entity["payload"].get("managed_skill"):
        document = conn.execute(
            "SELECT last_editor_id,submitted_by FROM skill_documents WHERE entity_id=?", (entity["id"],)
        ).fetchone()
    if document and person.user_id in {document["last_editor_id"], document["submitted_by"]}:
        return True
    if actions is None:
        actions = _activities(conn, entity["id"])
    if legacy_approved(entity):
        names, _ = _legacy_edits(actions)
        if any(is_same_person(person, name) for name in names):
            return True
    editor = next((row["actor"] for row in actions if row["action"] not in WORKFLOW_ACTIONS), None)
    submitter = next((row["actor"] for row in actions if row["action"] == "提交审核"), None)
    return any(is_same_person(person, actor) for actor in (identity.get("submitted_name"), editor, submitter))


def ensure_independent(conn: db.Connection, entity: dict) -> None:
    person = require_context()
    if person.readonly or person.role not in {"admin", "reviewer"}:
        raise ApiError("PERMISSION_DENIED")
    if is_review_participant(conn, entity, person):
        raise ApiError("SEPARATION_OF_DUTIES", SEPARATION_MESSAGE)


def guard_marked_action(conn: db.Connection, entity: dict, body: dict) -> None:
    from backend.kernel.kinds import persisted_kind

    meta = entity["payload"].get("r2_review")
    if not meta and not persisted_kind(entity["kind"]).governed:
        return
    person, action = require_context(), body.get("action")
    if action == "submit":
        if entity["status"] not in {"draft", "returned"}:
            raise ApiError("VERSION_CONFLICT")
        record_submission(conn, entity, person)
    if action in {"approve", "publish", "return"}:
        ensure_independent(conn, entity)
        if not meta:
            return
        recorded_release = action == "publish" and (
            legacy_approved(entity) or (meta.get("cycle_closed") and entity["status"] == "published")
        )
        if not recorded_release and not (meta.get("submitted_by") or meta.get("legacy_submitted_name")):
            raise ApiError("PRECONDITION_FAILED", "这项内容需要先由建设人员提交审核。")


def review_info(conn: db.Connection, entity: dict) -> dict:
    meta = entity["payload"].get("r2_review", {})
    actions = _activities(conn, entity["id"])
    submission = next((row for row in actions if row["action"] == "提交审核"), {})
    person = require_context()
    submitter = meta.get("submitted_name") or submission.get("actor", "")
    has_submission = bool(submitter) or legacy_approved(entity)
    blocked = is_review_participant(conn, entity, person, actions=actions)
    package = bool(conn.execute("SELECT 1 FROM skill_documents WHERE entity_id=?", (entity["id"],)).fetchone())
    return {
        "entity": entity,
        "submitter": submitter or "历史记录未注明提交人",
        "submitted_at": meta.get("submitted_at") or submission.get("created_at", ""),
        "can_decide": has_submission
        and not blocked
        and not package
        and person.role in {"admin", "reviewer"}
        and not person.readonly,
        "blocked_reason": "请在技能开发页完成技能包审核。"
        if package
        else SEPARATION_MESSAGE
        if blocked
        else "历史记录缺少提交人，请由建设人员重新提交。"
        if not has_submission
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


def legacy_approved(entity: dict) -> bool:
    return entity["status"] == "approved" and "cycle_closed" not in (entity["payload"].get("r2_review") or {})


def inbox(kind: str = "") -> dict:
    if kind and kind not in ASSETS:
        raise ApiError("VALIDATION_FAILED")
    with closing(db.connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM entities WHERE (status='review' OR "
            "(status='approved' AND JSON_EXTRACT(payload,'$.r2_review.cycle_closed') IS NULL)) "
            f"AND kind IN ({','.join('?' for _ in ASSETS)}) "
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
        approved = legacy_approved(entity)
        if entity["version"] != version or not (
            entity["status"] == "review" or (approved and decision == "approve_publish")
        ):
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
            if not approved:
                entity_action_in_transaction(
                    conn,
                    ident,
                    {"action": "approve", "reason": reason or "核对内容与原文后通过", "expected_version": version},
                )
                version += 1
            entity_action_in_transaction(
                conn,
                ident,
                {
                    "action": "publish",
                    "reason": reason or ("发布已审核的历史内容" if approved else "独立审核通过并发布"),
                    "expected_version": version,
                },
            )
    return get(ident)


def record_relation_editor(conn: db.Connection, entity: dict, editor_id: str) -> None:
    """A proposal author edits the revision; an independent relation decision does not."""
    from backend.kernel.kinds import persisted_kind

    if not persisted_kind(entity["kind"]).governed:
        return
    previous = dict(entity["payload"].get("r2_review") or {})
    submission = {
        key: previous[key]
        for key in ("submitted_by", "submitted_at", "submitted_name", "legacy_submitted_name")
        if key in previous
    }
    meta = record_editor(entity, editor_id, conn=conn)
    if meta["revision_id"] == previous.get("revision_id"):
        meta.update(submission)
    if entity["status"] in {"review", "approved"} and "cycle_closed" not in previous and not meta.get("submitted_by"):
        for row in _activities(conn, entity["id"]):
            if row["action"] in PUBLICATION_ACTIONS:
                break
            if row["action"] == "提交审核" and row["actor"]:
                meta.update(
                    legacy_submitted_name=row["actor"], submitted_name=row["actor"], submitted_at=row["created_at"]
                )
                break
    conn.execute(
        "UPDATE entities SET payload=? WHERE id=?", (json.dumps(entity["payload"], ensure_ascii=False), entity["id"])
    )
