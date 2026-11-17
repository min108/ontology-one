import { resolve } from "node:path";
import { defineConfig } from "./runtime.mjs";

export default defineConfig({
  testDir: resolve("e2e"),
  testMatch: "**/r2-mainline.spec.mjs",
  workers: 1,
  retries: 0,
  timeout: 240_000,
  expect: { timeout: 10_000 },
  outputDir: resolve(process.env.E2E_EVIDENCE_DIR, "test-results"),
  reporter: [["list"]],
  use: {
    actionTimeout: 30_000,
    baseURL: process.env.E2E_BASE_URL,
    viewport: { width: 1366, height: 768 },
    locale: "zh-CN",
    timezoneId: "Asia/Shanghai",
    reducedMotion: "reduce",
    trace: "off",
    screenshot: "off",
    launchOptions: process.env.E2E_CHROMIUM
      ? { executablePath: process.env.E2E_CHROMIUM }
      : {},
  },
});
