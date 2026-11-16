"""Versioned private reports; business entity and relation columns remain intact."""

from backend import db

TEST = "tests/migrations/test_r2.py:test_r2_preserves_rows_and_restores_all_new_tables"


def up(conn: db.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS artifacts (
        id VARCHAR(64) PRIMARY KEY, title VARCHAR(255) NOT NULL, template VARCHAR(32) NOT NULL,
        owner_user_id VARCHAR(64) NOT NULL, visibility VARCHAR(8) NOT NULL DEFAULT 'private',
        classification VARCHAR(16) NOT NULL DEFAULT 'internal', status VARCHAR(12) NOT NULL DEFAULT 'draft',
        current_version INT NOT NULL DEFAULT 0, final_version INT NULL,
        conversation_id VARCHAR(64) NULL, source_kind VARCHAR(12) NOT NULL DEFAULT 'analyst',
        tags JSON NULL, retention_until VARCHAR(40) NULL,
        created_at VARCHAR(40) NOT NULL, updated_at VARCHAR(40) NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")
    conn.execute("""CREATE TABLE IF NOT EXISTS artifact_versions (
        artifact_id VARCHAR(64) NOT NULL, version INT NOT NULL,
        spec JSON NOT NULL, files JSON NOT NULL, data_manifest JSON NOT NULL, citations JSON NOT NULL,
        run_id VARCHAR(64) NULL, change_note VARCHAR(500) NULL, generated_by VARCHAR(16) NOT NULL,
        created_by VARCHAR(64) NOT NULL, created_at VARCHAR(40) NOT NULL,
        PRIMARY KEY (artifact_id, version)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")
    conn.execute("""CREATE TABLE IF NOT EXISTS artifact_access_log (
        id BIGINT PRIMARY KEY AUTO_INCREMENT, artifact_id VARCHAR(64) NOT NULL, version INT NOT NULL,
        user_id VARCHAR(64) NOT NULL, action VARCHAR(12) NOT NULL, format VARCHAR(8) NULL, at VARCHAR(40) NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")
