"""Independently reviewed institution releases. The caller owns the tenant transaction."""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import quote

from backend import db, federation, sources
from backend.core.errors import ApiError
from backend.core.ids import new_id
from backend.domains.reviews import PUBLICATION_ACTIONS
from backend.kernel import modules
from backend.kernel import release_snapshots as snapshots
from backend.security.context import Principal
from backend.security.permissions import permissions_for

PUBLIC_STATES = {"published", "withdrawn"}
SUMMARY_FIELDS = (
    "suggested_version",
    "previous_release_id",
    "member_count",
    "ref_count",
    "submitted_by",
    "missing_dependencies",
)


def _tenant(conn: db.Connection, person: Principal, *, writing: bool = False) -> None:
    if person.identity_kind != "session" or (conn.principal and conn.principal != person):
        raise ApiError("PERMISSION_DENIED")
    if person.readonly and person.tenant_id != person.home_tenant_id:
        raise ApiError("NOT_FOUND")
    if writing and (person.readonly or conn.readonly):
        raise ApiError("PERMISSION_DENIED")


def _release(conn: db.Connection, release_id: str, *, lock: bool = False) -> dict:
    row = conn.execute(
        "SELECT * FROM ont_releases WHERE id=?" + (" FOR UPDATE" if lock else ""), (release_id,)
    ).fetchone()
    if not row:
        raise ApiError("NOT_FOUND")
    return {**dict(row), "stats": snapshots.decode(row["stats"] or "{}")}


def _lock_release(conn: db.Connection, person: Principal, release_id: str) -> tuple[dict, dict]:
    _tenant(conn, person, writing=True)
    # Acquire the ownership row first on every transition. A nonlocking lookup of
    # the release here would establish an InnoDB snapshot before a competing writer
    # commits a new head. FOR UPDATE OF m neither locks r nor creates such a view.
    row = conn.execute(
        "SELECT m.* FROM ont_modules m JOIN ont_releases r ON r.module_id=m.id "
        "WHERE r.id=? AND m.module_type IN ('org_common','scenario') FOR UPDATE OF m",
        (release_id,),
    ).fetchone()
    if not row:
        raise ApiError("NOT_FOUND")
    return dict(row), _release(conn, release_id, lock=True)


def _public_flow(module: dict) -> None:
    if module["module_type"] != "org_common" or module["status"] != "active":
        raise ApiError("PRECONDITION_FAILED", "目前仅开放机构公共部分的发布流程。")


def _frozen_items(conn: db.Connection, release_id: str, *, lock: bool = False) -> list[dict]:
    return [
        {**dict(row), "snapshot": snapshots.decode(row["snapshot"])}
        for row in conn.execute(
            "SELECT entity_id,entity_version,kind,snapshot,content_hash,item_role FROM ont_release_items "
            "WHERE release_id=? ORDER BY entity_id" + (" FOR SHARE" if lock else ""),
            (release_id,),
        )
    ]


def _summary(conn: db.Connection, person: Principal, release: dict, module: dict) -> dict:
    stats = release["stats"]
    result = {
        **{key: value for key, value in release.items() if key != "stats"},
        **{key: stats.get(key) for key in SUMMARY_FIELDS},
        "current_release_id": module["current_release_id"],
        "no_changes": False,
    }
    if release["status"] == "draft":
        result["missing_dependencies"] = modules.missing_module_dependencies(
            conn, module["id"], person.tenant_id, release_items=stats["preview_items"]
        )
    return result


def _detail(conn: db.Connection, person: Principal, release: dict, module: dict) -> dict:
    result = _summary(conn, person, release, module)
    stats = release["stats"]
    items = _frozen_items(conn, release["id"]) if release["status"] in PUBLIC_STATES else stats["preview_items"]
    return {**result, "items": items, "gates": stats["gates"], "diff": stats["diff"]}


def _can_read(person: Principal, release: dict) -> bool:
    return (
        release["status"] in PUBLIC_STATES and release["published_at"] is not None
    ) or "draft.read" in permissions_for(person)


