"""Read-only catalog adapters over existing local/federated services."""

from contextlib import closing
from typing import Literal

from pydantic import Field

from backend import db, federation
from backend.api.v1 import core
from backend.capabilities.build import BuildResult
from backend.capabilities.context import CapabilityContext
from backend.capabilities.read import read_capability
from backend.core.errors import ApiError, StrictModel
from backend.kernel.cards import build_card


class ElementList(StrictModel):
    q: str = Field(default="", max_length=300)
    kind: Literal["", "data", "rule", "skill", "knowledge"] = ""
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=25, ge=1, le=100)


class ElementGet(StrictModel):
    entity_id: str = Field(min_length=1, max_length=191)


@read_capability("element.list", "查看本体目录", ElementList, BuildResult, assistants=["builder", "analyst"])
def list_elements(ctx: CapabilityContext, body: ElementList) -> BuildResult:
    if ctx.principal.readonly:
        result = federation.search(**body.model_dump(), status="published", tenant_id=ctx.principal.tenant_id)
        if result["partial"]:
            raise ApiError("RUNTIME_UNAVAILABLE")
        return BuildResult(data={key: result[key] for key in ("items", "total", "page", "page_size")})
    result = core.entities(
        **body.model_dump(), asset_only=True, status="" if "draft.read" in ctx.permissions else "published"
    )
    # List metadata only: arbitrary payloads are not a catalog/search response.
    for item in result["items"]:
        item.pop("payload", None)
    return BuildResult(data=result)


@read_capability("element.get", "查看要素业务语言卡", ElementGet, BuildResult, assistants=["builder", "analyst"])
def get_element(ctx: CapabilityContext, body: ElementGet) -> BuildResult:
    if ctx.principal.readonly:
        result = federation.entity_detail(ctx.principal.tenant_id, body.entity_id)
        entity = {**result["entity"], "relations": result["relations"], "activities": []}
        if entity["kind"] not in federation.ASSETS or entity["status"] != "published":
            raise ApiError("NOT_FOUND")
        history = []
    else:
        entity = core.detail(body.entity_id)
        if entity["kind"] not in federation.ASSETS or entity["payload"].get("active_asset") in (0, False):
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
    return BuildResult(
        data={
            "entity": entity,
            "card": build_card(entity),
            "versions": history,
            "can_edit": "build.edit" in ctx.permissions and not ctx.principal.readonly,
        }
    )
