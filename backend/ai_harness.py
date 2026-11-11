"""Compatibility adapter: legacy AI features execute through official Harness.

No HTTP model client or alternative provider route exists here. The controlled
SDK task has no business tools and returns only transient text to the caller;
existing feature validation and explicit human adoption remain in those callers.
"""
from __future__ import annotations


async def complete_task(principal, messages, *, json_mode=False):
    from .agent_tasks import complete_task as governed_task
    return await governed_task(principal, messages, json_mode=json_mode)
