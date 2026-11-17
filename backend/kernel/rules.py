"""Pure, deterministic rule/metric test execution; no ledger queries or model calls."""

from collections.abc import Sequence
from copy import deepcopy
from datetime import date, datetime

from backend.kernel import oxl

OUTCOMES = {
    "not_applicable": "不适用",
    "pass": "符合",
    "violate": "不符",
    "needs_review": "待核实",
}
VERIFICATION_NOTICE = "需结合原始凭证由人员核实，不构成责任认定"


def _payload(entity: dict) -> dict:
    payload = entity.get("payload", entity)
    if not isinstance(payload, dict):
        raise oxl.OXLError("rule", "内容格式不正确。")
    return payload


def _expression(
    value: object,
    properties: oxl.PropertyDeclarations,
    relations: oxl.RelationDeclarations | None,
) -> oxl.Expression:
    if not isinstance(value, dict):
        raise oxl.OXLError("syntax", "请补齐判定式或计算式。")
    return oxl.parse(value.get("oxl"), properties=properties, relations=relations)


def prepare_payload(
    kind: str,
    payload: dict,
    *,
    properties: oxl.PropertyDeclarations = (),
    relations: oxl.RelationDeclarations | None = None,
) -> dict:
    """Save-time validation plus missing sentences; never mutate the caller's data.

    Missing test cases are a gate failure, not a draft-save error. Every supplied
    expression is parsed against the authorized object schema even in a draft.
    """
    result = deepcopy(payload)
    expressions = []
    if kind == "rule" and result.get("judgement") is not None:
        judgement = result["judgement"]
        if not isinstance(judgement, dict):
            raise oxl.OXLError("rule", "规则的核对条件格式不正确。")
        expressions = [judgement.get("condition"), judgement.get("requirement")]
        exceptions = judgement.get("exceptions", [])
        if not isinstance(exceptions, list):
            raise oxl.OXLError("rule", "例外条件需要填写为清单。")
        expressions.extend(exceptions)
    elif kind == "metric" and result.get("formula") is not None:
        expressions = [result["formula"]]
    # A generator of property declarations must also work for multiple expressions.
    properties = oxl.property_map(properties)
    for value in expressions:
        parsed = _expression(value, properties, relations)
        if not isinstance(value.get("sentence"), str) or not value["sentence"].strip():
            value["sentence"] = oxl.to_sentence(parsed)
    return result


def _truth(value: object) -> bool | None:
    if value is not None and type(value) is not bool:
        raise oxl.OXLError("type", "规则条件和要求的结果必须是是、否或待核实。")
    return value


def evaluate(
    rule: dict,
    objects: Sequence[dict],
    as_of: str | date | datetime | None = None,
    *,
    properties: oxl.PropertyDeclarations = (),
    relations: oxl.RelationDeclarations | None = None,
) -> list[dict]:
    """Evaluate supplied rows only, preserving explicit object/source references.

    Rows may be flat property mappings, or wrappers with `input`, `object_ref`
    and optional `related` authorized records. No database access is performed.
    """
    payload = _payload(rule)
    judgement = payload.get("judgement")
    if not isinstance(judgement, dict):
        raise oxl.OXLError("rule", "说明型规则尚未填写核对条件。")
    properties = oxl.property_map(properties)
    condition = _expression(judgement.get("condition"), properties, relations)
    requirement = _expression(judgement.get("requirement"), properties, relations)
    exceptions = judgement.get("exceptions", [])
    if not isinstance(exceptions, list):
        raise oxl.OXLError("rule", "例外条件需要填写为清单。")
    exceptions = [_expression(item, properties, relations) for item in exceptions]
    expressions = [condition, requirement, *exceptions]
    references = sorted(set().union(*(item.references for item in expressions)))
    link_references = set().union(*(item.relation_references for item in expressions))
    day = oxl.as_of_date(as_of).isoformat()
    meta = rule.get("_meta") or rule.get("meta") or payload.get("_meta") or {}
    results = []
    for index, obj in enumerate(objects):
        if not isinstance(obj, dict):
            raise oxl.OXLError("value", "核对对象需要提供属性记录。")
        values = obj.get("input", obj)
        if not isinstance(values, dict):
            raise oxl.OXLError("value", "核对对象需要提供属性记录。")
        related = obj.get("related")
        related = {} if related is None else related
        if not isinstance(related, dict):
            raise oxl.OXLError("value", "关联对象需要按关系提供记录清单。")
        for link, _prop in link_references:
            rows = related.get(link)
            if rows is not None and (type(rows) is not list or any(type(row) is not dict for row in rows)):
                raise oxl.OXLError("value", "关联对象需要提供记录清单。")
        condition_value = _truth(oxl.evaluate(condition, values, related=related, as_of=day))
        exception_values = []
        for item in exceptions:
            # A false condition or a confirmed exception already determines the
            # outcome; irrelevant branches must not cause arithmetic failures.
            if condition_value is False or True in exception_values:
                exception_values.append(None)
            else:
                exception_values.append(_truth(oxl.evaluate(item, values, related=related, as_of=day)))
        requirement_value = None
        if condition_value is False:
            outcome, reason = "not_applicable", "未满足适用条件。"
        elif any(value is True for value in exception_values):
            outcome, reason = "not_applicable", "符合例外条件。"
        elif condition_value is None:
            outcome, reason = "needs_review", "适用条件尚有信息需要核实。"
        elif any(value is None for value in exception_values):
            # An unknown exemption must not lead to a false allegation of breach.
            outcome, reason = "needs_review", "例外条件尚有信息需要核实。"
        else:
            requirement_value = _truth(oxl.evaluate(requirement, values, related=related, as_of=day))
            outcome = "needs_review" if requirement_value is None else "pass" if requirement_value else "violate"
            reason = {
                "needs_review": "要求是否满足尚有信息需要核实。",
                "pass": "符合规则要求。",
                "violate": "与规则要求不一致。",
            }[outcome]
        used = {key: values.get(key) for key in references}
        missing = [key for key, value in used.items() if value is None]
        related_values = {}
        for link, prop in sorted(link_references, key=lambda item: (item[0], item[1] or "")):
            rows = related.get(link)
            key = f"{link}.{prop}" if prop else link
            related_values[key] = None if rows is None else [row.get(prop) for row in rows] if prop else len(rows)
            if rows is None or (prop is not None and any(row.get(prop) is None for row in rows)):
                missing.append(key)
        results.append(
            {
                "outcome": outcome,
                "label": OUTCOMES[outcome],
                "reason": reason,
                "notice": VERIFICATION_NOTICE if outcome == "violate" else "",
                "values": used,
                "related_values": related_values,
                "missing_properties": missing,
                "missing_property_names": [properties.get(key, {}).get("name") or key for key in missing],
                "object_ref": deepcopy(obj.get("object_ref") or {"index": index}),
                "rule_id": rule.get("id"),
                "rule_version": rule.get("version", meta.get("version")),
                "valid_from": meta.get("valid_from"),
                "valid_to": meta.get("valid_to"),
                "as_of": day,
                "condition": condition_value,
                "requirement": requirement_value,
                "exceptions": exception_values,
            }
        )
    return results


