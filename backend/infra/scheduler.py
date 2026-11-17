"""Minute tick: atomically claim due periods and enqueue ordinary leased jobs."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from dataclasses import asdict
from datetime import UTC, datetime, timedelta, timezone

from fastapi import HTTPException

from backend.security.context import Principal
from backend.security.store import control_connection, get_tenant, list_tenants

BEIJING = timezone(timedelta(hours=8))
SYSTEM_USER = "system:scheduler"
SCHEDULED_KINDS = frozenset({"meta_backfill"})


def timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds")


def next_due(now: datetime) -> datetime:
    """Next 02:10 Beijing, strictly later than now (no catch-up backlog)."""
    local = now.astimezone(BEIJING)
    due = local.replace(hour=2, minute=10, second=0, microsecond=0)
    if due <= local:
        due += timedelta(days=1)
    return due.astimezone(UTC)


def system_principal(tenant: dict) -> Principal:
    return Principal(
        user_id=SYSTEM_USER,
        username="平台调度",
        home_tenant_id=tenant["id"],
        tenant_id=tenant["id"],
        role="system",
        is_superadmin=False,
        session_id="",
        auth_version=0,
        database_name=tenant["db_name"],
        identity_kind="system",
    )


def assert_system_job_current(principal: Principal, lease: tuple[str, str] | None) -> Principal:
    """System privilege exists only for our exact allowlisted leased queue frame."""
    if lease is None or principal.identity_kind != "system":
        raise HTTPException(403, "周期任务身份或租约无效。")
    tenant = get_tenant(principal.tenant_id)
    if not tenant or tenant["status"] != "active" or principal != system_principal(tenant):
        raise HTTPException(403, "周期任务机构已经停用或身份发生变化。")
    with control_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT kind,tenant_id,entity_id,principal_json,args_json FROM ontology_task_queue "
            "WHERE job_id=%s AND lease_token=%s AND status='running' AND lease_until>UTC_TIMESTAMP(6)",
            lease,
        )
        row = cur.fetchone()
    if (
        not row
        or row["kind"] not in SCHEDULED_KINDS
        or row["tenant_id"] != principal.tenant_id
        or row["entity_id"] != principal.tenant_id
        or json.loads(row["args_json"]) != []
        or Principal(**json.loads(row["principal_json"])) != principal
    ):
        raise HTTPException(403, "周期任务上下文不匹配。")
    return principal


def tick(now: datetime | None = None) -> int:
    """One transaction owns each compare-and-swap and corresponding queue insert.

    A crash before commit rolls both back. A running previous job defers its
    period; recovery still submits only once when the old job finishes.
    """
    from uuid import uuid4

    from backend.jobs import _slot

    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("Scheduler time must include a timezone")
    current, following = timestamp(now), timestamp(next_due(now))
    count = 0
    for tenant in list_tenants():
        if tenant["status"] != "active":
            continue
        # Lazy, unprovisioned tenant databases must not be initialized by a timer.
        from backend.infra.migrations import table_names

        if "entity_meta" not in table_names(tenant["db_name"]):
            continue
        kind = "meta_backfill"
        with control_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE job_schedule_state s JOIN ontology_task_queue q ON q.job_id=s.last_job_id "
                "SET s.last_status='failed' WHERE s.tenant_id=%s AND q.kind='meta_backfill' "
                "AND q.status='failed' AND s.last_status='running'",
                (tenant["id"],),
            )
            cur.execute(
                "INSERT IGNORE INTO job_schedule_state(job_kind,tenant_id,next_due_at) VALUES(%s,%s,%s)",
                (kind, tenant["id"], following),
            )
            cur.execute(
                "SELECT next_due_at FROM job_schedule_state WHERE job_kind=%s AND tenant_id=%s",
                (kind, tenant["id"]),
            )
            previous = cur.fetchone()["next_due_at"]
            if previous > current:
                continue
            slot = _slot(tenant["id"], kind, tenant["id"])
            cur.execute("SELECT job_id FROM ontology_task_queue WHERE active_key=%s", (slot,))
            if cur.fetchone():
                continue
            job_id = "SCHED-" + uuid4().hex
            cur.execute(
                "UPDATE job_schedule_state SET next_due_at=%s,last_enqueued_at=%s,last_job_id=%s,last_status='running' "
                "WHERE job_kind=%s AND tenant_id=%s AND next_due_at=%s",
                (following, current, job_id, kind, tenant["id"], previous),
            )
            if cur.rowcount != 1:
                continue
            cur.execute(
                "INSERT INTO ontology_task_queue(job_id,tenant_id,kind,entity_id,principal_json,args_json,status,"
                "available_at,active_key,created_at,updated_at) "
                "VALUES(%s,%s,%s,%s,%s,'[]','queued',UTC_TIMESTAMP(6),%s,UTC_TIMESTAMP(6),UTC_TIMESTAMP(6))",
                (
                    job_id,
                    tenant["id"],
                    kind,
                    tenant["id"],
                    json.dumps(asdict(system_principal(tenant)), ensure_ascii=False),
                    slot,
                ),
            )
            count += 1
    return count


async def scheduler_loop(stop: asyncio.Event | None = None) -> None:
    import logging

    while stop is None or not stop.is_set():
        try:
            await asyncio.to_thread(tick)
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.getLogger(__name__).exception("周期任务调度失败，将在下一分钟重试。")
        if stop is None:
            await asyncio.sleep(60)
        else:
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=60)
