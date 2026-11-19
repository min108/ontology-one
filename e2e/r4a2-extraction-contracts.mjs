import assert from "node:assert/strict";

export function submissionShare(original, saved) {
  const submitted = saved.filter(
    (item) => item.applied_status === "review",
  ).length;
  const total = original.length;
  return { submitted, total, ratio: total ? submitted / total : 0 };
}

export function assertSubmissionShare(original, saved, result) {
  assert.deepEqual(
    saved.map((item) => item.id).sort(),
    original.map((item) => item.id).sort(),
    "every original item remains in the denominator",
  );
  const queued = saved
    .filter((item) => item.applied_status === "review")
    .map((item) => item.applied_entity_id);
  assert.ok(
    queued.every(Boolean),
    "each saved review item identifies its draft",
  );
  assert.equal(
    new Set(queued).size,
    queued.length,
    "each submitted item has a distinct draft",
  );
  assert.deepEqual(
    [...result.submitted_entity_ids].sort(),
    [...queued].sort(),
    "the receipt matches the saved review queue",
  );
  assert.equal(result.submitted_count, queued.length);
  const share = submissionShare(original, saved);
  assert.ok(
    share.submitted > 1 && share.ratio > 0.5,
    "more than half of the entire original proposal reached independent review",
  );
}
