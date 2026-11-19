"""Administrator-requested rebuild of the selected institution's search projection."""

import json
from contextlib import closing
from dataclasses import asdict
from uuid import uuid4

import pymysql

from backend import db, jobs
from backend.core.errors import ApiError
from backend.infra.migrations import database_name
from backend.infra.search_index import rebuild_index
from backend.security import store
from backend.security.context import Principal, require_context
from backend.security.permissions import permissions_for


def _require_admin(person: Principal) -> None:
    if person.identity_kind != "session" or "admin.settings" not in permissions_for(person) or person.readonly:
        raise ApiError("PERMISSION_DENIED")


def enqueue_rebuild(person: Principal) -> dict:
    if require_context() != person:
        raise ApiError("PERMISSION_DENIED")
    person = store.assert_principal_current(person, require_write=True)
    _require_admin(person)
    queue = f"`{database_name(store._config()['database'])}`.ontology_task_queue"
    slot = jobs._slot(person.tenant_id, "index_rebuild", person.tenant_id)
    with closing(db.connect()) as conn, conn:
        existing = conn.execute(
            f"SELECT job_id,status FROM {queue} WHERE active_key=? AND tenant_id=? AND kind='index_rebuild'",
            (slot, person.tenant_id),
        ).fetchone()
        if existing:
            return {"job_id": existing["job_id"], "status": existing["status"]}
        ident, stamp = "IDX-JOB-" + uuid4().hex, db.now()
        conn.execute(
            "INSERT INTO jobs(id,kind,entity_id,status,progress,message,result,created_at,updated_at) "
            "VALUES(?,'index_rebuild',?,'queued',0,?,?,?,?)",
            (
                ident,
                person.tenant_id,
                "等待重建本机构的检索索引。",
                json.dumps({"requested_by": person.user_id}),
                stamp,
                stamp,
            ),
        )
        try:
            conn.execute(
                f"INSERT INTO {queue}(job_id,tenant_id,kind,entity_id,principal_json,args_json,status,"
                "available_at,active_key,created_at,updated_at) VALUES(?,?,'index_rebuild',?,?,'[]','queued',"
                "UTC_TIMESTAMP(6),?,UTC_TIMESTAMP(6),UTC_TIMESTAMP(6))",
                (ident, person.tenant_id, person.tenant_id, json.dumps(asdict(person), ensure_ascii=False), slot),
            )
        except pymysql.IntegrityError as exc:
            if exc.args[0] == 1062:
                raise ApiError("RUN_BUSY", "本机构正在重建检索索引。") from exc
            raise
    return {"job_id": ident, "status": "queued"}


def run_rebuild(job_id: str, tenant_id: str, arguments: object) -> None:
    person = jobs.require_leased_principal(job_id)
    _require_admin(person)
    if tenant_id != person.tenant_id or arguments != []:
        raise ApiError("PERMISSION_DENIED")
    with closing(db.connect()) as conn, conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
        conn.lock_search_rebuild()
        row = conn.execute(
            "SELECT status,result FROM jobs WHERE id=? AND entity_id=? AND kind='index_rebuild' FOR UPDATE",
            (job_id, tenant_id),
        ).fetchone()
        if not row or json.loads(row["result"] or "{}").get("requested_by") != person.user_id:
            raise ApiError("PERMISSION_DENIED")
        if row["status"] == "completed":
            return
        if row["status"] != "queued":
            raise ApiError("PRECONDITION_FAILED")
        counts = rebuild_index(conn)
        jobs.assert_job_lease_current()
        conn.execute(
            "UPDATE jobs SET status='completed',progress=100,message=?,result=?,updated_at=? WHERE id=?",
            ("检索索引已重建。", json.dumps({**counts, "requested_by": person.user_id}), db.now(), job_id),
        )
