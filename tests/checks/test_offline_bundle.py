"""Offline installation inputs are bound to their content and dependency locks."""

import tarfile
import tempfile
import unittest
from pathlib import Path

from ops.offline.bundle import make_manifest, verify_manifest, verify_source_locks


class OfflineBundleTests(unittest.TestCase):
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
