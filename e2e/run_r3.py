"""R3 action acceptance and consecutive UI mainlines, disposable original fixture, optional real model via env only."""

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

from r3_model import Provider
from r3_runner_metadata import model_metadata
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


def main(round_name: str = "r3") -> int:
    if round_name not in {"r3", "r4"}:
        raise ValueError("Unknown acceptance round")
    parser = argparse.ArgumentParser(description=__doc__.replace("R3", round_name.upper()))
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--repetitions", type=int, help="UI mainline repetitions (live default: 3; early rehearsal: 1)")
    parser.add_argument("--mainline-only", action="store_true")
    parser.add_argument(
        "--material",
        choices=("regulation-plain", "regulation-repeal", "regulation-history"),
        default="regulation-plain",
        help="Reviewed R4-A.1 source used by the browser mainline",
    )
    parser.add_argument("--grep", help="Run only matching UI cases while diagnosing a failed action")
    parser.add_argument("--skip-build", action="store_true", help="Use a build from this exact worktree")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--api-port", type=int, default=8895)
    parser.add_argument("--agent-port", type=int, default=8896)
    if round_name == "r4":
        parser.add_argument("--benchmark", action="store_true", help="Run BA12 once after the S1-S12 browser receipt")
    args = parser.parse_args()
    benchmark = getattr(args, "benchmark", False)
    if benchmark and (args.live or args.mainline_only or args.repetitions not in {None, 1}):
        raise ValueError("BA12 attaches to one local R4-B browser acceptance run")
    if args.live and not os.getenv("DEEPSEEK_API_KEY"):
        raise ValueError("Live rehearsal requires DEEPSEEK_API_KEY in the process environment")
    repetitions = args.repetitions if args.repetitions is not None else (3 if args.live else 1)
    if not 1 <= repetitions <= 3:
        raise ValueError("Repetitions must be 1 to 3")
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / "work") or output.exists():
        raise ValueError("Use a new directory under work/")
    output.mkdir(parents=True)
    check_ports(args.api_port, args.agent_port)
    clean_env = {
        key: value
        for key, value in os.environ.items()
        if key.upper() != "DEEPSEEK" and not key.upper().startswith("DEEPSEEK_")
    }
    if not args.skip_build:
        run_command(["npm", "--prefix", "frontend", "run", "build"], ROOT, clean_env, output / "build.log")
    if repetitions > 1:
        # Each complete rehearsal starts from the same original fixture in a new
        # namespace. Otherwise earlier runs intentionally trigger duplicate checks
        # and no longer exercise the same acceptance path. Never retry a failure.
        runs = []
        for index in range(1, repetitions + 1):
            destination = output / f"run-{index}"
            command = [
                sys.executable,
                f"e2e/run_{round_name}.py",
                "--repetitions",
                "1",
                "--skip-build",
                "--material",
                args.material,
                "--output",
                str(destination),
                "--api-port",
                str(args.api_port),
                "--agent-port",
                str(args.agent_port),
                *(["--live"] if args.live else []),
                *(["--mainline-only"] if args.mainline_only else []),
                *(["--grep", args.grep] if args.grep else []),
            ]
            completed = subprocess.run(command, cwd=ROOT, env=dict(os.environ), check=False)
            receipt = json.loads((destination / "runner.json").read_text())
            runs.append(receipt)
            if completed.returncode or receipt["status"] != "passed":
                break
        summary = {
            "provider": "real_deepseek" if args.live else "synthetic_messages_real_sdk",
            **({"material": args.material + ".txt"} if round_name == "r4" else {}),
            **(model_metadata(dict(os.environ)) if args.live else {}),
            "channel": "browser_ui",
            "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, cwd=ROOT).strip(),
            "status": "passed"
            if len(runs) == repetitions and all(row["status"] == "passed" for row in runs)
            else "failed",
            "repetitions": repetitions,
            "attempted": len(runs),
            "consecutive": True,
            "runs": runs,
            "services_stopped": all(row["services_stopped"] for row in runs),
            "databases_removed": all(row["databases_removed"] for row in runs),
        }
        (output / "runner.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(summary, ensure_ascii=False))
        return 0 if summary["status"] == "passed" else 1
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
        **({"material": args.material + ".txt"} if round_name == "r4" else {}),
        "status": "failed",
        "channel": "browser_ui",
        "selection": args.grep or ("mainline" if args.mainline_only or args.live else "all_actions"),
        "repetitions": repetitions,
        "consecutive": True,
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, cwd=ROOT).strip(),
    }
    processes = []
    with (
        tempfile.TemporaryDirectory(prefix=f"{round_name}-actions-", dir=ROOT / "work") as temporary,
        ExitStack() as stack,
    ):
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
            E2E_R3_LIVE="1" if args.live else "0",
            E2E_R4="1" if round_name == "r4" else "0",
            E2E_R4A1_MATERIAL=args.material,
            ONTOLOGY_MODEL_SOURCE="analyst" if args.live else "mock",
            ONTOLOGY_MODEL_FROM_ENV="1",
            E2E_PYTHON=sys.executable,
            E2E_R3_REPETITIONS=str(repetitions),
            E2E_R3_MAINLINE_ONLY="1" if args.mainline_only or args.live else "0",
        )
        if provider:
            environment.update(
                DEEPSEEK_API_KEY=provider.key,
                DEEPSEEK_BASE_URL=provider.url,
                DEEPSEEK_MODEL="synthetic-r2",
                ONTOLOGY_HARNESS_ALLOW_LOOPBACK_TEST="1",
            )
        report.update(model_metadata(environment))
        try:
            with scope:
                try:
                    bootstrap = {
                        key: value
                        for key, value in environment.items()
                        if key.upper() != "DEEPSEEK" and not key.upper().startswith("DEEPSEEK_")
                    }
                    run_command(
                        [sys.executable, "tests/fixtures/v070/load.py"], ROOT, bootstrap, output / "fixture.log"
                    )
                    scope.record_created()
                    if round_name == "r4" and not args.live and not args.mainline_only:
                        run_command(
                            [sys.executable, "e2e/r4_legacy_seed.py", "seed"],
                            ROOT,
                            bootstrap,
                            output / "r4-legacy-seed.log",
                        )
                    run_command([sys.executable, "scripts/migrate.py"], ROOT, bootstrap, output / "migrate.log")
                    scope.record_created()
                    if round_name == "r4" and not args.live and not args.mainline_only:
                        run_command(
                            [sys.executable, "e2e/r4_legacy_seed.py", "verify"],
                            ROOT,
                            bootstrap,
                            output / "r4-legacy-verify.log",
                        )
                        run_command([sys.executable, "e2e/r4_seed.py"], ROOT, bootstrap, output / "r4-seed.log")
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
                    browser_command = [
                        "node",
                        "frontend/node_modules/@playwright/test/cli.js",
                        "test",
                        "--config",
                        f"e2e/{round_name}.config.mjs",
                        *(["--grep", args.grep] if args.grep else []),
                    ]
                    if args.live:
                        # Playwright assertion diagnostics can quote the real answer.
                        # Keep stdout in memory from the outset; persist only a status.
                        browser_result = subprocess.run(
                            browser_command,
                            cwd=ROOT,
                            env=bootstrap,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            check=False,
                        )
                        (output / "browser.log").write_text(
                            json.dumps({"channel": "browser_ui", "returncode": browser_result.returncode}) + "\n"
                        )
                        if browser_result.returncode:
                            raise subprocess.CalledProcessError(browser_result.returncode, browser_command)
                    else:
                        run_command(browser_command, ROOT, bootstrap, output / "browser.log")
                    search_receipt = output / "r4b-search.json"
                    if round_name == "r4" and search_receipt.is_file():
                        search = json.loads(search_receipt.read_text())
                        if search.get("success") is not True or search.get("passed") != [f"S{i}" for i in range(1, 13)]:
                            raise ValueError("R4-B requires all twelve browser search actions")
                        report["search_actions"] = search["passed"]
                    if benchmark and "search_actions" in report:
                        run_command(
                            [
                                sys.executable,
                                "-m",
                                "scripts.drills.benchmark_r4b_search",
                                "--evidence",
                                str(search_receipt),
                                "--output",
                                str(output / "ba12.json"),
                            ],
                            ROOT,
                            bootstrap,
                            output / "ba12.log",
                        )
                    report["status"] = "passed"
                finally:
                    terminate_processes(processes)
        except Exception as error:
            report["status"] = "failed"
            report["error_type"] = type(error).__name__
        finally:
            report.update(
                services_stopped=all(p.poll() is not None for p in processes),
                databases_removed=scope.created == scope.cleaned,
                model_requests=provider.requests if provider else None,
                fixture_accounts=sorted(manifest["browser"]["accounts"]),
            )
            if not report["services_stopped"] or not report["databases_removed"]:
                report["status"] = "failed"
            mainline = output / ("a1-mainline-1.json" if round_name == "r4" else "mainline-1.json")
            if mainline.is_file():
                details = json.loads(mainline.read_text())
                for key in (
                    "first_numeric_blocked",
                    "automatic_revision",
                    "steps",
                    "total_seconds",
                    "publication_checks",
                    "gate_repairs",
                    "report_checks",
                    "extraction_summary",
                    "document_date",
                    "submitted_count",
                    "submission_share",
                    "source_sha256",
                    "remaining_count",
                    "pending_count",
                    "failure",
                ):
                    if key in details:
                        report[key] = details[key]
                if not details.get("success"):
                    report["status"] = "failed"
            elif args.mainline_only or args.live or not args.grep:
                report["status"] = "failed"
                report.setdefault("error_type", "MissingBrowserReceipt")
            if round_name == "r4" and report["selection"] == "all_actions":
                previous = output / "mainline-1.json"
                if previous.is_file():
                    retained = json.loads(previous.read_text())
                    report["retained_r3_mainline"] = {
                        key: retained[key]
                        for key in ("success", "gate_repairs", "publication_checks", "report_checks")
                        if key in retained
                    }
                    if not retained.get("success"):
                        report["status"] = "failed"
                else:
                    report["status"] = "failed"
                    report.setdefault("error_type", "MissingRetainedBrowserReceipt")
            if (
                round_name == "r4"
                and (benchmark or report["selection"] == "all_actions")
                and "search_actions" not in report
            ):
                report["status"] = "failed"
                report.setdefault("error_type", "MissingSearchReceipt")
            if args.live:
                shutil.rmtree(output / "test-results", ignore_errors=True)
            (output / "runner.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