def _project(conn: db.Connection, person: Principal, result: dict, private_refs: list[str]) -> dict:
    # Frozen rows store the accepted business snapshot. The response still uses
    # the reader's current source grants and published-only payload boundary.
    private = "draft.read" in permissions_for(person)
    items = [item for item in result["items"] if private or item["entity_id"] not in private_refs]
    visible = {
        item["entity_id"]: {
            "title": item["snapshot"]["title"],
            "org_id": item["snapshot"].get("org_id"),
            "detail_url": "/ontology/elements/" + quote(item["entity_id"], safe=""),
        }
        for item in items
    }
    source_access = {}
    projected = []
    for item in items:
        if item["item_role"] == "ref":
            projected.append(item)
            continue
        snap = dict(item["snapshot"])
        bindings = []
        for binding in snap["bindings"]:
            ident = binding["source_id"]
            if ident not in source_access:
                granted = sources._permissions(conn, {"id": ident}, person) if private and not person.readonly else {}
                source_access[ident] = bool(granted.get("discover") or granted.get("query"))
            if source_access[ident]:
                bindings.append(binding)
        snap["bindings"] = bindings
        if not private:
            snap["payload"], _ = federation._safe_payload(
                snap["payload"], {"id": person.tenant_id, "name": ""}, visible
            )
            snap["relations"] = [
                {key: value for key, value in edge.items() if key != "payload"}
                for edge in snap["relations"]
                if edge["source"] in visible and edge["target"] in visible
            ]
        projected.append({**item, "snapshot": snap})
    if not private:
        result = {
            **result,
            "gates": [],
            "missing_dependencies": [],
            "ref_count": sum(item["item_role"] == "ref" for item in items),
        }
    return {**result, "items": projected}


def get_release(conn: db.Connection, person: Principal, release_id: str) -> dict:
    _tenant(conn, person)
    release = _release(conn, release_id)
    if not _can_read(person, release):
        raise ApiError("NOT_FOUND")
    module = readable_module(conn, person, release["module_id"])
    return _project(conn, person, _detail(conn, person, release, module), release["stats"].get("private_refs", []))


def readable_module(conn: db.Connection, person: Principal, module_id: str) -> dict:
    _tenant(conn, person)
    module = modules.get_module(conn, module_id)
    if module["module_type"] != "org_common" and "draft.read" not in permissions_for(person):
        released = conn.execute(
            "SELECT 1 FROM ont_releases WHERE module_id=? AND status IN ('published','withdrawn') "
            "AND published_at IS NOT NULL LIMIT 1",
            (module_id,),
        ).fetchone()
        if not released:
            raise ApiError("NOT_FOUND")
    return module


def list_releases(conn: db.Connection, person: Principal, module_id: str = "MOD-DEFAULT") -> list[dict]:
    module = readable_module(conn, person, module_id)
    private = "draft.read" in permissions_for(person)
    where = "" if private else " AND status IN ('published','withdrawn') AND published_at IS NOT NULL"
    rows = conn.execute(
        "SELECT * FROM ont_releases WHERE module_id=?" + where + " ORDER BY created_at DESC,id", (module_id,)
    )
    results = []
    for row in rows:
        stats = snapshots.decode(row["stats"] or "{}")
        result = _summary(conn, person, {**dict(row), "stats": stats}, module)
        if not private:
            result.update(ref_count=result["ref_count"] - len(stats.get("private_refs", [])), missing_dependencies=[])
        results.append(result)
    return results


def _failed(check: dict) -> set[str]:
    return {item["code"] for item in check["checks"] if item["status"] == "fail"}


def _instant(value: object) -> datetime | None:
    if not isinstance(value, str) or len(value) <= 10:
        return None
    try:
        stamp = datetime.fromisoformat(value)
        return stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp.astimezone(UTC)
    except (ValueError, OverflowError):
        return None


