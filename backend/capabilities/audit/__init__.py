"""Headquarters audit query, reusing the v1 store with stricter visibility."""

from backend.api.v2.audit import AuditInput, AuditOutput, query_audit
from backend.capabilities.context import CapabilityContext
from backend.capabilities.registry import capability


@capability(
    id="audit.query",
    version="1.0",
    title="查询身份审计",
    summary="按时间、机构与操作人查询身份审计。",
    domain="audit",
    intent="read",
    effect="read",
    confirm="L0",
    permissions=["audit.read"],
    assistants=[],
    mcp=False,
    input=AuditInput,
    output=AuditOutput,
    preconditions=[],
    idempotent=True,
    audit="metadata",
    limits={"timeout_s": 30, "max_result_bytes": 262144},
)
def audit_query(ctx: CapabilityContext, body: AuditInput) -> AuditOutput:
    return AuditOutput.model_validate(query_audit(ctx.principal, body))
