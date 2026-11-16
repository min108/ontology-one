import assert from "node:assert/strict";
import { test } from "node:test";
import { createRequire } from "node:module";
import { spawnSync } from "node:child_process";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { resolve } from "node:path";
import { acceptanceSelection } from "./projects.mjs";

test("legacy keeps its original single project and exact retained spec", () => {
  assert.deepEqual(acceptanceSelection("legacy", false), {
    testMatch: "**/legacy.spec.mjs",
  });
});
test("foundation pages exclude the opt-in SDK file", () => {
  const { projects } = acceptanceSelection("foundation", false);
  assert.equal(projects.length, 3);
  assert.deepEqual(projects[0].testMatch, [
    "**/legacy.spec.mjs",
    "**/foundation-shell.spec.mjs",
  ]);
});
test("SDK runs only after pages, sequential home and sequential identity succeed", () => {
  const { projects } = acceptanceSelection("foundation", true);
  assert.equal(projects.length, 4);
  const pages = projects.find((project) => project.name === "foundation-pages");
  const home = projects.find((project) => project.name === "foundation-home");
  const identity = projects.find(
    (project) => project.name === "foundation-identity",
  );
  const sdk = projects.find((project) => project.name === "harness-sdk");
  assert(pages);
  assert.deepEqual(
    projects.map((project) => project.name),
    [
      "foundation-pages",
      "foundation-home",
      "foundation-identity",
      "harness-sdk",
    ],
  );
  assert.deepEqual(home.dependencies, [pages.name]);
  assert.deepEqual(identity.dependencies, [home.name]);
  assert.deepEqual(sdk.dependencies, [identity.name]);
  for (const project of [home, identity]) {
    assert.equal(project.workers, 1);
    assert.equal(project.fullyParallel, false);
    assert.equal(project.mode, undefined); // Default mode continues remaining tests after a failure.
  }
  assert.equal(home.testMatch, "**/foundation-home.spec.mjs");
  assert.equal(identity.testMatch, "**/foundation-identity.spec.mjs");
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
    [
      "foundation-pages",
      "foundation-home",
      "foundation-identity",
      "foundation-visual",
      "harness-sdk",
    ],
  );
  assert.deepEqual(projects[3].dependencies, ["foundation-identity"]);
  assert.equal(projects[3].workers, 1);
  assert.equal(projects[3].fullyParallel, false);
  assert.deepEqual(projects[4].dependencies, ["foundation-visual"]);
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

test("a failed Home case does not skip later Home cases or let identity/SDK run early (no browser)", (t) => {
  mkdirSync("work", { recursive: true });
  const root = mkdtempSync(resolve("work", "project-order-contract-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const require = createRequire(
    resolve(process.env.E2E_DEPENDENCY_ROOT || "frontend", "package.json"),
  );
  const playwright = require.resolve("@playwright/test");
  const cli = require.resolve("@playwright/test/cli");
  const output = resolve(root, "results.json");
  const marker = resolve(root, "executed.txt");
  const prelude = `import {createRequire} from 'node:module';import {appendFileSync} from 'node:fs';const {test,expect}=createRequire(import.meta.url)(${JSON.stringify(playwright)});`;
  for (const file of ["legacy", "foundation-shell"])
    writeFileSync(
      resolve(root, `${file}.spec.mjs`),
      `${prelude}test('page passes',()=>{});`,
    );
  writeFileSync(
    resolve(root, "foundation-home.spec.mjs"),
    `${prelude}test('Home intentional failure',()=>expect(1).toBe(2));test('remaining Home still runs',()=>appendFileSync(${JSON.stringify(marker)},${JSON.stringify("home\n")}));`,
  );
  for (const file of ["foundation-identity", "foundation-harness"])
    writeFileSync(
      resolve(root, `${file}.spec.mjs`),
      `${prelude}test('must wait for Home success',()=>appendFileSync(${JSON.stringify(marker)},${JSON.stringify(file + "\n")}));`,
    );
  writeFileSync(
    resolve(root, "config.cjs"),
    `module.exports=${JSON.stringify({ testDir: root, fullyParallel: true, workers: 2, retries: 0, reporter: [["json", { outputFile: output }]], ...acceptanceSelection("foundation", true) })};`,
  );
  const result = spawnSync(
    process.execPath,
    [cli, "test", "--config", resolve(root, "config.cjs")],
    { cwd: root, encoding: "utf8", timeout: 30_000 },
  );
  assert.equal(result.status, 1, result.stderr);
  assert(existsSync(output), result.stdout + result.stderr);
  const stats = JSON.parse(readFileSync(output, "utf8")).stats;
  assert.equal(stats.expected, 3);
  assert.equal(stats.unexpected, 1);
  assert.equal(stats.skipped, 2);
  assert.equal(readFileSync(marker, "utf8"), "home\n");
});
