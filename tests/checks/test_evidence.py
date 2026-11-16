"""Evidence bundles describe actual files, never a fabricated acceptance result."""

import json
import tempfile
import unittest
from pathlib import Path

from scripts.build_evidence import build_evidence


class EvidenceTests(unittest.TestCase):
    def test_manifest_hashes_actual_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "report.log"
            source.write_text("one test passed\n")
            result = build_evidence(root / "out", [source], metadata={"commit": "test"})
            manifest = json.loads((result / "manifest.json").read_text())
            self.assertEqual(len(manifest["files"]), 1)
            self.assertEqual(len(manifest["files"][0]["sha256"]), 64)
            self.assertEqual(manifest["metadata"]["commit"], "test")

    def test_missing_evidence_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(FileNotFoundError):
                build_evidence(root / "out", [root / "missing"], metadata={})

    def test_symlink_cannot_leak_external_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "leak").symlink_to("/etc/passwd")
            with self.assertRaises(ValueError):
                build_evidence(root / "out", [source], metadata={})

    def test_empty_evidence_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(ValueError):
            build_evidence(Path(directory) / "out", [], metadata={})

    def test_browser_authentication_state_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "report.json"
            source.write_text(
                json.dumps({"cookies": [{"name": "ontology_session", "value": "sensitive"}], "origins": []})
            )
            with self.assertRaises(ValueError):
                build_evidence(root / "out", [source], metadata={})

    def test_synthetic_session_fixture_is_not_blanket_banned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "session_fixture.json"
            source.write_text(json.dumps({"session_id": "synthetic-id", "expected_status": 401}))
            self.assertTrue(build_evidence(root / "out", [source], metadata={}).is_dir())
