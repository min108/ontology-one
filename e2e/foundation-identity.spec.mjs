import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { test, expect } from "./runtime.mjs";
import { accounts, authDirectory, roles } from "./fixture.mjs";

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
          page.getByRole("tab", exact(c.identity_grants_tab)),
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
    await button(userRow(page, username), a.identity_reset_password).click();
    await dialog(page)
      .getByLabel(c.identity_temporary_password, { exact: true })
      .fill("weak");
    await expect(button(dialog(page), a.identity_review)).toBeDisabled();
    await dialog(page)
      .getByLabel(c.identity_temporary_password, { exact: true })
      .fill(changedPassword);
    await confirm(page);
    await button(userRow(page, username), a.identity_disable).click();
    await button(dialog(page), a.identity_review).click();
    await button(dialog(page), a.identity_cancel).click();
    await expect(
      button(userRow(page, username), a.identity_disable),
    ).toBeVisible();
    await button(userRow(page, username), a.identity_disable).click();
    await confirm(page);
    await expect(userRow(page, username)).toContainText(c.identity_disabled);
    await button(userRow(page, username), a.identity_enable).click();
    await confirm(page);
    await expect(userRow(page, username)).toContainText(c.identity_enabled);
  });

  test("ADM-03: a new administrator editing itself is signed out", async ({
    page,
    browser,
    baseURL,
  }) => {
    const username = unique("selfedit");
    await createUser(page, username, { role: "admin" });
    const own = await browser.newContext({
      baseURL,
      storageState: { cookies: [], origins: [] },
    });
    try {
      const person = await own.newPage();
      await login(person, username, initialPassword);
      await load(person, "/admin/users", c.identity_users);
      await person.getByLabel(c.filter, { exact: true }).fill(username);
      await expect(
        button(userRow(person, username), a.identity_disable),
      ).toBeDisabled();
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
    await page.getByRole("tab", exact(c.identity_grants_tab)).click();
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
      .getByLabel(c.identity_audit_raw_action, { exact: true })
      .fill("login");
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
    await page
      .locator("summary")
      .filter({ hasText: a.identity_details })
      .first()
      .click();
    await expect(
      page.getByText(c.identity_request_id + ":", { exact: false }).first(),
    ).toBeVisible();
    await button(page, a.identity_reset).click();
    await expect(page).toHaveURL((url) => !url.searchParams.has("action"));
  });
});
