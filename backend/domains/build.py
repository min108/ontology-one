"""Recipe A composes legacy ingestion, extraction, anchors and adoption."""

import json
import re
import uuid
from contextlib import closing
from datetime import date

from fastapi import HTTPException

from backend import ai, db, jobs, materials, provenance
from backend.core.errors import ApiError
from backend.kernel import gates
from backend.kernel.metadata import get_meta, sync_meta
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


def processed_elsewhere(candidate: dict | None) -> bool:
    """Legacy missing status means pending; every terminal state stays terminal."""
    return candidate is None or bool(candidate.get("entity_id")) or candidate.get("status", "pending") != "pending"


def item_processed_elsewhere(item: dict, candidates: dict[str, dict]) -> bool:
    return not item["applied_entity_id"] and processed_elsewhere(candidates.get(item["after_data"]["id"]))


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
        # Recipe A is explicitly the institution's regulation intake. Classifying
        # the material here does not change its original text or immutable anchors.
        sync_meta(conn, item, {"subtype": "regulation"})
        ensure_org_root(conn)
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
                state.update(
                    extracted=True,
                    local=bool(fallback),
                    used_model=any(
                        candidate.get("extraction_mode") == "online" and not processed_elsewhere(candidate)
                        for candidate in candidates
                    ),
                )
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
            source = provenance.get_source_version(conn, material_id)
            facts = anchored_document_facts(conn, material_id, source)
            extraction = dict(item["payload"].get("extraction_summary") or {})
            extraction["fully_local"] = not bool(state.get("used_model"))
            conn.execute(
                "UPDATE changesets SET stats=? WHERE id=?",
                (materials.dump({"document_facts": facts, "extraction_summary": extraction}), cs_id),
            )
            for index, candidate in enumerate(item["payload"].get("candidates", []), 1):
                if processed_elsewhere(candidate):
                    continue
                candidate = enrich_candidate(conn, candidate, source["text"], facts)
                evidence, issues = [], []
                for quote in candidate.get("source_quotes", []):
                    try:
                        evidence.append(provenance.normalize_source_ref(conn, material_id, quote, min_quote_length=1))
                    except HTTPException:
                        issues.append("引文未逐字出现在原文中，请重新核对出处。")
                if not evidence:
                    issues.append("尚未找到可定位的原文，不能采纳。")
                for origin in candidate.get("field_provenance", {}).values():
                    if origin.get("origin") == "source":
                        ref = next(
                            (ref for ref in evidence if origin.get("quote") and origin["quote"] in ref["quote"]), None
                        )
                        if ref:
                            origin.update({key: ref[key] for key in ("anchor_id", "source_version", "material_id")})
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
        material = materials.material(conn, result["source_refs"][0]["material_id"])
        candidates = {candidate["id"]: candidate for candidate in material["payload"].get("candidates", [])}
        for item in result["items"]:
            item["processed_elsewhere"] = item_processed_elsewhere(item, candidates)
            if item["processed_elsewhere"]:
                item["decision"] = "processed_elsewhere"
            if item["applied_entity_id"]:
                current = db.entity_json(
                    conn.execute("SELECT * FROM entities WHERE id=?", (item["applied_entity_id"],)).fetchone()
                )
                if current:
                    if (
                        current["status"] in {"draft", "returned"}
                        and current["version"] != item["applied_version"]
                        and not unchanged_return(conn, item, current)
                        and item["decision"] in {"accepted", "edited"}
                    ):
                        item["stale_draft"] = True
                    item["after_data"] = current_candidate(conn, item, current)
                    item["applied_version"] = current["version"]
                    item["applied_status"] = current["status"]
            item["gates"] = preview_item(conn, item)
            eligible = (
                item["decision"] in {"accepted", "edited"}
                and not item["processed_elsewhere"]
                and item.get("applied_status", "draft") in {"draft", "returned"}
            )
            item["submit_blockers"] = submission_blockers(
                item["gates"], item["after_data"].get("waiver_applications") if not item["applied_entity_id"] else None
            )
            if item["validation"].get("level") == "error":
                item["submit_blockers"] += item["validation"].get("issues", [])
            if item.get("stale_draft"):
                item["submit_blockers"].append("草稿已变化，请查看最新内容并保存“改一改”后再提交。")
            item["can_submit"] = bool(eligible and not item["submit_blockers"])
            item["ready_for_bulk_accept"] = bool(
                not item["processed_elsewhere"]
                and not item["applied_entity_id"]
                and item["decision"] == "pending"
                and item["gates"]["passed"]
                and item["validation"].get("level") != "error"
                and item["tier"] == "sure"
            )
            if not item["gates"]["passed"] and item["tier"] == "sure":
                item["tier"] = "check"
            item["handoff"] = (
                {
                    "href": "/skill-development/" + item["applied_entity_id"]
                    if item["applied_entity_id"]
                    else "/skill-development",
                    "label": "前往技能工作台完善",
                }
                if item["after_data"]["kind"] == "skill"
                else None
            )
        result["can_submit"] = any(item["can_submit"] for item in result["items"])
        result["submit_blockers"] = [
            item["after_data"]["title"] + "：" + "；".join(reason.rstrip("。；") for reason in item["submit_blockers"])
            for item in result["items"]
            if item["decision"] in {"accepted", "edited"}
            and item.get("applied_status", "draft") in {"draft", "returned"}
            and not item["processed_elsewhere"]
            and item["submit_blockers"]
        ]
        ref = result["source_refs"][0]
        source = provenance.get_source_version(conn, ref["material_id"], ref.get("source_version"))
        result["source_text"] = source["text"]
        result["source_material_id"] = ref["material_id"]
        result["document_facts"] = result.get("stats", {}).get("document_facts") or document_facts(source["text"])
        result["extraction_summary"] = result.get("stats", {}).get("extraction_summary") or {
            "fully_local": not any(item["content_origin"] == "source" for item in result["items"])
        }
        result["applicability_options"] = [
            dict(row)
            for row in conn.execute(
                "SELECT id,title,kind FROM entities WHERE kind IN "
                "('organization','position','party','person','object_type') "
                "AND status<>'archived' ORDER BY title,id LIMIT 500"
            )
        ]
        return result


