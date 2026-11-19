"""Tenant-local institution settings, asset ownership and reference declarations."""

import json
from collections.abc import Iterable
from itertools import batched

from backend import db
from backend.core.errors import ApiError
from backend.infra.migrations import database_name
from backend.jobs import assert_execution_principal_current
from backend.kernel.domains import DOMAINS
from backend.kernel.gates import CONFIRMED
from backend.kernel.kinds import persisted_kind
from backend.security import store
from backend.security.context import Principal
from backend.security.permissions import PermissionScope, permissions_for

COMMON = "MOD-DEFAULT"
# scn_refs belongs to the later scenario workflow. D19's four-table foundation
# retains promotions, even before a first release, in this server-owned field.
_PROMOTED = "_promoted_entity_ids"
_PREFERENCES = frozenset({"granularity", "naming", "enabled_kinds", "glossary_hints", "avoid", "examples", "notes"})
_SETTINGS = frozenset({"name", "domain_code", "owner_user_id", "scope_note", "preferences"})
_SUPPORTED = "(module_type='scenario' OR (module_type='org_common' AND id='MOD-DEFAULT'))"


def _load(conn: db.Connection, module_id: str, *, lock: bool = False) -> dict:
    row = conn.execute(
        "SELECT * FROM ont_modules WHERE id=? AND " + _SUPPORTED + (" FOR UPDATE" if lock else ""),
        (module_id,),
    ).fetchone()
    if row is None:
        raise ApiError("NOT_FOUND")
    part = dict(row)
    value = part["preferences"]
    preferences = json.loads(value) if isinstance(value, str) else value
    if preferences is not None and not isinstance(preferences, dict):
        raise ApiError("PRECONDITION_FAILED", "整理偏好的存储格式无效。")
    part["preferences"] = preferences or {}
    return part


def _public(part: dict) -> dict:
    return {**part, "preferences": {key: value for key, value in part["preferences"].items() if key in _PREFERENCES}}


def get_module(conn: db.Connection, module_id: str, *, lock: bool = False) -> dict:
    return _public(_load(conn, module_id, lock=lock))


def require_management(principal: Principal, module: dict) -> None:
    scope = PermissionScope(tenant_id=principal.tenant_id, module_owner_id=module["owner_user_id"] or "")
    if "module.manage" not in permissions_for(principal, scope):
        raise ApiError("PERMISSION_DENIED")
    if module["status"] != "active":
        raise ApiError("PRECONDITION_FAILED", "这部分本体已停用。")


def _write_scope(conn: db.Connection, principal: Principal) -> None:
    if conn.principal is None or conn.principal.tenant_id != principal.tenant_id:
        raise ApiError("NOT_FOUND")
    if conn.principal != principal or principal.identity_kind != "session" or principal.readonly or conn.readonly:
        raise ApiError("PERMISSION_DENIED")
    assert_execution_principal_current(principal, require_write=True)


def _local_owner(conn: db.Connection, tenant_id: str, part: dict, owner: str) -> str:
    # Hold the account in the same transaction as the assignment, including
    # for HQ administrators operating inside another institution's workspace.
    control = database_name(store._config()["database"])
    user = conn.execute(
        f"SELECT u.id,u.role FROM `{control}`.sec_users u "
        f"JOIN `{control}`.sec_tenants t ON t.id=u.home_tenant_id "
        "WHERE u.id=? AND u.is_active=1 AND t.id=? AND t.status='active' AND t.db_name=DATABASE() FOR SHARE",
        (owner, tenant_id),
    ).fetchone()
    if not user:
        raise ApiError("NOT_FOUND")
    allowed = (
        {"admin", "modeler"} if part["module_type"] == "org_common" else {"admin", "modeler", "reviewer", "analyst"}
    )
    if user["role"] not in allowed:
        raise ApiError("VALIDATION_FAILED", "此角色不能担任这部分本体的负责人。")
    return user["id"]


