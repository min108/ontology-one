"""Context-aware dictionary retrieval. Ranking is a proposal, never a confirmed mapping."""
from __future__ import annotations

import math
import re
from collections import Counter

SCENARIOS = {
    "业务招待": ("招待", "接待", "宴请", "陪餐", "来宾"),
    "差旅": ("差旅", "出差", "交通费", "住宿费"),
    "会议": ("会议费", "会议报销", "会议日期", "参会"),
    "培训": ("培训", "学员", "讲课费"),
    "办公": ("办公费", "办公用品"),
    "采购": ("采购", "投标", "招标", "供应商", "保证金"),
    "出国境": ("出国", "出境", "境外旅费"),
}
SYNONYMS = (
    ("单号", "单编号"), ("流水号", "流水编号"), ("账号", "账户"),
    ("接待", "招待"), ("实付", "实际支付"), ("到账", "收款"),
    ("截止", "期限"), ("验收通过", "项目验收"), ("报支", "报销"),
    ("单据号码", "单据编号"), ("编码", "编号"), ("标识码", "编号"),
)


def normalized(text):
    text = re.sub(r"[\s_（）()·:：/\\-]", "", str(text).lower())
    for a, b in SYNONYMS:
        text = text.replace(a, b)
    return text


def positive_definition(item):
    p = item.get("payload", {})
    return re.split(r"不含|不包括|区别于|不代表|而非|不得以", str(p.get("definition") or item.get("summary") or ""), 1)[0]


def field_kind(text):
    text = normalized(text)
    if any(w in text for w in ("账户", "专户")) and not any(w in text for w in ("名称", "户名", "余额", "金额")):
        return "account"
    if any(w in text for w in ("编号", "代码", "证件号", "税号", "凭证号", "单据号")):
        return "identifier"
    if any(w in text for w in ("金额", "总额", "余额", "单价", "费用", "收入额", "支出额")):
        return "amount"
    if any(w in text for w in ("日期", "时间", "期限", "年月")):
        return "date"
    if any(w in text for w in ("人数", "数量", "天数", "次数")):
        return "count"
    if any(w in text for w in ("名称", "姓名", "户名")):
        return "name"
    if any(w in text for w in ("状态", "结果", "类型", "类别")):
        return "category"
    return "text"


def object_kind(text):
    text = normalized(text)
    for name, words in (
        ("报销审批单", ("报销审批单",)), ("出差审批单", ("出差审批单",)),
        ("报销单", ("报销单", "报销单据")), ("采购订单", ("采购订单", "订单编号")),
        ("发票", ("发票",)), ("合同", ("合同",)), ("付款", ("付款单", "付款编号", "支付单")),
        ("银行流水", ("流水编号", "交易流水")), ("项目任务", ("项目任务",)),
        ("问题", ("问题编号",)), ("项目", ("项目编号",)),
    ):
        if any(word in text for word in words):
            return name
    return ""


def scenarios(text):
    return {name for name, words in SCENARIOS.items() if any(w in str(text) for w in words)}


def source_context(mapping, sheet, entity=None):
    source = mapping.get("payload", {}).get("source_column") or mapping["column_name"]
    headers = [c["name"] for c in sheet.get("columns", [])]
    explicit = scenarios(source)
    sheet_context = scenarios(str(sheet.get("name", "")) + " " + str(sheet.get("grain", "")))
    neighbors = Counter(s for h in headers if h != source for s in scenarios(h))
    observed = set(sheet.get("observed_expense_scenarios") or [])
    context = explicit or (sheet_context | observed) or {k for k, n in neighbors.items() if n >= 2}
    if not context:
        context = scenarios(str((entity or {}).get("title", "")))
    return {"source_header": source, "field_kind": field_kind(source), "object_kind": object_kind(source),
            "scenarios": sorted(context), "scenario_basis": "原字段" if explicit else "工作表与一行含义" if sheet_context else "邻近表头或台账名称",
            "observed_expense_scenarios": sorted(observed), "all_headers": headers, "record_grain": sheet.get("grain") or "尚未确认", "grain_confirmed": bool(sheet.get("grain_confirmed"))}


def semantic_checks(mapping, standard, sheet, entity=None):
    ctx = source_context(mapping, sheet, entity)
    target = standard["title"]
    source_kind, target_kind = ctx["field_kind"], field_kind(target)
    source_obj, target_obj = ctx["object_kind"], object_kind(target)
    target_scenarios = scenarios(target + " " + str(standard.get("payload", {}).get("subcategory", "")) + " " + positive_definition(standard))
    errors, warnings = [], []
    if source_kind != "text" and target_kind != "text" and source_kind != target_kind:
        errors.append("字段含义冲突：标识、名称、日期、金额、账户等不同类型不能直接对应")
    if source_kind == "identifier" and source_obj and target_obj and source_obj != target_obj:
        errors.append(f"业务对象冲突：{source_obj}与{target_obj}不是同一份业务单据或记录")
    # Procurement can coexist with office expenses; expense subtypes, however, are not interchangeable.
    expense = {"业务招待", "差旅", "会议", "培训", "出国境", "办公"}
    source_expense, target_expense = set(ctx["scenarios"]) & expense, target_scenarios & expense
    if source_expense and target_expense and source_expense.isdisjoint(target_expense):
        errors.append("费用场景冲突：" + "、".join(sorted(source_expense)) + "与" + "、".join(sorted(target_expense)) + "须分别核对")
    if set(ctx["scenarios"]) == {"采购"} and target_expense and not source_expense:
        errors.append("业务场景冲突：采购工作表不能仅凭单号相似对应招待、差旅等报销字段")
    if len(source_expense) > 1 and target_expense and source_obj == "报销单":
        warnings.append("费用类别混合：同一工作表含多类费用，须拆分业务范围或增补适用的通用标准，不能整列归入单一费用类别")
    if target_expense and not source_expense and source_obj == "报销单":
        warnings.append("报销类别尚不明确：请补充是招待、差旅、会议还是其他费用，不能把通用报销单号直接限定为某一类别")
    if source_kind == "identifier" and not source_obj and target_obj and any(w in normalized(ctx["source_header"]) for w in ("单据", "编号")):
        warnings.append("单据对象尚不明确：须区分报销单、审批单、付款单、合同或其他业务记录")
    return {"errors": errors, "warnings": warnings, "context": ctx}


