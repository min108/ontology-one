import { test } from "node:test";
import assert from "node:assert/strict";
import {
  assertGateProjection,
  assertSavedRuleCases,
  assertRuleCaseResults,
  assertWaiverActivity,
  assertSubmissionBlocked,
  assertUnwaivedChecks,
  assertDataRepair,
} from "./r4-assertion-contracts.mjs";
test("mainline data repair rejects a lost unit, wrong grain or unsaved version", () => {
  const expected = { unit: "元", grain: "每份合同", before_version: 1 };
  const entity = {
    kind: "data",
    version: 2,
    payload: { unit: expected.unit, grain: expected.grain },
  };
  assertDataRepair(entity, expected);
  for (const changed of [
    { ...entity, payload: { ...entity.payload, unit: "" } },
    { ...entity, payload: { ...entity.payload, grain: "每次付款" } },
    { ...entity, version: 1 },
  ])
    assert.throws(() => assertDataRepair(changed, expected));
});
const checks = Array.from({ length: 5 }, (_, i) => ({
  code: `QG${i + 1}`,
  status: "pass",
  reason: "核对通过",
}));
const rows = checks.map((row) => ({ ...row, text: row.reason }));
test("mainline receipt rejects failed, missing, duplicate or waived checks", () => {
  assertUnwaivedChecks({ passed: true, checks });
  for (const changed of [
    { passed: false, checks },
    { passed: true, checks: checks.slice(1) },
    { passed: true, checks: [checks[1], ...checks.slice(1)] },
    {
      passed: true,
      checks: [{ ...checks[0], status: "waived" }, ...checks.slice(1)],
    },
  ])
    assert.throws(() => assertUnwaivedChecks(changed));
});
const cases = [
  { name: "适用", input: { amount: 30 }, expected: "pass" },
  { name: "不适用", input: { amount: 0 }, expected: "not_applicable" },
];
test("gate projection rejects a missing or stale real check and missing reason", () => {
  assertGateProjection({ checks }, rows);
  assert.throws(() => assertGateProjection({ checks }, rows.slice(1)));
  assert.throws(() =>
    assertGateProjection({ checks }, [
      { ...rows[0], status: "fail" },
      ...rows.slice(1),
    ]),
  );
  assert.throws(() =>
    assertGateProjection({ checks }, [
      { ...rows[0], text: "待审核确认" },
      ...rows.slice(1),
    ]),
  );
});
test("testcase save assertions reject lost cases and incorrect results", () => {
  assertSavedRuleCases(
    { payload: { judgement: { test_cases: cases } } },
    cases,
  );
  assert.throws(() =>
    assertSavedRuleCases(
      { payload: { judgement: { test_cases: cases.slice(1) } } },
      cases,
    ),
  );
  const result = {
    total: 2,
    passed_count: 2,
    passed: true,
    results: cases.map((item) => ({
      actual: item.expected,
      expected: item.expected,
      passed: true,
    })),
  };
  assertRuleCaseResults(result);
  assert.throws(() =>
    assertRuleCaseResults({
      ...result,
      results: [{ ...result.results[0], actual: "fail" }, result.results[1]],
    }),
  );
});
test("waiver activity assertion rejects an empty or missing recorded reason", () => {
  const entity = {
    status: "published",
    activities: [{ detail: "已核对纸质制度" }],
  };
  assertWaiverActivity(entity, "已核对纸质制度");
  assert.throws(() => assertWaiverActivity(entity, ""));
  assert.throws(() =>
    assertWaiverActivity({ ...entity, activities: [] }, "已核对纸质制度"),
  );
});
test("submission assertion rejects enabled submission and blank placeholder reasons", () => {
  assertSubmissionBlocked({ disabled: true, reason: "请补齐生效日期" });
  assert.throws(() =>
    assertSubmissionBlocked({ disabled: false, reason: "请补齐生效日期" }),
  );
  assert.throws(() => assertSubmissionBlocked({ disabled: true, reason: " " }));
  assert.throws(() =>
    assertSubmissionBlocked({ disabled: true, reason: "待审核确认" }),
  );
});

test("semantic group assertion rejects missing kinds and repeated drafts", async () => {
  const { assertSavedSemanticGroup } =
    await import("./r4-assertion-contracts.mjs");
  const entities = [
    "data",
    "link_type",
    "metric",
    "object_type",
    "process",
    "rule",
    "term",
  ].map((kind, i) => ({
    id: String(i),
    kind,
    status: "draft",
    version: 1,
    payload: { definition: "业务定义" },
  }));
  assertSavedSemanticGroup(entities);
  assert.throws(() => assertSavedSemanticGroup(entities.slice(1)));
  assert.throws(() =>
    assertSavedSemanticGroup(
      entities.map((entity) => ({ ...entity, id: "same" })),
    ),
  );
});
