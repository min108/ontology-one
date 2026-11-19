"""Recipe A composes legacy ingestion, extraction, anchors and adoption."""

import hashlib
import json
import re
import uuid
from contextlib import closing
from datetime import date
from urllib.parse import quote as url_quote
from urllib.parse import urlencode

from fastapi import HTTPException

from backend import ai, db, jobs, materials, provenance
from backend.core.errors import ApiError, NextAction, from_http
from backend.domains.build_source_facts import FACTS_VERSION, document_facts, ground_candidate
from backend.domains.build_source_facts import source_exception as source_exception
from backend.domains.build_sources import preserved_paragraphs, proposal_sources, unformed_chunks
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
    after = value.get("after_data")
    if isinstance(after, dict) and after.get("waiver_applications") is None:
        after["waiver_applications"] = {}
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
                    if not online or coverage.get(chunk["id"], {}).get("status") not in {"completed", "needs_review"}
                ]
                ids = {candidate["id"] for candidate in candidates}
                preserved = preserved_paragraphs(candidates)
                for chunk in fallback:
                    # A located rejected candidate needs an explicit reviewer
                    # choice, even if every candidate in its batch was rejected.
                    if coverage.get(chunk["id"], {}).get("discarded_source"):
                        continue
                    if not chunk["content"].strip() or (chunk["id"], chunk["content"]) in preserved:
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
            conn.execute("SELECT id FROM entities WHERE id=? FOR UPDATE", (material_id,)).fetchone()
            item = materials.material(conn, material_id)
            cs_id, stamp = "CS-" + uuid.uuid4().hex[:24], db.now()
            source = provenance.get_source_version(conn, material_id)
            refs = [{"material_id": material_id, "source_version": source["id"]}]
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
            facts = anchored_document_facts(conn, material_id, source)
            extraction = dict(item["payload"].get("extraction_summary") or {})
            extraction["fully_local"] = bool(state.get("local")) and not bool(state.get("used_model"))
            conn.execute(
                "UPDATE changesets SET stats=? WHERE id=?",
                (
                    materials.dump(
                        {
                            "document_facts": facts,
                            "extraction_summary": extraction,
                            "unformed_chunks": unformed_chunks(item, provenance.get_active_chunks(conn, material_id)),
                        }
                    ),
                    cs_id,
                ),
            )
            for index, candidate in enumerate(item["payload"].get("candidates", []), 1):
                if processed_elsewhere(candidate):
                    continue
                insert_candidate_item(conn, cs_id, index, material_id, candidate, source, facts)
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


