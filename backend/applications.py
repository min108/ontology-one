"""Reproducible business checks over the actual imported rows.

Results describe data conditions and require business review.
"""
import json
import re
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.db import connect

router = APIRouter()

SCENARIOS = [
    {"id": "deadline", "title": "项目期限核对", "subtitle": "逐项核对要求完成日期、实际完成日期与当前任务状态", "domain": "项目管理", "icon": "clock", "description": "以选定基准日识别逾期未完成、完成时间晚于要求以及日期信息不足的事项。期限来源和延期审批仍需人工核实。", "fields": [
        {"key": "due_date", "label": "要求完成日期", "required": True, "suggestions": ["要求完成日期", "任务期限", "计划完成日期", "截止日期"]},
        {"key": "completed_date", "label": "实际完成日期", "required": False, "suggestions": ["实际完成日期", "完成日期"]},
        {"key": "status", "label": "任务状态", "required": False, "suggestions": ["任务状态", "状态"]},
        {"key": "record", "label": "事项名称或编号", "required": False, "suggestions": ["任务名称", "任务编号", "项目编号"]},
    ], "boundary": "核对的是台账记载的期限与完成时间；无法仅凭本结果确认交付质量。"},
    {"id": "deposit", "title": "采购付款账户关联", "subtitle": "同一项目，不同投标单位，是否使用同一付款账号", "domain": "招标采购", "icon": "network", "description": "在同一业务归属单位内，按项目与付款账户分组，排除同一投标单位的分笔付款。采购方收款专户不能替代投标人付款账号。", "fields": [
        {"key": "project", "label": "项目编号", "required": True, "suggestions": ["项目编号", "采购项目编号", "标段编号", "项目名称"]},
        {"key": "bidder", "label": "投标单位", "required": True, "suggestions": ["投标单位", "投标人名称", "供应商名称", "供应商"]},
        {"key": "payer_account", "label": "投标人付款账号", "required": True, "suggestions": ["付款账号", "付款账户", "付款银行账号", "保证金付款账号"]},
    ], "boundary": "相同账户仅表示数据关联，需核对原始流水、账户主体及代付授权后解释业务含义。"},
    {"id": "evidence", "title": "交付材料齐备性检查", "subtitle": "检查完成记录是否具备佐证材料，定位需补充的事项", "domain": "项目验收", "icon": "files", "description": "检查材料记录是否为空，或是否包含本次选定的材料类别。这里只检查台账记录，不替代原件真实性与项目交付验收。", "fields": [
        {"key": "evidence", "label": "完成材料或材料目录", "required": True, "suggestions": ["完成材料", "交付材料", "材料目录", "验收材料", "证明材料"]},
        {"key": "status", "label": "任务状态", "required": False, "suggestions": ["任务状态", "状态"]},
        {"key": "record", "label": "事项名称或编号", "required": False, "suggestions": ["任务名称", "任务编号", "项目编号"]},
    ], "boundary": "材料目录完整不等于交付合格。仍需检查材料对应关系、实效及复核意见。"},
]


class RunRequest(BaseModel):
    scenario: str
    dataset_id: str
    fields: dict[str, str] = Field(default_factory=dict)
    as_of: str | None = None
    required_documents: list[str] = Field(default_factory=list)


def _text(value):
    return "" if value is None else str(value).strip()


def _missing(value):
    return _text(value).lower() in {"", "none", "null", "nan", "nat", "-", "—", "未提供", "无", "暂无", "待补充"}


def _date(value):
    if _missing(value):
        return None
    if isinstance(value, (int, float)):
        if 20000 <= value <= 100000:
            return date(1899, 12, 30) + timedelta(days=int(value))
        return None
    text = _text(value)
    text = re.sub(r"[年/.]", "-", text).replace("月", "-").replace("日", "")
    match = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if match:
        try:
            return date(*map(int, match.groups()))
        except ValueError:
            return None
    if re.fullmatch(r"\d{8}", text):
        try:
            return datetime.strptime(text, "%Y%m%d").date()
        except ValueError:
            return None
    return None


@router.get("/applications/scenarios")
def scenarios():
    return {"items": SCENARIOS}


