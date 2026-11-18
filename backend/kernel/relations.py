"""Payload-owned edges and candidate proposals, within the caller's tenant transaction."""

import json
import uuid
from contextlib import closing

from fastapi import HTTPException

from backend import db
from backend.relation_semantics import RELATION_SEMANTICS

DERIVED_TYPES = frozenset(
    {
        "applies_to",
        "has_property",
        "link_source",
        "link_target",
        "measures",
        "governs",
        "implements",
        "extends",
        "holds_position",
        "position_in",
        "plays_role",
        "performed_by",
        "follows_rule",
        "uses_input",
        "produces_output",
    }
)
TARGET_KINDS = {
    "applies_to": {"organization", "position", "person", "party", "object_type"},
    "has_property": {"data"},
    "link_source": {"object_type"},
    "link_target": {"object_type"},
    "measures": {"object_type"},
    "governs": {"object_type"},
    "implements": {"object_type"},
    "extends": {"object_type"},
    "holds_position": {"position"},
    "position_in": {"organization"},
    "plays_role": {"organization"},
    "performed_by": {"position", "organization", "party"},
    "follows_rule": {"rule"},
    "uses_input": {"object_type", "data"},
    "produces_output": {"object_type"},
    "uses_method": {"skill", "knowledge"},
}


def _payload(value: object) -> dict:
    return json.loads(value or "{}") if isinstance(value, str) else (value or {})


def derived_edges(entity: dict) -> list[dict]:
    """Pure derivation; stable step keys keep repeated references distinguishable."""
    payload = _payload(entity.get("payload"))
    result = []

    def add(typ: str, target: object, *, step: str | None = None, role: str | None = None) -> None:
        if not target:
            return
        if not isinstance(target, str):
            raise HTTPException(422, "关联对象编号须为文字。")
        meta = {"derived": True}
        if step is not None:
            if not isinstance(step, str) or not step:
                raise HTTPException(422, "办理步骤须有固定编号。")
            meta["step_key"] = step
        if role:
            meta["role"] = role
        edge = {"source": entity["id"], "target": target, "type": typ, "status": "confirmed", "payload": meta}
        if edge not in result:
            result.append(edge)

    kind = entity.get("kind")
    if kind == "object_type":
        for prop in payload.get("properties", []):
            add("has_property", prop.get("data_id"))
        for target in payload.get("implements", []):
            add("implements", target)
        add("extends", payload.get("extends"))
    elif kind == "link_type":
        add("link_source", payload.get("source_type"))
        add("link_target", payload.get("target_type"))
    elif kind == "metric":
        add("measures", payload.get("object_type"))
    elif kind == "rule":
        add("applies_to", payload.get("applies_to_id"))
        add("governs", (payload.get("judgement") or {}).get("applies_to", {}).get("object_type"))
    elif kind == "person":
        add("holds_position", payload.get("position_id"))
    elif kind == "position":
        add("position_in", payload.get("org_id"))
    elif kind == "party":
        for role in payload.get("roles", []):
            add("plays_role", role.get("org_id"), role=role.get("role"))
    elif kind == "process":
        for step in payload.get("steps", []):
            key = step.get("key")
            if not isinstance(key, str) or not key:
                raise HTTPException(422, "办理步骤须有固定编号。")
            for actor in step.get("actors", []):
                for field in ("position_id", "org_id", "organization_id", "party_id"):
                    add("performed_by", actor.get(field), step=key)
            for basis in step.get("basis", []) + step.get("constraints", []):
                add("follows_rule", basis.get("rule_id"), step=key)
            for item in step.get("inputs", []):
                add("uses_input", item.get("object_type") or item.get("data_id"), step=key)
            for item in step.get("outputs", []):
                add("produces_output", item.get("object_type"), step=key)
            decision = step.get("decision") or {}
            if isinstance(decision, dict):
                add("uses_method", decision.get("skill_id") or decision.get("knowledge_id"), step=key)
    return result


def _key(edge: dict) -> tuple:
    payload = _payload(edge.get("payload"))
    return edge["type"], edge["target"], payload.get("step_key"), payload.get("role")


def _validate_structure(conn: db.Connection, entity: dict, edge: dict, target: dict) -> None:
    if edge["type"] == "implements":
        if target["payload"].get("is_interface") is not True:
            raise HTTPException(422, "实现接口须指向通用对象接口。")
        actual = {prop["key"]: prop for prop in _payload(entity.get("payload")).get("properties", [])}
        for prop in target["payload"].get("properties", []):
            if prop.get("required") and (
                prop["key"] not in actual or actual[prop["key"]].get("datatype") != prop.get("datatype")
            ):
                raise HTTPException(422, "请先对齐接口的必备数据项与数据类型。")
    if edge["type"] == "extends":
        visited = {entity["id"]}
        current = target
        while current:
            if current["id"] in visited:
                raise HTTPException(422, "业务对象的扩展关系不能成环。")
            visited.add(current["id"])
            parent = current["payload"].get("extends")
            if not parent:
                break
            current = db.entity_json(conn.execute("SELECT * FROM entities WHERE id=?", (parent,)).fetchone())
            if not current or current["kind"] != "object_type":
                raise HTTPException(422, "扩展链中存在无法识别的业务对象。")


