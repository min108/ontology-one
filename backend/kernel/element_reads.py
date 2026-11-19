"""Read the current element, its citations and both endpoints in one authorized scope."""

import json
from urllib.parse import quote

from backend import db, federation
from backend.asset_kinds import canonical_entity
from backend.core.errors import ApiError
from backend.kernel import gate_fields, gates
from backend.kernel.kinds import KINDS
from backend.kernel.metadata import get_meta
from backend.kernel.semantic_evidence import saved_anchor
from backend.security import visibility

PUBLIC_RELATIONS = (*sorted(gates.CONFIRMED), "demo")


def card_payload(entity: dict) -> dict:
    payload = entity["payload"]
    if entity["kind"] == "material":
        # Original documents and their physical blocks are read using the paged material projection.
        return {
            k: v
            for k, v in payload.items()
            if k
            not in {
                "original_text",
                "source_blocks",
                "segmentation_preview",
                "original_text_before_v3",
                "original_body_before_v3",
            }
        }
    return payload


def display_payload(scope: visibility.Scope, payload: dict) -> dict:
    """Render public business fields; obtain every cited passage from its original version."""
    if scope.external_tool:
        return {}
    (clean,) = visibility.project_payloads(scope, [payload])
    ids = federation._reference_ids(clean)
    known = visibility.visible_entities(scope.connection, ids, scope=scope)
    tenant = {"id": scope.tenant_id, "name": ""}
    for ident, item in known.items():
        base = "/materials/" if item["kind"] == "material" else "/ontology/elements/"
        item["detail_url"] = base + quote(ident, safe="")

    def validate_ref(ref: dict, ident: str) -> dict | None:
        if any(key in ref for key in ("anchor_id", "span_id", "chunk_id", "source_version", "revision_id", "span")):
            if known[ident]["kind"] != "material":
                return None
            anchor = saved_anchor(scope.connection, ident, ref)
            if not anchor:
                return None
            return {
                **ref,
                "quote": anchor["quote"],
                "excerpt": anchor["quote"],
                "locator": anchor["locator"],
                "anchor_id": anchor["id"],
                "source_version": anchor["source_version"],
            }
        # A historical link can identify a material without proving an excerpt.
        return {key: ref[key] for key in ("material_id", "id", "source_id") if key in ref}

    return federation._safe_payload(clean, tenant, known, validate_ref=validate_ref)[0]


def detail_entity(scope: visibility.Scope, ident: str) -> tuple[dict, dict]:
    where, args = scope.predicate(history="all" if scope.drafts else "current")
    row = scope.connection.execute(
        f"SELECT e.* FROM entities e WHERE {where} AND e.id=? "
        "AND COALESCE(json_extract(e.payload,'$.active_asset'),1)!=0",
        (*args, ident),
    ).fetchone()
    if not row or row["kind"] not in KINDS:
        raise ApiError("NOT_FOUND")
    raw = db.entity_json(row)
    if scope.drafts and not scope.metadata_only:
        (payload,) = visibility.project_payloads(scope, [card_payload(raw)], legacy_notes=True)
    else:
        payload = display_payload(scope, card_payload(raw))
    return {**raw, "payload": payload}, raw


def relations(scope: visibility.Scope, ident: str) -> tuple[dict, dict]:
    source, source_args = scope.predicate("s")
    target, target_args = scope.predicate("t")
    args = [*source_args, *target_args]
    where = (
        " FROM relations r JOIN entities s ON s.id=r.source JOIN entities t ON t.id=r.target "
        f"WHERE {source} AND {target}"
    )
    if not scope.drafts:
        where += " AND r.status IN (" + ",".join("?" for _ in PUBLIC_RELATIONS) + ")"
        args.extend(PUBLIC_RELATIONS)
    result, counts = {}, {}
    for name, column in (("incoming", "target"), ("outgoing", "source")):
        filtered = where + f" AND r.{column}=?"
        bound = (*args, ident)
        select = (
            "SELECT r.id,r.source,r.target,r.type,r.label,r.status,s.title source_title,s.kind source_kind,"
            "t.title target_title,t.kind target_kind,s.status source_status"
        )
        if scope.drafts:
            select += ",r.payload"
        rows = scope.connection.execute(select + filtered + " ORDER BY r.id LIMIT 120", bound)
        result[name] = [dict(row) for row in rows]
        counts[name] = scope.connection.execute("SELECT count(*)" + filtered, bound).fetchone()[0]
    if scope.drafts:
        all_rows = [*result["incoming"], *result["outgoing"]]
        projections = visibility.project_payloads(
            scope,
            [json.loads(row["payload"] or "{}") for row in all_rows],
            records=True,
        )
        for row, payload in zip(all_rows, projections, strict=True):
            row["payload"] = json.dumps(payload, ensure_ascii=False)
    rules = {
        row["source"]
        for row in result["incoming"]
        if row["source_kind"] == "rule"
        and row["source_status"] == "published"
        and row["type"] in {"governs", "applies_to"}
    }
    if rules and not scope.metadata_only:
        marks = ",".join("?" for _ in rules)
        records = scope.connection.execute(
            f"SELECT s.id,s.payload FROM entities s WHERE {source} AND s.id IN ({marks})",
            (*source_args, *rules),
        ).fetchall()
        clean = visibility.project_payloads(scope, [json.loads(row["payload"] or "{}") for row in records])
        rule_payloads = {}
        for row, payload in zip(records, clean, strict=True):
            # The business reading uses just the published rule's own condition and requirement.
            projected = {k: payload[k] for k in ("conditions", "obligation", "rule_text") if k in payload}
            judgement = payload.get("judgement")
            if isinstance(judgement, dict):
                projected["judgement"] = {k: judgement[k] for k in ("condition", "requirement") if k in judgement}
            rule_payloads[row["id"]] = projected
        for row in result["incoming"]:
            if row["source"] in rule_payloads:
                row["source_payload"] = json.dumps(rule_payloads[row["source"]], ensure_ascii=False)
    return result, counts


