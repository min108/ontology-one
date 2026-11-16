"""Legacy changes cannot silently grow the exempt area or bypass strict typing."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.checks.check_legacy_shrink import check_tree


class LegacyShrinkTests(unittest.TestCase):
    def check_fixture(self, files: dict[str, str], retired: list[str] | None = None) -> list[str]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, content in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
            return check_tree(root, {"files": ["App.tsx"], "retired": retired or []})

    def test_original_file_passes(self) -> None:
        self.assertEqual(self.check_fixture({"legacy/App.tsx": ""}), [])

    def test_new_exempt_file_fails(self) -> None:
        self.assertTrue(self.check_fixture({"legacy/App.tsx": "", "legacy/new.ts": ""}))

    def test_replacing_file_without_growing_count_fails(self) -> None:
        self.assertTrue(self.check_fixture({"legacy/new.ts": ""}))

    def test_unreviewed_deletion_fails(self) -> None:
        self.assertTrue(self.check_fixture({}))

    def test_retired_file_must_be_removed(self) -> None:
        self.assertTrue(self.check_fixture({"legacy/App.tsx": ""}, ["App.tsx"]))
        self.assertEqual(self.check_fixture({}, ["App.tsx"]), [])

    def test_new_domain_cannot_import_legacy_source_relatively(self) -> None:
        self.assertTrue(self.check_fixture({"legacy/App.tsx": "", "app/Shell.tsx": "import App from '../legacy/App';"}))

    def test_declaration_alias_is_allowed(self) -> None:
        self.assertEqual(
            self.check_fixture({"legacy/App.tsx": "", "app/Shell.tsx": "import App from '@legacy/App';"}), []
        )

    def test_reused_main_path_never_inherits_an_old_lint_exemption(self) -> None:
        from scripts.checks import check_terms, check_tokens

        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            source = repository / "frontend/src"
            (source / "design").mkdir(parents=True)
            (source / "design/tokens.css").write_text("")
            (source / "main.tsx").write_text("const color='#112233'; const label='开始';")
            (repository / "frontend/eslint.config.js").write_text("const legacyFiles = ['src/main.tsx'];")
            for checker in (check_tokens, check_terms):
                with patch.object(checker, "ROOT", repository):
                    self.assertTrue(checker.check_tree(source))
