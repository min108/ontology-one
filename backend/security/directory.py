"""Server-owned organization lifecycle, serialized hierarchy and atomic renames."""

import hashlib
import json
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import HTTPException
from pymysql.cursors import DictCursor

from backend import db
from backend.api.v2.orgs import OrgManageInput
from backend.infra import migrations
from backend.infra.tenant_defaults import common_part_name
from backend.security import store
from backend.security.context import Principal

PUBLIC = (
    "id",
    "name",
    "category",
    "org_id",
    "status",
    "parent_id",
    "short_name",
    "org_code",
    "sort_order",
    "level",
    "is_demo",
    "mirror_of",
    "disabled_at",
    "disabled_reason",
)


def public(row: dict) -> dict:
    return {key: row[key] for key in PUBLIC}


def visible_directory(principal: Principal) -> list[dict]:
    principal = store.require_admin(principal)
    return [
        public(row)
        for row in store.list_tenants()
        if principal.is_superadmin or (row["id"] == principal.home_tenant_id and row["status"] == "active")
    ]


@contextmanager
def directory_lock() -> Iterator[None]:
    # One deployment-wide lock also prevents concurrent parent changes forming a cycle.
    lock = "ontology-directory-" + hashlib.sha256(store._config()["database"].encode()).hexdigest()[:40]
    with store.control_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT GET_LOCK(%s,30) AS acquired", (lock,))
        if cur.fetchone()["acquired"] != 1:
            raise HTTPException(409, "机构目录正在更新，请稍后重试。")
        try:
            yield
        finally:
            cur.execute("SELECT RELEASE_LOCK(%s)", (lock,))


def parent_level(rows: dict, ident: str, parent_id: str | None) -> int:
    seen = {ident}
    parent = parent_id
    while parent:
        if parent in seen:
            raise HTTPException(422, "上级机构不能形成循环。")
        seen.add(parent)
        row = rows.get(parent)
        if row is None or row["status"] != "active":
            raise HTTPException(422, "上级机构不存在或已停用。")
        parent = row["parent_id"]
    return 0 if parent_id is None else rows[parent_id]["level"] + 1


def manage(principal: Principal, body: OrgManageInput) -> str:
    principal = store.require_admin(principal, superadmin=True)
    if principal.home_tenant_id != "hq":
        raise HTTPException(403, "机构目录仅由总部平台管理员管理。")
    with directory_lock():
        rows = {row["id"]: row for row in store.list_tenants()}
        if body.action == "create":
            return _create(principal, body, rows)
        row = rows.get(body.org_id)
        if row is None:
            raise HTTPException(404, "机构不存在。")
        if row["status"] not in {"active", "disabled"}:
            raise HTTPException(409, "机构开通尚未完成，请联系平台管理员离线检查。")
        if row["id"] == "hq" and (
            body.action == "disable" or body.parent_id is not None or body.category not in (None, "headquarters")
        ):
            raise HTTPException(409, "总部机构不能停用、更改类别或设置上级。")
        if row["id"] != "hq" and body.category == "headquarters":
            raise HTTPException(422, "总部机构已存在，请选择其他类别。")
        changes = body.model_dump(exclude_unset=True, exclude={"action", "org_id", "reason"})
        if body.action == "disable":
            if principal.tenant_id == row["id"]:
                raise HTTPException(409, "请先切换到总部工作区，再停用此机构。")
            changes = {"status": "disabled", "disabled_at": db.now(), "disabled_reason": body.reason or ""}
        elif body.action == "enable":
            changes = {"status": "active", "disabled_at": None, "disabled_reason": ""}
        else:
            if not changes or any(
                value is None for key, value in changes.items() if key not in {"parent_id", "org_code"}
            ):
                raise HTTPException(422, "请填写需要修改的机构信息。")
            if "name" in changes:
                changes["name"] = changes["name"].strip()
                if not changes["name"]:
                    raise HTTPException(422, "机构名称不能为空。")
            parent = changes.get("parent_id", row["parent_id"])
            changes["level"] = parent_level(rows, row["id"], parent)
            if row["status"] != "active":
                raise HTTPException(409, "请先启用机构，再修改目录信息。")
        # Provision lazy existing roots before the cross-database DML transaction.
        if body.action == "update":
            migrations.provision_tenant(row["id"])
        with store.authorized_control_connection(principal, mode="standard") as conn, conn.cursor() as cur:
            if body.action == "update":
                _snapshot_root(cur, row, changes, body.reason or "机构目录更新")
            cur.execute(
                "UPDATE sec_tenants SET " + ",".join(f"{key}=%s" for key in changes) + " WHERE id=%s",
                (*changes.values(), row["id"]),
            )
            if "level" in changes:
                delta = changes["level"] - row["level"]
                descendants = {row["id"]}
                for _ in rows:
                    descendants.update(t["id"] for t in rows.values() if t["parent_id"] in descendants)
                for ident in descendants - {row["id"]}:
                    cur.execute("UPDATE sec_tenants SET level=level+%s WHERE id=%s", (delta, ident))
            store._audit(
                cur,
                principal,
                "org_" + body.action,
                row["id"],
                "success",
                {"changes": changes, "reason": body.reason or ""},
            )
        return row["id"]


