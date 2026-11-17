"""Validated capability declarations and a single gateway for all bindings."""

import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from pydantic import BaseModel, JsonValue, ValidationError

from backend.capabilities.context import CapabilityContext
from backend.capabilities.preconditions import CHECKS, check_all
from backend.core.config import CONFIG
from backend.core.errors import ApiError
from backend.db import business_readonly
from backend.security import store
from backend.security.context import use_context
from backend.security.permissions import ALL_PERMISSIONS, ROLES

READ_EFFECTS = frozenset({"none", "read", "external_read"})


@dataclass(frozen=True, slots=True)
class Capability:
    id: str
    version: str
    title: str
    summary: str
    domain: str
    intent: str
    effect: str
    confirm: str
    permissions: tuple[str, ...]
    assistants: tuple[str, ...]
    mcp: bool
    input: type[BaseModel]
    output: type[BaseModel]
    preconditions: tuple[str, ...]
    idempotent: bool
    audit: str
    limits: Mapping[str, int]
    function: Callable[[CapabilityContext, BaseModel], BaseModel]

    @property
    def tool_name(self) -> str:
        return self.id.replace(".", "_")


class Registry:
    def __init__(self) -> None:
        self._entries: dict[str, Capability] = {}

    @property
    def entries(self) -> Mapping[str, Capability]:
        return MappingProxyType(self._entries)

    def capability(
        self,
        *,
        id: str,
        version: str,
        title: str,
        summary: str,
        domain: str,
        intent: str,
        effect: str,
        confirm: str,
        permissions: list[str],
        assistants: list[str],
        mcp: bool,
        input: type[BaseModel],
        output: type[BaseModel],
        preconditions: list[str],
        idempotent: bool,
        audit: str,
        limits: dict[str, int],
    ) -> Callable:
        if id in self._entries or not re.fullmatch(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*", id):
            raise ValueError("Capability identifier must be unique and domain.action")
        if not version or not title or not summary or len(summary) > 200 or domain != id.split(".")[0]:
            raise ValueError("Invalid capability identity or description")
        if intent not in {"read", "analyze", "generate", "build", "govern", "admin"}:
            raise ValueError("Invalid capability intent")
        if effect not in READ_EFFECTS | {"propose", "write_draft", "state_change"}:
            raise ValueError("Invalid capability effect")
        if confirm not in {"L0", "L1", "L2", "L3"} or audit not in {"none", "metadata"}:
            raise ValueError("Invalid confirmation or audit mode")
        if set(permissions) - ALL_PERMISSIONS or set(assistants) - {"builder", "analyst"}:
            raise ValueError("Unknown permission or assistant")
        if set(preconditions) - CHECKS.keys():
            raise ValueError("Unknown precondition")
        if "analyst" in assistants and effect not in READ_EFFECTS and domain not in {"artifact", "feedback"}:
            raise ValueError("Application assistant cannot bind this mutation")
        if mcp and effect not in READ_EFFECTS:
            raise ValueError("External tools must be read-only")
        for model in (input, output):
            if not issubclass(model, BaseModel) or model.model_config.get("extra") != "forbid":
                raise ValueError("Capability models must forbid extra fields")
            model.model_json_schema()
        if set(limits) - {"timeout_s", "max_result_bytes"} or any(value <= 0 for value in limits.values()):
            raise ValueError("Invalid capability limits")
        # Mutation idempotency storage is introduced with the R4 write capabilities.
        if idempotent and effect not in READ_EFFECTS:
            raise ValueError("Mutation idempotency requires a persistent implementation")

        def decorate(function: Callable[[CapabilityContext, BaseModel], BaseModel]) -> Callable:
            if id in self._entries:
                raise ValueError("Duplicate capability identifier")
            self._entries[id] = Capability(
                id,
                version,
                title,
                summary,
                domain,
                intent,
                effect,
                confirm,
                tuple(permissions),
                tuple(assistants),
                mcp,
                input,
                output,
                tuple(preconditions),
                idempotent,
                audit,
                MappingProxyType(dict(limits)),
                function,
            )
            return function

        return decorate

    def invoke(self, ident: str, ctx: CapabilityContext, raw: dict[str, JsonValue]) -> BaseModel:
        item = self._entries.get(ident)
        if item is None:
            raise ApiError("NOT_FOUND")
        started, result_code, count = time.monotonic(), "INTERNAL_ERROR", 0
        try:
            person = ctx.principal
            if person.role not in ROLES or person.identity_kind != "session":
                raise ApiError("PERMISSION_DENIED")
            if person.readonly and item.effect not in READ_EFFECTS:
                raise ApiError("PERMISSION_DENIED")
            if not set(item.permissions) <= ctx.permissions:
                raise ApiError("PERMISSION_DENIED")
            if ctx.binding == "tool":
                if ctx.assistant not in item.assistants:
                    raise ApiError("PERMISSION_DENIED")
                if item.confirm == "L3":
                    raise ApiError("PAGE_ONLY_ACTION")
                if item.confirm in {"L1", "L2"}:
                    raise ApiError("CONFIRMATION_REQUIRED")
            elif ctx.binding == "mcp" and not item.mcp:
                raise ApiError("PERMISSION_DENIED")
            try:
                body = item.input.model_validate(raw)
            except ValidationError:
                raise ApiError("VALIDATION_FAILED") from None
            check_all(item.preconditions, ctx, body)
            with use_context(person), business_readonly(item.effect in READ_EFFECTS):
                result = item.output.model_validate(item.function(ctx, body))
            if len(result.model_dump_json().encode()) > item.limits.get(
                "max_result_bytes", CONFIG.maximum_result_bytes
            ):
                raise ApiError(
                    "IMPORT_LIMIT_EXCEEDED",
                    "这份材料整理后的内容超过单次方案容量（2 MiB）。原文与已保存条目仍保留；"
                    "请在素材页继续处理，或按章节拆成较小文件，分批上传整理。"
                    if ident.startswith("changeset.")
                    else "返回内容超过上限，请缩小查询范围后重试。",
                )
            if time.monotonic() - started > item.limits.get("timeout_s", CONFIG.capability_timeout_seconds):
                raise ApiError("QUERY_TIMEOUT")
            count = len(result.items) if isinstance(getattr(result, "items", None), list) else 1
            result_code = "OK"
            return result
        except ApiError as exc:
            result_code = exc.code
            raise
        finally:
            if item.audit == "metadata":
                store.append_audit(
                    ctx.principal,
                    "capability_invoke",
                    item.id,
                    "success" if result_code == "OK" else "rejected",
                    {
                        "capability_id": item.id,
                        "result_code": result_code,
                        "count": count,
                        "elapsed_ms": round((time.monotonic() - started) * 1000),
                    },
                )


registry = Registry()
capability = registry.capability
