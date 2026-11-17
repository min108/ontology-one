"""JSON Schema 2020-12 payload contracts plus local semantic invariants."""

import json
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator

from backend.kernel.kinds import get_kind


@lru_cache(maxsize=7)
def _validator(kind: str) -> Draft202012Validator:
    schema = json.loads((Path(__file__).parent / (kind + ".json")).read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _unique(items: list[dict], field: str, label: str) -> set[str]:
    values = [item[field] for item in items]
    if any(not value.strip() for value in values) or len(values) != len(set(values)):
        raise ValueError(label + "标识不能为空或重复。")
    return set(values)


def validate_payload(kind: str, payload: dict) -> None:
    """Validate a draft/save without requiring optional publication-gate fields.

    Old-kind payloads remain compatible. Cross-element reference authorization
    and expression validation are performed by the element service in its tenant
    transaction; this pure function checks only the local payload contract.
    """
    definition = get_kind(kind)
    if not isinstance(payload, dict):
        raise ValueError("要素内容必须为对象。")
    if definition.payload_schema is None:
        return
    error = next(_validator(definition.kind).iter_errors(payload), None)
    if error is not None:
        path = ".".join(str(part) for part in error.absolute_path)
        raise ValueError("要素内容格式无效" + ("（" + path + "）" if path else "") + "。")
    if kind == "object_type":
        props = payload.get("properties", [])
        keys = _unique(props, "key", "属性")
        references = set(payload.get("identity", {}).get("properties", []))
        if payload.get("title_property"):
            references.add(payload["title_property"])
        if not references <= keys:
            raise ValueError("对象标识与标题必须指向已有属性。")
        for prop in props:
            if not prop.get("data_id") and not prop.get("note", "").strip():
                raise ValueError("未引用数据项的属性须说明含义。")
    elif kind == "metric":
        if (
            "aggregations" in payload
            and payload.get("default_aggregation") is not None
            and payload["default_aggregation"] not in payload["aggregations"]
        ):
            raise ValueError("默认聚合方式必须在可用方式中。")
    elif kind == "process":
        stages = _unique(payload.get("stages", []), "key", "环节")
        steps = payload.get("steps", [])
        keys = _unique(steps, "key", "步骤")
        for step in steps:
            if step.get("stage") and step["stage"] not in stages:
                raise ValueError("办理步骤的环节不存在。")
            if not set(step.get("next", [])) <= keys:
                raise ValueError("后续办理步骤不存在。")
        for exception in payload.get("exceptions", []):
            if not set(exception.get("steps", [])) <= keys:
                raise ValueError("例外处理的办理步骤不存在。")
