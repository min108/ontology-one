"""R2 tables are additive, repeatable and present in trusted restoration."""

import os
import unittest

from backend.infra import backup, migrations, recovery_schema
from tests.migrations import test_integration as foundation


@unittest.skipUnless(os.getenv("RUN_JOBS_MYSQL_TESTS") == "1", "opt-in MySQL integration test")
class R2Migrations(unittest.TestCase):
    setUp = foundation.MigrationMySQL.setUp
    tearDown = foundation.MigrationMySQL.tearDown

    def test_r2_preserves_rows_and_restores_all_new_tables(self) -> None:
        names = foundation.MigrationMySQL.fixture(self)
        before = backup.snapshot_database(names["dept-01"])
        migrations.run_migrations()
        after = backup.snapshot_database(names["dept-01"])
        for name, digest in before["hashes"].items():
            self.assertEqual(after["hashes"][name], digest)
        self.assertEqual(len(after["columns"]["entities"]), 12)
        self.assertEqual(len(after["columns"]["relations"]), 7)
        self.assertTrue(
            {"changesets", "changeset_items", "recipe_runs", "artifacts", "artifact_versions", "artifact_access_log"}
            <= after["tables"].keys()
        )
        self.assertEqual(migrations.run_migrations()["migrated"], [])
        self.assertEqual(after["hashes"], backup.snapshot_database(names["dept-01"])["hashes"])
        recovery_schema.validate_schema(after, "tenant")
