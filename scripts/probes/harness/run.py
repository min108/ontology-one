"""Isolated official-SDK probes; stdout contains only bounded numeric/boolean evidence."""

from __future__ import annotations

import argparse
import contextlib
import importlib.metadata
import json
import os
import secrets
import signal
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
CASES = {
    "loop_cancel": "Reduce steps and let the platform resume; always reap cancelled processes.",
    "parallel": "Serialize every tool.",
    "compaction": "Return result handles and bounded summaries from the platform.",
    "retry": "Retry the whole turn once in the orchestrator.",
    "thinking": "Keep thinking disabled until live evidence exists.",
    "stream": "No downgrade: retain the existing public-progress contract.",
    "protocol": "Require a compatible Messages endpoint; no unverified protocols.",
    "digests": "Keep the pinned version until both platform digests and regression gates pass.",
}
SYNTHETIC_CASES = (
    "loop",
    "cancel",
    "parallel",
    "serial",
    "compaction",
    "retry",
    "auth",
    "thinking",
    "stream",
    "protocol",
)


def live_configuration(environment: dict[str, str]) -> dict[str, str] | None:
    key = environment.get("DEEPSEEK_API_KEY", "")
    return {"api_key": key} if key else None


def classify(case: str, evidence: dict[str, Any]) -> bool:
    if case == "cancel":
        return bool(evidence.get("cancelled") and evidence.get("reaped"))
    if case == "auth":
        return evidence.get("completed") is False and evidence.get("requests") == 1
    if not evidence.get("completed"):
        return False
    if case == "loop":
        return evidence.get("tool_calls", 0) >= 40 and evidence.get("requests", 0) >= 41
    if case in {"parallel", "serial"}:
        return evidence.get("tool_calls") == 4 and evidence.get("max_active") == (4 if case == "parallel" else 1)
    if case == "compaction":
        return evidence.get("compactions", 0) > 0 and evidence.get("surface_replacements", 0) > 0
    if case == "retry":
        return evidence.get("requests") == 2
    if case == "stream":
        return evidence.get("text_deltas", 0) >= 2
    if case == "thinking":
        return bool(evidence.get("thinking_enabled"))
    return case == "protocol" and evidence.get("messages_path") is True


def compaction_evidence(events: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "compactions": sum(
            1
            for event in events
            if event.get("type") == "compaction/summary" and event.get("data", {}).get("shadowedSeqs")
        ),
        "surface_replacements": sum(
            1
            for event in events
            if isinstance(event.get("surfaceOp"), dict) and event["surfaceOp"].get("op") == "replace"
        ),
        "compaction_failures": sum(
            1 for event in events if event.get("type") == "compaction/end" and event.get("data", {}).get("error")
        ),
    }


def build_patch(case: str, plugin: Path) -> list[dict[str, Any]]:
    disabled = [
        "persistent-bash",
        "persistent-pwsh",
        "terminal-bash",
        "terminal-pwsh",
        "tool-fs",
        "tool-working-directory",
        "mcp-resources",
        "image-offload",
        "plugin-package-inventory-deepseek",
        "sessions",
    ]
    patch: list[dict[str, Any]] = [{"id": name, "disabled": True} for name in disabled]
    provider: dict[str, Any] = {
        "thinking": "enabled" if case == "thinking" else "disabled",
        "streamIdleTimeoutMs": 15000,
    }
    if case in {"retry", "auth"}:
        provider["retryPolicy"] = {
            "mode": "normal",
            "maxRetries": 1,
            "retryableCodes": ["SERVER", "RATE_LIMIT", "TIMEOUT", "TRANSPORT"],
            "backoff": {"initialDelayMs": 1, "maxDelayMs": 10, "jitterRatio": 0},
        }
    if case == "compaction":
        provider["defaultContextWindow"] = 8192
        provider["models"] = [{"id": "probe-model", "contextWindow": 8192, "maxTokens": 256}]
    patch.extend(
        [
            {
                "id": "compaction-basic",
                "disabled": case != "compaction",
                "config": {"thresholdRatio": 0.5, "headroomTokens": 128, "retainTokens": 0, "maxTokens": 128},
            },
            {"id": "llm-retry", "disabled": case not in {"retry", "auth"}},
            {"id": "session-log-deepseek", "config": {"enabled": False}},
            {"id": "llm-deepseek", "config": provider},
            {"id": "agent-loop", "config": {"maxParallelToolCalls": 4}},
            {"insert": [{"id": "r1-probe", "name": str(plugin)}]},
        ]
    )
    return patch


