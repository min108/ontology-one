"""Two pre-R4 pending-review examples, only in the runner's disposable namespace.

These records deliberately predate submission gates: one can be independently
waived, the other lacks a non-waivable relationship. No public mutation path is
used to bypass current gates; the fixture is loaded before services start.
"""

from __future__ import annotations

import json
import os
import re
from contextlib import closing

import pymysql

from backend import db


def main() -> None:
    from backend.kernel.gates import check_entity
    from backend.kernel.metadata import sync_meta

    namespace = os.environ.get("MYSQL_DATABASE", "")
    if not re.fullmatch(r"e2e_r1_[0-9a-f]{24}", namespace):
        raise ValueError("This seed only accepts a newly generated disposable browser-test namespace")
    config = {
        "host": os.getenv("MYSQL_HOST", "127.0.0.1"),
        "port": int(os.getenv("MYSQL_PORT", "3306")),
        "user": os.getenv("MYSQL_USER", "root"),
        "password": os.getenv("MYSQL_PASSWORD", ""),
        "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor,
    }
    with closing(pymysql.connect(**config, database=namespace + "_control")) as control, control.cursor() as cursor:
        cursor.execute("SELECT db_name FROM sec_tenants WHERE id=%s", ("dept-01",))
        database = cursor.fetchone()["db_name"]
        cursor.execute("SELECT id,username FROM sec_users WHERE username=%s", ("fixture.builder",))
        builder = cursor.fetchone()
    if not database.startswith(namespace + "_t_"):
        raise ValueError("Tenant must belong to the disposable namespace")
    with closing(db.Connection(pymysql.connect(**config, database=database))) as conn:
        timestamp = db.now()
        for suffix, title in (
            ("WAIVABLE", "历史待审：需补出处与条件"),
            ("RELATION-MISSING", "历史待审：适用关系未确认"),
        ):
            identifier = "R4-LEGACY-" + suffix
            payload = {
                "definition": title,
                "content": "已在旧版提交、等待独立审核的试行要求。",
                "scope": "本机构试行事项",
                "source_refs": [],
                "r2_review": {
                    "last_editor_id": builder["id"],
                    "submitted_by": builder["id"],
                    "submitted_name": builder["username"],
                    "submitted_at": timestamp,
                },
            }
            entity = {
                "id": identifier,
                "kind": "rule",
                "title": title,
                "summary": title,
                "status": "review",
                "domain": "治理",
                "org_id": "dept-01",
                "source_kind": "manual",
                "version": 1,
                "payload": payload,
                "created_at": timestamp,
                "updated_at": timestamp,
            }
            conn.execute(
                "INSERT INTO entities(id,kind,title,summary,status,domain,org_id,source_kind,version,payload,"
                "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    "rule",
                    title,
                    title,
                    "review",
                    "治理",
                    "dept-01",
                    "manual",
                    1,
                    json.dumps(payload, ensure_ascii=False),
                    timestamp,
                    timestamp,
                ),
            )
            meta = sync_meta(conn, entity)
            conn.execute(
                "INSERT INTO versions(entity_id,version,snapshot,created_at,reason) VALUES(?,?,?,?,?)",
                (identifier, 1, json.dumps({**entity, "_meta": meta}, ensure_ascii=False), timestamp, "旧版待审快照"),
            )
            conn.execute(
                "INSERT INTO activities(entity_id,action,detail,actor,created_at) VALUES(?,?,?,?,?)",
                (identifier, "提交审核", "旧版已提交，升级后独立审核", builder["username"], timestamp),
            )
            conn.execute(
                "INSERT INTO entity_ownership(entity_id,tenant_id,created_by,created_at,updated_by,updated_at) "
                "VALUES(?,?,?,?,?,?)",
                (identifier, "dept-01", builder["id"], timestamp, builder["id"], timestamp),
            )
            if suffix == "WAIVABLE":
                conn.execute(
                    "INSERT INTO relations(id,source,target,type,label,status,payload) VALUES(?,?,?,?,?,?,?)",
                    ("R4-LEGACY-APPLIES", identifier, "dept-01", "applies_to", "适用于", "confirmed", "{}"),
                )
            checks = check_entity(conn, entity)["checks"]
            failures = {item["code"] for item in checks if item["status"] == "fail"}
            expected = {"QG1", "QG3"} if suffix == "WAIVABLE" else {"QG1", "QG3", "QG4"}
            if failures != expected:
                raise AssertionError(f"Legacy review seed has unexpected failures: {failures}")
        conn.commit()


if __name__ == "__main__":
    main()
