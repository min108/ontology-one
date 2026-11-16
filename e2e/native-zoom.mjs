import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

export function zoomPreferences(zoom) {
  if (![1, 1.25].includes(zoom)) throw new Error("Unreviewed native zoom");
  return zoom === 1
    ? {}
    : {
        partition: {
          default_zoom_level: { x: Math.log(zoom) / Math.log(1.2) },
        },
      };
}

export function calibratedWindow(metrics, width, height) {
  const chromeHeight = metrics.outerHeight - metrics.innerHeight;
  if (
    metrics.devicePixelRatio !== 1 ||
    metrics.visualScale !== 1 ||
    chromeHeight < 0 ||
    chromeHeight > 200
  )
    throw new Error("Invalid native 100% browser calibration");
  return { width, height: height + chromeHeight };
}

export function assertNativeMetrics(actual, expected, png) {
  const near = (a, b) => Math.abs(a - b) <= 1;
  if (
    actual.devicePixelRatio !== expected.zoom ||
    actual.visualScale !== 1 ||
    actual.outerWidth !== expected.width ||
    !near(actual.innerWidth, expected.width / expected.zoom) ||
    !near(actual.innerHeight, expected.height / expected.zoom)
  )
    throw new Error(
      `Unexpected native zoom metrics: ${JSON.stringify(actual)}`,
    );
  if (png.width !== expected.width || png.height !== expected.height)
    throw new Error(
      "PNG dimensions do not match the complete physical viewport",
    );
}

export async function measureNativePage(page) {
  return page.evaluate(() => ({
    outerWidth,
    outerHeight,
    innerWidth,
    innerHeight,
    devicePixelRatio,
    visualScale: visualViewport.scale,
    visualWidth: visualViewport.width,
    visualHeight: visualViewport.height,
  }));
}

// Profiles contain browser authentication and are always outside uploaded evidence.
export async function launchNativeContext(
  chromium,
  options,
  window,
  zoom,
  privateRoot,
) {
  mkdirSync(privateRoot, { recursive: true });
  const profile = mkdtempSync(resolve(privateRoot, "native-profile-"));
  mkdirSync(resolve(profile, "Default"));
  writeFileSync(
    resolve(profile, "Default", "Preferences"),
    JSON.stringify(zoomPreferences(zoom)),
  );
  try {
    const context = await chromium.launchPersistentContext(profile, {
      ...options,
      // Full Chromium supports the browser profile preferences used for native zoom.
      // An explicit executablePath still takes precedence for local diagnostics only.
      channel: "chromium",
      headless: true,
      viewport: null,
      deviceScaleFactor: undefined,
      args: [`--window-size=${window.width},${window.height}`],
    });
    return {
      context,
      async close() {
        try {
          await context.close();
        } finally {
          rmSync(profile, { recursive: true, force: true });
        }
      },
    };
  } catch (error) {
    rmSync(profile, { recursive: true, force: true });
    throw error;
  }
}
