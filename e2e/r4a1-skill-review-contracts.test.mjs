import test from "node:test";
import assert from "node:assert/strict";
import { assertPublishedSkillReview } from "./r4a1-skill-review-contracts.mjs";

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
