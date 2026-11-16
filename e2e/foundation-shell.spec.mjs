import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { test, expect } from "./runtime.mjs";
import { authDirectory, evidence, roles } from "./fixture.mjs";
import {
  primaryPaths,
  secondaryPaths,
  redirects,
  layouts,
  redirectMatches,
} from "./shell-contract.mjs";

const browserProblems = new WeakMap();
test.beforeEach(async ({ page }) => {
  const problems = [];
  browserProblems.set(page, problems);
  page.on("pageerror", (error) => problems.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") problems.push(message.text());
  });
});
test.afterEach(async ({ page }) => {
  expect(
    browserProblems.get(page),
    "No uncaught or console errors in the Shell",
  ).toEqual([]);
});

const primary = (page) =>
  page.getByRole("navigation", { name: "工作区导航", exact: true });
const secondary = (page) =>
  page.getByRole("navigation", { name: "工作区内导航", exact: true });
const links = (locator) =>
  locator
    .locator("a")
    .evaluateAll((nodes) => nodes.map((node) => node.getAttribute("href")));

async function identity(page) {
  // Tokens remain in memory; return only the identity fields this test needs.
  return page.evaluate(async () => {
    const sessionResponse = await fetch("/api/auth/session");
    if (!sessionResponse.ok) throw new Error("Session read failed");
    const session = await sessionResponse.json();
    const response = await fetch("/api/v2/capabilities/session.whoami:invoke", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": session.csrf_token,
        "X-Workspace-Id": session.workspace.tenant_id,
      },
      body: JSON.stringify({}),
    });
    if (!response.ok) throw new Error("Permission read failed");
    const result = await response.json();
    return {
      role: result.role,
      permissions: result.permissions,
      tenant: result.tenant_id,
    };
  });
}

async function capture(page, role, name, testInfo) {
  const directory = resolve(evidence, "shell-screenshots", role);
  mkdirSync(directory, { recursive: true });
  const path = resolve(directory, `${name}.png`);
  await page.screenshot({ path, fullPage: true, animations: "disabled" });
  await testInfo.attach(name, { path, contentType: "image/png" });
}

for (const role of roles) {
  test.describe(`${role}: foundation shell`, () => {
    test.use({ storageState: resolve(authDirectory, `${role}.json`) });
    test("six primary workspaces and permission-filtered secondary links", async ({
      page,
    }) => {
      await page.goto("/");
      const person = await identity(page);
      expect(person.role).toBe(role);
      await expect(primary(page)).toBeVisible();
      expect(await links(primary(page))).toEqual(primaryPaths);
      for (const path of primaryPaths) {
        await primary(page).locator(`a[href="${path}"]`).click();
        const expected = secondaryPaths(path, role, person.permissions);
        if (expected.length) {
          await expect(secondary(page)).toBeVisible();
          expect(await links(secondary(page))).toEqual(expected);
          await expect(page).toHaveURL((url) => url.pathname === expected[0]);
        } else await expect(secondary(page)).toHaveCount(0);
        await expect(
          primary(page).locator(`a[href="${path}"]`),
        ).toHaveAttribute("aria-current", "page");
      }
    });
    for (const layout of layouts) {
      test(`navigation and content remain usable at ${layout.name}`, async ({
        page,
      }, testInfo) => {
        testInfo.annotations.push({
          type: "viewport",
          description: layout.zoom,
        });
        await page.setViewportSize({
          width: layout.width,
          height: layout.height,
        });
        await page.goto("/materials");
        await expect(primary(page)).toBeVisible();
        await expect(page.locator("[data-legacy-page]")).toBeVisible();
        await page.evaluate(() => document.fonts.ready);
        expect(
          await page.evaluate(
            () => document.documentElement.scrollWidth <= window.innerWidth + 1,
          ),
        ).toBe(true);
        for (const path of primaryPaths)
          await expect(
            primary(page).locator(`a[href="${path}"]`),
          ).toBeInViewport();
        await expect(
          page.getByRole("combobox", { name: "机构", exact: true }),
        ).toBeInViewport();
        await primary(page).locator('a[href="/ontology"]').click();
        await expect(
          secondary(page).locator('a[href="/assets"]'),
        ).toBeVisible();
        await secondary(page).locator('a[href="/graph"]').click();
        await expect(page).toHaveURL((url) => url.pathname === "/graph");
        await capture(page, role, `shell-${layout.name}`, testInfo);
      });
    }
  });
}

test.describe("admin: compatibility redirects", () => {
  test.use({ storageState: resolve(authDirectory, "admin.json") });
  for (const [from, to] of redirects) {
    test(`redirect ${from} preserves query and hash`, async ({ page }) => {
      await page.goto(from);
      await expect(page).toHaveURL((url) => redirectMatches(url, to));
      await expect(page.locator("[data-legacy-page]")).toBeVisible();
    });
  }
});

test.describe("superadmin: workspace transition", () => {
  test.use({ storageState: resolve(authDirectory, "superadmin.json") });
  test("cancel preserves a new draft; confirm switches the real server workspace", async ({
    page,
  }, testInfo) => {
    await page.goto("/authority");
    expect((await identity(page)).tenant).toBe("hq");
    // The supplied authority fixture is published. Create a distinct draft through its real UI.
    await page
      .getByRole("button", { name: "新建权责规范", exact: true })
      .click();
    const create = page.getByRole("dialog", {
      name: "新建权责规范",
      exact: true,
    });
    await create
      .getByLabel("规范名称", { exact: true })
      .fill(`浏览器隔离草稿 ${testInfo.workerIndex}`);
    await create
      .getByRole("button", { name: "建立编辑稿", exact: true })
      .click();
    await expect(page).toHaveURL((url) =>
      /^\/authority\/[^/?#]+$/.test(url.pathname),
    );
    const title = page.getByLabel("规范名称", { exact: true });
    await expect(title).toBeEditable();
    const edited = "切换取消后仍保留的未保存编辑";
    await title.fill(edited);
    const draftUrl = page.url();
    let workspacePosts = 0;
    page.on("request", (request) => {
      if (
        new URL(request.url()).pathname === "/api/auth/workspace" &&
        request.method() === "POST"
      )
        workspacePosts++;
    });
    const choose = async () => {
      await page.getByRole("combobox", { name: "机构", exact: true }).click();
      await page.getByRole("option", { name: "办公厅", exact: true }).click();
    };
    await choose();
    const confirmation = page.getByRole("dialog", {
      name: "切换机构",
      exact: true,
    });
    await expect(confirmation).toBeVisible();
    await confirmation
      .getByRole("button", { name: "取消", exact: true })
      .click();
    await expect(confirmation).toHaveCount(0);
    await expect(title).toHaveValue(edited);
    await expect(title).toBeEditable();
    expect(page.url()).toBe(draftUrl);
    expect((await identity(page)).tenant).toBe("hq");
    expect(workspacePosts).toBe(0);
    await capture(page, "superadmin", "cancel-keeps-edit", testInfo);
    await choose();
    await confirmation
      .getByRole("button", { name: "确认切换", exact: true })
      .click();
    await expect(page).toHaveURL((url) => url.pathname === "/");
    await expect
      .poll(async () => (await identity(page)).tenant)
      .toBe("dept-01");
    expect(workspacePosts).toBe(1);
    await expect(page.getByLabel("规范名称", { exact: true })).toHaveCount(0);
    await capture(page, "superadmin", "workspace-switched", testInfo);
  });
});