def insert_candidate_item(
    conn: db.Connection,
    cs_id: str,
    index: int,
    material_id: str,
    candidate: dict,
    source: dict,
    facts: dict,
    *,
    retained_by: str | None = None,
) -> None:
    candidate = enrich_candidate(conn, candidate, source["text"], facts)
    evidence, issues = [], []
    for quote in candidate.get("source_quotes", []):
        try:
            if not isinstance(quote, dict) or (quote.get("source_version") and quote["source_version"] != source["id"]):
                raise HTTPException(422, "引文与方案的原文版本不一致")
            evidence.append(
                provenance.normalize_source_ref(
                    conn, material_id, {**quote, "source_version": source["id"]}, min_quote_length=1
                )
            )
        except HTTPException:
            issues.append("引文未逐字出现在原文中，请重新核对出处。")
    if not evidence:
        issues.append("尚未找到可定位的原文，不能采纳。")
    anchor_candidate_fields(candidate, evidence)
    duplicate = conn.execute(
        "SELECT id FROM entities WHERE kind=? AND title=? LIMIT 1", (candidate["kind"], candidate["title"])
    ).fetchone()
    local = candidate.get("extraction_mode") != "online"
    complete = not candidate.get("questions") and bool(candidate.get("content"))
    score, tier = confidence(exact=bool(evidence) and not issues, complete=complete, unique=not duplicate, local=local)
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
            materials.dump(
                {
                    "level": "error" if issues else "warn" if local else "ok",
                    "issues": issues,
                    **({"editor_ids": [retained_by]} if retained_by else {}),
                }
            ),
            duplicate["id"] if duplicate else None,
        ),
    )


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
        ref = result["source_refs"][0]
        source = provenance.get_source_version(conn, ref["material_id"], ref.get("source_version"))
        facts = changeset_document_facts(conn, result, source)
        legacy_facts = needs_legacy_projection(result)
        for item in result["items"]:
            if legacy_facts:
                align_legacy_item(item, candidates, source["text"], facts)
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
            if item["after_data"]["kind"] == "rule":
                item["after_data"].update(source_confirmation_limits(item, candidates, source["text"], facts))
            item["gates"] = preview_item(conn, item)
            if item["applied_entity_id"]:
                item["after_data"]["waiver_applications"] = active_applications(item["gates"])
            judgement = item["after_data"].get("judgement")
            if judgement is not None and not isinstance(judgement, dict):
                # Evaluate the stored historical value above so QG3 still reports the fault.
                # The form can open with an empty editor and an actionable explanation.
                item["after_data"]["judgement"] = None
                item["validation"]["issues"] = [
                    *item["validation"].get("issues", []),
                    "原有判定式格式异常，请在“改一改”中核对并重新保存。",
                ]
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
            if eligible and item["applied_entity_id"]:
                if current:
                    try:
                        guard_adopted_write(conn, current)
                    except ApiError as exc:
                        item["submit_blockers"].append(exc.message)
                else:
                    item["submit_blockers"].append("找不到已采纳的草稿，请刷新核对页确认。")
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
            item["handoff"] = None
            if item["after_data"]["kind"] == "skill":
                skill_id = item["applied_entity_id"]
                href = "/skill-development"
                if skill_id:
                    document = conn.execute(
                        "SELECT e.payload FROM skill_documents d JOIN entities e ON e.id=d.entity_id "
                        "WHERE d.entity_id=?",
                        (skill_id,),
                    ).fetchone()
                    managed = bool(document and json.loads(document["payload"] or "{}").get("managed_skill"))
                    href += "/" + url_quote(skill_id, safe="") if managed else "?" + urlencode({"legacy": skill_id})
                item["handoff"] = {"href": href, "label": "前往技能工作台完善"}
        result["can_submit"] = any(item["can_submit"] for item in result["items"])
        result["submit_blockers"] = [
            item["after_data"]["title"] + "：" + "；".join(reason.rstrip("。；") for reason in item["submit_blockers"])
            for item in result["items"]
            if item["decision"] in {"accepted", "edited"}
            and item.get("applied_status", "draft") in {"draft", "returned"}
            and not item["processed_elsewhere"]
            and item["submit_blockers"]
        ]
        retained = {item["after_data"].get("retained_anchor_id") for item in result["items"]}
        retained.update(
            candidate.get("retained_anchor_id") for candidate in candidates.values() if processed_elsewhere(candidate)
        )
        result["unformed_sources"] = [
            gap for gap in proposal_sources(conn, result, source, result["items"]) if gap["anchor_id"] not in retained
        ]
        result["source_text"] = source["text"]
        result["source_material_id"] = ref["material_id"]
        result["document_facts"] = facts
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


