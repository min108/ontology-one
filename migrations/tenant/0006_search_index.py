"""Create the tenant's native ngram index and derive its rows from business records."""

from backend import db

TEST = "tests/migrations/test_r4b2.py:test_native_ngram_indexes_search_title_alias_and_body_separately"


def up(conn: db.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS search_index (
        doc_id VARCHAR(200) PRIMARY KEY, doc_type VARCHAR(16) NOT NULL,
        entity_id VARCHAR(191) NULL, kind VARCHAR(64) NULL, pillar VARCHAR(16) NULL, module_id VARCHAR(64) NULL,
        status VARCHAR(64) NULL, title VARCHAR(500) NOT NULL, aliases TEXT NULL, body MEDIUMTEXT NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        FULLTEXT KEY ft_si_title (title, aliases) WITH PARSER ngram,
        FULLTEXT KEY ft_si_body (body) WITH PARSER ngram,
        KEY ix_si_kind (kind), KEY ix_si_module (module_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")
    if not getattr(conn, "schema_only", False):
        from backend.infra.search_index import rebuild_index

        rebuild_index(conn)