def _legacy_at_upgrade(conn: db.Connection, checks: list[dict]) -> set[str]:
    """The tenant's restored migration receipt marks when release gates became enforceable."""
    failed = [check["entity_id"] for check in checks if not check["passed"]]
    if not failed:
        return set()
    receipt = conn.execute("SELECT applied_at FROM schema_migrations WHERE version=5").fetchone()
    cutoff = _instant(receipt["applied_at"]) if receipt else None
    if cutoff is None:
        return set()
    placeholders = ",".join("?" for _ in failed)
    actions = sorted(PUBLICATION_ACTIONS)
    action_params = ",".join("?" for _ in actions)
    rows = conn.execute(
        "SELECT e.id,e.updated_at,a.action,a.created_at AS published_at FROM entities e "
        f"LEFT JOIN activities a ON a.entity_id=e.id AND a.action IN ({action_params}) "
        f"WHERE e.id IN ({placeholders})",
        (*actions, *failed),
    )
    ineligible = set()
    for row in rows:
        # Some 0.7 published records have no publication activity. Their current
        # published row must itself predate rollout. A later publication receipt
        # independently disqualifies a record with an old or imported row stamp.
        updated = _instant(row["updated_at"])
        published = _instant(row["published_at"])
        if updated is None or updated >= cutoff or (row["action"] and (published is None or published >= cutoff)):
            ineligible.add(row["id"])
    return set(failed) - ineligible


def _prepare(conn: db.Connection, person: Principal, module: dict) -> tuple[dict, str, str]:
    historic_versions = [
        row["version"]
        for row in conn.execute(
            "SELECT version FROM ont_releases WHERE module_id=? AND published_at IS NOT NULL FOR SHARE", (module["id"],)
        )
    ]
    previous_id = module["current_release_id"]
    previous = _release(conn, previous_id, lock=True) if previous_id else None
    if previous and (previous["module_id"] != module["id"] or previous["status"] != "published"):
        raise ApiError("PRECONDITION_FAILED", "当前发布版本记录不一致。")
    prior_items = _frozen_items(conn, previous_id, lock=True) if previous else []
    promoted = modules.promoted_ids(conn, module["id"])
    items, checks, private_refs = snapshots.collect(conn, module["id"], promoted)
    diff, bump = snapshots.compare(prior_items, items, promoted)
    first = not historic_versions
    bump = "major" if first else bump
    suggested = "1.0.0" if first else snapshots.next_version(max(historic_versions, key=snapshots.version_parts), bump)
    legacy = _legacy_at_upgrade(conn, checks) if first else set()
    prior_gates = {check["entity_id"]: check for check in previous["stats"].get("gates", [])} if previous else {}
    changed = set(diff["added"]) | set(diff["changed"])
    for check in checks:
        prior = prior_gates.get(check["entity_id"])
        inherited = (
            prior and not prior["blocking"] and check["entity_id"] not in changed and _failed(check) <= _failed(prior)
        )
        # Preserve recorded first-release deficiencies only while exactly that
        # content and those pre-existing failing checks persist. New failures,
        # new assets and changed content must pass the ordinary release gates.
        check["blocking"] = bool(not check["passed"] and check["entity_id"] not in legacy and not inherited)
    refs = [item["entity_id"] for item in items if item["item_role"] == "ref"]
    stats = {
        "suggested_version": suggested,
        "previous_release_id": previous_id,
        "member_count": len(items) - len(refs),
        "ref_count": len(refs),
        "submitted_by": None,
        "diff": diff,
        "gates": checks,
        "preview_items": items,
        "private_refs": private_refs,
        "missing_dependencies": modules.missing_module_dependencies(
            conn, module["id"], person.tenant_id, release_items=items
        ),
    }
    return stats, bump, snapshots.manifest_hash(items)


def _version_collision(conn: db.Connection, module_id: str, version: str) -> dict | None:
    return conn.execute(
        "SELECT id,status,manifest_hash,changelog FROM ont_releases WHERE module_id=? AND version=? FOR UPDATE",
        (module_id, version),
    ).fetchone()


