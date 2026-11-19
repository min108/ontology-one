"""Resolve saved provenance using authorized subjects and immutable source versions."""

from __future__ import annotations

import hashlib
import json
from urllib.parse import quote, urlencode

from fastapi import HTTPException

from backend import db, sources
from backend.core.errors import ApiError
from backend.kernel.semantic_graph import visible_relation
from backend.security import store, visibility
from backend.security.context import require_context

MAX_PASSAGES = 50


def _step(kind: str, ident: str, label: str) -> dict:
    return {"kind": kind, "id": ident, "label": label}


def _refs(payload: dict, tenant_id: str) -> list[dict]:
    if payload.get("tenant_id", tenant_id) != tenant_id:
        return []
    refs = payload.get("source_refs")
    if isinstance(refs, list) and refs:
        return [
            item
            for item in refs
            if isinstance(item, dict)
            and item.get("kind", "anchor") == "anchor"
            and item.get("tenant_id", tenant_id) == tenant_id
        ]
    chunks = payload.get("source_chunk_ids")
    if isinstance(chunks, list) and isinstance(material := payload.get("source_material_id"), str):
        return [{"material_id": material, "chunk_id": chunk} for chunk in chunks if isinstance(chunk, str)]
    return []


def _visible_refs(conn: db.Connection, scope: visibility.Scope, entity: dict) -> list[dict]:
    # Published readers may see a passage only when its material is visible too.
    # Keep the saved binding internal and avoid projecting the parent payload.
    if scope.metadata_only:
        return []
    payload = (
        entity["payload"]
        if scope.drafts
        else json.loads(
            conn.execute("SELECT payload FROM entities WHERE id=?", (entity["id"],)).fetchone()["payload"] or "{}"
        )
    )
    return _refs(payload, scope.tenant_id)


def saved_anchor(conn: db.Connection, material_id: str, ref: dict) -> dict | None:
    anchor = ref.get("anchor_id") or ref.get("span_id")
    chunk = ref.get("chunk_id")
    if (anchor is not None and not isinstance(anchor, str)) or (chunk is not None and not isinstance(chunk, str)):
        return None
    if not (anchor or chunk):
        return None
    clauses, params = [], []
    if anchor:
        clauses.append("a.id=?")
        params.append(anchor)
    if chunk:
        if anchor:
            # Historical chunk bindings survive resegmentation. A subspan must lie
            # inside that original chunk of this material and that exact revision.
            clauses.append(
                "EXISTS (SELECT 1 FROM chunk_anchors ca JOIN source_anchors base ON base.id=ca.anchor_id "
                "WHERE ca.chunk_id=? AND base.material_id=a.material_id AND base.source_version=a.source_version "
                "AND base.`start`<=a.`start` AND base.`end`>=a.`end`)"
            )
        else:
            clauses.append("EXISTS (SELECT 1 FROM chunk_anchors ca WHERE ca.chunk_id=? AND ca.anchor_id=a.id)")
        params.append(chunk)
    row = conn.execute(
        "SELECT a.id,a.source_version,a.`start`,a.`end`,a.locator,a.quote_sha256,v.version,"
        "SUBSTRING(v.text,a.`start`+1,a.`end`-a.`start`) AS quote "
        "FROM source_anchors a JOIN source_versions v ON v.id=a.source_version AND v.material_id=a.material_id "
        "WHERE a.material_id=? AND " + " AND ".join(clauses),
        (material_id, *params),
    ).fetchone()
    if (
        not row
        or not row["quote"]
        or len(row["quote"]) != row["end"] - row["start"]
        or hashlib.sha256(row["quote"].encode()).hexdigest() != row["quote_sha256"]
    ):
        return None
    # A mismatched saved binding is never silently relocated to today's text.
    expected = {
        "anchor_id": row["id"],
        "span_id": row["id"],
        "span": {"start": row["start"], "end": row["end"]},
        "source_version": row["source_version"],
        "revision_id": row["source_version"],
        "quote": row["quote"],
        "start": row["start"],
        "end": row["end"],
    }
    if any(k in ref and ref[k] != value for k, value in expected.items()):
        return None
    return dict(row)


def _extraction(conn: db.Connection, entity_id: str, tenant_id: str) -> dict:
    paths = {}
    rows = conn.execute(
        "SELECT i.id,i.changeset_id,i.evidence,r.id AS run_id FROM changeset_items i "
        "LEFT JOIN recipe_runs r ON r.changeset_id=i.changeset_id "
        "WHERE i.applied_entity_id=? ORDER BY i.applied_version DESC,i.id",
        (entity_id,),
    )
    for row in rows:
        evidence = row["evidence"]
        evidence = json.loads(evidence or "[]") if isinstance(evidence, str) else evidence
        for ref in evidence if isinstance(evidence, list) else []:
            if not isinstance(ref, dict) or ref.get("tenant_id", tenant_id) != tenant_id:
                continue
            key = (ref.get("material_id"), ref.get("source_version"), ref.get("anchor_id"))
            if all(isinstance(item, str) and item for item in key) and key not in paths:
                path = [_step("recipe", row["run_id"], "整理制度材料")] if row["run_id"] else []
                paths[key] = [
                    *path,
                    _step("changeset", row["changeset_id"], "核对建设方案"),
                    _step("item", row["id"], "采纳条目"),
                ]
    return paths


