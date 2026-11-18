"""Five deterministic release checks; callers own authorization and transactions.

Read paths compute fresh results without mutating business data. Release mutations
persist the same result and reject failures atomically. Waivers are content-bound,
reviewer-authored records, never a payload flag supplied by an editor.
"""

import hashlib
import json
import re
from contextlib import closing
from datetime import UTC, datetime

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


# An amount field/unit or an arithmetic definition describes data, not a limit.
# Keep explicit normative limits (including relative amounts without a numeral)
# alongside deadlines, authority and stated monetary values. Rule OXL is checked
# independently below, so a bland sentence cannot hide its numeric constraint.
_HARD_WORDS = re.compile(
    r"时限|期限|阈值|限额|上限|下限|审批权限|(?:不得|不能|不应|不可|不)(?:超过|高于|低于|少于|多于)|"
    r"(?:金额|付款|支付|额度)[^。；\n]{0,24}(?:超过|高于|低于|达到)[^。；\n]{0,24}(?:时|则|须|需|应)"
)
_HARD_NUMBERS = re.compile(r"\d+(?:\.\d+)?|[一二三四五六七八九十百千][一二三四五六七八九十百千万亿]*")
_HARD_UNIT = re.compile(r"\s*(?:个工作日|工作日|日内|天内|亿元|万元|元|小时|%)")
_NUMERIC_DATE_TYPES = {"integer", "decimal", "number", "date", "datetime"}
_NUMERIC_DATE_CALLS = {"max", "min", "abs", "round", "len", "days_between", "today", "year", "month"}


def _hard_prose(text: str) -> bool:
    # Consume each complete numeric token once before checking its suffix. A
    # failed unit match must not restart at every digit of the same long number.
    return bool(_HARD_WORDS.search(text)) or any(
        _HARD_UNIT.match(text, number.end()) for number in _HARD_NUMBERS.finditer(text)
    )


def hard_constraint(entity: dict, *, expression_schema: dict | None = None) -> bool:
    """Inspect governed prose and validated OXL, never a supplied sentence alone.

    The caller supplies declarations loaded from the current tenant. Invalid
    expressions cannot downgrade the evidence requirement while QG3 reports the
    expression problem. Test-case inputs are not themselves rule constraints.
    """
    from backend.kernel import oxl

    payload = _object(entity.get("payload"))
    judgement = _object(payload.get("judgement"))
    blocks = [judgement.get("condition"), judgement.get("requirement")]
    exceptions = judgement.get("exceptions") or []
    if isinstance(exceptions, list):
        blocks.extend(exceptions)
    # A metric's arithmetic is not automatically a normative threshold.
    # Its declared prose still participates in the four hard-constraint classes.
    prose_blocks = [*blocks, payload.get("formula")]
    text = " ".join(
        [plain(entity.get("title")), plain(entity.get("summary"))]
        + [
            plain(payload.get(key))
            for key in (
                "content",
                "rule_text",
                "obligation",
                "definition",
                "scope",
                "conditions",
                "exceptions",
                "constraints",
            )
        ]
        + [plain(block) for block in prose_blocks]
    )
    if payload.get("authority_spec") or _hard_prose(text):
        return True
    if entity.get("kind") != "rule":
        return False
    schema = expression_schema or {"properties": (), "relations": {}}
    for block in blocks:
        if not isinstance(block, dict) or not block.get("oxl"):
            continue
        try:
            expression = oxl.parse(block["oxl"], **schema)
        except oxl.OXLError:
            return True
        pending = [expression.root]
        while pending:
            node = pending.pop()
            if node.kind in {"binary", "aggregate"} or (node.kind == "call" and node.value in _NUMERIC_DATE_CALLS):
                return True
            if node.kind == "literal" and type(node.value) in {int, float}:
                return True
            if node.kind == "property":
                field = expression.properties.get(node.value, {})
                name = " ".join(str(field.get(key) or "") for key in ("key", "name", "unit", "note"))
                if field.get("datatype", field.get("data_type")) in _NUMERIC_DATE_TYPES or _hard_prose(name):
                    return True
            if node.kind == "literal" and isinstance(node.value, str):
                try:
                    datetime.fromisoformat(node.value.replace("Z", "+00:00"))
                except ValueError:
                    pass
                else:
                    return True
            pending.extend(node.children)
    return False


