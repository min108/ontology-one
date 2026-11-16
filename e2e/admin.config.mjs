import base from "./playwright.config.mjs";
import { defineConfig } from "./runtime.mjs";
export default defineConfig({
  ...base,
  projects: [
    { name: "foundation-admin", testMatch: "**/foundation-admin.spec.mjs" },
  ],
  fullyParallel: false,
  workers: 1,
});
