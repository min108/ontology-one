import test from "node:test";
import assert from "node:assert/strict";
import {
  assertPublishedSkillReview,
  assertSkillDialogFacts,
} from "./r4a1-skill-review-contracts.mjs";

test("confirmation facts reject duplicate rows, missing values and wrong Chinese provenance", () => {
  const expected = [
    "definition",
    "inputs",
    "outputs",
    "steps",
    "usage_conditions",
  ].map((key) => ({
    key,
    values: ["actual " + key],
    source: "editor",
    originLabel: "核对人填写",
  }));
  const rows = expected.map((field) => ({
    key: field.key,
    text: field.values[0],
    source: field.source,
    originText: field.originLabel,
  }));
  assertSkillDialogFacts(rows, expected);
  for (const broken of [
    rows.slice(1),
    [...rows, rows[0]],
    rows.map((row) => (row.key === "inputs" ? { ...row, text: "" } : row)),
    rows.map((row) =>
      row.key === "outputs" ? { ...row, originText: "未记录来源" } : row,
    ),
  ])
    assert.throws(() => assertSkillDialogFacts(broken, expected));
});

test("managed skill publication counterexamples reject missing proof, waived dependencies and lost decisions", () => {
  const reasons = ["application", "denial", "renewed application", "waiver"];
  const result = {
    entity: { status: "published" },
    last_test: { outcome: "passed", report: { mode: "contract" } },
    gates: {
      checks: ["QG1", "QG2", "QG3", "QG4", "QG5"].map((code) => ({
        code,
        status: code === "QG1" ? "waived" : "pass",
      })),
    },
    gate_history: reasons.map((detail) => ({ detail })),
  };
  assertPublishedSkillReview(result, reasons);
  for (const broken of [
    { ...result, entity: { status: "review" } },
    { ...result, last_test: null },
    {
      ...result,
      last_test: { outcome: "failed", report: { mode: "contract" } },
    },
    {
      ...result,
      gates: {
        checks: result.gates.checks.map((check) =>
          check.code === "QG4" ? { ...check, status: "waived" } : check,
        ),
      },
    },
    {
      ...result,
      gate_history: result.gate_history.filter(
        (event) => event.detail !== "denial",
      ),
    },
  ])
    assert.throws(() => assertPublishedSkillReview(broken, reasons));
});
