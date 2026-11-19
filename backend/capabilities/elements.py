"""Read-only catalog adapters over existing local/federated services."""

import json
from contextlib import closing
from typing import Literal

from pydantic import Field

from backend import db, federation
from backend.api.v1.search_results import ranked_entities
from backend.capabilities.context import CapabilityContext
from backend.capabilities.element_models import ElementDetailResult, ElementListResult
from backend.capabilities.read import read_capability, search_restrictions
from backend.core.errors import ApiError, StrictModel
from backend.kernel import element_reads
from backend.kernel.cards import build_card
from backend.security import visibility

APPLICABILITY_OPTION_LIMIT = 500


class ElementList(StrictModel):
    q: str = Field(default="", max_length=300)
    kind: Literal["", "data", "rule", "skill", "knowledge", "term", "object_type", "link_type", "metric", "process"] = (
        ""
    )
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=25, ge=1, le=100)


class ElementGet(StrictModel):
    entity_id: str = Field(min_length=1, max_length=191)


def _catalog_page(ctx: CapabilityContext, body: ElementList) -> dict:
    with closing(ctx.connection_factory()) as conn:
        return ranked_entities(
            conn,
            body.q,
            kind=body.kind,
            page=body.page,
            page_size=body.page_size,
            asset_only=True,
            **search_restrictions(ctx),
        )


def _material_ids(refs: object, tenant_id: str) -> list[str]:
    if not isinstance(refs, list):
        return []
    return [
        r["material_id"]
        for r in refs
        if isinstance(r, dict)
        and isinstance(r.get("material_id"), str)
        and r["material_id"]
        and r.get("tenant_id", tenant_id) == tenant_id
    ]


@read_capability("element.list", "查看本体目录", ElementList, ElementListResult, assistants=["builder", "analyst"])
def list_elements(ctx: CapabilityContext, body: ElementList) -> ElementListResult:
    if ctx.principal.readonly:
        result = federation.search(**body.model_dump(), status="published", tenant_id=ctx.principal.tenant_id)
        if result["partial"]:
            raise ApiError("RUNTIME_UNAVAILABLE")
        # Cross-institution sources stay behind the published-only gateway. Its
        # institution name is already authorized metadata; do not resolve material
        # identifiers against the currently selected local database.
        for item in result["items"]:
            item.pop("payload", None)
            item["source_title"] = item.get("tenant_name", "")
        return ElementListResult(data={key: result[key] for key in ("items", "total", "page", "page_size")})
    result = _catalog_page(ctx, body)
    items = result["items"]
    bodies = {row["id"]: row.pop("payload", {}) for row in items}
    source_ids = {
        ident: _material_ids(payload.get("source_refs"), ctx.principal.tenant_id)
        for ident, payload in bodies.items()
        if isinstance(payload, dict)
    }
    titles = {}
    missing = [ident for ident, payload in bodies.items() if payload == {}]
    if missing or any(source_ids.values()):
        with closing(ctx.connection_factory()) as conn:
            scope = visibility.scope_for(conn, **search_restrictions(ctx))
            where, args = scope.predicate()
            if missing and not scope.metadata_only:
                # Public ranked hits carry no raw payload. Fetch only this authorized page's links.
                marks = ",".join("?" for _ in missing)
                rows = conn.execute(
                    "SELECT e.id,JSON_EXTRACT(e.payload,'$.source_refs') refs FROM entities e "
                    f"WHERE {where} AND e.id IN ({marks})",
                    (*args, *missing),
                )
                for row in rows:
                    source_ids[row["id"]] = _material_ids(json.loads(row["refs"] or "null"), scope.tenant_id)
            wanted = sorted({ident for ids in source_ids.values() for ident in ids})
            if wanted and not scope.external_tool:
                marks = ",".join("?" for _ in wanted)
                titles = {
                    row["id"]: row["title"]
                    for row in conn.execute(
                        f"SELECT e.id,e.title FROM entities e WHERE {where} "
                        f"AND e.kind='material' AND e.id IN ({marks})",
                        (*args, *wanted),
                    )
                }
    for item in items:
        item["source_title"] = "；".join(
            dict.fromkeys(titles[mid] for mid in source_ids.get(item["id"], []) if mid in titles)
        )
    return ElementListResult(data=result)


@read_capability(
    "element.get", "查看要素业务语言卡", ElementGet, ElementDetailResult, assistants=["builder", "analyst"]
)
def get_element(ctx: CapabilityContext, body: ElementGet) -> ElementDetailResult:
    federated = None
    if ctx.principal.readonly:
        federated = federation.entity_detail(ctx.principal.tenant_id, body.entity_id)
        if federated["entity"]["kind"] not in federation.ASSETS or federated["entity"]["status"] != "published":
            raise ApiError("NOT_FOUND")
    with closing(ctx.connection_factory()) as conn:
        scope = visibility.scope_for(conn, **search_restrictions(ctx))
        entity, gate_result, history = element_reads.details(scope, body.entity_id)
        if federated:
            # Keep authorized gateway metadata. Its cached payload can contain sources
            # outside the narrower published-only scope of an element-detail request.
            entity.update({k: v for k, v in federated["entity"].items() if k not in {"payload", "relations"}})
            entity["relations"] = element_reads.published_federated_relations(federated["relations"])
            entity.pop("_meta", None)
    can_edit = scope.drafts and not scope.metadata_only and "build.edit" in ctx.permissions
    options = []
    if can_edit:
        authority = entity["payload"].get("authority_spec")
        selected = list(
            dict.fromkeys(
                ident
                for key in ("org_ids", "person_ids")
                for ident in (authority.get(key, []) if isinstance(authority, dict) else [])
                if isinstance(ident, str)
            )
        )
        # Keep already selected subjects on the bounded page, using the same
        # tenant, kind and active-record restrictions as every other option.
        priority = f"CASE WHEN id IN ({','.join('?' for _ in selected)}) THEN 0 ELSE 1 END," if selected else ""
        with closing(db.connect()) as conn:
            options = [
                dict(row)
                for row in conn.execute(
                    "SELECT id,title,kind FROM entities "
                    "WHERE kind IN ('organization','position','person','party','object_type') "
                    "AND (?=0 OR kind IN ('organization','person')) "
                    "AND status<>'archived' AND COALESCE(json_extract(payload,'$.active_asset'),1)!=0 "
                    f"ORDER BY {priority}title,id LIMIT ?",
                    (int(bool(authority)), *selected, APPLICABILITY_OPTION_LIMIT),
                )
            ]
    card = build_card(entity)
    if "_meta" in entity:
        entity["_meta"]["card_text"] = card["text"]
    return ElementDetailResult(
        data={
            "entity": entity,
            "card": card,
            "versions": history,
            "can_edit": can_edit,
            "gates": gate_result,
            "applicability_options": options,
        }
    )
