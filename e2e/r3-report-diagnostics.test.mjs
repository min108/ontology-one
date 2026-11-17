import test from "node:test";
import assert from "node:assert/strict";
import { reportIssueSummary } from "./r3-report-diagnostics.mjs";

test("numeric diagnostics retain safe locations and categories without model values", () => {
  assert.deepEqual(reportIssueSummary([
    "表格 3 第2行：31日（缺少本行的来源引用）",
    "段落 1：300000家（数值或单位与本段引用原文不符）",
  ]), [
    { kind: "missing_row_reference", block: 3, row: 2, unit: "日" },
    { kind: "paragraph_quantity_mismatch", block: 1, unit: "家" },
  ]);
});
test("unexpected text cannot become a diagnostic field", () => {
  const value = reportIssueSummary(["PRIVATE_ANSWER", "段落 2：5PRIVATE_ANSWER（PRIVATE_ANSWER）"]);
  assert.equal(JSON.stringify(value).includes("PRIVATE_ANSWER"), false);
  assert.deepEqual(reportIssueSummary(null), []);
});
