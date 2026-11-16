import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdtempSync, readFileSync, existsSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  zoomPreferences,
  calibratedWindow,
  assertNativeMetrics,
  launchNativeContext,
} from "./native-zoom.mjs";

const normal = {
  outerWidth: 1366,
  outerHeight: 768,
  innerWidth: 1366,
  innerHeight: 681,
  devicePixelRatio: 1,
  visualScale: 1,
};
test("native zoom uses Chromium's per-partition map, never the ineffective scalar", () => {
  assert.deepEqual(zoomPreferences(1), {});
  assert.deepEqual(zoomPreferences(1.25), {
    partition: { default_zoom_level: { x: Math.log(1.25) / Math.log(1.2) } },
  });
  assert.throws(() => zoomPreferences(0), /zoom/);
});
test("window height derives from the running browser rather than a fixed chrome constant", () => {
  assert.deepEqual(calibratedWindow(normal, 1366, 768), {
    width: 1366,
    height: 855,
  });
  assert.deepEqual(
    calibratedWindow({ ...normal, innerHeight: 700 }, 1920, 1080),
    { width: 1920, height: 1148 },
  );
  assert.throws(
    () => calibratedWindow({ ...normal, devicePixelRatio: 1.25 }, 1366, 768),
    /calibration/,
  );
});
test("native metrics require layout zoom, stable visual scale and real PNG dimensions", () => {
  const actual = {
    ...normal,
    outerHeight: 855,
    innerWidth: 1093,
    innerHeight: 614,
    devicePixelRatio: 1.25,
  };
  const expected = { width: 1366, height: 768, zoom: 1.25 };
  assertNativeMetrics(actual, expected, { width: 1366, height: 768 });
  for (const patch of [
    { devicePixelRatio: 1 },
    { visualScale: 1.25 },
    { innerWidth: 1366 },
    { innerHeight: 768 },
  ])
    assert.throws(
      () =>
        assertNativeMetrics({ ...actual, ...patch }, expected, {
          width: 1366,
          height: 768,
        }),
      /native/,
    );
  for (const cropped of [
    { width: 1093, height: 614 },
    { width: 874, height: 491 },
  ])
    assert.throws(() => assertNativeMetrics(actual, expected, cropped), /PNG/);
});

test("native launcher always selects full Chromium while allowing explicit local executable diagnostics", async () => {
  const root = mkdtempSync(join(tmpdir(), "native-launch-contract-"));
  try {
    for (const executablePath of [undefined, "/diagnostic/chromium"]) {
      let profile;
      let closed = false;
      const chromium = {
        async launchPersistentContext(directory, options) {
          profile = directory;
          assert.equal(options.channel, "chromium");
          assert.equal(options.executablePath, executablePath);
          assert.equal(options.headless, true);
          assert.equal(options.viewport, null);
          assert.deepEqual(options.args, ["--window-size=1366,855"]);
          assert.deepEqual(
            JSON.parse(
              readFileSync(join(directory, "Default", "Preferences"), "utf8"),
            ),
            zoomPreferences(1.25),
          );
          return {
            async close() {
              closed = true;
            },
          };
        },
      };
      const launched = await launchNativeContext(
        chromium,
        { channel: "chrome", executablePath },
        { width: 1366, height: 855 },
        1.25,
        root,
      );
      await launched.close();
      assert.equal(closed, true);
      assert.equal(existsSync(profile), false);
    }
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
