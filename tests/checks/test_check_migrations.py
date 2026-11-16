from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.checks.check_migrations import check


class MigrationCheck(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for folder in ("backend", "migrations/control", "migrations/tenant", "tests/migrations"):
            (self.root / folder).mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_new_business_ddl_is_detected(self) -> None:
        (self.root / "backend" / "business.py").write_text("SQL = 'CREATE TABLE IF NOT EXISTS forbidden(id INT)'\n")
        self.assertTrue(any("建表" in error for error in check(self.root)))

    def test_sequence_gap_is_detected(self) -> None:
        (self.root / "migrations/tenant/0002_gap.py").write_text("def up(conn):\n    pass\n")
        self.assertTrue(any("连续" in error for error in check(self.root)))

    def test_missing_migration_test_is_detected(self) -> None:
        (self.root / "migrations/tenant/0001_first.py").write_text("def up(conn):\n    pass\n")
        self.assertTrue(any("测试" in error for error in check(self.root)))

    def test_real_repository_passes(self) -> None:
        self.assertEqual(check(Path(__file__).resolve().parents[2]), [])