def published_fields(scope: visibility.Scope, entity: dict, meta: dict, fields: list[dict]) -> list[dict]:
    if scope.metadata_only or scope.principal.readonly:
        return []
    subjects = {
        row["target"]: row["target_title"]
        for row in entity["relations"]["outgoing"]
        if row["type"] in {"governs", "applies_to"} and row["status"] in gates.CONFIRMED
    }
    values = gate_fields.field_values(scope.connection, entity, meta, subject_titles=list(subjects.values()))
    provenance = {field["key"]: field for field in fields}
    if "subjects" in values and subjects:
        del values["subjects"]
        values.update({"subject:" + ident: title for ident, title in subjects.items()})
    published = []
    anchored = {}
    for key, value in values.items():
        subject_id = key.removeprefix("subject:") if key.startswith("subject:") else None
        field = {
            "key": key,
            "label": gate_fields.FIELD_LABELS["subjects" if subject_id else key],
            "value": value,
            "source": "legacy" if value else "missing",
            "href": "/ontology/elements/" + quote(subject_id, safe="") if subject_id else "",
        }
        record = provenance.get(key, {})
        if value and record.get("value") == value:
            if record["source"] in {"system", "editor", "batch_confirmation"}:
                field["source"] = record["source"]
            elif record["source"] == "source" and all(
                record.get(k) for k in ("material_id", "source_version", "anchor_id")
            ):
                anchored[key] = record
        published.append(field)
    allowed = visibility.visible_entities(scope.connection, [r["material_id"] for r in anchored.values()], scope=scope)
    for field in published:
        record = anchored.get(field["key"])
        if not record or allowed.get(record["material_id"], {}).get("kind") != "material":
            continue
        binding = {key: record[key] for key in ("material_id", "source_version", "anchor_id")}
        anchor = saved_anchor(scope.connection, record["material_id"], {**binding, "quote": record.get("source_quote")})
        if anchor:
            field.update(binding, source="source", source_quote=anchor["quote"])
    return published


def details(scope: visibility.Scope, ident: str) -> tuple[dict, dict, list]:
    entity, raw = detail_entity(scope, ident)
    entity["relations"], counts = relations(scope, ident)
    conn = scope.connection
    history, entity["activities"] = [], []
    if scope.drafts and not scope.metadata_only:
        entity["relation_counts"] = counts
        entity["activities"] = [
            dict(row)
            for row in conn.execute("SELECT * FROM activities WHERE entity_id=? ORDER BY id DESC LIMIT 30", (ident,))
        ]
        history = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM versions WHERE entity_id=? ORDER BY version DESC,id DESC",
                (ident,),
            )
        ]
        snapshots = []
        for row in history:
            try:
                snapshot = canonical_entity(json.loads(row["snapshot"]))
            except (ValueError, TypeError):
                # An unreadable legacy snapshot remains a version entry without emitting uninspected text.
                row.pop("snapshot", None)
                continue
            row["snapshot"] = snapshot
            snapshots.append(snapshot)
        visible = visibility.project_payloads(
            scope, [card_payload(snapshot) for snapshot in snapshots], legacy_notes=True
        )
        for snapshot, payload in zip(snapshots, visible, strict=True):
            snapshot["payload"] = payload
            if isinstance(snapshot.get("_meta"), dict):
                snapshot["_meta"].pop("gates", None)
                snapshot["_meta"].pop("card_text", None)
    meta = get_meta(conn, ident) or {}
    entity["_meta"] = {k: v for k, v in meta.items() if k not in {"gates", "card_text"}}
    checks = gates.check_entity(conn, raw)
    if not scope.drafts or scope.metadata_only:
        checks["fields"] = published_fields(scope, entity, meta, checks["fields"])
        checks["ready_to_submit"] = checks["passed"]
        checks["submission_blockers"] = list(checks["blockers"])
        for check in checks["checks"]:
            # Detailed review diagnostics can name other working drafts or quote
            # a field. The published card exposes the business check and its outcome.
            check["reason"] = {
                "pass": "本项检查已通过。",
                "fail": "本项检查未通过。",
                "waived": "审核人员已记录豁免。",
                "not_applicable": "此项检查不适用。",
            }[check["status"]]
            for key in ("application_reason", "application_by", "application_at", "denial_reason", "waiver_reason"):
                check[key] = ""
    return entity, checks, history


def published_federated_relations(groups: dict) -> dict:
    """Use the live grant on each named tenant, including both ends of a cross-institution relation."""
    by_tenant = {}
    for rows in groups.values():
        for row in rows:
            for side in ("source", "target"):
                by_tenant.setdefault(row[side + "_tenant_id"], set()).add(row[side + "_entity_id"])
    permitted = {}
    for tenant_id, ids in by_tenant.items():
        with db.tenant_connection(tenant_id, readonly=True) as conn:
            scope = visibility.federation_scope(conn, tenant_id, published_only=True)
            permitted[tenant_id] = visibility.visible_entities(conn, ids, scope=scope)
    return {
        direction: [
            r
            for r in rows
            if r["status"] in PUBLIC_RELATIONS
            and all(r[side + "_entity_id"] in permitted[r[side + "_tenant_id"]] for side in ("source", "target"))
        ]
        for direction, rows in groups.items()
    }
