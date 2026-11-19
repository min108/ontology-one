"""Only explicit, identity-bound approvals may dispatch and freeze a release."""

import asyncio
import json
import os
import unittest
from contextlib import closing
from dataclasses import asdict

import pymysql
from fastapi import HTTPException

from backend import db, jobs
from backend.api.v2.routes import registry
from backend.capabilities.context import CapabilityContext
from backend.core.errors import ApiError
from backend.infra import migrations, scheduler
from backend.infra.release_snapshot import run_snapshot
from backend.security import store
from backend.security.context import Principal, use_context
from backend.security.permissions import permissions_for
from tests.migrations import test_integration as foundation
from tests.security.test_permissions import principal


class SnapshotAdmission(unittest.TestCase):
    def test_only_an_explicit_approval_can_create_a_snapshot_job(self) -> None:
        self.assertNotIn("release_snapshot", scheduler.SCHEDULED_KINDS)
        with use_context(principal("reviewer")), self.assertRaises(ValueError):
            jobs.enqueue("job", "release_snapshot", "RLS-A", [])
        with use_context(principal("reviewer")), self.assertRaises(ApiError) as caught:
            run_snapshot("job", "RLS-A", ["1.0.0", "0" * 64])
        self.assertEqual(caught.exception.code, "PERMISSION_DENIED")


@unittest.skipUnless(os.getenv("RUN_JOBS_MYSQL_TESTS") == "1", "opt-in MySQL integration test")
class ReleaseSnapshotMySQL(unittest.TestCase):
    tearDown = foundation.MigrationMySQL.tearDown

    def setUp(self) -> None:
        foundation.MigrationMySQL.setUp(self)
        self.names = foundation.MigrationMySQL.fixture(self)
        migrations.run_migrations()
        self.author = self.login("fixture.admin")
        self.reviewer = self.login("fixture.reviewer")
        written = self.call(self.author, "release.create_draft", {"module_id": "MOD-DEFAULT"})
        self.release_id = written["id"]
        self.candidate = self.call(self.author, "release.submit", {"release_id": self.release_id})
        self.assertEqual(self.candidate["status"], "candidate")

    def login(self, name: str) -> Principal:
        raw = store.login(name, "SyntheticFixture9!", "127.0.0.1", "snapshot-test")
        return store.authenticate_session(raw, "snapshot-test")[0]

    def call(self, person: Principal, action: str, body: dict) -> dict:
        return registry.invoke(
            action, CapabilityContext(person, permissions=frozenset(permissions_for(person))), body
        ).model_dump()

    def approve(self) -> dict:
        return self.call(self.reviewer, "release.publish", {"release_id": self.release_id, "background": True})

    def stored(self) -> tuple:
        with use_context(self.reviewer), closing(db.connect()) as conn:
            row = conn.execute(
                "SELECT status,reviewed_by,manifest_hash FROM ont_releases WHERE id=?", (self.release_id,)
            ).fetchone()
            items = conn.execute(
                "SELECT COUNT(*) FROM ont_release_items WHERE release_id=?", (self.release_id,)
            ).fetchone()[0]
            head = conn.execute("SELECT current_release_id FROM ont_modules WHERE id='MOD-DEFAULT'").fetchone()[0]
        return row, items, head

    def test_worker_rejects_substituted_version_and_reauthorizes_only_the_original_candidate(self) -> None:
        self.assertFalse(asyncio.run(jobs.run_one()))
        with self.assertRaises(ApiError) as caught:
            self.call(self.author, "release.publish", {"release_id": self.release_id, "background": True})
        self.assertEqual(caught.exception.code, "SEPARATION_OF_DUTIES")
        receipt = self.approve()
        job_id = receipt["job_id"]
        self.assertEqual(self.approve()["job_id"], job_id)
        alias = self.call(self.reviewer, "release.publish", {"release_id": self.release_id.lower(), "background": True})
        self.assertEqual(alias["job_id"], job_id)
        with store.control_connection() as conn, conn.cursor() as cursor:
            cursor.execute("SELECT principal_json,args_json FROM ontology_task_queue WHERE kind='release_snapshot'")
            rows = cursor.fetchall()
            self.assertEqual(len(rows), 1)
            captured = json.loads(rows[0]["principal_json"])
            self.assertEqual(
                (captured["user_id"], captured["tenant_id"], captured["session_id"]),
                (self.reviewer.user_id, self.reviewer.tenant_id, self.reviewer.session_id),
            )
            self.assertEqual(json.loads(rows[0]["args_json"]), [receipt["version"], receipt["manifest_hash"]])
            # Change only the control queue. The tenant-side authorization still binds the exact reviewed content.
            cursor.execute(
                "UPDATE ontology_task_queue SET args_json=%s WHERE job_id=%s",
                (json.dumps(["9.9.9", receipt["manifest_hash"]]), job_id),
            )
        self.assertTrue(asyncio.run(jobs.run_one()))
        with use_context(self.reviewer):
            self.assertEqual(jobs.queue_status(job_id)["status"], "cancelled")
        row, items, head = self.stored()
        self.assertEqual((row["status"], row["reviewed_by"], items, head), ("candidate", None, 0, None))
        with use_context(self.author), self.assertRaises(HTTPException) as refused:
            jobs.retry_job(job_id)
        self.assertEqual(refused.exception.status_code, 409)
        retry = self.approve()
        self.assertNotEqual(retry["job_id"], job_id)
        self.assertTrue(asyncio.run(jobs.run_one()))
        with use_context(self.reviewer):
            self.assertEqual(jobs.queue_status(retry["job_id"])["status"], "completed")
        row, items, head = self.stored()
        self.assertEqual(
            (row["status"], row["reviewed_by"], row["manifest_hash"], items, head),
            (
                "published",
                self.reviewer.user_id,
                receipt["manifest_hash"],
                len(self.candidate["items"]),
                self.release_id,
            ),
        )

    def test_another_live_reviewer_cannot_replace_the_approver_in_the_queue(self) -> None:
        store.create_user(
            self.author,
            username="snapshot.reviewer",
            display_name="独立审核",
            tenant_id=self.author.tenant_id,
            role="reviewer",
            password="SyntheticFixture9!",
        )
        substitute = self.login("snapshot.reviewer")
        self.assertIn("review.decide", permissions_for(substitute))
        receipt = self.approve()
        with store.control_connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                "UPDATE ontology_task_queue SET principal_json=%s WHERE job_id=%s",
                (json.dumps(asdict(substitute)), receipt["job_id"]),
            )
        self.assertTrue(asyncio.run(jobs.run_one()))
        with use_context(self.reviewer):
            self.assertEqual(jobs.queue_status(receipt["job_id"])["status"], "cancelled")
        row, items, head = self.stored()
        self.assertEqual((row["status"], row["reviewed_by"], items, head), ("candidate", None, 0, None))

    def test_dispatch_failure_rolls_back_tenant_job_without_freezing(self) -> None:
        with store.control_connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                "CREATE TRIGGER fail_snapshot_dispatch BEFORE INSERT ON ontology_task_queue FOR EACH ROW "
                "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='test dispatch unavailable'"
            )
        with self.assertRaises(pymysql.MySQLError):
            self.approve()
        with use_context(self.reviewer), closing(db.connect()) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE kind='release_snapshot' AND entity_id=?", (self.release_id,)
            ).fetchone()[0]
            self.assertEqual(count, 0)
        row, items, head = self.stored()
        self.assertEqual((row["status"], items, head), ("candidate", 0, None))
