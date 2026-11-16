"""New admin URL exemptions stay exact and cannot become request exemptions."""

import unittest

from scripts.checks.check_offline import scan_text


class AdminOfflineTests(unittest.TestCase):
    def test_model_default_only_at_exact_model_page(self) -> None:
        content = "const endpoint = 'https://api.deepseek.com/anthropic';"
        self.assertEqual(scan_text(content, "frontend/src/features/admin/model/ModelPage.tsx"), [])
        self.assertTrue(scan_text(content, "frontend/src/features/admin/system/SystemPage.tsx"))
        self.assertTrue(
            scan_text('fetch("https://api.deepseek.com/anthropic")', "frontend/src/features/admin/model/ModelPage.tsx")
        )

    def test_release_only_user_clicked_at_exact_backup_page(self) -> None:
        content = '<a href="https://github.com/min108/ontology-one/releases">Release</a>'
        self.assertEqual(scan_text(content, "frontend/src/features/admin/backup/BackupPage.tsx"), [])
        self.assertTrue(scan_text(content, "frontend/src/features/admin/model/ModelPage.tsx"))
        self.assertTrue(
            scan_text(
                'fetch("https://github.com/min108/ontology-one/releases")',
                "frontend/src/features/admin/backup/BackupPage.tsx",
            )
        )