def lock_changeset(conn: db.Connection, ident: str) -> dict:
    row = conn.execute("SELECT * FROM changesets WHERE id=? FOR UPDATE", (ident,)).fetchone()
    if not row:
        raise ApiError("NOT_FOUND")
    return decode(row)


def current_candidate(conn: db.Connection, item: dict, entity: dict) -> dict:
    """Project current saved content; reading never accepts or rebases a stale decision."""
    payload = entity["payload"]
    result = dict(item["after_data"])
    for key in ("content", "definition", "scope", "exceptions", "unit", "grain", "boundary"):
        result[key] = gates.plain(payload.get(key))
    conditions = payload.get("conditions") or []
    result["conditions"] = conditions if isinstance(conditions, list) else [gates.plain(conditions)]
    result.update(title=entity["title"], kind=entity["kind"], judgement=payload.get("judgement"))
    meta = get_meta(conn, entity["id"]) or {}
    result.update({key: str(meta.get(key) or "") for key in ("valid_from", "valid_to")})
    target = conn.execute(
        "SELECT e.id,e.title FROM relations r JOIN entities e ON e.id=r.target "
        "WHERE r.source=? AND r.type='applies_to' AND r.status IN ('confirmed','source_verified') "
        "ORDER BY r.id LIMIT 1",
        (entity["id"],),
    ).fetchone()
    result.update(applies_to_id=target["id"] if target else "", applies_to_title=target["title"] if target else "")
    result["field_provenance"] = {
        key: value
        for key, value in (result.get("field_provenance") or {}).items()
        if result.get(key) == item["after_data"].get(key)
    }
    return result


def stale_draft() -> ApiError:
    return ApiError("VERSION_CONFLICT", "草稿已变化，请刷新核对页，查看最新内容并保存“改一改”后再提交。")


def unchanged_return(conn: db.Connection, item: dict, entity: dict) -> bool:
    if entity["status"] != "returned" or not entity["payload"].get("r2_review", {}).get("submitted_by"):
        return False
    current = current_candidate(conn, item, entity)
    previous = item["after_data"]
    text_keys = (
        "title",
        "kind",
        "content",
        "definition",
        "scope",
        "exceptions",
        "unit",
        "grain",
        "boundary",
        "valid_from",
        "valid_to",
        "applies_to_id",
    )
    return (
        all(gates.plain(previous.get(key)) == gates.plain(current.get(key)) for key in text_keys)
        and (previous.get("conditions") or []) == current["conditions"]
        and previous.get("judgement") == current["judgement"]
    )