def retain_source(ident: str, anchor_id: str, source_version: str) -> dict:
    person = require_context()
    with closing(db.connect()) as conn, conn:
        proposal = lock_changeset(conn, ident)
        ref = proposal["source_refs"][0]
        if ref.get("source_version") != source_version:
            raise ApiError("VERSION_CONFLICT", "所选原文版本与方案不一致，请刷新后重试。")
        material_id = ref["material_id"]
        material = materials.material(conn, material_id, for_update=True)
        source = provenance.get_source_version(conn, material_id, source_version)
        items = [
            decode(row)
            for row in conn.execute(
                "SELECT seq,after_data,evidence,content_origin FROM changeset_items WHERE changeset_id=? ORDER BY seq",
                (ident,),
            )
        ]
        existing = any(item["after_data"].get("retained_anchor_id") == anchor_id for item in items)
        if not existing:
            allowed = proposal_sources(conn, proposal, source, items)
            gap = next((entry for entry in allowed if entry["anchor_id"] == anchor_id), None)
            if not gap:
                raise ApiError("NOT_FOUND")
            evidence = provenance.normalize_source_ref(conn, material_id, gap)
            title = evidence["quote"].strip().splitlines()[0][:120]
            # This namespace cannot collide with a model candidate even when the
            # model chose the original paragraph as its title on the same chunk.
            candidate_id = "retained_" + anchor_id
            candidates = material["payload"].setdefault("candidates", [])
            previous = next((row for row in candidates if row["id"] == candidate_id), None)
            candidate = {
                "id": candidate_id,
                "kind": "rule",
                "title": title,
                "content": evidence["quote"],
                "summary": "按原文保留，请逐条核对。",
                "source_chunk_ids": [evidence["chunk_id"]],
                "source_quotes": [evidence],
                "status": "pending",
                "extraction_mode": "local",
                "conditions": [],
                "questions": ["请核对条目种类、适用范围与例外。"],
                "retained_anchor_id": anchor_id,
                "created_at": previous.get("created_at") if previous else db.now(),
            }
            if previous is None:
                candidates.append(candidate)
                materials.save_payload(conn, material, material["payload"], "核对建设方案时按原文保留条目")
            else:
                fields = ("title", "kind", "content", "retained_anchor_id")
                quotes = previous.get("source_quotes") or []
                if (
                    processed_elsewhere(previous)
                    or any(previous.get(key) != candidate[key] for key in fields)
                    or len(quotes) != 1
                    or not isinstance(quotes[0], dict)
                    or any(quotes[0].get(key) != evidence[key] for key in ("anchor_id", "source_version", "quote"))
                ):
                    raise ApiError("ITEM_CHANGED", "这一段已在素材页或其他方案中办理，请核对最新记录。")
            facts = changeset_document_facts(conn, proposal, source, anchor=True)
            seq = items[-1]["seq"] + 1 if items else 1
            insert_candidate_item(conn, ident, seq, material_id, candidate, source, facts, retained_by=person.user_id)
            conn.execute(
                "UPDATE changesets SET updated_at=?,status=IF(status='applied','partially_applied',status) WHERE id=?",
                (db.now(), ident),
            )
            materials.activity(conn, material_id, "candidate_review", "按原文保留为待核对条目：" + title)
    return get(ident)


def current_candidate(conn: db.Connection, item: dict, entity: dict, *, include_applications: bool = False) -> dict:
    """Project current saved content; reading never accepts or rebases a stale decision."""
    payload = entity["payload"]
    result = dict(item["after_data"])
    for key in ("content", "definition", "scope", "exceptions", "unit", "grain", "boundary"):
        result[key] = gates.plain(payload.get(key))
    conditions = payload.get("conditions") or []
    result["conditions"] = conditions if isinstance(conditions, list) else [gates.plain(conditions)]
    result.update(title=entity["title"], kind=entity["kind"], judgement=payload.get("judgement"))
    meta = get_meta(conn, entity["id"]) or {}
    if include_applications:
        # A sibling editor can withdraw a request outside the changeset. Never replay the proposal's old copy.
        result["waiver_applications"] = (
            active_applications(gates.check_entity(conn, entity))
            if (meta.get("gates") or {}).get("applications")
            else {}
        )
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


def guard_adopted_write(conn: db.Connection, entity: dict) -> None:
    from backend.api.v1.core import guard_generic_edit

    try:
        guard_generic_edit(conn, entity, {})
    except HTTPException as exc:
        if isinstance(exc.detail, dict) and exc.detail.get("code") == "managed_skill_route":
            raise ApiError(
                "PRECONDITION_FAILED",
                exc.detail["message"],
                next_actions=[
                    NextAction(label="前往技能工作台", href="/skill-development/" + url_quote(entity["id"], safe=""))
                ],
            ) from exc
        raise from_http(exc.status_code, exc.detail) from exc


