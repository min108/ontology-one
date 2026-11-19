import test from "node:test";
import assert from "node:assert/strict";
import {
  assertDialogActionsVisible,
  assertPublishedRepair,
  assertVisibleFacts,
  assertReviewOrigins,
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
  const fields = [{ key: "valid_from", value: "2026-01-01", source: "system" }];
  assertVisibleFacts(fields, [
    { key: "valid_from", text: "生效日期 2026-01-01", source: "system" },
  ]);
  for (const rows of [
    [],
    [{ key: "valid_from", text: "2019-03-01", source: "system" }],
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

test("AB10 requires an actual manual scope alongside extracted date and confirmed omissions", () => {
  const scope = "总部采购与所属单位工程合同付款";
  const fields = [
    { key: "scope", value: scope, source: "editor" },
    { key: "valid_from", value: "2026-01-01", source: "system" },
    { key: "exceptions", value: "无例外", source: "batch_confirmation" },
  ];
  assertReviewOrigins(fields, scope);
  for (const broken of [
    fields.slice(1),
    fields.map((field) =>
      field.key === "scope" ? { ...field, source: "source" } : field,
    ),
    fields.map((field) =>
      field.key === "scope"
        ? { ...field, value: "unchanged extraction" }
        : field,
    ),
    fields.map((field) =>
      field.key === "valid_from" ? { ...field, source: "editor" } : field,
    ),
    fields.filter((field) => field.source !== "batch_confirmation"),
  ])
    assert.throws(() => assertReviewOrigins(broken, scope));
});