def _dates(meta: dict) -> bool:
    def instant(value: object) -> datetime:
        if not isinstance(value, str):
            raise ValueError("Date must be text")
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)

    try:
        start = instant(meta.get("valid_from"))
        end = instant(meta["valid_to"]) if meta.get("valid_to") is not None else None
        return end is None or end >= start
    except (ValueError, TypeError):
        return False


def _process_complete(payload: dict) -> bool:
    steps = payload.get("steps") or []
    if not isinstance(steps, list) or any(not isinstance(step, dict) for step in steps):
        return False
    if not plain(payload.get("trigger")) or not plain(payload.get("end_result")) or len(steps) < 2:
        return False
    if any(not isinstance(step.get("key"), str) or not step["key"].strip() for step in steps):
        return False
    keys = {step["key"] for step in steps}
    if len(keys) != len(steps):
        return False
    edges = {key: set() for key in keys}
    for step in steps:
        if (
            step.get("confirmed") is not True
            or not step.get("actors")
            or not (step.get("basis") or step.get("outputs"))
        ):
            return False
        following = step.get("next") or []
        if not isinstance(following, list):
            return False
        for target in following:
            if not isinstance(target, str) or target not in keys:
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
    hard = facts["hard_constraint"] if "hard_constraint" in facts else hard_constraint(entity)
    if hard:
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
    missing = []
    expression_error = facts.get("expression_error", "")
    if kind == "rule":
        from backend.kernel.rules import validate_judgement_shape

        try:
            validate_judgement_shape(payload)
        except ValueError as error:
            expression_error = str(error)
    if kind == "rule":
        missing = [
            label
            for key, label in (("scope", "适用范围"), ("conditions", "适用条件"), ("exceptions", "例外"))
            if not plain(payload.get(key))
        ]
        if not meta.get("valid_from"):
            missing.append("生效日期")
        elif not _dates(meta):
            missing.append("有效的生效日期和失效日期（失效日期不得早于生效日期）")
        if payload.get("judgement") and not facts.get("tests", False):
            missing.append("判定式用例（至少两个且全部通过）")
        complete = not missing and not expression_error
    elif kind == "metric" and payload.get("formula"):
        complete = facts.get("tests", False)
    elif kind == "knowledge":
        missing = [] if plain(payload.get("conditions") or payload.get("usage_conditions")) else ["使用条件"]
        if not plain(payload.get("boundary")):
            missing.append("使用边界")
        complete = not missing
    elif kind == "skill":
        complete = all(payload.get(key) for key in ("inputs", "outputs", "steps")) and facts.get("tests", False)
    elif kind == "process":
        complete = _process_complete(payload)
    outcomes = (evidence, meaning, complete, facts.get("relations", False), facts.get("snapshot", False))
    reasons = (
        "请补充可逐字定位的原文出处。" if not hard else "时限、金额或审批要求须有制度原文依据；目前依据不足，待确认。",
        "已有同名内容《" + str(entity.get("title", "")) + "》使用不同定义，请核对现有内容或归档不用的草稿。"
        if facts.get("duplicate")
        else "请补充一句话定义、单位与粒度或身份标识。",
        (
            "；".join(
                (["请补充：" + "、".join(missing)] if missing else [])
                + ([expression_error.rstrip("。；")] if expression_error else [])
            )
            + "。"
        )
        if kind in {"rule", "knowledge"}
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
    return _result(checks, hard_constraint=hard)


def submission_eligibility(result: dict, applications: dict | None = None) -> bool:
    for item in result["checks"]:
        if item["status"] != "fail":
            continue
        reason = (applications or {}).get(item["code"], item.get("application_reason", ""))
        if (
            not item["can_waive"]
            or not isinstance(reason, str)
            or not reason.strip()
            or len(reason) > 500
            or (item.get("denial_reason") and not (applications or {}).get(item["code"]))
        ):
            return False
    return True


def _result(checks: list[dict], *, hard_constraint: bool = False) -> dict:
    blockers = [f"{item['label']}：{item['reason']}" for item in checks if item["status"] == "fail"]
    submission_blockers = [
        f"{item['label']}：{item['reason']}"
        for item in checks
        if item["status"] == "fail"
        and not (item["can_waive"] and item.get("application_reason") and not item.get("denial_reason"))
    ]
    return {
        "passed": not blockers,
        "ready_to_submit": not submission_blockers,
        "submission_blockers": submission_blockers,
        "checks": checks,
        "blockers": blockers,
        "hard_constraint": hard_constraint,
    }


def _valid_reference(conn: db.Connection, ref: object) -> tuple[bool, bool]:
    if not isinstance(ref, dict):
        return False, False
    if ref.get("kind", "anchor") == "dataset":
        if not all(isinstance(ref.get(key), str) and ref[key].strip() for key in ("dataset_id", "sheet", "column")):
            return False, False
        row = conn.execute(
            "SELECT s.payload FROM dataset_sheets s JOIN entities e ON e.id=s.dataset_id AND e.kind='dataset' "
            "WHERE s.dataset_id=? AND s.sheet_name=?",
            (ref["dataset_id"], ref["sheet"]),
        ).fetchone()
        fields = _object(row["payload"]).get("columns", []) if row else []
        if not isinstance(fields, list):
            return False, False
        names = {
            field.get("name") for field in fields if isinstance(field, dict) and isinstance(field.get("name"), str)
        }
        return bool(row and ref["column"] in names), False
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
        return (
            isinstance(steps, list)
            and bool(steps)
            and all(
                isinstance(step, dict)
                and any(_object(row.get("payload")).get("step_key") == step.get("key") for row in outgoing)
                for step in steps
            )
        )
    return True


def expression_context(conn: db.Connection, entity: dict) -> dict:
    from backend.kernel.rules import validate_judgement_shape

    payload = entity.get("payload") or {}
    try:
        validate_judgement_shape(payload)
    except ValueError as error:
        raise ApiError("EXPRESSION_INVALID", str(error)) from error
    judgement = _object(payload.get("judgement"))
    object_id = payload.get("object_type") or _object(judgement.get("applies_to")).get("object_type")
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

    try:
        context = expression_context(conn, entity)
    except (ValueError, ApiError) as error:
        return {"passed": False, "total": 0, "passed_count": 0, "results": [], "errors": [str(error)]}
    return run_test_cases(entity["kind"], entity.get("payload") or {}, **context)


def _fingerprint(entity: dict, meta: dict, conn: db.Connection | None = None) -> str:
    data = {key: entity.get(key) for key in ("id", "kind", "title", "summary", "domain")}
    data["payload"] = {key: value for key, value in entity.get("payload", {}).items() if key != "r2_review"}
    data["meta"] = {key: meta.get(key) for key in ("valid_from", "valid_to", "module_id", "subtype")}
    data["revision_id"] = _object(entity.get("payload", {}).get("r2_review")).get("revision_id")
    data["field_sources"] = _object(meta.get("gates")).get("field_sources", {})
    if conn is not None:
        # A candidate's independent confirmation changes approval, not authored
        # content. Changing an endpoint/type or rejecting it changes the binding.
        # A return-generated follow-up task is workflow history, not authored content.
        data["relations"] = sorted(
            {
                (row["source"], row["target"], row["type"])
                for row in conn.execute(
                    "SELECT r.source,r.target,r.type FROM relations r JOIN entities t ON t.id=r.target "
                    "WHERE (r.source=? OR r.target=?) "
                    "AND r.status IN ('candidate','confirmed','source_verified') "
                    "AND NOT (r.type='requires' AND t.kind='task')",
                    (entity["id"], entity["id"]),
                )
            }
        )
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
            "AND e.status NOT IN ('archived','discarded') "
            "AND COALESCE(json_extract(e.payload,'$.active_asset'),1)!=0",
            (entity["id"], entity["title"], entity["kind"], meta.get("module_id") or "MOD-DEFAULT"),
        )
        duplicate = any(
            plain(_object(row["payload"]).get("definition")) != plain(payload.get("definition")) for row in rows
        )
    tests = True
    expression_error = ""
    if (entity["kind"] == "rule" and payload.get("judgement") is not None) or (
        entity["kind"] == "metric" and payload.get("formula")
    ):
        try:
            report = run_cases(conn, entity)
            tests = report["passed"]
            expression_error = "；".join(report.get("errors", []))
        except (ValueError, ApiError) as error:
            tests = False
            expression_error = str(error)
    elif entity["kind"] == "skill":
        # Only the controlled package's latest matching proof is trusted.
        tests = bool(package_facts and package_facts["tests"])
    snapshot = preview or bool(
        conn.execute(
            "SELECT 1 FROM versions WHERE entity_id=? AND TRIM(COALESCE(reason,''))<>'' LIMIT 1", (entity["id"],)
        ).fetchone()
    )
    steps = payload.get("steps") or []
    step_evidence = False
    if entity["kind"] == "process" and isinstance(steps, list) and steps:
        step_evidence = all(
            isinstance(step, dict)
            and isinstance(step.get("source_refs"), list)
            and bool(step["source_refs"])
            and all(_valid_reference(conn, ref)[0] for ref in step["source_refs"])
            for step in steps
        )
    try:
        schema = expression_context(conn, entity) if entity["kind"] == "rule" and payload.get("judgement") else None
    except (ValueError, ApiError):
        schema = None
    facts = {
        "hard_constraint": hard_constraint(entity, expression_schema=schema),
        "evidence": bool(verified) and all(v[0] for v in verified),
        "regulation_evidence": any(v[1] for v in verified),
        "step_evidence": step_evidence,
        "duplicate": duplicate,
        "tests": tests,
        "expression_error": expression_error,
        "snapshot": snapshot,
        "relations": _relations_pass(
            conn, entity, proposed_relations, declared_empty=bool(package_facts and package_facts["relations"])
        )
        and (not package_facts or package_facts["relations"]),
    }
    result = evaluate_checks(entity, meta, facts)
    stored = _object(meta.get("gates"))
    fingerprint = _fingerprint(original, meta, conn)
    for item in result["checks"]:
        application = _object(_object(stored.get("applications")).get(item["code"]))
        if item["can_waive"] and item["status"] == "fail" and application.get("fingerprint") == fingerprint:
            item.update(
                application_reason=application.get("reason", ""),
                application_by=application.get("name", ""),
                application_at=application.get("at", ""),
            )
        denial = _object(_object(stored.get("denials")).get(item["code"]))
        if denial.get("fingerprint") == fingerprint:
            item["denial_reason"] = denial.get("reason", "")
        waiver = _object(_object(stored.get("waivers")).get(item["code"]))
        if (
            item["can_waive"]
            and item["status"] == "fail"
            and waiver.get("fingerprint") == fingerprint
            and waiver.get("reason")
        ):
            item.update(status="waived", waiver_reason=waiver["reason"], reason="审核人员已记录豁免理由。")
    from backend.kernel.gate_fields import project

    return {
        **_result(result["checks"], hard_constraint=result["hard_constraint"]),
        "fields": project(conn, original, meta),
    }


