"""Add the institution's common part, dependencies and immutable release storage."""

from backend import db
from backend.infra.migrations import MigrationError, database_name
from backend.infra.tenant_defaults import initial_common_part
from backend.security import store

TEST = "tests/migrations/test_r4b1.py:test_upgrade_preserves_all_four_legacy_tables_and_provisions_each_institution"


def up(conn: db.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS ont_modules (
        id VARCHAR(64) PRIMARY KEY, module_key VARCHAR(100) NOT NULL UNIQUE,
        module_type VARCHAR(16) NOT NULL DEFAULT 'org_common', name VARCHAR(255) NOT NULL,
        domain_code VARCHAR(64) NOT NULL, owner_user_id VARCHAR(64) NULL, scope_note TEXT NULL,
        status VARCHAR(20) NOT NULL DEFAULT 'active', sharing_level TINYINT NOT NULL DEFAULT 0,
        preferences JSON NULL, current_release_id VARCHAR(64) NULL, created_by VARCHAR(64) NOT NULL,
        created_at VARCHAR(40) NOT NULL, updated_at VARCHAR(40) NOT NULL,
        CONSTRAINT chk_ont_common CHECK (
            (module_type = 'org_common' AND id = 'MOD-DEFAULT' AND module_key = 'default' AND domain_code = 'general')
            OR (module_type <> 'org_common' AND id <> 'MOD-DEFAULT' AND module_key <> 'default')
        )
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")
    conn.execute("""CREATE TABLE IF NOT EXISTS ont_module_deps (
        module_id VARCHAR(64) NOT NULL, depends_on_tenant VARCHAR(64) NOT NULL, depends_on_module VARCHAR(64) NOT NULL,
        min_version VARCHAR(20) NULL, PRIMARY KEY (module_id, depends_on_tenant, depends_on_module)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")
    conn.execute("""CREATE TABLE IF NOT EXISTS ont_releases (
        id VARCHAR(64) PRIMARY KEY, module_id VARCHAR(64) NOT NULL, version VARCHAR(20) NOT NULL,
        status VARCHAR(20) NOT NULL, bump VARCHAR(8) NOT NULL, changelog TEXT NOT NULL,
        manifest_hash CHAR(64) NOT NULL, stats JSON NULL, created_by VARCHAR(64) NOT NULL,
        reviewed_by VARCHAR(64) NULL, created_at VARCHAR(40) NOT NULL, published_at VARCHAR(40) NULL,
        UNIQUE KEY uq_release (module_id, version)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")
    conn.execute("""CREATE TABLE IF NOT EXISTS ont_release_items (
        release_id VARCHAR(64) NOT NULL, entity_id VARCHAR(191) NOT NULL, entity_version INT NOT NULL,
        kind VARCHAR(64) NOT NULL, snapshot JSON NOT NULL, content_hash CHAR(64) NOT NULL,
        item_role VARCHAR(8) NOT NULL DEFAULT 'member', PRIMARY KEY (release_id, entity_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")
    # Database identity is directory-owned and unique, including pending new institutions.
    # A real connection returns one row even if that ownership is missing, which must abort migration.
    control = database_name(store._config()["database"])
    tenants = conn.execute(
        f"SELECT t.name,t.created_at FROM (SELECT DATABASE() AS db_name) target "
        f"LEFT JOIN `{control}`.sec_tenants t ON t.db_name=target.db_name"
    ).fetchall()
    for tenant in tenants:
        if tenant["name"] is None:
            raise MigrationError("工作区在机构目录中缺少归属，无法建立公共部分。")
        common = initial_common_part(tenant)
        conn.execute(
            f"INSERT INTO ont_modules ({','.join(common)}) SELECT {','.join('?' for _ in common)} "
            "WHERE NOT EXISTS (SELECT 1 FROM ont_modules WHERE id='MOD-DEFAULT')",
            tuple(common.values()),
        )
