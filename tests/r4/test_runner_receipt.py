"""A successful browser process is insufficient when disposable cleanup fails."""

import importlib
import io
import json
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch


class RunnerReceiptTests(unittest.TestCase):
    def test_cleanup_failure_and_incomplete_cleanup_cannot_report_success(self) -> None:
        self.check_runner("r4")

    def test_r2_cleanup_failure_and_incomplete_cleanup_cannot_report_success(self) -> None:
        self.check_runner("r2")

    def check_runner(self, round_name: str) -> None:
        root = Path(__file__).resolve().parents[2]
        with patch.object(sys, "path", [str(root / "e2e"), *sys.path]):
            runner = importlib.import_module("e2e.run_r2" if round_name == "r2" else "e2e.run_r3")
        for mode in (
            "complete",
            "exception",
            "exception_after_clean",
            "incomplete",
            "service_running",
            *(["missing_evidence"] if round_name == "r4" else []),
        ):
            work = root / "work"
            work.mkdir(exist_ok=True)
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(dir=work) as temporary:
                output = Path(temporary) / "receipt"
                scope = MagicMock()
                scope.created = {"owned"}
                databases_removed = mode not in {"exception", "incomplete"}
                scope.cleaned = {"owned"} if databases_removed else set()
                scope.__enter__.return_value = scope
                scope.__exit__.return_value = False
                if mode in {"exception", "exception_after_clean"}:
                    scope.__exit__.side_effect = OSError("injected cleanup failure")
                process = MagicMock()
                process.poll.return_value = None if mode == "service_running" else 0
                provider = MagicMock(requests=0, key="synthetic-only", url="http://127.0.0.1:1")
                manager = MagicMock()
                manager.__enter__.return_value = provider

                def run_command(command, *_args):
                    if round_name == "r4" and mode != "missing_evidence" and "test" in command:
                        (output / "a1-mainline-1.json").write_text(
                            json.dumps({"success": True, "gate_repairs": [{"field": "exceptions"}]})
                        )

                with ExitStack() as stack, redirect_stdout(io.StringIO()):
                    for target, value in (
                        ("DatabaseScope", MagicMock(return_value=scope)),
                        ("Provider", MagicMock(return_value=manager)),
                        ("check_ports", MagicMock()),
                        ("run_command", run_command),
                        ("wait_health", MagicMock()),
                        ("terminate_processes", MagicMock()),
                    ):
                        stack.enter_context(patch.object(runner, target, value))
                    stack.enter_context(patch.object(runner.subprocess, "Popen", return_value=process))
                    stack.enter_context(patch.object(runner.subprocess, "check_output", return_value="test-commit"))
                    args = [f"run_{round_name}.py", "--output", str(output)]
                    if round_name == "r4":
                        args.extend(["--skip-build", "--mainline-only"])
                    stack.enter_context(patch.object(sys, "argv", args))
                    code = runner.main() if round_name == "r2" else runner.main("r4")
                receipt = json.loads((output / "runner.json").read_text())
                self.assertEqual(code, 0 if mode == "complete" else 1)
                self.assertEqual(receipt["status"], "passed" if mode == "complete" else "failed")
                self.assertEqual(receipt["databases_removed"], databases_removed)
                self.assertEqual(receipt["services_stopped"], mode != "service_running")
                if mode == "missing_evidence":
                    self.assertEqual(receipt["error_type"], "MissingBrowserReceipt")
                if mode == "complete" and round_name == "r4":
                    self.assertEqual(receipt["gate_repairs"], [{"field": "exceptions"}])
                if mode in {"exception", "exception_after_clean"}:
                    self.assertEqual(receipt["error_type"], "OSError")
