"""Authorization and projections shared by tenant-local semantic reads (R4 D20)."""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from itertools import batched

from backend import db
from backend.core.errors import ApiError
from backend.jobs import assert_execution_principal_current
from backend.security import store
from backend.security.context import Principal, require_context
from backend.security.permissions import permissions_for
from backend.security.reference_fields import PRIVATE_ID, PRIVATE_KEYS, reference_keys

_METADATA = "e.id,e.kind,e.title,e.summary,e.status,e.domain,e.org_id,e.version,e.source_kind,e.created_at,e.updated_at"


def _checked_alias(alias: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", alias):
        raise ValueError("Invalid SQL entity alias")
    return alias


def mcp_published_predicate(alias: str = "e") -> str:
    """The fixed metadata projection used by the longstanding external tools."""
    e = _checked_alias(alias)
    return (
        f"{e}.status='published' AND {e}.kind IN ('data','rule','skill','knowledge','organization') "
        f"AND COALESCE(json_extract({e}.payload,'$.active_asset'),1)!=0 "
        f"AND COALESCE(CAST(json_extract({e}.payload,'$.hide_in_graph') AS CHAR),'false') NOT IN ('1','true')"
    )


@dataclass(frozen=True, slots=True)
class Scope:
    tenant_id: str
    drafts: bool
    metadata_only: bool
    external_tool: bool
    shared_only: bool
    principal: Principal
    connection: db.Connection

    def predicate(self, alias: str = "e", *, history: str = "current") -> tuple[str, tuple]:
        e = _checked_alias(alias)
        terms = [f"{e}.kind NOT IN ('artifact','report')"]
        if self.external_tool:
            terms.append(mcp_published_predicate(e))
        elif history != "all" or not self.drafts:
            terms.extend(
                (
                    f"COALESCE(json_extract({e}.payload,'$.active_asset'),1)!=0",
                    f"COALESCE({e}.status,'') NOT IN ('archived','discarded','withdrawn')",
                )
            )
        if not self.drafts and not self.external_tool:
            terms.append(f"{e}.status='published'")
        if self.shared_only:
            terms.extend(
                (
                    f"{e}.kind IN ('data','rule','skill','knowledge')",
                    f"EXISTS (SELECT 1 FROM entity_access va WHERE va.entity_id={e}.id "
                    "AND va.visibility='headquarters')",
                )
            )
        return " AND ".join(terms), ()


def can_read_drafts(principal: Principal) -> bool:
    return (
        principal.identity_kind == "session" and not principal.readonly and "draft.read" in permissions_for(principal)
    )


def _bound(conn: db.Connection) -> tuple[Principal, str]:
    person = require_context()
    if person.identity_kind not in {"session", "application"} or conn.principal != person:
        raise ApiError("PERMISSION_DENIED")
    assert_execution_principal_current(person)
    # Both db.connect and tenant_connection authenticate before acquiring a
    # connection. A federation connection can name a different actual database.
    return person, conn.execute("SELECT DATABASE()").fetchone()[0]


def _granted_scope(
    conn: db.Connection, person: Principal, database: str, tenant_id: str, *, published_only: bool, external: bool
) -> Scope:
    if not conn.readonly or person.identity_kind != "session":
        raise ApiError("PERMISSION_DENIED")
    grant = store.can_read_tenant(person, tenant_id)
    if not grant or grant["db_name"] != database:
        raise ApiError("NOT_FOUND")
    shared = grant["visibility"] != "private"
    drafts = not (shared or published_only or external) and can_read_drafts(person)
    return Scope(tenant_id, drafts, True, external, shared, person, conn)


def federation_scope(conn: db.Connection, tenant_id: str, *, published_only: bool = False) -> Scope:
    person, database = _bound(conn)
    return _granted_scope(conn, person, database, tenant_id, published_only=published_only, external=False)


def scope_for(conn: db.Connection, *, published_only: bool = False, metadata_only: bool = False) -> Scope:
    """Bindings may narrow access, but the live principal and physical DB set the upper bound."""
    person, database = _bound(conn)
    if database != person.database_name:
        raise ApiError("NOT_FOUND")
    if person.readonly and person.tenant_id != person.home_tenant_id:
        return _granted_scope(conn, person, database, person.tenant_id, published_only=True, external=metadata_only)
    external = metadata_only or person.identity_kind == "application"
    drafts = not (published_only or external) and can_read_drafts(person)
    return Scope(person.tenant_id, drafts, external, external, False, person, conn)


_SKIP = object()


def _records(value: object, in_sources: bool = False) -> Iterator[tuple[dict, bool]]:
    if isinstance(value, dict):
        yield value, in_sources
        for key, child in value.items():
            yield from _records(child, key == "source_refs")
    elif isinstance(value, list):
        for child in value:
            yield from _records(child, in_sources)


def _visible_pointer_ids(scope: Scope, objects: Iterable[dict]) -> dict[str, set[str]]:
    groups = {"owner": set(), "chunk": set(), "anchor": set(), "version": set()}
    for obj in objects:
        for record, in_sources in _records(obj):
            for _, kind, ident in reference_keys(record, in_sources):
                groups[kind].add(ident)
    where, params = scope.predicate()
    permitted = {}
    for group, source in {
        "owner": "entities e",
        "chunk": "chunks t JOIN entities e ON e.id=t.material_id",
        "anchor": "source_anchors t JOIN entities e ON e.id=t.material_id",
        "version": "source_versions t JOIN entities e ON e.id=t.material_id",
    }.items():
        permitted[group] = set()
        column = "e.id" if group == "owner" else "t.id"
        for ids in batched(groups[group], 400):
            marks = ",".join("?" for _ in ids)
            sql = f"SELECT {column} FROM {source} WHERE {where} AND {column} IN ({marks})"
            permitted[group].update(row[0] for row in scope.connection.execute(sql, (*params, *ids)))
    return permitted


def _clean_payload(
    value: object,
    scope: Scope,
    visible: dict[str, set[str]],
    *,
    in_sources: bool = False,
    root: bool = False,
    legacy_notes: bool = False,
) -> object:
    if isinstance(value, list):
        result = [_clean_payload(v, scope, visible, in_sources=in_sources, legacy_notes=legacy_notes) for v in value]
        return [v for v in result if v is not _SKIP]
    if not isinstance(value, dict):
        return _SKIP if isinstance(value, str) and PRIVATE_ID.fullmatch(value) else value
    pointers = list(reference_keys(value, in_sources))
    hidden = {key for key, group, ident in pointers if ident not in visible[group]}
    private = (
        value.get("kind") in ("artifact", "report")
        or any(key in value for key in PRIVATE_KEYS)
        or value.get("tenant_id", scope.tenant_id) != scope.tenant_id
    )
    if not root and (private or any(not isinstance(value.get(key), list) for key in hidden)):
        return _SKIP
    if in_sources and not pointers and value.get("kind") != "external" and not legacy_notes:
        return _SKIP
    clean = {}
    for key, child in value.items():
        if key in PRIVATE_KEYS:
            continue
        if key in hidden:
            if not isinstance(child, list):
                continue
            child = [
                ident
                for ident in child
                if any(k == key and ident == known and known in visible[group] for k, group, known in pointers)
            ]
        # The review cycle's revision id identifies editor history, unlike
        # a source reference's revision_id which identifies an original version.
        review_id = None
        if root and legacy_notes and key == "r2_review" and isinstance(child, dict):
            candidate = child.get("revision_id")
            if isinstance(candidate, str) and re.fullmatch(r"[0-9a-f]{32}", candidate):
                review_id = candidate
                child = {k: v for k, v in child.items() if k != "revision_id"}
        resolved = _clean_payload(child, scope, visible, in_sources=key == "source_refs", legacy_notes=legacy_notes)
        if review_id and isinstance(resolved, dict):
            resolved["revision_id"] = review_id
        if resolved is not _SKIP:
            clean[key] = resolved
    return clean


def project_payloads(
    scope: Scope, payloads: list[dict], *, records: bool = False, legacy_notes: bool = False
) -> list[dict]:
    """Remove inaccessible cross-record pointers and their cached titles and passages."""
    if scope.principal != require_context():
        raise ApiError("PERMISSION_DENIED")
    permitted = _visible_pointer_ids(scope, payloads)
    projected = [
        _clean_payload(payload, scope, permitted, root=not records, legacy_notes=legacy_notes and scope.drafts)
        for payload in payloads
    ]
    return [value if isinstance(value, dict) else {} for value in projected]


def visible_entities(
    conn: db.Connection, ids: Iterable[str], *, scope: Scope | None = None, history: str = "current"
) -> dict[str, dict]:
    current = scope or scope_for(conn)
    if current.connection is not conn or current.principal != require_context():
        raise ApiError("PERMISSION_DENIED")
    where, params = current.predicate(history=history)
    columns = "e.*" if current.drafts and not current.metadata_only else _METADATA
    result = {}
    for chunk in batched(dict.fromkeys(ids), 500):
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT {columns} FROM entities e WHERE {where} AND e.id IN ({placeholders})", (*params, *chunk)
        )
        for row in rows:
            entity = db.entity_json(row)
            entity.update(owner_tenant_id=current.tenant_id, owner_org_id=current.tenant_id)
            result[entity["id"]] = entity
    if current.drafts and not current.metadata_only:
        payloads = project_payloads(current, [entity["payload"] for entity in result.values()])
        for entity, payload in zip(result.values(), payloads, strict=True):
            entity["payload"] = payload
    return result


def visible_entity(conn: db.Connection, ident: str, *, scope: Scope | None = None) -> dict:
    entity = visible_entities(conn, [ident], scope=scope).get(ident)
    if entity is None:
        raise ApiError("NOT_FOUND")
    return entity
