"""Preserve the original connection injection point for legacy integrations."""

from backend.db import Connection


def connect() -> Connection:
    from backend import main

    return main.connect()
