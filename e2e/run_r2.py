"""One R2 mainline, disposable original fixture, optional real model via env only."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import ExitStack, nullcontext
from pathlib import Path

from r2_model import Provider
from run import (
    DatabaseScope,
    check_ports,
    child_environment,
    database_names,
    new_namespace,
    run_command,
    terminate_processes,
    wait_health,
)

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--api-port", type=int, default=8895)
    parser.add_argument("--agent-port", type=int, default=8896)
    args = parser.parse_args()
    if args.live and not os.getenv("DEEPSEEK_API_KEY"):
        raise ValueError("Live rehearsal requires DEEPSEEK_API_KEY in the process environment")
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / "work") or output.exists():
        raise ValueError("Use a new directory under work/")
    output.mkdir(parents=True)
    check_ports(args.api_port, args.agent_port)
    clean_env = {key: value for key, value in os.environ.items() if not key.upper().startswith("DEEPSEEK_")}
    run_command(["npm", "--prefix", "frontend", "run", "build"], ROOT, clean_env, output / "build.log")
    namespace = new_namespace()
    manifest = json.loads((ROOT / "tests/fixtures/v070/manifest.json").read_text())
    control = json.loads((ROOT / "tests/fixtures/v070/control.json").read_text())
    config = {
        "host": os.getenv("MYSQL_HOST", "127.0.0.1"),
        "port": int(os.getenv("MYSQL_PORT", "3306")),
        "user": os.getenv("MYSQL_USER", "root"),
        "password": os.getenv("MYSQL_PASSWORD", ""),
        "charset": "utf8mb4",
        "autocommit": True,
    }
    scope = DatabaseScope(config, database_names(namespace, [row["id"] for row in control["tables"]["sec_tenants"]]))
    report = {
        "provider": "real_deepseek" if args.live else "synthetic_messages_real_sdk",
        "status": "failed",
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, cwd=ROOT).strip(),
    }
    processes = []
    with tempfile.TemporaryDirectory(prefix="r2-mainline-", dir=ROOT / "work") as temporary, ExitStack() as stack:
        work = Path(temporary)
        provider = stack.enter_context(nullcontext() if args.live else Provider())
        environment = child_environment(
            dict(os.environ), namespace, work, api_port=args.api_port, agent_port=args.agent_port
        )
        environment.update(
            PYTHONPATH=str(ROOT),
            ONTOLOGY_WORKER_ENABLED="1",
            E2E_BASE_URL=f"http://127.0.0.1:{args.api_port}",
            E2E_EVIDENCE_DIR=str(output),
            E2E_DEPENDENCY_ROOT=str(ROOT / "frontend"),
            E2E_R2_LIVE="1" if args.live else "0",
            ONTOLOGY_MODEL_SOURCE="analyst" if args.live else "mock",
            ONTOLOGY_MODEL_FROM_ENV="1",
            E2E_PYTHON=sys.executable,
        )
        if provider:
            environment.update(
                DEEPSEEK_API_KEY=provider.key,
                DEEPSEEK_BASE_URL=provider.url,
                DEEPSEEK_MODEL="synthetic-r2",
                ONTOLOGY_HARNESS_ALLOW_LOOPBACK_TEST="1",
            )
        try:
            with scope:
                try:
                    bootstrap = {key: value for key, value in environment.items() if not key.startswith("DEEPSEEK_")}
                    run_command(
                        [sys.executable, "tests/fixtures/v070/load.py"], ROOT, bootstrap, output / "fixture.log"
                    )
                    scope.record_created()
                    run_command([sys.executable, "scripts/migrate.py"], ROOT, bootstrap, output / "migrate.log")
                    scope.record_created()
                    for name, module, port in [
                        ("agent", "backend.agent_service:app", args.agent_port),
                        ("api", "backend.main:app", args.api_port),
                    ]:
                        log = stack.enter_context((output / f"{name}.log").open("w"))
                        process = subprocess.Popen(
                            [sys.executable, "-m", "uvicorn", module, "--host", "127.0.0.1", "--port", str(port)],
                            cwd=ROOT,
                            env=environment,
                            stdout=log,
                            stderr=subprocess.STDOUT,
                            start_new_session=True,
                        )
                        processes.append(process)
                        wait_health(
                            process,
                            f"http://127.0.0.1:{port}" + ("/internal/health" if name == "agent" else "/api/health"),
                            "ready" if name == "agent" else "ok",
                        )
                    run_command(
                        [
                            "node",
                            "frontend/node_modules/@playwright/test/cli.js",
                            "test",
                            "--config",
                            "e2e/r2.config.mjs",
                        ],
                        ROOT,
                        bootstrap,
                        output / "browser.log",
                    )
                    report["status"] = "passed"
                finally:
                    terminate_processes(processes)
        except Exception as error:
            report["error_type"] = type(error).__name__
        finally:
            report.update(
                services_stopped=all(p.poll() is not None for p in processes),
                databases_removed=scope.created == scope.cleaned,
                model_requests=provider.requests if provider else None,
                fixture_accounts=sorted(manifest["browser"]["accounts"]),
            )
            if args.live:
                shutil.rmtree(output / "test-results", ignore_errors=True)
            (output / "runner.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
