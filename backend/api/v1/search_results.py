"""Adapt ranked entity and source hits to the original entity-list contract."""

from backend import db

ENTITY_FIELDS = (
    "id",
    "kind",
    "title",
    "summary",
    "domain",
    "org_id",
    "status",
    "source_kind",
    "payload",
    "created_at",
    "updated_at",
    "version",
    "owner_tenant_id",
    "owner_org_id",
)


def needs_ranked_catalog(query: str | None) -> bool:
    """Search requests and published-only catalogs use the same visibility boundary."""
    from backend.security.context import require_context
    from backend.security.visibility import can_read_drafts

    return query is not None or not can_read_drafts(require_context())


def ranked_entities(conn: db.Connection, query: str, **filters: object) -> dict:
    from backend.kernel.search import search

    result = search(conn, query, legacy_payload=True, **filters)
    rows = [{key: item[key] for key in ENTITY_FIELDS if key in item} for item in result["items"]]
    return {"items": rows, "total": result["total"], "page": result["page"], "page_size": result["page_size"]}
