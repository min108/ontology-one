"""Deterministic tenant search projection. The caller owns the MySQL transaction."""

import hashlib
import json
import re
from dataclasses import dataclass, field
from itertools import batched

from backend import db
from backend.kernel.cards import build_card
from backend.kernel.kinds import persisted_kind
from backend.security.reference_fields import own_card_payload

EVIDENCE_SEPARATOR = "\n\n已核对的原文引文\n"
_COLUMNS = "doc_id,doc_type,entity_id,kind,pillar,module_id,status,title,aliases,body,updated_at"
_ENTITY_SELECT = (
    "SELECT e.*,m.module_id,m.valid_from,m.valid_to FROM entities e LEFT JOIN entity_meta m ON m.entity_id=e.id "
)
_PRIVATE_KINDS = {"artifact", "report"}
_KEYS = {
    "entities": ("id", 0),
    "entity_meta": ("entity_id", 0),
    "chunks": ("material_id", 1),
    "source_versions": ("material_id", 1),
    "reading_versions": ("material_id", 1),
    "source_anchors": ("material_id", 1),
    "chunk_anchors": ("chunk_id", 0),
}
_MUTATION = re.compile(
    r"^\s*(insert(?:\s+(?:or\s+)?(?:ignore|replace))?\s+into|replace\s+into|update(?:\s+ignore)?|delete\s+from)\s+"
    r"`?(entities|entity_meta|chunks|source_versions|reading_versions|source_anchors|chunk_anchors)\b`?",
    re.I,
)
_VALUES = re.compile(r"\s*(?:\(([^()]*)\))?\s*VALUES\s*\(([^()]*)\)\s*(?:ON\s+DUPLICATE\s+KEY.*)?$", re.I | re.S)


@dataclass
class SearchChanges:
    """Track existing uniform INSERT/UPDATE statements. Unbounded operations require reconciliation."""

    entities: set[str] = field(default_factory=set)
    chunks: set[str] = field(default_factory=set)
    removed_owners: set[str] = field(default_factory=set)
    full: bool = False

    def observe(self, sql: str, params: tuple) -> None:
        match = _MUTATION.match(sql)
        if self.full or match is None:
            return
        verb, table = match.group(1).lower(), match.group(2).lower()
        column, position = _KEYS[table]
        suffix = sql[match.end() :]
        indices = []
        chunk_key = table == "chunk_anchors"
        if verb.startswith(("insert", "replace")):
            values = _VALUES.fullmatch(suffix)
            if values:
                if values[1]:
                    fields = [name.strip().strip("`").lower() for name in values[1].split(",")]
                    position = fields.index(column) if column in fields else -1
                slots = [s.strip() for s in values[2].split(",")]
                if 0 <= position < len(slots) and slots[position] == "?":
                    indices = [sum(token.count("?") for token in slots[:position])]
        else:
            where = re.search(r"\bWHERE\b", suffix, re.I)
            if where and not re.search(r"\b(?:OR|SELECT)\b", suffix[where.end() :], re.I):
                columns = "(material_id|id)" if table == "chunks" else f"({column})"
                key = re.search(
                    rf"\b{columns}\b`?\s*(?:=\s*\?|IN\s*\((?:\?\s*,\s*)*\?\))",
                    suffix[where.end() :],
                    re.I,
                )
                if key and table == "chunks":
                    chunk_key = key[1].lower() == "id"
                if key:
                    offset = suffix[: where.end() + key.start()].count("?")
                    indices = range(offset, offset + key[0].count("?"))
        if not indices or any(i >= len(params) or not isinstance(params[i], str) for i in indices):
            self.full = True
            return
        keys = {params[i] for i in indices}
        (self.chunks if chunk_key else self.entities).update(keys)
        if not chunk_key and (
            (verb.startswith("delete") and table in {"entities", "chunks"})
            or (table == "entities" and ("replace" in verb or re.search(r"\bkind\s*=", suffix, re.I)))
        ):
            self.removed_owners.update(keys)


def chunk_doc_id(ident: str) -> str:
    if len(ident) <= 194:
        return "chunk:" + ident
    return "chunk-sha256:" + hashlib.sha256(ident.encode()).hexdigest()


