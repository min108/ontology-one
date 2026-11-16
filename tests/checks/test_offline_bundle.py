"""Offline installation inputs are bound to their content and dependency locks."""

import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from ops.offline.bundle import (
    make_manifest,
    snapshot_source,
    verify_manifest,
    verify_source_commit,
    verify_source_locks,
)


class OfflineBundleTests(unittest.TestCase):
    def test_old_application_source_cannot_certify_new_commit_with_unchanged_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "source.json").write_text(json.dumps({"commit": "a" * 40}))
            verify_source_commit(root, "a" * 40)
            with self.assertRaisesRegex(ValueError, "commit"):
                verify_source_commit(root, "b" * 40)

    def test_source_snapshot_contains_all_migrations_and_excludes_private_work(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in (
                "migrations/control/0002_identity.py",
                "migrations/tenant/0005_preferences.py",
                "migrations/restore/v070.json",
                "frontend/package-lock.json",
                "frontend/node_modules/private.txt",
                "frontend/work/auth.json",
            ):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(name)
            snapshot_source(root, root / "source.tar")
            with tarfile.open(root / "source.tar") as archive:
                names = set(archive.getnames())
                self.assertIn("migrations/control/0002_identity.py", names)
                self.assertIn("migrations/tenant/0005_preferences.py", names)
                self.assertIn("migrations/restore/v070.json", names)
                self.assertNotIn("frontend/node_modules/private.txt", names)
                self.assertNotIn("frontend/work/auth.json", names)

    def test_modified_dependency_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "dependency.whl").write_bytes(b"wheel")
            make_manifest(root, {"runner": "sha256:test"})
            (root / "dependency.whl").write_bytes(b"changed")
            with self.assertRaises(ValueError):
                verify_manifest(root)

    def test_missing_dependency_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "dependency.whl").write_bytes(b"wheel")
            make_manifest(root, {})
            (root / "dependency.whl").unlink()
            with self.assertRaises(ValueError):
                verify_manifest(root)

    def test_unlisted_dependency_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "dependency.whl").write_bytes(b"wheel")
            make_manifest(root, {})
            (root / "extra.whl").write_bytes(b"wheel")
            with self.assertRaises(ValueError):
                verify_manifest(root)

    def test_valid_bundle_verifies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "dependency.whl").write_bytes(b"wheel")
            make_manifest(root, {"runner": "sha256:test"})
            self.assertEqual(verify_manifest(root)["images"]["runner"], "sha256:test")

    def test_new_lock_requires_new_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "requirements.txt"
            source.write_text("original")
            with tarfile.open(root / "source.tar", "w") as archive:
                archive.add(source, arcname="requirements.txt")
            source.write_text("new dependency")
            with self.assertRaises(ValueError):
                verify_source_locks(root, root)