def decide(ident: str, ids: list[str], decision: str, edit: dict | None, *, only_ready: bool = False) -> dict:
    if (decision == "edited") != (edit is not None) or (edit and len(ids) != 1):
        raise ApiError("VALIDATION_FAILED")
    with closing(db.connect()) as conn, conn:
        proposal = lock_changeset(conn, ident)
        material_id = proposal["source_refs"][0]["material_id"]
        conn.execute("SELECT id FROM entities WHERE id=? FOR UPDATE", (material_id,)).fetchone()
        material = materials.material(conn, material_id)
        candidates = {candidate["id"]: candidate for candidate in material["payload"].get("candidates", [])}
        for item_id in dict.fromkeys(ids):
            row = conn.execute(
                "SELECT * FROM changeset_items WHERE id=? AND changeset_id=? FOR UPDATE", (item_id, ident)
            ).fetchone()
            if not row:
                raise ApiError("NOT_FOUND")
            item = decode(row)
            if item_processed_elsewhere(item, candidates):
                continue
            if only_ready and (
                decision != "accepted"
                or item["decision"] != "pending"
                or item["applied_entity_id"]
                or item["tier"] != "sure"
                or not preview_item(conn, item)["passed"]
            ):
                continue
            if item["applied_entity_id"] and not edit and decision != "rejected":
                raise ApiError("ITEM_CHANGED", "这条内容已形成草稿，请在核对页修改或放弃草稿。")
            if decision in {"accepted", "edited"} and item["validation"]["level"] == "error":
                raise ApiError("EVIDENCE_NOT_FOUND")
            changes = {key: value for key, value in (edit or {}).items() if key != "expected_version"}
            old = None
            if item["applied_entity_id"]:
                old = db.entity_json(
                    conn.execute(
                        "SELECT * FROM entities WHERE id=? FOR UPDATE", (item["applied_entity_id"],)
                    ).fetchone()
                )
                expected = (edit or {}).get("expected_version")
                expected = item["applied_version"] if expected is None else expected
                if not old or old["status"] not in {"draft", "returned"} or old["version"] != expected:
                    raise stale_draft()
            after = (current_candidate(conn, item, old) if old else item["after_data"]) | changes
            if after["kind"] == "rule" and item["after_data"]["kind"] != "rule":
                source = provenance.get_source_version(
                    conn, material_id, proposal["source_refs"][0].get("source_version")
                )
                ensure_org_root(conn)
                after = enrich_candidate(conn, after, source["text"], proposal.get("stats", {}).get("document_facts"))
                after.update(changes)
                if "applies_to_id" not in changes:
                    suggest_applicability(conn, after)
            if not str(after.get("title", "")).strip() or not str(after.get("content", "")).strip():
                raise ApiError("VALIDATION_FAILED", "名称和正文不能为空。")
            validation = item["validation"]
            if edit and after != item["after_data"]:
                validation["last_content_editor_id"] = require_context().user_id
            if decision in {"accepted", "edited"}:
                validation["editor_ids"] = sorted(set(validation.get("editor_ids") or []) | {require_context().user_id})
            if edit:
                if "waiver_applications" not in changes and any(
                    after.get(key) != item["after_data"].get(key) for key in changes
                ):
                    after["waiver_applications"] = {}
                validate_candidate(conn, item, after)
                provenance_map = dict(item["after_data"].get("field_provenance") or {})
                for key in changes:
                    if key not in {"waiver_applications", "kind"} and after.get(key) != item["after_data"].get(key):
                        provenance_map[key] = manual_provenance()
                after["field_provenance"] = provenance_map
            if decision in {"accepted", "edited"} and after.get("applies_to_id"):
                after["field_provenance"] = {
                    **(after.get("field_provenance") or {}),
                    "applies_to_id": manual_provenance(),
                }
            if old:
                if decision == "rejected":
                    from backend.kernel.elements import transition

                    updated = transition(conn, old, "archived", "核对页放弃未提交草稿")
                else:
                    updated = save_adopted_candidate(conn, item, old, after)
                conn.execute("UPDATE changeset_items SET applied_version=? WHERE id=?", (updated["version"], item_id))
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
    from backend.kernel.elements import sync_after_write

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
        material = materials.material(conn, material_id)
        candidates = {candidate["id"]: candidate for candidate in material["payload"].get("candidates", [])}
        entity_ids, submitted_ids, remaining = [], [], []
        for item in items:
            if item_processed_elsewhere(item, candidates):
                continue
            if item["decision"] not in {"accepted", "edited"}:
                continue
            if not item["applied_entity_id"]:
                if item["validation"]["level"] == "error" or not item["evidence"]:
                    remaining.append(
                        {
                            "item_id": item["id"],
                            "title": item["after_data"]["title"],
                            "reasons": item["validation"].get("issues") or ["尚未找到可定位的原文，不能采纳。"],
                        }
                    )
                    continue
                validate_candidate(conn, item, item["after_data"])
                for ref in item["evidence"]:
                    provenance.normalize_source_ref(conn, material_id, ref)
                material = materials.material(conn, material_id)
                candidate = materials.find_candidate(material, item["after_data"]["id"])
                candidate.update(item["after_data"])
                materials.save_payload(conn, material, material["payload"])
                entity = materials.adopt_candidate_in_transaction(conn, material_id, candidate["id"])["entity"]
                mark_draft(
                    conn,
                    entity,
                    item["validation"].get("last_content_editor_id") or item["decided_by"] or person.user_id,
                    ident,
                    editor_ids=item["validation"].get("editor_ids") or [],
                )
                entity = db.entity_json(conn.execute("SELECT * FROM entities WHERE id=?", (entity["id"],)).fetchone())
                confirm_applicability(conn, entity, item["after_data"])
                entity = sync_after_write(conn, entity, meta_updates=candidate_meta(item["after_data"]))
                record_candidate_sources(conn, entity, item["after_data"])
                item["applied_entity_id"] = entity["id"]
                item["applied_version"] = entity["version"]
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
            if entity["status"] == "archived":
                continue
            if submit and entity["status"] in {"draft", "returned"}:
                if entity["version"] != item["applied_version"] and not unchanged_return(conn, item, entity):
                    raise stale_draft()
                blockers = submission_blockers(gates.check_entity(conn, entity))
                if blockers:
                    remaining.append({"item_id": item["id"], "title": entity["title"], "reasons": blockers})
                    entity_ids.append(entity_id)
                    continue
                conn.execute("UPDATE changeset_items SET applied_version=? WHERE id=?", (entity["version"], item["id"]))
                entity_action_in_transaction(conn, entity_id, {"action": "submit", "reason": "建设方案核对后提交审核"})
                submitted_ids.append(entity_id)
            elif submit and entity["status"] in {"review", "approved", "published"}:
                submitted_ids.append(entity_id)
            entity_ids.append(entity_id)
        if not entity_ids and not remaining:
            raise ApiError("PRECONDITION_FAILED", "请先采纳需要保留的条目。")
        pending = any(
            item["decision"] == "pending" and not item_processed_elsewhere(item, candidates) for item in items
        )
        conn.execute(
            "UPDATE changesets SET status=?,updated_at=? WHERE id=?",
            ("partially_applied" if pending or remaining else "applied", db.now(), ident),
        )
    return {
        "id": ident,
        "entity_ids": entity_ids,
        "submitted": bool(submit and submitted_ids),
        "submitted_entity_ids": submitted_ids,
        "submitted_count": len(submitted_ids),
        "remaining_items": remaining,
        "remaining_count": len(remaining),
    }


