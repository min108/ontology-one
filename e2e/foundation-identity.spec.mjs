import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { test, expect } from "./runtime.mjs";
import { accounts, authDirectory, roles } from "./fixture.mjs";
import { selectSavedViewMode } from "./select-saved-view-mode.mjs";
import { selectHistoricalAudit } from "./visual-audit.mjs";

const terms = JSON.parse(
  readFileSync(
    resolve(process.env.E2E_APPLICATION_ROOT || ".", "terms/zh-CN.json"),
    "utf8",
  ),
);
const c = terms.labels,
  a = terms.actions;
const unique = (prefix) =>
  `${prefix}.${Date.now().toString(36)}.${Math.random().toString(36).slice(2, 7)}`;
const initialPassword = "IdentityInitial123!";
const changedPassword = "IdentityChanged456!";
const exact = (name) => ({ name, exact: true });
const button = (scope, name) => scope.getByRole("button", exact(name));
const dialog = (page) => page.getByRole("dialog");
async function accountAction(page, username, name) {
  await button(userRow(page, username), a.r3_admin_more).click();
  await page.getByRole("menuitem", exact(name)).click();
}
async function confirm(page) {
  await button(dialog(page), a.identity_review).click();
  await button(dialog(page), a.identity_confirm).click();
  await expect(dialog(page)).toHaveCount(0);
}
async function load(page, path, title) {
  await page.goto(path);
  await expect(page.getByRole("heading", exact(title))).toBeVisible();
}
async function createUser(
  page,
  username,
  { name = username, role = "modeler", org = "hq" } = {},
) {
  await load(page, "/admin/users", c.identity_users);
  await button(page, a.identity_create_user).first().click();
  await dialog(page)
    .getByLabel(c.identity_username, { exact: true })
    .fill(username);
  await dialog(page).getByLabel(c.identity_name, { exact: true }).fill(name);
  await dialog(page)
    .getByLabel(c.identity_organization, { exact: true })
    .selectOption(org);
  await dialog(page)
    .getByLabel(c.identity_role, { exact: true })
    .selectOption(role);
  const roleValues = await dialog(page)
    .getByLabel(c.identity_role, { exact: true })
    .locator("option")
    .evaluateAll((nodes) => nodes.map((node) => node.value));
  expect(roleValues).toEqual(["", "admin", "modeler", "reviewer"]);
  await dialog(page)
    .getByLabel(c.identity_initial_password, { exact: true })
    .fill(initialPassword);
  await confirm(page);
  await page.getByLabel(c.filter, { exact: true }).fill(username);
  await expect(userRow(page, username)).toBeVisible();
}
const userRow = (page, username) =>
  page
    .getByRole("row")
    .filter({ has: page.getByText(username, { exact: true }) });
async function login(page, username, password) {
  await page.goto("/");
  await page
    .getByLabel(c.identity_login_account, { exact: true })
    .fill(username);
  await page.getByLabel(c.identity_password, { exact: true }).fill(password);
  await button(page, a.identity_login).click();
  await expect(
    page.getByRole("navigation", exact(terms.labels.navigation)),
  ).toBeVisible();
}
async function read(page, path) {
  return page.evaluate(async (path) => {
    const headers = {};
    if (!path.startsWith("/api/auth/")) {
      const session = await (await fetch("/api/auth/session")).json();
      headers["X-Workspace-Id"] = session.workspace.tenant_id;
    }
    const response = await fetch(path, { headers });
    if (!response.ok) throw new Error(`Read failed: ${response.status}`);
    return response.json();
  }, path);
}

async function capture(page, name) {
  const info = test.info();
  await page.evaluate(() => document.fonts.ready);
  const path = info.outputPath(name + ".png");
  await page.screenshot({ path, fullPage: true, animations: "disabled" });
  await info.attach(name, { path, contentType: "image/png" });
}

