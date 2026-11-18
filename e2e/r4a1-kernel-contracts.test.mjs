import assert from "node:assert/strict";
import { test } from "node:test";
import {
  assertRejectedWrite,
  assertLegacyEdit,
  assertMalformedReadable,
} from "./r4a1-kernel-contracts.mjs";

test("AB11/14 rejected save assertion detects a committed bad edit", () => {
  const before = { version: 1, payload: { content: "原文" } };
  assertRejectedWrite(before, structuredClone(before));
  assert.throws(() => assertRejectedWrite(before, { ...before, version: 2 }));
  assert.throws(() =>
    assertRejectedWrite(before, {
      ...before,
      payload: { content: "已错误保存" },
    }),
  );
});

test("AB12 ordinary edit assertion detects lost kind, content or version", () => {
  const before = { id: "OLD", kind: "memo", version: 1 };
  const after = { ...before, version: 2, payload: { content: "新文字" } };
  assertLegacyEdit(before, after, "新文字");
  for (const change of [
    { kind: "knowledge" },
    { version: 1 },
    { payload: { content: "旧文字" } },
  ]) {
    assert.throws(() =>
      assertLegacyEdit(before, { ...after, ...change }, "新文字"),
    );
  }
});

test("AB14 readable old rule must expose its real problem instead of false passing", () => {
  const detail = {
    entity: { payload: { judgement: { applies_to: null } } },
    gates: {
      checks: [
        { code: "QG3", status: "fail", reason: "判定式的适用对象格式不正确" },
      ],
    },
  };
  assertMalformedReadable(detail);
  assert.throws(() =>
    assertMalformedReadable({
      ...detail,
      gates: { checks: [{ code: "QG3", status: "pass", reason: "" }] },
    }),
  );
});
