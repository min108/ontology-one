"""Synthetic artifact contracts; these are not browser screenshots or acceptance evidence."""

import copy
import json
import math
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path

from e2e.visual_baselines import (
    AUDIT,
    BROWSER,
    LAYOUTS,
    extract_downloads,
    review_manifest,
    sha256,
    stage,
    states,
    validate_state,
    verify_packages,
)

COMMIT = "c" * 40


def packages(root: Path) -> dict[str, Path]:
    records: dict[str, list[dict]] = {}
    images = {}
    for stem, (layout, state) in states().items():
        page = "login" if state == "login--unauthenticated" else state.split("--", 1)[1]
        group = f"visual-{layout}-{page}"
        folder = root / group / "visual" / layout / state
        folder.mkdir(parents=True)
        width, height, zoom = LAYOUTS[layout]
        css_width, css_height = round(width / zoom), round(height / zoom)
        # Header-only fixture intentionally exercises file/metadata contracts, not image rendering.
        (folder / "actual.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 8 + struct.pack(">II", width, height))
        (folder / "axe.json").write_text(json.dumps({"violations": []}))
        (folder / "metrics.json").write_text(
            json.dumps(
                {
                    "sourceCommit": COMMIT,
                    "screenshotMethod": "cdp-physical-viewport",
                    "screenshotScale": "physical",
                    "browser": BROWSER,
                    "policy": {"mode": "capture"},
                    "resultKind": "baseline-capture-not-acceptance",
                    "metrics": {
                        "devicePixelRatio": zoom,
                        "visualScale": 1,
                        "outerWidth": width,
                        "innerWidth": css_width,
                        "innerHeight": css_height,
                    },
                    "preferences": {}
                    if zoom == 1
                    else {"partition": {"default_zoom_level": {"x": math.log(zoom) / math.log(1.2)}}},
                    **({"auditState": AUDIT, "auditDetailsOpen": True, "dynamicMasks": []} if page == "audit" else {}),
                }
            )
        )
        for name in ("actual.png", "axe.json", "metrics.json"):
            path = folder / name
            records.setdefault(group, []).append(
                {
                    "path": path.relative_to(root / group).as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": sha256(path),
                }
            )
        images[stem] = folder
    for group, files in records.items():
        (root / group / "index.json").write_text(
            json.dumps(
                {
                    "completeArtifact": "https://github.com/example/artifacts/1",
                    "bytes": sum(row["bytes"] for row in files),
                    "files": files,
                }
            )
        )
    return images


class VisualBaselineTests(unittest.TestCase):
    def test_cropped_css_png_and_false_screenshot_method_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            images = packages(Path(directory))
            stem = "1366-native125--simple--home"
            folder = images[stem]
            original = (folder / "actual.png").read_bytes()
            for width, height in ((1093, 614), (874, 491)):
                (folder / "actual.png").write_bytes(original[:16] + struct.pack(">II", width, height))
                with self.assertRaisesRegex(ValueError, "PNG dimensions"):
                    validate_state(folder, stem, COMMIT, "capture")
            (folder / "actual.png").write_bytes(original)
            metadata = json.loads((folder / "metrics.json").read_text())
            metadata["screenshotScale"] = "css"
            (folder / "metrics.json").write_text(json.dumps(metadata))
            with self.assertRaisesRegex(ValueError, "PNG dimensions"):
                validate_state(folder, stem, COMMIT, "capture")

    def test_download_catalog_checks_each_archive_against_the_reported_github_digest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            packages(root / "packages")
            download = root / "downloads"
            download.mkdir()
            entries = []
            for group in (root / "packages").iterdir():
                name = "r1-" + group.name + "-123"
                path = download / (name + ".zip")
                with zipfile.ZipFile(path, "w") as archive:
                    for child in group.rglob("*"):
                        if child.is_file():
                            archive.write(child, child.relative_to(group))
                entries.append({"name": name, "file": path.name, "sha256": sha256(path)})
            catalog = download / "artifacts.json"
            catalog.write_text(json.dumps({"capture_commit": COMMIT, "run_id": "123", "artifacts": entries}))
            extract_downloads(catalog, root / "extracted", COMMIT)
            self.assertEqual(len(verify_packages(root / "extracted", COMMIT)), 63)
            (download / entries[0]["file"]).write_bytes(b"altered download")
            with self.assertRaisesRegex(ValueError, "checksum"):
                extract_downloads(catalog, root / "again", COMMIT)
            with zipfile.ZipFile(download / entries[0]["file"], "w") as archive:
                archive.writestr("../escaped.json", "unsafe")
            entries[0]["sha256"] = sha256(download / entries[0]["file"])
            catalog.write_text(json.dumps({"capture_commit": COMMIT, "run_id": "123", "artifacts": entries}))
            with self.assertRaisesRegex(ValueError, "Unsafe"):
                extract_downloads(catalog, root / "unsafe", COMMIT)
            self.assertFalse((root / "escaped.json").exists())

    def test_complete_matrix_has_63_images_in_33_independent_packages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            packages(root)
            self.assertEqual(len(verify_packages(root, COMMIT)), 63)
            self.assertEqual(len(list(root.rglob("index.json"))), 33)

    def test_altered_download_or_wrong_commit_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            images = packages(root)
            with self.assertRaisesRegex(ValueError, "wrong-source"):
                verify_packages(root, "d" * 40)
            (next(iter(images.values())) / "actual.png").write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "checksum"):
                verify_packages(root, COMMIT)

    def test_partial_capture_and_unlisted_files_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            packages(root)
            index = next(root.rglob("index.json"))
            (index.parent / "auth-state.json").write_text("{}")
            with self.assertRaisesRegex(ValueError, "Unlisted"):
                verify_packages(root, COMMIT)
            (index.parent / "auth-state.json").unlink()
            index.unlink()
            with self.assertRaisesRegex(ValueError, "33"):
                verify_packages(root, COMMIT)

    def test_staging_requires_each_review_and_the_original_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            images = packages(root / "packages")
            review = review_manifest(images, COMMIT)
            review["reviewer"] = "Synthetic contract test, not a human visual approval"
            destination = root / "baselines"
            with self.assertRaisesRegex(ValueError, "explicit review"):
                stage(images, review, COMMIT, destination)
            for row in review["images"]:
                row.update(reviewed=True, notes="Synthetic contract fixture only")
            outside = root / "outside"
            outside.mkdir()
            linked = root / "linked"
            linked.symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "Symbolic"):
                stage(images, review, COMMIT, linked)
            wrong = copy.deepcopy(review)
            wrong["images"][0]["sha256"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "checksum"):
                stage(images, wrong, COMMIT, destination)
            stage(images, review, COMMIT, destination)
            self.assertEqual(len(list(destination.glob("*.png"))), 63)
            self.assertEqual(len(list(destination.glob("*.json"))), 64)

    def test_scalar_zoom_cannot_be_imported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            images = packages(root)
            folder = images["1366-native125--simple--home"]
            metrics = json.loads((folder / "metrics.json").read_text())
            metrics["preferences"] = {"partition": {"default_zoom_level": 1.25}}
            (folder / "metrics.json").write_text(json.dumps(metrics))
            # Re-signing a malformed local fixture does not bypass semantic verification.
            index = root / "visual-1366-native125-home" / "index.json"
            record = json.loads(index.read_text())
            for row in record["files"]:
                path = index.parent / row["path"]
                row.update(bytes=path.stat().st_size, sha256=sha256(path))
            record["bytes"] = sum(row["bytes"] for row in record["files"])
            index.write_text(json.dumps(record))
            with self.assertRaisesRegex(ValueError, "preference"):
                verify_packages(root, COMMIT)

    def test_serious_accessibility_issue_or_missing_scan_cannot_be_imported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            images = packages(Path(directory))
            stem = "1366--simple--home"
            for report in ({}, {"violations": [{"impact": "serious"}]}):
                (images[stem] / "axe.json").write_text(json.dumps(report))
                with self.subTest(report=report), self.assertRaisesRegex(ValueError, "accessibility"):
                    validate_state(images[stem], stem, COMMIT, "capture")
