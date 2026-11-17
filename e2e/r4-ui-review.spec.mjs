import { resolve } from "node:path";
import { test, expect } from "./runtime.mjs";
import {
  c,
  a,
  login,
  read,
  button,
  postResponse,
  unique,
} from "./r3-support.mjs";
import { selectSavedViewMode } from "./select-saved-view-mode.mjs";
import { assertNoLeaks, checkStructure } from "./r3-structure.mjs";

async function create(page, path, body) {
  const result = await page.evaluate(
    async ({ path, body }) => {
      const session = await (await fetch("/api/auth/session")).json();
      const response = await fetch(path, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Workspace-Id": session.workspace.tenant_id,
          "X-CSRF-Token": session.csrf_token,
        },
        body: JSON.stringify(body),
      });
      return { status: response.status, body: await response.json() };
    },
    { path, body },
  );
  expect(result.status, JSON.stringify(result.body)).toBe(200);
  return result.body;
}
const shot = (page, name) =>
  page.screenshot({
    path: resolve(process.env.E2E_EVIDENCE_DIR, name + ".png"),
  });

test("R4 review metric editor saves root test cases and runs the saved formula", async ({
  browser,
}) => {
  const owner = await login(browser, "modeler"),
    page = owner.page;
  try {
    const object = (
      await create(page, "/api/v2/capabilities/element.create_draft:invoke", {
        kind: "object_type",
        title: unique("金额对象"),
        payload: {
          schema: "object_type/1",
          definition: "每笔付款",
          properties: [
            {
              key: "amount",
              name: "金额",
              datatype: "decimal",
              note: "申请支付的金额",
            },
          ],
        },
      })
    ).data;
    const metric = (
      await create(page, "/api/v2/capabilities/element.create_draft:invoke", {
        kind: "metric",
        title: unique("双倍金额口径"),
        payload: {
          schema: "metric/1",
          definition: "金额乘以二",
          object_type: object.entity.id,
          grain: "每笔付款",
          unit: "元",
          formula: { oxl: "amount * 2" },
          test_cases: [],
        },
      })
    ).data;
    await page.goto("/ontology/elements/" + metric.entity.id);
    await selectSavedViewMode(page, "professional");
    await page.locator("[data-technical-details] > details > summary").click();
    const cases = [
      { name: "正额", input: { amount: 2 }, expected: 4 },
      { name: "零额", input: { amount: 0 }, expected: 0 },
    ];
    await page
      .getByRole("textbox", { name: c.r4_rule_cases, exact: true })
      .fill(JSON.stringify(cases, null, 2));
    const saving = postResponse(page, "/element.update_draft:invoke");
    await button(page, c.r4_rule_save).click();
    expect((await saving).ok()).toBe(true);
    const saved = (await read(page, "/api/v2/elements/" + metric.entity.id))
      .data;
    expect(saved.entity.payload.test_cases).toEqual(cases);
    expect(saved.entity.payload.formula).not.toHaveProperty("test_cases");
    const running = postResponse(page, "/rule.test_cases:invoke");
    await button(page, c.r4_rule_run).click();
    const result = (await (await running).json()).data;
    expect(result.total).toBe(2);
    expect(result.passed_count).toBe(2);
    await expect(page.locator("[data-rule-test-result]")).toHaveAttribute(
      "data-passed",
      "true",
    );
    await page.locator("[data-rule-editor]").scrollIntoViewIfNeeded();
    await shot(page, "r4-metric-editor");
    await selectSavedViewMode(page, "simple");
    await checkStructure(page, "r4-metric-element");
    await shot(page, "r4-metric-element");
  } finally {
    await owner.context.close();
  }
});

test("R4 review specialized authority and skill repair controls match legacy layout boundaries", async ({
  browser,
}) => {
  const owner = await login(browser, "modeler"),
    page = owner.page;
  try {
    const authority = await create(page, "/api/authority/assets", {
      title: "合成权责核对规范",
      spec: {
        type: "workflow",
        purpose: "明确核对职责",
        scope: "本机构付款",
        org_ids: ["dept-01"],
      },
    });
    await page.goto("/authority/" + authority.entity.id);
    await page
      .getByRole("button", { name: "适用范围与关联", exact: true })
      .click();
    await expect(page.getByLabel("适用条件", { exact: true })).toBeEditable();
    await expect(page.getByLabel("例外情况", { exact: true })).toBeEditable();
    await page.getByLabel("适用条件", { exact: true }).fill("收到申请时");
    await page.getByLabel("例外情况", { exact: true }).fill("无");
    await checkStructure(page, "r4-authority-repair", { legacy: true });
    await shot(page, "r4-authority-repair");
    // Save the edited draft before moving to the next specialized editor.
    const save = page.waitForResponse(
      (response) =>
        response.request().method() === "PUT" &&
        new URL(response.url()).pathname ===
          "/api/authority/assets/" + authority.entity.id,
    );
    await button(page, "保存编辑稿").click();
    expect((await save).ok()).toBe(true);
    const authoritySaved = await read(
      page,
      "/api/authority/assets/" + authority.entity.id,
    );
    expect(authoritySaved.entity.payload.authority_spec.conditions).toBe(
      "收到申请时",
    );
    expect(authoritySaved.entity.payload.authority_spec.exceptions).toBe("无");
    const skill = await create(page, "/api/skills", {
      title: "合成核对技能",
      package_name: "r4-review-capture",
    });
    await page.goto("/skill-development/" + skill.entity.id);
    await checkStructure(page, "r4-skill-page", { legacy: true });
    await button(page, "修改名称与业务说明").click();
    const dialog = page.getByRole("dialog");
    await expect(
      dialog.getByLabel("无需关联业务依据的说明", { exact: true }),
    ).toBeEditable();
    await expect(button(dialog, "添加出处")).toBeEnabled();
    await button(dialog, "添加出处").click();
    await expect(
      dialog.getByLabel("出处材料 1", { exact: true }),
    ).toBeVisible();
    await expect(
      dialog.getByLabel("原文引用 1", { exact: true }),
    ).toBeEditable();
    await shot(page, "r4-skill-repair");
    await assertNoLeaks(page, "R4 skill repair");
  } finally {
    await owner.context.close();
  }
});
