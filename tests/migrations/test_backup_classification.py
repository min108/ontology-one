from __future__ import annotations

import unittest

from backend import system
from scripts.restore_backup import schema_columns


class BackupClassification(unittest.TestCase):
    def test_every_application_table_classified(self) -> None:
        self.assertEqual(
            set(schema_columns(include_rebuildable=True)) | {"schema_migrations"}, set(system.TABLE_CLASSIFICATION)
        )

    def test_migration_history_is_not_exported(self) -> None:
        self.assertNotIn("schema_migrations", system.EXPORTED_TABLES)

    def test_demo_receipt_is_exported(self) -> None:
        self.assertIn("demo_installations", system.EXPORTED_TABLES)

    def test_restore_allowlist_covers_exports(self) -> None:
        self.assertEqual(set(system.EXPORTED_TABLES), set(schema_columns()))

    def test_original_column_contract(self) -> None:
        self.assertEqual(len(schema_columns()["entities"]), 12)
        self.assertEqual(len(schema_columns()["relations"]), 7)
