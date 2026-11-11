"""Compatibility access to historical records; new analysis uses the agent runtime."""
import json
from fastapi import APIRouter, HTTPException
from .db import connect

router = APIRouter(tags=['historical applications'])


@router.get('/applications/scenarios')
def scenarios():
    raise HTTPException(410, '固定场景分析已升级，请进入本体应用创建只读智能体会话。')


@router.post('/applications/run')
def run_check():
    raise HTTPException(410, '固定场景运行接口已停用，请使用受控 Harness 本体应用。')


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