def ensure_org_root(conn: db.Connection) -> dict:
    """Synchronize only this authenticated institution's directory-owned root."""
    from backend.security import store

    ident = require_context().tenant_id
    row = conn.execute("SELECT * FROM entities WHERE id=? AND kind='organization'", (ident,)).fetchone()
    if row:
        return db.entity_json(row)
    directory = next(
        (item for item in store.list_tenants() if item["id"] == ident and item["status"] == "active"), None
    )
    if not directory:
        raise ApiError("TENANT_DISABLED")
    stamp = db.now()
    conn.execute(
        "INSERT INTO entities(id,kind,title,summary,domain,org_id,status,source_kind,payload,"
        "created_at,updated_at,version) "
        "VALUES(?,'organization',?,'机构目录同步','机构管理',?,'published','directory','{}',?,?,1)",
        (ident, directory["name"], ident, stamp, stamp),
    )
    entity = db.entity_json(conn.execute("SELECT * FROM entities WHERE id=?", (ident,)).fetchone())
    sync_meta(conn, entity)
    db.log(conn, ident, "同步机构目录", "为制度适用范围补齐本机构单位要素")
    return entity


def document_facts(source_text: str) -> dict:
    """Only an operative sentence about this file establishes its effective date."""
    facts = {key: {"value": "", "origin": "document", "quote": ""} for key in ("valid_from", "scope")}
    prefix = r"(?:第[一二三四五六七八九十百零\d]+条\s*)?本(?:办法|制度|规定|细则|规程|规则)"
    for sentence in re.finditer(r"[^。\n]+(?:。|$)", source_text):
        quote = sentence.group().strip()
        start = sentence.start() + len(sentence.group()) - len(sentence.group().lstrip())
        match = re.fullmatch(
            prefix + r"(?:自|于)\s*(\d{4})[年-](\d{1,2})[月-](\d{1,2})日?\s*起?(?:施行|实施|执行|生效)[。；;]?", quote
        )
        if match and not facts["valid_from"]["value"]:
            try:
                value = date(*map(int, match.groups())).isoformat()
            except ValueError:
                continue
            facts["valid_from"] = {
                "value": value,
                "origin": "document",
                "quote": quote,
                "start": start,
                "end": start + len(quote),
            }
        scope = re.fullmatch(prefix + r"适用于(.+?)[。]?", quote)
        if scope and not facts["scope"]["value"]:
            facts["scope"] = {
                "value": scope.group(1),
                "origin": "document",
                "quote": quote,
                "start": start,
                "end": start + len(quote),
            }
    return facts


