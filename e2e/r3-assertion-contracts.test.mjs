import test from "node:test";
import assert from "node:assert/strict";
import {
  assertDatasetCardsLayout,
  assertRecipeTrayStage,
  assertReturnedReason,
  assertNoZeroHomeMetrics,
  assertRestrictionLabels,
  assertOneTitleLine,
} from "./r3-assertion-contracts.mjs";
import { leakedText } from "./r3-structure.mjs";

test("A2 counterexample: a task title cannot stand in for a missing stage", () => {
  assertRecipeTrayStage("正在提炼制度中的要求");
  assert.throws(() => assertRecipeTrayStage("制度整理"), /A2/);
  assert.throws(() => assertRecipeTrayStage(""), /A2/);
});
test("A8 counterexample: returned status and a local toast cannot replace the saved reason", () => {
  const entity = {
    id: "item",
    status: "returned",
    activities: [{ action: "退回补充", detail: "请核对原文。" }],
  };
  assertReturnedReason(entity, "item", "请核对原文。");
  assert.throws(
    () =>
      assertReturnedReason(
        { ...entity, activities: [{ action: "退回补充", detail: "" }] },
        "item",
        "请核对原文。",
      ),
    /A8/,
  );
});
test("A1 counterexamples: missing workbench and visible zero both fail", () => {
  assertNoZeroHomeMetrics({ roots: 1, metrics: [] });
  assertNoZeroHomeMetrics({ roots: 1, metrics: [3] });
  assert.throws(() => assertNoZeroHomeMetrics({ roots: 0, metrics: [] }), /A1/);
  assert.throws(
    () => assertNoZeroHomeMetrics({ roots: 1, metrics: [0] }),
    /A1/,
  );
});
for (const label of ["只读", "仅审核", "跨机构查看"])
  test(`U3 counterexample: ${label} hidden until a popover opens fails`, () => {
    assertRestrictionLabels({ popovers: 0, labels: [label] }, [label]);
    assert.throws(
      () => assertRestrictionLabels({ popovers: 0, labels: [] }, [label]),
      /U3/,
    );
    assert.throws(
      () => assertRestrictionLabels({ popovers: 1, labels: [label] }, [label]),
      /U3/,
    );
  });
test("B1 counterexample: individually aligned pages with different padding still fail globally", () => {
  const row = { width: 1366, height: 768, navCollapsed: "false", titleX: 280 };
  assertOneTitleLine([row, { ...row }]);
  assertOneTitleLine([row, { ...row, navCollapsed: "true", titleX: 96 }]);
  assert.throws(() => assertOneTitleLine([row, { ...row, titleX: 304 }]), /B1/);
});
test("dataset direct-entry counterexamples reject missing layout and escaping text", () => {
  const card = { display: "flex", width: 400, height: 80, overflow: false };
  const snapshot = { display: "grid", cards: [card, card] };
  assertDatasetCardsLayout(snapshot);
  assert.throws(
    () => assertDatasetCardsLayout({ ...snapshot, display: "block" }),
    /grid/,
  );
  assert.throws(
    () =>
      assertDatasetCardsLayout({
        ...snapshot,
        cards: [card, { ...card, display: "inline" }],
      }),
    /flex/,
  );
  assert.throws(
    () =>
      assertDatasetCardsLayout({
        ...snapshot,
        cards: [card, { ...card, overflow: true }],
      }),
    /text/,
  );
});
for (const [kind, text] of [
  ["object number", "RCP-12345"],
  ["32 digit identifier", "0123456789abcdef0123456789abcdef"],
  ["internal field", "source_chunk_ids"],
  ["JSON", '{"private":1}'],
  ["raw time", "2026-11-17T10:20:00Z"],
  ["raw state", "pending"],
  ["source character offsets", "原文第 1–39 字"],
  ["source character offsets", "原文第2-40字"],
  ["reading version", "阅读版 1"],
])
  test(`leak counterexample: ${text}`, () => {
    assert.equal(
      leakedText(text).some((leak) => leak.kind === kind),
      true,
    );
    assert.throws(() => assert.deepEqual(leakedText(text), []));
  });
