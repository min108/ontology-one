"""Tenant-local release contents and semantic version comparison (blueprint §4.8)."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Iterable

from backend import db
from backend.core.errors import ApiError
from backend.kernel import gates
from backend.kernel.kinds import KINDS

ASSET_KINDS = (*(key for key, value in KINDS.items() if value.module_eligible), "algorithm")
ENTITY_FIELDS = ("id", "kind", "title", "version", "summary", "domain", "org_id", "source_kind")
META_FIELDS = ("subtype", "element_key", "valid_from", "valid_to", "sensitivity", "provider_note")
# Facts created by the review, queue or audit path never describe an asset.
WORKFLOW_KEYS = frozenset(
    {
        "r2_review",
        "revision_history",
        "draft_editor_ids",
        "created_at",
        "updated_at",
        "submitted_at",
        "published_at",
        "reviewed_at",
        "approved_at",
        "created_by",
        "updated_by",
        "submitted_by",
        "published_by",
        "reviewed_by",
        "approved_by",
        "active_asset",
        "active",
    }
)
VERSION_PATTERN = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
BUMP_ORDER = ("patch", "minor", "major")


def encode(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def decode(value: str | dict) -> dict:
    return json.loads(value) if isinstance(value, str) else value


def digest(value: object) -> str:
    return hashlib.sha256(encode(value).encode("utf-8")).hexdigest()


def version_parts(version: str) -> tuple[int, int, int]:
    if not isinstance(version, str) or len(version) > 20 or not VERSION_PATTERN.fullmatch(version):
        raise ApiError("VALIDATION_FAILED", "版本号须为三段非负整数，例如 1.0.0。")
    major, minor, patch = map(int, version.split("."))
    return major, minor, patch


def choose_version(suggested: str, requested: str | None) -> str:
    if requested is None:
        requested = suggested
    if version_parts(requested) < version_parts(suggested):
        raise ApiError("VALIDATION_FAILED", "版本号只能保持建议值或上调。", details={"suggested_version": suggested})
    return requested


def next_version(previous: str, bump: str) -> str:
    major, minor, patch = version_parts(previous)
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def _semantic_member(snapshot: dict) -> dict:
    content = {key: value for key, value in snapshot.items() if key != "version"}
    # A derived edge can be recreated with another row ID. Its business endpoints,
    # role and step still identify the same assertion.
    content["relations"] = sorted(
        ({key: value for key, value in edge.items() if key != "id"} for edge in snapshot["relations"]), key=encode
    )
    content["bindings"] = sorted(
        ({key: value for key, value in binding.items() if key != "version"} for binding in snapshot["bindings"]),
        key=encode,
    )
    return content


def make_item(snapshot: dict, role: str) -> dict:
    return {
        "entity_id": snapshot["id"],
        "entity_version": snapshot["version"],
        "kind": snapshot["kind"],
        "item_role": role,
        "snapshot": snapshot,
        "content_hash": digest(_semantic_member(snapshot) if role == "member" else snapshot),
    }


def manifest_hash(items: list[dict]) -> str:
    hashes = "".join(item["content_hash"] for item in sorted(items, key=lambda item: item["entity_id"]))
    return hashlib.sha256(hashes.encode("ascii")).hexdigest()


def _expression(value: object) -> object:
    return value.get("oxl") if isinstance(value, dict) else value


def _judgement(value: object) -> object:
    if not isinstance(value, dict):
        return value
    exceptions = value.get("exceptions", [])
    return (
        _expression(value.get("condition")),
        _expression(value.get("requirement")),
        [_expression(item) for item in exceptions] if isinstance(exceptions, list) else exceptions,
        value.get("applies_to", {}).get("object_type")
        if isinstance(value.get("applies_to"), dict)
        else value.get("applies_to"),
    )


def _properties(payload: dict) -> dict:
    return {item["key"]: item for item in payload.get("properties", []) if isinstance(item, dict) and "key" in item}


def _identity(payload: dict) -> object:
    value = payload.get("identity")
    return value.get("properties") if isinstance(value, dict) else value


def _without_descriptions(value: object) -> object:
    # Compare functional fields after dropping prose. Future, unclassified fields
    # conservatively count as a feature change instead of a spelling correction.
    if isinstance(value, dict):
        text_fields = {
            "title",
            "summary",
            "definition",
            "description",
            "name",
            "label",
            "note",
            "sentence",
            "aliases",
            "source_refs",
            "source_chunk_ids",
            "basis",
            "provider_note",
            "examples",
            "caveats",
            "content",
        }
        return {key: _without_descriptions(child) for key, child in value.items() if key not in text_fields}
    if isinstance(value, list):
        return [_without_descriptions(child) for child in value]
    return value


def _change_level(before: dict, after: dict) -> str:
    a, b = before["payload"], after["payload"]
    if before["kind"] != after["kind"]:
        return "major"
    if after["kind"] == "object_type":
        old, new = _properties(a), _properties(b)
        if (
            old.keys() - new.keys()
            or _identity(a) != _identity(b)
            or any(old[key].get("datatype") != new[key].get("datatype") for key in old.keys() & new.keys())
        ):
            return "major"
        if new.keys() - old.keys():
            return "minor"
    if after["kind"] == "metric" and _expression(a.get("formula")) != _expression(b.get("formula")):
        return "major"
    if after["kind"] == "rule" and _judgement(a.get("judgement")) != _judgement(b.get("judgement")):
        return "major"
    return (
        "minor"
        if _without_descriptions(_semantic_member(before)) != _without_descriptions(_semantic_member(after))
        else "patch"
    )


def compare(previous: list[dict], current: list[dict], promoted_ids: Iterable[str]) -> tuple[dict, str]:
    old = {item["entity_id"]: item for item in previous if item["item_role"] == "member"}
    new = {item["entity_id"]: item for item in current if item["item_role"] == "member"}
    refs = {item["entity_id"] for item in current if item["item_role"] == "ref"}
    promoted = (old.keys() - new.keys()) & refs & set(promoted_ids)
    removed = old.keys() - new.keys() - promoted
    added = new.keys() - old.keys()
    changed = {key for key in old.keys() & new.keys() if old[key]["content_hash"] != new[key]["content_hash"]}
    levels = [_change_level(old[key]["snapshot"], new[key]["snapshot"]) for key in changed]
    levels.append("major" if removed else "minor" if added or promoted else "patch")
    diff = {
        "added": sorted(added),
        "changed": sorted(changed),
        "removed": sorted(removed),
        "promoted": sorted(promoted),
    }
    return diff, max(levels, key=BUMP_ORDER.index)


def _semantic_payload(entity: dict) -> dict:
    payload = {key: value for key, value in entity["payload"].items() if key not in WORKFLOW_KEYS}
    for field in ("aliases", "source_refs", "source_chunk_ids"):
        if isinstance(payload.get(field), list):
            payload[field] = sorted(payload[field], key=encode)
    if entity["kind"] == "object_type" and isinstance(payload.get("properties"), list):
        payload["properties"] = sorted(
            payload["properties"], key=lambda prop: prop.get("key", "") if isinstance(prop, dict) else ""
        )
    return payload


def _relations(conn: db.Connection, members: dict) -> tuple[dict[str, list[dict]], set[str]]:
    edges = defaultdict(list)
    external = set()
    if not members:
        return edges, external
    placeholders = ",".join("?" for _ in members)
    for row in conn.execute(
        f"SELECT * FROM relations WHERE status IN (?,?) AND "
        f"(source IN ({placeholders}) OR target IN ({placeholders})) ORDER BY id",
        (*sorted(gates.CONFIRMED), *members, *members),
    ):
        endpoints = {row["source"], row["target"]}
        external.update(endpoints - members.keys())
        payload = decode(row["payload"] or "{}")
        # Existing authority, skill and placement rows contain mutable review
        # receipts, actor names and copies of the other endpoint's full payload.
        # Keep the relationship and its authored attribution, never that history.
        edge = {key: row[key] for key in ("id", "source", "target", "type", "label", "status")}
        edge["payload"] = {
            key: payload[key]
            for key in ("role", "step_key", "dependency_kind", "source_refs", "note")
            if key in payload
        }
        for ident in endpoints & members.keys():
            edges[ident].append(edge)
    return edges, external


def _bindings(conn: db.Connection, members: dict) -> dict[str, list[dict]]:
    """Freeze only published source mappings that actually bind a member.

    No source connection, credential, discovery or row query is performed.
    Local dictionary 'confirmed' mappings have a different lifecycle (§4.10).
    """
    result = defaultdict(list)
    if not members:
        return result
    for row in conn.execute(
        "SELECT id,source_id,resource_id,title,version,definition,schema_hash FROM source_mappings "
        "WHERE status='published' ORDER BY id"
    ):
        definition = decode(row["definition"])
        targets = {item["asset_id"] for item in definition.get("fields", [])}
        targets.update(item["target_asset_id"] for item in definition.get("relation_keys", []))
        for ident in targets & members.keys():
            binding = {key: row[key] for key in ("id", "source_id", "resource_id", "title", "version", "schema_hash")}
            binding.update(
                {key: definition[key] for key in ("business_meaning", "grain", "object_key") if key in definition}
            )
            # Other targets may belong to a different scene. Freeze only the
            # association of this member with the published source definition.
            binding["fields"] = [item for item in definition.get("fields", []) if item["asset_id"] == ident]
            binding["relation_keys"] = [
                item for item in definition.get("relation_keys", []) if item["target_asset_id"] == ident
            ]
            result[ident].append(binding)
    return result


def collect(conn: db.Connection, module_id: str, promoted: set[str]) -> tuple[list[dict], list[dict], list[str]]:
    """Read one consistent tenant snapshot; callers serialize the release head."""
    kind_params = ",".join("?" for _ in ASSET_KINDS)
    rows = conn.execute(
        "SELECT e.*,"
        + ",".join("m." + key for key in META_FIELDS)
        + " FROM entities e LEFT JOIN entity_meta m ON m.entity_id=e.id WHERE "
        f"e.kind IN ({kind_params}) AND e.status='published' AND COALESCE(m.module_id,'MOD-DEFAULT')=? "
        "AND COALESCE(json_extract(e.payload,'$.active_asset'),1)!=0 ORDER BY e.id",
        (*ASSET_KINDS, module_id),
    ).fetchall()
    members = {row["id"]: db.entity_json(row) for row in rows}
    edges, external = _relations(conn, members)
    external.update(promoted - members.keys())
    bindings = _bindings(conn, members)
    items, checks, private_refs = [], [], []
    refs = {}
    if external:
        placeholders = ",".join("?" for _ in external)
        for row in conn.execute(
            f"SELECT e.id,e.kind,e.title,e.version,e.status,m.module_id FROM entities e "
            f"LEFT JOIN entity_meta m ON m.entity_id=e.id WHERE e.id IN ({placeholders}) "
            "AND COALESCE(json_extract(e.payload,'$.active_asset'),1)!=0 ORDER BY e.id",
            sorted(external),
        ):
            # A same-module draft is neither a published member nor an external
            # reference. Promotion points to currently published public content.
            if row["kind"] in ASSET_KINDS and (
                row["status"] != "published" or (row["module_id"] or "MOD-DEFAULT") == module_id
            ):
                continue
            refs[row["id"]] = {key: row[key] for key in ("id", "kind", "title", "version")}
            if row["status"] != "published":
                private_refs.append(row["id"])
    valid = members.keys() | refs.keys()
    for ident, entity in members.items():
        snapshot = {key: entity[key] for key in ENTITY_FIELDS}
        snapshot.update(
            meta={key: entity.get(key) for key in META_FIELDS},
            payload=_semantic_payload(entity),
            relations=[edge for edge in edges[ident] if edge["source"] in valid and edge["target"] in valid],
            bindings=bindings[ident],
        )
        if entity["kind"] == "skill" and entity["payload"].get("managed_skill"):
            # The package remains on the controlled skill surface. Its immutable
            # digest identifies package changes even when the entity title is stable.
            package = conn.execute("SELECT digest FROM skill_documents WHERE entity_id=?", (ident,)).fetchone()
            if package:
                snapshot["package_digest"] = package["digest"]
        items.append(make_item(snapshot, "member"))
        result = gates.check_entity(conn, entity)
        checks.append(
            {
                "entity_id": ident,
                "passed": result["passed"],
                "blocking": not result["passed"],
                "checks": [
                    {key: item[key] for key in ("code", "label", "status", "reason")} for item in result["checks"]
                ],
            }
        )
    items.extend(make_item(ref, "ref") for ref in refs.values())
    return sorted(items, key=lambda item: item["entity_id"]), checks, private_refs
