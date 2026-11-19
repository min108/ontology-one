"""Resolve saved provenance using authorized subjects and immutable source versions."""

from __future__ import annotations

import hashlib
import json
from urllib.parse import quote, urlencode

from fastapi import HTTPException

from backend import db, sources
from backend.core.errors import ApiError
from backend.infra.search_index import chunk_doc_id_sql
from backend.kernel.semantic_graph import visible_relation
from backend.security import store, visibility
from backend.security.context import require_context
from backend.security.reference_fields import PRIVATE_KEYS

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
            and not PRIVATE_KEYS.intersection(item)
        ]
    chunks = payload.get("source_chunk_ids")
    if isinstance(chunks, list) and isinstance(material := payload.get("source_material_id"), str):
        return [{"material_id": material, "chunk_id": chunk} for chunk in chunks if isinstance(chunk, str)]
    return []


def _visible_refs(conn: db.Connection, scope: visibility.Scope, entity: dict) -> list[dict]:
    # Work from the actual saved binding. A resegmentation may remove the working
    # chunk from the projected payload while its immutable chunk-to-anchor receipt survives.
    if scope.metadata_only:
        return []
    where, args = scope.predicate()
    row = conn.execute(f"SELECT e.payload FROM entities e WHERE {where} AND e.id=?", (*args, entity["id"])).fetchone()
    return _refs(json.loads(row["payload"] or "{}"), scope.tenant_id) if row else []


def _relation_refs(conn: db.Connection, scope: visibility.Scope, edge: dict) -> list[dict]:
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
        return [ref for ref in _visible_refs(conn, scope, source) if ref.get("material_id") == edge["target"]]
    return refs


def saved_anchor(conn: db.Connection, material_id: str, ref: dict) -> dict | None:
    anchor = ref.get("anchor_id") or ref.get("span_id")
    chunk = ref.get("chunk_id") or ref.get("source_chunk_id")
    if "source_chunk_id" in ref and ref["source_chunk_id"] != chunk:
        return None
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
        "material_id": material_id,
        "source_material_id": material_id,
        "source_id": material_id,
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


def _field_refs(conn: db.Connection, scope: visibility.Scope, ident: str) -> list[dict]:
    from backend.kernel import element_reads, gate_fields
    from backend.kernel.metadata import get_meta

    meta = get_meta(conn, ident) or {}
    gates = meta.get("gates")
    if not isinstance(gates, dict) or not gates.get("field_sources"):
        return []
    entity, raw = element_reads.detail_entity(scope, ident)
    fields = gate_fields.project(conn, raw, meta)
    if not scope.drafts:
        entity["relations"], _ = element_reads.relations(scope, ident)
        fields = element_reads.published_fields(scope, entity, meta, fields)
    return [
        {**{key: field[key] for key in ("material_id", "source_version", "anchor_id")}, "quote": field["source_quote"]}
        for field in fields
        if field.get("source") == "source"
        and all(field.get(key) for key in ("material_id", "anchor_id", "source_version", "source_quote"))
    ]


def _current_confirmed(conn: db.Connection, material_id: str, anchor: dict, chunk_id: str | None) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM source_anchors a JOIN chunk_anchors ca ON ca.anchor_id=a.id "
            "JOIN chunks c ON c.id=ca.chunk_id AND c.material_id=a.material_id "
            f"JOIN search_index si ON si.doc_id={chunk_doc_id_sql('c.id')} AND si.entity_id=a.material_id "
            "WHERE si.doc_type='chunk' AND a.material_id=? AND a.source_version=? "
            "AND a.source_version=(SELECT v.id FROM source_versions v WHERE v.material_id=a.material_id "
            "ORDER BY v.version DESC LIMIT 1) "
            "AND a.`start`<=? AND a.`end`>=? AND (? IS NULL OR c.id=?) LIMIT 1",
            (material_id, anchor["source_version"], anchor["start"], anchor["end"], chunk_id, chunk_id),
        ).fetchone()
    )


