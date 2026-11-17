"""Five deterministic release checks; callers own authorization and transactions.

Read paths compute fresh results without mutating business data. Release mutations
persist the same result and reject failures atomically. Waivers are content-bound,
reviewer-authored records, never a payload flag supplied by an editor.
"""

import hashlib
import json
import re
from contextlib import closing
from datetime import date

from backend import db
from backend.core.errors import ApiError
from backend.kernel.cards import plain
from backend.security.context import require_context

ASSETS = frozenset({"term", "object_type", "link_type", "data", "metric", "rule", "skill", "process", "knowledge"})
LABELS = ("出处可定位", "口径无歧义", "条件完整", "关系经审定", "版本可还原")
CONFIRMED = {"confirmed", "source_verified"}


def _object(value: object) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return {}
    return value if isinstance(value, dict) else {}


def hard_constraint(entity: dict) -> bool:
    payload = entity.get("payload") or {}
    text = " ".join(plain(payload.get(k)) for k in ("content", "rule_text", "obligation", "definition"))
    return bool(payload.get("authority_spec")) or bool(
        re.search(
            r"时限|期限|阈值|金额|审批权限|(?:\d+(?:\.\d+)?|[一二三四五六七八九十百千万]+)\s*(?:个工作日|工作日|日内|天内|万元|元|小时|%)",
            text,
        )
    )


def _dates(meta: dict) -> bool:
    try:
        start = date.fromisoformat(meta.get("valid_from") or "")
        end = date.fromisoformat(meta["valid_to"]) if meta.get("valid_to") else None
        return end is None or end >= start
    except (ValueError, TypeError):
        return False


def _process_complete(payload: dict) -> bool:
    steps = payload.get("steps") or []
    if not plain(payload.get("trigger")) or not plain(payload.get("end_result")) or len(steps) < 2:
        return False
    if any(not isinstance(step, dict) for step in steps):
        return False
    keys = {step.get("key") for step in steps}
    if len(keys) != len(steps) or not all(isinstance(key, str) and key.strip() for key in keys):
        return False
    edges = {key: set() for key in keys}
    for step in steps:
        if (
            step.get("confirmed") is not True
            or not step.get("actors")
            or not (step.get("basis") or step.get("outputs"))
        ):
            return False
        for target in step.get("next") or []:
            if target not in keys:
                return False
            edges[step["key"]].add(target)
            edges[target].add(step["key"])
    visited, pending = set(), [steps[0]["key"]]
    while pending:
        key = pending.pop()
        if key not in visited:
            visited.add(key)
            pending.extend(edges[key] - visited)
    return visited == keys