def _changes(conn: db.Connection, principal: Principal, part: dict, changes: dict) -> dict:
    values = {k: v for k, v in changes.items() if k in _SETTINGS}
    if set(changes) - _SETTINGS - {"expected_updated_at", "register_dependencies"}:
        raise ApiError("VALIDATION_FAILED", "包含不可修改的本体设置。")
    if "expected_updated_at" in changes and changes["expected_updated_at"] != part["updated_at"]:
        raise ApiError("VERSION_CONFLICT")
    if "register_dependencies" in changes and not isinstance(changes["register_dependencies"], bool):
        raise ApiError("VALIDATION_FAILED")
    if "name" in values:
        name = values["name"]
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 255:
            raise ApiError("VALIDATION_FAILED", "请填写 255 字以内的名称。")
        values["name"] = name.strip()
        if part["module_type"] == "org_common" and values["name"] != part["name"]:
            raise ApiError("VALIDATION_FAILED", "公共部分的名称随机构目录更新。")
    if "domain_code" in values:
        code = values["domain_code"]
        if (
            not isinstance(code, str)
            or code not in DOMAINS
            or (part["module_type"] == "org_common" and code != "general")
        ):
            raise ApiError("VALIDATION_FAILED", "请选择有效的领域，公共部分的领域为综合。")
    if values.get("scope_note") is not None and not isinstance(values["scope_note"], str):
        raise ApiError("VALIDATION_FAILED", "范围说明须为文字。")
    if values.get("owner_user_id") is not None:
        if not isinstance(values["owner_user_id"], str) or not 0 < len(values["owner_user_id"]) <= 64:
            raise ApiError("VALIDATION_FAILED", "请提供有效的负责人编号。")
        values["owner_user_id"] = _local_owner(conn, principal.tenant_id, part, values["owner_user_id"])
    if "preferences" in values:
        preferences = {} if values["preferences"] is None else values["preferences"]
        if not isinstance(preferences, dict) or set(preferences) - _PREFERENCES:
            raise ApiError("VALIDATION_FAILED", "只能修改整理偏好中的业务字段。")
        protected = {k: v for k, v in part["preferences"].items() if k not in _PREFERENCES}
        values["preferences"] = {**protected, **preferences}
    return {k: v for k, v in values.items() if v != part[k]}


def promoted_ids(conn: db.Connection, module_id: str) -> set[str]:
    """The source retains its explicit reference after a move, independent of any graph edge."""
    return set(_load(conn, module_id)["preferences"].get(_PROMOTED, ()))


def _register(conn: db.Connection, source: str, tenant_id: str, targets: Iterable[str]) -> None:
    for target in sorted(set(targets) - {source}):
        # Existing declarations may have been pinned to a minimum version.
        conn.execute(
            "INSERT INTO ont_module_deps(module_id,depends_on_tenant,depends_on_module,min_version) "
            "VALUES(?,?,?,NULL) ON DUPLICATE KEY UPDATE min_version=min_version",
            (source, tenant_id, target),
        )


def missing_reference_dependencies(
    conn: db.Connection, module_id: str, tenant_id: str, entity_ids: Iterable[str], *, lock: bool = False
) -> list[str]:
    """Only direct external assets have an owning part. Identity and material references have none."""
    targets = set()
    for batch in batched(sorted(set(entity_ids)), 500):
        rows = conn.execute(
            "SELECT e.kind,m.module_id FROM entities e LEFT JOIN entity_meta m ON e.id=m.entity_id "
            "WHERE e.id IN (" + ",".join("?" for _ in batch) + ") ORDER BY e.id" + (" FOR SHARE OF m" if lock else ""),
            batch,
        )
        for row in rows:
            if persisted_kind(row["kind"]).module_eligible:
                targets.add(row["module_id"] or COMMON)
    if not targets:
        return []
    declared = conn.execute(
        "SELECT depends_on_module FROM ont_module_deps WHERE module_id=? AND depends_on_tenant=?",
        (module_id, tenant_id),
    )
    missing = targets - {module_id} - {row["depends_on_module"] for row in declared}
    # Legacy metadata can contain obsolete identifiers. Do not declare a part which has no local row.
    return sorted(
        row["id"]
        for batch in batched(sorted(missing), 500)
        for row in conn.execute(
            "SELECT id FROM ont_modules WHERE " + _SUPPORTED + " AND id IN (" + ",".join("?" for _ in batch) + ")",
            batch,
        )
    )


def _outgoing_refs(conn: db.Connection, module_id: str) -> set[str]:
    rows = conn.execute(
        "SELECT DISTINCT r.target,e.kind FROM relations r JOIN entities e ON e.id=r.source "
        "LEFT JOIN entity_meta m ON m.entity_id=e.id WHERE (m.module_id=? OR (? AND m.entity_id IS NULL)) "
        "AND r.status IN (?,?)",
        (module_id, module_id == COMMON, *sorted(CONFIRMED)),
    )
    return {row["target"] for row in rows if persisted_kind(row["kind"]).module_eligible}


def record_confirmed_references(conn: db.Connection, source: str, refs: Iterable[str]) -> None:
    """Called after a human or payload establishes an outgoing relation in a scenario."""
    member = conn.execute("SELECT module_id FROM entity_meta WHERE entity_id=?", (source,)).fetchone()
    if not member or member["module_id"] in {None, COMMON}:
        return
    # A concurrent promotion may have placed the source in the public part.
    # Only scenario members establish fresh dependencies automatically.
    current = conn.execute(
        "SELECT m.module_id FROM entity_meta m JOIN ont_modules s ON s.id=m.module_id "
        "WHERE m.entity_id=? AND s.module_type='scenario' FOR SHARE OF m",
        (source,),
    ).fetchone()
    if current:
        tenant_id = conn.principal.tenant_id
        targets = missing_reference_dependencies(conn, current["module_id"], tenant_id, refs, lock=True)
        _register(conn, current["module_id"], tenant_id, targets)


