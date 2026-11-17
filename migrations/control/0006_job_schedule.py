"""Persist period claims beside the durable job queue."""

from backend import db

TEST = "tests/migrations/test_r4.py:test_control_schedule_restores_exactly"


def up(conn: db.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS job_schedule_state (
        job_kind VARCHAR(64) NOT NULL, tenant_id VARCHAR(64) NOT NULL,
        next_due_at VARCHAR(40) NOT NULL, last_enqueued_at VARCHAR(40) NULL,
        last_job_id VARCHAR(64) NULL, last_status VARCHAR(16) NULL,
        PRIMARY KEY (job_kind,tenant_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")
