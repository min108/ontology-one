"""Principal identity must survive the agent/MCP/job JSON process boundary."""

import json
import unittest
from dataclasses import asdict, replace
from unittest.mock import patch

from fastapi import HTTPException

from backend.mcp_tools import _SessionPolicy
from backend.security.context import Principal, has_readonly_session_execution, readonly_session_execution
from tests.security.test_permissions import principal


def roundtrip(person: Principal) -> Principal:
    return Principal(**json.loads(json.dumps(asdict(person))))


class PrincipalSerializationTests(unittest.TestCase):
    def test_empty_duties_survive_json_identity_comparison(self) -> None:
        original = principal()
        self.assertEqual(roundtrip(original), original)
        self.assertIsInstance(roundtrip(original).duties, tuple)

    def test_granted_duties_survive_json_identity_comparison(self) -> None:
        original = principal(home_tenant_id="hq", duties=("group_steward",))
        self.assertEqual(roundtrip(original), original)
        self.assertEqual(roundtrip(original).duties, ("group_steward",))

    def test_session_tool_policy_accepts_same_identity_after_worker_roundtrip(self) -> None:
        original = principal()
        policy = _SessionPolicy(roundtrip(original), [])
        with patch("backend.mcp_tools.assert_principal_current", return_value=original):
            self.assertEqual(policy._current(original), original)

    def test_session_tool_policy_still_rejects_changed_identity(self) -> None:
        original = principal()
        policy = _SessionPolicy(roundtrip(original), [])
        for altered in (
            replace(original, tenant_id="foreign"),
            replace(original, auth_version=2),
            replace(original, duties=("group_steward",)),
        ):
            with self.subTest(altered=altered), self.assertRaises(HTTPException):
                policy._current(altered)

    def test_owned_readonly_context_survives_worker_roundtrip(self) -> None:
        original = principal()
        with readonly_session_execution(roundtrip(original), lambda: True):
            self.assertTrue(has_readonly_session_execution(original))

    def test_external_list_cannot_mutate_frozen_identity(self) -> None:
        duties = ["group_steward"]
        person = principal(duties=duties)
        duties.clear()
        self.assertEqual(person.duties, ("group_steward",))

    def test_old_worker_payload_without_duties_remains_compatible(self) -> None:
        payload = asdict(principal())
        del payload["duties"]
        self.assertEqual(Principal(**payload).duties, ())

    def test_malformed_duties_are_rejected(self) -> None:
        for invalid in ("group_steward", {"group_steward": True}, [42], None):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                principal(duties=invalid)
