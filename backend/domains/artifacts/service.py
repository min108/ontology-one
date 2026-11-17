"""Only server-owned completed runs can become private, versioned reports."""

import hashlib
import json
import secrets
from contextlib import closing
from urllib.parse import urlencode

from fastapi import HTTPException

from backend import db, provenance
from backend.core.errors import ApiError
from backend.domains.artifacts import render
from backend.security.context import require_context
from backend.security.store import assert_principal_current


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _owned(conn: db.Connection, ident: str, *, lock: bool = False) -> dict:
    person = assert_principal_current(require_context())
    row = conn.execute(
        "SELECT * FROM artifacts WHERE id=? AND owner_user_id=?" + (" FOR UPDATE" if lock else ""),
        (ident, person.user_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "报告不存在或不属于当前用户。")
    return dict(row)


def _access(ident: str, version: int, action: str, format: str | None = None) -> None:
    # Access audit is the only write allowed during a read capability. It does
    # not modify ontology, source data, report content or visibility.
    with db.business_readonly(False), closing(db.connect()) as conn:
        conn.execute(
            "INSERT INTO artifact_access_log (artifact_id,version,user_id,action,format,at) VALUES (?,?,?,?,?,?)",
            (ident, version, require_context().user_id, action, format, db.now()),
        )
        conn.commit()


def _citations(conn: db.Connection, run: dict) -> dict:
    person, result = require_context(), {}
    for item in run.get("provenance", []):
        if item.get("kind") != "ontology" or item.get("tenant_id") != person.tenant_id:
            continue
        row = conn.execute(
            "SELECT * FROM entities WHERE id=? AND status='published'", (item.get("entity_id"),)
        ).fetchone()
        if row is None or row["version"] != item.get("version"):
            raise HTTPException(409, "回答引用的成果已变化，请重新提问后生成报告。")
        payload = json.loads(row["payload"] or "{}")
        if payload.get("hide_in_graph") in (True, 1, "1", "true"):
            raise HTTPException(403, "回答引用的成果已不在可见范围内。")
        for ref in payload.get("source_refs", []):
            checked = provenance.normalize_source_ref(conn, ref.get("material_id"), ref)
            material = conn.execute(
                "SELECT title FROM entities WHERE id=? AND kind='material'", (checked["material_id"],)
            ).fetchone()
            key = f"c{len(result) + 1}"
            result[key] = {
                "title": row["title"],
                "entity_id": row["id"],
                "source_title": material["title"] if material else "",
                "material_id": checked["material_id"],
                "version": row["version"],
                "quote": checked["quote"],
                "locator": checked.get("locator", ""),
                "path": "/materials/"
                + checked["material_id"]
                + "?"
                + urlencode({"anchor": checked["anchor_id"], "source_version": checked["source_version"]}),
                "anchor_id": checked["anchor_id"],
                "source_version": checked["source_version"],
            }
            if len(result) >= 50:
                return result
    if not result:
        raise HTTPException(422, "本次回答尚无可定位的制度原文。请让助手读取已发布要素的内容并引用依据后重新生成。")
    return result


def _files(spec: dict) -> list[dict]:
    return [
        {
            "format": format,
            "storage": "reproducible_spec",
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        for format, content in (("html", render.html(spec)), ("docx", render.docx(spec)))
    ]


def _save_version(conn: db.Connection, ident: str, version: int, spec: dict, run_id: str, note: str) -> None:
    files = _files(spec)  # Both formats must validate before anything is saved.
    manifest = [
        {
            "block": index,
            "columns": block["columns"],
            "rows": len(block["rows"]),
            "citations": block["citations"],
            "truncated": False,
        }
        for index, block in enumerate(spec["blocks"])
        if block["type"] == "table"
    ]
    conn.execute(
        "INSERT INTO artifact_versions (artifact_id,version,spec,files,data_manifest,citations,"
        "run_id,change_note,generated_by,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            ident,
            version,
            _json(spec),
            _json(files),
            _json(manifest),
            _json(spec["citations"]),
            run_id,
            note,
            "analyst",
            require_context().user_id,
            db.now(),
        ),
    )
    conn.execute("UPDATE artifacts SET current_version=?,updated_at=? WHERE id=?", (version, db.now(), ident))


def create(session_id: str, run_id: str, title: str = "") -> dict:
    from backend.agent_runtime import SESSIONS

    person = assert_principal_current(require_context())
    session = SESSIONS.get(person, session_id)
    run = session["runs"].get(run_id)
    if not run or run.get("status") != "completed" or not run.get("answer"):
        raise HTTPException(409, "请等待本次回答完成后生成报告。")
    # The agent binding also validates asynchronously. Recheck all retained
    # proofs here so capability callers cannot bypass source revocation checks.
    for ledger in session["ledgers"]:
        ledger.validate()
    from backend.agent_runtime import _configuration_signature

    if run.get("_report_configuration_signature") != _configuration_signature():
        raise HTTPException(409, "模型配置已变化，请重新提问。")
    ident, timestamp = "ART-" + secrets.token_hex(8), db.now()
    with closing(db.connect()) as conn:
        conn.execute("BEGIN")
        citations = _citations(conn, run)
        blocks = render.blocks_from_answer(run["answer"], citations)
        used = {key for block in blocks for key in block["citations"]}
        citations = {key: source for key, source in citations.items() if key in used}
        if not citations:
            raise ApiError(
                "EVIDENCE_NOT_FOUND",
                "回答未实际引用已读取的依据，请让助手在各段和表格中标明要素名称或链接。",
                details={
                    "code": "CITATION_UNVERIFIED",
                    "issues": ["回答中的段落和表格未标明可核验来源；请逐段、逐表补充实际读取的要素名称或链接。"],
                },
            )
        source_kind = run.get("report_source_kind", "analyst")
        spec = {
            "schema": "r2-report-v1",
            "title": title.strip() or "《" + next(iter(citations.values()))["title"][:64] + "》问答报告",
            "blocks": blocks,
            "citations": citations,
            "source_label": "模拟模型演练结果" if source_kind == "mock" else "真实模型回答 · 已核对引用与数值",
            "created_at": timestamp,
        }
        original_id = run.get("revision_of")
        if original_id:
            original = session["runs"].get(original_id)
            if not original or not original.get("answer") or original.get("status") != "completed":
                raise HTTPException(409, "修订前的回答已不可用，请重新提问后生成报告。")
            spec["revision"] = {"count": 1, "before": original["answer"], "after": run["answer"]}
            spec["source_label"] += " · 已按核对结果修订 1 次"
        try:
            render.verify(spec)
        except HTTPException as exc:
            if isinstance(exc.detail, dict) and exc.detail.get("code") == "NUMERIC_UNVERIFIED":
                raise ApiError(
                    "NUMERIC_CHECK_FAILED",
                    exc.detail["message"],
                    details={"code": exc.detail["code"], "issues": exc.detail["issues"]},
                ) from exc
            raise
        conn.execute(
            "INSERT INTO artifacts (id,title,template,owner_user_id,conversation_id,"
            "source_kind,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (ident, spec["title"], "official", person.user_id, session_id, source_kind, timestamp, timestamp),
        )
        _save_version(conn, ident, 1, spec, run_id, "从已完成回答生成")
        assert_principal_current(person)
        conn.commit()
    return get(ident)


def list_reports() -> dict:
    with closing(db.connect()) as conn:
        rows = conn.execute(
            "SELECT id,title,current_version,source_kind,status,created_at "
            "FROM artifacts WHERE owner_user_id=? ORDER BY updated_at DESC LIMIT 100",
            (require_context().user_id,),
        ).fetchall()
        return {"items": [dict(row) for row in rows]}


def get(ident: str) -> dict:
    with closing(db.connect()) as conn:
        result = _owned(conn, ident)
        version = conn.execute(
            "SELECT * FROM artifact_versions WHERE artifact_id=? AND version=?", (ident, result["current_version"])
        ).fetchone()
        if version is None:
            raise HTTPException(409, "报告版本不完整，请联系管理员。")
        result.update({key: json.loads(version[key]) for key in ("spec", "files", "data_manifest", "citations")})
        result["versions"] = [
            dict(row)
            for row in conn.execute(
                "SELECT version,created_at,change_note FROM artifact_versions WHERE artifact_id=? "
                "ORDER BY version DESC",
                (ident,),
            ).fetchall()
        ]
    _access(ident, result["current_version"], "view")
    return result


def regenerate(ident: str, expected_version: int) -> dict:
    with closing(db.connect()) as conn:
        conn.execute("BEGIN")
        result = _owned(conn, ident, lock=True)
        if result["current_version"] != expected_version:
            raise HTTPException(409, "报告已有新版本，请刷新后重试。")
        old = conn.execute(
            "SELECT spec,run_id FROM artifact_versions WHERE artifact_id=? AND version=?", (ident, expected_version)
        ).fetchone()
        _save_version(conn, ident, expected_version + 1, json.loads(old["spec"]), old["run_id"], "按已保存依据再次生成")
        conn.commit()
    return get(ident)


def download(ident: str, version: int, format: str) -> bytes:
    if format not in {"html", "docx"}:
        raise HTTPException(422, "仅支持网页与 Word 报告。")
    with closing(db.connect()) as conn:
        _owned(conn, ident)
        row = conn.execute(
            "SELECT spec,files FROM artifact_versions WHERE artifact_id=? AND version=?", (ident, version)
        ).fetchone()
        if row is None:
            raise HTTPException(404, "报告版本不存在。")
        content = getattr(render, format)(json.loads(row["spec"]))
        stored = next(file for file in json.loads(row["files"]) if file["format"] == format)
        if hashlib.sha256(content).hexdigest() != stored["sha256"]:
            raise HTTPException(409, "报告渲染器已升级，请再次生成后下载。")
    _access(ident, version, "download", format)
    return content
