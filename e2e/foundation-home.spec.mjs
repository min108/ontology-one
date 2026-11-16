import { resolve } from "node:path";
import { test, expect } from "./runtime.mjs";
import { authDirectory, fixture } from "./fixture.mjs";

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
