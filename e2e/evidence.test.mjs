import assert from "node:assert/strict";
import {
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { resolve } from "node:path";
import { test } from "node:test";
import { packageEvidence } from "./evidence.mjs";

function fixture(t) {
  const root = mkdtempSync(resolve("work", "evidence-contract-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const source = resolve(root, "source");
  mkdirSync(source);
  return { source, destination: resolve(root, "packages") };
}

test("summary is allowlisted, screenshots split by role, complete artifact remains linked", (t) => {
  const { source, destination } = fixture(t);
  writeFileSync(resolve(source, "runner.json"), '{"status":"passed"}');
  writeFileSync(resolve(source, "api.log"), "healthy\n");
  writeFileSync(
    resolve(source, "storageState.json"),
    '{"cookies":[{"value":"private"}]}',
  );
  for (const role of ["modeler", "reviewer", "admin"]) {
    mkdirSync(resolve(source, "screenshots", role), { recursive: true });
    writeFileSync(
      resolve(source, "screenshots", role, "home.png"),
      "synthetic screenshot",
    );
  }
  const index = packageEvidence(
    source,
    destination,
    "https://github.com/example/run/artifacts/123",
  );
  assert.equal(
    index.completeArtifact,
    "https://github.com/example/run/artifacts/123",
  );
  assert.deepEqual(
    index.packages.summary.files.map((item) => item.path).sort(),
    ["api.log", "runner.json"],
  );
  assert.equal(index.packages.admin.files.length, 1);
  assert.equal(index.packages.admin.files[0].sha256.length, 64);
  assert.equal(
    readFileSync(resolve(destination, "summary", "api.log"), "utf8"),
    "healthy\n",
  );
});

test("authentication content cannot hide under an allowed JSON name", (t) => {
  const { source, destination } = fixture(t);
  writeFileSync(
    resolve(source, "results.json"),
    JSON.stringify({
      nested: { cookies: [{ name: "session", value: "secret" }], origins: [] },
    }),
  );
  assert.throws(
    () => packageEvidence(source, destination, ""),
    /Authentication/,
  );
});

test("symbolic links cannot escape the evidence source", (t) => {
  const { source, destination } = fixture(t);
  writeFileSync(resolve(source, "private"), "private");
  symlinkSync(resolve(source, "private"), resolve(source, "runner.json"));
  assert.throws(() => packageEvidence(source, destination, ""), /symbolic/);
});

test("a package larger than the explicit download budget fails", (t) => {
  const { source, destination } = fixture(t);
  writeFileSync(resolve(source, "api.log"), "0123456789");
  assert.throws(() => packageEvidence(source, destination, "", 5), /budget/);
});

test("symbolic screenshot parents cannot bypass per-file validation", (t) => {
  const { source, destination } = fixture(t);
  const elsewhere = resolve(source, "elsewhere");
  mkdirSync(resolve(elsewhere, "admin"), { recursive: true });
  writeFileSync(resolve(elsewhere, "admin", "home.png"), "private");
  symlinkSync(elsewhere, resolve(source, "screenshots"));
  assert.throws(() => packageEvidence(source, destination, ""), /symbolic/);
});

test("authorization headers are rejected even in allowed logs", (t) => {
  const { source, destination } = fixture(t);
  writeFileSync(
    resolve(source, "api.log"),
    "Authorization: Bearer synthetic-private-token",
  );
  assert.throws(
    () => packageEvidence(source, destination, ""),
    /Authentication/,
  );
});
