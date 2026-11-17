"""Allowlisted periodic operations; invocation stays inside the leased worker."""

import asyncio
from contextlib import closing
from types import MappingProxyType


def _meta_backfill() -> None:
    from backend import db
    from backend.jobs import assert_job_lease_current
    from backend.kernel.metadata import backfill_meta

    with closing(db.connect()) as conn:
        backfill_meta(conn)
        assert_job_lease_current()
        conn.commit()


HANDLERS = MappingProxyType({"meta_backfill": _meta_backfill})


async def invoke(kind: str) -> None:
    try:
        handler = HANDLERS[kind]
    except KeyError:
        raise ValueError("Unsupported periodic operation") from None
    await asyncio.to_thread(handler)
