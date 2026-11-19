"""Bounded pages of the saved original, scoped separately from its citations and cards."""

from backend import db
from backend.core.errors import ApiError
from backend.security.context import require_context
from backend.security.visibility import Scope

PAGE_CHARACTERS = 4096


def _current_page(conn: db.Connection, material_id: str, source_version: str | None, offset: int) -> dict:
    row = conn.execute(
        "SELECT v.id AS source_version,v.version,CHAR_LENGTH(v.text) AS total,SUBSTRING(v.text,?,?) AS text,"
        "v.sha256=SHA2(v.text,256) AS verified FROM source_versions v WHERE v.material_id=? "
        "ORDER BY v.version DESC LIMIT 1",
        (offset + 1, PAGE_CHARACTERS, material_id),
    ).fetchone()
    if row is None:
        # Some original materials predate the immutable version table. Identify the actual saved
        # text by content so subsequent requests can pin it without creating a version on read.
        row = conn.execute(
            "SELECT CONCAT('sha256:',SHA2(original,256)) AS source_version,NULL AS version,"
            "CHAR_LENGTH(original) AS total,SUBSTRING(original,?,?) AS text,1 AS verified FROM "
            "(SELECT e.payload->>'$.original_text' AS original FROM entities e "
            "WHERE e.id=? AND JSON_TYPE(e.payload->'$.original_text')='STRING') saved",
            (offset + 1, PAGE_CHARACTERS, material_id),
        ).fetchone()
    if not row or not row["verified"] or (source_version is not None and row["source_version"] != source_version):
        raise ApiError("NOT_FOUND")
    return {key: row[key] for key in ("source_version", "version", "text", "total")} | {"locator": "原件全文"}


def read_page(
    conn: db.Connection,
    scope: Scope,
    material_id: str,
    *,
    offset: int,
    chunk_id: str | None = None,
    anchor_id: str | None = None,
    source_version: str | None = None,
    citing_entity_id: str | None = None,
    citing_relation_id: str | None = None,
) -> dict:
    if scope.connection is not conn or scope.principal != require_context():
        raise ApiError("PERMISSION_DENIED")
    if scope.metadata_only:
        raise ApiError("NOT_FOUND")
    where, params = scope.predicate()
    material = conn.execute(
        f"SELECT e.id,e.title,e.status FROM entities e WHERE {where} AND e.id=? AND e.kind='material'",
        (*params, material_id),
    ).fetchone()
    if not material:
        raise ApiError("NOT_FOUND")
    if chunk_id or anchor_id or citing_entity_id or citing_relation_id:
        from backend.kernel.semantic_evidence import readable_anchor

        anchor = readable_anchor(
            conn,
            scope,
            material_id,
            chunk_id=chunk_id,
            anchor_id=anchor_id,
            source_version=source_version,
            citing_entity_id=citing_entity_id,
            citing_relation_id=citing_relation_id,
        )
        page = {
            "source_version": anchor["source_version"],
            "version": anchor["version"],
            "locator": anchor["locator"],
            "text": anchor["quote"][offset : offset + PAGE_CHARACTERS],
            "total": anchor["end"] - anchor["start"],
        }
    else:
        page = _current_page(conn, material_id, source_version, offset)
    if offset > page["total"]:
        raise ApiError("VALIDATION_FAILED", "阅读位置超出所选原文。")
    return {
        **page,
        "material_id": material["id"],
        "title": material["title"],
        "status": material["status"],
        "offset": offset,
    }
