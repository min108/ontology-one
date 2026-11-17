import { expect } from "./runtime.mjs";
import { visualAuditHistory } from "./visual-contract.mjs";

export async function selectHistoricalAudit(page, mode) {
  const expected = visualAuditHistory;
  await page
    .getByRole("combobox", { name: "操作类型", exact: true })
    .selectOption(expected.action, { timeout: 10_000 });
  await page
    .getByRole("combobox", { name: "所属机构", exact: true })
    .selectOption(expected.tenant_id, { timeout: 10_000 });
  await page
    .getByRole("combobox", { name: "操作结果", exact: true })
    .selectOption(expected.outcome, { timeout: 10_000 });
  const pending = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return (
      url.pathname === "/api/v2/audit" &&
      url.searchParams.get("action") === expected.action &&
      url.searchParams.get("tenant_id") === expected.tenant_id &&
      url.searchParams.get("outcome") === expected.outcome
    );
  }, { timeout: 10_000 });
  await page.getByRole("button", { name: "筛选记录", exact: true }).click();
  const response = await pending;
  expect(response.ok()).toBe(true);
  const result = await response.json();
  expect(result.total).toBe(1);
  expect(result.items).toHaveLength(1);
  expect(String(result.items[0].id)).toBe(expected.id);
  expect(result.items[0]).toMatchObject({
    action: expected.action,
    resource: expected.resource,
    tenant_id: expected.tenant_id,
    outcome: expected.outcome,
    actor_username: expected.actor_username,
    request_id: expected.request_id,
  });
  const row = page.locator(`tr[data-row-key="${expected.id}"]`);
  await expect(row).toHaveCount(1);
  await expect(row).toContainText(expected.actor_username);
  await row.getByRole("button").click();
  const drawer = page.getByRole("dialog");
  await expect(drawer).toBeVisible();
  if (mode === "professional") {
    await drawer.locator("summary").filter({ hasText: "技术详情" }).click();
    await expect(drawer.locator("details")).toHaveAttribute("open", "");
    await expect(drawer).toContainText(expected.action);
    await expect(drawer).toContainText(expected.tenant_id);
    await expect(drawer).toContainText(expected.resource);
    await expect(drawer.locator("pre")).toContainText('"entity_count": 20');
    await expect(drawer.locator("pre")).toContainText('"synthetic": true');
    await drawer.locator("pre").scrollIntoViewIfNeeded();
    await expect(drawer.locator("pre")).toBeInViewport({ ratio: 1 });
  } else {
    await expect(drawer.locator("[data-technical-details],pre")).toHaveCount(0);
    await expect(drawer).not.toContainText(expected.resource);
  }
}

