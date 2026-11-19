import assert from "node:assert/strict";

export const original = {
  sha256: "2df00f33bd91deab4af971baaa9d45501e552fc1b2bf715e018a34d2d951065b",
  S1: "验收通过后10个工作日内提出付款申请",
  S2: "预付款比例不得超过合同总金额的30%",
  S5: "质保金按合同总金额的5%预留",
  S6: "一次性告知经办部门补正",
  archive: "对未经审批擅自付款",
};
export const alias = "质量保证金";
export function queries(rules) {
  const title = rules.S2.title;
  const fragment = "预付款比";
  assert.ok(
    title.includes(fragment),
    "S3 uses four consecutive characters of the official rule title",
  );
  return {
    S1: "10个工作日",
    S2: "预付款比例",
    S3: fragment,
    S4: rules.S2.id,
    S5: alias,
    S5_title: rules.S5.title,
    S6: original.S6,
  };
}

export function assertRuleEvidence(entity, sentence, materialId, source) {
  assert.equal(entity.kind, "rule");
  assert.equal(entity.status, "published");
  const reference = entity.payload.source_refs.find(
    (r) =>
      r.material_id === materialId &&
      r.quote.includes(sentence) &&
      source.includes(r.quote),
  );
  assert.ok(
    reference?.anchor_id && reference.chunk_id && reference.source_version,
    "published rule must retain its own verbatim anchored source",
  );
  return reference;
}

export function assertRankings(results, rules, materialId, chunks) {
  for (const key of ["S1", "S2"]) {
    const found = results[key]
      .slice(0, 5)
      .find(
        (hit) =>
          hit.doc_type === "element" &&
          hit.id === rules[key].id &&
          hit.kind === "rule" &&
          hit.status === "published",
      );
    assert.ok(
      found,
      key + " requires the cited published rule in the first five results",
    );
  }
  for (const key of ["S3", "S4"]) {
    assert.equal(results[key][0]?.doc_type, "element");
    assert.equal(
      results[key][0]?.id,
      rules.S2.id,
      key + " must rank the rule first",
    );
  }
  for (const key of ["S5", "S5_title"])
    assert.ok(
      results[key].some(
        (hit) =>
          hit.doc_type === "element" &&
          hit.id === rules.S5.id &&
          hit.title === rules.S5.title &&
          hit.status === "published",
      ),
      key + " must find the republished original title",
    );
  assert.ok(
    results.S6.some(
      (hit) =>
        hit.doc_type === "element" &&
        hit.id === rules.S6.id &&
        hit.status === "published",
    ),
    "S6 must include the independently published rule",
  );
  assert.ok(
    results.S6.some(
      (hit) =>
        hit.doc_type === "chunk" &&
        hit.entity_id === materialId &&
        chunks.some(
          (chunk) =>
            chunk.id === hit.chunk_id && chunk.content.includes(original.S6),
        ),
    ),
    "S6 must include the immutable source chunk which actually contains the quote",
  );
  for (const hits of Object.values(results)) assertChunkLimit(hits);
}

export function assertChunkLimit(hits) {
  const counts = new Map();
  for (const hit of hits.filter((hit) => hit.doc_type === "chunk")) {
    const count = (counts.get(hit.entity_id) ?? 0) + 1;
    assert.ok(count <= 2, "at most two source chunks from the same material");
    counts.set(hit.entity_id, count);
  }
}

export function assertAbsent(hits, forbiddenIds, message) {
  for (const hit of hits)
    for (const field of ["id", "entity_id", "chunk_id"])
      assert.ok(!forbiddenIds.has(hit[field]), message);
}

export function orderedDocuments(results) {
  return Object.fromEntries(
    Object.entries(results).map(([key, hits]) => {
      const ids = hits.map((hit) => hit.doc_id);
      for (const hit of hits) {
        const canonical =
          hit.doc_type === "chunk"
            ? "chunk:" + hit.chunk_id
            : "el:" + hit.entity_id;
        assert.equal(hit.doc_id, canonical);
      }
      assert.equal(
        new Set(ids).size,
        ids.length,
        key + " repeated a search document",
      );
      return [key, ids];
    }),
  );
}

export function assertRebuilt(before, after) {
  assert.deepEqual(
    orderedDocuments(after),
    orderedDocuments(before),
    "rebuild must restore the entire ordered result for every S1–S6 input",
  );
}

export function assertLegacy(legacy, canonical) {
  assert.equal(legacy.page, canonical.page);
  assert.equal(legacy.page_size, canonical.page_size);
  assert.equal(legacy.total, canonical.total);
  assert.deepEqual(
    legacy.items.map((item) => item.id),
    canonical.items.map((item) => item.id),
    "the original search route must return the same items in the same order",
  );
  for (const item of legacy.items) {
    assert.ok(typeof item.payload === "object" && item.payload !== null);
    assert.equal(typeof item.summary, "string");
    assert.equal(typeof item.version, "number");
  }
}
