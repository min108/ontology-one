"""Real-provider rehearsal; replies and exported files remain in memory only."""

from __future__ import annotations

import io
import json
import os
import time
import zipfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
TEXT = (
    "第一条 办理付款前应核对合同约定与验收资料；验收通过后30日内完成付款申请。\n"
    "第二条 累计付款不得超过合同金额；有异议的款项应记录原因并暂停支付。"
)


def request(client: httpx.Client, method: str, path: str, **kwargs: object) -> dict:
    response = client.request(method, path, **kwargs)
    if response.status_code >= 400:
        raise RuntimeError("request_failed_" + str(response.status_code))
    return response.json()


def login(client: httpx.Client, role: str) -> None:
    account = json.loads((ROOT / "tests/fixtures/v070/manifest.json").read_text())["browser"]["accounts"][role]
    result = request(client, "POST", "/api/auth/login", json={key: account[key] for key in ("username", "password")})
    client.headers.update({"X-CSRF-Token": result["csrf_token"], "X-Workspace-Id": result["workspace"]["tenant_id"]})


def invoke(client: httpx.Client, ident: str, body: dict) -> dict:
    return request(client, "POST", "/api/v2/capabilities/" + ident + ":invoke", json=body)["data"]


def wait(client: httpx.Client, path: str, wrapped: bool = False) -> dict:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        result = request(client, "GET", path)
        if wrapped:
            result = result["data"]
        if result["status"] in {"completed", "partial"}:
            return result
        if result["status"] in {"failed", "cancelled"}:
            raise RuntimeError("run_failed_" + str((result.get("error") or {}).get("code", "unknown")))
        time.sleep(0.5)
    raise RuntimeError("run_timeout")


def main() -> None:
    output = Path(os.environ["E2E_EVIDENCE_DIR"])
    result: dict = {"provider": "real_deepseek", "steps": [], "screenshots": 0}
    start = previous = time.monotonic()
    stage = "交进来"

    def record() -> None:
        nonlocal previous
        current = time.monotonic()
        result["steps"].append({"step": stage, "seconds": round(current - previous, 1), "passed": True})
        previous = current
        print(stage + "：通过", flush=True)

    try:
        with (
            httpx.Client(base_url=os.environ["E2E_BASE_URL"], trust_env=False, timeout=120) as builder,
            httpx.Client(base_url=os.environ["E2E_BASE_URL"], trust_env=False, timeout=120) as reviewer,
        ):
            login(builder, "modeler")
            login(reviewer, "reviewer")
            material = request(
                builder,
                "POST",
                "/api/materials/upload",
                files={"file": ("【演示】付款核对制度.txt", TEXT.encode(), "text/plain")},
            )
            recipe = invoke(builder, "recipe.start", {"material_id": material["id"]})
            run = wait(builder, "/api/v2/recipes/" + recipe["id"], True)
            if not run["used_model"]:
                raise RuntimeError("model_extraction_unavailable")
            record()
            stage = "看一眼"
            proposal = invoke(builder, "changeset.get", {"changeset_id": run["changeset_id"]})
            candidates = [
                item
                for item in proposal["items"]
                if item["content_origin"] != "local" and item["evidence"] and item["validation"]["level"] != "error"
            ]
            chosen = next((item for item in candidates if any("30" in ref["quote"] for ref in item["evidence"])), None)
            if chosen is None:
                raise RuntimeError("model_candidate_with_exact_quote_missing")
            invoke(
                builder,
                "changeset.decide_items",
                {"changeset_id": proposal["id"], "item_ids": [chosen["id"]], "decision": "accepted"},
            )
            applied = invoke(builder, "changeset.apply_and_submit", {"changeset_id": proposal["id"]})
            result["model_candidates"] = len(candidates)
            result["local_candidates"] = sum(item["content_origin"] == "local" for item in proposal["items"])
            record()
            stage = "点通过"
            ident = applied["entity_ids"][0]
            reviewed = request(
                reviewer, "POST", f"/api/v2/reviews/{ident}:decide", json={"decision": "approve_publish", "version": 2}
            )
            if reviewed["data"]["entity"]["status"] != "published":
                raise RuntimeError("publication_incomplete")
            record()
            stage = "问一个问题"
            session = request(builder, "POST", "/api/agent/sessions", json={"title": "【演示】付款制度报告"})
            turn = request(
                builder,
                "POST",
                f"/api/agent/sessions/{session['id']}/turns",
                json={
                    "message": "请读取选中制度的完整内容，说明付款前核对事项和验收通过后的付款申请时限。"
                    "用一段说明及一个两列表格回答；"
                    "数字须与原文一致，注明依据。",
                    "entity_ids": [ident],
                    "skill_ids": [],
                    "mapping_ids": [],
                    "scope_mode": "selected",
                },
            )
            answer = wait(builder, f"/api/agent/sessions/{session['id']}/runs/{turn['run_id']}")
            if not answer.get("answer") or not answer.get("provenance"):
                raise RuntimeError("answer_or_provenance_missing")
            result["answer_has_provenance"] = True
            record()
            stage = "出一份报告"
            report = request(
                builder, "POST", f"/api/agent/sessions/{session['id']}/runs/{turn['run_id']}/report", json={}
            )["data"]
            if report["source_kind"] != "analyst":
                raise RuntimeError("source_kind_mismatch")
            for format in ("html", "docx"):
                file = builder.get(f"/api/v2/artifacts/{report['id']}/versions/1/files/{format}")
                if file.status_code != 200:
                    raise RuntimeError("download_failed_" + format)
                if format == "docx":
                    with zipfile.ZipFile(io.BytesIO(file.content)) as archive:
                        if archive.testzip() is not None or "word/document.xml" not in archive.namelist():
                            raise RuntimeError("word_invalid")
                elif b"Content-Security-Policy" not in file.content:
                    raise RuntimeError("html_invalid")
            again = invoke(builder, "artifact.render", {"artifact_id": report["id"], "expected_version": 1})
            if again["current_version"] != 2:
                raise RuntimeError("regeneration_incomplete")
            record()
            result["status"] = "passed"
    except Exception as error:
        result.update(status="failed", failed_step=stage, error_type=type(error).__name__)
        if isinstance(error, RuntimeError):
            result["error_code"] = str(error)
        raise SystemExit("真实模型演练未通过；阶段：" + stage + "，错误类型：" + type(error).__name__) from None
    finally:
        result["total_seconds"] = round(time.monotonic() - start, 1)
        (output / "mainline.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
