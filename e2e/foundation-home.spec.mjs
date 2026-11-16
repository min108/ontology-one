import { resolve } from "node:path";
import { mkdirSync } from "node:fs";
import { test, expect } from "./runtime.mjs";
import { accounts, authDirectory, evidence, fixture } from "./fixture.mjs";

test.describe("R1-6 workbench and intake", () => {
  test.use({ storageState: resolve(authDirectory, "modeler.json") });
  test("search then Enter reaches a real object page", async ({ page }) => {
    await page.goto("/");
    await expect(
      page.getByRole("heading", { name: "工作台", exact: true }),
    ).toBeVisible();
    await page.keyboard.press("Control+k");
    const response = page.waitForResponse(
      (result) =>
        new URL(result.url()).pathname ===
          "/api/v2/capabilities/semantic.search:invoke" &&
        result.request().postDataJSON()?.q === fixture.objects.material,
    );
    const input = page.getByRole("combobox", { name: "查找内容" });
    await input.fill(fixture.objects.material);
    const found = await (await response).json();
    const selected = found.items.findIndex(
      (item) => item.id === fixture.objects.material,
    );
    expect(selected).toBeGreaterThanOrEqual(0);
    await expect(page.getByRole("option")).toHaveCount(found.items.length);
    for (let index = 0; index < selected; index++)
      await input.press("ArrowDown");
    await input.press("Enter");
    await expect(page).toHaveURL(
      new RegExp("/materials/" + encodeURIComponent(fixture.objects.material)),
    );
    await expect(page.locator("[data-legacy-page]")).toBeVisible();
  });
  test("dropping a document transfers the file into the old intake without uploading", async ({
    page,
  }, testInfo) => {
    let submitted = 0;
    page.on("request", (request) => {
      if (
        request.method() === "POST" &&
        new URL(request.url()).pathname === "/api/materials/upload"
      )
        submitted++;
    });
    await page.goto("/");
    await expect(
      page.getByRole("heading", { name: "工作台", exact: true }),
    ).toBeVisible();
    await page.goto("/authority");
    await page
      .locator("[data-legacy-page] header.page-head")
      .getByRole("button", { name: "新建权责规范", exact: true })
      .click();
    const create = page.getByRole("dialog", {
      name: "新建权责规范",
      exact: true,
    });
    await create
      .getByLabel("规范名称", { exact: true })
      .fill(`R1-6 route guard ${testInfo.workerIndex}`);
    await create
      .getByRole("button", { name: "建立编辑稿", exact: true })
      .click();
    await expect(page).toHaveURL((url) =>
      /^\/authority\/[^/?#]+$/.test(url.pathname),
    );
    const title = page.getByLabel("规范名称", { exact: true });
    const edited = "R1-6 unsaved edit retained on cancellation";
    await title.fill(edited);
    const draftUrl = page.url();
    const files = await page.evaluateHandle(() => {
      const transfer = new DataTransfer();
      transfer.items.add(
        new File(["R1-6 document fixture"], "home-transfer.md", {
          type: "text/markdown",
        }),
      );
      return transfer;
    });
    page.once("dialog", (dialog) => dialog.dismiss());
    await page.dispatchEvent("body", "dragenter", { dataTransfer: files });
    await page.dispatchEvent("body", "drop", { dataTransfer: files });
    await expect(page).toHaveURL(draftUrl);
    await expect(title).toHaveValue(edited);
    await expect(
      page.getByText("home-transfer.md", { exact: true }),
    ).toBeVisible();
    expect(submitted).toBe(0);
    page.once("dialog", (dialog) => dialog.accept());
    await page
      .getByRole("button", { name: "继续带入这批文件", exact: true })
      .click();
    await expect(page).toHaveURL(/\/materials\?/);
    const intake = page.getByRole("dialog", { name: "批量上传待整理材料" });
    await expect(intake).toBeVisible();
    await expect(
      intake.getByText("home-transfer.md", { exact: true }),
    ).toBeVisible();
    expect(submitted).toBe(0);
    await files.dispose();
  });
});

async function readWorkspace(page, path = "/api/overview") {
  return page.evaluate(async (path) => {
    const identity = await fetch("/api/auth/session", {
      credentials: "include",
    });
    if (!identity.ok) throw new Error("Session read failed");
    const session = await identity.json();
    const response = await fetch(path, {
      credentials: "include",
      headers: { "X-Workspace-Id": session.workspace.tenant_id },
    });
    if (!response.ok)
      throw new Error("Workspace read failed: " + response.status);
    return {
      tenant: session.workspace.tenant_id,
      manageUsers: session.permissions.manage_users,
      data: await response.json(),
    };
  }, path);
}
async function settledHome(page) {
  await expect(
    page.getByRole("region", { name: "工作台", exact: true }),
  ).toHaveAttribute("aria-busy", "false");
  await expect(page.getByTestId("home-metric")).toHaveCount(4);
}
async function homeCapture(page, testInfo, name) {
  await page.evaluate(() => document.fonts.ready);
  const directory = resolve(evidence, "screenshots", "home-a2");
  mkdirSync(directory, { recursive: true });
  const path = resolve(directory, name + ".png");
  await page.screenshot({ path, fullPage: true, animations: "disabled" });
  await testInfo.attach(name, { path, contentType: "image/png" });
}
async function command(page, id, pathname, query = {}) {
  await page.goto("/");
  await settledHome(page);
  await page.keyboard.press("Control+k");
  const input = page.getByRole("combobox", { name: "查找内容" });
  await input.fill("/" + id);
  await expect(page.getByRole("option")).toHaveCount(1);
  await input.press("Enter");
  await expect(page).toHaveURL(
    (url) =>
      url.pathname === pathname &&
      Object.entries(query).every(
        ([key, value]) => url.searchParams.get(key) === value,
      ),
  );
  await expect(page.getByRole("combobox", { name: "查找内容" })).toHaveCount(0);
  await expect(page.locator("[data-legacy-page]")).toBeVisible();
}

test.describe("R1-6 Overview A.2 operational parity", () => {
  test.use({ storageState: resolve(authDirectory, "modeler.json") });
  test("checks real overview measures, detail navigation, briefing and retained list destinations", async ({
    page,
  }, testInfo) => {
    // This case intentionally covers all four metrics plus five commands and four retained destinations.
    test.setTimeout(120_000);
    await page.goto("/?probe=home-a2#overview");
    await settledHome(page);
    const receipt = await readWorkspace(page),
      overview = receipt.data;
    const metrics = [
      {
        id: "assets",
        value: overview.assets,
        href: "/assets?kind=data",
        pathname: "/assets",
        query: { kind: "data" },
        heading: "业务资产",
      },
      {
        id: "relations",
        value: overview.relations,
        href: "/graph",
        pathname: "/graph",
        query: {},
        heading: "本体图谱",
      },
      {
        id: "organizations",
        value: overview.counts.organization || 0,
        href: "/graph?view=organization",
        pathname: "/graph",
        query: { view: "organization" },
        heading: "本体图谱",
      },
      {
        id: "rows",
        value: overview.data_rows,
        href: "/datasets",
        pathname: "/datasets",
        query: {},
        heading: "数据台账导入",
      },
    ];
    await testInfo.attach("actual-overview-counts", {
      body: JSON.stringify(
        {
          tenant: receipt.tenant,
          metrics: metrics.map(({ id, value }) => ({ id, value })),
        },
        null,
        2,
      ),
      contentType: "application/json",
    });
    for (const [index, metric] of metrics.entries())
      await expect(
        page.getByTestId("home-metric").nth(index).locator("strong"),
      ).toHaveText(metric.value.toLocaleString("zh-CN"));
    await expect(page.locator(".hero-overview")).toHaveCount(0);
    await homeCapture(page, testInfo, "workbench-populated");
    for (const [index, metric] of metrics.entries()) {
      await page.getByTestId("home-metric").nth(index).click();
      await expect(page).toHaveURL(
        (url) =>
          url.pathname === "/" &&
          url.searchParams.get("detail") === metric.id &&
          url.searchParams.get("probe") === "home-a2" &&
          url.hash === "#overview",
      );
      const drawer = page.getByRole("dialog");
      await expect(drawer).toBeVisible();
      await expect(drawer.locator("strong").first()).toHaveText(
        metric.value.toLocaleString("zh-CN"),
      );
      await homeCapture(page, testInfo, "detail-" + metric.id);
      const link = drawer.locator(`a[href="${metric.href}"]`);
      await expect(link).toHaveCount(1);
      await link.click();
      await expect(page).toHaveURL(
        (url) =>
          url.pathname === metric.pathname &&
          Object.entries(metric.query).every(
            ([key, value]) => url.searchParams.get(key) === value,
          ),
      );
      await expect(
        page
          .locator("[data-legacy-page]")
          .getByRole("heading", { name: metric.heading, exact: true }),
      ).toBeVisible();
      await page.goBack();
      await settledHome(page);
      await expect(page).toHaveURL(
        (url) =>
          url.pathname === "/" &&
          url.searchParams.get("detail") === metric.id &&
          url.hash === "#overview",
      );
      await expect(
        page.getByRole("dialog").locator("strong").first(),
      ).toHaveText(metric.value.toLocaleString("zh-CN"));
      await page
        .getByRole("dialog")
        .getByRole("button", { name: /Close|关闭/ })
        .click();
      await expect(page.getByRole("dialog")).not.toBeVisible();
    }
    await page
      .getByRole("link", { name: "打开业务汇报导览", exact: true })
      .click();
    await expect(page).toHaveURL((url) => url.pathname === "/briefing");
    await expect(
      page.getByRole("heading", { name: "业务汇报导览", exact: true }),
    ).toBeVisible();
    for (const entry of [
      {
        id: "materials",
        path: "/materials",
        query: { upload: "1" },
        heading: "素材整理",
        dialog: "批量上传待整理材料",
      },
      {
        id: "datasets",
        path: "/datasets",
        query: { upload: "1" },
        heading: "数据台账导入",
        dialog: "接入数据台账",
      },
      { id: "methods", path: "/applications", query: {}, heading: "本体应用" },
      { id: "projects", path: "/projects", query: {}, heading: "专题工作室" },
      {
        id: "history",
        path: "/maintenance",
        query: { tab: "history" },
        heading: "审核维护",
      },
    ]) {
      await command(page, entry.id, entry.path, entry.query);
      await expect(
        page
          .locator("[data-legacy-page]")
          .getByRole("heading", { name: entry.heading, exact: true }),
      ).toBeVisible();
      if (entry.dialog)
        await expect(
          page.getByRole("dialog", { name: entry.dialog, exact: true }),
        ).toBeVisible();
    }
    await expect(page.locator(".biz-activity").first()).toBeVisible();
    const dataItem = (
      await readWorkspace(
        page,
        "/api/entities?asset_only=true&kind=data&page_size=1",
      )
    ).data.items[0];
    expect(
      overview.domains.some((item) => item.domain === dataItem.domain),
    ).toBe(true);
    await page.goto(
      "/assets?" +
        new URLSearchParams({
          kind: "data",
          domain: dataItem.domain,
          q: dataItem.id,
        }),
    );
    await expect(page).toHaveURL(
      (url) => url.searchParams.get("domain") === dataItem.domain,
    );
    await page
      .locator(`a[href^="/assets/${encodeURIComponent(dataItem.id)}"]`)
      .first()
      .click();
    await expect(page).toHaveURL(
      (url) => url.pathname === "/assets/" + encodeURIComponent(dataItem.id),
    );
    await expect(
      page.getByRole("heading", { name: dataItem.title, exact: true }).first(),
    ).toBeVisible();
    const recent = overview.recent.find((item) => item.kind === "knowledge");
    expect(recent).toBeDefined();
    await page.goto(
      "/assets?" +
        new URLSearchParams({
          kind: "knowledge",
          knowledge_view: "all",
          q: recent.id,
        }),
    );
    await page
      .locator(`a[href^="/assets/${encodeURIComponent(recent.id)}"]`)
      .first()
      .click();
    await expect(page).toHaveURL(
      (url) => url.pathname === "/assets/" + encodeURIComponent(recent.id),
    );
    await expect(
      page.getByRole("heading", { name: recent.title, exact: true }).first(),
    ).toBeVisible();
    const project = overview.projects.find(
      (item) => item.id === fixture.objects.project,
    );
    expect(project).toBeDefined();
    await command(page, "projects", "/projects");
    await page
      .locator(`a[href^="/projects/${encodeURIComponent(project.id)}"]`)
      .first()
      .click();
    await expect(page).toHaveURL(
      (url) => url.pathname === "/projects/" + encodeURIComponent(project.id),
    );
    await expect(
      page.getByRole("heading", { name: project.title, exact: true }).first(),
    ).toBeVisible();
    await command(page, "history", "/maintenance", { tab: "history" });
    await expect(page.locator(".biz-activity").first()).toBeVisible();
  });
});

test.describe("R1-6 empty institution next step", () => {
  test.use({ storageState: { cookies: [], origins: [] } });
  test("actual empty HQ administrator can reach the retained demo workspace", async ({
    page,
  }, testInfo) => {
    await page.goto("/");
    await page
      .getByLabel("账号", { exact: true })
      .fill(accounts.superadmin.username);
    await page
      .getByLabel("密码", { exact: true })
      .fill(accounts.superadmin.password);
    await page.getByRole("button", { name: /登录/ }).click();
    await settledHome(page);
    const receipt = await readWorkspace(page);
    expect(receipt.tenant).toBe("hq");
    expect(receipt.manageUsers).toBe(true);
    expect(receipt.data.assets).toBe(0);
    await expect(
      page.getByTestId("home-metric").first().locator("strong"),
    ).toHaveText("0");
    await expect(
      page.getByText("本机构还没有业务成果，可先查看演示资料。", {
        exact: true,
      }),
    ).toBeVisible();
    await homeCapture(page, testInfo, "workbench-empty-admin");
    await page.getByRole("link", { name: "管理演示资料", exact: true }).click();
    await expect(page).toHaveURL((url) => url.pathname === "/admin/demo");
    await expect(page.locator(".demo-workspace")).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "用模拟资料验证效果", exact: true }),
    ).toBeVisible();
    await page.goBack();
    await settledHome(page);
    await expect(
      page.getByRole("link", { name: "管理演示资料", exact: true }),
    ).toBeVisible();
  });
});
