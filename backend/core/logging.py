"""Allowlisted metadata only: never serialize message arguments or exception text."""

import json
import logging
from datetime import UTC, datetime


class MetadataFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        fields = {key: getattr(record, key, "") for key in ("request_id", "actor_id", "tenant_id", "capability_id")}
        fields.update(
            timestamp=datetime.fromtimestamp(record.created, UTC).isoformat(), level=record.levelname, event=record.name
        )
        return json.dumps(fields, ensure_ascii=False)
