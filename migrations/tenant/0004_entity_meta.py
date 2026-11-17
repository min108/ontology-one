"""Semantic side table only; preserve every historical business row."""

from backend import db
from backend.kernel.metadata import backfill_meta

TEST = "tests/migrations/test_r4.py:test_legacy_rows_and_columns_unchanged"


def up(conn: db.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS entity_meta (
        entity_id VARCHAR(191) PRIMARY KEY,
        pillar VARCHAR(16) NOT NULL, subtype VARCHAR(32) NULL,
        module_id VARCHAR(64) NULL, element_key VARCHAR(191) NULL,
        owner_user_id VARCHAR(64) NULL, provider_note VARCHAR(500) NULL,
        valid_from VARCHAR(40) NULL, valid_to VARCHAR(40) NULL,
        sensitivity VARCHAR(16) NOT NULL DEFAULT 'internal',
        gates JSON NULL, card_text MEDIUMTEXT NULL, updated_at VARCHAR(40) NOT NULL,
        UNIQUE KEY uq_meta_module_key (module_id, element_key),
        KEY ix_meta_module (module_id), KEY ix_meta_pillar (pillar)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")
    backfill_meta(conn)
