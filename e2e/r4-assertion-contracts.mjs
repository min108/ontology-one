import assert from "node:assert/strict";

export function assertGateProjection(server, rendered) {
  assert.equal(server.checks.length, 5, "five checks must be present");
  assert.equal(rendered.length, 5, "the UI must show every actual check");
  for (const check of server.checks) {
    const row = rendered.find((item) => item.code === check.code);
    assert.ok(row, `missing check ${check.code}`);
    assert.equal(row.status, check.status, `stale check ${check.code}`);
    if (check.reason)
      assert.ok(
        row.text.includes(check.reason),
        `missing reason ${check.code}`,
      );
  }
}
export function assertSavedRuleCases(entity, cases) {
  assert.ok(cases.length >= 2, "the acceptance path must write two cases");
  assert.deepEqual(
    entity.payload.judgement.test_cases,
    cases,
    "UI save must persist both actual cases",
  );
}
export function assertRuleCaseResults(result) {
  assert.equal(result.total, 2, "both saved cases must run");
  assert.equal(result.results.length, 2);
  assert.equal(result.passed_count, 2);
  assert.equal(result.passed, true);
  for (const row of result.results) {
    assert.equal(row.passed, true);
    assert.equal(row.actual, row.expected);
  }
}
export function assertWaiverActivity(entity, reason) {
  assert.equal(entity.status, "published");
  assert.ok(reason.trim(), "waiver acceptance must assert a concrete reason");
  assert.ok(
    entity.activities.some((item) => item.detail.includes(reason)),
    "waiver reason must survive in activity history",
  );
}
export function assertSubmissionBlocked(snapshot) {
  assert.equal(
    snapshot.disabled,
    true,
    "failed checks must disable submission",
  );
  assert.ok(
    snapshot.reason.trim(),
    "disabled submission must explain the prerequisite",
  );
  assert.ok(
    !snapshot.reason.includes("待审核确认"),
    "a placeholder is not a check result",
  );
}
