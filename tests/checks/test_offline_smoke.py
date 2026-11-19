"""Offline startup evidence must include migrated tenant and control schemas."""

import unittest
from unittest.mock import patch

from ops.offline.container_smoke import migrated_schema_inventory, require_migrated_schema

TABLES = ("entity_meta", "ont_modules", "ont_module_deps", "ont_releases", "ont_release_items")
EXPECTATIONS = (("control", 6, ("job_schedule_state",)), ("tenant", 5, TABLES))


class OfflineSchemaTests(unittest.TestCase):
    def test_current_control_and_tenant_tables_pass(self) -> None:
        for kind, version, tables in EXPECTATIONS:
            with self.subTest(kind=kind):
                self.assertEqual(
                    require_migrated_schema(kind, {"current": version, "pending": [], "tables": tables}),
                    {"version": version, "required_table": tables[0], "required_tables": list(tables)},
                )

    def test_recorded_migration_without_physical_table_fails(self) -> None:
        for kind, version, tables in EXPECTATIONS:
            for missing in tables:
                present = {"schema_migrations", *tables} - {missing}
                with self.subTest(kind=kind, missing=missing), self.assertRaisesRegex(RuntimeError, missing):
                    require_migrated_schema(kind, {"current": version, "pending": [], "tables": present})

    def test_physical_table_without_current_migration_fails(self) -> None:
        for kind, version, tables in EXPECTATIONS:
            for current in (0, version - 1, version + 1):
                with self.subTest(kind=kind, current=current), self.assertRaisesRegex(RuntimeError, "migration"):
                    require_migrated_schema(kind, {"current": current, "pending": [], "tables": tables})

    def test_pending_migration_cannot_certify_offline_install(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "migration"):
            require_migrated_schema("tenant", {"current": 5, "pending": ["0006_future"], "tables": TABLES})

    def test_empty_directory_cannot_pass_without_tenant_checks(self) -> None:
        with (
            patch("backend.security.store.list_tenants", return_value=[]),
            self.assertRaisesRegex(RuntimeError, "tenant directory is empty"),
        ):
            migrated_schema_inventory()

    def test_every_directory_tenant_must_have_the_new_table(self) -> None:
        directory = [{"id": "first", "db_name": "first_db"}, {"id": "second", "db_name": "second_db"}]
        statuses = {
            "control_db": {"current": 6, "pending": [], "tables": ["job_schedule_state"]},
            "first_db": {"current": 5, "pending": [], "tables": TABLES},
            "second_db": {"current": 5, "pending": [], "tables": []},
        }
        with (
            patch("backend.security.store._config", return_value={"database": "control_db"}),
            patch("backend.security.store.list_tenants", return_value=directory),
            patch("backend.infra.migrations.migration_status", side_effect=lambda kind, name: statuses[name]),
            self.assertRaisesRegex(RuntimeError, "entity_meta"),
        ):
            migrated_schema_inventory()
