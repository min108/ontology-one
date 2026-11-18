"""A.1 immutable old-format examples, inserted only before semantic migration.

The headquarters records do not change the department's AA2 population. They
represent historical incompatibilities, never fixtures that bypass release gates.
"""

import json
import os
import re
from contextlib import closing

from backend import db
from backend.kernel.metadata import backfill_meta

MEMO_ID = "R4A1-OLD-MEMO"
BAD_RULE_ID = "R4A1-OLD-BAD-RULE"


def _database(names: dict[str, str]) -> str:
    namespace = os.environ.get("MYSQL_DATABASE", "")
    if not re.fullmatch(r"e2e_r1_[0-9a-f]{24}", namespace) or not names["hq"].startswith(namespace[:32] + "_t_"):
        raise ValueError("A.1 legacy examples require an isolated browser-test namespace")
    return names["hq"]


def _examples() -> list[dict]:
    return [
        {
            "id": MEMO_ID,
            "kind": "memo",
            "title": "升级前的工作备忘",
            "status": "draft",
            "payload": {"content": "旧系统保存的工作记录，保留原有种类与编号。", "definition": "历史工作备忘"},
        },
        {
            "id": BAD_RULE_ID,
            "kind": "rule",
            "title": "升级前待修复的判定式",
            "status": "review",
            "payload": {
                "definition": "旧系统保存的核对规则",
                "judgement": {
                    "applies_to": None,
                    "condition": {"oxl": "true"},
                    "requirement": {"oxl": "true"},
                },
            },
        },
    ]


def seed_legacy(names: dict[str, str]) -> None:
    with closing(db._raw_connection(_database(names))) as raw:
        conn = db.Connection(raw)
        if conn.execute("SHOW TABLES LIKE 'entity_meta'").fetchone():
            raise ValueError("Old malformed records must be seeded before semantic migration")
        for example in _examples():
            entity = {
                **example,
                "summary": "合成的升级前兼容性验收记录",
                "domain": "综合管理",
                "org_id": "hq",
                "source_kind": "manual",
                "created_at": "2026-11-17T00:00:00+00:00",
                "updated_at": "2026-11-17T00:00:00+00:00",
                "version": 1,
            }
            columns = tuple(entity)
            conn.execute(
                "INSERT INTO entities(" + ",".join(columns) + ") VALUES(" + ",".join("?" for _ in columns) + ")",
                tuple(
                    json.dumps(value, ensure_ascii=False) if key == "payload" else value
                    for key, value in entity.items()
                ),
            )
            conn.execute(
                "INSERT INTO versions(entity_id,version,snapshot,created_at,reason) VALUES(?,?,?,?,?)",
                (entity["id"], 1, json.dumps(entity, ensure_ascii=False), entity["created_at"], "升级前保存的工作记录"),
            )
        conn.commit()


def verify_legacy(names: dict[str, str]) -> dict:
    from backend.infra.migrations import migrate_database

    assert migrate_database("tenant", _database(names), tenant_id="hq") == []
    with closing(db._raw_connection(_database(names))) as raw:
        conn = db.Connection(raw)
        for example in _examples():
            stored = db.entity_json(conn.execute("SELECT * FROM entities WHERE id=?", (example["id"],)).fetchone())
            assert {key: stored[key] for key in example} == example
            assert stored["version"] == 1
        before = [dict(row) for row in conn.execute("SELECT * FROM entity_meta ORDER BY entity_id")]
        assert backfill_meta(conn) == 0
        assert backfill_meta(conn) == 0
        after = [dict(row) for row in conn.execute("SELECT * FROM entity_meta ORDER BY entity_id")]
        assert before == after
        memo = next(row for row in after if row["entity_id"] == MEMO_ID)
        assert memo["pillar"] == "aux" and memo["module_id"] is None
        return {
            "tenant": "hq",
            "ids": [MEMO_ID, BAD_RULE_ID],
            "unchanged": True,
            "migration_repeated": True,
            "backfill_runs": 2,
        }
