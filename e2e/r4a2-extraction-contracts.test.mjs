import { test } from "node:test";
import assert from "node:assert/strict";
import {
  submissionShare,
  assertSubmissionShare,
} from "./r4a2-extraction-contracts.mjs";

const initial = ["A", "B", "C"].map((id) => ({ id }));
const queued = initial.map(({ id }, index) => ({
  id,
  applied_entity_id: index < 2 ? "asset-" + id : null,
  applied_status: index < 2 ? "review" : null,
}));
const result = {
  submitted_count: 2,
  submitted_entity_ids: ["asset-A", "asset-B"],
};

test("AC13 computes the majority against every original item and independently saved review states", () => {
  assert.deepEqual(submissionShare(initial, queued), {
    submitted: 2,
    total: 3,
    ratio: 2 / 3,
  });
  assertSubmissionShare(initial, queued, result);
  for (const mutation of [
    () =>
      assertSubmissionShare(
        [...initial, { id: "D" }],
        [...queued, { id: "D" }],
        result,
      ),
    () =>
      assertSubmissionShare(initial, [queued[0], initial[1], initial[2]], {
        submitted_count: 1,
        submitted_entity_ids: ["asset-A"],
      }),
    () =>
      assertSubmissionShare(
        initial,
        [queued[0], { ...queued[1], applied_status: "draft" }, queued[2]],
        result,
      ),
    () =>
      assertSubmissionShare(initial, queued, {
        submitted_count: 2,
        submitted_entity_ids: ["asset-A", "asset-A"],
      }),
    () =>
      assertSubmissionShare(initial, queued, { ...result, submitted_count: 1 }),
  ])
    assert.throws(mutation);
});