def evaluate_checks(entity: dict, meta: dict, facts: dict) -> dict:
    """Pure decision table. Facts are collected from the tenant database below."""
    kind, payload = entity["kind"], entity.get("payload") or {}
    identity = kind in {"position", "party"}
    applicable = kind in ASSETS or identity
    evidence = facts.get("evidence", False)
    if hard_constraint(entity):
        evidence = evidence and facts.get("regulation_evidence", False)
    if kind == "process":
        evidence = evidence and facts.get("step_evidence", False)
    meaning = (
        isinstance(payload.get("definition"), str)
        and bool(payload["definition"].strip())
        and not facts.get("duplicate", False)
    )
    if kind in {"metric", "data"}:
        meaning = meaning and bool(plain(payload.get("unit"))) and bool(plain(payload.get("grain")))
    if kind == "object_type":
        identifiers = (payload.get("identity") or {}).get("properties") or []
        property_keys = {p.get("key") for p in payload.get("properties", []) if isinstance(p, dict)}
        meaning = meaning and bool(identifiers) and set(identifiers) <= property_keys
    if identity:
        meaning = meaning and bool(
            payload.get("org_id") if kind == "position" else payload.get("unified_code") or payload.get("identity")
        )
    complete = True
    if kind == "rule":
        complete = all(plain(payload.get(key)) for key in ("scope", "conditions", "exceptions")) and _dates(meta)
        if payload.get("judgement"):
            complete = complete and facts.get("tests", False)
    elif kind == "metric" and payload.get("formula"):
        complete = facts.get("tests", False)
    elif kind == "knowledge":
        complete = bool(plain(payload.get("conditions") or payload.get("usage_conditions"))) and bool(
            plain(payload.get("boundary"))
        )
    elif kind == "skill":
        complete = all(payload.get(key) for key in ("inputs", "outputs", "steps")) and facts.get("tests", False)
    elif kind == "process":
        complete = _process_complete(payload)
    outcomes = (evidence, meaning, complete, facts.get("relations", False), facts.get("snapshot", False))
    reasons = (
        "请补充可逐字定位的原文出处。"
        if not hard_constraint(entity)
        else "时限、金额或审批要求须有制度原文依据；目前依据不足，待确认。",
        "请补充一句话定义、单位与粒度或身份标识，并处理同名不同义的内容。",
        "请补齐适用范围、条件、例外和生效日期；有判定式时至少两个用例全部通过。"
        if kind == "rule"
        else "请补齐用例并运行通过。"
        if kind == "metric"
        else "请补齐起点、终点和至少两个连通步骤，确认每步的经办方与依据或结果。"
        if kind == "process"
        else "请补齐输入、输出、步骤并完成测试。"
        if kind == "skill"
        else "请补齐使用条件与适用边界。",
        "请确认必备关系：规则的适用主体或对象、指标的业务对象、对象关系的两端、技能依赖或每步经办方。",
        "请先保存带变更原因的版本快照，确保历史可还原。",
    )
    checks = []
    for index, (label, passed) in enumerate(zip(LABELS, outcomes, strict=True), 1):
        code = f"QG{index}"
        needed = applicable and (not identity or index in {2, 5})
        checks.append(
            {
                "code": code,
                "label": label,
                "status": "pass" if needed and passed else "fail" if needed else "not_applicable",
                "reason": "检查通过。"
                if needed and passed
                else reasons[index - 1]
                if needed
                else "此类内容按专用办理规则管理。"
                if not identity
                else "本项仅供参考，不阻断身份内容。",
                "can_waive": needed and index in {1, 3},
                "waiver_reason": "",
            }
        )
    return _result(checks)


def _result(checks: list[dict]) -> dict:
    blockers = [f"{item['label']}：{item['reason']}" for item in checks if item["status"] == "fail"]
    return {"passed": not blockers, "checks": checks, "blockers": blockers}


def _valid_reference(conn: db.Connection, ref: object) -> tuple[bool, bool]:
    if not isinstance(ref, dict):
        return False, False
    if ref.get("kind", "anchor") == "dataset":
        row = conn.execute(
            "SELECT payload FROM entities WHERE id=? AND kind='dataset'", (ref.get("dataset_id"),)
        ).fetchone()
        payload = _object(row["payload"]) if row else {}
        fields = payload.get("columns") or payload.get("fields") or []
        names = {
            field.get("name") if isinstance(field, dict) else field
            for field in fields
            if isinstance(field, (str, dict))
        }
        return bool(row and ref.get("column") in names), False
    if ref.get("kind", "anchor") != "anchor":
        # External notes and future statement records are not verified anchors.
        return False, False
    material_id = ref.get("material_id")
    anchor_id = ref.get("anchor_id") or ref.get("span_id")
    if not anchor_id and ref.get("chunk_id"):
        linked = conn.execute("SELECT anchor_id FROM chunk_anchors WHERE chunk_id=?", (ref["chunk_id"],)).fetchone()
        anchor_id = linked["anchor_id"] if linked else None
    row = conn.execute(
        "SELECT a.*,v.text,v.material_id source_material_id,e.source_kind,m.subtype "
        "FROM source_anchors a JOIN source_versions v ON v.id=a.source_version "
        "JOIN entities e ON e.id=a.material_id AND e.kind='material' "
        "LEFT JOIN entity_meta m ON m.entity_id=e.id WHERE a.id=? AND a.material_id=?",
        (anchor_id, material_id),
    ).fetchone()
    if not row or row["source_material_id"] != material_id:
        return False, False
    quote = row["text"][row["start"] : row["end"]]
    valid = (
        bool(quote) and ref.get("quote") == quote and hashlib.sha256(quote.encode()).hexdigest() == row["quote_sha256"]
    )
    valid = valid and (not ref.get("source_version") or ref["source_version"] == row["source_version"])
    valid = (
        valid
        and ("start" not in ref or ref["start"] == row["start"])
        and ("end" not in ref or ref["end"] == row["end"])
    )
    return valid, valid and (
        row["subtype"] == "regulation" or (not row["subtype"] and row["source_kind"] == "regulation")
    )