def run_test_cases(
    kind: str,
    payload: dict,
    *,
    properties: oxl.PropertyDeclarations = (),
    relations: oxl.RelationDeclarations | None = None,
    as_of: str | date | datetime | None = None,
) -> dict:
    """Return gate-ready evidence; at least two valid cases must all pass.

    Result: passed/total/passed_count/results/errors. Each case result carries
    name, expected, actual, passed, details (rule outcome), error and error_code.
    Errors are business-facing; input values are never executed as Python.
    """
    payload = _payload(payload)
    report = {"passed": False, "total": 0, "passed_count": 0, "results": [], "errors": []}
    properties = oxl.property_map(properties)
    try:
        as_of = oxl.as_of_date(as_of).isoformat()
        if kind == "rule":
            judgement = payload.get("judgement")
            if not isinstance(judgement, dict):
                raise oxl.OXLError("rule", "说明型规则尚未填写核对条件。")
            cases = judgement.get("test_cases", [])
            # Validate expressions even when no cases have been supplied.
            prepare_payload(kind, payload, properties=properties, relations=relations)
            formula = None
        elif kind == "metric":
            cases = payload.get("test_cases", [])
            formula = _expression(payload.get("formula"), properties, relations)
        else:
            raise oxl.OXLError("kind", "只有规则和指标口径支持运行测试用例。")
        if not isinstance(cases, list):
            raise oxl.OXLError("cases", "测试用例需要填写为清单。")
        report["total"] = len(cases)
        if len(cases) < 2:
            report["errors"].append("至少需要 2 个测试用例且全部通过。")
        for index, case in enumerate(cases):
            result = {
                "name": case.get("name", f"用例 {index + 1}") if isinstance(case, dict) else f"用例 {index + 1}",
                "expected": case.get("expected") if isinstance(case, dict) else None,
                "actual": None,
                "passed": False,
                "details": None,
                "error": None,
                "error_code": None,
            }
            try:
                if not isinstance(case, dict) or not isinstance(case.get("input"), dict) or "expected" not in case:
                    raise oxl.OXLError("cases", "每个测试用例都需要填写输入和预期结果。")
                if kind == "rule":
                    if not isinstance(case["expected"], str) or case["expected"] not in OUTCOMES:
                        raise oxl.OXLError("cases", "规则用例的预期结果必须是四种核对结论之一。")
                    details = evaluate({"payload": payload}, [case], as_of, properties=properties, relations=relations)[
                        0
                    ]
                    result["details"] = details
                    actual = details["outcome"]
                else:
                    actual = oxl.evaluate(formula, case["input"], related=case.get("related"), as_of=as_of)
                result["actual"] = actual
                expected = case["expected"]
                same_type = type(actual) is type(expected) or (
                    type(actual) in (int, float) and type(expected) in (int, float)
                )
                result["passed"] = same_type and actual == expected
            except oxl.OXLError as error:
                result["error"], result["error_code"] = error.message, error.code
            report["results"].append(result)
        report["passed_count"] = sum(result["passed"] for result in report["results"])
        report["passed"] = len(cases) >= 2 and report["passed_count"] == len(cases)
    except oxl.OXLError as error:
        report["errors"].append(error.message)
    return report
