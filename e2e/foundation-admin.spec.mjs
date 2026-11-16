import { resolve } from "node:path";
import { test, expect } from "./runtime.mjs";
import { authDirectory, roles } from "./fixture.mjs";

// Sensitive configuration bodies must never enter trace artifacts.
test.use({ trace: "off", permissions: ["clipboard-read", "clipboard-write"] });
// One worker in admin.config keeps operations ordered; independent cases still run after a failure.
const errors = new WeakMap();
test.beforeEach(async ({ page }) => {
  const found = [];
  errors.set(page, found);
  page.on("pageerror", (error) => found.push(error.message));
});
test.afterEach(async ({ page }) => {
  expect(errors.get(page)).toEqual([]);
});
async function invoke(page, path, body) {
  return page.evaluate(
    async ({ path, body }) => {
      const session = await (await fetch("/api/auth/session")).json();
      const response = await fetch("/api" + path, {
        method: body === undefined ? "GET" : "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": session.csrf_token,
          "X-Workspace-Id": session.workspace.tenant_id,
        },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
      return { status: response.status, body: await response.json() };
    },
    { path, body },
  );
}
async function ready(page, path, title) {
  await page.goto(path);
  await expect(
    page.getByRole("heading", { name: title, level: 1 }),
  ).toBeVisible();
  if (new URL(page.url()).pathname !== "/admin/demo")
    await expect(page.locator('[data-admin-ready="true"]')).toBeVisible();
  await page.waitForLoadState("networkidle");
}
for (const role of roles) {
  test.describe(role, () => {
    test.use({ storageState: resolve(authDirectory, `${role}.json`) });
    test("SET01 model status, role controls and original settings redirect", async ({
      page,
    }) => {
      await ready(page, "/settings", "模型与运行");
      await expect(page).toHaveURL(/\/admin\/model/);
      await expect(
        page.getByLabel("模型接口密钥", { exact: true }),
      ).toHaveValue("");
      if (role !== "admin") {
        await expect(
          page.getByLabel("模型接口密钥", { exact: true }),
        ).toBeDisabled();
        await expect(
          page.getByRole("button", { name: "保存统一配置" }),
        ).toHaveCount(0);
        expect(
          (
            await invoke(page, "/v2/capabilities/model.configure:invoke", {
              base_url: "https://model.test",
              model: "test",
            })
          ).status,
        ).toBe(403);
      } else
        await expect(
          page.getByRole("button", { name: "保存统一配置" }),
        ).toBeEnabled();
    });
    test("SET04-09 backup rights, offline instructions, release link and real system health", async ({
      page,
    }) => {
      const publicRequests = [];
      page.on("request", (request) => {
        if (
          new URL(request.url()).origin !==
          new URL(process.env.E2E_BASE_URL).origin
        )
          publicRequests.push(request.url());
      });
      await ready(page, "/admin/backup", "备份与升级");
      const exportButton = page.getByRole("button", {
        name: "导出工作区备份",
        exact: true,
      });
      if (role === "admin") await expect(exportButton).toBeEnabled();
      else await expect(exportButton).toBeDisabled();
      await expect(page.getByText(/恢复只能离线执行/)).toBeVisible();
      await expect(
        page.getByRole("link", { name: "查看平台发行包" }),
      ).toHaveAttribute(
        "href",
        "https://github.com/min108/ontology-one/releases",
      );
      await expect(
        page.getByRole("button", { name: "导出程序源码包" }),
      ).toHaveCount(0);
      const integrations = page
        .getByRole("navigation", { name: "工作区内导航", exact: true })
        .getByRole("link", { name: "集成应用" });
      await expect(integrations).toHaveCount(role === "admin" ? 1 : 0);
      await ready(page, "/admin/system", "系统信息");
      await expect(
        page.getByRole("heading", { name: "知元 1.0.0", exact: true }),
      ).toBeVisible();
      await expect(page.getByText("运行中", { exact: true })).toBeVisible();
      await expect(page.getByText("已启用", { exact: true })).toHaveCount(0);
      expect(publicRequests).toEqual([]);
    });
    test("SET03 demo route, six downloads, three copyable questions and manifest remain", async ({
      page,
    }) => {
      await ready(page, "/settings#demo", "演示资料");
      expect(new URL(page.url()).pathname).toBe("/admin/demo");
      expect(new URL(page.url()).hash).toBe("#demo");
      await page.getByText("下载小样本，自己练习上传", { exact: true }).click();
      const downloads = page.locator(".demo-downloads a");
      await expect(downloads).toHaveCount(6);
      for (let index = 0; index < 6; index++) {
        await expect(downloads.nth(index)).toHaveAttribute(
          "href",
          /workspace_id=dept-01/,
        );
        const pending = page.waitForEvent("download");
        await downloads.nth(index).click();
        expect(await (await pending).failure()).toBeNull();
      }
      await page.getByText("可以直接试问的问题", { exact: true }).click();
      await expect(
        page.getByRole("button", { name: "复制", exact: true }),
      ).toHaveCount(3);
      for (let index = 0; index < 3; index++) {
        await page
          .getByRole("button", { name: "复制", exact: true })
          .nth(index)
          .click();
        expect(
          (await page.evaluate(() => navigator.clipboard.readText())).length,
        ).toBeGreaterThan(20);
      }
      await expect(
        page.getByRole("link", { name: /查看演示资产/ }),
      ).toBeVisible();
      await expect(page.getByText(/本轮演示资料清单/)).toBeVisible();
      if (role !== "admin")
        await expect(
          page.getByRole("button", { name: "归档未使用的演示资料" }),
        ).toHaveCount(0);
    });
  });
}
test.describe("administrator operations", () => {
  test.use({ storageState: resolve(authDirectory, "admin.json") });
  test("SET01 saves, verifies with actual SDK, clears key and retains typed input on errors", async ({
    page,
  }) => {
    test.setTimeout(90000);
    if (!process.env.E2E_ADMIN_MODEL_URL || !process.env.E2E_ADMIN_MODEL_KEY)
      throw new Error("Local synthetic Messages provider must be running");
    await ready(page, "/admin/model", "模型与运行");
    await page
      .getByText("服务地址与模型（通常无需修改）", { exact: true })
      .click();
    await page
      .getByLabel("模型服务地址", { exact: true })
      .fill(process.env.E2E_ADMIN_MODEL_URL);
    await page.getByLabel("模型名称", { exact: true }).fill("synthetic-e2e");
    await page
      .getByLabel("模型接口密钥", { exact: true })
      .fill(process.env.E2E_ADMIN_MODEL_KEY);
    await page
      .getByRole("button", { name: "保存统一配置", exact: true })
      .click();
    await expect(
      page.getByText("统一配置已保存，可继续验证连接。"),
    ).toBeVisible();
    await expect(page.getByLabel("模型接口密钥", { exact: true })).toHaveValue(
      "",
    );
    const original = await invoke(page, "/skills/harness/status");
    expect(original.body.configured).toBe(true);
    expect(JSON.stringify(original.body)).not.toContain(
      process.env.E2E_ADMIN_MODEL_KEY,
    );
    await page
      .getByRole("button", { name: "验证服务连接", exact: true })
      .click();
    await expect(
      page.getByRole("heading", { name: "连接已验证", exact: true }),
    ).toBeVisible({ timeout: 60000 });
    const verified = await invoke(page, "/v2/system/model");
    expect(verified.body.verified).toBe(true);
    await page.getByLabel("清除已保存的模型密钥", { exact: true }).check();
    await page
      .getByRole("button", { name: "保存统一配置", exact: true })
      .click();
    await expect(
      page.getByLabel("清除已保存的模型密钥", { exact: true }),
    ).toHaveCount(0);
    expect((await invoke(page, "/skills/harness/status")).body.configured).toBe(
      false,
    );
    await page.getByLabel("模型名称", { exact: true }).fill("invalid model");
    await page
      .getByLabel("模型接口密钥", { exact: true })
      .fill("synthetic-unsaved");
    const rejected = page.waitForResponse((response) =>
      response.url().includes("model.configure:invoke"),
    );
    await page
      .getByRole("button", { name: "保存统一配置", exact: true })
      .click();
    expect((await rejected).status()).toBe(422);
    await expect(page.getByLabel("模型接口密钥", { exact: true })).toHaveValue(
      "synthetic-unsaved",
    );
    await expect(
      page.getByRole("button", { name: "重试", exact: true }),
    ).toBeVisible();
  });
  test("SET07-08 real archive download and all online restore calls remain blocked", async ({
    page,
  }) => {
    await ready(page, "/admin/backup", "备份与升级");
    const download = page.waitForEvent("download");
    await page
      .getByRole("button", { name: "导出工作区备份", exact: true })
      .click();
    const file = await download;
    expect(file.suggestedFilename()).toContain("机构备份.zip");
    expect(await file.failure()).toBeNull();
    const response = await invoke(
      page,
      "/v2/capabilities/backup.restore:invoke",
      { archive: "/not-opened.zip", reason: "test" },
    );
    expect(response.status).toBe(409);
    expect(response.body.error.code).toBe("PRECONDITION_FAILED");
    await expect(page.getByText("升级前备份", { exact: true })).toBeVisible();
  });
  test("SET03 query draft preparation keeps the original governance gate", async ({
    page,
  }) => {
    await ready(page, "/admin/demo", "演示资料");
    const status = await invoke(page, "/demo/status");
    expect(status.status).toBe(200);
    const button = page.getByRole("button", {
      name: /准备台账查询草稿|检查并补齐查询草稿/,
    });
    expect(status.body.data_standards_published).toBe(true);
    {
      await button.click();
      await expect(
        page.getByText("台账查询草稿已准备，请完成正常审核。"),
      ).toBeVisible();
      expect(
        (await invoke(page, "/demo/status")).body.mappings.length,
      ).toBeGreaterThan(0);
    }
  });
});
test.describe("headquarters superadministrator", () => {
  test.use({ storageState: resolve(authDirectory, "superadmin.json") });
  test("SET10 source export is visible and downloads a real archive", async ({
    page,
  }) => {
    test.setTimeout(90000);
    await ready(page, "/admin/backup", "备份与升级");
    const pending = page.waitForEvent("download");
    await page
      .getByRole("button", { name: "导出程序源码包", exact: true })
      .click();
    const download = await pending;
    expect(await download.failure()).toBeNull();
    expect(download.suggestedFilename()).toContain("程序源码.zip");
  });
  test("SET03 own-institution demo load, archive and restore retain original ids", async ({
    page,
  }) => {
    await ready(page, "/admin/demo", "演示资料");
    await page
      .getByRole("button", { name: "加载模拟资料", exact: true })
      .click();
    await page.getByRole("button", { name: "确认准备", exact: true }).click();
    await expect(
      page.getByRole("button", { name: "归档未使用的演示资料", exact: true }),
    ).toBeEnabled();
    const before = await invoke(page, "/demo/status");
    await page
      .getByRole("button", { name: "归档未使用的演示资料", exact: true })
      .click();
    await page.getByRole("button", { name: "确认归档", exact: true }).click();
    await expect(
      page.getByRole("button", { name: "恢复原有演示资料", exact: true }),
    ).toBeEnabled();
    await page
      .getByRole("button", { name: "恢复原有演示资料", exact: true })
      .click();
    await page.getByRole("button", { name: "确认准备", exact: true }).click();
    await expect(
      page.getByRole("button", { name: "归档未使用的演示资料", exact: true }),
    ).toBeEnabled();
    const after = await invoke(page, "/demo/status");
    expect(after.body.items.map((item) => item.id).sort()).toEqual(
      before.body.items.map((item) => item.id).sort(),
    );
  });
  test("SET01 cross-institution maintenance cannot validate the model", async ({
    page,
  }) => {
    await page.goto("/admin/model");
    expect(
      (await invoke(page, "/auth/workspace", { tenant_id: "dept-01" })).status,
    ).toBe(200);
    await ready(page, "/admin/model", "模型与运行");
    await expect(
      page.getByRole("button", { name: "验证服务连接", exact: true }),
    ).toBeDisabled();
    expect((await invoke(page, "/v2/system/model:test", {})).status).toBe(403);
  });
});
