"""Loopback Messages fixture for a real SDK, with no stored prompts or credentials."""

from __future__ import annotations

import hmac
import http.cookiejar
import json
import secrets
import threading
import urllib.request
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import TracebackType
from typing import Any
from urllib.parse import urlsplit


def process_identity(pid: int, proc: Path = Path("/proc")) -> tuple[int, str] | None:
    try:
        fields = (proc / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()
        return int(fields[1]), fields[19]
    except (OSError, IndexError, ValueError):
        return None


def process_tree(parent: int, proc: Path = Path("/proc")) -> dict[int, str]:
    records = {
        int(path.name): identity
        for path in proc.iterdir()
        if path.name.isdigit() and (identity := process_identity(int(path.name), proc))
    }
    owned = {parent}
    while additions := {pid for pid, value in records.items() if value[0] in owned and pid not in owned}:
        owned.update(additions)
    return {pid: records[pid][1] for pid in owned - {parent}}


def confirmation_sse() -> bytes:
    events = [
        {
            "type": "message_start",
            "message": {
                "id": "synthetic-e2e-confirmation",
                "model": "synthetic-e2e",
                "usage": {"input_tokens": 1, "output_tokens": 0},
            },
        },
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": "Confirmed"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 1}},
        {"type": "message_stop"},
    ]
    return "".join("event: " + item["type"] + "\ndata: " + json.dumps(item) + "\n\n" for item in events).encode()


class HoldProvider:
    """Only the model wire is synthetic; callers must exercise the actual SDK."""

    def __init__(self, agent_pid: int | None = None) -> None:
        self.agent_pid = agent_pid
        self.model_key = secrets.token_urlsafe(32)
        self.control_key = secrets.token_urlsafe(32)
        self.received = threading.Event()
        self.release = threading.Event()
        self.release.set()
        self.model_requests = 0
        self.held_requests = 0
        self.observed: dict[int, str] = {}
        self.lock = threading.Lock()
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    @property
    def model_url(self) -> str:
        if self.server is None:
            raise RuntimeError("Local model fixture is not running")
        return f"http://127.0.0.1:{self.server.server_port}"

    def hold(self) -> None:
        with self.lock:
            self.held_requests = 0
            self.observed.clear()
            self.received.clear()
            self.release.clear()

    def state(self) -> dict[str, Any]:
        with self.lock:
            alive = [
                pid for pid, start in self.observed.items() if (value := process_identity(pid)) and value[1] == start
            ]
            return {
                "model_provider": "local_synthetic_messages",
                "model_requests": self.model_requests,
                "held_requests": self.held_requests,
                "observed_processes": sorted(self.observed),
                "observed_processes_reaped": bool(self.observed) and not alive,
            }

    def __enter__(self) -> HoldProvider:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: object) -> None:
                pass

            def respond(self, code: int, body: bytes, content_type: str = "application/json") -> None:
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                with suppress(BrokenPipeError, ConnectionResetError):
                    self.wfile.write(body)

            def do_GET(self) -> None:
                self.control()

            def do_POST(self) -> None:
                if self.path.startswith("/control/"):
                    self.control()
                    return
                supplied = self.headers.get("x-api-key", "")
                if not supplied:
                    supplied = self.headers.get("Authorization", "").removeprefix("Bearer ")
                if not hmac.compare_digest(supplied, owner.model_key):
                    self.respond(403, b"{}")
                    return
                if self.path not in ("/v1/messages", "/messages"):
                    self.respond(404, b"{}")
                    return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 <= size <= 2 * 1024 * 1024:
                        raise ValueError()
                    json.loads(self.rfile.read(size))  # Validate, but never retain prompts or request headers.
                except (ValueError, UnicodeDecodeError):
                    self.respond(400, b"{}")
                    return
                with owner.lock:
                    owner.model_requests += 1
                    held = not owner.release.is_set()
                    if held:
                        owner.held_requests += 1
                        if owner.agent_pid:
                            owner.observed.update(process_tree(owner.agent_pid))
                        owner.received.set()
                if held and not owner.release.wait(120):
                    self.respond(504, b"{}")
                    return
                self.respond(200, confirmation_sse(), "text/event-stream")

            def control(self) -> None:
                if not hmac.compare_digest(self.headers.get("X-E2E-Control", ""), owner.control_key):
                    self.respond(403, b"{}")
                    return
                if self.command == "POST" and self.path == "/control/hold":
                    owner.hold()
                elif self.command == "POST" and self.path == "/control/release":
                    owner.release.set()
                elif self.command != "GET" or self.path != "/control/state":
                    self.respond(404, b"{}")
                    return
                self.respond(200, json.dumps(owner.state()).encode())

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def close(self) -> None:
        self.release.set()
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        if self.thread:
            self.thread.join(timeout=2)

    def __exit__(
        self, error_type: type[BaseException] | None, error: BaseException | None, traceback: TracebackType | None
    ) -> None:
        self.close()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: object, **_kwargs: object) -> None:
        return None


