import assert from "node:assert/strict";
import { test } from "node:test";
import { secondaryPaths, redirects, layouts } from "./shell-contract.mjs";

test("unrecognized role and workspace never inherit acceptance expectations", () => {
  assert.throws(() => secondaryPaths("/admin", "unknown", []), /role/);
  assert.throws(() => secondaryPaths("/unknown", "admin", []), /workspace/);
});
test("group and administration secondary items require their stated authorization", () => {
  assert(!secondaryPaths("/ontology", "admin", []).includes("/group"));
  assert(
    secondaryPaths("/ontology", "reviewer", ["federation.read"]).includes(
      "/group",
    ),
  );
  for (const role of ["reviewer", "modeler"]) {
    assert(!secondaryPaths("/admin", role, []).includes("/admin/users"));
    assert(
      !secondaryPaths("/admin", role, []).includes("/integration-applications"),
    );
  }
});
test("redirect vectors preserve both query and hash, equivalent layout is labeled honestly", () => {
  for (const [from, to] of redirects) {
    const source = new URL(from, "http://localhost");
    const target = new URL(to, "http://localhost");
    assert.equal(source.search, target.search);
    assert.equal(source.hash, target.hash);
  }
  assert.equal(layouts[1].width, Math.round(layouts[0].width / 1.25));
  assert.match(layouts[1].zoom, /not native/);
});
