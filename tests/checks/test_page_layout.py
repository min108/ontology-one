"""Executable positive and negative examples of the shared-page boundary."""

import unittest

from scripts.checks.check_page_layout import scan_css, scan_page
from scripts.checks.check_tokens import scan_text


class PageLayoutChecks(unittest.TestCase):
    def test_routed_page_requires_both_components(self) -> None:
        self.assertEqual(scan_page("<PageLayout header={<PageHeader title={copy.title} />} />"), [])
        self.assertTrue(scan_page("<section><h1>Custom title</h1></section>"))
        self.assertTrue(scan_page("<PageLayout header={<h1>Custom title</h1>} />"))
        self.assertTrue(scan_page("<PageLayout style={{padding: 24}} header={<PageHeader />} />"))

    def test_page_root_sizing_is_rejected_even_with_tokens(self) -> None:
        for rule in ("padding: var(--zy-space-6)", "max-width: var(--zy-page-list-max)", "margin: 0 auto"):
            with self.subTest(rule=rule):
                self.assertTrue(scan_css(".page { " + rule + "; }"))

    def test_nested_cards_and_form_controls_can_have_padding(self) -> None:
        self.assertEqual(scan_css(".card {padding: var(--zy-space-4)} .page .field {max-width: 100%}"), [])
        self.assertEqual(scan_css(".page {display: grid; gap: var(--zy-gap); min-width: 0}"), [])

    def test_all_dimensions_use_tokens(self) -> None:
        for css in (".card {padding: 24px}", ".field {width: 280px}", ".grid {gap: 8px}"):
            with self.subTest(css=css):
                self.assertTrue(scan_text(css))
        self.assertEqual(scan_text(".card {padding: var(--zy-space-6); width: 100%}"), [])

    def test_media_conditions_are_the_only_pixel_exception(self) -> None:
        self.assertEqual(scan_text("@media (width < 1440px) {.card {padding: var(--zy-space-6)}}"), [])
        self.assertEqual(scan_text("window.matchMedia('(max-width: 1439px)')"), [])
        self.assertTrue(scan_text("@media (width < 1440px) {.card {padding: 24px}}"))

    def test_public_presentation_assets_are_rejected(self) -> None:
        self.assertTrue(scan_text(".x { background-image: url(https://example.com/a.png) }"))
        self.assertTrue(scan_text('<img src="//example.com/a.png" />'))
        self.assertEqual(scan_text('<img src="/brand/mark.svg" />'), [])
        self.assertEqual(scan_text('<a href="https://github.com/min108/ontology-one/releases">Release</a>'), [])
