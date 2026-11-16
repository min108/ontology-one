import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { test, expect } from "./runtime.mjs";
import { authDirectory, evidence, target } from "./fixture.mjs";

if (target !== "legacy")
  throw new Error("Retirement screenshots must use the real legacy target");

async function capture(page, testInfo, name, content) {
  await page.waitForLoadState("networkidle");
  await expect(page.getByText("内容暂时未能加载", { exact: true })).toHaveCount(
    0,
  );
  for (const input of await page.locator('input[type="password"]').all())
    await expect(input).toHaveValue("");
  await page.evaluate(() => document.fonts.ready);
  const directory = resolve(evidence, "screenshots", "legacy-replacements");
  mkdirSync(directory, { recursive: true });
  const path = resolve(directory, name + ".png");
  await content.screenshot({ path, animations: "disabled" });
  await testInfo.attach(name, { path, contentType: "image/png" });
}

test.describe("Original Overview", () => {
  test.use({ storageState: resolve(authDirectory, "modeler.json") });
  test("real four measures and retained original content", async ({
    page,
  }, testInfo) => {
    await page.goto("/");
    await expect(
      page.getByRole("heading", { name: "总览驾驶舱", exact: true }),
    ).toBeVisible();
    const measures = await page.evaluate(async () => {
      const identity = await fetch("/api/auth/session", {
        credentials: "include",
      });
      if (!identity.ok) throw new Error("Session unavailable");
      const session = await identity.json();
      const response = await fetch("/api/overview", {
        credentials: "include",
        headers: { "X-Workspace-Id": session.workspace.tenant_id },
      });
      if (!response.ok) throw new Error("Overview unavailable");
      const data = await response.json();
      // Deliberately export only non-sensitive counts, never the session response.
      return {
        tenant: session.workspace.tenant_id,
        assets: data.assets,
        relations: data.relations,
        organizations: data.counts.organization || 0,
        rows: data.data_rows,
      };
    });
    await expect(page.locator(".metric-grid .stat")).toHaveCount(4);
    for (const [index, key] of [
      "assets",
      "relations",
      "organizations",
      "rows",
    ].entries())
      await expect(
        page.locator(".metric-grid .stat strong").nth(index),
      ).toHaveText(measures[key].toLocaleString("zh-CN"));
    for (const title of [
      "业务领域覆盖",
      "近期沉淀成果",
      "重点工作专题",
      "工作动态",
    ])
      await expect(
        page.getByRole("heading", { name: new RegExp(title) }),
      ).toBeVisible();
    await expect(page.locator(".hero-overview")).toBeVisible();
    await expect(page.locator(".quick-entry")).toHaveCount(3);
    await testInfo.attach("actual-overview-counts", {
      body: JSON.stringify(measures, null, 2),
      contentType: "application/json",
    });
    await capture(
      page,
      testInfo,
      "overview-modeler",
      page.locator("main").first(),
    );
  });
});

test.describe("Original Login", () => {
  test.use({ storageState: { cookies: [], origins: [] } });
  test("empty unauthenticated login", async ({ page }, testInfo) => {
    await page.goto("/");
    await expect(
      page.getByRole("heading", {
        name: "欢迎使用集团本体管理平台",
        exact: true,
      }),
    ).toBeVisible();
    await expect(page.getByLabel("账号", { exact: true })).toHaveValue("");
    await expect(page.getByLabel("密码", { exact: true })).toHaveValue("");
    await expect(
      page.getByRole("button", { name: "登录平台", exact: true }),
    ).toBeDisabled();
    await capture(page, testInfo, "login-empty", page.locator(".auth-screen"));
  });
});

test.describe("Original administrator pages", () => {
  test.use({ storageState: resolve(authDirectory, "admin.json") });
  test("security with current device and empty password form", async ({
    page,
  }, testInfo) => {
    await page.goto("/security");
    await expect(
      page.getByRole("heading", { name: "账号安全", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "修改登录密码", exact: true }),
    ).toBeVisible();
    await expect(
      page.locator(".session-row").filter({ hasText: "当前设备" }),
    ).toBeVisible();
    await capture(
      page,
      testInfo,
      "security-admin",
      page.locator("main").first(),
    );
  });
  test("settings with real service status and backup entry", async ({
    page,
  }, testInfo) => {
    await page.goto("/settings");
    await expect(
      page.getByRole("heading", { name: "系统设置", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "统一智能服务", exact: true }),
    ).toBeVisible();
    await expect(
      page.locator(".check-row").filter({ hasText: "平台业务服务" }),
    ).toContainText("运行中");
    await expect(
      page.getByRole("button", { name: "导出工作区备份", exact: true }),
    ).toBeEnabled();
    await capture(
      page,
      testInfo,
      "settings-admin",
      page.locator("main").first(),
    );
  });
});

test.describe("Original HQ administration", () => {
  test.use({ storageState: resolve(authDirectory, "superadmin.json") });
  test("actual account directory with no mutations", async ({
    page,
  }, testInfo) => {
    await page.goto("/administration");
    await expect(
      page.getByRole("heading", { name: "账号与权限管理", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("columnheader", { name: "姓名与账号", exact: true }),
    ).toBeVisible();
    await expect(page.locator(".admin-table tbody tr").first()).toBeVisible();
    await expect(
      page.getByRole("button", { name: "开通账号", exact: true }),
    ).toBeEnabled();
    await capture(
      page,
      testInfo,
      "administration-hq",
      page.locator("main").first(),
    );
  });
});
