import assert from "node:assert/strict";
import { test } from "node:test";
import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { resolve } from "node:path";
import {
  validateVisualPolicy,
  assertCanonicalCapture,
  visualResultLabel,
} from "./visual-policy.mjs";

test("default policy compares and cannot accept missing canonical images", () => {
  assert.deepEqual(validateVisualPolicy({ mode: "compare" }), {
    mode: "compare",
    updateSnapshots: "none",
  });
  assert.equal(visualResultLabel({ mode: "compare" }), "acceptance");
  for (const policy of [
    {},
    { mode: "new" },
    { mode: "capture" },
    { mode: "capture", reason: "" },
  ])
    assert.throws(() => validateVisualPolicy(policy), /policy/);
});
test("capture requires an explicit reason and is never labeled final acceptance", () => {
  const policy = { mode: "capture", reason: "R1 canonical baseline bootstrap" };
  assert.equal(validateVisualPolicy(policy).updateSnapshots, "all");
  assert.equal(visualResultLabel(policy), "baseline-capture-not-acceptance");
  assertCanonicalCapture({
    githubActions: "true",
    browserVersion: "159.0.8154.6",
  });
  for (const options of [
    {},
    { githubActions: "true", browserVersion: "156.0.8078.79" },
    {
      githubActions: "true",
      browserVersion: "159.0.8154.6",
      executablePath: "/usr/bin/chromium",
    },
  ])
    assert.throws(() => assertCanonicalCapture(options), /canonical/);
});

test("Playwright compare mode fails a missing image without writing a baseline (no browser needed)", (t) => {
  mkdirSync("work", { recursive: true });
  const root = mkdtempSync(resolve("work", "missing-visual-contract-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const require = createRequire(
    resolve(process.env.E2E_DEPENDENCY_ROOT || "frontend", "package.json"),
  );
  const playwright = require.resolve("@playwright/test");
  const cli = require.resolve("@playwright/test/cli");
  const image = resolve(root, "baselines", "absent.png");
  writeFileSync(
    resolve(root, "test.config.cjs"),
    `module.exports={testDir:__dirname, testMatch:'missing.spec.cjs',workers:1,retries:0,reporter:'line',updateSnapshots:'none',snapshotPathTemplate:${JSON.stringify(resolve(root, "baselines", "{arg}{ext}"))}};`,
  );
  writeFileSync(
    resolve(root, "missing.spec.cjs"),
    `const {test,expect}=require(${JSON.stringify(playwright)});test('missing image',()=>{expect(Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII=','base64')).toMatchSnapshot('absent.png')});`,
  );
  const result = spawnSync(
    process.execPath,
    [cli, "test", "--config", resolve(root, "test.config.cjs")],
    { cwd: root, encoding: "utf8", timeout: 30_000 },
  );
  assert.equal(result.status, 1, result.stderr);
  assert.match(
    result.stdout + result.stderr,
    /snapshot.*doesn't exist|snapshot.*missing/i,
  );
  assert.equal(existsSync(image), false);
});
