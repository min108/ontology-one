import { test } from "node:test";
import assert from "node:assert/strict";
import {
  assertLegacyCard,
  assertLegacyHighlight,
} from "./r4-legacy-contracts.mjs";

test("AA2 card assertion rejects a missing sentence, wrong record and offscreen card", () => {
  const expected = { id: "old-1", title: "原记录", status: "published" };
  const detail = { entity: expected, card: { one_liner: "应当核实原件。" } };
  const rendered = {
    title: expected.title,
    one_liner: detail.card.one_liner,
    visible: true,
  };
  assertLegacyCard(expected, detail, rendered);
  assert.throws(() =>
    assertLegacyCard(expected, detail, { ...rendered, one_liner: "" }),
  );
  assert.throws(() =>
    assertLegacyCard(
      expected,
      { ...detail, entity: { ...expected, id: "other" } },
      rendered,
    ),
  );
  assert.throws(() =>
    assertLegacyCard(expected, detail, { ...rendered, visible: false }),
  );
});
test("AA2 source assertion rejects removed highlighting and an incorrect original location", () => {
  const expected = {
    source_version: "original-1",
    start: 1,
    end: 3,
    quote: "原文",
  };
  const response = {
    source_version: expected.source_version,
    text: "据原文核对",
    focus: expected,
  };
  const rendered = { mark: "原文", unmatched: false };
  assertLegacyHighlight(expected, response, rendered);
  assert.throws(() =>
    assertLegacyHighlight(expected, response, { ...rendered, mark: "" }),
  );
  assert.throws(() =>
    assertLegacyHighlight(
      expected,
      { ...response, focus: { ...expected, start: 0 } },
      rendered,
    ),
  );
});
