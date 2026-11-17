"""Discovery contracts and real-MySQL migration execution."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.infra import migrations


class Discovery(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "tenant").mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write(self, name: str, body: str = "def up(conn):\n    pass\n") -> None:
        (self.root / "tenant" / name).write_text(body)

    def test_sorted_contiguous_sequence(self) -> None:
        self.write("0002_second.py")
        self.write("0001_first.py")
        self.assertEqual([m.version for m in migrations.discover("tenant", self.root)], [1, 2])

    def test_empty_sequence_cannot_bypass_version_gate(self) -> None:
        with self.assertRaises(migrations.MigrationError):
            migrations.discover("tenant", self.root)

    def test_sequence_gap_rejected(self) -> None:
        self.write("0002_second.py")
        with self.assertRaises(migrations.MigrationError):
            migrations.discover("tenant", self.root)

    def test_duplicate_rejected(self) -> None:
        self.write("0001_first.py")
        self.write("0001_other.py")
        with self.assertRaises(migrations.MigrationError):
            migrations.discover("tenant", self.root)

    def test_checksum_covers_source_bytes(self) -> None:
        self.write("0001_first.py")
        first = migrations.discover("tenant", self.root)[0]
        self.write("0001_first.py", "def up(conn):\n    pass  # changed\n")
        self.assertNotEqual(first.checksum, migrations.discover("tenant", self.root)[0].checksum)

    def test_execution_uses_exact_checksummed_bytes(self) -> None:
        self.write("0001_first.py", "def up(conn):\n    return 1\n")
        first = migrations.discover("tenant", self.root)[0]
        self.assertEqual(first.up(None), 1)
        self.write("0001_first.py", "def up(conn):\n    return 2\n")
        self.assertEqual(migrations.discover("tenant", self.root)[0].up(None), 2)

    def test_discovery_does_not_execute_migration_module(self) -> None:
        self.write("0001_first.py", "raise RuntimeError('module executed')\ndef up(conn):\n    pass\n")
        migration = migrations.discover("tenant", self.root)[0]
        with self.assertRaises(RuntimeError):
            migration.up(None)

    def test_invalid_name_rejected(self) -> None:
        self.write("1_bad.py")
        with self.assertRaises(migrations.MigrationError):
            migrations.discover("tenant", self.root)

    def test_unknown_sequence_rejected(self) -> None:
        with self.assertRaises(migrations.MigrationError):
            migrations.discover("other", self.root)

    def test_missing_up_rejected(self) -> None:
        self.write("0001_first.py", "value = 1\n")
        with self.assertRaises(migrations.MigrationError):
            migrations.discover("tenant", self.root)

    def test_applied_checksum_rejected(self) -> None:
        self.write("0001_first.py")
        sequence = migrations.discover("tenant", self.root)
        with self.assertRaises(migrations.MigrationError):
            migrations.pending(sequence, [{"version": 1, "name": "0001_first", "checksum": "0" * 64}])

    def test_unknown_applied_version_rejected(self) -> None:
        with self.assertRaises(migrations.MigrationError):
            migrations.pending([], [{"version": 1, "name": "0001_first", "checksum": "0" * 64}])

    def test_matching_version_is_noop(self) -> None:
        self.write("0001_first.py")
        sequence = migrations.discover("tenant", self.root)
        first = sequence[0]
        self.assertEqual(
            migrations.pending(sequence, [{"version": 1, "name": first.name, "checksum": first.checksum}]), []
        )

    def test_gap_in_applied_history_rejected(self) -> None:
        self.write("0001_first.py")
        self.write("0002_second.py")
        sequence = migrations.discover("tenant", self.root)
        last = sequence[1]
        with self.assertRaises(migrations.MigrationError):
            migrations.pending(sequence, [{"version": 2, "name": last.name, "checksum": last.checksum}])

    def test_failure_state_survives_new_reader(self) -> None:
        with patch.dict("os.environ", {"ONTOLOGY_STORAGE_ROOT": str(self.root / "tenants")}):
            migrations.write_state({"status": "failed", "database": "synthetic"})
            self.assertEqual(migrations.read_state()["status"], "failed")
            with self.assertRaises(migrations.MigrationError):
                migrations.require_healthy_state()

    def test_abrupt_process_exit_leaves_startup_blocked(self) -> None:
        environment = {**os.environ, "ONTOLOGY_STORAGE_ROOT": str(self.root / "tenants")}
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import os; from backend.infra.migrations import write_state; "
                "write_state({'status':'in_progress','database':'synthetic'}); os._exit(23)",
            ],
            env=environment,
            check=False,
        )
        self.assertEqual(result.returncode, 23)
        with patch.dict(os.environ, environment), self.assertRaises(migrations.MigrationError):
            migrations.require_healthy_state()

    def test_corrupt_state_fails_closed(self) -> None:
        with patch.dict("os.environ", {"ONTOLOGY_STORAGE_ROOT": str(self.root / "tenants")}):
            migrations.state_path().write_text("not json")
            with self.assertRaises(migrations.MigrationError):
                migrations.require_healthy_state()

    def test_baseline_sequences_have_tests(self) -> None:
        for kind in ("control", "tenant"):
            self.assertEqual(
                [m.version for m in migrations.discover(kind)],
                [1, 2, 3, 4, 5, 6] if kind == "control" else [1, 2, 3, 4],
            )