def create_draft(
    conn: db.Connection,
    person: Principal,
    module_id: str = "MOD-DEFAULT",
    *,
    version: str | None = None,
    changelog: str = "",
) -> dict:
    _tenant(conn, person, writing=True)
    module = modules.get_module(conn, module_id, lock=True)
    modules.require_management(person, module)
    stats, bump, manifest = _prepare(conn, person, module)
    current = module["current_release_id"]
    if current and not any(stats["diff"].values()):
        result = get_release(conn, person, current)
        snapshots.choose_version(result["version"], version)
        return {
            **result,
            "no_changes": True,
            "suggested_version": result["version"],
            "diff": stats["diff"],
            "gates": stats["gates"],
            "previous_release_id": current,
        }
    proposed = snapshots.choose_version(stats["suggested_version"], version)
    # The same preview can be retried. A different open draft/candidate keeps
    # its own immutable identity and reserved version; an implicit request uses
    # the next free patch, an explicitly occupied version is a conflict.
    while collision := _version_collision(conn, module_id, proposed):
        if (
            collision["status"] in {"draft", "candidate"}
            and collision["manifest_hash"] == manifest
            and collision["changelog"] == changelog
        ):
            existing = _release(conn, collision["id"], lock=True)
            if existing["stats"].get("previous_release_id") == current:
                if existing["status"] == "draft":
                    # A dependent fact or grant can change the gate result without
                    # changing the semantic manifest. Refresh the mutable preview.
                    stats["suggested_version"] = existing["stats"]["suggested_version"]
                    if stats != existing["stats"]:
                        conn.execute(
                            "UPDATE ont_releases SET stats=? WHERE id=?", (snapshots.encode(stats), existing["id"])
                        )
                        existing["stats"] = stats
                return _project(
                    conn, person, _detail(conn, person, existing, module), existing["stats"]["private_refs"]
                )
        if version is not None:
            raise ApiError("VERSION_CONFLICT", "该版本号已使用，请上调版本号。")
        proposed = snapshots.choose_version(stats["suggested_version"], snapshots.next_version(proposed, "patch"))
        stats["suggested_version"] = proposed
    ident = new_id("RLS-", lambda key: bool(conn.execute("SELECT id FROM ont_releases WHERE id=?", (key,)).fetchone()))
    conn.execute(
        "INSERT INTO ont_releases(id,module_id,version,status,bump,changelog,manifest_hash,stats,"
        "created_by,created_at) "
        "VALUES(?,?,?,'draft',?,?,?,?,?,?)",
        (ident, module["id"], proposed, bump, changelog, manifest, snapshots.encode(stats), person.user_id, db.now()),
    )
    return get_release(conn, person, ident)


def _validate_current(conn: db.Connection, person: Principal, module: dict, release: dict) -> dict:
    stats = release["stats"]
    if stats["previous_release_id"] != module["current_release_id"]:
        raise ApiError("VERSION_CONFLICT", "当前发布版本已变化，请重新生成草稿。")
    fresh, _, manifest = _prepare(conn, person, module)
    if manifest != release["manifest_hash"] or fresh["diff"] != stats["diff"]:
        raise ApiError("ITEM_CHANGED", "审核范围内的内容已变化，请重新生成发布草稿。")
    if snapshots.version_parts(release["version"]) < snapshots.version_parts(fresh["suggested_version"]):
        raise ApiError("VERSION_CONFLICT", "发布记录已变化，请重新生成草稿。")
    blockers = [check for check in fresh["gates"] if check["blocking"]]
    if blockers:
        raise ApiError("GATE_NOT_PASSED", "请补齐发布检查未通过的内容。", details={"gates": blockers})
    return fresh


def submit(conn: db.Connection, person: Principal, release_id: str, *, version: str | None = None) -> dict:
    module, release = _lock_release(conn, person, release_id)
    modules.require_management(person, module)
    _public_flow(module)
    if release["status"] != "draft":
        raise ApiError("VERSION_CONFLICT", "仅草稿可以提交审核。")
    selected = snapshots.choose_version(release["version"], version)
    if selected != release["version"] and _version_collision(conn, module["id"], selected):
        raise ApiError("VERSION_CONFLICT", "该版本号已使用。")
    fresh = _validate_current(conn, person, module, release)
    stats = {**release["stats"], "gates": fresh["gates"], "submitted_by": person.user_id}
    conn.execute(
        "UPDATE ont_releases SET status='candidate',version=?,stats=? WHERE id=?",
        (selected, snapshots.encode(stats), release_id),
    )
    return get_release(conn, person, release_id)