def sync_derived(conn: db.Connection, entity: dict) -> list[dict]:
    """Synchronize only payload-owned edges; never commit or touch manual history."""
    desired = derived_edges(entity)
    for edge in desired:
        target = db.entity_json(conn.execute("SELECT * FROM entities WHERE id=?", (edge["target"],)).fetchone())
        if not target or target["payload"].get("active_asset") in (False, 0):
            raise HTTPException(422, "关联的要素不存在或已成为历史内容。")
        if target["kind"] not in TARGET_KINDS[edge["type"]]:
            raise HTTPException(422, "关联对象的种类与该字段不符。")
        _validate_structure(conn, entity, edge, target)
    existing = [dict(row) for row in conn.execute("SELECT * FROM relations WHERE source=?", (entity["id"],))]
    owned = [
        row
        for row in existing
        if (
            _payload(row["payload"]).get("derived") is True
            or (
                entity.get("payload", {}).get("applies_to_id")
                and row["type"] == "applies_to"
                and _payload(row["payload"]).get("recipe_applicability") is True
            )
        )
        and not _payload(row["payload"]).get("skill_managed")
        and not _payload(row["payload"]).get("authority_managed")
    ]
    wanted = {_key(edge) for edge in desired}
    kept = {}
    for row in owned:
        key = _key(row)
        if key not in wanted or key in kept:
            conn.execute("DELETE FROM relations WHERE id=?", (row["id"],))
        else:
            kept[key] = row
    for edge in desired:
        old = kept.get(_key(edge))
        edge["id"] = old["id"] if old else "rel-" + uuid.uuid4().hex[:16]
        edge["label"] = (
            "运用"
            if entity.get("kind") == "process" and edge["type"] == "uses_method"
            else RELATION_SEMANTICS[edge["type"]][0]
        )
        encoded = json.dumps(edge["payload"], ensure_ascii=False, sort_keys=True)
        if old:
            if (
                old["status"] != "confirmed"
                or old["label"] != edge["label"]
                or _payload(old["payload"]) != edge["payload"]
            ):
                conn.execute(
                    "UPDATE relations SET label=?,status=?,payload=? WHERE id=?",
                    (edge["label"], "confirmed", encoded, edge["id"]),
                )
        else:
            conn.execute(
                "INSERT INTO relations(id,source,target,type,label,status,payload) VALUES(?,?,?,?,?,?,?)",
                (edge["id"], edge["source"], edge["target"], edge["type"], edge["label"], "confirmed", encoded),
            )
    return desired


def propose(source: str, target: str, typ: str, reason: str) -> dict:
    """Propose a manual relation using the legacy specialized-workflow boundaries."""
    from backend.api.v1.core import relation_work_entry
    from backend.skills import guard_generic_entity

    if typ not in RELATION_SEMANTICS or typ in DERIVED_TYPES:
        raise HTTPException(422, "请选择可独立核对的关联类型；派生关联随要素维护。")
    if not reason.strip():
        raise HTTPException(422, "请说明关联依据。")
    with closing(db.connect()) as conn, conn:
        endpoints = {}
        for ident in sorted({source, target}):
            entity = db.entity_json(conn.execute("SELECT * FROM entities WHERE id=? FOR UPDATE", (ident,)).fetchone())
            if not entity:
                raise HTTPException(404, "未找到关联的要素。")
            endpoints[ident] = entity
        if endpoints[source]["kind"] == "process" and typ == "uses_method":
            raise HTTPException(422, "流程的运用关系随办理步骤维护。")
        row = {
            "id": "rel-" + uuid.uuid4().hex[:16],
            "source": source,
            "target": target,
            "type": typ,
            "label": RELATION_SEMANTICS[typ][0],
            "status": "candidate",
            "payload": json.dumps({"proposal_reason": reason}, ensure_ascii=False),
        }
        entry = relation_work_entry(conn, row)
        if entry:
            raise HTTPException(400, entry["reason"])
        for entity in endpoints.values():
            guard_generic_entity(conn, entity)
            if entity["payload"].get("authority_spec"):
                raise HTTPException(400, "请从权责规范工作面维护关联。")
        current = endpoints[source]
        stamp = db.now()
        conn.execute(
            "INSERT INTO versions(entity_id,version,snapshot,created_at,reason) VALUES(?,?,?,?,?)",
            (source, current["version"], json.dumps(current, ensure_ascii=False), stamp, "提出关联：" + reason),
        )
        conn.execute(
            "INSERT INTO relations(id,source,target,type,label,status,payload) VALUES(?,?,?,?,?,?,?)",
            tuple(row[key] for key in ("id", "source", "target", "type", "label", "status", "payload")),
        )
        conn.execute("UPDATE entities SET version=version+1,updated_at=? WHERE id=?", (stamp, source))
        db.log(conn, source, "提出关联", reason)
    return {**row, "payload": _payload(row["payload"])}
