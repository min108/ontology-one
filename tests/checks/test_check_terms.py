"""Vocabulary generation and user-facing language are checked independently."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.checks.check_terms import check_tree, generated_contract_error, scan_text, validate_source
from scripts.gen_terms import ROOT, generated_files, read_source


class TermChecks(unittest.TestCase):
    def test_generated_files_match_single_source(self) -> None:
        for path, content in generated_files(read_source()).items():
            self.assertEqual((ROOT / path).read_text(encoding="utf-8"), content, path)

    def test_clean_copy(self) -> None:
        self.assertEqual(scan_text("<p>{terms.labels.search}</p>"), [])

    def test_brand_literal_is_rejected(self) -> None:
        self.assertTrue(scan_text("<h1>知元</h1>"))
        self.assertTrue(scan_text("const title = 'Ontology One';"))

    def test_old_brand_rejected(self) -> None:
        self.assertTrue(scan_text("<h1>集团本体管理平台</h1>"))

    def test_internal_terms_rejected(self) -> None:
        for word in ("Harness", "payload", "租户", "置信度", "候选集"):
            self.assertTrue(scan_text(f"<p>{word}</p>"), word)

    def test_program_identifiers_not_user_copy(self) -> None:
        self.assertEqual(scan_text("const payload = {kind: 'person', enabled: true};"), [])

    def test_hardcoded_status_rejected(self) -> None:
        self.assertTrue(scan_text("<Tag>已发布</Tag>"))

    def test_hardcoded_kind_rejected(self) -> None:
        self.assertTrue(scan_text("const label = '指标口径';"))

    def test_new_chinese_copy_requires_vocabulary(self) -> None:
        self.assertTrue(scan_text("<Button>查找内容</Button>"))

    def test_button_length(self) -> None:
        source = read_source()
        source["actions"]["too_long"] = "一二三四五六七八九"
        self.assertTrue(validate_source(source))

    def test_length_exception_requires_reason(self) -> None:
        source = read_source()
        source["actions"]["too_long"] = "一二三四五六七八九"
        source["length_exceptions"]["actions.too_long"] = ""
        self.assertTrue(validate_source(source))
        source["length_exceptions"]["actions.too_long"] = "官方动作名，保持业务含义完整。"
        self.assertFalse(validate_source(source))

    def test_source_is_clean(self) -> None:
        self.assertEqual(validate_source(read_source()), [])

    def test_new_path_not_hidden_by_legacy_exception(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ui").mkdir()
            (root / "ui" / "new.tsx").write_text("<p>Harness</p>", encoding="utf-8")
            self.assertTrue(check_tree(root))

    def test_template_copy_rejected(self) -> None:
        self.assertTrue(scan_text("const label = `请先选择内容`;"))

    def test_english_error_label_rejected(self) -> None:
        self.assertTrue(scan_text('<Input placeholder="payload" />'))

    def test_empty_status_is_rejected(self) -> None:
        source = read_source()
        source["statuses"]["task"]["draft"] = ""
        self.assertTrue(validate_source(source))

    def test_task_draft_has_business_name(self) -> None:
        self.assertEqual(read_source()["statuses"]["task"]["draft"], "待认领")

    def test_technical_filename_keeps_reasoned_exception(self) -> None:
        self.assertEqual(scan_text("const filename = 'Ontology One.exe';"), [])

    def test_dependency_manifest_matches_both_lock_and_package(self) -> None:
        import json

        package = json.loads((ROOT / "frontend/package.json").read_text())
        lock = json.loads((ROOT / "frontend/package-lock.json").read_text())
        manifest = json.loads((ROOT / "frontend/dependency-licenses.json").read_text())
        self.assertEqual(len(manifest["direct"]), len(package["dependencies"]) + len(package["devDependencies"]))
        for row in manifest["direct"]:
            self.assertEqual(row["version"], package[row["scope"]][row["name"]])
            self.assertEqual(row["version"], lock["packages"]["node_modules/" + row["name"]]["version"])
            self.assertTrue(row["license"])

    def test_english_jsx_expression_rejected(self) -> None:
        self.assertTrue(scan_text("<p>{'Harness'}</p>"))
        self.assertTrue(scan_text("<p>{`payload`}</p>"))

    def test_english_template_attribute_rejected(self) -> None:
        self.assertTrue(scan_text("<Input placeholder={`token`} />"))

    def test_derived_contract_must_match_generator_exactly(self) -> None:
        from scripts.gen_api_types import artifacts

        for filename, content in artifacts().items():
            relative = "api/generated/" + filename
            self.assertEqual(generated_contract_error(relative, content), [])
            self.assertTrue(generated_contract_error(relative, content + "// unexpected"))

    def test_generated_directory_does_not_hide_new_files(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "api/generated").mkdir(parents=True)
            (root / "api/generated/extra.tsx").write_text("<p>Harness</p>", encoding="utf-8")
            self.assertTrue(check_tree(root))
