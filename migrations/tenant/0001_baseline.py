"""Retain the original ordered initializer and standardize demo receipts."""

from __future__ import annotations

from backend import db

TEST = "tests/migrations/test_integration.py:test_baseline_preserves_every_old_row_and_column"


def up(conn: db.Connection) -> None:
    from backend import demo

    tenant = conn.tenant_id
    if not tenant:
        raise ValueError("Tenant migration requires a directory identity")
    # The old initializer's cache cannot substitute for a durable migration.
    from backend.security.store import get_tenant

    db._READY.discard(get_tenant(tenant)["db_name"])
    db.ensure_tenant_schema(tenant)
    demo._ensure(conn)