def _relations_pass(
    conn: db.Connection, entity: dict, proposed: tuple | list = (), *, declared_empty: bool = False
) -> bool:
    rows = [
        dict(row)
        for row in conn.execute(
            "SELECT r.*,s.kind source_kind,t.kind target_kind FROM relations r "
            "JOIN entities s ON s.id=r.source JOIN entities t ON t.id=r.target WHERE r.source=? OR r.target=?",
            (entity["id"], entity["id"]),
        )
    ] + list(proposed)
    kind = entity["kind"]
    required = {
        "rule": {"applies_to", "governs"},
        "metric": {"measures"},
        "link_type": {"link_source", "link_target"},
        "skill": {"requires_data", "uses_data", "based_on", "implements_rule", "typed_input"},
        "process": {"performed_by"},
    }.get(kind, set())
    if any(row["type"] in required and row["status"] == "candidate" for row in rows):
        return False
    outgoing = [
        row for row in rows if row["source"] == entity["id"] and row["type"] in required and row["status"] in CONFIRMED
    ]
    if kind == "rule":
        return any(
            row.get("target_kind") in {"organization", "position", "person", "party", "object_type"} for row in outgoing
        )
    if kind == "metric":
        return any(row.get("target_kind") == "object_type" for row in outgoing)
    if kind == "link_type":
        return all(
            any(row["type"] == typ and row.get("target_kind") == "object_type" for row in outgoing) for typ in required
        )
    if kind == "skill":
        return bool(outgoing) or declared_empty
    if kind == "process":
        steps = entity["payload"].get("steps") or []
        return bool(steps) and all(
            any(_object(row.get("payload")).get("step_key") == step.get("key") for row in outgoing) for step in steps
        )
    return True


def expression_context(conn: db.Connection, entity: dict) -> dict:
    payload = entity.get("payload") or {}
    object_id = payload.get("object_type") or (payload.get("judgement") or {}).get("applies_to", {}).get("object_type")
    if not object_id:
        return {"properties": (), "relations": {}}
    row = conn.execute("SELECT payload FROM entities WHERE id=? AND kind='object_type'", (object_id,)).fetchone()
    if not row:
        raise ApiError("EXPRESSION_INVALID", "判定式引用的业务对象不存在。")
    related = {}
    for link in conn.execute(
        "SELECT e.id,e.payload,m.element_key FROM entities e JOIN entity_meta m ON m.entity_id=e.id "
        "WHERE e.kind='link_type'"
    ):
        body = _object(link["payload"])
        if link["element_key"] and object_id in {body.get("source_type"), body.get("target_type")}:
            other = body.get("target_type") if body.get("source_type") == object_id else body.get("source_type")
            target = conn.execute("SELECT payload FROM entities WHERE id=? AND kind='object_type'", (other,)).fetchone()
            if target:
                related[link["element_key"]] = _object(target["payload"]).get("properties", [])
    return {"properties": _object(row["payload"]).get("properties", []), "relations": related}


