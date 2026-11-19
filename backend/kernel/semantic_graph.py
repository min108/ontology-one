"""Visible local neighborhoods and directed change impact with business readings."""

from __future__ import annotations

from collections.abc import Iterable

from backend import db, impact
from backend.core.errors import ApiError
from backend.kernel import gates
from backend.relation_semantics import RELATION_SEMANTICS, relation_reading, semantic_relation
from backend.security import visibility

NODE_FIELDS = ("id", "title", "kind", "status", "version")
MAX_NODES = 120
MAX_EDGES = 200
# These predicates are stored from a consuming definition to its dependency.
DEPENDENCIES = frozenset(impact.REVERSE) | {
    "has_property",
    "implements",
    "extends",
    "link_source",
    "link_target",
    "measures",
    "governs",
    "applies_to",
    "defines",
    "typed_input",
    "typed_output",
    "follows_rule",
    "uses_input",
    "produces_output",
    "uses_asset",
    *impact.SOURCE_DEPENDENCIES,
}


def node(entity: dict) -> dict:
    return {key: entity[key] for key in NODE_FIELDS}


def resolve(
    conn: db.Connection, text: str, kind: str, limit: int, *, published_only: bool = False, metadata_only: bool = False
) -> dict:
    from backend.kernel.search import search

    if not text.strip():
        return {"items": []}
    matches = search(
        conn,
        text,
        kind=kind,
        include_chunks=False,
        page_size=limit,
        published_only=published_only,
        metadata_only=metadata_only,
    )
    candidates = []
    for hit in matches["items"]:
        match = hit["match"]
        if match in {"id", "alias", "title"}:
            confidence = "high"
        elif match == "prefix":
            confidence = "medium"
        elif match == "partial":
            confidence = "low"
        else:
            continue
        candidates.append({**node(hit), "match": match, "confidence": confidence})
    return {"items": candidates}


def _in(column: str, values: Iterable[str]) -> tuple[str, list[str]]:
    params = list(values)
    return column + " IN (" + ",".join("?" for _ in params) + ")", params


def _family(family: str) -> tuple[str, list[str]]:
    if not family:
        return "1=1", []
    types = [key for key in RELATION_SEMANTICS if key != "uses_method" and relation_reading(key)[3] == family]
    clauses, params = [], []
    if types:
        condition, params = _in("r.type", types)
        clauses.append(condition)
    if family == "structure":
        clauses.append("(r.type='uses_method' AND s.kind='process')")
    if family == "application":
        clauses.append("(r.type='uses_method' AND s.kind!='process')")
    if family == "reference":
        known, keys = _in("r.type", RELATION_SEMANTICS)
        clauses.append("NOT (" + known + ")")
        params.extend(keys)
    return "(" + " OR ".join(clauses) + ")", params


def _frontier(frontier: list[str], directed: bool) -> tuple[str, list[str]]:
    incoming, targets = _in("r.target", frontier)
    outgoing, sources = _in("r.source", frontier)
    if not directed:
        return f"({incoming} OR {outgoing})", [*targets, *sources]
    reverse, reverse_types = _in("r.type", sorted(DEPENDENCIES))
    forward, forward_types = _in("r.type", impact.FORWARD)
    return (
        f"(({incoming} AND ({reverse} OR (r.type='references_skill' AND s.kind='case'))) "
        f"OR ({outgoing} AND ({forward} OR (r.type='references_skill' AND s.kind='rule'))))",
        [*targets, *reverse_types, *sources, *forward_types],
    )


def _visible_edges(
    conn: db.Connection, scope: visibility.Scope, condition: str, params: list, *, limit: int, payload: bool = False
) -> list:
    source, source_args = scope.predicate("s")
    target, target_args = scope.predicate("t")
    confirmed, statuses = _in("r.status", sorted(gates.CONFIRMED))
    projection = ",r.payload" if payload else ""
    return conn.execute(
        "SELECT r.id,r.type,r.label,r.status,r.source,r.target,s.kind AS sk,t.kind AS tk,"
        "s.title AS st,t.title AS tt,s.status AS ss,t.status AS ts,s.version AS sv,t.version AS tv"
        + projection
        + " FROM relations r JOIN entities s ON s.id=r.source JOIN entities t ON t.id=r.target "
        + f"WHERE ({source}) AND ({target}) AND {confirmed} AND ({condition}) ORDER BY r.id LIMIT ?",
        (*source_args, *target_args, *statuses, *params, limit),
    ).fetchall()