// Every mutation targets a unique account or organization created by this spec.
// Seed account passwords, status and sessions are never changed.
test.describe("identity: login UI", () => {
  test.use({ storageState: { cookies: [], origins: [] } });
  test("L-01 L-04: invalid credentials retain account, clear secret and allow retry", async ({
    page,
  }) => {
    await page.goto("/");
    await expect(page).toHaveTitle(
      `${terms.brand.product_name} · ${terms.brand.product_descriptor}`,
    );
    await expect(
      page.getByText(
        `${terms.brand.product_name} · ${terms.brand.product_name_en}`,
        { exact: true },
      ),
    ).toBeVisible();
    await expect(
      page.getByText(terms.brand.tagline, { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText(c.identity_login_contact, { exact: true }),
    ).toBeVisible();
    const version = readFileSync(
      resolve(process.env.E2E_APPLICATION_ROOT || ".", "VERSION"),
      "utf8",
    ).trim();
    await expect(
      page.getByText(`${terms.brand.product_name} ${version}`, { exact: true }),
    ).toBeVisible();
    await expect(page.getByText(/group\.admin/)).toHaveCount(0);
    await capture(page, "identity-login");
    const username = unique("missing");
    await page
      .getByLabel(c.identity_login_account, { exact: true })
      .fill(username);
    await page
      .getByLabel(c.identity_password, { exact: true })
      .fill(initialPassword);
    await button(page, a.identity_login).click();
    await expect(
      page.getByText(c.identity_login_failed, { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByLabel(c.identity_login_account, { exact: true }),
    ).toHaveValue(username);
    await expect(
      page.getByLabel(c.identity_password, { exact: true }),
    ).toHaveValue("");
  });
  test("L-01: connection failure offers reconnect without losing the login route", async ({
    page,
  }, info) => {
    info.annotations.push({
      type: "UI fault injection",
      description:
        "Browser session request aborted to verify the connection recovery UI.",
    });
    await page.route("**/api/auth/session", (route) =>
      route.abort("connectionrefused"),
    );
    await page.goto("/");
    await expect(button(page, a.identity_reconnect)).toBeVisible();
    await page.unroute("**/api/auth/session");
    await button(page, a.identity_reconnect).click();
    await expect(
      page.getByLabel(c.identity_login_account, { exact: true }),
    ).toBeVisible();
    await expect(button(page, a.identity_reconnect)).toHaveCount(0);
  });
  test("L-01: rate-limit response has a recoverable plain-language state", async ({
    page,
  }, info) => {
    info.annotations.push({
      type: "UI fault injection",
      description:
        "429 presentation only; real limiter covered by backend security regression.",
    });
    await page.route("**/api/auth/login", (route) =>
      route.fulfill({
        status: 429,
        contentType: "application/json",
        body: JSON.stringify({ detail: "rate limited" }),
      }),
    );
    await page.goto("/");
    await page
      .getByLabel(c.identity_login_account, { exact: true })
      .fill(unique("limited"));
    await page
      .getByLabel(c.identity_password, { exact: true })
      .fill(initialPassword);
    await button(page, a.identity_login).click();
    await expect(
      page.getByText(c.identity_login_limited, { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByLabel(c.identity_password, { exact: true }),
    ).toHaveValue("");
  });
});

for (const role of roles)
  test.describe(`${role}: identity permissions`, () => {
    test.use({ storageState: resolve(authDirectory, `${role}.json`) });
    test("SEC-01 ADM-11: real identity, account access and headquarters-only routes", async ({
      page,
    }) => {
      await load(page, "/account/security", c.identity_security);
      const session = await read(page, "/api/auth/session");
      expect(session.must_change_password).toBe(false);
      expect(session.user.must_change_password).toBe(false);
      await expect(
        page.getByText(session.user.username, { exact: true }),
      ).toBeVisible();
      if (role === "admin") {
        await expect(
          page.getByRole("region", exact(c.identity_security)),
        ).toHaveAttribute("aria-busy", "false");
        await capture(page, "identity-security");
      }
      for (const path of ["/admin/orgs", "/admin/audit"]) {
        await page.goto(path);
        await expect(
          page
            .getByRole("status")
            .filter({ hasText: terms.states.forbidden.title }),
        ).toBeVisible();
      }
      await page.goto("/admin/users");
      if (role === "admin") {
        await expect(
          page.getByRole("heading", exact(c.identity_users)),
        ).toBeVisible();
        await expect(
          page.getByRole("radio", exact(c.identity_grants_tab)),
        ).toHaveCount(0);
      } else
        await expect(
          page
            .getByRole("status")
            .filter({ hasText: terms.states.forbidden.title }),
        ).toBeVisible();
    });
  });

test.describe("headquarters: isolated identity mutations", () => {
  test.use({ storageState: { cookies: [], origins: [] } });
  test.beforeEach(async ({ page }) => {
    await login(
      page,
      accounts.superadmin.username,
      accounts.superadmin.password,
    );
  });
  test("ADM-01–05: create, edit, reset, disable and enable only a new account", async ({
    page,
  }) => {
    await load(page, "/admin/users", c.identity_users);
    await expect(
      page.getByRole("region", exact(c.identity_users)),
    ).toHaveAttribute("aria-busy", "false");
    await capture(page, "identity-users");
    const username = unique("managed");
    await createUser(page, username);
    await button(userRow(page, username), a.identity_edit_user).click();
    await dialog(page)
      .getByLabel(c.identity_name, { exact: true })
      .fill(`${username} renamed`);
    await dialog(page)
      .getByLabel(c.identity_role, { exact: true })
      .selectOption("reviewer");
    await confirm(page);
    await expect(userRow(page, username)).toContainText(terms.roles.reviewer);
    await accountAction(page, username, a.identity_reset_password);
    await dialog(page)
      .getByLabel(c.identity_temporary_password, { exact: true })
      .fill("weak");
    await expect(button(dialog(page), a.identity_review)).toBeDisabled();
    await dialog(page)
      .getByLabel(c.identity_temporary_password, { exact: true })
      .fill(changedPassword);
    await confirm(page);
    await accountAction(page, username, a.identity_disable);
    await button(dialog(page), a.identity_review).click();
    await button(dialog(page), a.identity_cancel).click();
    await button(userRow(page, username), a.r3_admin_more).click();
    await expect(
      page.getByRole("menuitem", exact(a.identity_disable)),
    ).toBeVisible();
    await page.keyboard.press("Escape");
    await accountAction(page, username, a.identity_disable);
    await confirm(page);
    await expect(userRow(page, username)).toContainText(c.identity_disabled);
    await accountAction(page, username, a.identity_enable);
    await confirm(page);
    await expect(userRow(page, username)).toContainText(c.identity_enabled);
  });

  test("ADM-03 ADM-06: other-superadmin reason is audited and self-edit signs out", async ({
    page,
    browser,
    baseURL,
  }) => {
    const username = unique("selfedit");
    await createUser(page, username, { role: "admin" });
    const target = (await read(page, "/api/v2/users")).items.find(
      (item) => item.username === username,
    );
    expect(target.is_superadmin).toBe(true);
    const reason = unique("其他超管调整原因");
    await button(userRow(page, username), a.identity_edit_user).click();
    await dialog(page)
      .getByLabel(c.identity_name, { exact: true })
      .fill(`${username}总部调整`);
    await expect(button(dialog(page), a.identity_review)).toBeDisabled();
    await dialog(page)
      .getByLabel(c.identity_reason_required, { exact: true })
      .fill(reason);
    await confirm(page);
    await expect(userRow(page, username)).toContainText(`${username}总部调整`);
    const audit = await read(
      page,
      "/api/v2/audit?action=user_update&outcome=success",
    );
    const recorded = audit.items.find((item) => item.resource === target.id);
    expect(recorded.details.reason).toBe(reason);
    await load(
      page,
      "/admin/audit?action=user_update&outcome=success",
      c.identity_audit,
    );
    await selectSavedViewMode(page, "pro");
    const auditRow = page.locator(`tr[data-row-key="${recorded.id}"]`);
    await expect(auditRow).toHaveCount(1);
    await auditRow.getByRole("button").click();
    await dialog(page)
      .locator("summary")
      .filter({ hasText: "技术详情" })
      .click();
    await expect(dialog(page).locator("pre")).toContainText(reason);
    await dialog(page)
      .getByRole("button", { name: /关闭|Close/ })
      .click();
    const own = await browser.newContext({
      baseURL,
      storageState: { cookies: [], origins: [] },
    });
    try {
      const person = await own.newPage();
      await login(person, username, initialPassword);
      await load(person, "/admin/users", c.identity_users);
      await person.getByLabel(c.filter, { exact: true }).fill(username);
      await button(userRow(person, username), a.r3_admin_more).click();
      await expect(
        person.getByRole("menuitem", exact(a.identity_disable)),
      ).toHaveAttribute("aria-disabled", "true");
      await person.keyboard.press("Escape");
      await button(userRow(person, username), a.identity_edit_user).click();
      await expect(
        dialog(person).getByLabel(c.identity_role, { exact: true }),
      ).toBeDisabled();
      await dialog(person)
        .getByLabel(c.identity_name, { exact: true })
        .fill(`${username}本人改名`);
      await button(dialog(person), a.identity_review).click();
      await expect(
        dialog(person).getByText(c.identity_self_update_impact, {
          exact: true,
        }),
      ).toBeVisible();
      await button(dialog(person), a.identity_confirm).click();
      await expect(
        person.getByLabel(c.identity_login_account, { exact: true }),
      ).toBeVisible();
      await login(person, username, initialPassword);
      expect((await read(person, "/api/auth/session")).user.display_name).toBe(
        `${username}本人改名`,
      );
    } finally {
      await own.close();
    }
  });

  test("SEC-02–04 L-02 L-03: new account changes password and revokes only its own devices", async ({
    page,
    browser,
    baseURL,
  }) => {
    const username = unique("security");
    await createUser(page, username);
    const first = await browser.newContext({
      baseURL,
      storageState: { cookies: [], origins: [] },
    });
    const second = await browser.newContext({
      baseURL,
      storageState: { cookies: [], origins: [] },
    });
    try {
      const current = await first.newPage(),
        other = await second.newPage();
      await login(current, username, initialPassword);
      await login(other, username, initialPassword);
      await load(current, "/account/security", c.identity_security);
      await expect(
        current
          .getByText(c.identity_password_recommended, { exact: true })
          .first(),
      ).toBeVisible();
      await current
        .getByLabel(c.identity_current_password, { exact: true })
        .fill(initialPassword);
      await current
        .getByLabel(c.identity_new_password, { exact: true })
        .fill(changedPassword);
      await current
        .getByLabel(c.identity_confirm_password, { exact: true })
        .fill("mismatch");
      await expect(button(current, a.identity_password_change)).toBeDisabled();
      await current
        .getByLabel(c.identity_confirm_password, { exact: true })
        .fill(changedPassword);
      await button(current, a.identity_password_change).click();
      await expect(
        current.getByText(c.identity_password_changed, { exact: true }).first(),
      ).toBeVisible();
      await other.reload();
      await expect(
        other.getByLabel(c.identity_login_account, { exact: true }),
      ).toBeVisible();
      await login(other, username, changedPassword);
      await current.reload();
      await expect(
        current.getByText(c.identity_password_recommended, { exact: true }),
      ).toHaveCount(0);
      const otherDevice = current
        .locator("section")
        .filter({
          has: current
            .locator("strong")
            .filter({ hasText: c.identity_other_device }),
        })
        .last();
      await button(otherDevice, a.identity_revoke_session).click();
      await button(dialog(current), a.identity_cancel).click();
      await expect(otherDevice).toBeVisible();
      await button(otherDevice, a.identity_revoke_session).click();
      await button(dialog(current), a.identity_confirm_revoke).click();
      await other.reload();
      await expect(
        other.getByLabel(c.identity_login_account, { exact: true }),
      ).toBeVisible();
      const ownDevice = current
        .locator("section")
        .filter({
          has: current
            .locator("strong")
            .filter({ hasText: c.identity_current_device }),
        })
        .last();
      await button(ownDevice, a.identity_revoke_session).click();
      await button(dialog(current), a.identity_confirm_revoke).click();
      await expect(
        current.getByLabel(c.identity_login_account, { exact: true }),
      ).toBeVisible();
    } finally {
      await first.close();
      await second.close();
    }
  });

  test("ADM-07–09: grant levels, replacement and cancellation use a new headquarters account", async ({
    page,
  }) => {
    const username = unique("grant");
    await createUser(page, username, { role: "admin" });
    const users = await read(page, "/api/v2/users");
    const user = users.items.find((user) => user.username === username);
    const grantsTab = page.getByRole("radio", exact(c.identity_grants_tab));
    await grantsTab.locator("..").click();
    await expect(grantsTab).toBeChecked();
    for (const access of ["read_published", "read_private", "write"]) {
      await button(page, a.identity_create_grant).first().click();
      await dialog(page)
        .getByLabel(c.identity_grant_user, { exact: true })
        .selectOption(user.id);
      await expect(
        dialog(page)
          .getByLabel(c.identity_grant_org, { exact: true })
          .locator('option[value="hq"]'),
      ).toHaveCount(0);
      await dialog(page)
        .getByLabel(c.identity_grant_org, { exact: true })
        .selectOption("dept-01");
      await dialog(page)
        .getByLabel(c.identity_grant_access, { exact: true })
        .selectOption(access);
      await dialog(page)
        .getByLabel(c.identity_grant_reason, { exact: true })
        .fill("测试隔离授权");
      await confirm(page);
      const grants = await read(page, "/api/v2/grants");
      expect(
        grants.items
          .filter(
            (grant) =>
              grant.user_id === user.id && grant.tenant_id === "dept-01",
          )
          .map((grant) => grant.access),
      ).toEqual([access]);
    }
    await page.goto(
      `/admin/users?tab=grants&q=${encodeURIComponent(username + " 办公厅")}`,
    );
    const row = page
      .getByRole("row")
      .filter({ has: page.getByText(username, { exact: true }) })
      .filter({ hasText: "办公厅" });
    await button(row, a.identity_revoke_grant).click();
    await button(dialog(page), a.identity_review).click();
    await button(dialog(page), a.identity_cancel).click();
    await expect(row).toBeVisible();
    await button(row, a.identity_revoke_grant).click();
    await confirm(page);
    expect(
      (await read(page, "/api/v2/grants")).items.filter(
        (grant) => grant.user_id === user.id && grant.tenant_id === "dept-01",
      ),
    ).toHaveLength(0);
  });

  test("org governance: new directory lifecycle, protected headquarters and no deletion", async ({
    page,
    browser,
    baseURL,
  }) => {
    const name = unique("机构验收");
    await load(page, "/admin/orgs", c.identity_orgs);
    await expect(button(page, a.identity_disable)).toBeDisabled();
    await expect(page.getByRole("button", { name: /删除/ })).toHaveCount(0);
    await capture(page, "identity-organizations");
    await button(page, a.identity_create_org).click();
    await dialog(page)
      .getByLabel(c.identity_org_name, { exact: true })
      .fill(name);
    await dialog(page)
      .getByLabel(c.identity_org_category, { exact: true })
      .selectOption("subsidiary");
    await confirm(page);
    const org = (await read(page, "/api/v2/orgs")).items.find(
      (org) => org.name === name,
    );
    expect(org.id).toMatch(/^org-[a-f0-9]{8}$/);
    await page.getByRole("tree").getByLabel(name, { exact: true }).click();
    await button(page, a.identity_edit_org).click();
    await expect(
      dialog(page)
        .getByLabel(c.identity_org_parent, { exact: true })
        .locator(`option[value="${org.id}"]`),
    ).toHaveCount(0);
    await dialog(page)
      .getByLabel(c.identity_org_name, { exact: true })
      .fill(`${name}改名`);
    await dialog(page)
      .getByLabel(c.identity_org_sort, { exact: true })
      .fill("42");
    await confirm(page);
    expect(
      (await read(page, "/api/v2/orgs")).items.find(
        (item) => item.id === org.id,
      ).name,
    ).toBe(`${name}改名`);
    const username = unique("orgmember");
    await createUser(page, username, { org: org.id });
    const member = await browser.newContext({
      baseURL,
      storageState: { cookies: [], origins: [] },
    });
    try {
      const person = await member.newPage();
      await login(person, username, initialPassword);
      expect(
        (await read(person, "/api/auth/session")).workspace.tenant_id,
      ).toBe(org.id);
      await load(page, "/admin/orgs", c.identity_orgs);
      await page
        .getByRole("tree")
        .getByLabel(`${name}改名`, { exact: true })
        .click();
      await button(page, a.identity_disable).click();
      await dialog(page)
        .getByLabel(c.identity_org_reason, { exact: true })
        .fill("隔离验收停用");
      await confirm(page);
      await person.reload();
      await expect(
        person.getByLabel(c.identity_login_account, { exact: true }),
      ).toBeVisible();
      await page
        .getByRole("tree")
        .getByLabel(`${name}改名`, { exact: true })
        .click();
      await expect(button(page, a.identity_edit_org)).toBeDisabled();
      await button(page, a.identity_enable).click();
      await confirm(page);
      await login(person, username, initialPassword);
      expect(
        (await read(person, "/api/auth/session")).workspace.tenant_id,
      ).toBe(org.id);
    } finally {
      await member.close();
    }
  });

  test("ADM-10: audit is paginated, filterable and retains details", async ({
    page,
  }) => {
    await load(page, "/admin/audit", c.identity_audit);
    await expect(
      page.getByRole("region", exact(c.identity_audit)),
    ).toHaveAttribute("aria-busy", "false");
    await capture(page, "identity-audit");
    await page
      .getByRole("combobox", { name: c.r3_admin_action, exact: true })
      .selectOption("login");
    await button(page, a.identity_filter).click();
    await expect(page).toHaveURL(
      (url) => url.searchParams.get("action") === "login",
    );
    const response = await read(
      page,
      "/api/v2/audit?action=login&page=1&page_size=30",
    );
    expect(response.page_size).toBe(30);
    expect(response.items.every((row) => row.action === "login")).toBe(true);
    await expect(page.locator("tbody tr")).toHaveCount(response.items.length);
    await selectSavedViewMode(page, "pro");
    await page.locator("tbody tr").first().getByRole("button").click();
    await dialog(page)
      .locator("summary")
      .filter({ hasText: "技术详情" })
      .click();
    await expect(
      dialog(page).getByText(c.identity_request_id, { exact: true }),
    ).toBeVisible();
    await dialog(page)
      .getByRole("button", { name: /关闭|Close/ })
      .click();
    await button(page, a.identity_reset).click();
    await expect(page).toHaveURL((url) => !url.searchParams.has("action"));
    // The preceding real management operations make this serial acceptance scope exceed one page.
    const all = await read(page, "/api/v2/audit?page=1&page_size=30");
    expect(all.total).toBeGreaterThan(30);
    await page.getByTitle("2", { exact: true }).click();
    await expect(page).toHaveURL((url) => url.searchParams.get("page") === "2");
    const next = await read(page, "/api/v2/audit?page=2&page_size=30");
    await expect(page.locator("tbody tr")).toHaveCount(next.items.length);
  });
});


test("R3 historical audit fixture stays filterable in both business views", async ({ browser }) => {
  test.setTimeout(60_000);
  const context = await browser.newContext({
    baseURL: process.env.E2E_BASE_URL,
    storageState: resolve(authDirectory, "superadmin.json"),
    viewport: { width: 1366, height: 768 },
  });
  try {
    const page = await context.newPage();
    await page.goto("/");
    for (const mode of ["simple", "professional"]) {
      await selectSavedViewMode(page, mode);
      await page.goto("/admin/audit");
      await expect(page.getByRole("heading", { name: "审计记录", exact: true })).toBeVisible();
      await selectHistoricalAudit(page, mode);
      await page.getByRole("dialog").getByRole("button", { name: /关闭|Close/ }).click();
    }
  } finally {
    await context.close();
  }
});
