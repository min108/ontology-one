"""Business cards from already-authorized data; no model or persistence."""

import re

from backend.core.terms import TERMS
from backend.relation_semantics import RELATION_SEMANTICS


def plain(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "；".join(filter(None, (plain(item) for item in value)))
    if isinstance(value, dict):
        return plain(
            next(
                (
                    value[key]
                    for key in ("content", "text", "description", "label", "name", "title", "value")
                    if key in value
                ),
                "",
            )
        )
    return ""


def build_card(entity: dict) -> dict:
    payload = entity.get("payload") or {}
    kind = entity.get("kind", "")
    definition = plain(payload.get("definition"))
    summary = plain(entity.get("summary") or payload.get("content") or payload.get("rule_text"))
    first = re.split(r"(?<=[。！？])|\n", summary, maxsplit=1)[0]
    fields = {
        "rule": (
            ("scope", TERMS["labels"]["r2_card_field_scope"]),
            ("conditions", TERMS["labels"]["r2_card_field_conditions"]),
            ("obligation", TERMS["labels"]["r2_card_field_obligation"]),
            ("rule_text", TERMS["labels"]["r2_card_field_rule_text"]),
            ("exceptions", TERMS["labels"]["r2_card_field_exceptions"]),
            ("effective_from", TERMS["labels"]["r2_card_field_effective_from"]),
        ),
        "data": (
            ("definition", TERMS["labels"]["r2_card_field_definition"]),
            ("unit", TERMS["labels"]["r2_card_field_unit"]),
            ("data_type", TERMS["labels"]["r2_card_field_data_type"]),
            ("constraints", TERMS["labels"]["r2_card_field_constraints"]),
            ("standard_basis", TERMS["labels"]["r2_card_field_standard_basis"]),
        ),
    }.get(
        kind,
        (
            ("scenario", TERMS["labels"]["r2_card_field_scenario"]),
            ("inputs", TERMS["labels"]["r2_card_field_inputs"]),
            ("outputs", TERMS["labels"]["r2_card_field_outputs"]),
            ("boundary", TERMS["labels"]["r2_card_field_boundary"]),
            ("content", TERMS["labels"]["r2_card_field_content"]),
        ),
    )
    points = [label + "：" + plain(payload.get(key))[:500] for key, label in fields if plain(payload.get(key))][:6]
    if not points and summary:
        points = [summary[:800]]
    sentences = []
    grouped: dict[str, list[str]] = {}
    for direction in ("outgoing", "incoming"):
        for relation in entity.get("relations", {}).get(direction, []):
            if relation.get("status") not in ("confirmed", "source_verified", "demo"):
                continue
            predicate = relation.get("type", "")
            forward, _, _, family = RELATION_SEMANTICS.get(
                predicate, (relation.get("label", predicate), "", False, "other")
            )
            sentence = f"{relation.get('source_title', '')} {forward} {relation.get('target_title', '')}".strip()
            grouped.setdefault(family, []).append(sentence)
    for group in grouped.values():
        sentences.extend(group[:3])
        if len(group) > 3:
            sentences.append(TERMS["labels"]["r2_card_more"].format(count=len(group) - 3))
    usage = TERMS["labels"].get("r2_card_use_" + kind, TERMS["labels"]["r2_card_use_generic"])
    one_liner = (definition or first or TERMS["labels"]["r2_card_no_definition"])[:1000]
    return {
        "one_liner": one_liner,
        "points": points,
        "relations": sentences,
        "usage": usage,
        "text": "\n".join([entity.get("title", ""), one_liner, *points, *sentences, usage])[:1200],
    }
