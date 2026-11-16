"""The single institution model configuration, shared with the original API."""

from pydantic import Field

from backend import skill_harness
from backend.capabilities.context import CapabilityContext
from backend.capabilities.registry import capability
from backend.core.errors import StrictModel


class ModelConfiguration(skill_harness.HarnessConfiguration):
    """Keep original validation; require explicit fields on the new binding."""

    base_url: str = Field(min_length=1, max_length=1000)
    model: str = Field(min_length=1, max_length=200)


class ModelStatus(StrictModel):
    available: bool = False
    configured: bool = False
    verified: bool = False
    version: str = ""
    reason: str = ""
    migration_required: bool = False
    configuration_notice: str = ""
    base_url: str = ""
    model: str = ""

    @classmethod
    def public(cls, value: dict) -> "ModelStatus":
        # Explicitly select public fields even if an upstream service later expands.
        return cls.model_validate({key: value[key] for key in cls.model_fields if key in value})


@capability(
    id="model.configure",
    version="1.0",
    title="保存模型配置",
    summary="保存当前机构的统一模型服务，密钥不回显。",
    domain="model",
    intent="admin",
    effect="state_change",
    confirm="L3",
    permissions=["admin.model"],
    assistants=[],
    mcp=False,
    input=ModelConfiguration,
    output=ModelStatus,
    preconditions=[],
    idempotent=False,
    audit="metadata",
    limits={"timeout_s": 30, "max_result_bytes": 16384},
)
def configure(ctx: CapabilityContext, body: ModelConfiguration) -> ModelStatus:
    return ModelStatus.public(skill_harness._save_configuration(body))