def chunk_doc_id_sql(column: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\.id", column):
        raise ValueError("Expected a qualified internal chunk id")
    return f"IF(CHAR_LENGTH({column})<=194,CONCAT('chunk:',{column}),CONCAT('chunk-sha256:',SHA2({column},256)))"


def _payload(entity: dict) -> dict:
    value = entity.get("payload")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def public_card_text(entity: dict, meta: dict | None = None) -> str:
    # The single index serves identities with different source and draft permissions.
    # Rebuild the own-content card without cached neighbouring titles or unverified quotations.
    payload = own_card_payload(_payload(entity))
    card = {
        **entity,
        "kind": persisted_kind(entity["kind"]).kind,
        "payload": payload,
        "_meta": meta or {},
        "relations": {},
    }
    try:
        return build_card(card)["text"]
    except (AttributeError, TypeError, ValueError):
        # Historic invalid structured fields must not prevent the workspace from upgrading.
        return build_card({**card, "payload": {}})["text"]


def _aliases(payload: dict) -> str:
    raw = payload.get("aliases")
    raw = [raw] if isinstance(raw, str) else raw if isinstance(raw, list) else []
    result = {}
    remaining = 65535  # MySQL TEXT limit, in bytes.
    for value in raw:
        if not isinstance(value, str):
            continue
        alias = " ".join(value.split())
        if not alias or alias in result:
            continue
        size = len(alias.encode()) + bool(result)
        if size <= remaining:
            result[alias] = None
            remaining -= size
    return "\n".join(result)


def _mediumtext(value: str) -> str:
    # MySQL MEDIUMTEXT is limited by bytes; keep the matching Unicode prefix.
    limit = 16_777_215
    if len(value) <= limit // 4:
        return value
    return value.encode()[:limit].decode(errors="ignore")


def verified_quotes(conn: db.Connection, payload: dict, *, locking: bool = False) -> list[str]:
    """Return only verbatim references to the current, available local original."""
    refs = payload.get("source_refs")
    result = []
    current_sources = {}
    lock = " FOR SHARE" if locking else ""
    for ref in refs if isinstance(refs, list) else []:
        if not isinstance(ref, dict) or ref.get("kind", "anchor") != "anchor":
            continue
        material, quote = ref.get("material_id"), ref.get("quote")
        if not isinstance(material, str) or not isinstance(quote, str) or not quote.strip() or quote in result:
            continue
        if material not in current_sources:
            current_sources[material] = conn.execute(
                "SELECT v.id,v.text FROM entities e JOIN source_versions v ON v.material_id=e.id "
                "WHERE e.id=? AND e.kind='material' AND e.status NOT IN ('archived','discarded','withdrawn') "
                "AND COALESCE(json_extract(e.payload,'$.active_asset'),1)<>0 ORDER BY v.version DESC LIMIT 1" + lock,
                (material,),
            ).fetchone()
        source = current_sources[material]
        if not source or any(ref.get(key) and ref[key] != source["id"] for key in ("source_version", "revision_id")):
            continue
        piece = None
        if ref.get("chunk_id"):
            if not isinstance(ref["chunk_id"], str):
                continue
            piece = conn.execute(
                "SELECT a.id,a.start,a.end FROM chunks c JOIN chunk_anchors ca ON ca.chunk_id=c.id "
                "JOIN source_anchors a ON a.id=ca.anchor_id WHERE c.id=? AND c.material_id=? AND "
                "a.material_id=c.material_id AND a.source_version=?" + lock,
                (ref["chunk_id"], material, source["id"]),
            ).fetchone()
            if not piece:
                continue
        anchor = ref.get("anchor_id") or ref.get("span_id") or (piece and piece["id"])
        row = (
            conn.execute(
                "SELECT id,start,end,quote_sha256 FROM source_anchors WHERE id=? AND material_id=? AND source_version=?"
                + lock,
                (anchor, material, source["id"]),
            ).fetchone()
            if isinstance(anchor, str)
            else None
        )
        if not row or (ref.get("span_id") and ref["span_id"] != row["id"]):
            continue
        start, end = row["start"], row["end"]
        if (
            not 0 <= start < end <= len(source["text"])
            or (piece and not piece["start"] <= start < end <= piece["end"])
            or ref.get("start", start) != start
            or ref.get("end", end) != end
            or ("span" in ref and ref["span"] != {"start": start, "end": end})
            or source["text"][start:end] != quote
            or hashlib.sha256(quote.encode()).hexdigest() != row["quote_sha256"]
        ):
            continue
        result.append(quote)
    return result


def _element(conn: db.Connection, row: dict, *, locking: bool) -> tuple:
    kind = persisted_kind(row["kind"])
    payload = _payload(row)
    body = public_card_text(row, row).replace(EVIDENCE_SEPARATOR, EVIDENCE_SEPARATOR.strip("\n"))
    quotes = verified_quotes(conn, payload, locking=locking)
    if quotes:
        body += EVIDENCE_SEPARATOR + "\n".join(quotes)
    module = row.get("module_id") or ("MOD-DEFAULT" if kind.module_eligible else None)
    return (
        "el:" + row["id"],
        "element",
        row["id"],
        kind.kind,
        kind.pillar,
        module,
        row["status"],
        (row["title"] or "")[:500],
        _aliases(payload),
        _mediumtext(body),
        row["updated_at"] or row["created_at"] or "",
    )


def _chunks(conn: db.Connection, material: dict, *, locking: bool) -> list[tuple]:
    lock = " FOR SHARE" if locking else ""
    current = conn.execute(
        "SELECT s.id,s.text,r.segments,r.created_at FROM source_versions s LEFT JOIN reading_versions r "
        "ON r.material_id=s.material_id AND r.source_version=s.id WHERE s.material_id=? "
        "ORDER BY s.version DESC,r.version DESC LIMIT 1" + lock,
        (material["id"],),
    ).fetchone()
    if not current or not current["segments"]:
        return []
    try:
        segments = json.loads(current["segments"])
    except (ValueError, TypeError):
        return []
    if not isinstance(segments, list):
        return []
    selected = {s["chunk_id"]: s for s in segments if isinstance(s, dict) and isinstance(s.get("chunk_id"), str)}
    result = []
    for ids in batched(sorted(selected), 400):
        rows = conn.execute(
            "SELECT ch.id,ch.content,a.id anchor_id,a.start,a.end,a.quote_sha256 FROM chunks ch "
            "JOIN chunk_anchors ca ON ca.chunk_id=ch.id JOIN source_anchors a ON a.id=ca.anchor_id "
            "WHERE ch.material_id=? AND a.material_id=ch.material_id AND a.source_version=? AND ch.id IN ("
            + ",".join("?" for _ in ids)
            + ") ORDER BY ch.id"
            + lock,
            (material["id"], current["id"], *ids),
        )
        for row in rows:
            seg = selected[row["id"]]
            start, end, content = row["start"], row["end"], row["content"]
            if (
                not isinstance(content, str)
                or not content.strip()
                or not 0 <= start < end <= len(current["text"])
                or (seg.get("anchor_id") and seg["anchor_id"] != row["anchor_id"])
                or seg.get("start", start) != start
                or seg.get("end", end) != end
                or current["text"][start:end] != content
                or hashlib.sha256(content.encode()).hexdigest() != row["quote_sha256"]
            ):
                continue
            result.append(
                (
                    chunk_doc_id(row["id"]),
                    "chunk",
                    material["id"],
                    "material",
                    "source",
                    None,
                    material["status"],
                    (material["title"] or "")[:500],
                    "",
                    _mediumtext(content),
                    current["created_at"] or material["updated_at"] or "",
                )
            )
    return result


def _store_rows(conn: db.Connection, rows: list[tuple]) -> None:
    for batch in batched(rows, 250):
        conn.executemany(
            f"INSERT INTO search_index({_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON DUPLICATE KEY UPDATE "
            + ",".join(f"{name}=VALUES({name})" for name in _COLUMNS.split(",")[1:]),
            batch,
        )


def _documents(conn: db.Connection, rows: list, *, locking: bool = False) -> tuple[list[tuple], list[tuple]]:
    elements, chunks = [], []
    for row in rows:
        if row["kind"] in _PRIVATE_KINDS:
            continue
        elements.append(_element(conn, row, locking=locking))
        if row["kind"] == "material":
            chunks.extend(_chunks(conn, row, locking=locking))
    return elements, chunks


def rebuild_index(conn: db.Connection) -> dict[str, int]:
    """Replace the cache atomically from tenant facts, using stable business timestamps."""
    conn.lock_search_rebuild()
    conn.execute("SELECT meta_key FROM workspace_meta WHERE meta_key='schema_version' FOR UPDATE").fetchone()
    conn.execute("DELETE FROM search_index")
    counts = {"element_count": 0, "chunk_count": 0}
    after = None
    while True:
        rows = conn.execute(
            _ENTITY_SELECT + ("WHERE e.id>? " if after is not None else "") + "ORDER BY e.id LIMIT 250 FOR SHARE",
            (after,) if after is not None else (),
        ).fetchall()
        if not rows:
            conn.search_projection_rebuilt()
            return counts
        elements, chunks = _documents(conn, rows, locking=True)
        _store_rows(conn, elements + chunks)
        counts["element_count"] += len(elements)
        counts["chunk_count"] += len(chunks)
        after = rows[-1]["id"]


def _committed_referrers(conn: db.Connection, materials: set[str]) -> set[str]:
    """Discover committed candidate ids without retaining table-wide InnoDB locks.

    A writer can already hold an old REPEATABLE READ snapshot. The separately
    bounded control pool supplies a fresh, nonlocking read of *ids only*, in the
    physical schema selected on the writer's connection. Its own uncommitted
    entities are already in SearchChanges. All projection content is read under
    current row locks, and written, on the original business transaction.
    """
    from backend.infra.migrations import database_name
    from backend.security import store

    database = database_name(conn.execute("SELECT DATABASE()").fetchone()[0])
    result = set()
    with store.control_connection() as reader, reader.cursor() as cursor:
        for batch in batched(sorted(materials), 400):
            cursor.execute(
                f"SELECT id FROM `{database}`.entities WHERE JSON_OVERLAPS("
                "JSON_EXTRACT(IF(JSON_VALID(payload),payload,'{}'),'$.source_refs[*].material_id'),CAST(%s AS JSON))",
                (json.dumps(batch),),
            )
            result.update(row["id"] for row in cursor)
    return result


def sync_changes(conn: db.Connection, changes: SearchChanges) -> None:
    ids = set(changes.entities)
    materials = set()
    if changes.full:
        # Unbounded legacy writes already hold row locks. Reconcile here under their
        # shared gate; upgrading it while another writer waits on those rows can deadlock.
        ids.update(r["id"] for r in conn.execute("SELECT id FROM entities FOR SHARE"))
        for row in conn.execute("SELECT entity_id,kind FROM search_index WHERE entity_id IS NOT NULL FOR SHARE"):
            ids.add(row["entity_id"])
            if row["kind"] == "material":
                materials.add(row["entity_id"])
    for batch in batched(sorted(changes.chunks), 400):
        live = {
            row["id"]: row["material_id"]
            for row in conn.execute(
                "SELECT id,material_id FROM chunks WHERE id IN (" + ",".join("?" for _ in batch) + ") FOR SHARE", batch
            )
        }
        ids.update(live.values())
        removed = [chunk_doc_id(key) for key in batch if key not in live]
        if removed:
            slots = ",".join("?" for _ in removed)
            ids.update(
                row["entity_id"]
                for row in conn.execute(
                    "SELECT entity_id FROM search_index WHERE doc_id IN (" + slots + ") FOR SHARE", removed
                )
            )
            conn.execute("DELETE FROM search_index WHERE doc_id IN (" + slots + ")", removed)
    for batch in batched(sorted(ids), 400):
        values = ",".join("?" for _ in batch)
        materials.update(
            r["id"]
            for r in conn.execute(
                "SELECT id FROM entities WHERE kind='material' AND id IN (" + values + ") FOR SHARE", batch
            )
        )
    for batch in batched(sorted(changes.removed_owners), 400):
        materials.update(
            r["entity_id"]
            for r in conn.execute(
                "SELECT entity_id FROM search_index WHERE kind='material' AND doc_id IN ("
                + ",".join("?" for _ in batch)
                + ") FOR SHARE",
                tuple("el:" + i for i in batch),
            )
        )
    if materials and not changes.full:
        ids.update(_committed_referrers(conn, materials))
    for batch in batched(sorted(ids), 400):
        slots = ",".join("?" for _ in batch)
        rows = conn.execute(_ENTITY_SELECT + "WHERE e.id IN (" + slots + ") ORDER BY e.id FOR SHARE", batch).fetchall()
        elements, chunks = _documents(conn, rows, locking=True)
        missing = {"el:" + i for i in batch} - {r[0] for r in elements}
        if missing:
            conn.execute(
                "DELETE FROM search_index WHERE doc_id IN (" + ",".join("?" for _ in missing) + ")", sorted(missing)
            )
        affected = set(batch) & materials
        if affected:
            # Confirmed readings are immutable. Their original chunk ids give indexed
            # primary-key deletes without locking unrelated documents in a full table scan.
            slots = ",".join("?" for _ in affected)
            known = {
                chunk_doc_id(r["id"])
                for r in conn.execute(
                    "SELECT id FROM chunks WHERE material_id IN (" + slots + ") FOR SHARE", sorted(affected)
                )
            }
            removed = affected if changes.full else affected & changes.removed_owners
            if removed:
                known.update(
                    r["doc_id"]
                    for r in conn.execute(
                        "SELECT doc_id FROM search_index WHERE kind='material' AND doc_type='chunk' AND entity_id IN ("
                        + ",".join("?" for _ in removed)
                        + ") FOR SHARE",
                        sorted(removed),
                    )
                )
            stale = known - {r[0] for r in chunks}
            for keys in batched(sorted(stale), 400):
                conn.execute("DELETE FROM search_index WHERE doc_id IN (" + ",".join("?" for _ in keys) + ")", keys)
        _store_rows(conn, elements + chunks)
