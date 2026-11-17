"""Environment model opt-in, truthful labels and child-process boundaries."""

import os
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from backend import agent_tasks, ai
from backend import skill_harness as harness
from backend.mcp_server import ProcessExecutor
from ops.skill_runner import DockerRunner
from scripts import start_r2_demo


class ModelEnvironmentTests(unittest.TestCase):
    def test_skill_runner_child_environment_does_not_change_parent(self) -> None:
        with (
            patch.dict(os.environ, {"DEEPSEEK_API_KEY": "synthetic-runner-key", "DEEPSEEK_MODEL": "synthetic-model"}),
            patch.object(DockerRunner, "command", return_value=SimpleNamespace(stdout=b"", returncode=1)),
            patch("ops.skill_runner.threading.Thread"),
        ):
            runner = DockerRunner("/tmp/synthetic-runner.sock")
            self.assertFalse(any(name.upper().startswith("DEEPSEEK_") for name in runner.environment))
            self.assertEqual(os.environ["DEEPSEEK_API_KEY"], "synthetic-runner-key")

    def test_only_explicit_one_overrides_tenant_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "harness.json"
            path.write_bytes(
                harness._json(
                    {
                        "base_url": harness.DEFAULT_BASE_URL,
                        "model": "tenant-model",
                        "api_key": "synthetic-tenant-key",
                        "revision": "tenant",
                        "verified": True,
                    }
                )
            )
            path.chmod(0o600)
            with (
                patch.object(harness, "_config_path", return_value=path),
                patch.object(harness, "read_bytes", side_effect=lambda p: p.read_bytes()),
            ):
                for switch in ("", "0", "true", "false", "2"):
                    with (
                        self.subTest(switch=switch),
                        patch.dict(
                            os.environ,
                            {
                                "ONTOLOGY_MODEL_FROM_ENV": switch,
                                "DEEPSEEK_API_KEY": "synthetic-environment-key",
                            },
                        ),
                    ):
                        self.assertEqual(harness._configuration()["model"], "tenant-model")
                with patch.dict(
                    os.environ,
                    {
                        "ONTOLOGY_MODEL_FROM_ENV": "1",
                        "DEEPSEEK_API_KEY": "synthetic-environment-key",
                        "DEEPSEEK_MODEL": "environment-model",
                    },
                ):
                    self.assertEqual(harness._configuration()["model"], "environment-model")
                    self.assertIn(
                        "由运行环境统一配置，所有机构共用", ai._public_status(ai.config())["configuration_notice"]
                    )
                    with (
                        patch.object(harness, "_principal"),
                        patch.object(harness, "_runtime"),
                        patch.object(harness, "_work_root"),
                    ):
                        self.assertIn(
                            "由运行环境统一配置，所有机构共用", harness.harness_status()["configuration_notice"]
                        )
                self.assertIn(b"synthetic-tenant-key", path.read_bytes())
                self.assertNotIn(b"synthetic-environment-key", path.read_bytes())

    def test_source_uses_endpoint_and_model_and_override_cannot_promote(self) -> None:
        for endpoint, model, expected in (
            ("https://api.deepseek.com/anthropic", "deepseek-flash", "analyst"),
            ("http://127.0.0.1:9999", "deepseek-flash", "mock"),
            ("https://localhost", "deepseek-flash", "mock"),
            ("https://[::1]", "deepseek-flash", "mock"),
            ("https://10.0.0.1", "deepseek-flash", "mock"),
            ("https://model.internal", "deepseek-flash", "mock"),
            ("https://model.example.invalid", "deepseek-flash", "mock"),
            ("https://api.deepseek.com/anthropic", "synthetic-r2", "mock"),
            ("https://api.deepseek.com/anthropic", "mock-model", "mock"),
            ("https://mock.example.com/anthropic", "deepseek-flash", "mock"),
        ):
            config = {"base_url": endpoint, "model": model}
            with self.subTest(endpoint=endpoint, model=model):
                for override in ("", "real", "analyst"):
                    with patch.dict(os.environ, {"ONTOLOGY_MODEL_SOURCE": override}):
                        self.assertEqual(harness.model_source_kind(config), expected)
                with patch.dict(os.environ, {"ONTOLOGY_MODEL_SOURCE": "mock"}):
                    self.assertEqual(harness.model_source_kind(config), "mock")


class ChildEnvironmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_label_is_derived_from_actual_task_configuration(self) -> None:
        config = {
            "base_url": "http://127.0.0.1:9999",
            "model": "deepseek-flash",
            "api_key": "synthetic-key",
            "revision": "one",
        }
        with (
            patch.dict(os.environ, {"ONTOLOGY_MODEL_SOURCE": "real"}),
            patch.object(agent_tasks, "use_context", return_value=nullcontext()),
            patch.object(agent_tasks, "current"),
            patch.object(agent_tasks, "append_audit"),
            patch.object(harness, "_configuration", return_value=config),
            patch.object(harness, "_runtime", return_value="synthetic-runtime"),
            patch.object(harness.EXECUTOR, "execute", new_callable=AsyncMock) as execute,
        ):
            execute.return_value = {"text": "业务回答", "usage": {}, "source_kind": "analyst"}
            result = await agent_tasks.execute_controlled(None, mode="agent", metadata={})
            self.assertEqual(result["source_kind"], "mock")

    async def test_mcp_default_and_explicit_environments_strip_provider_values(self) -> None:
        executor = ProcessExecutor(1)
        parent = {"DEEPSEEK_API_KEY": "synthetic-key", "DEEPSEEK_BASE_URL": "synthetic-url", "MYSQL_PASSWORD": "db"}
        for explicit in (None, dict(parent), {}):
            with (
                self.subTest(explicit=explicit),
                patch.dict(os.environ, parent),
                patch("backend.mcp_server.asyncio.create_subprocess_exec", new_callable=AsyncMock) as spawn,
            ):
                await executor._spawn_command(["synthetic-command"], explicit)
                environment = spawn.call_args.kwargs["env"]
                self.assertFalse(any(name.upper().startswith("DEEPSEEK_") for name in environment))
                self.assertEqual(os.environ["DEEPSEEK_API_KEY"], "synthetic-key")
                if explicit != {}:
                    self.assertEqual(environment["MYSQL_PASSWORD"], "db")
                else:
                    self.assertEqual(environment, {})

    async def test_windows_mcp_spawn_also_strips_provider_values(self) -> None:
        with (
            patch("backend.mcp_server.os.name", "nt"),
            patch("backend.windows_async_process.async_spawn", new_callable=AsyncMock) as spawn,
        ):
            await ProcessExecutor(1)._spawn_command(
                ["synthetic-command"],
                {
                    "DEEPSEEK_API_KEY": "synthetic-key",
                    "deepseek_custom": "synthetic-option",
                    "MYSQL_PASSWORD": "db",
                },
            )
            self.assertEqual(spawn.call_args.kwargs["env"], {"MYSQL_PASSWORD": "db"})


class DemoEnvironmentTests(unittest.TestCase):
    def test_demo_selects_model_overlay_only_when_explicitly_enabled(self) -> None:
        source_root = start_r2_demo.ROOT
        for switch in ("", "0", "1"):
            with (
                self.subTest(switch=switch),
                tempfile.TemporaryDirectory() as folder,
                patch.object(start_r2_demo, "ROOT", Path(folder)),
                patch.dict(
                    os.environ,
                    {
                        "ONTOLOGY_MODEL_FROM_ENV": switch,
                        "DEEPSEEK_API_KEY": "synthetic-launch-key",
                        "DEEPSEEK_MODEL": "synthetic-launch-model",
                        "SSL_CERT_FILE": "",
                    },
                ),
                patch.object(start_r2_demo.getpass, "getpass", return_value="SyntheticDemo123"),
                patch.object(start_r2_demo.subprocess, "run") as run,
                patch("builtins.print"),
            ):
                start_r2_demo.main()
                self.assertEqual(run.call_count, 2)
                for call in run.call_args_list:
                    overlay = str(Path(folder) / "docker-compose.model-env.yml")
                    self.assertEqual(overlay in call.args[0], switch == "1")
                    environment = call.kwargs["env"]
                    self.assertEqual("DEEPSEEK_API_KEY" in environment, switch == "1")
                    self.assertEqual("DEEPSEEK_MODEL" in environment, switch == "1")
                self.assertEqual(os.environ["DEEPSEEK_API_KEY"], "synthetic-launch-key")
                for path in Path(folder).rglob("*"):
                    if path.is_file():
                        self.assertNotIn("synthetic-launch-key", path.read_text())
        self.assertNotIn("DEEPSEEK_", (source_root / "docker-compose.yml").read_text())
        overlay_text = (source_root / "docker-compose.model-env.yml").read_text()
        self.assertNotIn("migrate:", overlay_text)
        for service in ("api", "agent", "worker"):
            self.assertIn("  " + service + ":", overlay_text)
