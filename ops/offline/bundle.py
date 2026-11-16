"""Content manifest shared by online preparation and isolated verification."""

from __future__ import annotations

import hashlib
import json
import tarfile
from pathlib import Path

SOURCE_ROOTS = ("backend", "frontend", "scripts", "ops", "migrations", "VERSION", "requirements.txt")
EXCLUDED = {"node_modules", ".git", ".venv", "__pycache__", "dist", "work", "data", "uploads"}


def snapshot_source(root: Path, destination: Path) -> None:
    """Keep runtime migration definitions with the exact application source."""
    with tarfile.open(destination, "w") as archive:
        for name in SOURCE_ROOTS:
            source = root / name
            paths = sorted(source.rglob("*")) if source.is_dir() else [source]
            for path in paths:
                if path.is_file() and not EXCLUDED.intersection(path.relative_to(root).parts):
                    if path.is_symlink():
                        raise ValueError(f"Symlink source cannot enter offline bundle: {path}")
                    archive.add(path, arcname=path.relative_to(root), recursive=False)


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def inventory(root: Path) -> dict[str, str]:
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Unexpected symlink in bundle: {path}")
        if path.is_file() and path != root / "manifest.json":
            result[path.relative_to(root).as_posix()] = digest(path)
    return result


def make_manifest(root: Path, images: dict[str, str]) -> None:
    (root / "manifest.json").write_text(
        json.dumps({"schema": 1, "images": images, "files": inventory(root)}, indent=2) + "\n"
    )


def verify_manifest(root: Path) -> dict:
    manifest = json.loads((root / "manifest.json").read_text())
    if manifest["schema"] != 1 or manifest["files"] != inventory(root):
        raise ValueError("Offline bundle content does not match manifest; regenerate online")
    return manifest


def verify_source_locks(bundle: Path, checkout: Path) -> None:
    """An old dependency cache must not certify a checkout with newer locks."""
    with tarfile.open(bundle / "source.tar") as archive:
        for name in (
            "requirements.txt",
            "frontend/package.json",
            "frontend/package-lock.json",
            "VERSION",
            "ops/offline/Dockerfile",
        ):
            stream = archive.extractfile(name)
            if stream is None or stream.read() != (checkout / name).read_bytes():
                raise ValueError(f"Offline source snapshot is stale: {name}; regenerate online")


def verify_source_commit(bundle: Path, expected_commit: str) -> None:
    if json.loads((bundle / "source.json").read_text()).get("commit") != expected_commit:
        raise ValueError("Offline application commit differs from this checkout; regenerate source online")