def visible_relation(conn: db.Connection, scope: visibility.Scope, ident: str) -> dict:
    rows = _visible_edges(conn, scope, "r.id=?", [ident], limit=1, payload=True)
    if not rows:
        raise ApiError("NOT_FOUND")
    return dict(rows[0])


def _endpoint(row: dict, side: str) -> dict:
    prefix = "s" if side == "source" else "t"
    return {
        "id": row[side],
        "kind": row[prefix + "k"],
        "title": row[prefix + "t"],
        "status": row[prefix + "s"],
        "version": row[prefix + "v"],
    }


def _edge(row: dict) -> dict:
    value = semantic_relation(
        {k: row[k] for k in ("id", "type", "status", "source", "target", "label")}, row["sk"], row["tk"]
    )
    result = {
        k: value[k]
        for k in (
            "id",
            "source",
            "target",
            "type",
            "label",
            "status",
            "family",
            "family_label",
            "original_source",
            "original_target",
        )
    }
    titles = (row["tt"], row["st"]) if value["display_reversed"] else (row["st"], row["tt"])
    return {**result, "reading": f"{titles[0]}{result['label']}{titles[1]}"}


def _walk(
    conn: db.Connection, scope: visibility.Scope, entity: dict, depth: int, *, family: str = "", directed: bool = False
) -> tuple:
    root = entity["id"]
    nodes, edges, paths = {root: node(entity)}, {}, {root: []}
    frontier = [root]
    truncated = False
    for _ in range(depth):
        if not frontier:
            break
        condition, params = _frontier(frontier, directed)
        family_where, family_args = _family(family)
        condition += " AND " + family_where
        params.extend(family_args)
        if edges:
            visited, ids = _in("r.id", edges)
            condition += f" AND NOT ({visited})"
            params.extend(ids)
        remaining = MAX_EDGES - len(edges)
        rows = _visible_edges(conn, scope, condition, params, limit=remaining + 1)
        truncated |= len(rows) > remaining
        following = []
        for row in rows[:remaining]:
            # Projection uses the stored direction to establish dependencies and
            # the dictionary's business direction to present them to the reader.
            if directed:
                outgoing = row["type"] in impact.FORWARD or (row["type"] == "references_skill" and row["sk"] == "rule")
                previous, side = (row["source"], "target") if outgoing else (row["target"], "source")
            else:
                previous, side = (row["source"], "target") if row["source"] in frontier else (row["target"], "source")
            ident = row[side]
            if ident not in nodes and len(nodes) >= MAX_NODES:
                truncated = True
                continue
            edge = _edge(row)
            edges[edge["id"]] = edge
            if ident not in nodes:
                nodes[ident] = _endpoint(row, side)
                paths[ident] = [*paths[previous], edge]
                following.append(ident)
        frontier = following
    return nodes, edges, paths, truncated


def neighbors(conn: db.Connection, scope: visibility.Scope, entity_id: str, depth: int, family: str) -> dict:
    entity = visibility.visible_entity(conn, entity_id, scope=scope)
    nodes, edges, _, truncated = _walk(conn, scope, entity, depth, family=family)
    return {
        "entity_id": entity["id"],
        "nodes": list(nodes.values()),
        "relations": list(edges.values()),
        "truncated": truncated,
    }


def analyze(conn: db.Connection, scope: visibility.Scope, entity_id: str) -> dict:
    if not scope.drafts:
        raise ApiError("PERMISSION_DENIED")
    entity = visibility.visible_entity(conn, entity_id, scope=scope)
    nodes, _, paths, truncated = _walk(conn, scope, entity, 2, directed=True)
    direct, indirect = [], []
    for ident, path in paths.items():
        if path:
            (direct if len(path) == 1 else indirect).append({**nodes[ident], "path": path})
    # This round reports local institution dependencies; no foreign tenant is opened.
    parts = conn.execute(
        "SELECT m.id,m.name,d.min_version FROM entity_meta e JOIN ont_module_deps d ON d.depends_on_module=e.module_id "
        "JOIN ont_modules m ON m.id=d.module_id WHERE e.entity_id=? AND d.depends_on_tenant=? AND m.id!=e.module_id "
        "AND m.module_type IN ('org_common','scenario') ORDER BY m.name,m.id LIMIT ?",
        (entity["id"], scope.tenant_id, MAX_NODES + 1),
    ).fetchall()
    return {
        "entity_id": entity["id"],
        "direct": direct,
        "indirect": indirect,
        "dependent_modules": [dict(row) for row in parts[:MAX_NODES]],
        "truncated": truncated or len(parts) > MAX_NODES,
    }
