import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { test, expect } from "./runtime.mjs";
import { authDirectory, evidence } from "./fixture.mjs";
import { layouts } from "./shell-contract.mjs";

// No trace containing session/control credentials; only explicit public evidence is retained.
test.use({ storageState: resolve(authDirectory, "admin.json"), trace: "off" });

async function control(action, method = "GET") {
  const response = await fetch(
    `${process.env.E2E_HARNESS_URL}/control/${action}`,
    {
      method,
      headers: { "X-E2E-Control": process.env.E2E_HARNESS_CONTROL },
    },
  );
  if (!response.ok)
    throw new Error(`Local model fixture control failed (${response.status})`);
  return response.json();
}

async function business(page, path, body) {
  return page.evaluate(
    async ({ path, body }) => {
      const sessionResponse = await fetch("/api/auth/session");
      if (!sessionResponse.ok)
        throw new Error("Acceptance session unavailable");
      const session = await sessionResponse.json();
      const response = await fetch(`/api${path}`, {
        method: body === undefined ? "GET" : "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": session.csrf_token,
          "X-Workspace-Id": session.workspace.tenant_id,
        },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
      if (!response.ok)
        throw new Error(`Business request failed (${response.status})`);
      return response.json();
    },
    { path, body },
  );
}

// NavigationProvider recreates its in-memory visit token after a reload.
// Every other part of the task URL, including query order and hash, stays exact.
function withoutVia(value) {
  const url = new URL(value);
  url.searchParams.delete("via");
  return url.href;
}

test("real SDK task survives closing its global view and is stopped through the UI", async ({
  page,
}, testInfo) => {
  test.setTimeout(90_000);
  const directory = resolve(evidence, "harness-screenshots");
  mkdirSync(directory, { recursive: true });
  const record = {
    status: "failed",
    model_provider: "local_synthetic_messages",
    run_id: null,
    states: [],
    reopening_checks: [],
    screenshots: [],
  };
  let run;
  try {
    await page.goto("/materials");
    await expect(
      page.getByRole("navigation", { name: "工作区导航", exact: true }),
    ).toBeVisible();
    const question = "合同金额大于100000元时，应核实什么审批依据？";
    const retrieval = await business(page, "/ai/ask", {
      question,
      mode: "retrieval",
    });
    expect(
      retrieval.citations.length,
      "The real fixture must provide grounding before SDK invocation",
    ).toBeGreaterThan(0);
    record.grounding_citations = retrieval.citations.length;
    await control("hold", "POST");
    run = await business(page, "/harness/tasks", {
      operation: "ai_ask",
      entity_id: "",
      body: {
        question,
        mode: "online",
      },
    });
    expect(run.id).toMatch(/^[a-f0-9]{32}$/);
    record.run_id = run.id;
    await expect
      .poll(async () => (await control("state")).held_requests)
      .toBeGreaterThan(0);
    const observed = await control("state");
    expect(
      observed.observed_processes.length,
      "Actual agent-owned SDK processes must be observed",
    ).toBeGreaterThan(0);
    record.sdk_processes = observed.observed_processes;
    record.model_requests = observed.model_requests;
    const read = async () => business(page, `/harness/runs/${run.id}`);
    await expect.poll(async () => (await read()).status).toBe("running");
    record.states.push("running");

    const panel = page.locator("details.harness-activity");
    const openRun = async () => {
      await panel.locator('summary[aria-label^="后台任务"]').click();
      await panel
        .getByRole("button", { name: "刷新后台任务", exact: true })
        .click();
      await panel.locator(`a[href*="harness_run=${run.id}"]`).click();
    };
    await openRun();
    const conversation = page.getByRole("region", {
      name: "智能助手处理对话",
      exact: true,
    });
    await expect(conversation).toBeVisible();
    await expect(
      conversation.getByRole("button", { name: "停止本轮处理", exact: true }),
    ).toBeVisible();
    expect(
      await conversation.evaluate((node) =>
        Boolean(node.closest(".legacy-root")),
      ),
    ).toBe(true);
    expect(
      await panel.evaluate((node) => Boolean(node.closest(".legacy-root"))),
    ).toBe(true);
    for (const layout of [
      { name: "1920", width: 1920, height: 1080, zoom: "native 100%" },
      ...layouts,
    ]) {
      await page.setViewportSize({
        width: layout.width,
        height: layout.height,
      });
      await expect(conversation).toBeInViewport();
      await expect(
        conversation.getByRole("button", { name: "停止本轮处理", exact: true }),
      ).toBeInViewport();
      const screenshot = `global-harness-${layout.name}.png`;
      await page.screenshot({
        path: resolve(directory, screenshot),
        animations: "disabled",
      });
      record.screenshots.push({
        file: screenshot,
        viewport: { width: layout.width, height: layout.height },
        zoom: layout.zoom,
      });
    }
    const taskUrl = page.url();
    await page.keyboard.press("Escape");
    await expect(conversation).toBeHidden();
    expect((await read()).status).toBe("running");
    await expect(page).toHaveURL(taskUrl);
    record.reopening_checks.push("escape_closed_while_running");
    await openRun();
    await expect(page).toHaveURL(taskUrl);
    await expect(conversation).toBeVisible();
    await expect(
      conversation.getByRole("button", { name: "停止本轮处理", exact: true }),
    ).toBeVisible();
    expect((await read()).status).toBe("running");
    record.reopening_checks.push("same_url_reopened_while_running");
    const viaDeepLink = new URL(taskUrl);
    viaDeepLink.searchParams.set("via", "harness-reopen-regression");
    viaDeepLink.searchParams.set("reopen_probe", "retained");
    viaDeepLink.hash = "task-reopen";
    const expectedDeepLink = withoutVia(viaDeepLink.href);
    await page.goto(viaDeepLink.href);
    await expect(page).toHaveURL(
      (url) => withoutVia(url.href) === expectedDeepLink,
    );
    await expect(conversation).toBeVisible();
    await expect
      .poll(() => new URL(page.url()).searchParams.get("via"))
      .toBeTruthy();
    expect((await read()).status).toBe("running");
    record.reopening_checks.push("via_deep_link_opened_while_running");
    await page.reload();
    await expect(page).toHaveURL(
      (url) => withoutVia(url.href) === expectedDeepLink,
    );
    await expect(conversation).toBeVisible();
    await expect(
      conversation.getByRole("button", { name: "停止本轮处理", exact: true }),
    ).toBeVisible();
    expect((await read()).status).toBe("running");
    record.reopening_checks.push("deep_link_reloaded_while_running");
    await conversation
      .getByRole("button", { name: "收起智能处理对话", exact: true })
      .click();
    await expect(conversation).toHaveCount(0);
    expect((await read()).status).toBe("running");
    record.states.push("view_closed_while_running");
    await page
      .getByRole("navigation", { name: "工作区导航", exact: true })
      .locator('a[href="/ontology"]')
      .click();
    await openRun();
    await expect(conversation).toBeVisible();
    await expect(
      conversation.locator(".legacy-progress ol li").first(),
    ).toBeVisible();
    const cancelled = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname ===
          `/api/harness/runs/${run.id}/cancel` &&
        response.request().method() === "POST",
    );
    await conversation
      .getByRole("button", { name: "停止本轮处理", exact: true })
      .click();
    record.cancel_http_status = (await cancelled).status();
    expect(record.cancel_http_status).toBe(200);
    await expect.poll(async () => (await read()).status).toBe("cancelled");
    await expect(
      conversation.getByText("本轮处理已停止。", { exact: true }),
    ).toBeVisible();
    await expect(
      conversation.getByRole("button", { name: "停止本轮处理", exact: true }),
    ).toHaveCount(0);
    await expect
      .poll(async () => (await control("state")).observed_processes_reaped)
      .toBe(true);
    record.states.push("cancelled");
    record.sdk_processes_reaped = true;
    record.status = "passed";
    await page.screenshot({
      path: resolve(directory, "global-harness-cancelled.png"),
      animations: "disabled",
    });
  } finally {
    // Cleanup still attempts cancellation if an earlier UI assertion failed.
    if (run && record.status !== "passed") {
      try {
        await business(page, `/harness/runs/${run.id}/cancel`, {});
      } catch {
        record.cleanup_cancel_failed = true;
      }
    }
    await control("release", "POST");
    const path = resolve(evidence, "harness-result.json");
    writeFileSync(path, JSON.stringify(record, null, 2) + "\n");
    await testInfo.attach("real-sdk-synthetic-provider-result", {
      path,
      contentType: "application/json",
    });
  }
});