class HarnessFixture:
    """Own configuration only inside this runner's new tenant storage directory."""

    def __init__(self, base_url: str, storage: Path, account: dict[str, str], agent_pid: int) -> None:
        url = urlsplit(base_url)
        if url.scheme != "http" or url.hostname != "127.0.0.1" or not url.port or url.path:
            raise ValueError("Harness fixture requires this runner's loopback API")
        self.base_url = base_url
        self.storage = storage.resolve()
        self.account = account
        self.provider = HoldProvider(agent_pid)
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), NoRedirect()
        )
        self.csrf = ""
        self.tenant = ""
        self.configuration: Path | None = None
        self.original: bytes | None = None
        self.sdk_version = ""
        self.verified = False
        self.changed_configuration = False

    def request(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {"Origin": self.base_url, "Content-Type": "application/json"}
        if self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        if self.tenant:
            headers["X-Workspace-Id"] = self.tenant
        request = urllib.request.Request(
            self.base_url + "/api" + path, data=None if body is None else json.dumps(body).encode(), headers=headers
        )
        with self.opener.open(request, timeout=90) as response:
            return json.load(response)

    def start(self) -> dict[str, str]:
        self.provider.__enter__()
        session = self.request("/auth/login", self.account)
        self.csrf = session["csrf_token"]
        self.tenant = session["workspace"]["tenant_id"]
        if self.tenant != "dept-01":
            raise ValueError("Expected the isolated fixture admin's home workspace")
        self.configuration = self.storage / self.tenant / "data/runtime/harness.json"
        if self.configuration.exists():
            if self.configuration.resolve() != self.configuration:
                raise ValueError("Harness fixture refuses symbolic configuration paths")
            self.original = self.configuration.read_bytes()
        status = self.request("/skills/harness/status")
        self.sdk_version = status.get("version", "")
        if status.get("configured"):
            raise ValueError("Harness fixture refuses to replace an existing model credential")
        self.changed_configuration = True
        self.request(
            "/skills/harness/config",
            {"base_url": self.provider.model_url, "model": "synthetic-e2e", "api_key": self.provider.model_key},
        )
        if self.request("/skills/harness/test", {}).get("ok") is not True:
            raise RuntimeError("Actual SDK connection verification failed")
        self.verified = True
        return {"E2E_HARNESS_URL": self.provider.model_url, "E2E_HARNESS_CONTROL": self.provider.control_key}

    def close(self) -> None:
        # Runner stops its API/agent processes first, so no active task can race restoration.
        self.provider.close()
        if self.changed_configuration and self.configuration is not None:
            if self.original is None:
                self.configuration.unlink(missing_ok=True)
            else:
                self.configuration.write_bytes(self.original)
                self.configuration.chmod(0o600)


def validate_harness_result(path: Path) -> None:
    result = json.loads(path.read_text())
    if (
        result.get("status") != "passed"
        or result.get("model_provider") != "local_synthetic_messages"
        or result.get("sdk_processes_reaped") is not True
        or not result.get("sdk_processes")
        or result.get("cancel_http_status") != 200
        or result.get("model_requests", 0) < 1
        or result.get("states") != ["running", "view_closed_while_running", "cancelled"]
    ):
        raise ValueError("Harness acceptance requires executed UI cancellation and process cleanup evidence")
