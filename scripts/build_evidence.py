"""Collect explicitly named evidence with source hashes and reproducible steps.

This script packages observations; it never infers that an acceptance gate passed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def reject_authentication_state(path: Path) -> None:
    """Browser storage snapshots belong outside uploaded evidence."""
    if {".auth", "e2e-auth"}.intersection(path.parts) or path.name.lower() in {
        "storagestate.json",
        "storage-state.json",
        "auth-state.json",
    }:
        raise ValueError(f"Authentication state is not evidence: {path}")
    if path.suffix.lower() != ".json":
        return
    try:
        value = json.loads(path.read_text())
    except (ValueError, UnicodeError):
        return
    if isinstance(value, dict) and isinstance(value.get("cookies"), list) and "origins" in value:
        if any(isinstance(cookie, dict) and cookie.get("value") for cookie in value["cookies"]):
            raise ValueError(f"Browser authentication cookies cannot be packaged: {path}")
        if any(isinstance(origin, dict) and origin.get("localStorage") for origin in value.get("origins", [])):
            raise ValueError(f"Browser local storage cannot be packaged: {path}")


def build_evidence(output: Path, inputs: list[Path], *, metadata: dict[str, str]) -> Path:
    sources: list[tuple[Path, Path]] = []
    destinations = set()
    for source in inputs:
        if not source.exists():
            raise FileNotFoundError(source)
        if source.is_symlink():
            raise ValueError(f"Symlink evidence is not allowed: {source}")
        if source.resolve() == output.resolve() or source.resolve() in output.resolve().parents:
            raise ValueError("Evidence output must not be inside an input directory")
        files = sorted(source.rglob("*")) if source.is_dir() else [source]
        for path in files:
            if path.is_symlink():
                raise ValueError(f"Symlink evidence is not allowed: {path}")
            if not path.is_file():
                continue
            reject_authentication_state(path)
            relative = Path(source.name) / path.relative_to(source) if source.is_dir() else Path(source.name)
            if relative in destinations:
                raise ValueError(f"Duplicate evidence destination: {relative}")
            destinations.add(relative)
            sources.append((path, relative))
    if not sources:
        raise ValueError("No evidence files supplied")
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"Output is not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    entries = []
    for source, relative in sources:
        destination = output / "files" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        entries.append(
            {"path": f"files/{relative.as_posix()}", "bytes": destination.stat().st_size, "sha256": digest(destination)}
        )
    manifest = {
        "schema": 1,
        "metadata": metadata,
        "files": entries,
        "acceptance": "Not inferred; inspect the supplied reports and independently reproduce.",
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    checksums = [(entry["sha256"], entry["path"]) for entry in entries]
    checksums.append((digest(output / "manifest.json"), "manifest.json"))
    (output / "SHA256SUMS").write_text("".join(f"{value}  {name}\n" for value, name in checksums))
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", type=Path, action="append", required=True, help="Explicit report directory or file; repeatable"
    )
    parser.add_argument("--output", type=Path, default=ROOT / "work/evidence/r1/package")
    parser.add_argument("--steps", type=Path, required=True, help="File containing the actual reproduction commands")
    args = parser.parse_args()
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    metadata = {
        "commit": commit,
        "version": (ROOT / "VERSION").read_text().strip(),
        "github_run_id": os.getenv("GITHUB_RUN_ID", ""),
        "github_run_attempt": os.getenv("GITHUB_RUN_ATTEMPT", ""),
        "github_repository": os.getenv("GITHUB_REPOSITORY", ""),
    }
    output = build_evidence(args.output, [*args.input, args.steps], metadata=metadata)
    archive = output.with_suffix(".tar.gz")
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(output, arcname=output.name)
    archive.with_suffix(archive.suffix + ".sha256").write_text(f"{digest(archive)}  {archive.name}\n")
    print(json.dumps({"package": str(output), "archive": str(archive), "sha256": digest(archive)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