def trace(
    conn: db.Connection,
    scope: visibility.Scope,
    *,
    entity_id: str | None = None,
    relation_id: str | None = None,
    receipt_id: str | None = None,
) -> dict:
    if receipt_id is not None:
        return _query_receipt(conn, scope, receipt_id)
    if relation_id is not None:
        edge = visible_relation(conn, scope, relation_id)
        subject, kind, version = edge["id"], "relation", None
        payload = json.loads(edge["payload"] or "{}") if isinstance(edge["payload"], str) else edge["payload"] or {}
        refs = _refs(payload, scope.tenant_id)
        if (
            not refs
            and not payload.get("source_refs")
            and payload.get("tenant_id", scope.tenant_id) == scope.tenant_id
            and edge["tk"] == "material"
            and edge["type"] in {"derived_from", "references_source", "sourced_from", "anchored_in"}
        ):
            source = visibility.visible_entity(conn, edge["source"], scope=scope)
            refs = [ref for ref in _visible_refs(conn, scope, source) if ref.get("material_id") == edge["target"]]
        last = _step("relation", subject, "登记关联依据")
    else:
        entity = visibility.visible_entity(conn, entity_id, scope=scope)
        subject, kind, version = entity["id"], "entity", entity["version"]
        last = _step("entity", subject, entity["title"])
        refs = _visible_refs(conn, scope, entity)
    result = {"subject_id": subject, "subject_type": kind, "entity_version": version, "sources": [], "truncated": False}
    if scope.metadata_only or not refs:
        return result
    materials = visibility.visible_entities(
        conn, {ref["material_id"] for ref in refs if isinstance(ref.get("material_id"), str)}, scope=scope
    )
    exposure = _extraction(conn, subject, scope.tenant_id) if scope.drafts and kind == "entity" else {}
    seen = set()
    for ref in refs:
        material = materials.get(ref.get("material_id")) if isinstance(ref.get("material_id"), str) else None
        if not material or material["kind"] != "material":
            continue
        anchor = saved_anchor(conn, material["id"], ref)
        if not anchor or anchor["id"] in seen:
            continue
        if len(seen) >= MAX_PASSAGES:
            result["truncated"] = True
            break
        seen.add(anchor["id"])
        key = (material["id"], anchor["source_version"], anchor["id"])
        steps = exposure.get(key, [])
        if not steps and kind == "entity" and scope.drafts and (candidate := entity["payload"].get("candidate_id")):
            candidates = material["payload"].get("candidates") or []
            if any(
                isinstance(c, dict) and c.get("id") == candidate and c.get("entity_id") == subject for c in candidates
            ):
                steps = [_step("candidate", candidate, "采纳材料候选")]
        path = [
            _step("material", material["id"], material["title"]),
            _step("source_version", anchor["source_version"], f"原文第 {anchor['version']} 版"),
            _step("anchor", anchor["id"], anchor["locator"] or "原文引文"),
            *steps,
            last,
        ]
        result["sources"].append(
            {
                "material_id": material["id"],
                "title": material["title"],
                "source_version": anchor["source_version"],
                "version": anchor["version"],
                "anchor_id": anchor["id"],
                "start": anchor["start"],
                "end": anchor["end"],
                "quote": anchor["quote"],
                "locator": anchor["locator"],
                "href": "/materials/"
                + quote(material["id"], safe="")
                + "?"
                + urlencode({"anchor": anchor["id"], "source_version": anchor["source_version"]}),
                "path": path,
            }
        )
    return result


def _query_receipt(conn: db.Connection, scope: visibility.Scope, receipt: str) -> dict:
    # The source-query API returns its server-generated request id in X-Request-Id.
    # Its append-only success record contains provenance and row counts, never source rows.
    if scope.metadata_only or require_context().readonly:
        raise ApiError("NOT_FOUND")
    person = require_context()
    with store.control_connection() as control, control.cursor() as cursor:
        cursor.execute(
            "SELECT resource,details FROM sec_audit WHERE request_id=%s AND tenant_id=%s AND actor_id=%s "
            "AND action='source_query' AND outcome='success' ORDER BY id LIMIT 2",
            (receipt, scope.tenant_id, person.user_id),
        )
        rows = cursor.fetchall()
    if len(rows) != 1:
        raise ApiError("NOT_FOUND")
    recorded = json.loads(rows[0]["details"])
    if not isinstance(recorded, dict) or (
        recorded.get("application_id")
        or not isinstance(recorded.get("schema_hash"), str)
        or type(recorded.get("mapping_version")) is not int
        or type(recorded.get("row_count")) is not int
    ):
        raise ApiError("NOT_FOUND")
    mapping = conn.execute(
        "SELECT source_id FROM source_mappings WHERE id=? AND status='published'", (rows[0]["resource"],)
    ).fetchone()
    if not mapping or mapping["source_id"] != recorded.get("source_id"):
        raise ApiError("NOT_FOUND")
    # Reuse the same current source authorization as the producing query.
    try:
        sources._authorize(conn, mapping["source_id"], "query")
    except HTTPException as exc:
        if exc.status_code not in {403, 404}:
            raise
        raise ApiError("NOT_FOUND") from None
    return {
        "subject_id": receipt,
        "subject_type": "receipt",
        "sources": [],
        "query": {
            "source_id": mapping["source_id"],
            "mapping_id": rows[0]["resource"],
            "mapping_version": recorded["mapping_version"],
            "source_version": recorded.get("config_version"),
            "schema_hash": recorded["schema_hash"],
            "row_count": recorded["row_count"],
        },
    }
