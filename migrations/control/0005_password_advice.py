"""H.2-1: keep the historical column, remove its blocking value."""

from backend import db

TEST = "tests/migrations/test_identity.py:test_old_password_flag_is_cleared"


def up(conn: db.Connection) -> None:
    conn.execute("UPDATE sec_users SET must_change_password=0 WHERE must_change_password<>0")
