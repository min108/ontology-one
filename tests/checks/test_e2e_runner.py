"""The browser runner cannot select or clean an existing deployment."""

import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from e2e.run import (
    DatabaseScope,
    browser_counts,
    check_ports,
    child_environment,
    database_names,
    new_namespace,
    source_commit,
    terminate_processes,
)


class BrowserRunnerTests(unittest.TestCase):
    def test_namespace_is_generated_and_short_enough_for_tenant_mapping(self) -> None:
        first, second = new_namespace(), new_namespace()
        self.assertNotEqual(first, second)
        self.assertRegex(first, r"^e2e_r1_[0-9a-f]{24}$")
        self.assertLessEqual(len(first), 32)

    def test_database_names_are_derived_without_fixture_database_values(self) -> None:
        names = database_names("e2e_r1_" + "a" * 24, ["hq", "dept-01"])
        self.assertEqual(len(names), 4)
        self.assertTrue(all(name.startswith("e2e_r1_") for name in names))

    def test_external_namespace_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            database_names("production", ["hq"])

    def test_child_environment_cannot_inherit_database_or_storage_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            namespace = new_namespace()
            environment = child_environment(
                {
                    "MYSQL_DATABASE": "production",
                    "ONTOLOGY_STORAGE_ROOT": "/production",
                    "MYSQL_PASSWORD": "synthetic-secret",
                    "ONTOLOGY_WORKER_ENABLED": "1",
                },
                namespace,
                work,
                api_port=8785,
                agent_port=8786,
            )
            self.assertEqual(environment["MYSQL_DATABASE"], namespace)
            self.assertEqual(environment["MYSQL_PASSWORD"], "synthetic-secret")
            self.assertEqual(environment["ONTOLOGY_STORAGE_ROOT"], str(work / "storage"))
            self.assertEqual(environment["ONTOLOGY_WORKER_ENABLED"], "0")
            self.assertEqual(environment["ONTOLOGY_AGENT_SERVICE_URL"], "http://127.0.0.1:8786")
            self.assertEqual(environment["ONTOLOGY_TRUSTED_ORIGINS"], "http://127.0.0.1:8785")

    def test_database_url_override_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            child_environment(
                {"ONTOLOGY_MYSQL_URL": "mysql://user@host/production"},
                new_namespace(),
                Path("work/test"),
                api_port=8785,
                agent_port=8786,
            )

    def test_legacy_commit_is_fixed(self) -> None:
        self.assertEqual(source_commit("legacy"), "1359118dd672431dcd0cde2eb81f2b0f994eef37")

    def test_existing_database_is_never_dropped(self) -> None:
        scope = DatabaseScope({}, {"e2e_r1_" + "a" * 24})
        with patch.object(scope, "_existing", return_value=set(scope.candidates)), self.assertRaises(ValueError):
            scope.__enter__()
        self.assertFalse(scope.armed)
        self.assertEqual(scope.created, set())

    def test_cleanup_only_drops_exact_new_databases(self) -> None:
        allowed = "e2e_r1_" + "a" * 24
        scope = DatabaseScope({}, {allowed})
        cursor = MagicMock()
        connection = MagicMock()
        connection.__enter__.return_value.cursor.return_value.__enter__.return_value = cursor
        with (
            patch.object(scope, "_existing", side_effect=[set(), {allowed}]),
            patch("e2e.run.pymysql.connect", return_value=connection),
            scope,
        ):
            pass
        self.assertEqual(scope.created, {allowed})
        cursor.execute.assert_called_once_with(f"DROP DATABASE `{allowed}`")

    def test_failed_process_is_reaped_without_signaling_other_processes(self) -> None:
        process = MagicMock()
        process.poll.return_value = 1
        with patch("e2e.run.os.killpg") as kill:
            terminate_processes([process])
        kill.assert_not_called()
        process.wait.assert_called_once()

    def test_cleanup_runs_when_browser_phase_raises(self) -> None:
        allowed = "e2e_r1_" + "b" * 24
        scope = DatabaseScope({}, {allowed})
        connection = MagicMock()
        cursor = connection.__enter__.return_value.cursor.return_value.__enter__.return_value
        with (
            patch.object(scope, "_existing", side_effect=[set(), {allowed}]),
            patch("e2e.run.pymysql.connect", return_value=connection),
            self.assertRaises(RuntimeError),
            scope,
        ):
            raise RuntimeError("synthetic browser failure")
        cursor.execute.assert_called_once_with(f"DROP DATABASE `{allowed}`")

    def test_live_service_termination_targets_its_own_process_group(self) -> None:
        process = MagicMock()
        process.pid = 12345
        process.poll.return_value = None
        with patch("e2e.run.os.killpg") as kill:
            terminate_processes([process])
        self.assertEqual(kill.call_args.args[0], 12345)
        process.wait.assert_called_once_with(timeout=15)

    def test_listing_tests_cannot_be_reported_as_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "results.json"
            report.write_text(json.dumps({"stats": {"expected": 0, "unexpected": 0, "flaky": 0, "skipped": 0}}))
            with self.assertRaises(ValueError):
                browser_counts(report)

    def test_skipped_browser_test_is_not_a_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "results.json"
            report.write_text(json.dumps({"stats": {"expected": 74, "unexpected": 0, "flaky": 0, "skipped": 1}}))
            with self.assertRaises(ValueError):
                browser_counts(report)

    def test_live_listener_is_rejected(self) -> None:
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            with self.assertRaises(OSError):
                check_ports(listener.getsockname()[1], 8786)

    def test_recently_closed_service_port_can_be_reused(self) -> None:
        with socket.socket() as listener, socket.socket() as client, socket.socket() as other:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0))
            other.bind(("127.0.0.1", 0))
            port, other_port = listener.getsockname()[1], other.getsockname()[1]
            listener.listen()
            client.connect(("127.0.0.1", port))
            connection, _ = listener.accept()
            with connection:
                connection.shutdown(socket.SHUT_WR)
                self.assertEqual(client.recv(1), b"")
        check_ports(port, other_port)
