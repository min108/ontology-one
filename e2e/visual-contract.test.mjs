import assert from "node:assert/strict";
import { test } from "node:test";
import {
  visualPages,
  visualLayouts,
  visualModes,
  assertVisualInventory,
  seriousViolations,
} from "./visual-contract.mjs";

test("visual matrix covers all replacement areas, both views, and actual native zoom", () => {
  assertVisualInventory();
  assert.deepEqual(
    visualPages.map((page) => page.name),
    [
      "home",
      "shell-legacy",
      "security",
      "users",
      "orgs",
      "audit",
      "model",
      "backup",
      "system",
      "demo",
    ],
  );
  assert.deepEqual(visualModes, ["simple", "professional"]);
  assert.equal(visualLayouts[2].zoom, 1.25);
  assert.equal(visualLayouts[2].width, 1366); // Browser window, not a simulated 1093 CSS viewport.
  assert.equal(
    visualPages.filter((page) => page.ready || page.readyButton).length,
    9,
  );
});
test("axe serious and critical findings always fail; lower findings remain in the report", () => {
  const violations = ["minor", "moderate", "serious", "critical"].map(
    (impact) => ({ id: impact, impact }),
  );
  assert.deepEqual(
    seriousViolations({ violations }).map((item) => item.id),
    ["serious", "critical"],
  );
  assert.equal(violations.length, 4);
});
