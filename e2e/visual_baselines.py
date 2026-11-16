"""Verify canonical visual artifacts and stage only individually reviewed baselines."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import stat
import struct
import zipfile
from pathlib import Path, PurePosixPath

BROWSER = "159.0.8154.6"
LAYOUTS = {"1366": (1366, 768, 1), "1920": (1920, 1080, 1), "1366-native125": (1366, 768, 1.25)}
PAGES = ("home", "shell-legacy", "security", "users", "orgs", "audit", "model", "backup", "system", "demo")
AUDIT = {
    "id": "11",
    "action": "demo_loaded",
    "resource": "business-demo-0.7",
    "tenant_id": "dept-01",
    "outcome": "success",
    "actor_username": "fixture.admin",
    "request_id": "fixture",
}


def states() -> dict[str, tuple[str, str]]:
    return {
        f"{layout}--{state}": (layout, state)
        for layout in LAYOUTS
        for state in (
            "login--unauthenticated",
            *[f"{mode}--{page}" for mode in ("simple", "professional") for page in PAGES],
        )
    }


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def extract_downloads(catalog: Path, destination: Path, commit: str) -> None:
    """Verify the archive digests copied from GitHub artifact metadata before extraction."""
    manifest = json.loads(catalog.read_text())
    run_id = str(manifest.get("run_id", ""))
    if manifest.get("capture_commit") != commit or not run_id.isdigit():
        raise ValueError("Download catalog must identify the exact capture commit and run")
    expected = {f"r1-visual-{layout}-{page}-{run_id}" for layout in LAYOUTS for page in ("login", *PAGES)}
    records = manifest.get("artifacts", [])
    if len(records) != 33 or {row.get("name") for row in records} != expected:
        raise ValueError("Download catalog must contain exactly the 33 expected visual artifacts")
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Extraction destination must be new or empty")
    checked = []
    for row in records:
        candidate = catalog.parent / row["file"]
        archive_path = candidate.resolve()
        digest = row.get("sha256", "").removeprefix("sha256:")
        if (
            not archive_path.is_relative_to(catalog.parent.resolve())
            or any(path.is_symlink() for path in (candidate, *candidate.parents))
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or sha256(archive_path) != digest
        ):
            raise ValueError("Downloaded artifact archive checksum/path differs from GitHub metadata")
        if archive_path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError("Downloaded visual archive exceeds 32 MiB")
        with zipfile.ZipFile(archive_path) as archive:
            if sum(info.file_size for info in archive.infolist()) > 30 * 1024 * 1024:
                raise ValueError("Extracted visual package exceeds 30 MiB")
            for info in archive.infolist():
                path = PurePosixPath(info.filename)
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or "\\" in info.filename
                    or stat.S_ISLNK(info.external_attr >> 16)
                ):
                    raise ValueError("Unsafe visual archive member")
        checked.append((row["name"], archive_path))
    destination.mkdir(parents=True, exist_ok=True)
    for name, archive_path in checked:
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(destination / name)
    (destination / "download-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


def validate_state(directory: Path, stem: str, commit: str, mode: str) -> dict:
    layout, _ = states()[stem]
    width, height, zoom = LAYOUTS[layout]
    metadata = json.loads((directory / "metrics.json").read_text())
    axe = json.loads((directory / "axe.json").read_text())
    kind = "baseline-capture-not-acceptance" if mode == "capture" else "acceptance"
    if (
        metadata.get("sourceCommit") != commit
        or metadata.get("browser") != BROWSER
        or metadata.get("resultKind") != kind
        or metadata.get("policy", {}).get("mode") != mode
    ):
        raise ValueError(f"{stem}: noncanonical, wrong-source or mislabeled visual evidence")
    if not isinstance(axe.get("violations"), list):
        raise ValueError(f"{stem}: missing accessibility scan")
    if any(item.get("impact") in {"serious", "critical"} for item in axe["violations"]):
        raise ValueError(f"{stem}: unresolved serious accessibility violation")
    png = (directory / "actual.png").read_bytes()
    if not png.startswith(b"\x89PNG\r\n\x1a\n") or len(png) < 24:
        raise ValueError(f"{stem}: invalid PNG")
    png_width, png_height = struct.unpack(">II", png[16:24])
    metrics = metadata["metrics"]
    if (
        metrics.get("devicePixelRatio") != zoom
        or metrics.get("visualScale") != 1
        or metrics.get("outerWidth") != width
        or abs(metrics.get("innerWidth", 0) - width / zoom) > 1
        or abs(metrics.get("innerHeight", 0) - height / zoom) > 1
        or (png_width, png_height) != (width, height)
        or metadata.get("screenshotMethod") != "cdp-physical-viewport"
        or metadata.get("screenshotScale") != "physical"
    ):
        raise ValueError(f"{stem}: native zoom/PNG dimensions differ")
    preferences = {} if zoom == 1 else {"partition": {"default_zoom_level": {"x": math.log(zoom) / math.log(1.2)}}}
    if zoom == 1:
        valid_preferences = metadata.get("preferences") == preferences
    else:
        level = metadata.get("preferences", {}).get("partition", {}).get("default_zoom_level", {})
        valid_preferences = (
            isinstance(level, dict)
            and abs(level.get("x", 0) - preferences["partition"]["default_zoom_level"]["x"]) < 1e-12
        )
    if not valid_preferences:
        raise ValueError(f"{stem}: missing native profile preference")
    if stem.endswith("--audit") and (
        metadata.get("auditState") != AUDIT
        or metadata.get("auditDetailsOpen") is not True
        or metadata.get("dynamicMasks") != []
    ):
        raise ValueError(
            f"{stem}: audit must show the exact real historical fixture with details, without masking its content"
        )
    return metadata


def verify_packages(source: Path, commit: str) -> dict[str, Path]:
    if not source.is_dir() or source.is_symlink():
        raise ValueError("Expected a real directory containing exactly 33 extracted visual packages")
    indexes = sorted(source.rglob("index.json"))
    if len(indexes) != 33:
        raise ValueError("Expected exactly 33 visual package indexes")
    observed: dict[str, Path] = {}
    for index in indexes:
        if any(parent.is_symlink() for parent in (index, *index.parents)):
            raise ValueError("Symlink artifact paths are forbidden")
        record = json.loads(index.read_text())
        if not record.get("completeArtifact"):
            raise ValueError("Missing complete artifact link")
        files = record.get("files", [])
        if record.get("bytes") != sum(item["bytes"] for item in files):
            raise ValueError("Package byte summary differs from its inventory")
        if sum(item["bytes"] for item in files) + index.stat().st_size > 30 * 1024 * 1024:
            raise ValueError("Visual artifact exceeds the 30 MiB download budget")
        members = set()
        for item in files:
            relative = PurePosixPath(item["path"])
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or len(relative.parts) != 4
                or relative.parts[0] != "visual"
            ):
                raise ValueError("Unexpected visual artifact path")
            if relative.name not in {"actual.png", "metrics.json", "axe.json"} or relative.as_posix() in members:
                raise ValueError("Unexpected or duplicated visual artifact member")
            members.add(relative.as_posix())
            path = index.parent.joinpath(*relative.parts)
            if any(parent.is_symlink() for parent in (path, *path.parents)) or not path.is_file():
                raise ValueError("Missing or symbolic visual artifact member")
            if path.stat().st_size != item["bytes"] or sha256(path) != item["sha256"]:
                raise ValueError("Visual artifact checksum mismatch")
            stem = f"{relative.parts[1]}--{relative.parts[2]}"
            if stem not in states():
                raise ValueError("Unexpected visual matrix state")
            if relative.name == "actual.png":
                if stem in observed:
                    raise ValueError("Duplicate visual state across packages")
                observed[stem] = path.parent
        for path in index.parent.rglob("*"):
            if path.is_file() and path != index and path.relative_to(index.parent).as_posix() not in members:
                raise ValueError("Unlisted artifact file")
    if set(observed) != set(states()):
        raise ValueError("Incomplete 63-image visual matrix")
    for stem, directory in observed.items():
        validate_state(directory, stem, commit, "capture")
    return observed


def review_manifest(images: dict[str, Path], commit: str) -> dict:
    return {
        "capture_commit": commit,
        "browser": BROWSER,
        "reviewer": "",
        "images": [
            {
                "name": stem,
                "sha256": sha256(directory / "actual.png"),
                "package_index_sha256": sha256(directory.parents[2] / "index.json"),
                "complete_artifact": json.loads((directory.parents[2] / "index.json").read_text())["completeArtifact"],
                "reviewed": False,
                "notes": "",
            }
            for stem, directory in sorted(images.items())
        ],
    }


def stage(images: dict[str, Path], review: dict, commit: str, destination: Path) -> None:
    if any(path.is_symlink() for path in (destination, *destination.parents)):
        raise ValueError("Symbolic baseline destinations are forbidden")
    expected = review_manifest(images, commit)
    if (
        review.get("capture_commit") != commit
        or review.get("browser") != BROWSER
        or not isinstance(review.get("reviewer"), str)
        or not review["reviewer"].strip()
    ):
        raise ValueError("Review must identify its capture commit, canonical browser and reviewer")
    rows = review.get("images", [])
    if len(rows) != 63 or {row.get("name") for row in rows} != set(images):
        raise ValueError("Review must cover every one of the 63 images exactly once")
    expected_rows = {row["name"]: row for row in expected["images"]}
    for row in rows:
        if (
            row.get("reviewed") is not True
            or not isinstance(row.get("notes"), str)
            or not row["notes"].strip()
            or any(
                row.get(key) != expected_rows[row["name"]][key]
                for key in ("sha256", "package_index_sha256", "complete_artifact")
            )
        ):
            raise ValueError("Every image needs an explicit review note and matching checksum")
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Baseline destination must be new or empty")
    destination.mkdir(parents=True, exist_ok=True)
    for stem, directory in sorted(images.items()):
        shutil.copyfile(directory / "actual.png", destination / f"{stem}.png")
        shutil.copyfile(directory / "metrics.json", destination / f"{stem}.json")
    (destination / "review.json").write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n")
    download_manifest = next(iter(images.values())).parents[3] / "download-manifest.json"
    if download_manifest.is_file():
        shutil.copyfile(download_manifest, destination / "download-manifest.json")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("extract", "verify", "review", "stage"))
    parser.add_argument("--source", type=Path)
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--capture-commit", required=True)
    parser.add_argument("--review", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.capture_commit):
        parser.error("A full capture commit SHA is required")
    if args.command == "extract":
        if not args.catalog or not args.output:
            parser.error("extract requires --catalog and --output")
        extract_downloads(args.catalog, args.output, args.capture_commit)
        args.source = args.output
    if not args.source:
        parser.error("Provide --source with the extracted visual packages")
    images = verify_packages(args.source, args.capture_commit)
    if args.command == "review":
        if not args.review or args.review.exists():
            parser.error("Provide a new --review file")
        args.review.parent.mkdir(parents=True, exist_ok=True)
        args.review.write_text(
            json.dumps(review_manifest(images, args.capture_commit), ensure_ascii=False, indent=2) + "\n"
        )
    if args.command == "stage":
        if not args.review or not args.output:
            parser.error("stage requires --review and --output")
        stage(images, json.loads(args.review.read_text()), args.capture_commit, args.output)
    print(
        json.dumps(
            {
                "verified_images": len(images),
                "verified_packages": 33,
                "capture_commit": args.capture_commit,
                "browser": BROWSER,
                "command": args.command,
                "final_acceptance": False,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
