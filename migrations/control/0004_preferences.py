"""Authenticated users own their display preferences."""

from backend import db

TEST = "tests/migrations/test_identity.py:test_preferences_persist_only_for_self"


def up(conn: db.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS sec_user_prefs (
        user_id VARCHAR(64) NOT NULL, pref_key VARCHAR(64) NOT NULL,
        value JSON NOT NULL, updated_at VARCHAR(40) NOT NULL,
        PRIMARY KEY(user_id,pref_key)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")
