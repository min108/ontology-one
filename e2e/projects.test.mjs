import assert from "node:assert/strict";
import { test } from "node:test";
import { acceptanceSelection } from "./projects.mjs";

test("legacy keeps its original single project and exact retained spec", () => {
  assert.deepEqual(acceptanceSelection("legacy", false), {
    testMatch: "**/legacy.spec.mjs",
  });
});
test("foundation pages exclude the opt-in SDK file", () => {
  const { projects } = acceptanceSelection("foundation", false);
  assert.equal(projects.length, 1);
  assert.deepEqual(projects[0].testMatch, [
    "**/legacy.spec.mjs",
    "**/foundation-shell.spec.mjs",
  ]);
});
test("SDK runs alone only after the complete pages project succeeds", () => {
  const { projects } = acceptanceSelection("foundation", true);
  assert.equal(projects.length, 2);
  const pages = projects.find((project) => project.name === "foundation-pages");
  const sdk = projects.find((project) => project.name === "harness-sdk");
  assert(pages);
  assert.deepEqual(sdk.dependencies, [pages.name]);
  assert.equal(sdk.workers, 1);
  assert.equal(sdk.fullyParallel, false);
  assert.equal(sdk.testMatch, "**/foundation-harness.spec.mjs");
  assert.equal(pages.dependencies, undefined);
});
test("unreviewed target and legacy SDK combinations are rejected", () => {
  assert.throws(() => acceptanceSelection("unknown", false), /target/);
  assert.throws(() => acceptanceSelection("legacy", true), /foundation/);
});

test("native visual work is sequential after pages and before the deadline-sensitive SDK", () => {
  const { projects } = acceptanceSelection("foundation", true, true);
  assert.deepEqual(
    projects.map((project) => project.name),
    ["foundation-pages", "foundation-visual", "harness-sdk"],
  );
  assert.deepEqual(projects[1].dependencies, ["foundation-pages"]);
  assert.equal(projects[1].workers, 1);
  assert.equal(projects[1].fullyParallel, false);
  assert.deepEqual(projects[2].dependencies, ["foundation-visual"]);
  assert.throws(() => acceptanceSelection("legacy", false, true), /foundation/);
});

test("canonical visual-only scope cannot execute mutation or SDK projects", () => {
  const { projects } = acceptanceSelection("foundation", false, true, true);
  assert.equal(projects.length, 1);
  assert.equal(projects[0].name, "foundation-visual");
  assert.equal(projects[0].dependencies, undefined);
  assert.equal(projects[0].workers, 1);
  assert.throws(
    () => acceptanceSelection("foundation", true, true, true),
    /separate/,
  );
  assert.throws(
    () => acceptanceSelection("legacy", false, true, true),
    /foundation/,
  );
});
