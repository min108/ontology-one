"""AB15 must independently inspect the body, evidence text and internal link of the downloaded Word file."""

import hashlib
import unittest

from backend.domains.artifacts import render
from e2e.inspect_report_export import inspect_docx
from tests.r2.test_artifacts import spec


class ExportReceipt(unittest.TestCase):
    def test_missing_body_link_or_correct_source_fails_its_own_check(self) -> None:
        original = spec()
        digest = hashlib.sha256(original["citations"]["c1"]["quote"].encode()).hexdigest()
        data = render.docx(original)
        result = inspect_docx(data, "30日", "c1", digest)
        self.assertEqual(result, {"body_deadline": True, "citation_link": True, "source_quote": True})
        original["blocks"] = [{"type": "paragraph", "text": "请按要求办理。", "citations": []}]
        only_citation_list = inspect_docx(render.docx(original), "30日", "c1", digest)
        self.assertEqual(only_citation_list, {"body_deadline": False, "citation_link": False, "source_quote": True})
        self.assertFalse(inspect_docx(data, "30日", "c1", "incorrect")["source_quote"])
