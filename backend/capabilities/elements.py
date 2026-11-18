"""Read-only catalog adapters over existing local/federated services."""

from contextlib import closing
from typing import Literal

from pydantic import Field

from backend import db, federation
from backend.api.v1 import core
from backend.capabilities.context import CapabilityContext
from backend.capabilities.element_models import ElementDetailResult, ElementListResult
from backend.capabilities.read import read_capability
from backend.core.errors import ApiError, StrictModel
from backend.kernel import gates
from backend.kernel.cards import build_card
from backend.kernel.kinds import KINDS
from backend.kernel.metadata import get_meta


class ElementList(StrictModel):
    q: str = Field(default="", max_length=300)
    kind: Literal["", "data", "rule", "skill", "knowledge", "term", "object_type", "link_type", "metric", "process"] = (
        ""
    )
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=25, ge=1, le=100)


class ElementGet(StrictModel):
    entity_id: str = Field(min_length=1, max_length=191)


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
    result = core.entities(
        **body.model_dump(), asset_only=True, status="" if "draft.read" in ctx.permissions else "published"
    )
    source_ids: dict[str, list[str]] = {}
    for item in result["items"]:
        payload = item.pop("payload", {})
        refs = payload.get("source_refs", []) if isinstance(payload, dict) else []
        source_ids[item["id"]] = list(
            dict.fromkeys(
                ref["material_id"]
                for ref in (refs if isinstance(refs, list) else [])
                if isinstance(ref, dict)
                and isinstance(ref.get("material_id"), str)
                and ref["material_id"]
                and ref.get("tenant_id") in (None, "", ctx.principal.tenant_id)
            )
        )
    wanted = sorted({ident for ids in source_ids.values() for ident in ids})
    titles = {}
    if wanted:
        # db.connect is bound to the authenticated tenant. Resolve only active
        # material metadata, following the local element detail visibility rule.
        with closing(db.connect()) as conn:
            titles = {
                row["id"]: row["title"]
                for row in conn.execute(
                    "SELECT id,title FROM entities WHERE kind='material' "
                    "AND COALESCE(json_extract(payload,'$.active_asset'),1)!=0 "
                    f"AND id IN ({','.join('?' for _ in wanted)})",
                    wanted,
                )
            }
    for item in result["items"]:
        item["source_title"] = "；".join(
            dict.fromkeys(titles[ident] for ident in source_ids[item["id"]] if ident in titles)
        )
    return ElementListResult(data=result)


@read_capability(
    "element.get", "查看要素业务语言卡", ElementGet, ElementDetailResult, assistants=["builder", "analyst"]
)
def get_element(ctx: CapabilityContext, body: ElementGet) -> ElementDetailResult:
    if ctx.principal.readonly:
        result = federation.entity_detail(ctx.principal.tenant_id, body.entity_id)
        entity = {**result["entity"], "relations": result["relations"], "activities": []}
        if entity["kind"] not in federation.ASSETS or entity["status"] != "published":
            raise ApiError("NOT_FOUND")
        history = []
    else:
        entity = core.detail(body.entity_id)
        if entity["kind"] not in KINDS or entity["payload"].get("active_asset") in (0, False):
            raise ApiError("NOT_FOUND")
        private = "draft.read" in ctx.permissions
        if not private and entity["status"] != "published":
            raise ApiError("NOT_FOUND")
        history = core.versions(body.entity_id)["items"] if private else []
        if not private:
            # Never reveal draft relation titles or arbitrary nested payloads.
            with closing(db.connect()) as conn:
                visible = {
                    row["id"]: dict(row)
                    for row in conn.execute(
                        "SELECT id,title,org_id FROM entities "
                        "WHERE COALESCE(json_extract(payload,'$.active_asset'),1)!=0 "
                        "AND (status='published' OR kind='material')"
                    )
                }
            tenant = {"id": ctx.principal.tenant_id, "name": ""}
            for item in visible.values():
                item["detail_url"] = "/assets/" + item["id"]
            entity["payload"], _ = federation._safe_payload(entity["payload"], tenant, visible)
            for direction in ("incoming", "outgoing"):
                entity["relations"][direction] = [
                    row
                    for row in entity["relations"][direction]
                    if row["source"] in visible and row["target"] in visible
                ]
            entity["activities"] = []
            entity.pop("relation_counts", None)
        with closing(db.connect()) as conn:
            # The same authorized entity drives the card and fresh gate checks.
            entity["_meta"] = get_meta(conn, body.entity_id) or {}
            gate_result = gates.check_entity(conn, entity)
            for relation in entity["relations"]["incoming"]:
                if relation.get("source_kind") == "rule" and relation.get("type") in {"governs", "applies_to"}:
                    rule = conn.execute(
                        "SELECT payload,status FROM entities WHERE id=? AND status='published'", (relation["source"],)
                    ).fetchone()
                    if rule:
                        relation["source_status"] = rule["status"]
                        relation["source_payload"] = rule["payload"]
    if ctx.principal.readonly:
        # Federation authorized this published item. Check its original tenant
        # data, returning only the business gate result, never private payloads.
        with db.tenant_connection(ctx.principal.tenant_id, readonly=True) as conn:
            original = db.entity_json(conn.execute("SELECT * FROM entities WHERE id=?", (body.entity_id,)).fetchone())
            if not original:
                raise ApiError("NOT_FOUND")
            gate_result = gates.check_entity(conn, original)
    options = []
    if "build.edit" in ctx.permissions and not ctx.principal.readonly:
        with closing(db.connect()) as conn:
            options = [
                dict(row)
                for row in conn.execute(
                    "SELECT id,title,kind FROM entities "
                    "WHERE kind IN ('organization','position','person','party','object_type') "
                    "AND status<>'archived' AND COALESCE(json_extract(payload,'$.active_asset'),1)!=0 "
                    "ORDER BY title,id LIMIT 500"
                )
            ]
    if ctx.principal.readonly:
        # Federated projections must not expose private field values, applicants or source text.
        gate_result["fields"] = []
        gate_result["ready_to_submit"] = gate_result["passed"]
        gate_result["submission_blockers"] = list(gate_result["blockers"])
        for check in gate_result["checks"]:
            for key in ("application_reason", "application_by", "application_at", "denial_reason", "waiver_reason"):
                check[key] = ""
        entity.pop("_meta", None)
    else:
        # Gate storage includes stale decisions and internal fingerprints; expose only fresh projection.
        entity.setdefault("_meta", {}).pop("gates", None)
    return ElementDetailResult(
        data={
            "entity": entity,
            "card": build_card(entity),
            "versions": history,
            "can_edit": "build.edit" in ctx.permissions and not ctx.principal.readonly,
            "gates": gate_result,
            "applicability_options": options,
        }
    )