@router.post("/applications/run")
def run_check(body: RunRequest):
    scenario = next((x for x in SCENARIOS if x["id"] == body.scenario), None)
    if scenario is None:
        raise HTTPException(422, "请选择受支持的业务分析场景")
    if body.as_of:
        try:
            as_of = date.fromisoformat(body.as_of)
        except ValueError:
            raise HTTPException(422, "基准日应为 YYYY-MM-DD 格式")
    else:
        as_of = datetime.now(timezone(timedelta(hours=8))).date()
    conn = connect()
    try:
        dataset = conn.execute("SELECT * FROM entities WHERE id=? AND kind='dataset'", (body.dataset_id,)).fetchone()
        if dataset is None:
            raise HTTPException(404, "未找到该单位数据台账")
        raw_rows = conn.execute("SELECT row_no, values_json, org_id FROM dataset_rows WHERE dataset_id=? ORDER BY row_no", (body.dataset_id,)).fetchall()
        if not raw_rows:
            raise HTTPException(422, "该台账还没有可分析的明细行，请先完成上传和解析")
        rows = []
        columns = set()
        for r in raw_rows:
            values = json.loads(r["values_json"])
            columns.update(values.keys())
            rows.append({"row_no": r["row_no"], "values": values, "org_id": r["org_id"]})
        for field in scenario["fields"]:
            col = body.fields.get(field["key"])
            if field["required"] and not col:
                raise HTTPException(422, f"请指定“{field['label']}”对应的原表字段")
            if col and col not in columns:
                raise HTTPException(422, f"原表不存在字段“{col}”，请重新选择")
        selected = [v for v in body.fields.values() if v]
        if len(selected) != len(set(selected)):
            raise HTTPException(422, "不同业务含义不能使用同一个原表字段，请重新核对字段对应关系")
        if body.scenario == "deposit" and re.search(r"收款|收取|接收|专户", body.fields.get("payer_account", "")):
            raise HTTPException(422, "付款同源核对需要投标人的付款账号，采购方收款专户不能作为付款来源")
        findings = []
        insufficient = []
        affected_rows = set()
        normal = 0

        def value(row, key):
            return row["values"].get(body.fields.get(key, ""))

        def item(row, title, explanation, severity="需核查", **extra):
            return {"title": title, "explanation": explanation, "severity": severity, "row_numbers": [row["row_no"]], "org_id": row["org_id"], "record": _text(value(row, "record")) or f"原表第 {row['row_no']} 行", "values": row["values"], **extra}

        if body.scenario == "deadline":
            for row in rows:
                due = _date(value(row, "due_date"))
                actual_raw = value(row, "completed_date")
                actual = _date(actual_raw)
                state = _text(value(row, "status"))
                if due is None:
                    insufficient.append(item(row, "要求完成日期缺失或无法识读", "缺少有效的要求完成日期，无法计算是否逾期。", "条件不足"))
                    continue
                if not _missing(actual_raw) and actual is None:
                    insufficient.append(item(row, "实际完成日期无法识读", "请核对日期格式及原始填报记录。", "条件不足"))
                    continue
                if not body.fields.get("completed_date"):
                    insufficient.append(item(row, "未指定实际完成日期字段", "仅有期限无法确定实际完成情况；请补充字段后重新运行。", "条件不足"))
                    continue
                if actual and actual > as_of:
                    insufficient.append(item(row, "完成日期晚于分析基准日", "当前基准日无法确认之后的完成事实，请核对基准日或原表记载。", "条件不足"))
                    continue
                if actual is None and state in {"已完成", "完成", "已验收", "已交付"}:
                    insufficient.append(item(row, "完成状态与日期记载不一致", "台账标记已完成或已验收，但缺少实际完成日期，需补充核实。", "条件不足"))
                    continue
                end = actual or as_of
                if end > due:
                    delay = (end - due).days
                    title = "完成时间晚于要求" if actual else "超过要求期限，尚无完成日期"
                    findings.append(item(row, title, f"要求 {due.isoformat()} 前完成；{'实际完成于 ' + actual.isoformat() if actual else '截至 ' + as_of.isoformat() + ' 未记载完成日期'}，相差 {delay} 天。请核验延期审批及实际进展。", days_overdue=delay))
                    affected_rows.add(row["row_no"])
                else:
                    normal += 1
        elif body.scenario == "deposit":
            groups = defaultdict(list)
            for row in rows:
                project, bidder, account = [re.sub(r"\s+", "", _text(value(row, k))) for k in ("project", "bidder", "payer_account")]
                if any(_missing(v) for v in (project, bidder, account)) or re.search(r"[*＊•●]|[xX]{3}", account):
                    insufficient.append(item(row, "付款主体、项目或完整账号不足", "缺失或脱敏账号不能支持可靠的同源比对，请核验授权调阅的原始流水。", "条件不足"))
                    continue
                account = re.sub(r"[-－]", "", account)
                groups[(row["org_id"] or dataset["org_id"], project, account)].append((row, bidder))
            for (business_org, project, account), group in groups.items():
                bidders = sorted({bidder for _, bidder in group})
                if len(bidders) > 1:
                    nums = [r["row_no"] for r, _ in group]
                    affected_rows.update(nums)
                    findings.append({"title": f"{project} · {len(bidders)} 家投标单位付款账号相同", "explanation": f"付款账号 {account} 对应 {'、'.join(bidders)}。已合并同一投标单位的分笔付款；请核实账户主体与代付关系。", "severity": "需核查", "row_numbers": nums, "record": project, "payer_account": account, "bidders": bidders, "values": group[0][0]["values"], "org_id": group[0][0]["org_id"]})
                else:
                    normal += len(group)
        else:
            required = [x.strip() for x in body.required_documents if x.strip()][:30]
            for row in rows:
                evidence = _text(value(row, "evidence"))
                missing = required if _missing(evidence) else [doc for doc in required if doc not in evidence]
                if _missing(evidence) or missing:
                    explanation = "未记载完成佐证材料。" if _missing(evidence) else "台账材料目录未检出：" + "、".join(missing) + "。"
                    findings.append(item(row, "需补充或核对佐证材料", explanation + "请核对原件、附件和台账遗漏情况；材料未记载不等于实际不存在。", missing_documents=missing))
                    affected_rows.add(row["row_no"])
                else:
                    normal += 1
        run_id = "CHECK-" + uuid.uuid4().hex[:12].upper()
        now = datetime.now(timezone.utc).isoformat()
        result = {
            "id": run_id, "scenario": scenario["id"], "scenario_title": scenario["title"], "dataset_id": body.dataset_id,
            "dataset_title": dataset["title"], "fields": body.fields, "as_of": as_of.isoformat(), "required_documents": body.required_documents,
            "total_rows": len(rows), "checked_rows": len(rows) - len(insufficient), "normal_rows": normal,
            "finding_count": len(findings), "affected_rows": len(affected_rows), "insufficient_rows": len(insufficient),
            "findings": findings[:200], "insufficient": insufficient[:100], "findings_truncated": len(findings) > 200,
            "insufficient_truncated": len(insufficient) > 100, "full_record_available": True,
            "boundary": scenario["boundary"], "record_type": "analysis_run", "created_at": now,
            "dataset_version": dataset["version"], "source_kind": dataset["source_kind"],
            "conclusion": "条件不足，暂不能形成完整筛查结果" if len(insufficient) == len(rows) else ("发现需进一步核查的记录" if findings else ("可核对记录未见本场景异常，部分记录仍需补充" if insufficient else "本次选定条件下未发现异常记录")),
        }
        title = f"{scenario['title']} · {dataset['title']}"
        summary = f"核对 {len(rows)} 行；发现 {len(findings)} 组线索，涉及 {len(affected_rows)} 行；{len(insufficient)} 行条件不足。"
        conn.execute("INSERT INTO entities(id,kind,title,summary,domain,org_id,status,source_kind,payload,created_at,updated_at,version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (run_id, "issue", title, summary, scenario["domain"], dataset["org_id"], "completed", dataset["source_kind"], json.dumps(result, ensure_ascii=False), now, now, 1))
        conn.execute("CREATE TABLE IF NOT EXISTS application_findings(run_id VARCHAR(191), category VARCHAR(100), sequence INTEGER, payload LONGTEXT, PRIMARY KEY(run_id,category,sequence)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4")
        conn.executemany("INSERT INTO application_findings(run_id,category,sequence,payload) VALUES(?,?,?,?)", [(run_id, category, i, json.dumps(finding, ensure_ascii=False)) for category, collection in [("findings", findings), ("insufficient", insufficient)] for i, finding in enumerate(collection)])
        conn.execute("INSERT INTO relations(id,source,target,type,label,status,payload) VALUES(?,?,?,?,?,?,?)", ("REL-" + uuid.uuid4().hex[:12], body.dataset_id, run_id, "supports", "支撑分析", "confirmed", json.dumps({"fields": body.fields, "dataset_version": dataset["version"], "as_of": as_of.isoformat()}, ensure_ascii=False)))
        conn.execute("INSERT INTO activities(entity_id,action,detail,actor,created_at) VALUES(?,?,?,?,?)", (run_id, "运行业务分析", summary, "本地业务人员", now))
        conn.commit()
        return result
    finally:
        conn.close()


@router.get("/applications/{run_id}/export")
def export_check(run_id: str):
    conn = connect()
    try:
        row = conn.execute("SELECT payload FROM entities WHERE id=? AND kind='issue'", (run_id,)).fetchone()
        if not row:
            raise HTTPException(404, "未找到该次分析记录")
        result = json.loads(row["payload"])
        if result.get("record_type") != "analysis_run":
            raise HTTPException(422, "该事项不是业务分析记录")
        if conn.execute("SELECT 1 FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name='application_findings'").fetchone():
            full = conn.execute("SELECT category,payload FROM application_findings WHERE run_id=? ORDER BY category,sequence", (run_id,)).fetchall()
            if full:
                result["findings"] = [json.loads(r["payload"]) for r in full if r["category"] == "findings"]
                result["insufficient"] = [json.loads(r["payload"]) for r in full if r["category"] == "insufficient"]
                result["findings_truncated"] = False
                result["insufficient_truncated"] = False
        return result
    finally:
        conn.close()