def anchored_document_facts(conn: db.Connection, material_id: str, source: dict) -> dict:
    facts = document_facts(source["text"])
    for fact in facts.values():
        if fact["value"]:
            ref = provenance.normalize_source_ref(conn, material_id, {**fact, "source_version": source["id"]})
            fact.update({key: ref[key] for key in ("anchor_id", "source_version", "material_id")})
    return facts


def enrich_candidate(conn: db.Connection, candidate: dict, source_text: str, facts: dict | None = None) -> dict:
    """Item facts must be literal own-quote facts; only file scope/date are shared."""
    result = dict(candidate)
    facts = facts if facts is not None else document_facts(source_text)
    quotes = [
        item["quote"]
        for item in candidate.get("source_quotes", [])
        if isinstance(item, dict) and isinstance(item.get("quote"), str) and item["quote"] in source_text
    ]
    local_quotes = []
    ambiguous = False
    for quote in quotes:
        pieces = re.split(r"(?=第[一二三四五六七八九十百零\d]+条)", quote)
        articles = [piece for piece in pieces if re.match(r"第[一二三四五六七八九十百零\d]+条", piece)]
        if len(articles) < 2:
            local_quotes.append(quote)
            continue
        content = str(candidate.get("content") or "")
        grounded = [piece.strip() for piece in articles if piece.strip() in content]
        if not grounded and content.strip() and content.strip() in quote:
            grounded = [content.strip()]
        if not grounded:
            ambiguous = True
            # A model's field may still be a supported literal in its own
            # content, but a neighbouring article is never an automatic default.
            values = [candidate.get(key) for key in ("scope", "exceptions")]
            values += candidate.get("conditions") if isinstance(candidate.get("conditions"), list) else []
            grounded = [
                value for value in values if isinstance(value, str) and value and value in quote and value in content
            ]
        local_quotes.extend(grounded)
    quotes = local_quotes
    own = "\n".join(quotes)
    result["bulk_confirmation_blocked"] = ambiguous
    origins = {}
    if not result.get("definition") or candidate.get("extraction_mode") == "local":
        result["definition"] = candidate.get("content", "").split("。", 1)[0]
    for field in ("scope", "exceptions"):
        value = result.get(field)
        value = value if isinstance(value, str) and value.strip() and any(value in quote for quote in quotes) else ""
        if not value:
            pattern = (
                r"(?:适用范围[：:]|本(?:制度|办法|规定|细则)适用于)([^。\n；]+)"
                if field == "scope"
                else r"(?:例外(?:情形)?[：:])([^。\n；]+)"
            )
            match = re.search(pattern, own)
            value = match.group(1).strip() if match else ""
        if field == "exceptions" and not value:
            match = re.search(r"[^。\n；]*(?:除外|确需|但)[^。\n；]*", own)
            value = match.group().strip() if match else ""
        result[field] = value
        if value:
            origins[field] = {"origin": "source", "quote": next(quote for quote in quotes if value in quote)}
    conditions = result.get("conditions")
    conditions = (
        [
            value
            for value in conditions
            if isinstance(value, str) and value.strip() and any(value in quote for quote in quotes)
        ]
        if isinstance(conditions, list)
        else []
    )
    if not conditions:
        match = re.search(r"适用条件[：:]([^。\n；]+)", own)
        if match:
            conditions = [match.group(1).strip()]
    if not conditions:
        # Ordinary trigger clauses are copied literally from this item's quote.
        # They must not be presented as "the source has no condition" merely
        # because the model returned a paraphrase or omitted the field.
        trigger = re.search(
            r"(?:在|当|若|如果)[^，。；\n]{1,80}?(?:后|时|前)|[^，。；\n]{1,80}的(?=[，,])|(?:期满|完成|通过|收到)[^，。；\n]{0,60}?(?:后|时)",
            own,
        )
        if trigger:
            conditions = [trigger.group().strip()]
    result["conditions"] = conditions
    if conditions:
        origins["conditions"] = {"origin": "source", "quote": next(quote for quote in quotes if conditions[0] in quote)}
    if not result["scope"] and facts["scope"]["value"]:
        result["scope"] = facts["scope"]["value"]
        origins["scope"] = dict(facts["scope"])
    # Never trust candidate/model dates or unrelated dated passages.
    result["valid_from"] = facts["valid_from"]["value"]
    result["valid_to"] = ""
    if result["valid_from"]:
        origins["valid_from"] = dict(facts["valid_from"])
    if result.get("definition") and quotes:
        origins["definition"] = {"origin": "source", "quote": quotes[0]}
    result["field_provenance"] = origins
    if candidate["kind"] == "rule":
        suggest_applicability(conn, result)
    return result


