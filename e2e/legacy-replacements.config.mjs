import { defineConfig } from "./runtime.mjs";
import shared from "./playwright.config.mjs";

// Additional read-only retirement evidence; the original 75-route baseline is unchanged.
export default defineConfig({
  ...shared,
  fullyParallel: false,
  workers: 1,
  projects: [
    {
      name: "legacy-replacements",
      testMatch: "**/legacy-replacements.spec.mjs",
      workers: 1,
      fullyParallel: false,
    },
  ],
});
