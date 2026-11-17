"""Deterministic business cards built exclusively from already-authorized data."""

import json
import re

from backend import db
from backend.core.terms import TERMS
from backend.relation_semantics import relation_reading


def plain(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "；".join(filter(None, (plain(item) for item in value)))
    if isinstance(value, dict):
        return plain(
            next(
                (
                    value[key]
                    for key in ("sentence", "content", "text", "description", "label", "name", "title", "note", "value")
                    if value.get(key) is not None
                ),
                "",
            )
        )
    return ""


def _mapping(value: object) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return {}
    return value if isinstance(value, dict) else {}


def _kind_name(kind: str) -> str:
    # The registry owns all kind names; fallback supports pre-migration imports.
    try:
        from backend.kernel.kinds import get_kind

        registered = get_kind(kind)
        return registered.name if registered else "要素"
    except (ImportError, KeyError, ValueError):
        return TERMS["kinds"].get(kind, "要素")


def _points(entity: dict, payload: dict) -> list[str]:
    result = []

    def point(label: str, value: object) -> None:
        text = plain(value)
        if text:
            result.append(label + "：" + text[:500])

    kind = entity.get("kind")
    if kind == "rule":
        judgement = _mapping(payload.get("judgement"))
        applies = _mapping(judgement.get("applies_to"))
        point("适用范围", applies.get("subject_note") or payload.get("scope"))
        point("条件", judgement.get("condition") or payload.get("conditions"))
        point("要求", judgement.get("requirement") or payload.get("obligation") or payload.get("rule_text"))
        point("例外", judgement.get("exceptions") or payload.get("exceptions"))
        meta = entity.get("_meta") or entity.get("meta") or {}
        start, end = (
            meta.get("valid_from") or payload.get("effective_from"),
            meta.get("valid_to") or payload.get("effective_to"),
        )
        point("生效日期", start)
        point("失效日期", end)
    elif kind == "object_type":
        properties = payload.get("properties") or []
        names = {prop.get("key"): prop.get("name") or prop.get("key") for prop in properties}
        identity = payload.get("identity") or {}
        point(
            "唯一标识", identity.get("note") or "、".join(names.get(key, key) for key in identity.get("properties", []))
        )
        point("粒度", payload.get("grain"))
        point(
            "关键数据项",
            "；".join(
                plain(prop.get("name")) + ("（" + plain(prop["unit"]) + "）" if prop.get("unit") else "")
                for prop in properties[:6]
            ),
        )
        point("边界", payload.get("boundary"))
        if payload.get("is_interface"):
            point("用途", "集团通用对象接口")
    elif kind == "metric":
        point("计算口径", payload.get("formula"))
        point("单位", payload.get("unit"))
        point("粒度", payload.get("grain"))
        labels = {"sum": "求和", "avg": "平均", "max": "最大值", "min": "最小值", "count": "计数"}
        point("汇总方式", [labels.get(value, value) for value in payload.get("aggregations", [])])
        point("默认汇总", labels.get(payload.get("default_aggregation"), ""))
        point("使用边界", payload.get("caveats"))
    elif kind == "term":
        point("别名", payload.get("aliases"))
        point(
            "不要混淆",
            [
                plain(item.get("term")) + ("：" + plain(item.get("note")) if item.get("note") else "")
                for item in payload.get("not_to_confuse", [])
            ],
        )
        point("使用边界", payload.get("boundary"))
    elif kind == "process":
        point("起点", payload.get("trigger"))
        point("终点", payload.get("end_result"))
        scope = _mapping(payload.get("scope"))
        point("适用范围", scope.get("include"))
        point("不包括", scope.get("exclude"))
        steps, stages = payload.get("steps") or [], payload.get("stages") or []
        point("办理过程", f"{len(stages)} 个环节，{len(steps)} 个步骤")
        actors = list(dict.fromkeys(plain(actor) for step in steps for actor in step.get("actors", []) if plain(actor)))
        point("主要参与方", actors)
    elif kind == "data":
        for key, label in (
            ("definition", "含义"),
            ("unit", "单位"),
            ("data_type", "数据类型"),
            ("constraints", "取值口径"),
            ("standard_basis", "口径依据"),
        ):
            point(label, payload.get(key) or (payload.get("datatype") if key == "data_type" else None))
    elif kind == "knowledge":
        point("适用条件", payload.get("conditions") or payload.get("applicable_conditions") or payload.get("scenario"))
        point("使用边界", payload.get("boundary") or payload.get("limitations"))
        point("核查路径", payload.get("verification_path") or payload.get("steps"))
        point("经验要点", payload.get("content"))
    elif kind == "skill":
        point("输入", payload.get("inputs"))
        if isinstance(payload.get("steps"), list):
            point("步骤", f"{len(payload['steps'])} 个步骤")
        point("输出", payload.get("outputs"))
        point("使用边界", payload.get("boundary"))
    else:
        for key, label in (
            ("scenario", "适用场景"),
            ("inputs", "输入"),
            ("outputs", "输出"),
            ("boundary", "使用边界"),
            ("content", "内容"),
        ):
            point(label, payload.get(key))
    if not result and plain(entity.get("summary")):
        result = [plain(entity["summary"])[:800]]
    return result[:6]


def _sources(payload: dict) -> list[str]:
    result = []
    for ref in payload.get("source_refs", []) or []:
        if not isinstance(ref, dict):
            continue
        kind = ref.get("kind", "anchor")
        if kind == "dataset":
            text = " · ".join(
                filter(
                    None,
                    (
                        plain(ref.get("dataset_title") or ref.get("title")) or "台账",
                        plain(ref.get("sheet")),
                        plain(ref.get("column")),
                    ),
                )
            )
        elif kind == "anchor":
            title = plain(ref.get("material_title") or ref.get("title"))
            text = "《" + title + "》" if title else "原文材料"
            if ref.get("source_version"):
                text += "第 " + plain(ref["source_version"]) + " 版"
            locator = plain(ref.get("locator"))
            if locator:
                text += " · " + locator
        elif kind == "external":
            text = " · ".join(filter(None, (plain(ref.get("title")), plain(ref.get("note")))))
        elif kind == "statement":
            text = "陈述记录" + ("：" + plain(ref.get("quote")) if ref.get("quote") else "")
        elif kind == "element":
            text = plain(ref.get("title")) or "关联要素"
            if ref.get("version"):
                text += "第 " + plain(ref["version"]) + " 版"
        else:
            continue
        if text and text not in result:
            result.append(text)
    return result


def build_card(entity: dict) -> dict:
    """No database/model access: callers supply only data the reader may see."""
    payload = _mapping(entity.get("payload"))
    kind = entity.get("kind", "")
    summary = plain(entity.get("summary") or payload.get("content") or payload.get("rule_text"))
    first = re.split(r"(?<=[。！？])|\n", summary, maxsplit=1)[0]
    one_liner = (plain(payload.get("definition")) or first or TERMS["labels"]["r2_card_no_definition"])[:1000]
    points = _points(entity, payload)
    grouped, governing, seen = {}, [], set()
    for direction in ("outgoing", "incoming"):
        for relation in entity.get("relations", {}).get(direction, []):
            if relation.get("status") not in ("confirmed", "source_verified", "demo"):
                continue
            identity = relation.get("id") or (
                relation.get("source"),
                relation.get("target"),
                relation.get("type"),
                json.dumps(_mapping(relation.get("payload")), sort_keys=True),
            )
            if identity in seen:
                continue
            seen.add(identity)
            forward, _, _, family = relation_reading(
                relation.get("type", ""),
                relation.get("source_kind", kind if direction == "outgoing" else ""),
                relation.get("label", ""),
            )
            source = plain(relation.get("source_title")) or (
                plain(entity.get("title")) if direction == "outgoing" else "关联要素"
            )
            target = plain(relation.get("target_title")) or (
                plain(entity.get("title")) if direction == "incoming" else "关联要素"
            )
            sentence = f"{source} {forward} {target}"
            grouped.setdefault(family, []).append(sentence)
            if (
                direction == "incoming"
                and relation.get("type") in ("governs", "applies_to")
                and relation.get("source_kind") == "rule"
                and relation.get("source_status") == "published"
            ):
                rule = _mapping(relation.get("source_payload"))
                judgement = _mapping(rule.get("judgement"))
                condition = plain(judgement.get("condition") or rule.get("conditions"))
                requirement = plain(judgement.get("requirement") or rule.get("obligation") or rule.get("rule_text"))
                governing.append("：".join(filter(None, (source, "，".join(filter(None, (condition, requirement)))))))
    sentences = []
    for group in grouped.values():
        sentences.extend(group[:3])
        if len(group) > 3:
            sentences.append(TERMS["labels"]["r2_card_more"].format(count=len(group) - 3))
    sources = _sources(payload)
    usage = TERMS["labels"].get("r2_card_use_" + kind, TERMS["labels"]["r2_card_use_generic"])
    meta = entity.get("_meta") or entity.get("meta") or {}
    status = TERMS["statuses"]["element"].get(entity.get("status"), "")
    header = {
        "kind": _kind_name(kind),
        "title": plain(entity.get("title")),
        "id": entity.get("id", ""),
        "version": entity.get("version"),
        "status": status,
        "module": plain(meta.get("module_name")),
        "organization": plain(entity.get("org_name") or meta.get("org_name")),
    }
    heading = f"【{header['kind']}】{header['title']}"
    details = [
        plain(header["id"]),
        "第 " + str(header["version"]) + " 版" if header["version"] else "",
        status,
        header["module"],
        header["organization"],
    ]
    if any(details):
        heading += "（" + " · ".join(filter(None, details)) + "）"
    sections = [heading, "一句话：" + one_liner]
    for label, values in (("要点", points), ("关系", sentences), ("规矩", governing), ("出处", sources)):
        if values:
            sections.append(label + "：" + "；".join(values))
    sections.append("怎么用：" + usage)
    return {
        "header": header,
        "one_liner": one_liner,
        "points": points,
        "relations": sentences,
        "rules": governing,
        "sources": sources,
        "usage": usage,
        "text": "\n".join(sections)[:1200],
    }


def cache_card(conn: db.Connection, entity: dict) -> dict:
    """Mutation hook; caller owns its transaction and must ensure meta exists."""
    enriched = dict(entity)
    if "relations" not in enriched:
        base = (
            "SELECT r.*,s.title source_title,t.title target_title,s.kind source_kind,t.kind target_kind,"
            "s.status source_status,s.payload source_payload FROM relations r "
            "JOIN entities s ON s.id=r.source JOIN entities t ON t.id=r.target WHERE "
        )
        enriched["relations"] = {
            "outgoing": [dict(row) for row in conn.execute(base + "r.source=? ORDER BY r.id", (entity["id"],))],
            "incoming": [dict(row) for row in conn.execute(base + "r.target=? ORDER BY r.id", (entity["id"],))],
        }
    card = build_card(enriched)
    conn.execute("UPDATE entity_meta SET card_text=? WHERE entity_id=?", (card["text"], entity["id"]))
    return card
