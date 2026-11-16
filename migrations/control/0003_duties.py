"""Reserve duties without adding a grant operation or new account roles."""

from backend import db

TEST = "tests/migrations/test_identity.py:test_duties_are_empty_and_projected"


def up(conn: db.Connection) -> None:
    if "duties" not in {row[0] for row in conn.execute("SHOW COLUMNS FROM sec_users")}:
        conn.execute("ALTER TABLE sec_users ADD COLUMN duties JSON NOT NULL DEFAULT (JSON_ARRAY())")
