"""Tenant-local lexical retrieval over the versioned MySQL ngram projection."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from urllib.parse import quote, urlencode

from backend import db
from backend.ai import _FACET_EQUIVALENTS, _STOP, _query_facets
from backend.infra.search_index import EVIDENCE_SEPARATOR, chunk_doc_id, chunk_doc_id_sql
from backend.security.visibility import Scope, federation_scope, scope_for, visible_entities

_FACETS = ("kind", "status", "source_kind", "domain")
_FIELDS = {
    "kind": "i.kind",
    "pillar": "i.pillar",
    "module_id": "i.module_id",
    "status": "e.status",
    "domain": "e.domain",
    "org_id": "e.org_id",
    "source_kind": "e.source_kind",
}
_FROM = " FROM search_index i JOIN entities e ON e.id=i.entity_id "
_ENTITY_FIELDS = (
    "e.id,e.kind,e.title,e.summary,e.domain,e.org_id,e.status,e.source_kind,e.created_at,e.updated_at,e.version"
)


@dataclass(frozen=True)
class Query:
    original: str
    text: str
    terms: tuple[str, ...]
    facets: tuple[tuple[str, ...], ...]

    @classmethod
    def parse(cls, q: str) -> Query:
        text = q.strip().casefold()
        question = bool(_STOP.search(text) or re.search(r"[?？]", text))
        facets = _query_facets(text) if question else ()
        terms = tuple(dict.fromkeys(word for word in text.split() if len(word) > 1))
        return cls(q.strip(), text, terms, tuple(tuple(_FACET_EQUIVALENTS.get(facet, (facet,))) for facet in facets))

    def fulltext(self) -> str:
        return " ".join([self.text, *(term for group in self.facets for term in group if term not in self.text)])


def _filters(
    scope: Scope, filters: dict[str, str], history: str, chunks: bool, asset_only: bool, exclude_root: str = ""
) -> tuple[str, list]:
    where, args = scope.predicate(history=history)
    clauses, params = [where], list(args)
    clauses.append(
        "i.doc_type IN ('element','chunk')" if chunks and not scope.metadata_only else "i.doc_type='element'"
    )
    if asset_only:
        clauses.append("i.pillar='asset'")
    if exclude_root:
        clauses.append("NOT (e.kind='organization' AND e.id=?)")
        params.append(exclude_root)
    for field, values in filters.items():
        if not values:
            continue
        words = list(dict.fromkeys(value.strip() for value in values.split(",")))
        clauses.append(f"{_FIELDS[field]} IN ({','.join('?' for _ in words)})")
        params.extend(words)
    return " AND ".join(clauses), params


def _base_columns(scope: Scope) -> str:
    # The node metadata is read from the live entity, including its status.
    # The canonical index supplies the document identity, aliases and card text.
    fields = _ENTITY_FIELDS + ",i.doc_id,i.doc_type,i.entity_id,i.pillar,i.module_id"
    if scope.metadata_only:
        fields += (
            ",COALESCE((SELECT a.visibility FROM entity_access a WHERE a.entity_id=e.id),'institution') visibility"
        )
    return fields


def _empty_page(scope: Scope, where: str, args: list, offset: int, limit: int, *, original: bool) -> dict:
    conn = scope.connection
    total = conn.execute("SELECT count(*)" + _FROM + "WHERE " + where, args).fetchone()[0]
    rows = []
    if limit and offset < total:
        order = "e.id" if scope.metadata_only else "e.updated_at DESC,e.id"
        fields = _base_columns(scope)
        rows = conn.execute(
            f"SELECT {fields}" + _FROM + f"WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?", (*args, limit, offset)
        ).fetchall()
    return {"items": _project(scope, rows, Query.parse(""), original=original), "total": total}


def _candidates(scope: Scope, query: Query, where: str, args: list) -> list:
    columns = _base_columns(scope)
    extra_join = ""
    params = []
    if scope.metadata_only:
        # Federated and external discovery must not match or rank a hidden
        # body/alias. In these contexts only their existing metadata is indexed
        # for the request. Federation also exposes the domain label.
        columns += ",'' aliases,e.summary body,0 title_ft,0 body_ft,0 regulation"
        fields = ["e.title", "e.id", "e.summary"]
        if not scope.external_tool:
            fields.append("e.domain")
        signals = {query.text}
        if len(query.terms) >= 2:
            signals.update(query.terms)
        signals.update(term for group in query.facets for term in group)
        match_fields = " OR ".join(f"LOCATE(?,COALESCE({field},''))>0" for field in fields)
        candidates = " OR ".join(match_fields for _ in signals)
        terms = [term for term in sorted(signals) for _ in fields]
    else:
        title_ft = "MATCH(i.title,i.aliases) AGAINST (? IN NATURAL LANGUAGE MODE)"
        body_ft = "MATCH(i.body) AGAINST (? IN NATURAL LANGUAGE MODE)"
        fulltext = query.fulltext()
        body = "i.body"
        if not scope.drafts:
            # Evidence tails can cite intake material still in draft. The own
            # content section remains available for its published element.
            body = "CASE WHEN i.doc_type='element' THEN SUBSTRING_INDEX(i.body,?,1) ELSE i.body END"
            # Only candidate elements with a cached evidence tail need to
            # resolve their limited set of live, visible citation sources.
            columns += (
                ",CASE WHEN i.doc_type='element' AND LOCATE(?,i.body)>0 "
                "THEN json_extract(e.payload,'$.source_refs') END evidence_refs"
            )
            params.extend((EVIDENCE_SEPARATOR, EVIDENCE_SEPARATOR))
        columns += f",i.aliases,{body} body,"
        if scope.drafts:
            columns += f"{title_ft} title_ft,{body_ft} body_ft,"
            params.extend((fulltext, fulltext))
        else:
            # Raw document frequencies and scores could reflect a hidden source.
            columns += f"0 title_ft,0 body_ft,({title_ft} OR {body_ft}) native_signal,"
            params.extend((fulltext, fulltext))
        columns += (
            "(COALESCE(m.subtype,e.source_kind)='regulation' AND COALESCE(e.source_kind,'')!='demo' "
            "AND COALESCE(json_extract(e.payload,'$.is_demo'),'false') NOT IN ('1','true')) regulation"
        )
        extra_join = "LEFT JOIN entity_meta m ON m.entity_id=e.id "
        candidates = f"{title_ft} OR {body_ft} OR i.doc_id IN (?,?,?) OR LOCATE(?,i.title)>0 OR LOCATE(?,i.aliases)>0"
        terms = [
            fulltext,
            fulltext,
            "el:" + query.original,
            chunk_doc_id(query.original),
            query.original,
            query.text,
            query.text,
        ]
    if len(query.text) == 1:
        candidates, terms = "LEFT(e.title,1)=?", [query.text]
    sql = f"SELECT {columns}{_FROM}{extra_join}WHERE {where} AND ({candidates})"
    rows = scope.connection.execute(sql, (*params, *args, *terms)).fetchall()
    if not scope.drafts and not scope.metadata_only:
        _add_visible_evidence(scope, rows)
    return rows


def _add_visible_evidence(scope: Scope, rows: list) -> None:
    from backend.infra.search_index import verified_quotes

    references = {}
    materials = set()
    for row in rows:
        encoded = row.get("evidence_refs")
        refs = json.loads(encoded) if encoded else []
        if not isinstance(refs, list):
            continue
        anchors = [r for r in refs if isinstance(r, dict) and isinstance(r.get("material_id"), str)]
        if anchors:
            references[row["doc_id"]] = anchors
            materials.update(r["material_id"] for r in anchors)
    visible = visible_entities(scope.connection, materials, scope=scope) if materials else {}
    for row in rows:
        refs = [r for r in references.get(row["doc_id"], []) if r["material_id"] in visible]
        if refs:
            quotes = verified_quotes(scope.connection, {"source_refs": refs})
            if quotes:
                row["body"] += EVIDENCE_SEPARATOR + "\n".join(quotes)


def _rank(row: dict, query: Query, scope: Scope) -> tuple[float, str] | None:
    q, title = query.text, (row["title"] or "").casefold()
    is_element = row["doc_type"] == "element"
    aliases = tuple(alias.casefold() for alias in (row["aliases"] or "").splitlines())
    body = (row["body"] or "").casefold()
    if scope.metadata_only and not scope.external_tool:
        body += "\n" + (row["domain"] or "").casefold()
    if len(q) == 1:
        if not is_element or not title.startswith(q):
            return None
        score, match = 30, "prefix"
    elif (
        (is_element and q == row["id"].casefold())
        or q == row["doc_id"].casefold()
        or (not is_element and chunk_doc_id(query.original).casefold() == row["doc_id"].casefold())
    ):
        score, match = 120, "id"
    elif is_element and q in aliases:
        score, match = 96, "alias"
    elif is_element and q == title:
        score, match = 92, "title"
    elif is_element and (q in title or any(q in alias for alias in aliases)):
        prefix = title.startswith(q) or any(alias.startswith(q) for alias in aliases)
        score, match = 64, ("prefix" if prefix else "partial")
    elif q in body:
        score, match = 36, "text"
    elif scope.metadata_only and q in row["id"].casefold():
        score, match = 31, "text"
    elif query.facets:
        # The first line of a business card repeats its title and id. An
        # otherwise unrelated clause cannot answer a question from that header.
        evidence = body.partition("\n")[2] if is_element and body.startswith("【") else body
        if not all(any(term in evidence for term in group) for group in query.facets):
            return None
        score, match = 25, "text"
    elif len(query.terms) >= 2 and all(
        term in ("\n".join((title, *aliases, body)) if is_element else body) for term in query.terms
    ):
        score, match = 24, "text"
    else:
        return None

    # The lexical signal uses §4.12's 3:1 weighting; a bounded monotone
    # transform preserves explicit exact-match tiers across corpus growth.
    lexical = 3 * float(row["title_ft"] or 0) + float(row["body_ft"] or 0)
    score += min(10.0, math.log1p(lexical))
    score += 3.0 * (row["status"] == "published") + 1.5 * bool(row["regulation"])
    return round(score, 7), match


def _ranked(scope: Scope, query: Query, where: str, args: list) -> list[dict]:
    ranked, prefix = [], []
    any_native = False
    for row in _candidates(scope, query, where, args):
        rank = _rank(row, query, scope)
        if rank is None:
            continue
        row["score"], row["match"] = rank
        native = bool(row["title_ft"] or row["body_ft"] or row.get("native_signal"))
        if native:
            any_native = True
        if (
            len(query.text) >= 2
            and not scope.metadata_only
            and not native
            and row["match"] not in {"id", "alias", "title"}
        ):
            # MySQL can omit stopwords and punctuation completely. In that
            # case a literal occurrence inside a name/body is not an FT hit.
            if row["doc_type"] == "element" and (row["title"] or "").casefold().startswith(query.text):
                row["match"] = "prefix"
                prefix.append(row)
        else:
            ranked.append(row)
    if not any_native:
        ranked.extend(prefix)
    ranked.sort(key=lambda row: (-row["score"], row["doc_id"]))
    # Bound each material on the entire ordered result set before totals,
    # offsets, category facets and old/new presentation projections.
    counts = Counter()
    result = []
    for row in ranked:
        if row["doc_type"] == "chunk":
            counts[row["entity_id"]] += 1
            if counts[row["entity_id"]] > 2:
                continue
        result.append(row)
    # Within comparable content matches, let another relevant category
    # contribute before additional near-duplicates from the first category.
    # Exact names and IDs retain their full lexical score.
    categories = Counter()
    for row in result:
        if row["match"] == "text":
            category = (row["doc_type"], row["kind"])
            row["score"] -= min(categories[category], 3) * 1.25
            categories[category] += 1
    return sorted(result, key=lambda row: (-row["score"], row["doc_id"]))


def _highlight(row: dict, query: Query) -> str:
    title = row["title"] or ""
    body = row.get("body") or row.get("summary") or title
    source = body
    position = body.casefold().find(query.text)
    if position < 0 and query.text in title.casefold():
        source, position = title, title.casefold().index(query.text)
    if position < 0 and row.get("match") == "alias":
        source, position = next((a for a in row["aliases"].splitlines() if a.casefold() == query.text), title), 0
    start = max(0, position - 28)
    return source[start : start + 160].strip()


def _project(scope: Scope, rows: list, query: Query, *, original: bool) -> list[dict]:
    result = []
    entity_ids = [row["entity_id"] for row in rows if row["doc_type"] == "element"]
    payloads = {}
    if entity_ids and original and scope.drafts and not scope.metadata_only:
        entities = visible_entities(scope.connection, entity_ids, scope=scope, history="all")
        payloads = {ident: entity["payload"] for ident, entity in entities.items()}
    long_ids = [row["doc_id"] for row in rows if row["doc_id"].startswith("chunk-sha256:")]
    chunk_ids = {}
    if long_ids:
        doc_id = chunk_doc_id_sql("c.id")
        marks = ",".join("?" for _ in long_ids)
        chunk_ids = {
            r["doc_id"]: r["id"]
            for r in scope.connection.execute(
                f"SELECT c.id,{doc_id} doc_id FROM chunks c WHERE {doc_id} IN ({marks})", long_ids
            )
        }
    for row in rows:
        element = row["doc_type"] == "element"
        entity_id, doc_id = row["entity_id"], row["doc_id"]
        chunk_id = None if element else chunk_ids.get(doc_id, doc_id.removeprefix("chunk:"))
        ident = entity_id if element else chunk_id
        href = ("/ontology/elements/" if element else "/materials/") + quote(entity_id, safe="")
        if not element:
            href += "?" + urlencode({"tab": "source", "chunk": chunk_id})
        item = {
            key: row[key]
            for key in (
                "kind",
                "title",
                "summary",
                "domain",
                "org_id",
                "status",
                "source_kind",
                "created_at",
                "updated_at",
                "version",
            )
        }
        item.update(
            id=ident,
            doc_id=doc_id,
            doc_type=row["doc_type"],
            entity_id=entity_id,
            chunk_id=chunk_id,
            pillar=row["pillar"],
            module_id=row["module_id"],
            href=href,
            score=row.get("score", 0),
            match=row.get("match", "text"),
            highlight=_highlight(row, query),
            owner_tenant_id=scope.tenant_id,
            owner_org_id=scope.tenant_id,
        )
        if not element:
            item["summary"] = item["highlight"]
        if original:
            item["payload"] = (
                payloads.get(entity_id, {}) if element else {"material_id": entity_id, "chunk_id": chunk_id}
            )
        if "visibility" in row:
            item["visibility"] = row["visibility"]
        result.append(item)
    return result


def _page(
    scope: Scope,
    q: str,
    filters: dict[str, str],
    *,
    offset: int,
    limit: int,
    include_chunks: bool,
    history: str = "current",
    asset_only: bool = False,
    original: bool = False,
    exclude_root: str = "",
) -> dict:
    query = Query.parse(q)
    where, args = _filters(scope, filters, history, bool(query.text) and include_chunks, asset_only, exclude_root)
    if not query.text:
        return _empty_page(scope, where, args, offset, limit, original=original)
    rows = _ranked(scope, query, where, args)
    return {"items": _project(scope, rows[offset : offset + limit], query, original=original), "total": len(rows)}


def search(
    conn: db.Connection,
    q: str = "",
    *,
    kind: str = "",
    pillar: str = "",
    module_id: str = "",
    status: str = "",
    domain: str = "",
    org_id: str = "",
    source_kind: str = "",
    history: str = "current",
    asset_only: bool = False,
    include_chunks: bool = True,
    page: int = 1,
    page_size: int = 25,
    legacy_payload: bool = False,
    published_only: bool = False,
    metadata_only: bool = False,
) -> dict:
    scope = scope_for(conn, published_only=published_only, metadata_only=metadata_only)
    page, page_size = max(1, page), max(1, min(200, page_size))
    filters = {
        "kind": kind,
        "pillar": pillar,
        "module_id": module_id,
        "status": status,
        "domain": domain,
        "org_id": org_id,
        "source_kind": source_kind,
    }
    result = _page(
        scope,
        q,
        filters,
        offset=(page - 1) * page_size,
        limit=page_size,
        include_chunks=include_chunks,
        history=history,
        asset_only=asset_only,
        original=legacy_payload,
    )
    return {**result, "page": page, "page_size": page_size}


def search_federated(
    conn: db.Connection,
    tenant: dict,
    *,
    q: str = "",
    kind: str = "",
    status: str = "",
    offset: int = 0,
    limit: int = 25,
    exclude_root: bool = False,
) -> dict:
    scope = federation_scope(conn, tenant["id"])
    exclude = (tenant.get("org_id") or scope.tenant_id) if exclude_root else ""
    return _page(
        scope,
        q,
        {"kind": kind, "status": status},
        offset=max(0, offset),
        limit=max(0, limit),
        include_chunks=False,
        exclude_root=exclude,
    )


def facet_counts(
    conn: db.Connection,
    q: str,
    *,
    kind: str = "",
    status: str = "",
    source_kind: str = "",
    domain: str = "",
    org_id: str = "",
    history: str = "current",
) -> dict:
    """Count the ranked visible result set, omitting a facet's own selected filter."""
    scope, query = scope_for(conn), Query.parse(q)
    where, args = _filters(scope, {"org_id": org_id}, history, bool(query.text), False)
    if query.text:
        rows = _ranked(scope, query, where, args)
    else:
        rows = conn.execute("SELECT i.kind,e.status,e.source_kind,e.domain" + _FROM + "WHERE " + where, args)
    filters = {
        field: {part.strip() for part in values.split(",")}
        for field, values in zip(_FACETS, (kind, status, source_kind, domain), strict=True)
        if values
    }
    counts = {field: Counter() for field in _FACETS}
    total = 0
    for row in rows:
        mismatched = {field for field, values in filters.items() if row[field] not in values}
        if not mismatched:
            total += 1
        for field in _FACETS:
            if row[field] and not (mismatched - {field}):
                counts[field][row[field]] += 1
    return {"total": total, **{field: dict(counter) for field, counter in counts.items()}}
