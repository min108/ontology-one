import { writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { test, expect } from "./runtime.mjs";
import { accounts, legacyRoutes } from "./fixture.mjs";
import {
  a,
  c,
  unique,
  initialPassword,
  changedPassword,
  login,
  loginCredentials,
  read,
  button,
  createUser,
  confirmIdentity,
  userRow,
} from "./r3-support.mjs";
import { checkStructure, assertNoLeaks } from "./r3-structure.mjs";
import {
  selectSavedViewMode,
  openViewModeMenu,
  closeViewModeMenu,
} from "./select-saved-view-mode.mjs";

async function more(page, row, name) {
  await button(row, a.r3_admin_more).click();
  await page.getByRole("menuitem", { name, exact: true }).click();
}
async function switchOrganization(page, name) {
  await button(page, c.r3_ui_org_switch || "切换机构与范围").click();
  await page.getByRole("combobox", { name: "机构", exact: true }).click();
  await page.getByRole("option", { name, exact: true }).click();
  await expect(page).toHaveURL((url) => url.pathname === "/");
}

test("R3 A1 A14 A15: new user, empty workbench, account actions and business audit filters", async ({
  browser,
}) => {
  const admin = await login(browser, "superadmin");
  const page = admin.page;
  const member = await browser.newContext({
    baseURL: process.env.E2E_BASE_URL,
    viewport: { width: 1366, height: 768 },
  });
  const person = await member.newPage();
  try {
    await selectSavedViewMode(page, "simple");
    const orgName = unique("验收空机构");
    await page.goto("/admin/orgs");
    await button(page, a.identity_create_org).click();
    await page
      .getByRole("dialog")
      .getByLabel(c.identity_org_name, { exact: true })
      .fill(orgName);
    await page
      .getByRole("dialog")
      .getByLabel(c.identity_org_category, { exact: true })
      .selectOption("subsidiary");
    await confirmIdentity(page);
    const org = (await read(page, "/api/v2/orgs")).items.find(
      (o) => o.name === orgName,
    );
    expect(org.id).toMatch(/^org-/);
    const username = unique("r3.new"),
      user = await createUser(page, username, { org: org.id });
    await loginCredentials(person, username, initialPassword);
    await expect(person).toHaveURL((url) => url.pathname === "/");
    await expect(
      person.getByRole("heading", { name: "从这里开始", exact: true }),
    ).toBeVisible();
    await openViewModeMenu(person);
    await expect(
      person.getByRole("radio", { name: "简洁视图", exact: true }),
    ).toBeChecked();
    await closeViewModeMenu(person);
    expect(
      (await read(person, "/api/v2/prefs")).preferences.view_mode ?? "simple",
    ).toBe("simple");
    await checkStructure(person, "empty-home", { empty: true });

    await page.goto("/admin/users");
    await page.getByLabel(c.filter, { exact: true }).fill(username);
    const row = userRow(page, username);
    expect(await row.getByRole("button").count()).toBeLessThanOrEqual(2);
    await button(row, a.identity_edit_user).click();
    await page
      .getByRole("dialog")
      .getByLabel(c.identity_name, { exact: true })
      .fill("核验后的建设人员");
    await confirmIdentity(page);
    expect(
      (await read(page, "/api/v2/users")).items.find((u) => u.id === user.id)
        .display_name,
    ).toBe("核验后的建设人员");
    await more(page, row, a.identity_reset_password);
    await page
      .getByRole("dialog")
      .getByLabel(c.identity_temporary_password, { exact: true })
      .fill(changedPassword);
    await confirmIdentity(page);
    await person.reload();
    await expect(
      person.getByLabel(c.identity_login_account, { exact: true }),
    ).toBeVisible();
    await loginCredentials(person, username, changedPassword);
    expect((await read(person, "/api/auth/session")).user.display_name).toBe(
      "核验后的建设人员",
    );
    await more(page, row, a.identity_disable);
    await confirmIdentity(page);
    await expect(row).toContainText(c.identity_disabled);
    const disabled = (await read(page, "/api/v2/users")).items.find(
      (u) => u.id === user.id,
    );
    expect(
      disabled.is_active ?? disabled.active ?? disabled.status === "active",
    ).toBeFalsy();
    await person.reload();
    await expect(
      person.getByLabel(c.identity_login_account, { exact: true }),
    ).toBeVisible();
    await checkStructure(page, "users");

    await page.goto("/admin/audit");
    await page.getByLabel(c.r3_admin_actor, { exact: true }).selectOption({
      label: `${accounts.superadmin.display_name || (await read(page, "/api/auth/session")).user.display_name}（${accounts.superadmin.username}）`,
    });
    await page
      .getByLabel(c.r3_admin_action, { exact: true })
      .selectOption("user_update");
    await page
      .getByLabel(c.r3_admin_outcome, { exact: true })
      .selectOption("success");
    const today = new Intl.DateTimeFormat("sv-SE", {
      timeZone: "Asia/Shanghai",
    }).format(new Date());
    for (const name of [c.identity_audit_since, c.identity_audit_until]) {
      await page.getByLabel(name, { exact: true }).fill(today);
      await page.getByLabel(name, { exact: true }).press("Enter");
    }
    await button(page, a.identity_filter).click();
    await expect(page).toHaveURL(
      (url) =>
        url.searchParams.get("action") === "user_update" &&
        url.searchParams.get("since") === today &&
        url.searchParams.get("until") === today,
    );
    const response = await read(
      page,
      "/api/v2/audit?action=user_update&outcome=success",
    );
    expect(response.items.some((item) => item.resource === user.id)).toBe(true);
    await expect(page.locator("tbody tr")).not.toHaveCount(0);
    await assertNoLeaks(page, "audit closed");
    await checkStructure(page, "audit");
    await page.locator("tbody tr").first().getByRole("button").click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await expect(page.getByRole("dialog").locator("pre")).toHaveCount(0);
    await page
      .getByRole("dialog")
      .getByRole("button", { name: /关闭|Close/ })
      .click();
    await selectSavedViewMode(page, "pro");
    await page.locator("tbody tr").first().getByRole("button").click();
    const details = page.getByRole("dialog");
    await details.locator("summary").filter({ hasText: "技术详情" }).click();
    await expect(details.locator("[data-technical-details]")).toContainText(
      c.identity_request_id,
    );
    await expect(details.locator("pre")).not.toBeEmpty();
  } finally {
    await Promise.allSettled([member.close(), admin.context.close()]);
  }
});

test("R3 A16: organization switch and scope retain server permissions", async ({
  browser,
}) => {
  const admin = await login(browser, "superadmin");
  try {
    const page = admin.page;
    const before = await read(page, "/api/auth/session");
    expect(before.workspace.tenant_id).toBe("hq");
    await switchOrganization(page, "办公厅");
    const after = await read(page, "/api/auth/session");
    expect(after.user.id).toBe(before.user.id);
    expect(after.workspace.tenant_id).toBe("dept-01");
    expect(after.permissions.manage_users).toBe(true);
    expect(after.workspace.readonly).toBe(false);
    await button(page, c.r3_ui_org_switch).click();
    await expect(
      page.getByText(c.r3_ui_cross_org, { exact: true }).last(),
    ).toBeVisible();
    await page.keyboard.press("Escape");
    await page.reload();
    expect((await read(page, "/api/auth/session")).workspace.tenant_id).toBe(
      "dept-01",
    );
    await switchOrganization(page, before.workspace.name);
    const restored = await read(page, "/api/auth/session");
    expect(restored.workspace.tenant_id).toBe("hq");
    expect(restored.permissions).toEqual(before.permissions);
  } finally {
    await admin.context.close();
  }
});

test("R3 new administration and landing pages: structural baseline and leakage", async ({
  browser,
}) => {
  const admin = await login(browser, "superadmin");
  try {
    await selectSavedViewMode(admin.page, "simple");
    for (const path of [
      "/admin/orgs",
      "/admin/users",
      "/admin/model",
      "/integration-applications",
      "/admin/backup",
      "/admin/demo",
      "/admin/system",
      "/admin/audit",
      "/account/security",
      "/build",
      "/ontology",
      "/apply",
      "/govern",
      "/admin",
    ]) {
      await admin.page.goto(path);
      await expect(admin.page.locator("[data-page-title]")).toBeVisible();
      await admin.page.waitForLoadState("networkidle");
      await checkStructure(admin.page, "new" + path.replaceAll("/", "-"));
    }
  } finally {
    await admin.context.close();
  }
});

const parents = {
  briefing: ["/", "/"],
  group: ["/ontology", "/group"],
  experience: ["/build", "/experience"],
  impact: ["/govern", "/impact"],
  work: ["/build", "/work"],
  materials: ["/build", "/materials"],
  material: ["/build", "/materials"],
  projects: ["/build", "/projects"],
  project: ["/build", "/projects"],
  task: ["/build", "/work"],
  assets: ["/ontology", "/assets"],
  asset: ["/ontology", "/assets"],
  authority: ["/ontology", "/authority"],
  "authority-detail": ["/ontology", "/authority"],
  collection: ["/ontology", "/assets"],
  datasets: ["/build", "/datasets"],
  dataset: ["/build", "/datasets"],
  "data-sources": ["/build", "/data-sources"],
  "data-source": ["/build", "/data-sources"],
  graph: ["/ontology", "/graph"],
  applications: ["/apply", "/applications"],
  maintenance: ["/govern", "/maintenance"],
  "integration-applications": ["/admin", "/integration-applications"],
  "skill-development": ["/build", "/skill-development"],
  skill: ["/build", "/skill-development"],
};
test("R3 A17: expanded rail and all 25 retained routes with legacy bridge", async ({
  browser,
}) => {
  const admin = await login(browser, "admin"),
    rows = [];
  try {
    const page = admin.page;
    for (const [name, path] of legacyRoutes) {
      const [workspace, list] = parents[name];
      await page.goto("/");
      const primary = page.locator(`[data-primary-link][href="${workspace}"]`);
      await primary.focus();
      if (workspace !== "/") {
        await primary.press("ArrowRight");
        const secondary = page.getByRole("navigation", {
          name: "工作区内导航",
          exact: true,
        });
        await expect(secondary).toBeVisible();
        await secondary.locator(`a[href="${list}"]`).click();
        await expect(page).toHaveURL((url) => url.pathname === list);
      }
      if (path !== list) {
        const link = page.locator(`a[href="${path}"]`).first();
        if (await link.count()) await link.click();
        else await page.goto(path); // detail/bookmarked routes remain part of the 25-route contract
      }
      await expect(page.locator("main")).toBeVisible();
      await page.waitForLoadState("networkidle");
      await expect(page.locator(".legacy-root")).toBeVisible();
      await checkStructure(page, `legacy-${name}`, { legacy: true });
      rows.push({ name, path, rail: list, reachable: true });
    }
    expect(rows).toHaveLength(25);
    writeFileSync(
      resolve(process.env.E2E_EVIDENCE_DIR, "legacy-routes.json"),
      JSON.stringify(rows, null, 2) + "\n",
    );
  } finally {
    await admin.context.close();
  }
});
