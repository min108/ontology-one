"""Recipe A composes legacy ingestion, extraction, anchors and adoption."""

import json
import uuid
from contextlib import closing

from fastapi import HTTPException

from backend import ai, db, jobs, materials, provenance
from backend.core.errors import ApiError
from backend.security.context import require_context

JSON_FIELDS = {
    "source_refs",
    "stats",
    "after_data",
    "before_data",
    "evidence",
    "validation",
    "requirement_ids",
    "progress",
    "input",
    "state",
    "degraded",
    "error",
}


def decode(row: db.Row) -> dict:
    value = dict(row)
    for key in JSON_FIELDS & value.keys():
        if isinstance(value[key], str):
            value[key] = json.loads(value[key])
    if value.get("confidence") is not None:
        value["confidence"] = float(value["confidence"])
    return value


def confidence(*, exact: bool, complete: bool, unique: bool, local: bool) -> tuple[float, str]:
    score = round(0.5 + 0.2 * exact + 0.1 * complete + 0.1 * unique, 3)
    if local or not exact:
        score = min(score, 0.49)
    return score, "sure" if score >= 0.8 else "check" if score >= 0.5 else "doubt"


def model_available() -> bool:
    try:
        return bool(ai.config()["api_key"])
    except ai.AIError:
        return False


def start(material_id: str) -> dict:
    person = require_context()
    with closing(db.connect()) as conn, conn:
        conn.execute("SELECT id FROM entities WHERE id=? FOR UPDATE", (material_id,)).fetchone()
        item = materials.material(conn, material_id)
        if jobs.active_for("material_extract", material_id) or jobs.active_for("recipe_run", material_id):
            raise ApiError("RUN_BUSY")
        ident, stamp = "RCP-" + uuid.uuid4().hex[:24], db.now()
        title = ("《" + item["title"] + "》整理")[:255]
        conn.execute(
            "INSERT INTO recipe_runs(id,recipe,title,status,stage,stage_message,input,state,created_by,created_at) "
            "VALUES(?,'A',?,'queued','queued',?,?,?, ?,?)",
            (
                ident,
                title,
                "已收到材料，正在等待整理",
                materials.dump({"material_id": material_id}),
                "{}",
                person.user_id,
                stamp,
            ),
        )
        conn.execute(
            "INSERT INTO jobs(id,kind,entity_id,status,progress,message,result,created_at,updated_at) "
            "VALUES(?,'recipe_run',?,'queued',0,?,'{}',?,?)",
            (ident, material_id, "已收到材料，正在等待整理", stamp, stamp),
        )
        jobs.enqueue(ident, "recipe_run", material_id, [ident])
    return status(ident)


def status(ident: str) -> dict:
    with closing(db.connect()) as conn:
        row = conn.execute("SELECT * FROM recipe_runs WHERE id=?", (ident,)).fetchone()
        if not row:
            raise ApiError("NOT_FOUND")
        result = decode(row)
    queued = jobs.queue_status(ident)
    if not result["changeset_id"] and queued and queued["status"] in {"failed", "cancelled"}:
        result.update(status="failed", stage_message=queued["last_error"] or "整理未完成，请重新提交重试。")
    return result


def stage(ident: str, name: str, message: str, done: int, state: dict) -> None:
    with closing(db.connect()) as conn, conn:
        conn.execute(
            "UPDATE recipe_runs SET status='running',stage=?,stage_message=?,progress=?,state=?,"
            "started_at=coalesce(started_at,?),heartbeat_at=? WHERE id=?",
            (
                name,
                message,
                materials.dump({"done": done, "total": 4}),
                materials.dump(state),
                db.now(),
                db.now(),
                ident,
            ),
        )
        materials.job_update(conn, ident, "running", done * 25, message)


