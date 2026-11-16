"""Management acceptance gets an isolated provider and never joins the global SDK run."""

import os
import unittest
from unittest.mock import MagicMock, patch


class AdminRunnerTests(unittest.TestCase):
    def test_provider_is_scoped_and_secrets_only_enter_child_environment(self) -> None:
        from e2e.run_admin import main

        provider = MagicMock()
        provider.model_url = "http://127.0.0.1:12345"
        provider.model_key = "synthetic-memory-only"
        provider.model_requests = 1
        manager = MagicMock()
        manager.__enter__.return_value = provider
        before = dict(os.environ)
        with patch("e2e.run_admin.HoldProvider", return_value=manager), patch("e2e.run_admin.subprocess.run") as run:
            run.return_value.returncode = 0
            self.assertEqual(main(["--evidence", "work/admin-test", "--api-port", "8875", "--agent-port", "8876"]), 0)
        args, kwargs = run.call_args
        self.assertNotIn("synthetic-memory-only", str(args))
        self.assertNotIn("--harness", args[0])
        self.assertEqual(args[0][-2:], ["--config", "e2e/admin.config.mjs"])
        self.assertEqual(kwargs["env"]["E2E_ADMIN_MODEL_KEY"], "synthetic-memory-only")
        self.assertEqual(kwargs["env"]["ONTOLOGY_HARNESS_ALLOW_LOOPBACK_TEST"], "1")
        manager.__exit__.assert_called_once()
        self.assertEqual(dict(os.environ), before)

    def test_provider_closes_after_child_failure(self) -> None:
        from e2e.run_admin import main

        manager = MagicMock()
        manager.__enter__.return_value.model_requests = 0
        with (
            patch("e2e.run_admin.HoldProvider", return_value=manager),
            patch("e2e.run_admin.subprocess.run", side_effect=OSError("failed")),
            self.assertRaises(OSError),
        ):
            main(["--evidence", "work/admin-test"])
        manager.__exit__.assert_called_once()
