import { test, expect } from "./runtime.mjs";
import { a, c, unique, login, read, button } from "./r3-support.mjs";
import { checkStructure, assertNoLeaks } from "./r3-structure.mjs";
import { selectSavedViewMode } from "./select-saved-view-mode.mjs";

// Token responses stay in browser memory. This suite has trace/screenshot capture
// disabled by r3.config; structural screenshots are taken after token dismissal.
test("R3 integration applications: governed operations, one-time credentials and complete U6 structure", async ({
  browser,
}) => {
  const admin = await login(browser, "admin");
  const page = admin.page;
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  try {
    await selectSavedViewMode(page, "simple");
    await page.goto("/integration-applications");
    await expect(page.locator("[data-page-title]")).toHaveText(
      c.r3_integration_title,
    );
    const options = await read(page, "/api/integrations/applications/options");
    expect(options.users.length).toBeGreaterThan(0);
    await checkStructure(page, "integration-empty");
    await button(page, a.r3_integration_register).click();
    let dialog = page.getByRole("dialog");
    const name = unique("验收接入应用");
    await dialog.getByLabel(c.r3_integration_name, { exact: true }).fill(name);
    await dialog
      .getByLabel(c.r3_integration_delegate, { exact: true })
      .selectOption(options.users[0].id);
    await dialog
      .getByLabel(c.r3_integration_description, { exact: true })
      .fill("核验授权范围与凭据操作");
    await assertNoLeaks(page);
    await dialog.getByLabel(c.r3_integration_confirm, { exact: true }).check();
    await button(dialog, c.r3_integration_create).click();
    const token = page.getByLabel(c.r3_integration_token, { exact: true });
    await expect(token).toHaveAttribute("type", "password");
    expect(await token.evaluate((input) => input.value.length > 20)).toBe(true);
    await button(
      page.getByRole("dialog"),
      c.r3_integration_token_close,
    ).click();
    await expect(token).toHaveCount(0);
    const find = async () =>
      (await read(page, "/api/integrations/applications")).items.find(
        (item) => item.name === name,
      );
    let application = await find();
    expect(application.description).toBe("核验授权范围与凭据操作");
    expect(application.delegate_user_id).toBe(options.users[0].id);
    expect(application.tool_scopes).toEqual(["ontology_discover"]);
    expect(application.credentials).toHaveLength(1);
    expect(application.credentials[0]).not.toHaveProperty("token");
    let card = page
      .locator("article")
      .filter({ has: page.getByRole("heading", { name, exact: true }) });
    await expect(card).toBeVisible();
    await assertNoLeaks(page);
    await checkStructure(page, "integration-populated");
    await button(card, c.r3_integration_edit).click();
    dialog = page.getByRole("dialog");
    await expect(token).toHaveCount(0);
    await dialog
      .getByLabel(c.r3_integration_description, { exact: true })
      .fill("修订后的调用用途");
    // Real navigation must retain the legacy unsaved-draft guard.
    await page.locator('[data-primary-link][href="/"]').click();
    const confirmation = page
      .getByRole("dialog")
      .filter({ hasText: c.shell_switch_continue });
    await expect(confirmation).toBeVisible();
    await button(confirmation, a.cancel).click();
    await expect(page).toHaveURL(
      (url) => url.pathname === "/integration-applications",
    );
    await expect(
      dialog.getByLabel(c.r3_integration_description, { exact: true }),
    ).toHaveValue("修订后的调用用途");
    await dialog.getByLabel(c.r3_integration_confirm, { exact: true }).check();
    await button(dialog, c.r3_integration_save).click();
    await expect(dialog).toHaveCount(0);
    expect((await find()).description).toBe("修订后的调用用途");
    await button(card, c.r3_integration_disable).click();
    await expect(button(card, c.r3_integration_enable)).toBeVisible();
    expect((await find()).enabled).toBe(false);
    await button(card, c.r3_integration_enable).click();
    await expect(button(card, c.r3_integration_disable)).toBeVisible();
    expect((await find()).enabled).toBe(true);
    await card
      .locator("summary")
      .filter({ hasText: c.r3_integration_credentials })
      .click();
    await button(card, c.r3_integration_rotate).click();
    await page
      .getByRole("dialog")
      .getByLabel(c.r3_integration_reason, { exact: true })
      .fill("按期核验密钥轮换");
    await button(
      page.getByRole("dialog"),
      c.r3_integration_rotate_confirm,
    ).click();
    await expect(token).toHaveAttribute("type", "password");
    expect(await token.evaluate((input) => input.value.length > 20)).toBe(true);
    await button(
      page.getByRole("dialog"),
      c.r3_integration_token_close,
    ).click();
    await expect(token).toHaveCount(0);
    application = await find();
    expect(application.credentials).toHaveLength(2);
    expect(
      application.credentials.filter((item) => !item.revoked_at),
    ).toHaveLength(1);
    await button(card, c.r3_integration_revoke).click();
    await page
      .getByRole("dialog")
      .getByLabel(c.r3_integration_reason, { exact: true })
      .fill("验收结束撤销调用凭据");
    await button(
      page.getByRole("dialog"),
      c.r3_integration_revoke_confirm,
    ).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    expect(
      (await find()).credentials.every((item) => Boolean(item.revoked_at)),
    ).toBe(true);
    await page.reload();
    await expect(token).toHaveCount(0);
    await expect(card).toBeVisible();
    await assertNoLeaks(page);
    await selectSavedViewMode(page, "professional");
    await card.locator("[data-technical-details] summary").click();
    await expect(card.locator("[data-technical-details]")).toContainText(
      application.id,
    );
    await assertNoLeaks(page, { professional: true });
    await selectSavedViewMode(page, "simple");
    await expect(page.locator("[data-technical-details]")).toHaveCount(0);
    await checkStructure(page, "integration-final");
    expect(errors).toEqual([]);
  } finally {
    await admin.context.close();
  }
});