def _terms(text):
    text = normalized(text)
    out = set(re.findall(r"[a-z0-9]{2,}", text))
    for part in re.findall(r"[\u4e00-\u9fff]+", text):
        for size in (2, 3, 4):
            out.update(part[i:i+size] for i in range(max(0, len(part)-size+1)))
    return out


def rank_candidates(standards, mapping, sheet, entity=None, query="", limit=24):
    """Scan the complete current dictionary, combine meaning and local context, then shortlist."""
    ctx = source_context(mapping, sheet, entity)
    source = ctx["source_header"]
    terms = _terms(source)
    query_terms = _terms(query)
    prepared = []
    for item in standards:
        p = item.get("payload", {})
        aliases = " ".join(str(x) for x in p.get("aliases", []) if isinstance(x, str))
        title = normalized(item["title"] + " " + aliases)
        definition = normalized(positive_definition(item))
        prepared.append((item, title, definition))
    frequencies = {t: sum(t in title for _, title, _ in prepared) for t in terms | query_terms}
    ranked = []
    for item, title, definition in prepared:
        p = item.get("payload", {})
        matched = [t for t in terms if t in title]
        score = sum((5 if t in title else 1.3 if t in definition else 0) * min(len(t), 4) * min(5, 1 + math.log(1 + len(standards) / max(1, frequencies[t]))) for t in terms)
        if query_terms:
            query_score = sum((7 if t in title else 1.5 if t in definition else 0) * min(len(t), 4) for t in query_terms)
            id_match = bool(query.strip()) and query.strip().lower() in item["id"].lower()
            score += query_score + (1800 if id_match else 0)
            if query_score == 0 and not id_match:
                continue
        kind = field_kind(item["title"])
        obj = object_kind(item["title"])
        reasons = []
        if normalized(source) == normalized(item["title"]) or normalized(source) in [normalized(a) for a in p.get("aliases", []) if isinstance(a, str)]:
            score += 320
            reasons.append("名称或现有别名一致")
        elif matched:
            reasons.append("已合并单号/编号、账号/账户等同义表达")
        if kind == ctx["field_kind"] and kind != "text":
            score += 90
            reasons.append("字段类型一致")
        elif kind != "text" and ctx["field_kind"] != "text":
            score -= 260
        if obj and obj == ctx["object_kind"]:
            score += 190
            reasons.append("业务对象一致")
        elif obj and ctx["object_kind"]:
            score -= 280
        target_scenarios = scenarios(item["title"] + " " + str(p.get("subcategory", "")) + " " + positive_definition(item))
        common = set(ctx["scenarios"]) & target_scenarios
        if common:
            score += 120 * len(common)
            reasons.append("表内语境支持：" + "、".join(sorted(common)))
        checks = semantic_checks(mapping, item, sheet, entity)
        score -= 400 * len(checks["errors"])
        if item["id"] == mapping.get("standard_id"):
            score += 12
            reasons.append("原有对应候选，仍须复核")
        # Type alone is insufficient; keep only lexical, object or explicit supplemental-search evidence.
        if not matched and not (obj and obj == ctx["object_kind"]) and not (common and kind == ctx["field_kind"] and kind == "identifier" and not ctx["object_kind"]) and not query_terms:
            continue
        if score <= 0:
            continue
        ranked.append(dict(item, retrieval={"score": round(score, 1), "reasons": reasons, "warnings": checks["warnings"], "conflicts": checks["errors"]}))
    ranked.sort(key=lambda x: (-x["retrieval"]["score"], x["id"]))
    return {"items": ranked[:limit], "total": len(ranked), "dictionary_count": len(standards), "context": ctx,
            "truncated": len(ranked) > limit}


def value_profile(values):
    """Return aggregate shape evidence only; no original values leave the local process."""
    strings = [str(v) for v in values if v is not None and str(v) != ""]
    lengths = [len(v) for v in strings]
    return {"sampled_rows": len(values), "nonempty": len(strings), "min_length": min(lengths, default=0),
            "max_length": max(lengths, default=0), "numeric_values": sum(bool(re.fullmatch(r"-?\d+(?:\.\d+)?", v)) for v in strings),
            "date_values": sum(bool(re.fullmatch(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:[ T].*)?", v)) for v in strings),
            "leading_zero_values": sum(bool(re.match(r"0\d", v)) for v in strings), "raw_values_included": False}
