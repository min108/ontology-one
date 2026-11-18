import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { test, expect } from "./runtime.mjs";
import { c, a, login, read, button, postResponse } from "./r3-support.mjs";
import { selectSavedViewMode } from "./select-saved-view-mode.mjs";
import {
  assertRejectedWrite,
  assertLegacyEdit,
  assertMalformedReadable,
} from "./r4a1-kernel-contracts.mjs";

const entity = (page, id) => read(page, `/api/entities/${id}`);
const element = async (page, id) =>
  (await read(page, `/api/v2/elements/${id}`)).data;
const judgement = {
  condition: { oxl: "true" },
  requirement: { oxl: "true" },
  exceptions: [],
};

async function technical(scope) {
  const details = scope.locator("[data-technical-details]").last();
  await details.locator("summary").click();
  return details;
}

test("R4-A.1 AB11 bounded judgement and long numeric body preserve saved state", async ({
  browser,
}) => {
  const owner = await login(browser, "modeler"),
    page = owner.page;
  try {
    await page.goto("/assets?kind=rule");
    await button(page, "手工新建").click();
    const modal = page.getByRole("dialog", { name: "手工新建业务资产" });
    await modal.getByLabel("名称", { exact: true }).fill("正文处理检查");
    await modal.getByLabel("业务内容", { exact: true }).fill("核对工作记录");
    const creating = postResponse(page, "/element.create_draft:invoke");
    await button(modal, "保存草稿").click();
    const created = (await (await creating).json()).data.entity;
    await page.goto(`/ontology/elements/${created.id}`);
    await selectSavedViewMode(page, "professional");
    const details = await technical(page);
    const before = await entity(page, created.id);
    await details.getByLabel(c.r4_rule_judgement, { exact: true }).fill(
      JSON.stringify({
        ...judgement,
        requirement: { oxl: "round(1, -4000000) == 0" },
      }),
    );
    const rejecting = postResponse(page, "/element.update_draft:invoke");
    await button(details, c.r4_rule_save).click();
    expect((await rejecting).status()).toBe(422);
    await expect(details.getByRole("alert")).toContainText("-100 到 100");
    assertRejectedWrite(before, await entity(page, created.id));

    await page.goto(`/assets/${created.id}`);
    await button(page, "编辑内容").click();
    const edit = page.getByRole("dialog"),
      content = "7".repeat(10000);
    await edit.getByLabel("规则要求", { exact: true }).fill(content);
    await edit
      .getByLabel("修改说明", { exact: true })
      .fill("验证完整的长编号正文能够正常保存");
    const saving = page.waitForResponse(
      (r) =>
        r.request().method() === "PATCH" &&
        r.url().endsWith(`/api/entities/${created.id}`),
    );
    await button(edit, "保存修改").click();
    const response = await saving;
    expect(response.status()).toBe(200);
    await response.finished();
    expect(response.request().timing().responseEnd).toBeLessThan(1000);
    assertLegacyEdit(before, await entity(page, created.id), content);
    await page.goto(`/ontology/elements/${created.id}`);
    await expect(
      page.getByRole("heading", { name: created.title, exact: true }),
    ).toBeVisible();
    expect((await element(page, created.id)).entity.payload.content).toBe(
      content,
    );
    await page.goto("/govern/reviews");
    await expect(page.locator("[data-page-header]")).toBeVisible();
    expect((await read(page, "/api/v2/reviews?kind=")).data).toBeTruthy();
  } finally {
    await owner.context.close();
  }
});

