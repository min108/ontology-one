import assert from "node:assert/strict";
import { test } from "node:test";
import { openAuthenticatedVisualPage } from "./visual-auth.mjs";

function scenario(status = 200) {
  const calls = [];
  const listeners = {};
  const account = { username: "synthetic-user", password: "synthetic-only" };
  const page = {
    on(event, handler) {
      listeners[event] = handler;
    },
    async evaluate(callback) {
      assert.match(callback.toString(), /navigator.userAgent/);
      calls.push("user-agent");
      return "Canonical Chromium user agent";
    },
    async goto(url) {
      assert.equal(url, "/");
      calls.push("navigate");
      listeners.console({
        type: () => "error",
        text: () => "Failed to load resource: 401",
      });
      listeners.console({ type: () => "warning", text: () => "warning" });
      listeners.pageerror(new Error("Application error"));
    },
  };
  const context = {
    request: {
      async post(url, options) {
        assert.equal(url, "/api/auth/login");
        assert.deepEqual(options.data, account);
        assert.deepEqual(options.headers, {
          "User-Agent": "Canonical Chromium user agent",
        });
        calls.push("login");
        return {
          status: () => status,
          async dispose() {
            calls.push("dispose");
          },
          async json() {
            throw new Error(
              "Authentication response must not be read or logged",
            );
          },
        };
      },
    },
  };
  return { account, page, context, calls };
}

test("visual authentication uses the same context before navigation and preserves every page error", async () => {
  const state = scenario();
  const problems = await openAuthenticatedVisualPage(
    state.context,
    state.page,
    state.account,
  );
  assert.deepEqual(state.calls, ["user-agent", "login", "dispose", "navigate"]);
  assert.deepEqual(problems, [
    "Failed to load resource: 401",
    "Application error",
  ]);
});

test("failed visual authentication disposes its response and cannot navigate or expose credentials", async () => {
  const state = scenario(401);
  await assert.rejects(
    openAuthenticatedVisualPage(state.context, state.page, state.account),
    {
      message: "Visual fixture login failed (HTTP 401)",
    },
  );
  assert.deepEqual(state.calls, ["user-agent", "login", "dispose"]);
});
