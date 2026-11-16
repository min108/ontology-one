import { resolve } from "node:path";
import { readFileSync } from "node:fs";
import { defineConfig } from "./runtime.mjs";
import { evidence, target } from "./fixture.mjs";
import { acceptanceSelection } from "./projects.mjs";
import { validateVisualPolicy } from "./visual-policy.mjs";

const visualPolicy = validateVisualPolicy(
  JSON.parse(
    readFileSync(new URL("./visual-policy.json", import.meta.url), "utf8"),
  ),
);

export default defineConfig({
  testDir: resolve("e2e"),
  ...acceptanceSelection(
    target,
    process.env.E2E_HARNESS_ACCEPTANCE === "1",
    process.env.E2E_VISUAL_ACCEPTANCE === "1",
    process.env.E2E_VISUAL_ONLY === "1",
  ),
  snapshotPathTemplate: resolve(
    process.env.E2E_VISUAL_BASELINE_DIR || "e2e/visual-baselines",
    "{arg}{ext}",
  ),
  updateSnapshots: visualPolicy.updateSnapshots,
  fullyParallel: true,
  workers: Number(process.env.E2E_WORKERS || 3),
  retries: 0,
  timeout: 45_000,
  expect: { timeout: 10_000 },
  globalSetup: resolve("e2e/setup.mjs"),
  outputDir: resolve(evidence, "results"),
  reporter: [
    ["list"],
    ["json", { outputFile: resolve(evidence, "results.json") }],
    ["html", { outputFolder: resolve(evidence, "report"), open: "never" }],
  ],
  use: {
    baseURL: process.env.E2E_BASE_URL || "http://127.0.0.1:8765",
    viewport: { width: 1366, height: 768 },
    locale: "zh-CN",
    timezoneId: "Asia/Shanghai",
    reducedMotion: "reduce",
    colorScheme: "light",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    launchOptions: process.env.E2E_CHROMIUM
      ? { executablePath: process.env.E2E_CHROMIUM }
      : {},
  },
});
