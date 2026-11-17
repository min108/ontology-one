"""Clock, transaction, lease and tenant-boundary coverage for periodic repairs."""

import asyncio
import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import patch

from fastapi import HTTPException

from backend import db, jobs
from backend.infra import migrations, scheduler
from backend.kernel.metadata import get_meta
from backend.security import store
from backend.security.context import use_context
from tests.migrations import test_integration as foundation


class SchedulerClock(unittest.TestCase):
    def test_next_due_uses_beijing_day_boundary(self) -> None:
        for now, expected in (
            ("2026-11-16T18:09:00+00:00", "2026-11-16T18:10:00+00:00"),
            ("2026-11-16T18:10:00+00:00", "2026-11-17T18:10:00+00:00"),
            ("2026-11-16T20:00:00+00:00", "2026-11-17T18:10:00+00:00"),
        ):
            with self.subTest(now=now):
                self.assertEqual(scheduler.next_due(datetime.fromisoformat(now)).isoformat(), expected)

    def test_system_identity_has_no_session_or_superadmin_privileges(self) -> None:
        p = scheduler.system_principal({"id": "dept-01", "db_name": "isolated"})
        self.assertEqual(p.identity_kind, "system")
        self.assertFalse(p.is_superadmin)
        self.assertEqual(p.role, "system")
        self.assertEqual(p.session_id, "")
        with self.assertRaises(HTTPException):
            scheduler.assert_system_job_current(p, None)
        with use_context(p), self.assertRaises(HTTPException):
            jobs.enqueue("fake", "meta_backfill", p.tenant_id, [])

    def test_tick_rejects_naive_clock(self) -> None:
        with self.assertRaises(ValueError):
            scheduler.tick(datetime(2026, 11, 17))


@unittest.skipUnless(os.getenv("RUN_JOBS_MYSQL_TESTS") == "1", "opt-in MySQL integration test")
class SchedulerMySQL(unittest.TestCase):
    tearDown = foundation.MigrationMySQL.tearDown

    def setUp(self) -> None:
        foundation.MigrationMySQL.setUp(self)
        self.names = foundation.MigrationMySQL.fixture(self)
        migrations.run_migrations()
        self.tenant = store.get_tenant("dept-01")
        self.select = patch.object(scheduler, "list_tenants", return_value=[self.tenant])
        self.select.start()
        self.addCleanup(self.select.stop)
        scheduler.tick()
        with store.control_connection() as conn, conn.cursor() as cur:
            cur.execute("UPDATE job_schedule_state SET next_due_at=%s", ("2020-01-01T00:00:00+00:00",))

    def queued(self) -> list:
        with store.control_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM ontology_task_queue WHERE kind='meta_backfill'")
            return list(cur.fetchall())

    def test_concurrent_tick_enqueues_one_job_for_one_period(self) -> None:
        with ThreadPoolExecutor(max_workers=2) as workers:
            result = list(workers.map(lambda _: scheduler.tick(), range(2)))
        self.assertEqual(sum(result), 1)
        rows = self.queued()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tenant_id"], self.tenant["id"])
        self.assertEqual(json.loads(rows[0]["args_json"]), [])
        self.assertEqual(json.loads(rows[0]["principal_json"])["username"], "平台调度")
        with self.assertRaises(AssertionError):
            self.assertEqual(len(rows + rows), 1)

    def test_missed_periods_enqueue_only_once_and_skip_active_job(self) -> None:
        self.assertEqual(scheduler.tick(), 1)
        self.assertEqual(scheduler.tick(), 0)
        with store.control_connection() as conn, conn.cursor() as cur:
            cur.execute("UPDATE job_schedule_state SET next_due_at=%s", ("2020-01-01T00:00:00+00:00",))
        self.assertEqual(scheduler.tick(), 0)
        self.assertEqual(len(self.queued()), 1)

    def test_worker_repairs_only_captured_tenant_and_records_completion(self) -> None:
        removed = {}
        for scope in ("dept-01", "sub-01"):
            with closing(db._raw_connection(self.names[scope])) as raw:
                conn = db.Connection(raw)
                removed[scope] = conn.execute(
                    "SELECT entity_id FROM entity_meta ORDER BY entity_id LIMIT 1"
                ).fetchone()[0]
                conn.execute("DELETE FROM entity_meta WHERE entity_id=?", (removed[scope],))
                conn.commit()
        self.assertEqual(scheduler.tick(), 1)
        self.assertTrue(asyncio.run(jobs.run_one()))
        self.assertEqual(self.queued()[0]["status"], "completed")
        for scope in ("dept-01", "sub-01"):
            with closing(db._raw_connection(self.names[scope])) as raw:
                result = get_meta(db.Connection(raw), removed[scope])
                self.assertEqual(result is not None, scope == "dept-01")
        with store.control_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT last_status FROM job_schedule_state WHERE tenant_id=%s", ("dept-01",))
            self.assertEqual(cur.fetchone()["last_status"], "done")

    def test_expired_or_forged_system_identity_cannot_access_tenant(self) -> None:
        scheduler.tick()
        row = jobs._claim()
        principal = scheduler.system_principal(self.tenant)
        lease = (row["job_id"], row["lease_token"])
        self.assertEqual(scheduler.assert_system_job_current(principal, lease), principal)
        for altered in (
            replace(principal, database_name=self.names["sub-01"]),
            replace(principal, is_superadmin=True),
            replace(principal, readonly=True),
        ):
            with self.subTest(altered=altered.database_name), self.assertRaises(HTTPException):
                scheduler.assert_system_job_current(altered, lease)
        with store.control_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE ontology_task_queue SET lease_until=DATE_SUB(UTC_TIMESTAMP(6),INTERVAL 1 SECOND) "
                "WHERE job_id=%s",
                (row["job_id"],),
            )
        with self.assertRaises(HTTPException):
            scheduler.assert_system_job_current(principal, lease)

    def test_disabled_tenant_cancels_captured_periodic_job(self) -> None:
        scheduler.tick()
        with store.control_connection() as conn, conn.cursor() as cur:
            cur.execute("UPDATE sec_tenants SET status='disabled' WHERE id='dept-01'")
        self.assertTrue(asyncio.run(jobs.run_one()))
        self.assertEqual(self.queued()[0]["status"], "cancelled")
        with store.control_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT last_status FROM job_schedule_state WHERE tenant_id='dept-01'")
            self.assertEqual(cur.fetchone()["last_status"], "failed")

    def test_queue_insert_failure_rolls_back_period_claim(self) -> None:
        # A real trigger forces the second statement to fail after the CAS.
        with store.control_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "CREATE TRIGGER fail_period_enqueue BEFORE INSERT ON ontology_task_queue FOR EACH ROW "
                "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='synthetic queue failure'"
            )
        with self.assertRaises(db.pymysql.MySQLError):
            scheduler.tick()
        with store.control_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT next_due_at,last_job_id FROM job_schedule_state WHERE tenant_id='dept-01'")
            state = cur.fetchone()
            self.assertEqual(state["next_due_at"], "2020-01-01T00:00:00+00:00")
            self.assertIsNone(state["last_job_id"])
            cur.execute("DROP TRIGGER fail_period_enqueue")
        self.assertEqual(scheduler.tick(), 1)
        self.assertEqual(len(self.queued()), 1)

    def test_unprovisioned_tenant_is_not_created_by_timer(self) -> None:
        tenant = store.get_tenant("dept-02")
        with patch.object(scheduler, "list_tenants", return_value=[tenant]):
            self.assertEqual(scheduler.tick(datetime.now(UTC)), 0)
        self.assertEqual(migrations.table_names(tenant["db_name"]), [])