def _saved_citation(conn: db.Connection, material_id: str, ref: dict) -> dict | None:
    anchor = saved_anchor(conn, material_id, ref)
    if not anchor:
        return None
    # An editable draft can contain copied identifiers alone. Historical access
    # discloses only the exact text the visible subject actually retained.
    if ref.get("quote") == anchor["quote"] or _current_confirmed(
        conn, material_id, anchor, ref.get("chunk_id") or ref.get("source_chunk_id")
    ):
        return anchor
    return None


def readable_anchor(
    conn: db.Connection,
    scope: visibility.Scope,
    material_id: str,
    *,
    chunk_id: str | None = None,
    anchor_id: str | None = None,
    source_version: str | None = None,
    citing_entity_id: str | None = None,
    citing_relation_id: str | None = None,
) -> dict:
    """Authorize the current confirmed passage, or this exact saved citation on a visible subject."""
    if scope.metadata_only or scope.external_tool or (citing_entity_id is not None and citing_relation_id is not None):
        raise ApiError("NOT_FOUND")
    material = visibility.visible_entity(conn, material_id, scope=scope)
    if material["kind"] != "material":
        raise ApiError("NOT_FOUND")
    location = {"chunk_id": chunk_id, "anchor_id": anchor_id, "source_version": source_version}
    anchor = saved_anchor(conn, material_id, {key: value for key, value in location.items() if value is not None})
    if not anchor:
        raise ApiError("NOT_FOUND")
    if citing_entity_id is None and citing_relation_id is None:
        if _current_confirmed(conn, material_id, anchor, chunk_id):
            return anchor
        raise ApiError("NOT_FOUND")

    def matches(ref: dict) -> bool:
        saved_chunk = ref.get("chunk_id") or ref.get("source_chunk_id")
        if ref.get("material_id") != material_id or (chunk_id and saved_chunk and saved_chunk != chunk_id):
            return False
        candidate = _saved_citation(conn, material_id, ref)
        return bool(
            candidate and candidate["id"] == anchor["id"] and candidate["source_version"] == anchor["source_version"]
        )

    if citing_relation_id is not None:
        edge = visible_relation(conn, scope, citing_relation_id)
        refs = _relation_refs(conn, scope, edge)
    else:
        subject = visibility.visible_entity(conn, citing_entity_id, scope=scope)
        refs = _visible_refs(conn, scope, subject)
    if any(matches(ref) for ref in refs):
        return anchor
    if citing_entity_id is not None and any(matches(ref) for ref in _field_refs(conn, scope, citing_entity_id)):
        return anchor
    raise ApiError("NOT_FOUND")


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
    chunk_id: str | None = None,
    anchor_id: str | None = None,
    source_version: str | None = None,
    citing_entity_id: str | None = None,
    citing_relation_id: str | None = None,
) -> dict:
    if receipt_id is not None:
        return _query_receipt(conn, scope, receipt_id)
    linked_from = {}
    focused = None
    if relation_id is not None:
        edge = visible_relation(conn, scope, relation_id)
        subject, kind, version = edge["id"], "relation", None
        refs = _relation_refs(conn, scope, edge)
        linked_from["citing_relation_id"] = subject
        last = _step("relation", subject, "登记关联依据")
    else:
        entity = visibility.visible_entity(conn, entity_id, scope=scope)
        subject, kind, version = entity["id"], "entity", entity["version"]
        last = _step("entity", subject, entity["title"])
        if chunk_id or anchor_id:
            linked_from = {
                key: value
                for key, value in {
                    "citing_entity_id": citing_entity_id,
                    "citing_relation_id": citing_relation_id,
                }.items()
                if value is not None
            }
            focused = readable_anchor(
                conn,
                scope,
                subject,
                chunk_id=chunk_id,
                anchor_id=anchor_id,
                source_version=source_version,
                **linked_from,
            )
            refs = [{"material_id": subject}]
        else:
            refs = _visible_refs(conn, scope, entity)
            linked_from["citing_entity_id"] = subject
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
        anchor = focused or _saved_citation(conn, material["id"], ref)
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
                + urlencode({"anchor": anchor["id"], "source_version": anchor["source_version"], **linked_from}),
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