def _review(conn: db.Connection, person: Principal, release_id: str) -> tuple[dict, dict, dict]:
    module, release = _lock_release(conn, person, release_id)
    _public_flow(module)
    if "review.decide" not in permissions_for(person):
        raise ApiError("PERMISSION_DENIED")
    if release["status"] != "candidate" or not release["stats"].get("submitted_by"):
        raise ApiError("PRECONDITION_FAILED", "需要先提交发布草稿，再由其他审核人员发布。")
    if person.user_id in {release["created_by"], release["stats"]["submitted_by"]}:
        raise ApiError("SEPARATION_OF_DUTIES", "创建人和提交人均须由其他审核人员办理。")
    fresh = _validate_current(conn, person, module, release)
    return module, release, fresh


def authorize_publish(conn: db.Connection, person: Principal, release_id: str) -> dict:
    module, release, _ = _review(conn, person, release_id)
    return _project(conn, person, _detail(conn, person, release, module), release["stats"]["private_refs"])


def publish(
    conn: db.Connection,
    person: Principal,
    release_id: str,
    *,
    expected_version: str | None = None,
    expected_manifest_hash: str | None = None,
) -> dict:
    module, release, fresh = _review(conn, person, release_id)
    if (expected_version is not None and expected_version != release["version"]) or (
        expected_manifest_hash is not None and expected_manifest_hash != release["manifest_hash"]
    ):
        raise ApiError("VERSION_CONFLICT", "审核时核对的版本或清单已变化，请重新核对。")
    stats = dict(release["stats"])
    stats.pop("preview_items")
    stats.update(
        gates=fresh["gates"], private_refs=fresh["private_refs"], missing_dependencies=fresh["missing_dependencies"]
    )
    # Membership and all semantic hashes have been recomputed against the reviewed
    # manifest. Persist the live entity version receipts with that exact content.
    values = [
        (
            release_id,
            item["entity_id"],
            item["entity_version"],
            item["kind"],
            snapshots.encode(item["snapshot"]),
            item["content_hash"],
            item["item_role"],
        )
        for item in fresh["preview_items"]
    ]
    if values:
        conn.executemany(
            "INSERT INTO ont_release_items(release_id,entity_id,entity_version,kind,snapshot,content_hash,item_role) "
            "VALUES(?,?,?,?,?,?,?)",
            values,
        )
    stamp = db.now()
    conn.execute(
        "UPDATE ont_releases SET status='published',reviewed_by=?,published_at=?,stats=? WHERE id=?",
        (person.user_id, stamp, snapshots.encode(stats), release_id),
    )
    conn.execute(
        "UPDATE ont_modules SET current_release_id=?,updated_at=? WHERE id=?", (release_id, stamp, module["id"])
    )
    return get_release(conn, person, release_id)


def withdraw(conn: db.Connection, person: Principal, release_id: str) -> dict:
    module, release = _lock_release(conn, person, release_id)
    if "review.decide" not in permissions_for(person):
        raise ApiError("PERMISSION_DENIED")
    _public_flow(module)
    if release["status"] == "withdrawn" and release["published_at"]:
        return _project(conn, person, _detail(conn, person, release, module), release["stats"]["private_refs"])
    if release["status"] != "published":
        raise ApiError("VERSION_CONFLICT", "只有已发布的版本可以撤回。")
    conn.execute("UPDATE ont_releases SET status='withdrawn' WHERE id=?", (release_id,))
    if module["current_release_id"] == release_id:
        candidates = conn.execute(
            "SELECT id,version FROM ont_releases WHERE module_id=? AND status='published' "
            "AND published_at IS NOT NULL FOR SHARE",
            (module["id"],),
        ).fetchall()
        previous = (
            max(candidates, key=lambda row: snapshots.version_parts(row["version"]))["id"] if candidates else None
        )
        conn.execute(
            "UPDATE ont_modules SET current_release_id=?,updated_at=? WHERE id=?", (previous, db.now(), module["id"])
        )
    return get_release(conn, person, release_id)
