import base from "./playwright.config.mjs";
import { defineConfig } from "./runtime.mjs";
export default defineConfig({
  ...base,
  projects: [
    {
      name: "foundation-identity",
      testMatch: "**/foundation-identity.spec.mjs",
    },
  ],
  fullyParallel: false,
  workers: 1,
});
