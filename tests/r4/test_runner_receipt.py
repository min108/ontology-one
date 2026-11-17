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
        root = Path(__file__).resolve().parents[2]
        with patch.object(sys, "path", [str(root / "e2e"), *sys.path]):
            runner = importlib.import_module("e2e.run_r3")
        for mode in ("complete", "exception", "incomplete"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(dir=root / "work") as temporary:
                output = Path(temporary) / "receipt"
                scope = MagicMock()
                scope.created = {"owned"}
                scope.cleaned = {"owned"} if mode == "complete" else set()
                scope.__enter__.return_value = scope
                scope.__exit__.return_value = False
                if mode == "exception":
                    scope.__exit__.side_effect = OSError("injected cleanup failure")
                process = MagicMock()
                process.poll.return_value = 0
                provider = MagicMock(requests=0, key="synthetic-only", url="http://127.0.0.1:1")
                manager = MagicMock()
                manager.__enter__.return_value = provider
                with ExitStack() as stack, redirect_stdout(io.StringIO()):
                    for target, value in (
                        ("DatabaseScope", MagicMock(return_value=scope)),
                        ("Provider", MagicMock(return_value=manager)),
                        ("check_ports", MagicMock()),
                        ("run_command", MagicMock()),
                        ("wait_health", MagicMock()),
                    ):
                        stack.enter_context(patch.object(runner, target, value))
                    stack.enter_context(patch.object(runner.subprocess, "Popen", return_value=process))
                    stack.enter_context(patch.object(runner.subprocess, "check_output", return_value="test-commit"))
                    stack.enter_context(
                        patch.object(
                            sys, "argv", ["run_r4.py", "--skip-build", "--mainline-only", "--output", str(output)]
                        )
                    )
                    code = runner.main("r4")
                receipt = json.loads((output / "runner.json").read_text())
                self.assertEqual(code, 0 if mode == "complete" else 1)
                self.assertEqual(receipt["status"], "passed" if mode == "complete" else "failed")
                self.assertEqual(receipt["databases_removed"], mode == "complete")
                if mode == "exception":
                    self.assertEqual(receipt["error_type"], "OSError")
