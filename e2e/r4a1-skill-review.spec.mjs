import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { test, expect } from "./runtime.mjs";
import { login, button, read, postResponse, c } from "./r3-support.mjs";
import {
  assertPublishedSkillReview,
  assertSkillDialogFacts,
} from "./r4a1-skill-review-contracts.mjs";
import { assertNoLeaks } from "./r3-structure.mjs";
const schemaFixture = JSON.parse(
  readFileSync(
    resolve(
      process.env.E2E_APPLICATION_ROOT || ".",
      "frontend/test-fixtures/r4a1-schema-facts.json",
    ),
    "utf8",
  ),
);

const application =
  "合成技能来自经办经验，纸质方法说明待归档，请独立核对验证样例。";
const denial = "暂缺纸质方法核对记录，请补充经办人确认说明后重新申请。";
const renewed =
  "已取得经办人书面确认，原件待归档；请独立核对本版说明及验证样例。";
const waiver = "已独立核对经办人确认说明及当前验证样例，同意本次出处豁免。";
const getSkill = (page, id) => read(page, "/api/skills/" + id);
const shot = (page, name) =>
  page.screenshot({
    path: resolve(process.env.E2E_EVIDENCE_DIR, name + ".png"),
    animations: "disabled",
  });

function skillArchive() {
  const name = "r4a1-review-" + Date.now();
  const manifest = {
    schema_version: "1.0",
    applicability: schemaFixture.usage,
    inputs: schemaFixture.inputs,
    outputs: schemaFixture.outputs,
    steps: [{ title: "核对资料", instruction: "逐项核对合同金额与付款金额" }],
    entrypoint: null,
    test_cases: [
      {
        name: "资料核对",
        input: { amount: 10 },
        expected_output: { checked: true },
      },
    ],
  };
  const files = {
    "SKILL.md": `---\nname: ${name}\ndescription: ${schemaFixture.definition}\nmetadata:\n  ontology-one.manifest: assets/ontology-one.json\n---\n\n# 内部资料核对\n\n按声明步骤核对输入，保留经办记录。\n`,
    "assets/ontology-one.json": JSON.stringify(manifest),
  };
  return execFileSync(
    process.env.E2E_PYTHON || "python",
    [
      "-c",
      "import io,json,sys,zipfile\nb=io.BytesIO()\nwith zipfile.ZipFile(b,'w') as z:\n for k,v in json.load(sys.stdin).items():z.writestr(k,v)\nsys.stdout.buffer.write(b.getvalue())",
    ],
    { input: JSON.stringify(files) },
  );
}

async function apply(page, id, reason) {
  await page.goto("/ontology/elements/" + id);
  const panel = page.getByRole("region", { name: c.r4a1_legacy_repair_title });
  const check = (await getSkill(page, id)).gates.checks.find(
    (item) => item.code === "QG1",
  );
  await panel
    .getByLabel(check.label + " · " + c.r4a1_legacy_application, {
      exact: true,
    })
    .fill(reason);
  const saved = postResponse(page, "/element.update_draft:invoke");
  await button(panel, c.r4a1_legacy_save).click();
  expect((await saved).ok()).toBe(true);
  await expect(
    page.getByText(c.r4a1_legacy_saved, { exact: true }),
  ).toBeVisible();
  expect(
    (await getSkill(page, id)).gates.checks.find((item) => item.code === "QG1")
      .application_reason,
  ).toBe(reason);
}
async function submit(page, id) {
  await page.goto("/skill-development/" + id);
  await button(page, "提交审核").click();
  const dialog = page.getByRole("dialog", { name: "提交审核" });
  await dialog
    .getByLabel("技能审核办理意见", { exact: true })
    .fill("已核对本版技能说明、依赖声明及真实验证记录，请独立审核。");
  const response = postResponse(page, "/" + id + "/action");
  await button(dialog, "确认提交审核").click();
  expect((await response).ok()).toBe(true);
  await expect(dialog).not.toBeVisible();
  expect((await getSkill(page, id)).entity.status).toBe("review");
  expect((await getSkill(page, id)).capabilities.review).toBe(false);
  await expect(button(page, "审核并发布")).toHaveCount(0);
  await expect(
    page.getByText(c.r4a1_legacy_sod, { exact: true }),
  ).toBeVisible();
}

