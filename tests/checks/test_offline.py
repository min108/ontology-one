"""Public dependencies must fail in source and compiled output."""

import tempfile
import unittest
from pathlib import Path

from scripts.checks.check_offline import scan_text, scan_tree


class OfflineTests(unittest.TestCase):
    def test_cdn_script_fails(self) -> None:
        self.assertTrue(scan_text('<script src="https://cdn.example.com/a.js"></script>', "frontend/index.html"))

    def test_css_font_fails(self) -> None:
        self.assertTrue(scan_text("@font-face{src:url(//fonts.example.com/a.woff2)}", "frontend/src/a.css"))

    def test_runtime_request_fails(self) -> None:
        self.assertTrue(scan_text('fetch("https://telemetry.example.com/event")', "frontend/src/a.ts"))

    def test_websocket_and_uppercase_scheme_fail(self) -> None:
        for url in ("wss://telemetry.example.com/events", "HTTPS://cdn.example.com/a.js"):
            self.assertTrue(scan_text(f'new WebSocket("{url}")', "frontend/dist/a.js"))

    def test_encoded_url_fails(self) -> None:
        self.assertTrue(scan_text(r'fetch("https:\/\/cdn.example.com/a")', "frontend/dist/a.js"))

    def test_unicode_escaped_url_fails(self) -> None:
        self.assertTrue(scan_text(r'fetch("\u0068ttps://cdn.example.com/a")', "frontend/dist/a.js"))

    def test_url_userinfo_cannot_disguise_public_host(self) -> None:
        self.assertTrue(scan_text('fetch("https://127.0.0.1@evil.example/a")', "frontend/dist/a.js"))

    def test_loopback_and_private_addresses_pass(self) -> None:
        self.assertEqual(scan_text("http://127.0.0.1:8765 http://10.1.2.3/a", "backend/a.py"), [])

    def test_svg_namespace_passes(self) -> None:
        self.assertEqual(scan_text('<svg xmlns="http://www.w3.org/2000/svg">', "frontend/dist/a.js"), [])

    def test_release_hyperlink_only_passes_at_registered_location(self) -> None:
        text = '<a href="https://github.com/min108/ontology-one/releases">发行包</a>'
        self.assertEqual(scan_text(text, "frontend/src/pages/Settings.tsx"), [])
        self.assertTrue(scan_text(text, "frontend/src/new.tsx"))

    def test_release_link_cannot_be_used_for_fetch(self) -> None:
        text = 'fetch("https://github.com/min108/ontology-one/releases")'
        self.assertTrue(scan_text(text, "frontend/src/pages/Settings.tsx"))

    def test_model_address_exception_is_location_scoped(self) -> None:
        self.assertTrue(scan_text('fetch("https://api.deepseek.com/anthropic")', "frontend/src/new.ts"))

    def test_vendor_diagnostic_url_is_not_a_request_exception(self) -> None:
        for url in (
            "https://react.dev/errors/",
            "https://reactrouter.com/en/main/routers/picking-a-router.",
            "https://github.com/ungap/url-search-params.",
            "https://reactflow.dev?utm_source=attribution",
        ):
            self.assertTrue(scan_text(f'fetch("{url}")', "frontend/dist/a.js"))

    def test_javascript_slash_regex_is_not_a_host(self) -> None:
        self.assertEqual(scan_text(r'value.replace(/\//g,"%2F")', "frontend/dist/a.js"), [])

    def test_compiled_output_is_scanned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "frontend/dist/a.js"
            output.parent.mkdir(parents=True)
            output.write_text('fetch("https://evil.example/a")')
            self.assertTrue(scan_tree(root, require_dist=True))

    def test_missing_build_is_not_a_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertTrue(scan_tree(Path(directory), require_dist=True))
