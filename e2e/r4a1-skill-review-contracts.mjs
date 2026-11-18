import assert from "node:assert/strict";

export function assertPublishedSkillReview(result, reasons) {
  assert.equal(result.entity.status, "published");
  assert.equal(result.last_test?.outcome, "passed");
  assert.equal(result.last_test?.report?.mode, "contract");
  assert.equal(
    result.gates.checks.find((check) => check.code === "QG1")?.status,
    "waived",
  );
  for (const code of ["QG2", "QG3", "QG4", "QG5"])
    assert.equal(
      result.gates.checks.find((check) => check.code === code)?.status,
      "pass",
    );
  for (const reason of reasons)
    assert.ok(
      result.gate_history.some((event) => event.detail.includes(reason)),
      "Missing application or independent decision receipt",
    );
}
