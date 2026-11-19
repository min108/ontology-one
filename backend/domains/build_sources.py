"""Keep the source paragraphs that a recipe could not turn into proposal items."""

import json

from backend import db, provenance


def unformed_chunks(material: dict, chunks: list[dict]) -> dict[str, str]:
    payload = material["payload"]
    represented = {cid for candidate in payload.get("candidates", []) for cid in candidate.get("source_chunk_ids", [])}
    missing = {}
    for chunk in chunks:
        cid = chunk["id"]
        coverage = (payload.get("coverage") or {}).get(cid, {})
        if (
            chunk["content"].strip()
            and coverage.get("mode") == "online"
            and (coverage.get("discarded_source") or coverage.get("status") == "needs_review" or cid not in represented)
        ):
            missing[cid] = "invalid_quote" if coverage.get("discarded_source") else "uncovered"
    return missing


def proposal_sources(conn: db.Connection, proposal: dict, source: dict, items: list[dict]) -> list[dict]:
    """Resolve only the proposal's own immutable reading layout, also for A.1 rows."""
    ref = proposal["source_refs"][0]
    stats = proposal.get("stats") or {}
    missing = stats.get("unformed_chunks")
    if missing is None:
        # Older runs kept only the count. Recover demonstrably uncovered paragraphs
        # from the reading version at proposal creation. Do not guess the source of
        # an invalid quote from a newer reading layout or from the model's title.
        if ref.get("source_version") != source["id"] or not any(item["content_origin"] == "source" for item in items):
            return []
        version = conn.execute(
            "SELECT segments FROM reading_versions WHERE material_id=? AND source_version=? "
            "AND created_at<=? ORDER BY version DESC LIMIT 1",
            (ref["material_id"], source["id"], proposal["created_at"]),
        ).fetchone()
        if not version:
            return []
        represented = {q.get("chunk_id") for item in items for q in (item.get("evidence") or [])}
        missing = {
            part["chunk_id"]: "uncovered"
            for part in json.loads(version["segments"])
            if part.get("chunk_id") and part["chunk_id"] not in represented
        }
    sources = provenance.chunk_refs_for_version(conn, ref["material_id"], source, missing)
    for gap in sources:
        gap["unformed_reason"] = missing[gap["chunk_id"]]
    return sorted(sources, key=lambda gap: gap["unformed_reason"] != "invalid_quote")
