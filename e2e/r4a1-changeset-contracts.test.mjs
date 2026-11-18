import { test } from "node:test";
import assert from "node:assert/strict";
import {
  assertDocumentDate,
  assertPartialSubmission,
  assertBatchExcluded,
} from "./r4a1-changeset-contracts.mjs";
const proposal = (date) => ({
  document_facts: { valid_from: { value: date } },
  items: [
    {
      id: "A",
      after_data: { kind: "rule", valid_from: date },
      decision: "pending",
      ready_for_bulk_accept: false,
    },
  ],
});
test("date assertion rejects a repeal/history date or inconsistent per-rule date", () => {
  assertDocumentDate(proposal(""), "");
  assert.throws(() => assertDocumentDate(proposal("2019-03-01"), ""));
  const wrong = proposal("2026-01-01");
  wrong.items[0].after_data.valid_from = "2018-05-01";
  assert.throws(() => assertDocumentDate(wrong, "2026-01-01"));
});
test("partial assertion rejects submitting a blocked draft, false counts, or hidden reasons", () => {
  const result = {
    submitted_count: 1,
    remaining_count: 1,
    submitted_entity_ids: ["R-1"],
    remaining_items: [{ item_id: "B", reasons: ["Missing unit"] }],
  };
  assertPartialSubmission(result, ["R-1"], ["B"]);
  for (const bad of [
    { ...result, submitted_entity_ids: ["R-2"] },
    { ...result, remaining_count: 0 },
    { ...result, remaining_items: [{ item_id: "B", reasons: [] }] },
  ])
    assert.throws(() => assertPartialSubmission(bad, ["R-1"], ["B"]));
});
test("bulk assertion rejects a supposedly excluded row that was accepted or materialized", () => {
  const before = proposal("");
  assertBatchExcluded(before, structuredClone(before));
  for (const mutation of [
    { decision: "accepted" },
    { applied_entity_id: "R-1" },
  ]) {
    const after = structuredClone(before);
    Object.assign(after.items[0], mutation);
    assert.throws(() => assertBatchExcluded(before, after));
  }
});