def persist(conn: db.Connection, entity: dict, result: dict) -> None:
    from backend.kernel.metadata import get_meta, sync_meta

    meta = get_meta(conn, entity["id"]) or sync_meta(conn, entity)
    stored = _object(meta.get("gates"))
    stored.update(result=result, checked_version=entity["version"], checked_at=db.now())
    conn.execute(
        "UPDATE entity_meta SET gates=? WHERE entity_id=?", (json.dumps(stored, ensure_ascii=False), entity["id"])
    )


def enforce(conn: db.Connection, entity: dict, *, stage: str = "release") -> dict:
    result = check_entity(conn, entity)
    if not result["ready_to_submit" if stage == "submit" else "passed"]:
        raise ApiError(
            "GATE_NOT_PASSED",
            "还差："
            + "；".join(
                reason.rstrip("。；") for reason in result["submission_blockers" if stage == "submit" else "blockers"]
            ),
            details={"gates": result, "repair_href": "/assets/" + entity["id"]},
        )
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
    from backend.domains.reviews import ensure_independent

    ensure_independent(entity)
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
    if entity["status"] != "review":
        raise ApiError("PRECONDITION_FAILED", "请先提交申请，由独立审核人员在待审核内容中办理。")
    meta = get_meta(conn, entity["id"]) or sync_meta(conn, entity)
    stored = _object(meta.get("gates"))
    current = {item["code"]: item for item in check_entity(conn, entity)["checks"]}
    for code in reasons:
        if identity.get("revision_id") and not current[code].get("application_reason"):
            raise ApiError("PRECONDITION_FAILED", "建设人员尚未为此项缺口提交有效申请说明。")
    waivers = stored.setdefault("waivers", {})
    fingerprint = _fingerprint(entity, meta, conn)
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


