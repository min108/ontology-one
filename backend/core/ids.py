"""Identifiers for newly introduced objects; existing identifiers are untouched."""

import secrets
from collections.abc import Callable

PREFIXES = frozenset(
    [
        "ORG-",
        "POS-",
        "PTY-",
        "TM-",
        "OT-",
        "LT-",
        "MT-",
        "MOD-",
        "RLS-",
        "BND-",
        "CS-",
        "CSI-",
        "RV-",
        "RCP-",
        "FB-",
        "EV-",
        "NT-",
        "RC-",
        "CQ-",
        "PRC-",
        "SCN-",
        "STM-",
        "ASK-",
        "CONV-",
        "RUN-",
        "STP-",
        "ACT-",
        "ATT-",
        "ART-",
        "FM-",
        "FO-",
        "FA-",
        "FC-",
        "FBR-",
        "GRL-",
        "org-",
        "rcp_",
        "demo-p-",
    ]
)


def new_id(prefix: str, exists: Callable[[str], bool] | None = None) -> str:
    if prefix not in PREFIXES:
        raise ValueError("Unknown identifier prefix")
    for _ in range(32):
        suffix = secrets.token_hex(4)
        value = prefix + (suffix if prefix in {"org-", "rcp_", "demo-p-"} else suffix.upper())
        if exists is None or not exists(value):
            return value
    raise RuntimeError("Identifier collision retry limit reached")