def save(conn: db.Connection, principal: Principal, module_id: str, changes: dict) -> dict:
    _write_scope(conn, principal)
    part = _load(conn, module_id, lock=True)
    require_management(principal, part)
    values = _changes(conn, principal, part, changes)
    if changes.get("register_dependencies"):
        refs = _outgoing_refs(conn, part["id"]) | set(part["preferences"].get(_PROMOTED, ()))
        missing = missing_reference_dependencies(conn, part["id"], principal.tenant_id, refs, lock=True)
        _register(conn, part["id"], principal.tenant_id, missing)
    if values:
        values["updated_at"] = db.now()
        part.update(values)
        if "preferences" in values:
            try:
                values["preferences"] = json.dumps(values["preferences"], ensure_ascii=False, allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise ApiError("VALIDATION_FAILED", "整理偏好须使用有效的 JSON 值。") from exc
        conn.execute(
            "UPDATE ont_modules SET " + ",".join(k + "=?" for k in values) + " WHERE id=?",
            (*values.values(), part["id"]),
        )
    return _public(part)


def move_elements(
    conn: db.Connection, principal: Principal, source_module_id: str, target_module_id: str, entity_ids: list[str]
) -> dict:
    """Promote existing members atomically. Never dispatch an entity edit or create a new asset version."""
    from backend.domains.build import guard_adopted_write

    _write_scope(conn, principal)
    parts = {ident: _load(conn, ident, lock=True) for ident in sorted({source_module_id, target_module_id})}
    source, target = parts[source_module_id], parts[target_module_id]
    if source["module_type"] != "scenario" or target["id"] != COMMON:
        raise ApiError("PRECONDITION_FAILED", "请选择已有场景中的内容提升到本机构公共部分。")
    require_management(principal, target)
    if source["status"] != "active":
        raise ApiError("PRECONDITION_FAILED", "源场景已停用。")
    if (
        not entity_ids
        or any(not isinstance(eid, str) or not eid for eid in entity_ids)
        or len(set(entity_ids)) != len(entity_ids)
    ):
        raise ApiError("VALIDATION_FAILED", "请选择不同的已有要素。")
    ids = sorted(entity_ids)
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        "SELECT e.*,m.module_id FROM entities e LEFT JOIN entity_meta m ON m.entity_id=e.id "
        "WHERE e.id IN (" + placeholders + ") ORDER BY e.id FOR UPDATE",
        ids,
    ).fetchall()
    if {row["id"] for row in rows} != set(ids):
        raise ApiError("NOT_FOUND")
    for row in rows:
        entity = db.entity_json(row)
        guard_adopted_write(conn, entity)
        if not persisted_kind(entity["kind"]).module_eligible or (
            entity["kind"] == "object_type" and entity["payload"].get("is_interface")
        ):
            raise ApiError("PRECONDITION_FAILED", "该种类不能提升到公共部分。")
        if (
            row["module_id"] != source["id"]
            or entity["status"] == "archived"
            or entity["payload"].get("active_asset") == 0
        ):
            raise ApiError("PRECONDITION_FAILED", "所选内容须为源场景的当前业务资产。")
    stamp = db.now()
    try:
        conn.execute(
            "UPDATE entity_meta SET module_id=?,updated_at=? WHERE module_id=? AND entity_id IN (" + placeholders + ")",
            (COMMON, stamp, source["id"], *ids),
        )
    except db.pymysql.IntegrityError as exc:
        if exc.args and exc.args[0] == 1062:
            raise ApiError("PRECONDITION_FAILED", "公共部分已有相同业务标识，请先核对。") from exc
        raise
    protected = source["preferences"]
    protected[_PROMOTED] = sorted(set(protected.get(_PROMOTED, ())) | set(ids))
    conn.execute(
        "UPDATE ont_modules SET preferences=?,updated_at=? WHERE id=?",
        (json.dumps(protected, ensure_ascii=False), stamp, source["id"]),
    )
    dependents = conn.execute(
        "SELECT DISTINCT m.module_id FROM relations r JOIN entity_meta m ON m.entity_id=r.source "
        "JOIN ont_modules s ON s.id=m.module_id AND s.module_type='scenario' "
        "WHERE r.status IN (?,?) AND r.target IN (" + placeholders + ")",
        (*sorted(CONFIRMED), *ids),
    )
    for ident in sorted({source["id"], *(row["module_id"] for row in dependents)}):
        _register(conn, ident, principal.tenant_id, [COMMON])
    for eid in ids:
        db.log(conn, eid, "提升到公共部分", source["name"])
    return {"source_module_id": source["id"], "target_module_id": COMMON, "entity_ids": ids, "moved_count": len(ids)}
