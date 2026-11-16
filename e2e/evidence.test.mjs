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
  mkdirSync("work", { recursive: true });
  const root = mkdtempSync(resolve("work", "evidence-contract-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const source = resolve(root, "source");
  mkdirSync(source);
  return { source, destination: resolve(root, "packages") };
}

test("visual evidence is independently readable per viewport and page, including axe and native metrics", (t) => {
  const { source, destination } = fixture(t);
  for (const mode of ["simple", "professional"]) {
    const directory = resolve(
      source,
      "visual",
      "1366-native125",
      `${mode}--home`,
    );
    mkdirSync(directory, { recursive: true });
    writeFileSync(resolve(directory, "actual.png"), "synthetic PNG");
    writeFileSync(
      resolve(directory, "metrics.json"),
      '{"devicePixelRatio":1.25}',
    );
    writeFileSync(resolve(directory, "axe.json"), '{"violations":[]}');
  }
  const index = packageEvidence(source, destination, "complete-url");
  assert.equal(index.packages["visual-1366-native125-home"].files.length, 6);
  assert.equal(index.completeArtifact, "complete-url");
});

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

test("the package index also counts against the download budget", (t) => {
  const { source, destination } = fixture(t);
  mkdirSync(resolve(source, "screenshots/admin"), { recursive: true });
  for (let index = 0; index < 10; index++)
    writeFileSync(resolve(source, `screenshots/admin/${index}.png`), "");
  assert.throws(() => packageEvidence(source, destination, "", 200), /budget/);
});