def suggest_applicability(conn: db.Connection, result: dict) -> None:
    root = conn.execute(
        "SELECT id,title FROM entities WHERE id=? AND kind='organization'", (require_context().tenant_id,)
    ).fetchone()
    # Prefer a specifically named local department/object when it exists.
    matches = [
        dict(row)
        for row in conn.execute(
            "SELECT id,title FROM entities WHERE kind IN ('organization','position','party','object_type') "
            "ORDER BY CHAR_LENGTH(title) DESC,id"
        )
        if row["title"] and row["title"] in (result.get("content", "") + " " + result.get("scope", ""))
    ]
    target = matches[0] if matches else root
    if target:
        result.update(applies_to_id=target["id"], applies_to_title=target["title"])


def candidate_meta(candidate: dict) -> dict:
    return {key: candidate.get(key) or None for key in ("valid_from", "valid_to")}


def candidate_payload(candidate: dict, evidence: list[dict]) -> dict:
    keys = ("content", "definition", "scope", "conditions", "exceptions", "unit", "grain", "boundary", "judgement")
    return {**{key: candidate[key] for key in keys if key in candidate}, "source_refs": evidence}


def suggested_relation(conn: db.Connection, entity: dict, candidate: dict) -> dict | None:
    if entity["kind"] != "rule":
        return None
    target = conn.execute("SELECT id,title,kind FROM entities WHERE id=?", (candidate.get("applies_to_id"),)).fetchone()
    if not target or target["kind"] not in {"organization", "position", "party", "person", "object_type"}:
        return None
    return {
        "source": entity["id"],
        "target": target["id"],
        "type": "applies_to",
        "label": "适用于",
        "status": "confirmed",
        "target_kind": target["kind"],
        "payload": {"recipe_applicability": True},
    }


def confirm_applicability(conn: db.Connection, entity: dict, candidate: dict) -> None:
    relation = suggested_relation(conn, entity, candidate)
    if entity["kind"] != "rule":
        return
    if not relation:
        raise ApiError("VALIDATION_FAILED", "请选择本机构中已有的适用主体或业务对象。")
    existing = conn.execute(
        "SELECT id,target,status,payload FROM relations WHERE source=? AND type='applies_to'", (entity["id"],)
    ).fetchall()
    for row in existing:
        if json.loads(row["payload"] or "{}").get("recipe_applicability"):
            if row["target"] == relation["target"] and row["status"] == "confirmed":
                return
            # Keep the previous human confirmation trace when applicability changes.
            conn.execute("UPDATE relations SET status='rejected' WHERE id=?", (row["id"],))
    payload = {"recipe_applicability": True, "confirmed_by": require_context().user_id, "confirmed_at": db.now()}
    conn.execute(
        "INSERT INTO relations(id,source,target,type,label,status,payload) "
        "VALUES(?,?,?,'applies_to','适用于','confirmed',?)",
        ("rel-" + uuid.uuid4().hex, entity["id"], relation["target"], materials.dump(payload)),
    )
    db.log(conn, entity["id"], "确认适用关系", "核对条目时一并确认适用主体或对象")


