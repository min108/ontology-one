import assert from "node:assert/strict";
import { test } from "node:test";
import {
  zoomPreferences,
  calibratedWindow,
  assertNativeMetrics,
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
  assertNativeMetrics(actual, expected, { width: 1093, height: 614 });
  for (const patch of [
    { devicePixelRatio: 1 },
    { visualScale: 1.25 },
    { innerWidth: 1366 },
    { innerHeight: 768 },
  ])
    assert.throws(
      () =>
        assertNativeMetrics({ ...actual, ...patch }, expected, {
          width: 1093,
          height: 614,
        }),
      /native/,
    );
  assert.throws(
    () => assertNativeMetrics(actual, expected, { width: 1366, height: 768 }),
    /PNG/,
  );
});
