"""Installation-owned recovery SQL, also usable by the historical fixture loader.

This module deliberately uses only the standard library so its validator can be
loaded while the fixture's actual 0.7.0 backend is on sys.path.
"""

from __future__ import annotations

import json
import re
from pathlib import Path


def trusted_schema(kind: str) -> dict:
    path = Path(__file__).resolve().parents[2] / "migrations" / "restore" / "v1.json"
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
    trusted = trusted_schema(kind)
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
