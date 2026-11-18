import test from "node:test";
import assert from "node:assert/strict";
import {
  assertDialogActionsVisible,
  assertPublishedRepair,
  assertVisibleFacts,
} from "./r4a1-legacy-contracts.mjs";

test("AB7–9 counterexamples reject draft state, identity loss and missing application or waiver activity", () => {
  const expected = {
    id: "old",
    kind: "data",
    previousVersion: 1,
    application: "原件待归档",
    waiver: "已独立核对纸质凭证",
  };
  const detail = {
    entity: {
      id: "old",
      kind: "data",
      status: "published",
      version: 2,
      activities: [
        { detail: expected.application },
        { detail: expected.waiver },
      ],
    },
    gates: {
      checks: [
        { code: "QG1", status: "waived" },
        { code: "QG2", status: "pass" },
      ],
    },
  };
  assertPublishedRepair(detail, expected);
  for (const patch of [
    { id: "replacement" },
    { status: "review" },
    { version: 1 },
    { activities: [{ detail: expected.waiver }] },
    { activities: [{ detail: expected.application }] },
  ]) {
    assert.throws(() =>
      assertPublishedRepair(
        { ...detail, entity: { ...detail.entity, ...patch } },
        expected,
      ),
    );
  }
  assert.throws(() =>
    assertPublishedRepair(
      {
        ...detail,
        gates: {
          checks: [
            { code: "QG1", status: "waived" },
            { code: "QG2", status: "fail" },
          ],
        },
      },
      expected,
    ),
  );
});
test("AB10 counterexamples reject missing date, altered fact and falsely attributed origin", () => {
  const fields = [{ key: "valid_from", value: "2026-01-01", source: "source" }];
  assertVisibleFacts(fields, [
    { key: "valid_from", text: "生效日期 2026-01-01", source: "source" },
  ]);
  for (const rows of [
    [],
    [{ key: "valid_from", text: "2019-03-01", source: "source" }],
    [{ key: "valid_from", text: "2026-01-01", source: "editor" }],
  ])
    assert.throws(() => assertVisibleFacts(fields, rows));
});

test("publication footer counterexample rejects actions below the viewport", () => {
  const boxes = [
    { x: 0, y: 700, width: 80, height: 36 },
    { x: 100, y: 700, width: 80, height: 36 },
  ];
  assertDialogActionsVisible(boxes, { height: 768 });
  assert.throws(() =>
    assertDialogActionsVisible([boxes[0], { ...boxes[1], y: 900 }], {
      height: 768,
    }),
  );
  assert.throws(() =>
    assertDialogActionsVisible([boxes[0], null], { height: 768 }),
  );
});
