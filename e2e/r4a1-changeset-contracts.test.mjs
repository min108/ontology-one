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

test("every expected rule must pass and facts cannot leak from its neighbor", async () => {
  const { assertAllOrdinaryRulesReady } =
    await import("./r4a1-changeset-contracts.mjs");
  const articles = [
    "first subject if approved no exception",
    "second subject if delivered no exception",
  ];
  const before = {
    items: articles.map((quote, index) => ({
      id: String(index),
      evidence: [{ quote }],
      after_data: {
        kind: "rule",
        scope: index === 0 ? "first subject" : "second subject",
        conditions: [index === 0 ? "if approved" : "if delivered"],
        exceptions: "no exception",
      },
    })),
  };
  const after = structuredClone(before);
  for (const item of after.items) {
    item.gates = { passed: true };
    item.after_data.valid_from = "2026-01-01";
    item.after_data.field_provenance = Object.fromEntries(
      ["scope", "conditions", "exceptions"].map((key) => [
        key,
        { origin: "source", quote: item.evidence[0].quote },
      ]),
    );
  }
  assert.deepEqual(assertAllOrdinaryRulesReady(before, after, articles), [
    "0",
    "1",
  ]);
  const blocked = structuredClone(after);
  blocked.items[1].gates.passed = false;
  assert.throws(() => assertAllOrdinaryRulesReady(before, blocked, articles));
  const inherited = structuredClone(after);
  inherited.items[1].after_data.conditions = ["if approved"];
  inherited.items[1].after_data.field_provenance.conditions.quote = articles[0];
  assert.throws(() => assertAllOrdinaryRulesReady(before, inherited, articles));
  const fakeDocumentCondition = structuredClone(after);
  fakeDocumentCondition.items[1].after_data.field_provenance.conditions.origin =
    "document";
  assert.throws(() =>
    assertAllOrdinaryRulesReady(before, fakeDocumentCondition, articles),
  );
});

test("document scope must be exact and inherited from its anchored current-file sentence", async () => {
  const { assertDocumentScope } =
    await import("./r4a1-changeset-contracts.mjs");
  const fact = {
    value: "all employees business travel",
    quote: "This policy applies to all employees business travel.",
    origin: "document",
    material_id: "M",
    anchor_id: "A",
    source_version: "V",
  };
  const proposal = {
    source_material_id: "M",
    document_facts: { scope: fact },
    items: [
      {
        after_data: {
          kind: "rule",
          scope: fact.value,
          field_provenance: { scope: { ...fact } },
        },
      },
    ],
  };
  assertDocumentScope(proposal, fact.quote, fact.value, fact.quote);
  const wrong = structuredClone(proposal);
  wrong.document_facts.scope.value = "historical budget scope";
  assert.throws(() =>
    assertDocumentScope(wrong, fact.quote, fact.value, fact.quote),
  );
  const inherited = structuredClone(proposal);
  inherited.items[0].after_data.field_provenance.scope.source_version = "OLD";
  assert.throws(() =>
    assertDocumentScope(inherited, fact.quote, fact.value, fact.quote),
  );
});