def _create(principal: Principal, body: OrgManageInput, rows: dict) -> str:
    if body.category == "headquarters":
        raise HTTPException(422, "总部机构已存在，请选择其他类别。")
    ident = "org-" + uuid.uuid4().hex[:8]
    level = parent_level(rows, ident, body.parent_id)
    control = store._config()["database"]
    database = (
        control.removesuffix("_control")[:32]
        + "_t_"
        + hashlib.sha256((control + ":" + ident).encode()).hexdigest()[:24]
    )
    with store.authorized_control_connection(principal, mode="standard") as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO sec_tenants(id,name,category,org_id,db_name,status,created_at,parent_id,"
            "short_name,org_code,sort_order,level) VALUES(%s,%s,%s,%s,%s,'provisioning',%s,%s,%s,%s,%s,%s)",
            (
                ident,
                body.name.strip(),
                body.category,
                ident,
                database,
                time.time(),
                body.parent_id,
                body.short_name or body.name[:120],
                body.org_code,
                body.sort_order or 0,
                level,
            ),
        )
        store._audit(cur, principal, "org_create", ident, "pending", {"category": body.category})
    try:
        migrations.provision_tenant(ident, pending=True)
        with store.authorized_control_connection(principal, mode="standard") as conn, conn.cursor() as cur:
            cur.execute("UPDATE sec_tenants SET status='active' WHERE id=%s AND status='provisioning'", (ident,))
            store._audit(cur, principal, "org_create", ident, "success", {})
    except Exception:
        # Failure records must survive revocation of the initiating administrator.
        with store.control_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE sec_tenants SET status='provision_failed' WHERE id=%s AND status='provisioning'", (ident,)
            )
            store._audit(cur, principal, "org_create", ident, "failed", {"next_step": "offline_repair"})
        raise HTTPException(503, "机构开通未完成，记录与数据已保留。请联系平台管理员离线检查。") from None
    return ident


def _snapshot_root(cur: DictCursor, row: dict, changes: dict, reason: str) -> None:
    # Both databases are on this server: one InnoDB transaction includes the
    # root snapshot, root update, directory update and append-only audit.
    database = migrations.database_name(row["db_name"])
    cur.execute("SELECT DATABASE() AS db_name")
    control = cur.fetchone()["db_name"]
    raw = cur.connection
    raw.select_db(database)
    try:
        tenant = db.Connection(raw, project_search=True)
        old = tenant.execute("SELECT * FROM entities WHERE id=? FOR UPDATE", (row["id"],)).fetchone()
        if old is None:
            raise HTTPException(409, "机构根节点不存在，请先离线检查工作区。")
        snapshot = db.entity_json(old)
        snapshot["owner_tenant_id"] = row["id"]
        snapshot["owner_org_id"] = row["id"]
        tenant.execute(
            "INSERT INTO versions(entity_id,version,snapshot,created_at,reason) VALUES(?,?,?,?,?)",
            (old["id"], old["version"], json.dumps(snapshot, ensure_ascii=False), db.now(), reason),
        )
        tenant.execute(
            "UPDATE entities SET title=?,version=version+1,updated_at=? WHERE id=?",
            (changes.get("name", row["name"]), db.now(), old["id"]),
        )
        if "name" in changes and changes["name"] != row["name"]:
            tenant.execute(
                "UPDATE ont_modules SET name=?,updated_at=? WHERE id='MOD-DEFAULT'",
                (common_part_name(changes["name"]), db.now()),
            )
        tenant.flush_search_changes()
    finally:
        raw.select_db(control)
