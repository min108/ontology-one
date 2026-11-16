"""Register the existing 0.7.0 control schema without duplicating its DDL."""

from __future__ import annotations

from backend import db

TEST = "tests/migrations/test_integration.py:test_baseline_preserves_every_old_row_and_column"


def up(conn: db.Connection) -> None:
    from backend.federation import initialize_federation
    from backend.jobs import ensure_queue
    from backend.security.store import initialize_control

    initialize_control()
    initialize_federation()
    ensure_queue()