async def run_recipe(ident: str) -> None:
    run = status(ident)
    if run["changeset_id"]:
        return
    material_id = run["input"]["material_id"]
    state = run["state"] or {}
    try:
        if not state.get("segmented"):
            stage(ident, "segment", "正在按条款拆分原文", 0, state)
            with closing(db.connect()) as conn:
                item = materials.material(conn, material_id)
                text = provenance.get_source_version(conn, material_id)["text"]
                has_chunks = bool(provenance.get_active_chunks(conn, material_id))
            if not has_chunks:
                parts = await materials.propose_segments(item, text, "default")
                with closing(db.connect()) as conn, conn:
                    conn.execute("SELECT id FROM entities WHERE id=? FOR UPDATE", (material_id,)).fetchone()
                    item = materials.material(conn, material_id)
                    draft, source = materials._reading_draft(conn, material_id)
                    draft["segments"] = [
                        {
                            "id": materials.uid("seg"),
                            "start": part["source_start"],
                            "end": part["source_end"],
                            "heading": part["heading"],
                            "locked_after": False,
                        }
                        for part in parts
                    ]
                    materials._confirm_reading(conn, item, draft, source, "整理任务自动按原文分段")
            state["segmented"] = True
        if not state.get("extracted"):
            stage(ident, "extract", "正在提炼制度中的要求", 1, state)
            online = model_available()
            if online:
                await materials.run_extraction(material_id, ident, "online", True)
            with closing(db.connect()) as conn, conn:
                conn.execute("SELECT id FROM entities WHERE id=? FOR UPDATE", (material_id,)).fetchone()
                item = materials.material(conn, material_id)
                chunks = provenance.get_active_chunks(conn, material_id)
                candidates = item["payload"].setdefault("candidates", [])
                coverage = item["payload"].get("coverage", {})
                fallback = [
                    chunk
                    for chunk in chunks
                    if not online or coverage.get(chunk["id"], {}).get("status") != "completed"
                ]
                ids = {candidate["id"] for candidate in candidates}
                for chunk in fallback:
                    if not chunk["content"].strip():
                        continue
                    title = chunk["content"].strip().splitlines()[0][:120]
                    cid = materials.candidate_id(material_id, "rule", title, [chunk["id"]])
                    if cid not in ids:
                        candidates.append(
                            {
                                "id": cid,
                                "kind": "rule",
                                "title": title,
                                "summary": "按原文条款保留，请核对适用范围与例外。",
                                "content": chunk["content"],
                                "source_chunk_ids": [chunk["id"]],
                                "source_quotes": [{"chunk_id": chunk["id"], "quote": chunk["content"]}],
                                "conditions": [],
                                "questions": ["未经智能整理，请逐条完善。"],
                                "status": "pending",
                                "extraction_mode": "local",
                                "created_at": db.now(),
                            }
                        )
                materials.save_payload(conn, item, item["payload"])
                state.update(extracted=True, local=bool(fallback), used_model=online and len(fallback) < len(chunks))
        stage(ident, "locate", "正在逐字核对引文与原文位置", 2, state)
        with closing(db.connect()) as conn, conn:
            locked = conn.execute("SELECT * FROM recipe_runs WHERE id=? FOR UPDATE", (ident,)).fetchone()
            if locked["changeset_id"]:
                return
            item = materials.material(conn, material_id)
            cs_id, stamp = "CS-" + uuid.uuid4().hex[:24], db.now()
            refs = [{"material_id": material_id, "source_version": item["payload"].get("source_version")}]
            conn.execute(
                "INSERT INTO changesets(id,title,summary,origin,origin_ref,source_refs,created_by,assigned_to,"
                "created_at,updated_at) "
                "VALUES(?,?,?,'recipe',?,?,?,?,?,?)",
                (
                    cs_id,
                    run["title"],
                    "请对照原文核对条目，再采纳并提交独立审核。",
                    ident,
                    materials.dump(refs),
                    run["created_by"],
                    run["created_by"],
                    stamp,
                    stamp,
                ),
            )
            for index, candidate in enumerate(item["payload"].get("candidates", []), 1):
                if candidate.get("entity_id"):
                    continue
                evidence, issues = [], []
                for quote in candidate.get("source_quotes", []):
                    try:
                        evidence.append(provenance.normalize_source_ref(conn, material_id, quote, min_quote_length=1))
                    except HTTPException:
                        issues.append("引文未逐字出现在原文中，请重新核对出处。")
                if not evidence:
                    issues.append("尚未找到可定位的原文，不能采纳。")
                duplicate = conn.execute(
                    "SELECT id FROM entities WHERE kind=? AND title=? LIMIT 1", (candidate["kind"], candidate["title"])
                ).fetchone()
                local = candidate.get("extraction_mode") != "online"
                complete = not candidate.get("questions") and bool(candidate.get("content"))
                score, tier = confidence(
                    exact=bool(evidence) and not issues, complete=complete, unique=not duplicate, local=local
                )
                conn.execute(
                    "INSERT INTO changeset_items(id,changeset_id,seq,op,target_kind,target_id,after_data,one_liner,"
                    "rationale,evidence,content_origin,confidence,tier,validation,duplicate_of) "
                    "VALUES(?,?,?,'create_element',?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        "CSI-" + uuid.uuid4().hex[:24],
                        cs_id,
                        index,
                        candidate["kind"],
                        "tmp:" + str(index),
                        materials.dump(candidate),
                        (candidate.get("summary") or candidate["title"])[:500],
                        "未经智能整理，请逐条完善" if local else "依据原文整理，请确认适用条件与例外",
                        materials.dump(evidence),
                        "local" if local else "source",
                        score,
                        tier,
                        materials.dump({"level": "error" if issues else "warn" if local else "ok", "issues": issues}),
                        duplicate["id"] if duplicate else None,
                    ),
                )
            summary = (
                "智能整理暂不可用，已按条款拆分，请逐条完善" if state["local"] else "建设方案已就绪，请对照原文核对"
            )
            conn.execute(
                "UPDATE recipe_runs SET status=?,stage='ready',stage_message=?,changeset_id=?,state=?,progress=?,"
                "degraded=?,used_model=?,finished_at=?,heartbeat_at=? WHERE id=?",
                (
                    "partial" if state["local"] else "completed",
                    summary,
                    cs_id,
                    materials.dump(state),
                    materials.dump({"done": 4, "total": 4}),
                    materials.dump(["智能整理不可用或部分内容未通过校验，已按条款保留"] if state["local"] else []),
                    int(state["used_model"]),
                    stamp,
                    stamp,
                    ident,
                ),
            )
            materials.job_update(conn, ident, "completed", 100, summary, {"changeset_id": cs_id})
    except Exception:
        with closing(db.connect()) as conn, conn:
            conn.execute(
                "UPDATE recipe_runs SET status='failed',stage_message=? WHERE id=?",
                ("整理未完成，原文与已保存内容仍保留，可在素材页继续。", ident),
            )
            materials.job_update(conn, ident, "failed", 0, "整理未完成，原文与已保存内容仍保留")
        raise


