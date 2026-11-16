"""Offline: verify content, install/build/start in a namespace with no network."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import time
from pathlib import Path

from bundle import digest, verify_manifest, verify_source_commit, verify_source_locks

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=ROOT / "work/offline/r1")
    parser.add_argument("--evidence", type=Path, default=ROOT / "work/evidence/r1/offline")
    args = parser.parse_args()
    bundle, evidence = args.bundle.resolve(), args.evidence.resolve()
    manifest = verify_manifest(bundle)
    verify_source_locks(bundle, ROOT)
    commit = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    verify_source_commit(bundle, commit)
    if evidence.exists() and any(evidence.iterdir()):
        parser.error("Evidence output must be empty; use a fresh directory for each attempt")
    evidence.mkdir(parents=True, exist_ok=True)
    if (bundle / "images.tar").exists():
        subprocess.run(["docker", "load", "-i", str(bundle / "images.tar")], check=True)
    for image in manifest["images"].values():
        subprocess.run(["docker", "image", "inspect", image], check=True, stdout=subprocess.DEVNULL)
    name = "ontology-offline-" + secrets.token_hex(6)
    # Disposable local MySQL secret is passed only through inherited environment.
    environment = dict(os.environ, MYSQL_ROOT_PASSWORD=secrets.token_urlsafe(24))
    environment["MYSQL_PASSWORD"] = environment["MYSQL_ROOT_PASSWORD"]
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "--network",
            "none",
            "-e",
            "MYSQL_ROOT_PASSWORD",
            "-e",
            "MYSQL_ROOT_HOST=%",
            manifest["images"]["mysql"],
            "--character-set-server=utf8mb4",
            "--collation-server=utf8mb4_unicode_ci",
        ],
        check=True,
        env=environment,
        stdout=subprocess.DEVNULL,
    )
    try:
        for _ in range(120):
            result = subprocess.run(
                ["docker", "exec", name, "mysqladmin", "ping", "-h127.0.0.1", "--silent"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if result.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError("Isolated MySQL did not become ready")
        mode = subprocess.check_output(
            ["docker", "inspect", "--format", "{{.HostConfig.NetworkMode}}", name], text=True
        ).strip()
        if mode != "none":
            raise RuntimeError("MySQL namespace is not network=none")
        (evidence / "isolation.json").write_text(
            json.dumps(
                {
                    "mysql_network_mode": mode,
                    "runner_network_mode": "container:" + name,
                    "images": manifest["images"],
                    "source_commit": commit,
                    "bundle_manifest_sha256": digest(bundle / "manifest.json"),
                },
                indent=2,
            )
            + "\n"
        )
        script = (
            "mkdir /src; tar -xf /bundle/source.tar -C /src; "
            "cp -a /bundle/npm-cache /tmp/npm-cache; cd /src; python ops/offline/container_smoke.py"
        )
        command = [
            "docker",
            "run",
            "--rm",
            "--network",
            "container:" + name,
            "-v",
            f"{bundle}:/bundle:ro",
            "-v",
            f"{evidence}:/evidence",
            "-e",
            "MYSQL_PASSWORD",
            "-e",
            "MYSQL_HOST=127.0.0.1",
            "-e",
            "MYSQL_DATABASE=r1_offline",
            "-e",
            "ONTOLOGY_WORKER_ENABLED=0",
            manifest["images"]["runner"],
            "bash",
            "-euc",
            script,
        ]
        with (evidence / "offline.log").open("w") as log:
            subprocess.run(command, check=True, env=environment, stdout=log, stderr=subprocess.STDOUT)
    finally:
        subprocess.run(["docker", "rm", "-fv", name], check=True, stdout=subprocess.DEVNULL)
    print(f"Offline verification passed: {evidence}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