def preview_item(conn: db.Connection, item: dict) -> dict:
    if item["applied_entity_id"]:
        row = conn.execute("SELECT * FROM entities WHERE id=?", (item["applied_entity_id"],)).fetchone()
        if row:
            return gates.check_entity(conn, db.entity_json(row))
    candidate = item["after_data"]
    entity = {
        "id": item["id"],
        "title": candidate["title"],
        "kind": candidate["kind"],
        "version": 1,
        "status": "draft",
        "payload": candidate_payload(candidate, item["evidence"]),
    }
    relation = suggested_relation(conn, entity, candidate)
    return gates.check_entity(
        conn,
        entity,
        meta_updates=candidate_meta(candidate),
        proposed_relations=[relation] if relation else [],
        preview=True,
    )


def manual_provenance(origin: str = "manual") -> dict:
    person = require_context()
    return {
        "origin": origin,
        "actor_id": person.user_id,
        "confirmed_by": person.username,
        "confirmed_at": db.now(),
        "quote": "",
    }


def submission_blockers(result: dict, applications: dict | None = None) -> list[str]:
    return [
        check.get("denial_reason") or check["reason"]
        for check in result["checks"]
        if check["status"] == "fail" and not gates.submission_eligibility({"checks": [check]}, applications)
    ]


def validate_candidate(conn: db.Connection, item: dict, candidate: dict) -> None:
    from backend.kernel.rules import prepare_payload, validate_judgement_shape

    for key in ("valid_from", "valid_to"):
        value = candidate.get(key)
        if value:
            try:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    raise ValueError()
                date.fromisoformat(value)
            except (TypeError, ValueError) as exc:
                raise ApiError("VALIDATION_FAILED", candidate["title"] + "：请填写正确的生效或失效日期。") from exc
    payload = candidate_payload(candidate, item["evidence"])
    try:
        validate_judgement_shape(payload)
        if candidate["kind"] == "rule" and payload.get("judgement") is not None:
            entity = {"id": item["applied_entity_id"] or item["id"], "kind": "rule", "payload": payload}
            prepared = prepare_payload("rule", payload, **gates.expression_context(conn, entity))
            candidate["judgement"] = prepared["judgement"]
    except ValueError as exc:
        raise ApiError("VALIDATION_FAILED", str(exc)) from exc


def record_candidate_sources(conn: db.Connection, entity: dict, candidate: dict) -> None:
    origins = {"source": "source", "document": "source", "manual": "editor", "bulk_confirmation": "batch_confirmation"}
    sources = {}
    allowed = {
        "rule": {"definition", "scope", "conditions", "exceptions", "valid_from", "valid_to", "subjects"},
        "data": {"definition", "unit", "grain"},
        "knowledge": {"definition", "usage_conditions", "boundary"},
        "skill": {"definition"},
    }.get(entity["kind"], set())
    for key, value in (candidate.get("field_provenance") or {}).items():
        key = (
            "subjects"
            if key == "applies_to_id"
            else "usage_conditions"
            if key == "conditions" and entity["kind"] == "knowledge"
            else key
        )
        if key in allowed and value.get("origin") in origins:
            sources[key] = {
                "source": origins[value["origin"]],
                "source_quote": value.get("quote", ""),
                "confirmed_by": value.get("confirmed_by", ""),
            }
    if sources:
        gates.record_field_sources(conn, entity, sources)
    applications = candidate.get("waiver_applications")
    if applications is not None:
        gates.apply_for_waiver(conn, entity, {key: value for key, value in applications.items() if value is not None})


def save_adopted_candidate(conn: db.Connection, item: dict, old: dict, after: dict) -> dict:
    from backend.kernel.elements import update_draft

    payload = {**old["payload"], **candidate_payload(after, old["payload"].get("source_refs") or [])}
    # Skill-only schema fields cannot silently survive reclassification to rule.
    if after["kind"] != old["kind"]:
        payload = {
            **candidate_payload(after, old["payload"].get("source_refs") or []),
            "r2_review": old["payload"].get("r2_review", {}),
        }
    updated = update_draft(
        conn,
        old,
        {
            "kind": after["kind"],
            "title": after["title"],
            "payload": payload,
            "meta": candidate_meta(after),
            "expected_version": old["version"],
            "reason": "核对页补齐发布条件",
        },
    )
    confirm_applicability(conn, updated, after)
    record_candidate_sources(conn, updated, after)
    return updated


