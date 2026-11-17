import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdtempSync, writeFileSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { compareVisualFiles, describePng } from "./r3-visual-diagnostics.mjs";
const png = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII=",
  "base64",
);
test("D8/D9 visual compare requires an existing readable baseline and never writes it", () => {
  const dir = mkdtempSync(join(tmpdir(), "r3-visual-")),
    path = join(dir, "baseline.png");
  try {
    assert.throws(() => compareVisualFiles(png, path), /ENOENT/);
    assert.equal(existsSync(path), false);
    writeFileSync(path, "not an image");
    assert.throws(() => compareVisualFiles(png, path), /Invalid PNG/);
    writeFileSync(path, png);
    assert.equal(compareVisualFiles(png, path).identical, true);
    assert.equal(describePng(png).width, 1);
    // Different metadata bytes are reported without a pixel-difference rejection.
    const changed = Buffer.concat([png, Buffer.from("review metadata")]);
    const result = compareVisualFiles(changed, path);
    assert.equal(result.mode, "review-only");
    assert.equal(result.identical, false);
    assert.equal(result.dimensions_equal, true);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
