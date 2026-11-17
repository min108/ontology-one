"""Installation-owned recovery SQL, also usable by the historical fixture loader.

This module deliberately uses only the standard library so its validator can be
loaded while the fixture's actual 0.7.0 backend is on sys.path.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


def trusted_schema(kind: str, version: int | None = None) -> dict:
    if version is None:
        version = 7 if kind == "tenant" else 1
    if version not in {1, 2, 3, 4, 5, 6, 7}:
        raise ValueError("未知的受信恢复结构版本。")
    path = Path(__file__).resolve().parents[2] / "migrations" / "restore" / f"v{version}.json"
    registry = json.loads(path.read_text())
    if registry.get("format") != "ontology-trusted-restore-schema-v1" or kind not in {"control", "tenant"}:
        raise ValueError("安装包恢复结构无效。")
    return registry["schemas"][kind]


def normalized_ddl(value: str) -> str:
    # MySQL can print redundant per-column character sets. Numeric allocation
    # counters are restored separately with a fixed ALTER TABLE template.
    value = re.sub(r"^CREATE DEFINER=`[^`]+`@`[^`]+`\s+", "CREATE ", value)
    # Normalize only SQL outside quoted identifiers/literals. Otherwise a fake
    # DEFAULT literal could disappear during whitespace/option normalization.
    parts = re.split(r"('(?:\\.|''|[^'])*'|\"(?:\\.|\"\"|[^\"])*\"|`(?:``|[^`])*`)", value)
    for index in range(0, len(parts), 2):
        part = re.sub(r" AUTO_INCREMENT=\d+", "", parts[index])
        part = part.replace(" CHARACTER SET utf8mb4", "")
        parts[index] = " ".join(part.split())
    return " ".join(part for part in parts if part)


def validate_schema(value: dict, kind: str) -> dict:
    trusted = schema_for_snapshot(value, kind)
    optional = {"schema_migrations", "demo_installations"} if kind == "tenant" else {"schema_migrations"}
    required = set(trusted["ddl"]) - optional
    if not required <= set(value["tables"]) <= set(trusted["ddl"]):
        raise ValueError("备份表集合与受信任的恢复结构不一致。")
    if set(value["tables"]) != set(value["ddl"]) or set(value["tables"]) != set(value["columns"]):
        raise ValueError("备份结构清单不完整。")
    if set(value.get("triggers", {})) != set(trusted["triggers"]):
        raise ValueError("备份审计约束缺失或包含未知触发器。")
    for name, statement in value["triggers"].items():
        if normalized_ddl(statement) != normalized_ddl(trusted["triggers"][name]):
            raise ValueError("备份审计约束被修改。")
    for table, rows in value["tables"].items():
        if normalized_ddl(value["ddl"][table]) != normalized_ddl(trusted["ddl"][table]):
            raise ValueError("备份表结构与受信任的恢复定义不一致。")
        columns = value["columns"][table]
        if columns != trusted["columns"][table] or any(set(row) != set(columns) for row in rows):
            raise ValueError("备份行与受信任的列清单不一致。")
    return trusted


def schema_for_snapshot(value: dict, kind: str) -> dict:
    """Select an exact schema from checked, continuous installation-owned history.

    Legacy snapshots have no history. The history table itself is verified with
    the immutable v1 definition before any row can select a newer schema.
    """
    baseline = trusted_schema(kind, 1)
    rows = value["tables"].get("schema_migrations")
    if rows is None:
        return baseline
    columns = baseline["columns"]["schema_migrations"]
    if (
        value["columns"].get("schema_migrations") != columns
        or normalized_ddl(value["ddl"].get("schema_migrations", ""))
        != normalized_ddl(baseline["ddl"]["schema_migrations"])
        or any(set(row) != set(columns) for row in rows)
    ):
        raise ValueError("迁移历史结构与受信定义不一致。")
    folder = Path(__file__).resolve().parents[2] / "migrations" / kind
    versions = sorted(rows, key=lambda row: row["version"])
    for index, row in enumerate(versions, 1):
        paths = list(folder.glob(f"{index:04d}_*.py"))
        if len(paths) != 1 or type(row["version"]) is not int or row["version"] != index:
            raise ValueError("备份迁移历史不连续或版本不受支持。")
        path = paths[0]
        if row["name"] != path.stem or row["checksum"] != hashlib.sha256(path.read_bytes()).hexdigest():
            raise ValueError("备份迁移历史与已发布定义不一致。")
    supported = {"control": {0: 1, 1: 1, 2: 2, 3: 3, 4: 4, 5: 4, 6: 7}, "tenant": {0: 1, 1: 1, 2: 5, 3: 6, 4: 7}}
    version = supported[kind].get(len(versions))
    if version is None:
        raise ValueError("备份迁移版本尚未登记受信恢复结构。")
    return trusted_schema(kind, version)
