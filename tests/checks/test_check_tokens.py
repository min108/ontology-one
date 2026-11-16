"""Design tokens reject literals outside the design boundary."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.checks.check_tokens import check_tree, scan_text


class TokenChecks(unittest.TestCase):
    def test_clean_token_css(self) -> None:
        self.assertEqual(scan_text(".x { color: var(--zy-gray-900); font-size: var(--zy-font-body); }"), [])

    def test_hex_color(self) -> None:
        self.assertTrue(scan_text(".x { color: #123456; }"))

    def test_function_color(self) -> None:
        self.assertTrue(scan_text(".x { background: rgb(1 2 3); }"))
        self.assertTrue(scan_text(".x { color: hsl(20 30% 40%); }"))

    def test_pixel_font(self) -> None:
        self.assertTrue(scan_text(".x { font-size: 15px; }"))

    def test_small_inline_font(self) -> None:
        self.assertTrue(scan_text("<p style={{fontSize: 11}}>x</p>"))

    def test_literal_radius_and_shadow(self) -> None:
        self.assertTrue(scan_text(".x { border-radius: 6px; box-shadow: 0 1px 2px black; }"))

    def test_zero_border_radius_is_still_outside_tokens(self) -> None:
        self.assertTrue(scan_text(".x { border-radius: 0; }"))

    def test_inline_color(self) -> None:
        self.assertTrue(scan_text("<div style={{color: 'red'}} />"))

    def test_design_and_legacy_only_are_excluded(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for folder in ("design", "legacy", "ui"):
                (root / folder).mkdir()
                (root / folder / "bad.css").write_text(".x { color: #123456; }", encoding="utf-8")
            errors = check_tree(root)
        self.assertEqual(len(errors), 1)
        self.assertIn("ui/bad.css", errors[0])

    def test_unknown_token_rejected(self) -> None:
        self.assertTrue(scan_text(".x { color: var(--other-brand); }"))

    def test_invented_design_token_rejected(self) -> None:
        self.assertTrue(scan_text(".x { color: var(--zy-invented); }"))

    def test_named_background_rejected(self) -> None:
        self.assertTrue(scan_text(".x { background: red; }"))

    def test_corner_radius_rejected(self) -> None:
        self.assertTrue(scan_text(".x { border-top-left-radius: 4px; }"))

    def test_rem_font_rejected(self) -> None:
        self.assertTrue(scan_text(".x { font-size: 0.5rem; }"))

    def test_relative_font_shorthand_rejected(self) -> None:
        self.assertTrue(scan_text(".x { font: 0.5rem sans-serif; }"))

    def test_mixed_token_and_radius_literal_rejected(self) -> None:
        self.assertTrue(scan_text(".x { border-radius: var(--zy-radius-card) 999px; }"))

    def test_camel_case_background_and_corner_rejected(self) -> None:
        self.assertTrue(scan_text("<div style={{backgroundColor:'red',borderTopLeftRadius:123}}/>"))

    def test_logical_corner_radius_rejected(self) -> None:
        self.assertTrue(scan_text(".x { border-start-start-radius: 9px; }"))

    def test_border_named_color_rejected(self) -> None:
        self.assertTrue(scan_text(".x { border-left: 1px solid red; }"))
