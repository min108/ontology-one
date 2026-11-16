import { writeFileSync } from "node:fs";

export const nativeScreenshotMethod = "cdp-physical-viewport";

// Capture the entire physical surface. Playwright's screenshot clip is expressed
// in CSS pixels and crops native browser zoom before its optional scale division.
export async function captureNativeViewport(
  page,
  { path, maskKinds = [] } = {},
) {
  if (
    !Array.isArray(maskKinds) ||
    maskKinds.some((kind) => !["timestamp", "request-id"].includes(kind))
  )
    throw new Error(
      "Only explicitly marked timestamps and request IDs may be masked",
    );
  const preparation = await page.evaluateHandle((kinds) => {
    const nodes = [];
    const animations = [];
    const restore = () => {
      for (const node of nodes) node.remove();
      for (const { animation, time, playState } of animations) {
        if (animation.playState !== "idle") {
          animation.pause();
          animation.currentTime = time;
          if (playState === "running") animation.play();
        }
      }
    };
    try {
      for (const animation of document.getAnimations()) {
        if (["running", "paused"].includes(animation.playState)) {
          animations.push({
            animation,
            time: animation.currentTime,
            playState: animation.playState,
          });
          // Do not keep a random animation frame in a zero-tolerance baseline.
          // Pausing before seeking avoids finish callbacks and preserves the
          // original animation object for restoration after the screenshot.
          const end = animation.effect?.getComputedTiming().endTime;
          animation.pause();
          animation.currentTime = Number.isFinite(end) ? end : 0;
        }
      }
      const style = document.createElement("style");
      style.dataset.nativeCapture = "temporary";
      style.textContent =
        "*,*::before,*::after{caret-color:transparent!important;animation-play-state:paused!important}";
      nodes.push(style);
      document.documentElement.append(style);
      for (const kind of new Set(kinds)) {
        for (const element of document.querySelectorAll(
          `[data-visual-dynamic="${kind}"]`,
        )) {
          if (getComputedStyle(element).visibility === "hidden") continue;
          for (const rect of element.getClientRects()) {
            const left = Math.max(0, rect.left),
              top = Math.max(0, rect.top);
            const width = Math.min(innerWidth, rect.right) - left;
            const height = Math.min(innerHeight, rect.bottom) - top;
            if (width <= 0 || height <= 0) continue;
            const overlay = document.createElement("div");
            overlay.dataset.nativeCapture = "temporary";
            overlay.setAttribute("aria-hidden", "true");
            overlay.style.cssText = `all:initial!important;position:fixed!important;left:${left}px!important;top:${top}px!important;width:${width}px!important;height:${height}px!important;background:#fff!important;z-index:2147483647!important;pointer-events:none!important;`;
            nodes.push(overlay);
            document.documentElement.append(overlay);
          }
        }
      }
      return { restore };
    } catch (error) {
      restore();
      throw error;
    }
  }, maskKinds);
  let cdp;
  try {
    cdp = await page.context().newCDPSession(page);
    const result = await cdp.send("Page.captureScreenshot", {
      format: "png",
      fromSurface: true,
      captureBeyondViewport: false,
    });
    const png = Buffer.from(result.data, "base64");
    if (path) writeFileSync(path, png);
    return png;
  } finally {
    try {
      await preparation.evaluate((state) => state.restore());
    } finally {
      try {
        await preparation.dispose();
      } finally {
        if (cdp) await cdp.detach();
      }
    }
  }
}