test("R4-A.1 managed skill: independent reviewer returns an application then waives and publishes through the dedicated workbench", async ({
  browser,
}) => {
  const builder = await login(browser, "admin"),
    reviewer = await login(browser, "reviewer");
  try {
    await builder.page.goto("/skill-development");
    await button(builder.page, "导入已有技能").click();
    const importing = builder.page.getByRole("dialog", {
      name: "导入已有技能",
    });
    await importing
      .getByLabel("选择已有技能文件", { exact: true })
      .setInputFiles({
        name: "business-skill.zip",
        mimeType: "application/zip",
        buffer: skillArchive(),
      });
    const uploaded = postResponse(builder.page, "/skills/import");
    await button(importing, "导入为草稿").click();
    const imported = await (await uploaded).json(),
      id = imported.entity.id;
    await expect(builder.page).toHaveURL(
      new RegExp("/skill-development/" + id),
    );
    await button(builder.page, "修改名称与业务说明").click();
    const editing = builder.page.getByRole("dialog", {
      name: "修改名称与业务说明",
    });
    await editing
      .getByLabel("技能名称", { exact: true })
      .fill("经办资料核对合成技能");
    await editing
      .getByLabel("无需关联业务依据的说明", { exact: true })
      .fill("本技能仅核对显式输入，不依赖其他数据项、规则或知识。");
    const saving = builder.page.waitForResponse(
      (response) =>
        response.request().method() === "PUT" &&
        new URL(response.url()).pathname === "/api/skills/" + id,
    );
    await button(editing, "保存说明").click();
    expect((await saving).ok()).toBe(true);
    await expect(editing).not.toBeVisible();
    const testing = postResponse(builder.page, "/" + id + "/test");
    await button(
      builder.page.locator(".sd-governance-actions"),
      "验证效果",
    ).click();
    expect((await (await testing).json()).outcome).toBe("passed");
    const tested = await getSkill(builder.page, id);
    expect(tested.last_test.report.mode).toBe("contract");
    expect(tested.gates.checks.find((item) => item.code === "QG4").status).toBe(
      "pass",
    );
    await apply(builder.page, id, application);
    await submit(builder.page, id);

    await reviewer.page.goto("/govern/reviews");
    await reviewer.page.locator(`[data-review-item-id="${id}"]`).click();
    const row = reviewer.page.locator(`[data-review-id="${id}"]`);
    await expect(button(reviewer.page, "通过并发布")).toBeDisabled();
    await row
      .getByRole("link", { name: c.r4a1_legacy_skill_link, exact: true })
      .click();
    await button(reviewer.page, "退回补充").click();
    const returning = reviewer.page.getByRole("dialog", { name: "退回补充" });
    await expect(returning.locator("[data-waiver-application]")).toContainText(
      application,
    );
    const source = tested.gates.checks.find((item) => item.code === "QG1");
    await returning
      .getByLabel(source.label + " · " + c.r4a1_legacy_denial, { exact: true })
      .fill(denial);
    await returning
      .getByLabel("技能审核办理意见", { exact: true })
      .fill(denial);
    const returned = postResponse(reviewer.page, "/" + id + "/action");
    await button(returning, "确认退回补充").click();
    expect((await returned).ok()).toBe(true);
    await expect(returning).not.toBeVisible();
    expect((await getSkill(reviewer.page, id)).entity.status).toBe("returned");
    expect(
      (await getSkill(reviewer.page, id)).gate_history.some((event) =>
        event.detail.includes(denial),
      ),
    ).toBe(true);
    await apply(builder.page, id, renewed);
    await submit(builder.page, id);

    await reviewer.page.goto("/skill-development/" + id);
    await button(reviewer.page, "审核并发布").click();
    const publishing = reviewer.page.getByRole("dialog", {
      name: "审核并发布",
    });
    await expect(publishing.locator("[data-waiver-application]")).toContainText(
      renewed,
    );

    const expectedFacts = [
      { key: "definition", values: [schemaFixture.definition] },
      {
        key: "inputs",
        values: [
          schemaFixture.amountLabel,
          c.r4a1_schema_number,
          c.r4a1_schema_required,
          "0",
          "100",
        ],
      },
      {
        key: "outputs",
        values: [
          schemaFixture.resultLabel,
          c.r4a1_schema_boolean,
          c.r4a1_schema_optional,
        ],
      },
      { key: "steps", values: [schemaFixture.steps] },
      { key: "usage_conditions", values: [schemaFixture.usage] },
    ].map((field) => ({
      ...field,
      source: "editor",
      originLabel: c.r4a1_legacy_origin_editor,
    }));
    const renderedFacts = () =>
      publishing
        .locator("[data-governance-field]")
        .evaluateAll((nodes) =>
          nodes.map((node) => ({
            key: node.dataset.governanceField,
            text: node.textContent,
            source:
              node.querySelector("[data-fact-origin]")?.dataset.factOrigin,
            originText:
              node.querySelector("[data-fact-origin]")?.textContent || "",
          })),
        );
    await expect(publishing.locator("[data-governance-facts]")).toHaveCount(1);
    assertSkillDialogFacts(await renderedFacts(), expectedFacts);
    for (const field of expectedFacts) {
      const row = publishing.locator(`[data-governance-field="${field.key}"]`);
      await expect(row).toHaveCount(1);
      await row.scrollIntoViewIfNeeded();
      await expect(row.locator("[data-fact-origin]")).toContainText(
        c.r4a1_legacy_origin_editor,
      );
    }
    const origin = publishing.locator(
      '[data-governance-field="inputs"] [data-fact-origin]',
    );
    const originalOrigin = await origin.textContent();
    await origin.evaluate((node) => {
      node.textContent = "未记录来源";
    });
    const corrupted = await renderedFacts();
    expect(() => assertSkillDialogFacts(corrupted, expectedFacts)).toThrow();
    await origin.evaluate((node, value) => {
      node.textContent = value;
    }, originalOrigin);
    assertSkillDialogFacts(await renderedFacts(), expectedFacts);
    await assertNoLeaks(
      reviewer.page,
      "managed skill publication business facts",
    );
    await publishing
      .locator('[data-governance-field="definition"]')
      .scrollIntoViewIfNeeded();
    await shot(reviewer.page, "r4a1-managed-skill-facts");

    await publishing
      .getByLabel("技能审核办理意见", { exact: true })
      .fill("已独立核对技能方法、当前验证记录和无需依赖其他要素的声明。");
    await expect(button(publishing, "确认审核并发布")).toBeDisabled();
    await publishing
      .getByLabel(source.label + " · " + c.r4_gate_waiver, { exact: true })
      .fill(waiver);
    await expect(button(publishing, "确认审核并发布")).toBeEnabled();
    await shot(reviewer.page, "r4a1-managed-skill-waiver");
    const published = postResponse(reviewer.page, "/" + id + "/action");
    await button(publishing, "确认审核并发布").click();
    expect((await published).ok()).toBe(true);
    await expect(publishing).not.toBeVisible();
    const result = await getSkill(reviewer.page, id);
    assertPublishedSkillReview(result, [application, denial, renewed, waiver]);
    expect(result.entity.status).toBe("published");
    expect(result.last_test.outcome).toBe("passed");
    expect(result.last_test.report.mode).toBe("contract");
    expect(result.gates.checks.find((item) => item.code === "QG1").status).toBe(
      "waived",
    );
    for (const code of ["QG2", "QG3", "QG4", "QG5"])
      expect(
        result.gates.checks.find((item) => item.code === code).status,
      ).toBe("pass");
    for (const reason of [application, denial, renewed, waiver])
      expect(
        result.gate_history.some((event) => event.detail.includes(reason)),
      ).toBe(true);
    await button(reviewer.page, "版本记录").click();
    await expect(reviewer.page.locator(".sd-outcome-body")).toContainText(
      waiver,
    );
    await expect(reviewer.page.locator(".sd-outcome-body")).toContainText(
      denial,
    );
    await shot(reviewer.page, "r4a1-managed-skill-records");
  } finally {
    await builder.context.close();
    await reviewer.context.close();
  }
});
