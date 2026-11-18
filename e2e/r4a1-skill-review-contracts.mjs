import assert from "node:assert/strict";

export function assertSkillDialogFacts(rows, expected) {
  assert.equal(expected.length, 5);
  assert.equal(
    rows.length,
    5,
    "The confirmation must contain exactly five skill facts",
  );
  for (const field of expected) {
    const matches = rows.filter((row) => row.key === field.key);
    assert.equal(
      matches.length,
      1,
      "A unique confirmation field is required: " + field.key,
    );
    const row = matches[0];
    for (const value of field.values)
      assert.ok(
        row.text.includes(value),
        "Missing business value: " + field.key,
      );
    assert.equal(row.source, field.source);
    assert.ok(
      row.originText.includes(field.originLabel),
      "Missing Chinese origin: " + field.key,
    );
  }
}

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
