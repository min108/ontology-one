import { createRequire } from "node:module";
import { resolve } from "node:path";

// Keep browser dependencies in the frontend's single, pinned dependency graph.
const require = createRequire(
  resolve(process.env.E2E_DEPENDENCY_ROOT || "frontend", "package.json"),
);
export const {
  test,
  expect,
  defineConfig,
  chromium,
} = require("@playwright/test");
