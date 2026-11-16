"""Run SET01-10 in a fresh deployment, independent of global task acceptance.

Supply MYSQL_* connection credentials and a writable ONTOLOGY_HARNESS_TMP_ROOT.
The actual pinned SDK uses a private loopback synthetic Messages provider.
Provider credentials only enter the child environment; traces are disabled for
this spec. This is not evidence of a real public model provider connection.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

if __package__:
    from .harness_fixture import HoldProvider
else:
    from harness_fixture import HoldProvider

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-port", type=int, default=8875)
    parser.add_argument("--agent-port", type=int, default=8876)
    parser.add_argument("--evidence", required=True, help="New evidence directory under this checkout work/.")
    args = parser.parse_args(argv)
    with HoldProvider() as provider:
        environment = dict(os.environ)
        environment.update(
            ONTOLOGY_HARNESS_ALLOW_LOOPBACK_TEST="1",
            E2E_ADMIN_MODEL_URL=provider.model_url,
            E2E_ADMIN_MODEL_KEY=provider.model_key,
        )
        command = [
            sys.executable,
            str(ROOT / "e2e/run.py"),
            "--target",
            "foundation",
            "--api-port",
            str(args.api_port),
            "--agent-port",
            str(args.agent_port),
            "--evidence",
            args.evidence,
            "--",
            "--config",
            "e2e/admin.config.mjs",
        ]
        result = subprocess.run(command, cwd=ROOT, env=environment, check=False)
        print(f"Management acceptance synthetic provider requests: {provider.model_requests}")
        return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
