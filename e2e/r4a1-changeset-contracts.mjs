import assert from "node:assert/strict";
export function assertDocumentDate(proposal, expected) {
  assert.equal(
    proposal.document_facts.valid_from.value,
    expected,
    "current document effective date",
  );
  const rules = proposal.items.filter(
    (item) => item.after_data.kind === "rule",
  );
  assert.ok(rules.length > 0, "rules present");
  for (const item of rules)
    assert.equal(
      item.after_data.valid_from || "",
      expected,
      "rule inherits only current document date",
    );
}
export function assertPartialSubmission(result, readyIds, blockedIds) {
  assert.equal(
    result.submitted_count,
    readyIds.length,
    "exact submitted count",
  );
  assert.equal(
    result.remaining_count,
    blockedIds.length,
    "exact remaining count",
  );
  assert.deepEqual(
    [...result.submitted_entity_ids].sort(),
    [...readyIds].sort(),
  );
  assert.deepEqual(
    result.remaining_items.map((item) => item.item_id).sort(),
    [...blockedIds].sort(),
  );
  for (const item of result.remaining_items)
    assert.ok(
      item.reasons.length > 0,
      "each remaining row explains its blockers",
    );
}
export function assertBatchExcluded(before, after) {
  const excluded = before.items.filter(
    (item) => item.decision === "pending" && !item.ready_for_bulk_accept,
  );
  assert.ok(excluded.length > 0, "real incomplete candidates exist");
  for (const item of excluded) {
    const saved = after.items.find((row) => row.id === item.id);
    assert.equal(
      saved.decision,
      "pending",
      "incomplete candidate was not bulk accepted",
    );
    assert.ok(
      !saved.applied_entity_id,
      "incomplete candidate has no adopted draft",
    );
  }
}
