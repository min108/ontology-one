import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect } from "./runtime.mjs";
import { accounts } from "./fixture.mjs";
export const terms = JSON.parse(
  readFileSync(
    resolve(process.env.E2E_APPLICATION_ROOT || ".", "terms/zh-CN.json"),
    "utf8",
  ),
);
export const c = terms.labels,
  a = terms.actions;
export const initialPassword = "R3DisposableInitial123!";
export const changedPassword = "R3DisposableChanged456!";
export const unique = (prefix) =>
  `${prefix}.${Date.now().toString(36)}.${Math.random().toString(36).slice(2, 7)}`;
export const exact = (name) => ({ name, exact: true });
export const button = (scope, name) => scope.getByRole("button", exact(name));
export async function loginCredentials(page, username, password) {
  await page.goto(process.env.E2E_BASE_URL + "/");
  await page
    .getByLabel(c.identity_login_account, { exact: true })
    .fill(username);
  await page.getByLabel(c.identity_password, { exact: true }).fill(password);
  await button(page, a.identity_login).click();
  await expect(page.getByRole("navigation", exact(c.navigation))).toBeVisible();
}
export async function login(browser, role) {
  const context = await browser.newContext({
    viewport: { width: 1366, height: 768 },
    acceptDownloads: true,
    baseURL: process.env.E2E_BASE_URL,
  });
  const page = await context.newPage();
  await loginCredentials(
    page,
    accounts[role].username,
    accounts[role].password,
  );
  return { context, page };
}
export async function read(page, path) {
  return page.evaluate(async (path) => {
    const session = await (await fetch("/api/auth/session")).json();
    const response = await fetch(path, {
      headers: { "X-Workspace-Id": session.workspace.tenant_id },
    });
    if (!response.ok) throw new Error(`Read HTTP ${response.status}`);
    return response.json();
  }, path);
}
export const postResponse = (page, suffix) =>
  page.waitForResponse(
    (r) =>
      new URL(r.url()).pathname.endsWith(suffix) &&
      r.request().method() === "POST",
  );
export const userRow = (page, username) =>
  page
    .getByRole("row")
    .filter({ has: page.getByText(username, { exact: true }) });
export async function confirmIdentity(page) {
  const modal = page.getByRole("dialog");
  await button(modal, a.identity_review).click();
  await button(modal, a.identity_confirm).click();
  await expect(modal).toHaveCount(0);
}
export async function createUser(
  page,
  username,
  { name = username, role = "modeler", org = "hq" } = {},
) {
  await page.goto("/admin/users");
  await button(page, a.identity_create_user).first().click();
  const modal = page.getByRole("dialog");
  await modal.getByLabel(c.identity_username, { exact: true }).fill(username);
  await modal.getByLabel(c.identity_name, { exact: true }).fill(name);
  await modal
    .getByLabel(c.identity_organization, { exact: true })
    .selectOption(org);
  await modal.getByLabel(c.identity_role, { exact: true }).selectOption(role);
  await modal
    .getByLabel(c.identity_initial_password, { exact: true })
    .fill(initialPassword);
  await confirmIdentity(page);
  await page.getByLabel(c.filter, { exact: true }).fill(username);
  await expect(userRow(page, username)).toBeVisible();
  return (await read(page, "/api/v2/users")).items.find(
    (u) => u.username === username,
  );
}
export async function confirmAction(page, name) {
  const modal = page.getByRole("dialog", exact(name));
  await expect(modal).toBeVisible();
  await button(modal, name).click();
  await expect(modal).not.toBeVisible();
}