def get(ident: str) -> dict:
    with closing(db.connect()) as conn:
        row = conn.execute("SELECT * FROM changesets WHERE id=?", (ident,)).fetchone()
        if not row:
            raise ApiError("NOT_FOUND")
        result = decode(row)
        result["items"] = [
            decode(item)
            for item in conn.execute("SELECT * FROM changeset_items WHERE changeset_id=? ORDER BY seq", (ident,))
        ]
        result["can_submit"] = any(
            item["decision"] in {"accepted", "edited"}
            and (
                not item["applied_entity_id"]
                or conn.execute(
                    "SELECT 1 FROM entities WHERE id=? AND status IN ('draft','returned')",
                    (item["applied_entity_id"],),
                ).fetchone()
            )
            for item in result["items"]
        )
        result["can_submit"] = bool(result["can_submit"])
        ref = result["source_refs"][0]
        source = provenance.get_source_version(conn, ref["material_id"], ref.get("source_version"))
        result["source_text"] = source["text"]
        result["source_material_id"] = ref["material_id"]
        return result


def lock_changeset(conn: db.Connection, ident: str) -> dict:
    row = conn.execute("SELECT * FROM changesets WHERE id=? FOR UPDATE", (ident,)).fetchone()
    if not row:
        raise ApiError("NOT_FOUND")
    return decode(row)