class Fixture:
    """Discard all request contents after deriving non-sensitive counters."""

    def __init__(self, case: str) -> None:
        self.case = case
        self.evidence: dict[str, Any] = {"requests": 0, "tool_calls": 0, "max_active": 0, "text_deltas": 0}
        self.active = 0
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format: str, *args: object) -> None:
                pass

            def do_POST(self) -> None:
                if self.path == "/metric" and self.headers.get("Authorization") != "Bearer " + owner.token:
                    self.reply(403, b"{}", "application/json")
                    return
                if int(self.headers.get("Content-Length", "0")) > 1048576:
                    self.reply(413, b"{}", "application/json")
                    return
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                with owner.lock:
                    if self.path == "/metric":
                        kind = body.get("type")
                        if kind == "tool_start":
                            owner.active += 1
                            owner.evidence["max_active"] = max(owner.active, owner.evidence["max_active"])
                        elif kind == "tool_end":
                            owner.active -= 1
                            owner.evidence["tool_calls"] += 1
                        elif kind == "text_delta":
                            owner.evidence["text_deltas"] += 1
                        self.reply(200, b"{}", "application/json")
                        return
                    owner.evidence["requests"] += 1
                    turn = owner.evidence["requests"]
                    owner.evidence["messages_path"] = self.path == "/v1/messages"
                    owner.evidence["thinking_enabled"] = body.get("thinking", {}).get("type") == "enabled"
                if owner.case == "cancel":
                    time.sleep(4)
                    return
                if owner.case == "auth" or (owner.case == "retry" and turn == 1):
                    status = 401 if owner.case == "auth" else 503
                    self.reply(
                        status,
                        json.dumps(
                            {
                                "type": "error",
                                "error": {
                                    "type": "authentication_error" if status == 401 else "api_error",
                                    "message": "synthetic",
                                },
                            }
                        ).encode(),
                        "application/json",
                    )
                    return
                tools = (owner.case == "loop" and turn <= 40) or (
                    owner.case in {"parallel", "serial", "compaction"} and turn == 1
                )
                count = 4 if owner.case in {"parallel", "serial"} else 1
                blocks = (
                    [
                        {
                            "type": "tool_use",
                            "id": f"call-{turn}-{index}",
                            "name": "probe_write" if owner.case == "serial" else "probe_read",
                            "input": {},
                        }
                        for index in range(count)
                    ]
                    if tools
                    else [{"type": "text", "text": ""}]
                )
                events: list[dict[str, Any]] = [
                    {
                        "type": "message_start",
                        "message": {
                            "id": "synthetic",
                            "model": body.get("model"),
                            "role": "assistant",
                            "content": [],
                            "usage": {"input_tokens": 3, "output_tokens": 0},
                        },
                    }
                ]
                for index, block in enumerate(blocks):
                    events.append({"type": "content_block_start", "index": index, "content_block": block})
                    if not tools:
                        for chunk in ("Synthetic ", "answer."):
                            events.append(
                                {
                                    "type": "content_block_delta",
                                    "index": index,
                                    "delta": {"type": "text_delta", "text": chunk},
                                }
                            )
                    events.append({"type": "content_block_stop", "index": index})
                events.extend(
                    [
                        {
                            "type": "message_delta",
                            "delta": {"stop_reason": "tool_use" if tools else "end_turn"},
                            "usage": {"output_tokens": 2},
                        },
                        {"type": "message_stop"},
                    ]
                )
                wire = "".join(
                    "event: " + row["type"] + "\ndata: " + json.dumps(row) + "\n\n" for row in events
                ).encode()
                self.reply(200, wire, "text/event-stream")

            def reply(self, status: int, body: bytes, content_type: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                with contextlib.suppress(BrokenPipeError):
                    self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)