def decide(
    ident: str,
    ids: list[str],
    decision: str,
    edit: dict | None,
    *,
    only_ready: bool = False,
    expected_versions: dict[str, int] | None = None,
) -> dict:
    if (decision == "edited") != (edit is not None) or (edit and len(ids) != 1):
        raise ApiError("VALIDATION_FAILED")
    with closing(db.connect()) as conn, conn:
        proposal = lock_changeset(conn, ident)
        material_id = proposal["source_refs"][0]["material_id"]
        conn.execute("SELECT id FROM entities WHERE id=? FOR UPDATE", (material_id,)).fetchone()
        material = materials.material(conn, material_id)
        candidates = {candidate["id"]: candidate for candidate in material["payload"].get("candidates", [])}
        legacy = legacy_document(conn, proposal)
        for item_id in dict.fromkeys(ids):
            row = conn.execute(
                "SELECT * FROM changeset_items WHERE id=? AND changeset_id=? FOR UPDATE", (item_id, ident)
            ).fetchone()
            if not row:
                raise ApiError("NOT_FOUND")
            item = decode(row)
            if legacy:
                align_legacy_item(item, candidates, legacy[0]["text"], legacy[1])
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
            if "waiver_applications" in changes and changes["waiver_applications"] is None:
                changes["waiver_applications"] = {}
            old = None
            if item["applied_entity_id"]:
                old = db.entity_json(
                    conn.execute(
                        "SELECT * FROM entities WHERE id=? FOR UPDATE", (item["applied_entity_id"],)
                    ).fetchone()
                )
                if not old:
                    raise stale_draft()
                guard_adopted_write(conn, old)
                expected = (edit or {}).get("expected_version")
                if expected is None:
                    expected = (expected_versions or {}).get(item_id, item["applied_version"])
                if old["status"] not in {"draft", "returned"} or old["version"] != expected:
                    raise stale_draft()
            after = (
                current_candidate(conn, item, old, include_applications=True) if old else item["after_data"]
            ) | changes
            if after["kind"] == "rule" and item["after_data"]["kind"] != "rule":
                source = provenance.get_source_version(
                    conn, material_id, proposal["source_refs"][0].get("source_version")
                )
                ensure_org_root(conn)
                after = enrich_candidate(
                    conn, after, source["text"], legacy[1] if legacy else proposal["stats"]["document_facts"]
                )
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
                validate_candidate(conn, item, after)
                provenance_map = dict(after.get("field_provenance") or {})
                for key in changes:
                    if key not in {"waiver_applications", "kind"} and after.get(key) != item["after_data"].get(key):
                        provenance_map[key] = manual_provenance()
                after["field_provenance"] = provenance_map
            if old:
                if decision == "rejected":
                    from backend.api.v1.core import entity_action_in_transaction

                    updated = entity_action_in_transaction(
                        conn,
                        old["id"],
                        {"action": "archive", "reason": "核对页放弃未提交草稿", "expected_version": expected},
                    )
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


def adopt_changeset_item(conn: db.Connection, material_id: str, item: dict) -> dict:
    from backend.domains.reviews import mark_draft
    from backend.kernel.elements import sync_after_write

    candidate_data = item["after_data"]
    validate_candidate(conn, item, candidate_data)
    for ref in item["evidence"]:
        provenance.normalize_source_ref(conn, material_id, ref)
    material = materials.material(conn, material_id)
    candidate = materials.find_candidate(material, candidate_data["id"])
    candidate.update(candidate_data)
    materials.save_payload(conn, material, material["payload"])
    entity = materials.adopt_candidate_in_transaction(conn, material_id, candidate["id"])["entity"]
    mark_draft(
        conn,
        entity,
        item["validation"].get("last_content_editor_id") or item["decided_by"] or require_context().user_id,
        item["changeset_id"],
        editor_ids=item["validation"].get("editor_ids") or [],
    )
    confirm_applicability(conn, entity, candidate_data)
    entity = sync_after_write(conn, entity, meta_updates=candidate_meta(candidate_data))
    record_candidate_sources(conn, entity, candidate_data)
    conn.execute(
        "UPDATE changeset_items SET applied_entity_id=?,applied_version=?,after_data=? WHERE id=?",
        (entity["id"], entity["version"], materials.dump(candidate_data), item["id"]),
    )
    return entity


