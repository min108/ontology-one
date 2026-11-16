import assert from "node:assert/strict";
import { test } from "node:test";
import { runInNewContext } from "node:vm";
import {
  captureNativeViewport,
  nativeScreenshotMethod,
} from "./native-screenshot.mjs";

for (const failure of [null, "session", "capture", "restore"]) {
  test(`physical screenshot restores temporary state and detaches after ${failure || "success"}`, async () => {
    const calls = [];
    const png = Buffer.from(
      "synthetic screenshot transport contract, not a real image",
    );
    const page = {
      async evaluateHandle(_prepare, kinds) {
        assert.deepEqual(kinds, ["timestamp", "request-id"]);
        calls.push("prepare");
        return {
          async evaluate(_restore) {
            calls.push("restore");
            if (failure === "restore") throw new Error(failure);
          },
          async dispose() {
            calls.push("dispose");
          },
        };
      },
      context() {
        return {
          async newCDPSession(actualPage) {
            assert.equal(actualPage, page);
            calls.push("session");
            if (failure === "session") throw new Error(failure);
            return {
              async send(method, options) {
                calls.push("capture");
                assert.equal(method, "Page.captureScreenshot");
                assert.deepEqual(options, {
                  format: "png",
                  fromSurface: true,
                  captureBeyondViewport: false,
                });
                if (failure === "capture") throw new Error(failure);
                return { data: png.toString("base64") };
              },
              async detach() {
                calls.push("detach");
              },
            };
          },
        };
      },
    };
    const capture = captureNativeViewport(page, {
      maskKinds: ["timestamp", "request-id"],
    });
    if (failure) await assert.rejects(capture, new RegExp(failure));
    else assert.deepEqual(await capture, png);
    assert.deepEqual(
      calls,
      failure === "session"
        ? ["prepare", "session", "restore", "dispose"]
        : ["prepare", "session", "capture", "restore", "dispose", "detach"],
    );
  });
}
test("business content and arbitrary selectors cannot become screenshot masks", async () => {
  assert.equal(nativeScreenshotMethod, "cdp-physical-viewport");
  for (const kind of ["business", "*", 'timestamp"], body'])
    await assert.rejects(
      captureNativeViewport({}, { maskKinds: [kind] }),
      /Only explicitly marked/,
    );
});

for (const failCapture of [false, true]) {
  test(`animation phases are deterministic and original running/paused states restore (${failCapture ? "failure" : "success"})`, async () => {
    const makeAnimation = (playState, currentTime, endTime) => ({
      playState,
      currentTime,
      playbackRate: 1.5,
      effect: { getComputedTiming: () => ({ endTime }) },
      pause() {
        this.playState = "paused";
      },
      play() {
        this.playState = "running";
      },
    });
    const animations = [
      makeAnimation("running", 123, 1000),
      makeAnimation("running", 987, Infinity),
      makeAnimation("paused", 456, 1000),
      makeAnimation("paused", 654, Infinity),
      makeAnimation("finished", 1000, 1000),
    ];
    const original = animations.map(
      ({ playState, currentTime, playbackRate }) => ({
        playState,
        currentTime,
        playbackRate,
      }),
    );
    const nodes = new Set();
    let detached = false;
    const document = {
      getAnimations: () => animations,
      createElement: () => {
        const node = {
          dataset: {},
          remove() {
            nodes.delete(node);
          },
        };
        return node;
      },
      documentElement: {
        append(node) {
          nodes.add(node);
        },
      },
      querySelectorAll: () => [],
    };
    const page = {
      async evaluateHandle(prepare, kinds) {
        const state = runInNewContext(`(${prepare.toString()})`, { document })(
          kinds,
        );
        return {
          evaluate: async (restore) => restore(state),
          dispose: async () => {},
        };
      },
      context: () => ({
        newCDPSession: async () => ({
          async send() {
            assert.deepEqual(
              animations.map(({ currentTime }) => currentTime),
              [1000, 0, 1000, 0, 1000],
            );
            assert(
              animations
                .slice(0, 4)
                .every(({ playState }) => playState === "paused"),
            );
            if (failCapture) throw new Error("Synthetic capture failure");
            return {
              data: Buffer.from("synthetic transport").toString("base64"),
            };
          },
          async detach() {
            detached = true;
          },
        }),
      }),
    };
    if (failCapture)
      await assert.rejects(
        captureNativeViewport(page),
        /Synthetic capture failure/,
      );
    else await captureNativeViewport(page);
    assert.deepEqual(
      animations.map(({ playState, currentTime, playbackRate }) => ({
        playState,
        currentTime,
        playbackRate,
      })),
      original,
    );
    assert.equal(nodes.size, 0);
    assert.equal(detached, true);
  });
}