def worker(case: str, live: bool, model: str, base_url: str) -> dict[str, Any]:
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (25, 25))
    from deepseek_harness import DeepSeekHarness

    secret = json.load(sys.stdin).get("api_key") if live else "synthetic-probe-only"
    fixture = Fixture(case)
    evidence = fixture.evidence
    try:
        with tempfile.TemporaryDirectory(prefix="probe-", dir=ROOT / "work" / "r1-10") as temporary:
            directory = Path(temporary)
            patch = directory / "probe.patch.yml"
            patch.write_text(json.dumps(build_patch(case, Path(__file__).with_name("plugin.mjs"))), encoding="utf-8")
            home = directory / "home"
            home.mkdir(mode=0o700)
            os.environ.clear()
            os.environ.update(
                {
                    "PATH": os.defpath,
                    "HOME": str(home),
                    "TMPDIR": str(directory),
                    "DSH_HOME": str(home),
                    "NODE_OPTIONS": "--max-old-space-size=256 --max-semi-space-size=8",
                    "UV_THREADPOOL_SIZE": "2",
                    "PROBE_PORT": str(fixture.server.server_port),
                    "PROBE_TOKEN": fixture.token,
                    "PROBE_CASE": case,
                    "DSH_RUNTIME_MODE": "exe",
                }
            )
            with DeepSeekHarness(
                profile="sdk-minimal",
                cwd=str(directory),
                dsh_home=str(home),
                patches=(str(patch),),
                model=model if live else "probe-model",
                max_tokens=256,
                reasoning_effort="high" if case == "thinking" else "off",
                base_url=base_url if live else f"http://127.0.0.1:{fixture.server.server_port}",
                api_key=secret,
                initialize_timeout_seconds=20,
                request_timeout_seconds=65,
                shutdown_timeout_seconds=1,
            ) as harness:
                if case == "cancel":
                    runtime = harness.client._proc

                    def run_cancelled() -> None:
                        with contextlib.suppress(Exception):
                            harness.run("Synthetic cancellation fixture.", session_id="r1-cancel")

                    turn = threading.Thread(target=run_cancelled, daemon=True)
                    turn.start()
                    deadline = time.monotonic() + 10
                    while not evidence["requests"] and turn.is_alive() and time.monotonic() < deadline:
                        time.sleep(0.01)
                    evidence["cancelled"] = evidence["requests"] == 1
                    harness.close()
                    turn.join(3)
                    evidence["reaped"] = runtime is not None and runtime.poll() is not None and not turn.is_alive()
                    evidence["runtime_pid_absent"] = runtime is not None and not Path(f"/proc/{runtime.pid}").exists()
                    evidence["passed"] = classify(case, evidence) and evidence["runtime_pid_absent"]
                    return evidence
                response = harness.run(
                    "Say hello in one short sentence. Do not call tools."
                    if live
                    else "Synthetic source sentence. " * 400
                    if case == "compaction"
                    else "Run the synthetic fixture.",
                    session_id="r1-probe",
                )
                evidence["completed"] = response.finish_reason == "completed"
                evidence.update(compaction_evidence(response.events))
                if live:
                    evidence["response_present"] = bool(response.final_response)
                    evidence["reasoning_observed"] = any(
                        part.get("type") in {"reasoning", "thinking"}
                        for event in response.events
                        for part in event.get("data", {}).get("content", [])
                        if isinstance(part, dict)
                    )
                    evidence["passed"] = evidence["completed"] and evidence["response_present"]
                    if case == "thinking":
                        evidence["passed"] = evidence["passed"] and evidence["reasoning_observed"]
    except Exception as exc:
        evidence.update(completed=False, error_type=type(exc).__name__)
    finally:
        fixture.close()
    evidence.setdefault("passed", classify(case, evidence))
    return evidence


def execute(case: str, live: bool, model: str, base_url: str) -> dict[str, Any]:
    config = live_configuration({"DEEPSEEK_API_KEY": os.environ.get("DEEPSEEK_API_KEY", "")}) if live else {}
    if live and config is None:
        return {"case": case, "evidence_kind": "live-model", "status": "pending", "reason": "DEEPSEEK_API_KEY absent"}
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker",
        "--case",
        case,
        "--model",
        model,
        "--base-url",
        base_url,
    ]
    if live:
        command.append("--live")
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env={"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    try:
        raw, _ = process.communicate(json.dumps(config).encode(), timeout=90)
        try:
            result = json.loads(raw) if raw else {"passed": False, "error_type": "WorkerExit"}
        except (ValueError, UnicodeError):
            result = {"passed": False, "error_type": "WorkerOutput"}
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        result = {"passed": False, "error_type": "WallClockLimit", "worker_reaped": process.poll() is not None}
    return {
        "case": case,
        "evidence_kind": "live-model" if live else "official-sdk-synthetic-model",
        "status": "passed" if result.pop("passed", False) else "unavailable",
        **result,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=(*SYNTHETIC_CASES, "all"), default="all")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--base-url", default="https://api.deepseek.com/anthropic")
    args = parser.parse_args()
    if args.live and not args.base_url.startswith("https://"):
        parser.error("Live model endpoint must use HTTPS")
    if args.live and args.case not in {"all", "thinking", "protocol"}:
        parser.error("Live mode is limited to thinking and protocol verification")
    (ROOT / "work" / "r1-10").mkdir(parents=True, exist_ok=True)
    if args.worker:
        print(json.dumps(worker(args.case, args.live, args.model, args.base_url)))
        return
    cases = (
        ("thinking", "protocol")
        if args.live and args.case == "all"
        else SYNTHETIC_CASES
        if args.case == "all"
        else (args.case,)
    )
    statuses = []
    for case in cases:
        result = execute(case, args.live, args.model, args.base_url)
        result["sdk_version"] = importlib.metadata.version("deepseek-harness-sdk")
        print(json.dumps(result, sort_keys=True), flush=True)
        statuses.append(result["status"])
    if "unavailable" in statuses:
        raise SystemExit(1)
    if "pending" in statuses:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
