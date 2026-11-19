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
from backend.provenance import ensure_source_version, normalize_source_ref

MEMO_ID = "R4A1-OLD-MEMO"
BAD_RULE_ID = "R4A1-OLD-BAD-RULE"


def _database(names: dict[str, str]) -> str:
    namespace = os.environ.get("MYSQL_DATABASE", "")
    if not re.fullmatch(r"e2e_r1_[0-9a-f]{24}", namespace) or not names["hq"].startswith(namespace[:32] + "_t_"):
        raise ValueError("A.1 legacy examples require an isolated browser-test namespace")
    return names["hq"]


SOURCE_ID = "R4A2-OLD-FILING-NOTE"
SOURCE_QUOTE = "档案移交时，接收人员依据登记簿逐项核对卷宗。仅用于纸质档案交接。"


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
        {
            "id": SOURCE_ID,
            "kind": "material",
            "title": "旧档案室移交记录",
            "status": "published",
            "payload": {"original_text": SOURCE_QUOTE},
        },
        *[
            {
                "id": ident,
                "kind": "knowledge",
                "title": title,
                "status": "approved",
                "payload": {
                    "definition": "按登记簿复核待移交卷宗的方法",
                    "usage_conditions": "档案移交时",
                    "boundary": "仅用于纸质档案交接",
                },
            }
            for ident, title in (
                ("R4A2-OLD-APPROVED", "已审核的档案移交方法"),
                ("R4A2-OLD-APPROVED-PAPER", "已审核的纸质档案登记方法"),
            )
        ],
        *[
            {
                "id": "R4A2-OLD-RULE-" + operation,
                "kind": "rule",
                "title": "旧登记规则 · " + operation,
                "status": "review",
                "payload": {"definition": "历史登记规则", "judgement": {"applies_to": None}},
            }
            for operation in ("REVISE", "ARCHIVE", "RETURN")
        ],
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
            if entity["id"] == "R4A2-OLD-APPROVED":
                source = ensure_source_version(conn, SOURCE_ID)
                anchor = normalize_source_ref(
                    conn,
                    SOURCE_ID,
                    {"source_version": source["id"], "start": 0, "end": len(SOURCE_QUOTE), "quote": SOURCE_QUOTE},
                )
                entity["payload"]["source_refs"] = [anchor]
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
            if entity["id"].startswith("R4A2-OLD-APPROVED"):
                for action, actor in (("创建", "历史编录员"), ("提交审核", "历史编录员"), ("审核通过", "历史审核员")):
                    conn.execute(
                        "INSERT INTO activities(entity_id,action,actor,detail,created_at) VALUES(?,?,?,?,?)",
                        (entity["id"], action, actor, "升级前归档的办理记录", entity["created_at"]),
                    )
        conn.commit()


def verify_legacy(names: dict[str, str]) -> dict:
    from backend.infra.migrations import migrate_database

    assert migrate_database("tenant", _database(names), tenant_id="hq") == []
    with closing(db._raw_connection(_database(names))) as raw:
        conn = db.Connection(raw)
        for example in _examples():
            stored = db.entity_json(conn.execute("SELECT * FROM entities WHERE id=?", (example["id"],)).fetchone())
            if example["id"] == "R4A2-OLD-APPROVED":
                anchor = stored["payload"]["source_refs"][0]
                assert anchor["material_id"] == SOURCE_ID and anchor["quote"] == SOURCE_QUOTE
                example["payload"]["source_refs"] = [anchor]
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
            "ids": [row["id"] for row in _examples()],
            "unchanged": True,
            "migration_repeated": True,
            "backfill_runs": 2,
        }
