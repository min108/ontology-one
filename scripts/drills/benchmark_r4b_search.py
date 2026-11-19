"""BA12. Run once inside the disposable browser fixture after S1 through S12 pass."""

import argparse
import hashlib
import json
import math
import os
import platform
import re
import subprocess
from contextlib import closing
from pathlib import Path
from time import perf_counter_ns
from urllib.parse import urlsplit

import httpx

from backend import db
from backend.kernel import elements
from backend.security import store
from backend.security.context import Principal, use_context

ROOT = Path(__file__).resolve().parents[2]
DOCUMENTS = 10_000
QUERIES = 200
QUERY = "10个工作日"
SUBJECTS = (
    "合同台账",
    "预付款登记",
    "验收记录",
    "收款单位",
    "工时核对",
    "采购计划",
    "预算说明",
    "项目备案",
    "物资编码",
    "归档清册",
    "调度安排",
    "费用分摊",
)
DESCRIPTIONS = (
    "经办部门登记数量并核实单位。",
    "复核人员按期校验记录。",
    "负责人依据签收凭证核对归属。",
    "按合同约定的交付日期登记差异。",
    "收到凭证后五个工作日内完成复核。",
    "每月最后一个工作日汇总并归档。",
)
KINDS = ("data", "knowledge", "term", "object_type")


