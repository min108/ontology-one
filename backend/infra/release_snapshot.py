"""Persist and execute one independent reviewer's explicit approval."""

import hashlib
import json
from dataclasses import asdict
from uuid import uuid4

import pymysql

from backend import db, jobs
from backend.core.errors import ApiError
from backend.security import store
from backend.security.context import Principal


def _approval(person: Principal, release_id: str, version: str, manifest_hash: str) -> str:
    values = [
        person.tenant_id,
        person.user_id,
        person.session_id,
        person.auth_version,
        release_id,
        version,
        manifest_hash,
    ]
    return hashlib.sha256(json.dumps(values, separators=(",", ":")).encode()).hexdigest()


def enqueue_snapshot(conn: db.Connection, person: Principal, release_id: str) -> dict:
    from backend.kernel import releases

    candidate = releases.authorize_publish(conn, person, release_id)
    version, manifest = candidate["version"], candidate["manifest_hash"]
    approval = _approval(person, release_id, version, manifest)
    # Both records are on the same MySQL instance. Commit the reviewer binding,
    # tenant job and durable dispatch together, using the tenant transaction.
    queue = f"`{store._config()['database']}`.ontology_task_queue"
    slot = jobs._slot(person.tenant_id, "release_snapshot", release_id)
    active = conn.execute(f"SELECT job_id,principal_json,args_json FROM {queue} WHERE active_key=?", (slot,)).fetchone()
    if active:
        captured = Principal(**json.loads(active["principal_json"]))
        if (
            json.loads(active["args_json"]) != [version, manifest]
            or _approval(captured, release_id, version, manifest) != approval
        ):
            raise ApiError("RUN_BUSY", "另一项版本审核正在处理，请稍后查看。")
        return {**candidate, "job_id": active["job_id"]}
    job_id, stamp = "RLS-JOB-" + uuid4().hex, db.now()
    conn.execute(
        "INSERT INTO jobs(id,kind,entity_id,status,progress,message,result,created_at,updated_at) "
        "VALUES(?,'release_snapshot',?,'queued',0,?,?,?,?)",
        (job_id, release_id, "已收到审核，等待保存版本。", json.dumps({"approval": approval}), stamp, stamp),
    )
    try:
        conn.execute(
            f"INSERT INTO {queue}(job_id,tenant_id,kind,entity_id,principal_json,args_json,status,available_at,"
            "active_key,created_at,updated_at) VALUES(?,?,'release_snapshot',?,?,?,'queued',UTC_TIMESTAMP(6),"
            "?,UTC_TIMESTAMP(6),UTC_TIMESTAMP(6))",
            (job_id, person.tenant_id, release_id, json.dumps(asdict(person)), json.dumps([version, manifest]), slot),
        )
    except pymysql.IntegrityError as exc:
        if exc.args[0] == 1062:
            raise ApiError("RUN_BUSY") from None
        raise
    return {**candidate, "job_id": job_id}


def run_snapshot(job_id: str, release_id: str, arguments: object) -> None:
    from contextlib import closing

    from backend.kernel import releases

    person = jobs.require_leased_principal(job_id)
    if person.identity_kind != "session" or person.readonly or person.role not in {"admin", "reviewer"}:
        raise ApiError("PERMISSION_DENIED")
    if not isinstance(arguments, list) or len(arguments) != 2 or any(not isinstance(v, str) for v in arguments):
        raise ApiError("PRECONDITION_FAILED")
    version, manifest = arguments
    expected = _approval(person, release_id, version, manifest)
    with closing(db.connect()) as conn, conn:
        job = conn.execute(
            "SELECT status,result FROM jobs WHERE id=? AND kind='release_snapshot' AND entity_id=? FOR UPDATE",
            (job_id, release_id),
        ).fetchone()
        if not job or json.loads(job["result"]).get("approval") != expected:
            raise ApiError("PERMISSION_DENIED")
        if job["status"] == "completed":
            return
        if job["status"] != "queued":
            raise ApiError("PRECONDITION_FAILED")
        frozen = releases.publish(conn, person, release_id, expected_version=version, expected_manifest_hash=manifest)
        result = {
            "approval": expected,
            "release_id": release_id,
            "version": frozen["version"],
            "manifest_hash": frozen["manifest_hash"],
            "member_count": frozen["member_count"],
            "ref_count": frozen["ref_count"],
        }
        conn.execute(
            "UPDATE jobs SET status='completed',progress=100,message=?,result=?,updated_at=? WHERE id=?",
            ("发布版本已保存。", json.dumps(result), db.now(), job_id),
        )
