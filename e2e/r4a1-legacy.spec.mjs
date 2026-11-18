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
import { assertNoLeaks } from "./r3-structure.mjs";
import {
  assertPublishedRepair,
  assertVisibleFacts,
} from "./r4a1-legacy-contracts.mjs";

const application =
  "此项为手工维护的业务说明，原始纸质材料尚未归档；请审核人员核实经办记录。";
const waiver =
  "已独立核对经办记录和纸质材料，同意本次出处豁免，后续补充归档材料。";
const detail = async (page, id) =>
  (await read(page, "/api/v2/elements/" + encodeURIComponent(id))).data;
const screenshot = (page, name) =>
  page.screenshot({
    path: resolve(process.env.E2E_EVIDENCE_DIR, name + ".png"),
    animations: "disabled",
  });

async function createOld(page, kind) {
  await page.goto("/assets?kind=" + kind);
  await button(page, c.r4a1_legacy_create).click();
  const modal = page.getByRole("dialog", { name: c.r4a1_legacy_create_title });
  const title = unique(
    kind === "data" ? "人工登记的归档份数" : "纸质资料归档要求",
  );
  await modal.getByLabel(c.r4a1_legacy_name, { exact: true }).fill(title);
  await modal
    .getByLabel(c.r4a1_legacy_content, { exact: true })
    .fill(
      kind === "data"
        ? "记录经办人员完成归档的资料份数，便于核对归档进度。"
        : "经办人员完成资料核对后，将纸质资料交档案人员归档。",
    );
  await screenshot(page, "r4a1-legacy-create");
  const response = postResponse(page, "/element.create_draft:invoke");
  await button(modal, c.r4a1_legacy_create_save).click();
  const saved = await response;
  expect(saved.status()).toBe(200);
  const created = (await saved.json()).data;
  await expect(page).toHaveURL(new RegExp("/assets/" + created.entity.id));
  expect((await detail(page, created.entity.id)).entity.status).toBe("draft");
  return created;
}
async function oldAction(page, action, reason) {
  await button(page, action).click();
  const modal = page.getByRole("dialog", { name: action, exact: true });
  await modal.getByLabel("办理意见", { exact: true }).fill(reason);
  const response = postResponse(page, "/action");
  await button(modal, "确认" + action).click();
  return { response: await response, modal };
}
async function blockedToRepair(page, id) {
  await page.goto("/assets/" + encodeURIComponent(id));
  const { response, modal } = await oldAction(
    page,
    "提交审核",
    "请独立审核本次修订。",
  );
  expect(response.ok()).toBe(false);
  await expect(modal.getByRole("alert")).toBeVisible();
  await expect(modal.getByText(c.r4a1_legacy_sod)).toBeVisible();
  await screenshot(page, "r4a1-legacy-blocked");
  await modal
    .getByRole("link", { name: c.r4a1_legacy_repair, exact: true })
    .click();
  await expect(page).toHaveURL(/\/ontology\/elements\//);
  await expect(
    page.getByRole("region", { name: c.r4a1_legacy_repair_title }),
  ).toBeVisible();
}
async function repair(page, id) {
  const before = await detail(page, id);
  const panel = page.getByRole("region", { name: c.r4a1_legacy_repair_title });
  const values = {
    definition:
      before.entity.kind === "data"
        ? "按每次经办记录统计完成归档的资料份数"
        : before.entity.kind === "knowledge"
          ? "帮助经办人员核对资料并保留处理记录的方法说明"
          : "明确经办人员核对并归档纸质资料的职责要求",
    unit: "份",
    grain: "每次经办记录",
    scope: "本单位经办的纸质资料归档事项",
    conditions: "资料核对完成并可移交归档时",
    exceptions: "涉及争议的资料暂缓归档并记录原因",
    valid_from: "2026-01-01",
    boundary: "仅供内部资料核对，不替代正式审核结论",
    usage_conditions: "具备经办记录和待核对的原始资料",
  };
  for (const field of before.gates.fields) {
    if (values[field.key] && (!field.value || field.key === "definition"))
      await panel
        .getByLabel(field.label, { exact: true })
        .fill(values[field.key]);
  }
  if (
    before.entity.kind === "rule" &&
    !before.gates.fields.some((field) => field.key.startsWith("subject:"))
  ) {
    const target = before.applicability_options.find(
      (option) => option.kind === "organization",
    );
    expect(target).toBeTruthy();
    await panel.getByRole("combobox", { name: c.r4a1_legacy_subject }).click();
    await page.getByTitle(target.title, { exact: true }).last().click();
  }
  const source = before.gates.checks.find((check) => check.code === "QG1");
  await panel
    .getByLabel(source.label + " · " + c.r4a1_legacy_application, {
      exact: true,
    })
    .fill(application);
  const saved = postResponse(page, "/element.update_draft:invoke");
  await button(panel, c.r4a1_legacy_save).click();
  const response = await saved;
  expect(response.status(), await response.text()).toBe(200);
  await expect(page.getByRole("status")).toContainText(c.r4a1_legacy_saved);
  const repaired = await detail(page, id);
  expect(
    repaired.gates.checks.find((check) => check.code === "QG1").status,
  ).toBe("fail");
  expect(
    repaired.gates.checks.find((check) => check.code === "QG1")
      .application_reason,
  ).toBe(application);
  expect(
    repaired.gates.checks.find((check) => check.code === "QG2").status,
  ).toBe("pass");
  expect(repaired.gates.ready_to_submit, JSON.stringify(repaired.gates)).toBe(
    true,
  );
  await screenshot(page, "r4a1-element-repair");
  const submitted = postResponse(page, "/element.submit_draft:invoke");
  await button(page, c.r4a1_legacy_submit).click();
  expect((await submitted).status()).toBe(200);
  await expect(
    page.getByText(c.r4a1_legacy_submitted, { exact: true }),
  ).toBeVisible();
  expect((await detail(page, id)).entity.status).toBe("review");
}
async function publish(page, id) {
  await page.goto("/govern/reviews");
  await page.locator(`[data-review-item-id="${id}"]`).click();
  const current = page.locator(`[data-review-id="${id}"]`);
  await expect(current.locator("[data-waiver-application]")).toContainText(
    application,
  );
  await expect(button(page, a.catalog_2_5)).toBeDisabled();
  const row = (await read(page, "/api/v2/reviews")).data.items.find(
    (item) => item.entity.id === id,
  );
  const source = row.gates.checks.find((check) => check.code === "QG1");
  await current
    .getByLabel(source.label + " · " + c.r4_gate_waiver, { exact: true })
    .fill(waiver);
  await current.locator("[data-governance-facts]").scrollIntoViewIfNeeded();
  await screenshot(page, "r4a1-review-facts");
  await button(page, a.catalog_2_5).click();
  const modal = page.getByRole("dialog", { name: a.catalog_2_5 });
  await expect(modal).toBeVisible();
  await expect(modal.locator("[data-waiver-application]")).toContainText(
    application,
  );
  await screenshot(page, "r4a1-publish-confirmation");
  const response = postResponse(page, "/" + id + ":decide");
  await button(modal, a.catalog_2_5).click();
  expect((await response).status()).toBe(200);
  return detail(page, id);
}

test("R4-A.1 AB7: manual data application reaches independent review but missing meaning cannot be waived", async ({
  browser,
}) => {
  const builder = await login(browser, "admin"),
    reviewer = await login(browser, "reviewer");
  try {
    const created = await createOld(builder.page, "data");
    await blockedToRepair(builder.page, created.entity.id);
    await expect(
      builder.page.locator('[data-waiver-application-code="QG2"]'),
    ).toHaveCount(0);
    await expect(button(builder.page, c.r4a1_legacy_submit)).toBeDisabled();
    await repair(builder.page, created.entity.id);
    // The same editor cannot approve the newly submitted content from either view.
    await builder.page.goto("/assets/" + created.entity.id);
    const selfReview = await oldAction(
      builder.page,
      "审核通过",
      "尝试审核本人登记的内容。",
    );
    expect(selfReview.response.status()).toBe(403);
    await expect(selfReview.modal.getByRole("alert")).toBeVisible();
    await button(selfReview.modal, "取消").click();
    await builder.page.goto("/govern/reviews");
    await builder.page
      .locator(`[data-review-item-id="${created.entity.id}"]`)
      .click();
    await expect(button(builder.page, a.catalog_2_5)).toBeDisabled();
    const published = await publish(reviewer.page, created.entity.id);
    assertPublishedRepair(published, {
      id: created.entity.id,
      kind: "data",
      previousVersion: created.entity.version,
      application,
      waiver,
    });
    await reviewer.page.goto("/ontology/elements/" + created.entity.id);
    await reviewer.page.getByText(c.r2_card_activity, { exact: true }).click();
    await expect(
      reviewer.page.getByRole("region", { name: c.r2_card_activity }),
    ).toContainText(application);
    await expect(
      reviewer.page.getByRole("region", { name: c.r2_card_activity }),
    ).toContainText(waiver);
    await assertNoLeaks(reviewer.page, "AB7 published manual data");
  } finally {
    await builder.context.close();
    await reviewer.context.close();
  }
});

test("R4-A.1 AB8: original 0.7 published data rule and knowledge revise through old UI and publish again", async ({
  browser,
}) => {
  const builder = await login(browser, "modeler"),
    reviewer = await login(browser, "reviewer");
  try {
    for (const id of [
      "DEMO07-FIELD-ATTACHMENT_READY",
      "R-AUTH-5b0009c34f1e",
      "FIXTURE-KNOWLEDGE-001",
    ]) {
      const original = await detail(builder.page, id);
      expect(original.entity.status).toBe("published");
      await builder.page.goto("/assets/" + id);
      if (original.entity.payload.authority_spec) {
        const revision = await oldAction(
          builder.page,
          "发起修订",
          "复核旧规范的适用边界。",
        );
        expect(revision.response.status()).toBe(200);
        await builder.page.getByRole("link", { name: "编排权责内容" }).click();
        await builder.page
          .getByLabel("一句话说明", { exact: true })
          .fill(original.entity.summary + "。");
        const saved = builder.page.waitForResponse(
          (response) =>
            response.request().method() === "PUT" &&
            new URL(response.url()).pathname.endsWith(
              "/authority/assets/" + id,
            ),
        );
        await button(builder.page, "保存编辑稿").first().click();
        expect((await saved).status()).toBe(200);
      } else {
        await button(builder.page, "编辑内容").click();
        const modal = builder.page.getByRole("dialog");
        await modal
          .getByLabel("摘要", { exact: true })
          .fill((original.entity.summary || original.entity.title) + "。");
        await modal
          .getByLabel("修改说明", { exact: true })
          .fill("补充标点并核对原有业务表述。");
        const saved = builder.page.waitForResponse(
          (response) =>
            response.request().method() === "PATCH" &&
            new URL(response.url()).pathname.endsWith("/entities/" + id),
        );
        await button(modal, "保存修改").click();
        expect((await saved).status()).toBe(200);
      }
      expect((await detail(builder.page, id)).entity.status).toBe("draft");
      await blockedToRepair(builder.page, id);
      await repair(builder.page, id);
      assertPublishedRepair(await publish(reviewer.page, id), {
        id,
        kind: original.entity.kind,
        previousVersion: original.entity.version,
        application,
        waiver,
      });
    }
  } finally {
    await builder.context.close();
    await reviewer.context.close();
  }
});

test("R4-A.1 AB9: old UI manually creates data and rule then repairs submits and independently publishes", async ({
  browser,
}) => {
  const builder = await login(browser, "modeler"),
    reviewer = await login(browser, "reviewer");
  try {
    for (const kind of ["data", "rule"]) {
      const created = await createOld(builder.page, kind);
      await blockedToRepair(builder.page, created.entity.id);
      await repair(builder.page, created.entity.id);
      assertPublishedRepair(await publish(reviewer.page, created.entity.id), {
        id: created.entity.id,
        kind,
        application,
        waiver,
      });
    }
  } finally {
    await builder.context.close();
    await reviewer.context.close();
  }
});

test("R4-A.1 AB10: reviewer sees actual rule facts and their sources, including effective date in confirmation", async ({
  browser,
}) => {
  const {
    startRegulation,
    confirmOmissions,
    acceptItem,
    submitReady,
    readProposal,
  } = await import("./r4a1-changeset-support.mjs");
  const builder = await login(browser, "modeler"),
    reviewer = await login(browser, "reviewer");
  try {
    const { proposal } = await startRegulation(
      builder.page,
      "regulation-plain",
    );
    const confirmed = await confirmOmissions(builder.page, proposal.id);
    const rule = confirmed.items.find(
      (item) => item.after_data.kind === "rule" && item.gates?.ready_to_submit,
    );
    expect(
      rule,
      "ordinary regulation must yield a submit-ready rule after batch confirmation",
    ).toBeTruthy();
    await acceptItem(builder.page, rule);
    await submitReady(builder.page);
    const submitted = await readProposal(builder.page, proposal.id);
    const applied = submitted.items.find((item) => item.id === rule.id);
    expect(applied.applied_status).toBe("review");
    const id = applied.applied_entity_id;
    await reviewer.page.goto("/govern/reviews");
    await reviewer.page.locator(`[data-review-item-id="${id}"]`).click();
    const current = reviewer.page.locator(`[data-review-id="${id}"]`);
    const saved = await detail(reviewer.page, id);
    const fields = saved.gates.fields.filter(
      (field) =>
        ["scope", "conditions", "exceptions", "valid_from"].includes(
          field.key,
        ) || field.key.startsWith("subject:"),
    );
    expect(fields.filter((field) => field.value)).toHaveLength(5);
    expect(fields.find((field) => field.key === "valid_from").value).toBe(
      "2026-01-01",
    );
    const visible = async (scope) =>
      scope.locator("[data-governance-field]").evaluateAll((nodes) =>
        nodes.map((node) => ({
          key: node.dataset.governanceField,
          text: node.textContent,
          source: node.querySelector("[data-fact-origin]")?.dataset.factOrigin,
        })),
      );
    assertVisibleFacts(fields, await visible(current));
    expect(fields.some((field) => field.source === "source")).toBe(true);
    expect(fields.some((field) => field.source === "batch_confirmation")).toBe(
      true,
    );
    await screenshot(reviewer.page, "r4a1-ordinary-review-facts");
    await button(reviewer.page, a.catalog_2_5).click();
    const modal = reviewer.page.getByRole("dialog", { name: a.catalog_2_5 });
    assertVisibleFacts(fields, await visible(modal));
    await expect(modal.getByText("2026-01-01", { exact: true })).toBeVisible();
    await assertNoLeaks(reviewer.page, "AB10 review and publication facts");
    await screenshot(reviewer.page, "r4a1-ordinary-publish-confirmation");
    await button(modal, a.r2_cancel).click();
  } finally {
    await builder.context.close();
    await reviewer.context.close();
  }
});