def confirm_facts(ident: str, body: dict) -> dict:
    """Explicitly confirm a document date or missing per-item facts; never infer consent."""
    with closing(db.connect()) as conn, conn:
        proposal = lock_changeset(conn, ident)
        source_ref = proposal["source_refs"][0]
        source = provenance.get_source_version(conn, source_ref["material_id"], source_ref.get("source_version"))
        stats = proposal.get("stats") or {}
        facts = stats.get("document_facts") or anchored_document_facts(conn, source_ref["material_id"], source)
        items = [
            decode(row)
            for row in conn.execute(
                "SELECT * FROM changeset_items WHERE changeset_id=? ORDER BY seq FOR UPDATE", (ident,)
            )
        ]
        exceptions = set(body.get("confirm_no_exceptions_item_ids") or [])
        conditions = set(body.get("confirm_unconditional_item_ids") or [])
        if not (exceptions | conditions).issubset({item["id"] for item in items}):
            raise ApiError("NOT_FOUND")
        date_changed = "valid_from" in body and body["valid_from"] is not None
        if date_changed:
            value = body["valid_from"]
            if value:
                try:
                    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                        raise ValueError()
                    date.fromisoformat(value)
                except ValueError as exc:
                    raise ApiError("VALIDATION_FAILED", "本制度生效日期须为正确的年月日。") from exc
            facts["valid_from"] = {"value": value, **manual_provenance()}
        material = materials.material(conn, source_ref["material_id"])
        candidates = {candidate["id"]: candidate for candidate in material["payload"].get("candidates", [])}
        for item in items:
            if (
                item["after_data"]["kind"] != "rule"
                or item["decision"] == "rejected"
                or item_processed_elsewhere(item, candidates)
            ):
                continue
            if not date_changed and item["id"] not in exceptions | conditions:
                continue
            old = None
            if item["applied_entity_id"]:
                row = conn.execute(
                    "SELECT * FROM entities WHERE id=? FOR UPDATE", (item["applied_entity_id"],)
                ).fetchone()
                old = db.entity_json(row) if row else None
                if not old or old["status"] not in {"draft", "returned"}:
                    continue
                expected = body.get("expected_versions", {}).get(item["id"], item["applied_version"])
                if old["version"] != expected:
                    raise stale_draft()
            after = current_candidate(conn, item, old) if old else dict(item["after_data"])
            origins = dict(after.get("field_provenance") or {})
            if date_changed:
                after["valid_from"] = facts["valid_from"]["value"]
                origins["valid_from"] = dict(facts["valid_from"])
            source_candidate = enrich_candidate(conn, after, source["text"], facts)
            for key, selected, value in (
                ("exceptions", exceptions, "无例外"),
                ("conditions", conditions, ["无附加适用条件"]),
            ):
                if item["id"] in selected:
                    if source_candidate.get("bulk_confirmation_blocked"):
                        raise ApiError(
                            "VALIDATION_FAILED", "引文包含多条要求，请先逐条核对条件和例外，不可批量确认为无。"
                        )
                    if source_candidate.get(key):
                        raise ApiError("VALIDATION_FAILED", "原文中已写明条件或例外，请逐条核对，不可批量确认为无。")
                    if after.get(key):
                        raise ApiError("VERSION_CONFLICT", "这条规则已填写条件或例外，请刷新后逐条核对，不可批量覆盖。")
                    after[key] = value
                    origins[key] = manual_provenance("bulk_confirmation")
            after["field_provenance"] = origins
            after["waiver_applications"] = {}
            validate_candidate(conn, item, after)
            if old:
                updated = save_adopted_candidate(conn, item, old, after)
                conn.execute(
                    "UPDATE changeset_items SET applied_version=? WHERE id=?", (updated["version"], item["id"])
                )
            validation = {
                **item["validation"],
                "last_content_editor_id": require_context().user_id,
                "editor_ids": sorted(set(item["validation"].get("editor_ids") or []) | {require_context().user_id}),
            }
            conn.execute(
                "UPDATE changeset_items SET after_data=?,validation=?,decided_by=?,decided_at=? WHERE id=?",
                (materials.dump(after), materials.dump(validation), require_context().user_id, db.now(), item["id"]),
            )
        stats["document_facts"] = facts
        conn.execute("UPDATE changesets SET stats=?,updated_at=? WHERE id=?", (materials.dump(stats), db.now(), ident))
    return get(ident)
