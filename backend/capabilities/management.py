"""Common declaration for page-only administrative mutations."""

from collections.abc import Callable

from backend.api.v2.users import MutationOutput
from backend.capabilities.registry import capability
from backend.core.errors import StrictModel


def management(ident: str, title: str, permission: str, model: type[StrictModel]) -> Callable:
    return capability(
        id=ident,
        version="1.0",
        title=title,
        summary=title,
        domain=ident.split(".")[0],
        intent="admin",
        effect="state_change",
        confirm="L3",
        permissions=[permission],
        assistants=[],
        mcp=False,
        input=model,
        output=MutationOutput,
        preconditions=[],
        idempotent=False,
        audit="metadata",
        limits={"timeout_s": 120, "max_result_bytes": 262144},
    )
