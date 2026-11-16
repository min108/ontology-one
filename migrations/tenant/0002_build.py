"""Add proposals and resumable recipe records without changing legacy entities."""

from backend import db

TEST = "tests/migrations/test_r2.py:test_r2_preserves_rows_and_restores_all_new_tables"


def up(conn: db.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS changesets (
        id VARCHAR(64) PRIMARY KEY, title VARCHAR(255) NOT NULL, summary TEXT NULL,
        origin VARCHAR(32) NOT NULL, origin_ref VARCHAR(191) NULL,
        module_id VARCHAR(64) NULL, scenario_id VARCHAR(64) NULL, source_refs JSON NULL,
        status VARCHAR(20) NOT NULL DEFAULT 'open', stats JSON NULL,
        assigned_to VARCHAR(64) NULL, split_from VARCHAR(64) NULL,
        created_by VARCHAR(64) NOT NULL, created_at VARCHAR(40) NOT NULL, updated_at VARCHAR(40) NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")
    conn.execute("""CREATE TABLE IF NOT EXISTS changeset_items (
        id VARCHAR(64) PRIMARY KEY, changeset_id VARCHAR(64) NOT NULL, seq INT NOT NULL,
        op VARCHAR(32) NOT NULL, target_kind VARCHAR(64) NULL, target_id VARCHAR(191) NULL,
        target_module_id VARCHAR(64) NULL, requirement_ids JSON NULL, base_version INT NULL,
        before_data JSON NULL, after_data JSON NOT NULL, one_liner VARCHAR(500) NOT NULL,
        rationale TEXT NULL, evidence JSON NULL, content_origin VARCHAR(16) NOT NULL DEFAULT 'source',
        confidence DECIMAL(4,3) NULL, tier VARCHAR(8) NOT NULL DEFAULT 'check', validation JSON NULL,
        duplicate_of VARCHAR(191) NULL, decision VARCHAR(12) NOT NULL DEFAULT 'pending',
        reject_reason VARCHAR(500) NULL, decided_by VARCHAR(64) NULL, decided_at VARCHAR(40) NULL,
        applied_entity_id VARCHAR(191) NULL, applied_version INT NULL, stale TINYINT NOT NULL DEFAULT 0,
        KEY ix_csi_cs (changeset_id, seq)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")
    conn.execute("""CREATE TABLE IF NOT EXISTS recipe_runs (
        id VARCHAR(64) PRIMARY KEY, recipe CHAR(1) NOT NULL, title VARCHAR(255) NOT NULL,
        status VARCHAR(16) NOT NULL, stage VARCHAR(32) NOT NULL, stage_message VARCHAR(255) NOT NULL,
        progress JSON NULL, autopilot VARCHAR(8) NOT NULL DEFAULT 'standard',
        input JSON NOT NULL, state JSON NULL, changeset_id VARCHAR(64) NULL, batch_id VARCHAR(64) NULL,
        degraded JSON NULL, used_model TINYINT NOT NULL DEFAULT 0, error JSON NULL,
        created_by VARCHAR(64) NOT NULL, created_at VARCHAR(40) NOT NULL,
        started_at VARCHAR(40) NULL, finished_at VARCHAR(40) NULL, heartbeat_at VARCHAR(40) NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")
