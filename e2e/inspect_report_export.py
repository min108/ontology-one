"""Inspect the downloaded Word body and its particular source bookmark without retaining the document."""

from __future__ import annotations

import hashlib
import io
import json
import sys

from docx import Document
from docx.oxml.ns import qn


def inspect_docx(content: bytes, deadline: str, citation: str, quote_digest: str) -> dict[str, bool]:
    document = Document(io.BytesIO(content))
    paragraphs = document.paragraphs
    appendix = next((i for i, p in enumerate(paragraphs) if p.text == "依据清单"), len(paragraphs))
    body = [p.text for p in paragraphs[:appendix]]
    body.extend(cell.text for table in document.tables for row in table.rows for cell in row.cells)
    linked = any(
        link.get(qn("w:anchor")) == citation for p in paragraphs[:appendix] for link in p._p.xpath(".//w:hyperlink")
    )
    quote_matches = any(
        mark.get(qn("w:name")) == citation
        and hashlib.sha256("".join(paragraphs[i + 1].text.split()).encode()).hexdigest() == quote_digest
        for i in range(appendix, len(paragraphs) - 1)
        for mark in paragraphs[i]._p.xpath(".//w:bookmarkStart")
    )
    return {
        "body_deadline": deadline in "".join("".join(body).split()),
        "citation_link": linked,
        "source_quote": quote_matches,
    }


if __name__ == "__main__":
    print(json.dumps(inspect_docx(sys.stdin.buffer.read(), *sys.argv[1:])))
