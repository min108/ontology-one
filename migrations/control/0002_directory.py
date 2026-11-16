"""Stable directory hierarchy; old identifiers and database names never change."""

from backend import db

TEST = "tests/migrations/test_identity.py:test_directory_migration_preserves_identity"


def up(conn: db.Connection) -> None:
    additions = {
        "parent_id": "VARCHAR(64) NULL",
        "short_name": "VARCHAR(120) NOT NULL DEFAULT ''",
        "org_code": "VARCHAR(120) NULL",
        "sort_order": "INT NOT NULL DEFAULT 0",
        "level": "INT NOT NULL DEFAULT 0",
        "is_demo": "TINYINT NOT NULL DEFAULT 0",
        "mirror_of": "VARCHAR(64) NULL",
        "disabled_at": "VARCHAR(40) NULL",
        "disabled_reason": "VARCHAR(500) NOT NULL DEFAULT ''",
    }
    columns = {row[0] for row in conn.execute("SHOW COLUMNS FROM sec_tenants")}
    for name, ddl in additions.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE sec_tenants ADD COLUMN {name} {ddl}")
    conn.execute("UPDATE sec_tenants SET short_name=LEFT(name,120) WHERE short_name=''")
    conn.execute("UPDATE sec_tenants SET parent_id='hq',level=1 WHERE id<>'hq' AND parent_id IS NULL")
