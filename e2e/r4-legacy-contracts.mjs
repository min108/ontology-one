import assert from "node:assert/strict";

export function assertLegacyCard(expected, detail, rendered) {
  assert.equal(
    detail.entity.id,
    expected.id,
    "the requested stored record must be displayed",
  );
  assert.equal(detail.entity.title, expected.title);
  assert.equal(
    detail.entity.status,
    expected.status,
    "reading must preserve the old status",
  );
  assert.equal(rendered.title, expected.title);
  assert.ok(
    detail.card.one_liner.trim(),
    "a business card needs a nonempty business sentence",
  );
  assert.equal(
    rendered.one_liner,
    detail.card.one_liner,
    "the first card sentence must match stored content",
  );
  assert.ok(
    rendered.visible,
    "the card sentence must appear in the first viewport",
  );
}

export function assertLegacyHighlight(expected, response, rendered) {
  assert.ok(
    expected.quote.trim(),
    "the expected quote must contain original text",
  );
  assert.equal(response.source_version, expected.source_version);
  assert.equal(response.focus.start, expected.start);
  assert.equal(response.focus.end, expected.end);
  assert.equal(response.focus.quote, expected.quote);
  assert.equal(
    Array.from(response.text).slice(expected.start, expected.end).join(""),
    expected.quote,
  );
  assert.equal(
    rendered.mark,
    expected.quote,
    "the original excerpt must be highlighted, not only echoed",
  );
  assert.equal(
    rendered.unmatched,
    false,
    "the original locator must resolve exactly",
  );
}
