"""Online: build a versioned tool image and collect wheels, npm cache and source."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

from bundle import make_manifest, verify_manifest, verify_source_locks

ROOT = Path(__file__).resolve().parents[2]
EXCLUDED = {"node_modules", ".git", ".venv", "__pycache__", "dist", "work", "data", "uploads"}


def run(command: list[str]) -> str:
    return subprocess.check_output(command, text=True).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "work/offline/r1")
    parser.add_argument(
        "--export-images", action="store_true", help="Include docker save archive for transfer to another host"
    )
    parser.add_argument(
        "--ca-bundle",
        type=Path,
        default=os.getenv("SSL_CERT_FILE"),
        help="Optional trusted corporate CA bundle for online downloads only",
    )
    parser.add_argument(
        "--reuse-dependencies",
        type=Path,
        help="Reuse a verified bundle only when dependency locks and tool Dockerfile are unchanged",
    )
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("Output must be empty; use a fresh directory to regenerate changed dependencies")
    output.mkdir(parents=True, exist_ok=True)
    if args.reuse_dependencies:
        previous = args.reuse_dependencies.resolve()
        images = verify_manifest(previous)["images"]
        verify_source_locks(previous, ROOT)
    else:
        subprocess.run(
            [
                "docker",
                "build",
                "-f",
                str(ROOT / "ops/offline/Dockerfile"),
                "-t",
                "ontology-offline-tools:r1",
                str(ROOT / "ops/offline"),
            ],
            check=True,
        )
        subprocess.run(["docker", "pull", "mysql:8.4"], check=True)
        images = {
            name: run(["docker", "image", "inspect", "--format", "{{.Id}}", tag])
            for name, tag in {"runner": "ontology-offline-tools:r1", "mysql": "mysql:8.4"}.items()
        }
    (output / "images.json").write_text(json.dumps(images, indent=2) + "\n")
    with tarfile.open(output / "source.tar", "w") as archive:
        for name in ("backend", "frontend", "scripts", "ops", "VERSION", "requirements.txt"):
            source = ROOT / name
            paths = sorted(source.rglob("*")) if source.is_dir() else [source]
            for path in paths:
                if path.is_file() and not EXCLUDED.intersection(path.relative_to(ROOT).parts):
                    archive.add(path, arcname=path.relative_to(ROOT), recursive=False)
    preparation = (
        "mkdir /src; tar -xf /bundle/source.tar -C /src; "
        "python -m pip download --only-binary=:all: -r /src/requirements.txt -d /bundle/wheels; "
        "cd /src/frontend; npm ci --cache /bundle/npm-cache --no-audit --no-fund; "
        "rm -rf /bundle/npm-cache/_logs"
    )
    if args.reuse_dependencies:
        for name in ("wheels", "npm-cache"):
            shutil.copytree(previous / name, output / name)
    else:
        certificate = []
        if args.ca_bundle:
            certificate = [
                "-v",
                f"{args.ca_bundle.resolve()}:/online-ca.pem:ro",
                "-e",
                "PIP_CERT=/online-ca.pem",
                "-e",
                "NODE_EXTRA_CA_CERTS=/online-ca.pem",
            ]
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{output}:/bundle",
                *certificate,
                images["runner"],
                "bash",
                "-euc",
                preparation,
            ],
            check=True,
        )
    if args.export_images:
        subprocess.run(["docker", "save", "-o", str(output / "images.tar"), *images.values()], check=True)
    make_manifest(output, images)
    print(f"Offline bundle prepared: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
