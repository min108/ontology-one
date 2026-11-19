"""BA1 verifies institution provisioning and exact historical rows on MySQL."""

import hashlib
import os
import unittest

from backend.infra import backup, migrations
from backend.security import store
from tests.migrations import test_integration as foundation

LEGACY = ("entities", "relations", "versions", "activities")
ADDITIONS = {"ont_modules", "ont_module_deps", "ont_releases", "ont_release_items"}


def row_hashes(snapshot: dict, table: str) -> list[str]:
    return sorted(hashlib.sha256(backup.canonical(row).encode()).hexdigest() for row in snapshot["tables"][table])


@unittest.skipUnless(os.getenv("RUN_JOBS_MYSQL_TESTS") == "1", "opt-in MySQL integration test")
class InstitutionMigration(unittest.TestCase):
    setUp = foundation.MigrationMySQL.setUp
    tearDown = foundation.MigrationMySQL.tearDown
    fixture = foundation.MigrationMySQL.fixture

    def test_upgrade_preserves_all_four_legacy_tables_and_provisions_each_institution(self) -> None:
        names = self.fixture()
        before = {ident: backup.snapshot_database(name) for ident, name in names.items() if ident != "control"}
        migrations.run_migrations()
        for ident, old in before.items():
            with self.subTest(tenant=ident):
                current = backup.snapshot_database(names[ident])
                self.assertEqual(ADDITIONS & old["tables"].keys(), set())
                self.assertLessEqual(ADDITIONS, current["tables"].keys())
                self.assertEqual(len(current["columns"]["entities"]), 12)
                self.assertEqual(len(current["columns"]["relations"]), 7)
                for table in LEGACY:
                    self.assertEqual(len(current["tables"][table]), len(old["tables"][table]))
                    self.assertEqual(row_hashes(current, table), row_hashes(old, table))
                self.assertEqual(
                    [
                        (r["id"], r["module_key"], r["module_type"], r["name"], r["domain_code"])
                        for r in current["tables"]["ont_modules"]
                    ],
                    [("MOD-DEFAULT", "default", "org_common", store.get_tenant(ident)["name"] + "公共部分", "general")],
                )
        self.assertTrue(all(before["dept-01"]["tables"][table] for table in LEGACY))
