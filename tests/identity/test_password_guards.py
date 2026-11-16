"""H.2-1 removes only the password flag; every adjacent guard still rejects."""

import unittest
from dataclasses import replace
from unittest.mock import patch

from fastapi import HTTPException

from backend.security.context import readonly_session_execution, use_context
from tests.security.test_permissions import principal

P = replace(principal(), must_change_password=True)


class PasswordGuardTests(unittest.TestCase):
    def test_v1_password_flag_allowed_role_still_checked(self) -> None:
        from backend.security.policy import authorize

        self.assertTrue(authorize(P, "GET", "/api/entities").allowed)
        self.assertFalse(authorize(replace(P, role="viewer"), "GET", "/api/entities").allowed)

    def test_v2_password_flag_allowed_application_still_denied(self) -> None:
        from backend.security.policy import authorize_v2

        self.assertTrue(authorize_v2(P, "GET", "/api/v2/prefs").allowed)
        self.assertFalse(authorize_v2(replace(P, identity_kind="application"), "GET", "/api/v2/prefs").allowed)

    def test_readonly_execution_flag_allowed_foreign_still_denied(self) -> None:
        with readonly_session_execution(P, lambda: True):
            pass
        with (
            self.assertRaises(HTTPException),
            readonly_session_execution(replace(P, tenant_id="foreign"), lambda: True),
        ):
            pass

    def test_readonly_execution_application_still_denied(self) -> None:
        with (
            self.assertRaises(HTTPException),
            readonly_session_execution(replace(P, identity_kind="application"), lambda: True),
        ):
            pass

    def test_skills_password_flag_and_readonly(self) -> None:
        from backend import skills

        with patch.object(skills, "assert_principal_current", side_effect=lambda value: value):
            with use_context(P):
                self.assertEqual(skills._role(skills.ROLES), P)
            with use_context(replace(P, readonly=True)), self.assertRaises(HTTPException):
                skills._role(skills.ROLES)

    def test_sources_password_flag_and_role(self) -> None:
        from backend import sources

        with patch.object(sources, "assert_principal_current", side_effect=lambda value, **kw: value):
            with use_context(P):
                self.assertEqual(sources._role({"admin"}), P)
            with use_context(replace(P, role="reviewer")), self.assertRaises(HTTPException):
                sources._role({"admin"})

    def test_demo_password_flag_and_cross_tenant_write(self) -> None:
        from backend import demo

        with patch.object(demo, "assert_principal_current", side_effect=lambda value, **kw: value):
            with use_context(P):
                self.assertEqual(demo._principal(write=True), P)
            with use_context(replace(P, tenant_id="other")), self.assertRaises(HTTPException):
                demo._principal(write=True)

    def test_ai_password_flag_and_application(self) -> None:
        from backend import ai

        with patch("backend.jobs.assert_execution_principal_current", side_effect=lambda value, **kw: value):
            self.assertEqual(ai._principal(P), P)
            with self.assertRaises(HTTPException):
                ai._principal(replace(P, identity_kind="application"))

    def test_ai_admin_role_remains_required(self) -> None:
        from backend import ai

        with patch.object(ai, "assert_storage_write", return_value=P):
            self.assertEqual(ai.require_admin(), P)
        with (
            patch.object(ai, "assert_storage_write", return_value=replace(P, role="reviewer")),
            self.assertRaises(HTTPException),
        ):
            ai.require_admin()

    def test_harness_password_flag_and_readonly_write(self) -> None:
        from backend import skill_harness

        with patch.object(skill_harness, "assert_principal_current", side_effect=lambda value, **kw: value):
            self.assertEqual(skill_harness._principal(P, build=True), P)
            with self.assertRaises(HTTPException):
                skill_harness._principal(replace(P, readonly=True), build=True)

    def test_agent_tasks_password_flag_and_cross_tenant(self) -> None:
        from backend import agent_tasks

        with patch("backend.jobs.assert_execution_principal_current", side_effect=lambda value, **kw: value):
            self.assertEqual(agent_tasks.current(P), P)
            with self.assertRaises(HTTPException):
                agent_tasks.current(replace(P, tenant_id="other"))

    def test_agent_tool_password_flag_and_identity(self) -> None:
        from backend.agent_tools import SessionToolBridge

        with patch("backend.agent_tools.assert_principal_current", side_effect=lambda value: value):
            self.assertEqual(SessionToolBridge(P)._current(), P)
            with self.assertRaises(HTTPException):
                SessionToolBridge(replace(P, identity_kind="application"))._current()

    def test_agent_tool_cancel_still_denies(self) -> None:
        from backend.agent_tools import SessionToolBridge

        bridge = SessionToolBridge(P)
        bridge.cancelled.set()
        with self.assertRaises(HTTPException):
            bridge._current()

    def test_mcp_password_flag_and_readonly(self) -> None:
        from backend.mcp_tools import _SessionPolicy

        with patch("backend.mcp_tools.assert_principal_current", side_effect=lambda value: value):
            self.assertEqual(_SessionPolicy(P, ())._current(P), P)
            readonly = replace(P, readonly=True)
            with self.assertRaises(HTTPException):
                _SessionPolicy(readonly, ())._current(readonly)

    def test_integrations_password_flag_and_invalid_session(self) -> None:
        from backend import integrations

        with (
            patch.object(integrations, "authenticate_session", return_value=(P, {})),
            patch.object(integrations, "_invoke", return_value={"ok": True}),
        ):
            self.assertEqual(integrations.invoke_as_session("session", None), {"ok": True})
        with patch.object(integrations, "authenticate_session", return_value=None), self.assertRaises(HTTPException):
            integrations.invoke_as_session("session", None)

    def test_application_flag_does_not_override_expiry(self) -> None:
        from backend.security import applications
        from tests.test_applications import row

        value = {**row(), "must_change_password": 1}
        current = applications._principal(value, "")
        self.assertIsNotNone(current)
        self.assertTrue(applications._same(current, replace(current, must_change_password=True)))
        self.assertFalse(
            applications._same(current, replace(current, permission_version=current.permission_version + 1))
        )
        self.assertIsNone(applications._principal({**value, "expires_at": 1}, ""))

    def test_prefs_values_are_bounded(self) -> None:
        from backend.security.preferences import validate_preference

        for key, value in (
            ("onboarding", {"step": False}),
            ("graph_views", []),
            ("density", "comfortable"),
            ("autopilot_default", "step"),
        ):
            validate_preference(key, value)
        for key, value in (
            ("onboarding", {"step": "not-bool"}),
            ("graph_views", [{"password": "secret"}]),
            ("graph_views", [{}] * 51),
        ):
            with self.subTest(key=key), self.assertRaises(HTTPException):
                validate_preference(key, value)

    def test_audit_timezone_and_order_are_validated(self) -> None:
        from pydantic import ValidationError

        from backend.api.v2.audit import AuditInput

        for values in (
            {"since": "2025-01-01T00:00:00"},
            {"since": "2026-01-01T00:00:00Z", "until": "2025-01-01T00:00:00Z"},
            {"page_size": 101},
        ):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                AuditInput(**values)

    def test_delegate_flag_allowed_inactive_still_denied(self) -> None:
        from unittest.mock import MagicMock

        from backend.security import applications

        cursor = MagicMock()
        user = {"id": "u", "home_tenant_id": "sub-01", "role": "modeler", "is_active": 1, "must_change_password": 1}
        cursor.fetchone.return_value = user
        self.assertEqual(applications._delegate(cursor, "u", "sub-01"), user)
        cursor.fetchone.return_value = {**user, "is_active": 0}
        with self.assertRaises(HTTPException):
            applications._delegate(cursor, "u", "sub-01")

    def test_application_credential_public_flag_and_auth_version(self) -> None:
        import time
        from unittest.mock import MagicMock

        from backend.security import applications

        cursor = MagicMock()
        cursor.fetchall.return_value = [
            {
                "id": "c",
                "token_prefix": "test",
                "delegate_auth_version": 1,
                "expires_at": time.time() + 1000,
                "revoked_at": None,
                "created_at": time.time(),
                "last_used_at": None,
            }
        ]
        row = {
            "id": "app",
            "tenant_id": "sub-01",
            "name": "App",
            "description": "",
            "delegate_user_id": "u",
            "delegate_username": "user",
            "version": 1,
            "enabled": 1,
            "tool_scopes": "[]",
            "mapping_ids": "[]",
            "created_at": time.time(),
            "updated_at": time.time(),
            "is_active": 1,
            "home_tenant_id": "sub-01",
            "auth_version": 1,
            "must_change_password": 1,
        }
        self.assertTrue(applications._public(cursor, row)["credentials"][0]["usable"])
        self.assertFalse(applications._public(cursor, {**row, "auth_version": 2})["credentials"][0]["usable"])

    def test_harness_submission_old_flag_reaches_live_guard(self) -> None:
        import asyncio

        from backend.harness_runs import RunStore

        async def check() -> None:
            store = RunStore()
            with patch("backend.harness_runs.assert_principal_current", side_effect=RuntimeError("live checked")):
                with self.assertRaisesRegex(RuntimeError, "live checked"):
                    await store.submit(P, kind="controlled_task", title="test", entity_id="e", operation=None)
                with self.assertRaises(HTTPException):
                    await store.submit(
                        replace(P, identity_kind="application"),
                        kind="controlled_task",
                        title="test",
                        entity_id="e",
                        operation=None,
                    )

        asyncio.run(check())

    def test_legacy_flag_survives_frames_without_changing_readonly_identity(self) -> None:
        import json
        from dataclasses import asdict

        from backend.mcp_tools import _SessionPolicy
        from backend.security.context import Principal, has_readonly_session_execution

        captured = Principal(**json.loads(json.dumps(asdict(P))))
        current = replace(P, must_change_password=False)
        self.assertTrue(captured.must_change_password)
        with readonly_session_execution(captured, lambda: True):
            self.assertTrue(has_readonly_session_execution(current))
            policy = _SessionPolicy(captured, [])
            with patch("backend.mcp_tools.assert_principal_current", return_value=current):
                self.assertEqual(policy._current(current), current)
            for altered in (
                replace(current, auth_version=2),
                replace(current, tenant_id="foreign"),
                replace(current, role="modeler"),
                replace(current, duties=("unknown",)),
            ):
                with self.subTest(altered=altered):
                    self.assertFalse(has_readonly_session_execution(altered))
                    with self.assertRaises(HTTPException):
                        policy._current(altered)

    def test_queued_legacy_flag_frame_reaches_live_guard(self) -> None:
        import asyncio
        import json
        from dataclasses import asdict
        from unittest.mock import MagicMock

        from backend import jobs

        row = {
            "principal_json": json.dumps(asdict(P)),
            "tenant_id": P.tenant_id,
            "job_id": "j",
            "lease_token": "lease",
            "kind": "material_extract",
            "entity_id": "e",
        }
        connection = MagicMock()
        connection.execute.return_value.fetchone.return_value = {"status": "completed"}
        with (
            patch("backend.db.connect", return_value=connection),
            patch.object(
                jobs, "assert_execution_principal_current", return_value=replace(P, must_change_password=False)
            ) as guard,
        ):
            self.assertEqual(asyncio.run(jobs._invoke(row)), "completed")
            self.assertTrue(guard.call_args.args[0].must_change_password)
            with self.assertRaises(HTTPException):
                asyncio.run(jobs._invoke({**row, "tenant_id": "foreign"}))
