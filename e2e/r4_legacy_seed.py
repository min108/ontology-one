"""AA1/AA2: preserve the 26 genuine fixture rows; add named synthetic 0.7-format copies.

Run seed after the original loader and before migrate, then verify immediately
following migrate. Evidence explicitly separates original and synthetic records.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
from contextlib import closing
from pathlib import Path

from backend import db
from backend.infra.backup import canonical, snapshot_database
from backend.provenance import normalize_source_ref
from backend.security import store
from e2e.r4a1_legacy_seed import seed_legacy, verify_legacy

TABLES = ("entities", "relations", "versions", "activities")
SCOPES = ("hq", "dept-01", "sub-01")
TARGET = 200


def digest(rows: list[dict]) -> dict:
    encoded = sorted(canonical(row) for row in rows)
    return {"count": len(rows), "row_hashes": [hashlib.sha256(row.encode()).hexdigest() for row in encoded]}


def preservation(snapshots: dict) -> dict:
    return {tenant: {table: digest(snapshots[tenant]["tables"][table]) for table in TABLES} for tenant in SCOPES}


def assert_preserved(before: dict, after: dict) -> None:
    if before != after:
        raise AssertionError("Migration changed a historical row or count in the four protected tables")


def databases() -> dict:
    namespace = os.environ.get("MYSQL_DATABASE", "")
    if not re.fullmatch(r"e2e_r1_[0-9a-f]{24}", namespace):
        raise ValueError("Legacy expansion requires a disposable browser-test namespace")
    tenants = {row["id"]: row["db_name"] for row in store.list_tenants() if row["id"] in SCOPES}
    if set(tenants) != set(SCOPES) or any(not value.startswith(namespace[:32] + "_t_") for value in tenants.values()):
        raise ValueError("Fixture tenant databases are outside the isolated namespace")
    return tenants


def seed(path: Path) -> None:
    if path.exists():
        raise ValueError("Legacy acceptance receipt already exists")
    names = databases()
    original = {tenant: snapshot_database(name) for tenant, name in names.items()}
    if any("entity_meta" in snapshot["tables"] for snapshot in original.values()):
        raise ValueError("Legacy copies must be created before the semantic migration")
    seed_legacy(names)
    dept = original["dept-01"]
    rows = sorted(dept["tables"]["entities"], key=lambda row: row["id"])
    if len(rows) >= TARGET or any(row["id"].startswith("R4-LEGACY-COPY-") for row in rows):
        raise ValueError("Unexpected original fixture population")
    source = next(row for row in dept["tables"]["source_versions"] if row["material_id"] == "DEMO07-MATERIAL-RULES")
    # Use distinct, immutable excerpts of the original fixture, including text following an earlier number.
    quotes = ("应核实审批依据", "累计付款金额与合同金额应按同一合同编号比较。", "附件信息为空只表示尚未核实")
    items = [
        {"id": row["id"], "title": row["title"], "status": row["status"], "origin": "original_v070", "source": None}
        for row in rows
    ]
    columns = dept["columns"]["entities"]
    with closing(db._raw_connection(names["dept-01"])) as raw:
        conn = db.Connection(raw)
        references = []
        for quote in quotes:
            start = source["text"].index(quote)
            ref = normalize_source_ref(
                conn,
                source["material_id"],
                {
                    "source_version": source["id"],
                    "quote": quote,
                    "start": start,
                    "end": start + len(quote),
                },
            )
            references.append({"kind": "anchor", **ref})
        for index in range(TARGET - len(rows)):
            original_row = rows[index % len(rows)]
            clone = copy.deepcopy(original_row)
            clone["id"] = f"R4-LEGACY-COPY-{index + 1:04d}"
            clone["title"] = f"合成旧格式副本{index + 1:03d} · {original_row['title']}"
            payload = json.loads(clone["payload"]) if isinstance(clone["payload"], str) else clone["payload"]
            anchored = index % 4 == 0
            reference = references[index // 4 % len(references)]
            if anchored:
                payload["source_refs"] = [reference]
            clone["payload"] = json.dumps(payload, ensure_ascii=False)
            conn.execute(
                "INSERT INTO entities(" + ",".join(columns) + ") VALUES(" + ",".join("?" for _ in columns) + ")",
                tuple(clone[column] for column in columns),
            )
            # A stored old-format snapshot makes each copy a genuine migration input.
            snapshot = {**clone, "payload": payload}
            conn.execute(
                "INSERT INTO versions(entity_id,version,snapshot,created_at,reason) VALUES(?,?,?,?,?)",
                (
                    clone["id"],
                    clone["version"],
                    json.dumps(snapshot, ensure_ascii=False),
                    clone["updated_at"],
                    "合成旧格式副本",
                ),
            )
            items.append(
                {
                    "id": clone["id"],
                    "title": clone["title"],
                    "status": clone["status"],
                    "origin": "synthetic_legacy_copy",
                    "copied_from": original_row["id"],
                    "source": reference if anchored else None,
                }
            )
        conn.commit()
    expanded = {tenant: snapshot_database(name) for tenant, name in names.items()}
    for tenant in SCOPES:
        for table in TABLES:
            # Existing rows remain exact, including historical snapshots and IDs.
            after = {canonical(row) for row in expanded[tenant]["tables"][table]}
            if any(canonical(row) not in after for row in original[tenant]["tables"][table]):
                raise AssertionError("Synthetic fixture expansion changed an original row")
    path.write_text(
        json.dumps(
            {
                "description": (
                    "24 original department records plus 176 explicitly synthetic legacy-format copies; "
                    "not 200 original fixture records"
                ),
                "original_counts": {tenant: len(original[tenant]["tables"]["entities"]) for tenant in SCOPES},
                "browser_tenant": "dept-01",
                "browser_original_count": len(rows),
                "browser_synthetic_count": TARGET - len(rows),
                "browser_total": len(items),
                "sourced_synthetic_count": sum(item["source"] is not None for item in items),
                "before_migration": preservation(expanded),
                "items": items,
                "migration_verified": False,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )


def verify(path: Path) -> None:
    evidence = json.loads(path.read_text())
    names = databases()
    snapshots = {tenant: snapshot_database(name) for tenant, name in names.items()}
    after = preservation(snapshots)
    assert_preserved(evidence["before_migration"], after)
    for snapshot in snapshots.values():
        if len(snapshot["columns"]["entities"]) != 12 or len(snapshot["columns"]["relations"]) != 7:
            raise AssertionError("Protected table column contracts changed")
        if sorted(row["id"] for row in snapshot["tables"]["entities"]) != sorted(
            row["entity_id"] for row in snapshot["tables"]["entity_meta"]
        ):
            raise AssertionError("Every migrated entity must have exactly one metadata row")
    from backend.infra.migrations import run_migrations

    if run_migrations()["migrated"]:
        raise AssertionError("A second migration run must be a no-op")
    repeated = {tenant: snapshot_database(name) for tenant, name in names.items()}
    if any(snapshots[tenant]["hashes"] != repeated[tenant]["hashes"] for tenant in SCOPES):
        raise AssertionError("Repeat migration changed tenant data")
    broken = copy.deepcopy(after)
    broken["dept-01"]["entities"]["row_hashes"][0] = "0" * 64
    try:
        assert_preserved(evidence["before_migration"], broken)
    except AssertionError:
        pass
    else:
        raise AssertionError("Preservation assertion did not reject a modified row")
    evidence.update(
        legacy_compatibility=verify_legacy(names),
        migration_verified=True,
        after_migration=after,
        repeat_migration_unchanged=True,
        row_hash_counterexample_rejected=True,
    )
    path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("seed", "verify"))
    args = parser.parse_args()
    path = Path(os.environ["E2E_EVIDENCE_DIR"]) / "r4-legacy.json"
    (seed if args.phase == "seed" else verify)(path)
    print(json.dumps({"phase": args.phase, "status": "passed", "receipt": path.name}))


if __name__ == "__main__":
    main()
