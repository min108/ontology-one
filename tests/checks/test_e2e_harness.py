"""Local provider contracts do not execute the SDK or claim browser acceptance."""

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from e2e.harness_fixture import HoldProvider, process_tree


class HoldProviderTests(unittest.TestCase):
    def test_real_http_sse_contains_only_synthetic_confirmation(self) -> None:
        with HoldProvider() as provider:
            request = urllib.request.Request(
                provider.model_url + "/v1/messages",
                data=b'{"model":"synthetic","messages":[{"content":"private prompt"}]}',
                headers={"X-Api-Key": provider.model_key, "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request) as response:
                result = response.read().decode()
            self.assertIn("event: message_stop", result)
            self.assertNotIn("private prompt", json.dumps(provider.state()))
            self.assertEqual(provider.state()["model_requests"], 1)

    def test_model_and_control_require_independent_ephemeral_credentials(self) -> None:
        with HoldProvider() as provider:
            for path in ("/v1/messages", "/control/state"):
                request = urllib.request.Request(provider.model_url + path, data=b"{}")
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(request)
                self.assertEqual(error.exception.code, 403)
            self.assertNotEqual(provider.model_key, provider.control_key)

    def test_hold_is_observable_and_close_releases_blocked_request(self) -> None:
        provider = HoldProvider()
        provider.__enter__()
        provider.hold()
        responses = []

        def invoke() -> None:
            request = urllib.request.Request(
                provider.model_url + "/v1/messages", data=b"{}", headers={"X-Api-Key": provider.model_key}
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                responses.append(response.status)

        thread = threading.Thread(target=invoke)
        thread.start()
        try:
            self.assertTrue(provider.received.wait(2))
            self.assertEqual(responses, [])
            self.assertEqual(provider.state()["held_requests"], 1)
        finally:
            provider.close()
            thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(responses, [200])

    def test_process_tree_includes_only_descendants_with_start_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for pid, parent in ((100, 1), (101, 100), (102, 101), (200, 1)):
                (root / str(pid)).mkdir()
                # /proc stat fields 3..22: state, ppid, eighteen fields ending in starttime.
                (root / str(pid) / "stat").write_text(f"{pid} (a process) S {parent} " + "0 " * 17 + str(pid * 10))
            self.assertEqual(process_tree(100, root), {101: "1010", 102: "1020"})

    def test_pid_reuse_does_not_keep_old_sdk_process_alive(self) -> None:
        with HoldProvider() as provider:
            provider.observed = {123: "old-start"}
            with patch("e2e.harness_fixture.process_identity", return_value=(1, "new-start")):
                self.assertTrue(provider.state()["observed_processes_reaped"])


class HarnessConfigurationTests(unittest.TestCase):
    def test_nonlocal_fixture_endpoint_is_rejected(self) -> None:
        from e2e.harness_fixture import HarnessFixture

        with self.assertRaises(ValueError):
            HarnessFixture("https://example.invalid", Path("work"), {}, 123)

    def test_existing_credentials_are_not_overwritten_or_removed(self) -> None:
        from e2e.harness_fixture import HarnessFixture

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "dept-01/data/runtime/harness.json"
            config.parent.mkdir(parents=True)
            config.write_bytes(b"original-private-configuration")
            fixture = HarnessFixture("http://127.0.0.1:8785", root, {}, 123)
            with patch.object(
                fixture,
                "request",
                side_effect=[{"csrf_token": "private", "workspace": {"tenant_id": "dept-01"}}, {"configured": True}],
            ):
                try:
                    with self.assertRaisesRegex(ValueError, "existing model"):
                        fixture.start()
                finally:
                    fixture.close()
            self.assertEqual(config.read_bytes(), b"original-private-configuration")

    def test_failed_sdk_verification_restores_original_configuration(self) -> None:
        from e2e.harness_fixture import HarnessFixture

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "dept-01/data/runtime/harness.json"
            config.parent.mkdir(parents=True)
            config.write_bytes(b"original-empty-configuration")
            fixture = HarnessFixture("http://127.0.0.1:8785", root, {}, 123)
            with patch.object(
                fixture,
                "request",
                side_effect=[
                    {"csrf_token": "private", "workspace": {"tenant_id": "dept-01"}},
                    {"configured": False},
                    {},
                    {"ok": False},
                ],
            ):
                try:
                    with self.assertRaisesRegex(RuntimeError, "verification failed"):
                        fixture.start()
                    config.write_bytes(b"temporary-private-key")
                finally:
                    fixture.close()
            self.assertEqual(config.read_bytes(), b"original-empty-configuration")


class HarnessEvidenceTests(unittest.TestCase):
    def test_connection_only_or_claimed_success_without_process_evidence_is_rejected(self) -> None:
        from e2e.harness_fixture import validate_harness_result

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            path.write_text(json.dumps({"status": "passed", "model_provider": "local_synthetic_messages"}))
            with self.assertRaisesRegex(ValueError, "executed UI"):
                validate_harness_result(path)