def fixture_document(number: int) -> dict:
    kind = KINDS[number % len(KINDS)]
    subject = SUBJECTS[(number // len(KINDS)) % len(SUBJECTS)]
    summary = subject + "用于机构业务月度核对。" + DESCRIPTIONS[number % len(DESCRIPTIONS)]
    payload = {"definition": summary} if kind != "object_type" else {"properties": []}
    if kind in {"term", "object_type"}:
        payload["schema"] = kind + "/1"
    return {"kind": kind, "title": f"{subject} {number:05d}", "summary": summary, "payload": payload}


def count_documents(conn: db.Connection) -> dict[str, int]:
    return {
        row["doc_type"]: row["n"]
        for row in conn.execute("SELECT doc_type,COUNT(*) n FROM search_index GROUP BY doc_type")
    }


def prepare(person: Principal, evidence: dict) -> dict:
    with use_context(person), closing(db.connect()) as conn:
        before = count_documents(conn)
        existing = before.get("element", 0)
        if existing >= DOCUMENTS or before.get("chunk", 0) < 1:
            raise ValueError("The fixture must contain its confirmed source and fewer than 10,000 elements")
        checks = [(evidence["material_id"], "material"), (evidence["s1_rule_id"], "rule")]
        for ident, kind in checks:
            row = conn.execute("SELECT kind,status FROM entities WHERE id=?", (ident,)).fetchone()
            if not row or row["kind"] != kind or (kind == "rule" and row["status"] != "published"):
                raise ValueError("The source and published rule must exist in the measured institution")
        added = DOCUMENTS - existing
        for start in range(0, added, 100):
            with conn:
                for number in range(start, min(start + 100, added)):
                    elements.create_draft(conn, fixture_document(number))
        after = count_documents(conn)
        if after.get("element") != DOCUMENTS or after.get("chunk") != before.get("chunk"):
            raise AssertionError("Normal element saves must add exactly one searchable element each")
        composition = [
            dict(row)
            for row in conn.execute(
                "SELECT kind,status,COUNT(*) AS count FROM search_index WHERE doc_type='element' "
                "GROUP BY kind,status ORDER BY kind,status"
            )
        ]
        originals = list(dict.fromkeys(evidence["published_rule_ids"]))
        statuses = {
            row["id"]: row["status"]
            for row in conn.execute(
                "SELECT id,status FROM entities WHERE kind='rule' AND id IN (" + ",".join("?" for _ in originals) + ")",
                originals,
            )
        }
        if len(statuses) != len(originals):
            raise AssertionError("Each independently published source rule must remain in the institution")
        return {
            "before": before,
            "after": after,
            "by_kind_and_status": composition,
            "added_synthetic_drafts": added,
            "original_rules_current_status": statuses,
            "synthetic_subjects": len(SUBJECTS),
            "synthetic_descriptions": DESCRIPTIONS,
            "mysql_version": conn.execute("SELECT VERSION() AS v").fetchone()["v"],
        }


def measure(client: httpx.Client, rule_id: str) -> dict:
    samples = []
    for _ in range(QUERIES):
        start = perf_counter_ns()
        response = client.post("/api/v2/capabilities/semantic.search:invoke", json={"q": QUERY})
        elapsed = (perf_counter_ns() - start) / 1_000_000
        response.raise_for_status()
        if rule_id not in [hit["id"] for hit in response.json()["items"][:5]]:
            raise AssertionError(
                "The authenticated search must return the original published S1 rule within its first five hits"
            )
        samples.append(elapsed)
    ordered = sorted(samples)
    return {
        "query": QUERY,
        "requests": len(samples),
        "p95_ms": round(ordered[math.ceil(len(ordered) * 0.95) - 1], 3),
        "p50_ms": round(ordered[math.ceil(len(ordered) * 0.5) - 1], 3),
        "max_ms": round(ordered[-1], 3),
        "samples_ms": [round(value, 3) for value in samples],
    }


def run(evidence_path: Path, output: Path) -> dict:
    evidence = json.loads(evidence_path.read_text())
    base_url = os.environ["E2E_BASE_URL"]
    namespace = os.environ.get("MYSQL_DATABASE", "")
    if (
        urlsplit(base_url).hostname not in {"127.0.0.1", "localhost", "::1"}
        or not re.fullmatch(r"e2e_r1_[0-9a-f]{24}", namespace)
        or evidence["tenant_id"] != "sub-01"
        or evidence.get("success") is not True
        or evidence.get("passed") != [f"S{i}" for i in range(1, 13)]
        or evidence["s1_rule_id"] not in evidence["published_rule_ids"]
    ):
        raise ValueError("BA12 requires the completed, isolated R4 browser fixture")
    fixture = json.loads((ROOT / "tests/fixtures/v070/manifest.json").read_text())
    password = next(row["password"] for row in fixture["accounts"] if row["username"] == "fixture.builder")
    with output.open("x", encoding="utf-8") as stream:
        stream.write('{"status":"preparing"}\n')
    receipt = {
        "status": "preparing",
        "tenant": evidence["tenant_id"],
        "material_id": evidence["material_id"],
        "s1_rule_id": evidence["s1_rule_id"],
        "platform": platform.platform(),
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "source_sha256": hashlib.sha256(
            (ROOT / "docs/blueprint/rounds/r4a1-materials/regulation-plain.txt").read_bytes()
        ).hexdigest(),
    }
    try:
        with httpx.Client(base_url=base_url, trust_env=False, timeout=30.0) as client:
            response = client.post("/api/auth/login", json={"username": "b2.search.builder", "password": password})
            response.raise_for_status()
            session = response.json()
            person = store.authenticate_session(client.cookies[store.COOKIE_NAME], "ba12")[0]
            if person.tenant_id != evidence["tenant_id"] or person.role != "modeler":
                raise AssertionError("The measured session must be the actual builder of the browser fixture")
            client.headers.update({"X-CSRF-Token": session["csrf_token"], "X-Workspace-Id": person.tenant_id})
            receipt["corpus"] = prepare(person, evidence)
            receipt["status"] = "measuring"
            output.write_text(json.dumps(receipt, ensure_ascii=False) + "\n")
            receipt["measurement"] = measure(client, evidence["s1_rule_id"])
            receipt["status"] = "passed"
    finally:
        output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.evidence, args.output)
    print(
        json.dumps(
            {key: value for key, value in result["measurement"].items() if key != "samples_ms"}, ensure_ascii=False
        )
    )