def apply(ident: str, *, submit: bool) -> dict:
    from backend.api.v1.core import entity_action_in_transaction

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
        stale_items = 0
        legacy = legacy_document(conn, proposal)
        for item in items:
            if legacy:
                align_legacy_item(item, candidates, legacy[0]["text"], legacy[1])
            if item_processed_elsewhere(item, candidates) or item["decision"] not in {"accepted", "edited"}:
                continue
            entity_id = item["applied_entity_id"]
            title = item["after_data"]["title"]
            if not entity_id and (item["validation"]["level"] == "error" or not item["evidence"]):
                remaining.append(
                    {
                        "item_id": item["id"],
                        "title": title,
                        "reasons": item["validation"].get("issues") or ["尚未找到可定位的原文，不能采纳。"],
                    }
                )
                continue
            blockers = []
            conn.execute("SAVEPOINT changeset_item")
            try:
                if entity_id:
                    entity = db.entity_json(
                        conn.execute("SELECT * FROM entities WHERE id=? FOR UPDATE", (entity_id,)).fetchone()
                    )
                    if not entity:
                        entity_id = None
                        raise ApiError("NOT_FOUND")
                else:
                    entity = adopt_changeset_item(conn, material_id, item)
                title = entity["title"]
                if submit and entity["status"] in {"draft", "returned"}:
                    guard_adopted_write(conn, entity)
                    if (
                        entity_id
                        and entity["version"] != item["applied_version"]
                        and not unchanged_return(conn, item, entity)
                    ):
                        stale_items += 1
                        blockers = [stale_draft().message]
                    else:
                        blockers = submission_blockers(gates.check_entity(conn, entity))
                    if not blockers:
                        entity_action_in_transaction(
                            conn,
                            entity["id"],
                            {
                                "action": "submit",
                                "reason": "建设方案核对后提交审核",
                                "expected_version": entity["version"],
                            },
                        )
                        conn.execute(
                            "UPDATE changeset_items SET applied_version=? WHERE id=?", (entity["version"], item["id"])
                        )
                        submitted_ids.append(entity["id"])
                entity_id = entity["id"] if entity["status"] != "archived" else None
            except (ApiError, HTTPException) as exc:
                error = from_http(exc.status_code, exc.detail) if isinstance(exc, HTTPException) else exc
                if not submit or error.code not in {
                    "VALIDATION_FAILED",
                    "EVIDENCE_NOT_FOUND",
                    "GATE_NOT_PASSED",
                    "NOT_FOUND",
                    "ITEM_CHANGED",
                    "PRECONDITION_FAILED",
                    "VERSION_CONFLICT",
                }:
                    raise
                conn.execute("ROLLBACK TO SAVEPOINT changeset_item")
                blockers = [error.message]
            conn.execute("RELEASE SAVEPOINT changeset_item")
            if blockers:
                remaining.append({"item_id": item["id"], "title": title, "reasons": blockers})
            if entity_id:
                entity_ids.append(entity_id)
        if stale_items and stale_items == len(remaining) and not submitted_ids:
            # Preserve the historical conflict when every outstanding draft needs a fresh version.
            raise stale_draft()
        if not entity_ids and not remaining:
            raise ApiError("PRECONDITION_FAILED", "请先采纳需要保留的条目。")
        pending = sum(
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
        "pending_count": pending,
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


def date_choice_id(fact: dict, source_id: str) -> str:
    basis = f"{source_id}:{fact['start']}:{fact['end']}"
    return "date-" + hashlib.sha256(basis.encode()).hexdigest()


def changeset_document_facts(conn: db.Connection, proposal: dict, source: dict, *, anchor: bool = False) -> dict:
    stored = (proposal.get("stats") or {}).get("document_facts") or {}
    if stored.get("recognition_version") == FACTS_VERSION:
        return stored
    # Older recognizers missed some dated provisions and scope exclusions.
    # Reads calculate choices from the frozen source without writing anchors.
    material_id = proposal["source_refs"][0]["material_id"]
    if anchor:
        facts = anchored_document_facts(conn, material_id, source)
    else:
        facts = document_facts(source["text"])
        for fact in [facts["scope"], *facts["valid_from_candidates"]]:
            if not fact["value"]:
                continue
            row = conn.execute(
                "SELECT id FROM source_anchors WHERE material_id=? AND source_version=? AND start=? AND end=?",
                (material_id, source["id"], fact["start"], fact["end"]),
            ).fetchone()
            fact.update(material_id=material_id, source_version=source["id"])
            if row:
                fact["anchor_id"] = row["id"]
        for option in facts["valid_from_candidates"]:
            option["candidate_id"] = date_choice_id(option, source["id"])
        if facts["valid_from"]["value"]:
            facts["valid_from"] = dict(facts["valid_from_candidates"][0])
    for key in ("valid_from", "scope"):
        if (stored.get(key) or {}).get("origin") == "manual":
            facts[key] = dict(stored[key])
    return facts


def needs_legacy_projection(proposal: dict) -> bool:
    stats = proposal.get("stats") or {}
    return (
        bool(stats.get("legacy_fact_items"))
        or (stats.get("document_facts") or {}).get("recognition_version") != FACTS_VERSION
    )


def legacy_document(conn: db.Connection, proposal: dict) -> tuple[dict, dict] | None:
    if not needs_legacy_projection(proposal):
        return None
    ref = proposal["source_refs"][0]
    source = provenance.get_source_version(conn, ref["material_id"], ref.get("source_version"))
    return source, changeset_document_facts(conn, proposal, source, anchor=True)


def align_legacy_item(item: dict, originals: dict, text: str, facts: dict) -> None:
    after = item["after_data"]
    origins = after.get("field_provenance") or {}
    original = originals.get(after.get("id"))
    grounded = None
    if not item.get("applied_entity_id") and original:
        grounded = ground_candidate({**original, "source_quotes": item["evidence"]}, text, facts)
        anchor_candidate_fields(grounded, item["evidence"])
    for key, record in list(origins.items()):
        if not isinstance(record, dict):
            continue
        if record.get("origin") == "document" or (
            record.get("origin") == "system" and record.get("level") == "document"
        ):
            if item.get("applied_entity_id"):
                origins[key] = {**record, "origin": "system", "level": "document"}
            elif key in {"valid_from", "scope"}:
                after[key] = facts[key]["value"]
                if after[key]:
                    origins[key] = dict(facts[key])
                else:
                    origins.pop(key)
        elif record.get("origin") == "source" or (
            record.get("origin") == "system" and key in {"conditions", "exceptions", "scope"}
        ):
            if grounded is not None and key in grounded:
                after[key] = grounded[key]
                if key in grounded["field_provenance"]:
                    origins[key] = grounded["field_provenance"][key]
                else:
                    origins.pop(key)
            elif after.get("extraction_mode") == "local":
                origins[key] = {**record, "origin": "system"}


def anchored_document_facts(conn: db.Connection, material_id: str, source: dict) -> dict:
    facts = document_facts(source["text"])
    for fact in [facts["scope"], *facts["valid_from_candidates"]]:
        if fact["value"]:
            ref = provenance.normalize_source_ref(conn, material_id, {**fact, "source_version": source["id"]})
            fact.update({key: ref[key] for key in ("anchor_id", "source_version", "material_id")})
    for option in facts["valid_from_candidates"]:
        option["candidate_id"] = option["anchor_id"]
    if facts["valid_from"]["value"]:
        facts["valid_from"] = dict(facts["valid_from_candidates"][0])
    return facts


def anchor_candidate_fields(candidate: dict, evidence: list[dict]) -> None:
    for field in candidate.get("field_provenance", {}).values():
        for origin in [field, *field.get("parts", [])]:
            if origin.get("origin") not in {"source", "system"} or origin.get("level") == "document":
                continue
            ref = next((ref for ref in evidence if origin.get("quote") and origin["quote"] in ref["quote"]), None)
            if ref:
                origin.update({key: ref[key] for key in ("anchor_id", "source_version", "material_id")})
                if isinstance(ref.get("start"), int):
                    start = ref["start"] + ref["quote"].index(origin["quote"])
                    origin.update(start=start, end=start + len(origin["quote"]))


def enrich_candidate(conn: db.Connection, candidate: dict, source_text: str, facts: dict | None = None) -> dict:
    result = ground_candidate(candidate, source_text, facts)
    if candidate["kind"] == "rule":
        suggest_applicability(conn, result)
    return result


def source_confirmation_limits(item: dict, candidates: dict, text: str, facts: dict) -> dict:
    """Use original evidence for the omission list and the write guard, even after manual edits."""
    after = item["after_data"]
    original = candidates.get(after.get("id")) or after
    evidence = [
        ref
        for ref in (item.get("evidence") or [])
        if isinstance(ref, dict) and isinstance(ref.get("quote"), str) and ref["quote"].strip() and ref["quote"] in text
    ]
    grounded = ground_candidate({**original, "source_quotes": evidence}, text, facts)
    return {
        "source_declared_fields": [field for field in ("conditions", "exceptions") if grounded.get(field)],
        "bulk_confirmation_blocked": not evidence or grounded["bulk_confirmation_blocked"],
    }


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
        result.setdefault("field_provenance", {})["applies_to_id"] = {"origin": "system", "quote": ""}


def candidate_meta(candidate: dict) -> dict:
    return {key: candidate.get(key) or None for key in ("valid_from", "valid_to")}


def candidate_payload(candidate: dict, evidence: list[dict]) -> dict:
    keys = ("content", "definition", "scope", "conditions", "exceptions", "unit", "grain", "boundary", "judgement")
    return {**{key: candidate[key] for key in keys if key in candidate}, "source_refs": evidence}


def suggested_relation(conn: db.Connection, entity: dict, candidate: dict) -> dict | None:
    if entity["kind"] != "rule":
        return None
    target = conn.execute(
        "SELECT id,title,kind FROM entities WHERE id=? AND status<>'archived' "
        "AND COALESCE(json_extract(payload,'$.active_asset'),1)!=0",
        (candidate.get("applies_to_id"),),
    ).fetchone()
    if not target or target["kind"] not in {"organization", "position", "party", "person", "object_type"}:
        return None
    return {
        "source": entity["id"],
        "target": target["id"],
        "type": "applies_to",
        "label": "适用于",
        "status": "confirmed",
        "target_kind": target["kind"],
        "target_title": target["title"],
        "payload": {"recipe_applicability": True},
    }


def confirm_applicability(conn: db.Connection, entity: dict, candidate: dict) -> None:
    relation = suggested_relation(conn, entity, candidate)
    if entity["kind"] != "rule":
        return
    if candidate.get("applies_to_id") and not relation:
        raise ApiError("VALIDATION_FAILED", "请选择本机构中已有的适用主体或业务对象。")
    existing = conn.execute(
        "SELECT id,target,status,payload FROM relations WHERE source=? AND type='applies_to'", (entity["id"],)
    ).fetchall()
    for row in existing:
        if json.loads(row["payload"] or "{}").get("recipe_applicability"):
            if relation and row["target"] == relation["target"] and row["status"] == "confirmed":
                return
            # Keep the previous human confirmation trace when applicability changes.
            conn.execute("UPDATE relations SET status='rejected' WHERE id=?", (row["id"],))
    if not relation:
        # An incomplete draft can be saved; QG4 still blocks its submission.
        return
    payload = {"recipe_applicability": True, "confirmed_by": require_context().user_id, "confirmed_at": db.now()}
    conn.execute(
        "INSERT INTO relations(id,source,target,type,label,status,payload) "
        "VALUES(?,?,?,'applies_to','适用于','confirmed',?)",
        ("rel-" + uuid.uuid4().hex, entity["id"], relation["target"], materials.dump(payload)),
    )
    from backend.kernel.modules import record_confirmed_references

    record_confirmed_references(conn, entity["id"], [relation["target"]])
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
    checks = gates.check_entity(
        conn,
        entity,
        meta_updates=candidate_meta(candidate),
        proposed_relations=[relation] if relation else [],
        preview=True,
    )
    sources = candidate_field_sources(candidate)
    for field in checks["fields"]:
        key = "subjects" if field["key"].startswith("subject:") else field["key"]
        if key == "subjects" and relation:
            field.update(
                key="subject:" + relation["target"],
                value=relation["target_title"],
                href="/ontology/elements/" + url_quote(relation["target"], safe=""),
            )
        if field["value"] and key in sources:
            field.update(sources[key])
    return checks


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

    if (
        candidate["kind"] == "rule"
        and candidate.get("applies_to_id")
        and not suggested_relation(conn, {"kind": "rule", "id": item["id"]}, candidate)
    ):
        raise ApiError("VALIDATION_FAILED", candidate["title"] + "：请选择本机构中有效的适用主体或业务对象。")
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


def candidate_field_sources(candidate: dict) -> dict:
    origins = {
        "source": "source",
        "system": "system",
        "document": "system",
        "manual": "editor",
        "bulk_confirmation": "batch_confirmation",
    }
    sources = {}
    allowed = {
        "rule": {"definition", "scope", "conditions", "exceptions", "valid_from", "valid_to", "subjects"},
        "data": {"definition", "unit", "grain"},
        "knowledge": {"definition", "usage_conditions", "boundary"},
        "skill": {"definition"},
    }.get(candidate["kind"], set())
    for key, value in (candidate.get("field_provenance") or {}).items():
        key = (
            "subjects"
            if key == "applies_to_id"
            else "usage_conditions"
            if key == "conditions" and candidate["kind"] == "knowledge"
            else key
        )
        if key in allowed and isinstance(value, dict) and value.get("origin") in origins:
            sources[key] = {
                "source": origins[value["origin"]],
                "source_quote": value.get("quote", ""),
                "confirmed_by": value.get("confirmed_by", ""),
                **{
                    name: value[name]
                    for name in ("actor_id", "method", "confirmed_at", "material_id", "source_version", "anchor_id")
                    if value.get(name)
                },
            }
            if value.get("parts"):
                sources[key]["parts"] = [
                    {
                        "value": part["value"],
                        "source": part["origin"],
                        "source_quote": part["quote"],
                        **{
                            name: part[name]
                            for name in ("material_id", "source_version", "anchor_id")
                            if part.get(name)
                        },
                    }
                    for part in value["parts"]
                    if isinstance(part, dict) and part.get("origin") in {"source", "system"} and part.get("value")
                ]
    return sources


def active_applications(result: dict) -> dict:
    return {item["code"]: item["application_reason"] for item in result["checks"] if item.get("application_reason")}


def record_candidate_sources(conn: db.Connection, entity: dict, candidate: dict) -> None:
    sources = candidate_field_sources(candidate)
    if sources:
        gates.record_field_sources(conn, entity, sources)
    applications = candidate.get("waiver_applications")
    if applications is not None:
        if not applications and not ((get_meta(conn, entity["id"]) or {}).get("gates") or {}).get("applications"):
            return
        result = gates.apply_for_waiver(
            conn, entity, {key: value for key, value in applications.items() if value is not None}
        )
        candidate["waiver_applications"] = active_applications(result)


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
        conn.execute("SELECT id FROM entities WHERE id=? FOR UPDATE", (source_ref["material_id"],)).fetchone()
        source = provenance.get_source_version(conn, source_ref["material_id"], source_ref.get("source_version"))
        stats = proposal.get("stats") or {}
        legacy_facts = needs_legacy_projection(proposal)
        facts = changeset_document_facts(conn, proposal, source, anchor=True)
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
        selection = body.get("valid_from_candidate_id")
        typed_date = body.get("valid_from") is not None
        date_changed = selection is not None or typed_date
        if selection is not None:
            if typed_date:
                raise ApiError("VALIDATION_FAILED", "请从原句中选择或手工填写一个生效日期。")
            # The client supplies only an ID. Re-derive eligible spans from this
            # changeset's immutable document version and validate the actual anchor.
            verified = anchored_document_facts(conn, source_ref["material_id"], source)
            chosen = next(
                (
                    c
                    for c in verified["valid_from_candidates"]
                    if selection in {c["anchor_id"], date_choice_id(c, source["id"])}
                ),
                None,
            )
            if chosen is None:
                raise ApiError("VALIDATION_FAILED", "请选择本方案原文中列出的施行日期候选句。")
            facts["valid_from_candidates"] = verified["valid_from_candidates"]
            facts["valid_from"] = {**chosen, **manual_provenance(), "method": "selection", "quote": chosen["quote"]}
        elif typed_date:
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
            if legacy_facts:
                align_legacy_item(item, candidates, source["text"], facts)
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
                guard_adopted_write(conn, old)
                expected = body.get("expected_versions", {}).get(item["id"], item["applied_version"])
                if old["version"] != expected:
                    raise stale_draft()
            after = current_candidate(conn, item, old, include_applications=True) if old else dict(item["after_data"])
            origins = dict(after.get("field_provenance") or {})
            if date_changed:
                after["valid_from"] = facts["valid_from"]["value"]
                origins["valid_from"] = dict(facts["valid_from"])
            limits = source_confirmation_limits(item, candidates, source["text"], facts)
            for key, selected, value in (
                ("exceptions", exceptions, "无例外"),
                ("conditions", conditions, ["无附加适用条件"]),
            ):
                if item["id"] in selected:
                    if limits["bulk_confirmation_blocked"]:
                        raise ApiError("VALIDATION_FAILED", "请先逐条核对引文中的条件和例外，不可批量确认为无。")
                    if key in limits["source_declared_fields"]:
                        raise ApiError("VALIDATION_FAILED", "原文中已写明条件或例外，请逐条核对，不可批量确认为无。")
                    if after.get(key):
                        raise ApiError("VERSION_CONFLICT", "这条规则已填写条件或例外，请刷新后逐条核对，不可批量覆盖。")
                    after[key] = value
                    origins[key] = manual_provenance("bulk_confirmation")
            after["field_provenance"] = origins
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
        if legacy_facts:
            stats["legacy_fact_items"] = True
        conn.execute("UPDATE changesets SET stats=?,updated_at=? WHERE id=?", (materials.dump(stats), db.now(), ident))
    return get(ident)