def decide(ident: str, ids: list[str], decision: str, edit: dict | None) -> dict:
    if (decision == "edited") != (edit is not None) or (edit and len(ids) != 1):
        raise ApiError("VALIDATION_FAILED")
    with closing(db.connect()) as conn, conn:
        lock_changeset(conn, ident)
        for item_id in dict.fromkeys(ids):
            row = conn.execute(
                "SELECT * FROM changeset_items WHERE id=? AND changeset_id=? FOR UPDATE", (item_id, ident)
            ).fetchone()
            if not row:
                raise ApiError("NOT_FOUND")
            item = decode(row)
            if item["applied_entity_id"]:
                raise ApiError("ITEM_CHANGED", "这条内容已形成草稿，请在草稿页继续修改。")
            if decision in {"accepted", "edited"} and item["validation"]["level"] == "error":
                raise ApiError("EVIDENCE_NOT_FOUND")
            after = item["after_data"] | (edit or {})
            validation = item["validation"]
            if edit and any(after.get(key) != item["after_data"].get(key) for key in ("title", "content", "kind")):
                validation["last_content_editor_id"] = require_context().user_id
            conn.execute(
                "UPDATE changeset_items SET decision=?,after_data=?,target_kind=?,one_liner=?,"
                "decided_by=?,validation=?,decided_at=? WHERE id=?",
                (
                    decision,
                    materials.dump(after),
                    after["kind"],
                    (
                        after["content"][:500]
                        if edit and after.get("content") != item["after_data"].get("content")
                        else item["one_liner"]
                    ),
                    require_context().user_id,
                    materials.dump(validation),
                    db.now(),
                    item_id,
                ),
            )
        conn.execute("UPDATE changesets SET updated_at=? WHERE id=?", (db.now(), ident))
    return get(ident)


def apply(ident: str, *, submit: bool) -> dict:
    from backend.api.v1.core import entity_action_in_transaction
    from backend.domains.reviews import mark_draft

    person = require_context()
    with closing(db.connect()) as conn, conn:
        proposal = lock_changeset(conn, ident)
        material_id = proposal["source_refs"][0]["material_id"]
        conn.execute("SELECT id FROM entities WHERE id=? FOR UPDATE", (material_id,)).fetchone()
        items = [
            decode(row)
            for row in conn.execute(
                "SELECT * FROM changeset_items WHERE changeset_id=? ORDER BY seq FOR UPDATE", (ident,)
            )
        ]
        entity_ids = []
        for item in items:
            if item["decision"] not in {"accepted", "edited"}:
                continue
            if not item["applied_entity_id"]:
                if item["validation"]["level"] == "error" or not item["evidence"]:
                    raise ApiError("EVIDENCE_NOT_FOUND")
                for ref in item["evidence"]:
                    provenance.normalize_source_ref(conn, material_id, ref)
                material = materials.material(conn, material_id)
                candidate = materials.find_candidate(material, item["after_data"]["id"])
                if candidate.get("entity_id"):
                    raise ApiError("ITEM_CHANGED", "候选已在其他页面采纳，请重新整理材料后核对。")
                candidate.update(item["after_data"])
                materials.save_payload(conn, material, material["payload"])
                entity = materials.adopt_candidate_in_transaction(conn, material_id, candidate["id"])["entity"]
                mark_draft(
                    conn,
                    entity,
                    item["validation"].get("last_content_editor_id") or item["decided_by"] or person.user_id,
                    ident,
                )
                item["applied_entity_id"] = entity["id"]
                conn.execute(
                    "UPDATE changeset_items SET applied_entity_id=?,applied_version=? WHERE id=?",
                    (entity["id"], entity["version"], item["id"]),
                )
            entity_id = item["applied_entity_id"]
            entity = db.entity_json(
                conn.execute("SELECT * FROM entities WHERE id=? FOR UPDATE", (entity_id,)).fetchone()
            )
            if not entity:
                raise ApiError("NOT_FOUND")
            if submit and entity["status"] in {"draft", "returned"}:
                entity_action_in_transaction(conn, entity_id, {"action": "submit", "reason": "建设方案核对后提交审核"})
            entity_ids.append(entity_id)
        if not entity_ids:
            raise ApiError("PRECONDITION_FAILED", "请先采纳需要保留的条目。")
        pending = any(item["decision"] == "pending" for item in items)
        conn.execute(
            "UPDATE changesets SET status=?,updated_at=? WHERE id=?",
            ("partially_applied" if pending else "applied", db.now(), ident),
        )
    return {"id": ident, "entity_ids": entity_ids, "submitted": submit}