def record_field_sources(conn: db.Connection, entity: dict, sources: dict) -> None:
    from backend.kernel.gate_fields import record

    record(conn, entity, sources)


def _application_reasons(reasons: dict) -> dict[str, str]:
    if not isinstance(reasons, dict) or set(reasons) - {"QG1", "QG3"}:
        raise ApiError("VALIDATION_FAILED", "仅出处和条件两项可逐项申请豁免。")
    if any(not isinstance(value, str) or not value.strip() or len(value) > 500 for value in reasons.values()):
        raise ApiError("VALIDATION_FAILED", "请逐项填写不超过500字的申请说明或办理理由。")
    return {key: value.strip() for key, value in reasons.items()}


def apply_for_waiver(conn: db.Connection, entity: dict, applications: dict) -> dict:
    from backend.kernel.metadata import get_meta, sync_meta

    person = require_context()
    if person.readonly or person.role not in {"admin", "modeler"}:
        raise ApiError("PERMISSION_DENIED")
    if entity["status"] not in {"draft", "returned"}:
        raise ApiError("PRECONDITION_FAILED", "请在草稿中填写申请说明后提交审核。")
    applications = _application_reasons(applications)
    from backend.domains.reviews import record_editor

    record_editor(entity, person.user_id)
    conn.execute(
        "UPDATE entities SET payload=? WHERE id=?", (json.dumps(entity["payload"], ensure_ascii=False), entity["id"])
    )
    result = check_entity(conn, entity)
    current = {item["code"]: item for item in result["checks"]}
    if any(current[code]["status"] != "fail" for code in applications):
        raise ApiError("VALIDATION_FAILED", "只需为尚未通过的出处或条件项申请豁免。")
    meta = get_meta(conn, entity["id"]) or sync_meta(conn, entity)
    stored = _object(meta.get("gates"))
    stored["applications"] = {}
    fingerprint = _fingerprint(entity, meta, conn)
    for code, reason in applications.items():
        stored["applications"][code] = {
            "reason": reason,
            "by": person.user_id,
            "name": person.username,
            "at": db.now(),
            "fingerprint": fingerprint,
            "version": entity["version"],
        }
        db.log(conn, entity["id"], "申请发布检查豁免", f"{LABELS[int(code[-1]) - 1]}：{reason}", person.username)
    if not applications:
        db.log(conn, entity["id"], "申请发布检查豁免", "撤回本次豁免申请。", person.username)
    stored.pop("denials", None)
    conn.execute(
        "UPDATE entity_meta SET gates=? WHERE entity_id=?", (json.dumps(stored, ensure_ascii=False), entity["id"])
    )
    return check_entity(conn, entity)


def deny_applications(conn: db.Connection, entity: dict, reasons: dict) -> None:
    from backend.domains.reviews import ensure_independent
    from backend.kernel.metadata import get_meta

    ensure_independent(entity)
    reasons = _application_reasons(reasons)
    checks = {item["code"]: item for item in check_entity(conn, entity)["checks"]}
    meta = get_meta(conn, entity["id"]) or {}
    stored = _object(meta.get("gates"))
    for code, reason in reasons.items():
        if not checks[code].get("application_reason"):
            raise ApiError("PRECONDITION_FAILED", "这项申请不存在或内容已变更，请刷新后重新核对。")
        stored.setdefault("denials", {})[code] = {
            "reason": reason,
            "fingerprint": _fingerprint(entity, meta, conn),
            "by": require_context().user_id,
            "at": db.now(),
        }
        stored.setdefault("waivers", {}).pop(code, None)
        db.log(
            conn, entity["id"], "拒绝发布检查豁免", f"{LABELS[int(code[-1]) - 1]}：{reason}", require_context().username
        )
    conn.execute(
        "UPDATE entity_meta SET gates=? WHERE entity_id=?", (json.dumps(stored, ensure_ascii=False), entity["id"])
    )
