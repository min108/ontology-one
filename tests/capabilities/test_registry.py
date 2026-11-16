import unittest
from unittest.mock import patch

from pydantic import BaseModel

from backend.capabilities.context import CapabilityContext
from backend.capabilities.registry import Registry
from backend.core.errors import ApiError, StrictModel
from tests.security.test_permissions import principal


class Empty(StrictModel):
    pass


def echo(ctx: CapabilityContext, body: Empty) -> Empty:
    return body


class RegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = Registry()

    def register(self, **overrides: object) -> None:
        options = {
            "id": "test.read",
            "version": "1.0",
            "title": "读取测试",
            "summary": "读取测试内容。",
            "domain": "test",
            "intent": "read",
            "effect": "read",
            "confirm": "L0",
            "permissions": [],
            "assistants": [],
            "mcp": False,
            "input": Empty,
            "output": Empty,
            "preconditions": [],
            "idempotent": False,
            "audit": "none",
            "limits": {},
        }
        options.update(overrides)
        self.registry.capability(**options)(echo)

    def test_duplicate_id_rejected(self) -> None:
        self.register()
        with self.assertRaises(ValueError):
            self.register()

    def test_analyst_cannot_bind_write(self) -> None:
        with self.assertRaises(ValueError):
            self.register(effect="write_draft", assistants=["analyst"])

    def test_external_write_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.register(effect="write_draft", mcp=True)

    def test_extra_fields_rejected_at_registration(self) -> None:
        class Loose(BaseModel):
            pass

        with self.assertRaises(ValueError):
            self.register(input=Loose)

    def test_unknown_permission_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.register(permissions=["arbitrary.permission"])

    def test_unknown_precondition_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.register(preconditions=["unknown"])

    def test_unknown_capability(self) -> None:
        with self.assertRaises(ApiError) as caught:
            self.registry.invoke("missing.read", CapabilityContext(principal()), {})
        self.assertEqual(caught.exception.code, "NOT_FOUND")

    def test_permission_denied(self) -> None:
        self.register(permissions=["build.edit"])
        with self.assertRaises(ApiError) as caught:
            self.registry.invoke("test.read", CapabilityContext(principal("viewer"), permissions=frozenset()), {})
        self.assertEqual(caught.exception.code, "PERMISSION_DENIED")

    def test_page_only_tool_denied(self) -> None:
        self.register(confirm="L3", assistants=["builder"])
        with self.assertRaises(ApiError) as caught:
            self.registry.invoke("test.read", CapabilityContext(principal(), binding="tool", assistant="builder"), {})
        self.assertEqual(caught.exception.code, "PAGE_ONLY_ACTION")

    def test_confirmation_required_for_tool(self) -> None:
        self.register(confirm="L2", assistants=["builder"])
        with self.assertRaises(ApiError) as caught:
            self.registry.invoke("test.read", CapabilityContext(principal(), binding="tool", assistant="builder"), {})
        self.assertEqual(caught.exception.code, "CONFIRMATION_REQUIRED")

    def test_metadata_audit_excludes_input(self) -> None:
        self.register(audit="metadata")
        with patch("backend.security.store.append_audit") as audit:
            self.registry.invoke("test.read", CapabilityContext(principal(), permissions=frozenset()), {})
        self.assertEqual(audit.call_count, 1)
        self.assertEqual(set(audit.call_args.args[4]), {"capability_id", "result_code", "count", "elapsed_ms"})

    def test_read_capability_uses_database_readonly(self) -> None:
        self.register()
        with patch("backend.capabilities.registry.business_readonly") as readonly:
            self.registry.invoke("test.read", CapabilityContext(principal(), permissions=frozenset()), {})
        readonly.assert_called_once_with(True)

    def test_readonly_context_can_read(self) -> None:
        self.register()
        self.assertIsInstance(self.registry.invoke("test.read", CapabilityContext(principal(readonly=True)), {}), Empty)

    def test_readonly_context_cannot_write(self) -> None:
        self.register(effect="write_draft")
        with self.assertRaises(ApiError) as caught:
            self.registry.invoke("test.read", CapabilityContext(principal(readonly=True)), {})
        self.assertEqual(caught.exception.code, "PERMISSION_DENIED")
