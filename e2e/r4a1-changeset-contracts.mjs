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

export function assertAllOrdinaryRulesReady(before, after, expectedArticles) {
  assert.ok(
    expectedArticles.length > 1,
    "ordinary rule coverage includes multiple articles",
  );
  const expected = [];
  for (const article of expectedArticles) {
    const matches = before.items.filter(
      (item) =>
        item.after_data.kind === "rule" &&
        item.evidence.some((evidence) => evidence.quote === article),
    );
    assert.equal(
      matches.length,
      1,
      "each expected original article has its own rule candidate",
    );
    const original = matches[0],
      saved = after.items.find((item) => item.id === original.id);
    expected.push(saved.id);
    assert.equal(
      saved.gates.passed,
      true,
      "every expected ordinary rule becomes ready",
    );
    assert.equal(saved.after_data.valid_from, "2026-01-01");
    const ownQuotes = saved.evidence.map((evidence) => evidence.quote);
    for (const field of ["scope", "conditions", "exceptions"]) {
      const value = saved.after_data[field],
        previous = original.after_data[field];
      assert.ok(
        Array.isArray(value)
          ? value.length > 0
          : typeof value === "string" && value.trim(),
        "required facts are explicit",
      );
      const provenance = saved.after_data.field_provenance[field];
      assert.ok(provenance, "each required fact has provenance");
      if (provenance.level === "document") {
        assert.equal(provenance.origin, "system");
        assert.equal(
          field,
          "scope",
          "conditions and exceptions never inherit document/neighbor facts",
        );
        assert.equal(value, after.document_facts.scope.value);
        assert.equal(provenance.quote, after.document_facts.scope.quote);
      } else if (["source", "system"].includes(provenance.origin)) {
        const originalFact = original.after_data.field_provenance[field];
        assert.equal(
          provenance.origin,
          originalFact?.origin,
          "confirmation preserves the original extraction method",
        );
        assert.equal(
          provenance.quote,
          originalFact?.quote,
          "confirmation preserves the original supporting sentence",
        );
        assert.ok(
          provenance.quote &&
            ownQuotes.some((quote) => quote.includes(provenance.quote)),
          "source fact cites this rule own evidence",
        );
        for (const part of Array.isArray(value) ? value : [value])
          assert.ok(
            ownQuotes.some((quote) => quote.includes(part)),
            "source value belongs to this rule own quote",
          );
      } else {
        assert.equal(
          provenance.origin,
          "bulk_confirmation",
          "only explicit missing-fact confirmation is added",
        );
        assert.ok(field === "conditions" || field === "exceptions");
        assert.ok(
          Array.isArray(previous) ? previous.length === 0 : !previous,
          "existing source facts are not overwritten by blanket confirmation",
        );
        assert.ok(
          provenance.actor_id && provenance.confirmed_at,
          "batch confirmation records actor and time",
        );
      }
    }
  }
  return expected;
}

export function assertDocumentScope(
  proposal,
  source,
  expectedValue,
  expectedQuote,
) {
  const fact = proposal.document_facts.scope;
  assert.equal(
    fact.value,
    expectedValue,
    "current-file general scope is exact",
  );
  assert.equal(
    fact.quote,
    expectedQuote,
    "scope quotes the full current-file sentence",
  );
  assert.ok(source.includes(expectedQuote));
  assert.equal(fact.material_id, proposal.source_material_id);
  assert.ok(
    fact.anchor_id && fact.source_version,
    "default scope is anchored to a real source version",
  );
  let inherited = 0;
  for (const item of proposal.items.filter(
    (item) => item.after_data.kind === "rule",
  )) {
    assert.equal(item.after_data.scope, expectedValue);
    const provenance = item.after_data.field_provenance.scope;
    if (provenance.level === "document") {
      inherited++;
      assert.equal(provenance.origin, "system");
      for (const field of [
        "quote",
        "anchor_id",
        "source_version",
        "material_id",
      ])
        assert.equal(
          provenance[field],
          fact[field],
          "inherited scope retains current-file provenance",
        );
    } else {
      assert.ok(["source", "system"].includes(provenance.origin));
      assert.ok(
        item.evidence.some(
          (evidence) =>
            evidence.quote.includes(provenance.quote) &&
            evidence.quote.includes(expectedValue),
        ),
      );
    }
  }
  assert.ok(inherited > 0, "default scope is inherited by ordinary rules");
}
