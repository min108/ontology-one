import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import {
  visualPages,
  visualLayouts,
  visualModes,
  assertVisualInventory,
  seriousViolations,
  visualAuditHistory,
} from "./visual-contract.mjs";

test("visual audit selects exactly one immutable original fixture event, not latest randomized sessions", () => {
  const control = JSON.parse(
    readFileSync("tests/fixtures/v070/control.json", "utf8"),
  );
  const rows = control.tables.sec_audit.filter(
    (row) =>
      row.action === visualAuditHistory.action &&
      row.tenant_id === visualAuditHistory.tenant_id &&
      row.outcome === visualAuditHistory.outcome,
  );
  assert.equal(rows.length, 1);
  const actual = Object.fromEntries(
    Object.keys(visualAuditHistory).map((key) => [key, String(rows[0][key])]),
  );
  assert.deepEqual(actual, visualAuditHistory);
});

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
