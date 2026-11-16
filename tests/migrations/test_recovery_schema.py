"""Recovery allowlists and retention stay independent of untrusted backup SQL."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.infra.backup import prune_upgrade_backups
from backend.infra.recovery_schema import normalized_ddl, trusted_schema, validate_schema
from scripts.restore_backup import schema_columns


class RecoverySchema(unittest.TestCase):
    def test_trusted_tenant_columns_match_current_application(self) -> None:
        columns = trusted_schema("tenant")["columns"]
        self.assertEqual(
            {table: frozenset(value) for table, value in columns.items() if table != "schema_migrations"},
            schema_columns(),
        )

    def test_normalization_does_not_change_quoted_defaults(self) -> None:
        original = "CREATE TABLE `x` (`value` text DEFAULT '') ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        malicious = original.replace("DEFAULT ''", "DEFAULT ' AUTO_INCREMENT=999 CHARACTER SET utf8mb4'")
        self.assertNotEqual(normalized_ddl(original), normalized_ddl(malicious))

    def test_control_requires_complete_immutable_audit_triggers(self) -> None:
        trusted = copy.deepcopy(trusted_schema("control"))
        trusted["tables"] = {table: [] for table in trusted["ddl"]}
        validate_schema(trusted, "control")
        trusted["triggers"].pop("sec_audit_no_update")
        with self.assertRaises(ValueError):
            validate_schema(trusted, "control")

    def test_retention_preserves_other_deployments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for number in range(5):
                path = root / f"upgrade-{number}"
                path.mkdir()
                (path / "manifest.json").write_text(
                    json.dumps({"control_database": "other_control" if number == 0 else "own_control"})
                )
            with patch("backend.infra.backup.verify_upgrade_backup") as verify:
                prune_upgrade_backups(root, "own_control")
            verify.assert_called_once_with(root / "upgrade-1")
            self.assertTrue((root / "upgrade-0").exists())
            self.assertFalse((root / "upgrade-1").exists())
            self.assertTrue(all((root / f"upgrade-{n}").exists() for n in (2, 3, 4)))
