"""Runs inside an isolated Docker network namespace, never on the host."""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


def command(args: list[str]) -> None:
    print("RUN", " ".join(args), flush=True)
    subprocess.run(args, check=True)


def main() -> int:
    evidence = Path("/evidence")
    with socket.socket() as probe:
        probe.settimeout(2)
        try:
            probe.connect(("1.1.1.1", 443))
        except OSError:
            pass
        else:
            raise RuntimeError("External network unexpectedly reachable")
    routes = Path("/proc/net/route").read_text()
    if any(line.split()[1] == "00000000" for line in routes.splitlines()[1:]):
        raise RuntimeError("Offline namespace has a default route")
    command(
        [sys.executable, "-m", "pip", "install", "--no-index", "--find-links=/bundle/wheels", "-r", "requirements.txt"]
    )
    command(["npm", "--prefix", "frontend", "ci", "--offline", "--cache", "/tmp/npm-cache", "--no-audit", "--no-fund"])
    command(["npm", "--prefix", "frontend", "run", "build"])
    command([sys.executable, "scripts/checks/check_offline.py", "--require-dist"])
    command([sys.executable, "scripts/checks/check_policy_coverage.py"])
    command([sys.executable, "scripts/migrate.py"])
    environment = dict(os.environ, ONTOLOGY_WORKER_ENABLED="0")
    with (evidence / "server.log").open("w") as server_log:
        server = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", "8765"],
            stdout=server_log,
            stderr=subprocess.STDOUT,
            env=environment,
        )
        try:
            health = None
            for _ in range(90):
                if server.poll() is not None:
                    raise RuntimeError("Application exited before startup completed")
                try:
                    with urllib.request.urlopen("http://127.0.0.1:8765/api/health", timeout=1) as response:
                        health = json.load(response)
                    break
                except OSError:
                    time.sleep(1)
            if not health or not health.get("ok"):
                raise RuntimeError("Backend health probe failed")
            with urllib.request.urlopen("http://127.0.0.1:8765/", timeout=5) as response:
                index = response.read().decode()
            if '<div id="root">' not in index or "/static/" not in index:
                raise RuntimeError("Built frontend was not served by the running backend")
            asset = re.search(r'<script[^>]+src="(/static/[^\"]+)"', index)
            if asset is None:
                raise RuntimeError("Built frontend script reference is missing")
            with urllib.request.urlopen("http://127.0.0.1:8765" + asset.group(1), timeout=5) as response:
                if response.status != 200 or len(response.read()) < 100:
                    raise RuntimeError("Built frontend script is not served")
            summary = {
                "network": "isolated loopback only; no default route; public TCP probe failed",
                "backend_install": "passed (--no-index)",
                "frontend_install": "passed (--offline)",
                "frontend_build": "passed",
                "migration": "passed",
                "backend_startup": "passed with actual lifespan and MySQL",
                "frontend_http": "passed",
                "health": health,
                "python": sys.version,
                "node": subprocess.check_output(["node", "--version"], text=True).strip(),
            }
            (evidence / "offline-result.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
            print("Offline installation/build/startup passed", flush=True)
        finally:
            server.terminate()
            try:
                server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
