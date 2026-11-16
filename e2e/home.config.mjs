import { defineConfig } from "./runtime.mjs";
import shared from "./playwright.config.mjs";

// Isolated package acceptance; the final integrated suite retains all legacy routes.
export default defineConfig({
  ...shared,
  fullyParallel: false,
  workers: 1,
  projects: [
    {
      name: "foundation-home",
      testMatch: "**/foundation-home.spec.mjs",
      workers: 1,
      fullyParallel: false,
    },
  ],
});
