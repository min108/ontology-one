import assert from "node:assert/strict";

export function assertRejectedWrite(before, after) {
  assert.deepEqual(
    after,
    before,
    "Rejected writes must preserve saved contents and version",
  );
}

export function assertLegacyEdit(before, after, content) {
  assert.equal(after.id, before.id);
  assert.equal(after.kind, before.kind);
  assert.equal(after.payload.content, content);
  assert.equal(after.version, before.version + 1);
}

export function assertMalformedReadable(detail) {
  assert.equal(detail.entity.payload.judgement.applies_to, null);
  const check = detail.gates.checks.find((item) => item.code === "QG3");
  assert.equal(check.status, "fail");
  assert.match(check.reason, /判定式|适用对象/);
}
