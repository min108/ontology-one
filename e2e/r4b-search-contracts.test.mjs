import test from "node:test";
import assert from "node:assert/strict";
import {
  assertAbsent,
  assertChunkLimit,
  assertLegacy,
  assertRankings,
  assertRebuilt,
  assertRuleEvidence,
  original,
} from "./r4b-search-contracts.mjs";

function element(id) {
  return {
    id,
    doc_type: "element",
    entity_id: id,
    doc_id: "el:" + id,
    kind: "rule",
    title: "制度条款" + id,
    status: "published",
  };
}
function chunk(id, entityId = "M") {
  return {
    id,
    doc_type: "chunk",
    chunk_id: id,
    entity_id: entityId,
    doc_id: "chunk:" + id,
    kind: "material",
    title: "出处",
    status: "published",
  };
}
const rules = Object.fromEntries(
  ["S1", "S2", "S5", "S6"].map((key) => [key, element(key)]),
);
const chunks = [
  { id: "C", content: original.S6 },
  { id: "unrelated", content: "资料需要复查。" },
];
function results() {
  return {
    S1: [chunk("A"), rules.S1],
    S2: [rules.S2],
    S3: [rules.S2],
    S4: [rules.S2],
    S5: [rules.S5],
    S5_title: [rules.S5],
    S6: [rules.S6, chunk("C")],
  };
}
function ranks(actual) {
  assertRankings(actual, rules, "M", chunks);
}

test("S1/S2 fail when a rule falls to sixth or loses its published status", () => {
  ranks(results());
  for (const key of ["S1", "S2"]) {
    const late = results();
    late[key] = [
      ...Array.from({ length: 5 }, (_, i) => element("other-" + i)),
      rules[key],
    ];
    assert.throws(() => ranks(late), /first five/);
    const draft = results();
    draft[key] = [{ ...rules[key], status: "draft" }];
    assert.throws(() => ranks(draft), /published rule/);
  }
});
test("S3/S4 require rank one, S5 requires both alias and retained official title", () => {
  for (const key of ["S3", "S4"]) {
    const later = results();
    later[key].unshift(element("other"));
    assert.throws(() => ranks(later), /rank the rule first/);
  }
  for (const key of ["S5", "S5_title"]) {
    const missing = results();
    missing[key] = [];
    assert.throws(() => ranks(missing), /republished original title/);
    const renamed = results();
    renamed[key] = [{ ...rules.S5, title: "质量保证金" }];
    assert.throws(() => ranks(renamed), /republished original title/);
  }
});
test("S6 requires the original material's matching confirmed chunk and its rule; two per material", () => {
  for (const hits of [
    [rules.S6],
    [rules.S6, chunk("unrelated")],
    [rules.S6, chunk("C", "foreign")],
    [chunk("C")],
  ]) {
    const missing = results();
    missing.S6 = hits;
    assert.throws(() => ranks(missing), /S6 must/);
  }
  assertChunkLimit([chunk("1"), chunk("2"), chunk("3", "other")]);
  assert.throws(
    () => assertChunkLimit([chunk("1"), chunk("2"), chunk("3")]),
    /at most two/,
  );
});
test("an attractive result with fabricated or foreign evidence fails the original document check", () => {
  const ref = {
    material_id: "M",
    source_version: "V",
    anchor_id: "A",
    chunk_id: "C",
    quote: original.S1,
  };
  const entity = { ...rules.S1, payload: { source_refs: [ref] } };
  assert.equal(assertRuleEvidence(entity, original.S1, "M", original.S1), ref);
  for (const changes of [
    { quote: "编造" + original.S1 },
    { chunk_id: "" },
    { anchor_id: "" },
    { material_id: "other" },
  ]) {
    assert.throws(
      () =>
        assertRuleEvidence(
          { ...entity, payload: { source_refs: [{ ...ref, ...changes }] } },
          original.S1,
          "M",
          original.S1,
        ),
      /verbatim anchored/,
    );
  }
});
test("S7/S10 and immediate archive/discard checks reject rule and source-ownership leaks", () => {
  const forbidden = new Set(["private"]);
  assertAbsent([element("other")], forbidden);
  for (const leaked of [
    element("private"),
    chunk("C", "private"),
    chunk("private"),
  ])
    assert.throws(
      () => assertAbsent([leaked], forbidden, "visible private data"),
      /visible private/,
    );
});
test("S11 fails for reordered v1 rows or an incompatible legacy payload", () => {
  const rows = [
    { id: "R", version: 2, payload: {}, summary: "说明" },
    { id: "C", version: 1, payload: { chunk_id: "C" }, summary: "原文" },
  ];
  const page = { page: 1, page_size: 25, total: 2, items: rows };
  assertLegacy(page, page);
  assert.throws(
    () => assertLegacy({ ...page, items: [...rows].reverse() }, page),
    /same order/,
  );
  assert.throws(() =>
    assertLegacy(
      { ...page, items: [{ ...rows[0], payload: null }, rows[1]] },
      page,
    ),
  );
});
test("S12 fails for reordered, lost, duplicated or wrong type documents in any query including the S5 official title", () => {
  assertRebuilt(results(), results());
  const reordered = results();
  reordered.S6.reverse();
  assert.throws(
    () => assertRebuilt(results(), reordered),
    /entire ordered result/,
  );
  const lost = results();
  lost.S5_title = [];
  assert.throws(() => assertRebuilt(results(), lost), /entire ordered result/);
  const repeated = results();
  repeated.S1.push(rules.S1);
  assert.throws(() => assertRebuilt(results(), repeated), /repeated/);
  const mixed = results();
  mixed.S6[1] = { ...chunk("C"), doc_id: "el:C" };
  assert.throws(() => assertRebuilt(results(), mixed));
});