test("R4-A.1 AB12 AB14 migrated unknown kind edits normally and malformed old rule remains visible", async ({
  browser,
}) => {
  const owner = await login(browser, "superadmin"),
    page = owner.page;
  try {
    const id = "R4A1-OLD-MEMO",
      before = await entity(page, id);
    expect(before.kind).toBe("memo");
    await page.goto(`/assets/${id}`);
    await expect(
      page.getByRole("heading", { name: before.title, exact: true }),
    ).toBeVisible();
    await button(page, "编辑内容").click();
    const edit = page.getByRole("dialog"),
      content = before.payload.content + " 已核对。";
    await edit.getByLabel("业务内容", { exact: true }).fill(content);
    await edit
      .getByLabel("修改说明", { exact: true })
      .fill("升级后继续维护原工作记录");
    const saving = page.waitForResponse(
      (r) =>
        r.request().method() === "PATCH" &&
        r.url().endsWith(`/api/entities/${id}`),
    );
    await button(edit, "保存修改").click();
    expect((await saving).status()).toBe(200);
    assertLegacyEdit(before, await entity(page, id), content);
    await page.reload();
    await expect(page.getByText(content, { exact: true })).toBeVisible();
    await page.goto("/ontology/elements/R4A1-OLD-BAD-RULE");
    await expect(
      page.getByRole("heading", { name: "升级前待修复的判定式", exact: true }),
    ).toBeVisible();
    const broken = await element(page, "R4A1-OLD-BAD-RULE");
    assertMalformedReadable(broken);
    await expect(page.locator('[data-gate-code="QG3"]')).toContainText(
      /判定式|适用对象/,
    );
    await page.goto("/govern/reviews");
    await expect(
      page.getByText("升级前待修复的判定式", { exact: true }).first(),
    ).toBeVisible();
    expect((await read(page, "/api/v2/reviews?kind=")).data).toBeTruthy();
  } finally {
    await owner.context.close();
  }
});

test("R4-A.1 AB14 malformed candidate edit is rejected atomically and its review page stays usable", async ({
  browser,
}) => {
  const owner = await login(browser, "modeler"),
    page = owner.page;
  try {
    await selectSavedViewMode(page, "professional");
    const source = readFileSync(
      resolve("docs/blueprint/rounds/r4a1-materials/regulation-plain.txt"),
      "utf8",
    );
    const dropped = await page.evaluateHandle((text) => {
      const transfer = new DataTransfer();
      transfer.items.add(
        new File([text], "制度判定格式核对.txt", { type: "text/plain" }),
      );
      return transfer;
    }, source);
    await page.dispatchEvent("body", "dragenter", { dataTransfer: dropped });
    await page.dispatchEvent("body", "drop", { dataTransfer: dropped });
    await button(page.getByRole("dialog"), "开始整理").click();
    await expect(button(page, "核对建设方案")).toBeVisible({ timeout: 180000 });
    const getting = page.waitForResponse((r) =>
      /\/api\/v2\/changesets\/CS-/.test(new URL(r.url()).pathname),
    );
    await button(page, "核对建设方案").click();
    const before = (await (await getting).json()).data;
    const item = before.items.find((value) => value.after_data.kind === "rule");
    expect(item).toBeTruthy();
    await page.locator(`button[data-item-id="${item.id}"]`).click();
    await button(page.locator("[data-selected-item-id]"), "改一改").click();
    const edit = page.getByRole("dialog");
    const details = await technical(edit);
    await details
      .getByLabel(c.r4_rule_judgement, { exact: true })
      .fill(JSON.stringify({ ...judgement, applies_to: null }));
    const rejected = postResponse(page, "/changeset.decide_items:invoke");
    await button(edit, a.r2_save).click();
    expect((await rejected).status()).toBe(422);
    await expect(
      page
        .getByRole("alert")
        .filter({ hasText: /判定式|适用对象/ })
        .first(),
    ).toBeVisible();
    const after = (await read(page, `/api/v2/changesets/${before.id}`)).data;
    assertRejectedWrite(
      item,
      after.items.find((value) => value.id === item.id),
    );
    await edit.getByRole("button", { name: /^取\s*消$/ }).click();
    await expect(edit).not.toBeVisible();
    await page.reload();
    await expect(
      page.locator(`button[data-item-id="${item.id}"]`),
    ).toBeVisible();
    await page.goto("/govern/reviews");
    await expect(page.locator("[data-page-header]")).toBeVisible();
  } finally {
    await owner.context.close();
  }
});