def run_cases(conn: db.Connection, entity: dict) -> dict:
    from backend.kernel.rules import run_test_cases

    return run_test_cases(entity["kind"], entity.get("payload") or {}, **expression_context(conn, entity))


def _fingerprint(entity: dict, meta: dict) -> str:
    data = {key: entity.get(key) for key in ("id", "kind", "title", "summary", "domain")}
    data["payload"] = {key: value for key, value in entity.get("payload", {}).items() if key != "r2_review"}
    data["meta"] = {key: meta.get(key) for key in ("valid_from", "valid_to", "module_id", "subtype")}
    return hashlib.sha256(
        json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def check_entity(
    conn: db.Connection,
    entity: dict,
    *,
    meta_updates: dict | None = None,
    proposed_relations: tuple | list = (),
    preview: bool = False,
) -> dict:
    from backend.kernel.metadata import get_meta

    meta = {**(get_meta(conn, entity["id"]) or {}), **(meta_updates or {})}
    original = entity
    package_facts = None
    if entity.get("payload", {}).get("managed_skill"):
        from backend.skills import skill_gate_facts

        package_facts = skill_gate_facts(conn, entity)
        entity = {**entity, "payload": package_facts["payload"]}
    payload = entity.get("payload") or {}
    refs = payload.get("source_refs") or []
    verified = [_valid_reference(conn, ref) for ref in refs] if isinstance(refs, list) else []
    duplicate = False
    if entity["kind"] in ASSETS:
        rows = conn.execute(
            "SELECT e.payload FROM entities e JOIN entity_meta m ON m.entity_id=e.id "
            "WHERE e.id<>? AND e.title=? AND e.kind=? AND COALESCE(m.module_id,'MOD-DEFAULT')=? "
            "AND COALESCE(json_extract(e.payload,'$.active_asset'),1)!=0",
            (entity["id"], entity["title"], entity["kind"], meta.get("module_id") or "MOD-DEFAULT"),
        )
        duplicate = any(
            plain(_object(row["payload"]).get("definition")) != plain(payload.get("definition")) for row in rows
        )
    tests = True
    if payload.get("judgement") or (entity["kind"] == "metric" and payload.get("formula")):
        try:
            tests = run_cases(conn, entity)["passed"]
        except (ValueError, ApiError):
            tests = False
    elif entity["kind"] == "skill":
        # Only the controlled package's latest matching proof is trusted.
        tests = bool(package_facts and package_facts["tests"])
    snapshot = preview or bool(
        conn.execute(
            "SELECT 1 FROM versions WHERE entity_id=? AND TRIM(COALESCE(reason,''))<>'' LIMIT 1", (entity["id"],)
        ).fetchone()
    )
    steps = payload.get("steps") or []
    facts = {
        "evidence": bool(verified) and all(v[0] for v in verified),
        "regulation_evidence": any(v[1] for v in verified),
        "step_evidence": all(
            step.get("source_refs") and all(_valid_reference(conn, r)[0] for r in step["source_refs"]) for step in steps
        ),
        "duplicate": duplicate,
        "tests": tests,
        "snapshot": snapshot,
        "relations": _relations_pass(
            conn, entity, proposed_relations, declared_empty=bool(package_facts and package_facts["relations"])
        )
        and (not package_facts or package_facts["relations"]),
    }
    result = evaluate_checks(entity, meta, facts)
    stored = _object(meta.get("gates"))
    fingerprint = _fingerprint(original, meta)
    for item in result["checks"]:
        waiver = stored.get("waivers", {}).get(item["code"], {})
        if (
            item["can_waive"]
            and item["status"] == "fail"
            and waiver.get("fingerprint") == fingerprint
            and waiver.get("reason")
        ):
            item.update(status="waived", waiver_reason=waiver["reason"], reason="审核人员已记录豁免理由。")
    return _result(result["checks"])


def persist(conn: db.Connection, entity: dict, result: dict) -> None:
    from backend.kernel.metadata import get_meta, sync_meta

    meta = get_meta(conn, entity["id"]) or sync_meta(conn, entity)
    stored = _object(meta.get("gates"))
    stored.update(result=result, checked_version=entity["version"], checked_at=db.now())
    conn.execute(
        "UPDATE entity_meta SET gates=? WHERE entity_id=?", (json.dumps(stored, ensure_ascii=False), entity["id"])
    )


def enforce(conn: db.Connection, entity: dict) -> dict:
    result = check_entity(conn, entity)
    if not result["passed"]:
        raise ApiError("GATE_NOT_PASSED", "还差：" + "；".join(result["blockers"]), details={"gates": result})
    persist(conn, entity, result)
    return result


def waive(conn: db.Connection, entity: dict, reasons: dict[str, str]) -> dict:
    from backend.kernel.metadata import get_meta, sync_meta

    person = require_context()
    if person.readonly or person.role not in {"admin", "reviewer"}:
        raise ApiError("PERMISSION_DENIED")
    if not reasons:
        raise ApiError("VALIDATION_FAILED", "请选择需豁免的项目并写明理由。")
    if set(reasons) - {"QG1", "QG3"}:
        raise ApiError("VALIDATION_FAILED", "口径、关系和版本三项不能豁免。")
    if any(not isinstance(reason, str) or not reason.strip() or len(reason) > 500 for reason in reasons.values()):
        raise ApiError("VALIDATION_FAILED", "豁免必须写明接受此项缺口的理由。")
    identity = entity["payload"].get("r2_review", {})
    if person.user_id in {identity.get("last_editor_id"), identity.get("submitted_by")}:
        raise ApiError("SEPARATION_OF_DUTIES")
    if not identity:
        from backend.domains.reviews import WORKFLOW_ACTIONS, is_same_person

        actors = conn.execute(
            "SELECT action,actor FROM activities WHERE entity_id=? ORDER BY id DESC", (entity["id"],)
        ).fetchall()
        editor = next((row["actor"] for row in actors if row["action"] not in WORKFLOW_ACTIONS), None)
        submitter = next((row["actor"] for row in actors if row["action"] == "提交审核"), None)
        if is_same_person(person, editor) or is_same_person(person, submitter):
            raise ApiError("SEPARATION_OF_DUTIES")
    if entity["payload"].get("managed_skill"):
        document = conn.execute(
            "SELECT last_editor_id,submitted_by FROM skill_documents WHERE entity_id=?", (entity["id"],)
        ).fetchone()
        if document and person.user_id in {document["last_editor_id"], document["submitted_by"]}:
            raise ApiError("SEPARATION_OF_DUTIES")
    meta = get_meta(conn, entity["id"]) or sync_meta(conn, entity)
    stored = _object(meta.get("gates"))
    waivers = stored.setdefault("waivers", {})
    fingerprint = _fingerprint(entity, meta)
    for code, reason in reasons.items():
        waivers[code] = {
            "reason": reason.strip(),
            "by": person.user_id,
            "at": db.now(),
            "fingerprint": fingerprint,
            "version": entity["version"],
        }
        db.log(conn, entity["id"], "发布检查豁免", f"{LABELS[int(code[-1]) - 1]}：{reason.strip()}", person.username)
    conn.execute(
        "UPDATE entity_meta SET gates=? WHERE entity_id=?", (json.dumps(stored, ensure_ascii=False), entity["id"])
    )
    result = check_entity(conn, entity)
    persist(conn, entity, result)
    return result


def check(entity_id: str) -> dict:
    with closing(db.connect()) as conn:
        entity = db.entity_json(conn.execute("SELECT * FROM entities WHERE id=?", (entity_id,)).fetchone())
        if not entity:
            raise ApiError("NOT_FOUND")
        return check_entity(conn, entity)
