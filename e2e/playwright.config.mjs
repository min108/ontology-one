import { resolve } from "node:path";
import { defineConfig } from "./runtime.mjs";
import { evidence, target } from "./fixture.mjs";

export default defineConfig({
  testDir: resolve("e2e"),
  testMatch: target === "legacy" ? "**/legacy.spec.mjs" : "**/*.spec.mjs",
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
