import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { writeFileSync } from "node:fs";
import { expect, test } from "./runtime.mjs";
import {
  a,
  button,
  c,
  login,
  postResponse,
  read,
  unique,
} from "./r3-support.mjs";
import { selectSavedViewMode } from "./select-saved-view-mode.mjs";
import { startRegulation } from "./r4a1-changeset-support.mjs";

const output = (filename) => resolve(process.env.E2E_EVIDENCE_DIR, filename);
const detail = async (page, id) =>
  (await read(page, "/api/v2/elements/" + encodeURIComponent(id))).data;
function receipt(name, data) {
  writeFileSync(
    output("r4a2-ac12-" + name + ".json"),
    JSON.stringify(data, null, 2),
  );
}
async function legacyAction(page, id, action, reason, status) {
  await page.goto("/assets/" + encodeURIComponent(id));
  await button(page, action).click();
  const modal = page.getByRole("dialog", { name: action, exact: true });
  await modal.getByLabel("办理意见", { exact: true }).fill(reason);
  const response = postResponse(page, "/action");
  await button(modal, "确认" + action).click();
  const result = await response;
  expect(result.status(), await result.text()).toBe(200);
  const saved = (await detail(page, id)).entity;
  expect(saved.id).toBe(id);
  expect(saved.status).toBe(status);
  return saved;
}
async function legacyReview(page, id) {
  const approved = await legacyAction(
    page,
    id,
    "审核通过",
    "已独立核对填报内容及逐字依据。",
    "approved",
  );
  const published = await legacyAction(
    page,
    id,
    "发布资产",
    "同意发布核准的本次版本。",
    "published",
  );
  expect(published.version).toBe(approved.version + 1);
  return published;
}
async function repairAndSubmit(page, id) {
  const before = await detail(page, id);
  await page.goto("/ontology/elements/" + encodeURIComponent(id));
  const panel = page.getByRole("region", { name: c.r4a1_legacy_repair_title });
  const values = {
    scope: "本机构合同付款经办事项",
    definition: "经办人员核对合同付款事项的业务要求",
    conditions: "提交付款申请时",
    exceptions: "遇争议由经办人员登记后复核",
    valid_from: "2026-01-01",
  };
  for (const f of before.gates.fields) {
    if (!f.value && values[f.key]) {
      const field = panel.getByLabel(f.label, { exact: true });
      await field.fill(values[f.key]);
      if (f.key === "valid_from") await field.press("Enter");
    }
  }
  if (
    before.entity.kind === "rule" &&
    !before.entity.payload.authority_spec &&
    !before.gates.fields.some((f) => f.key.startsWith("subject:"))
  ) {
    const org = before.applicability_options.find(
      (row) => row.kind === "organization",
    );
    expect(org).toBeTruthy();
    await panel.getByRole("combobox", { name: c.r4a1_legacy_subject }).click();
    await page.getByTitle(org.title, { exact: true }).last().click();
  }
  const source = before.gates.checks.find((check) => check.code === "QG1");
  if (source.status === "fail") {
    await panel
      .getByLabel(source.label + " · " + c.r4a1_legacy_application, {
        exact: true,
      })
      .fill("纸质岗位授权书尚未归档，申请审核人员独立查核。");
  }
  const saving = postResponse(page, "/element.update_draft:invoke");
  await button(panel, c.r4a1_legacy_save).click();
  const saved = await saving;
  expect(saved.status(), await saved.text()).toBe(200);
  const refreshed = await detail(page, id);
  expect(refreshed.gates.ready_to_submit, JSON.stringify(refreshed.gates)).toBe(
    true,
  );
  const submitting = postResponse(page, "/element.submit_draft:invoke");
  await button(panel, c.r4a1_legacy_submit).click();
  expect((await submitting).status()).toBe(200);
  expect((await detail(page, id)).entity.status).toBe("review");
  return refreshed.gates;
}

test("R4-A.2 AC12 original material candidate is adopted on the legacy material page and independently published", async ({
  browser,
}) => {
  const builder = await login(browser, "modeler"),
    reviewer = await login(browser, "reviewer");
  try {
    await selectSavedViewMode(builder.page, "professional");
    const { proposal } = await startRegulation(
      builder.page,
      "regulation-plain",
    );
    const item = proposal.items.find((i) => i.after_data.kind === "rule");
    expect(item).toBeTruthy();
    const page = builder.page;
    await page.goto("/materials/" + proposal.source_material_id);
    const card = page
      .locator("article")
      .filter({ has: button(page, item.after_data.title) });
    const adopting = postResponse(page, "/adopt");
    await button(card, "采纳为草稿").click();
    const response = await adopting;
    expect(response.status(), await response.text()).toBe(200);
    const created = (await response.json()).entity;
    expect(created.status).toBe("draft");
    expect(created.payload.source_refs[0].anchor_id).toBeTruthy();
    const gates = await repairAndSubmit(page, created.id);
    expect(gates.checks.every((check) => check.status !== "fail")).toBe(true);
    const final = await legacyReview(reviewer.page, created.id);
    receipt("candidate", {
      material: proposal.source_material_id,
      candidate: item.after_data.id,
      entity: final.id,
      version: final.version,
      status: final.status,
    });
  } finally {
    await builder.context.close();
    await reviewer.context.close();
  }
});

test("R4-A.2 AC12 original Excel template imports three measured data items and each publishes in the old UI", async ({
  browser,
}) => {
  const builder = await login(browser, "modeler"),
    reviewer = await login(browser, "reviewer");
  try {
    const page = builder.page;
    await page.goto("/assets?kind=data");
    const downloading = page.waitForEvent("download");
    await page.getByRole("link", { name: "下载标准模板" }).click();
    const template = output("ac12-template.xlsx"),
      filled = output("ac12-intake.xlsx");
    await (await downloading).saveAs(template);
    execFileSync(process.env.E2E_PYTHON || "python", [
      resolve("e2e/r4a2_workbook.py"),
      template,
      filled,
    ]);
    const chooser = page.waitForEvent("filechooser");
    await button(page, "Excel 一键导入").click();
    const fileChooser = await chooser;
    const [response] = await Promise.all([
      postResponse(page, "/asset-import/data"),
      fileChooser.setFiles(filled),
    ]);
    expect(response.status(), await response.text()).toBe(200);
    const summary = await response.json();
    expect([summary.total, summary.created, summary.updated]).toEqual([
      3, 3, 0,
    ]);
    await expect(page.getByText("台账导入完成", { exact: true })).toBeVisible();
    const result = [];
    for (const [i, unit] of ["份", "人", "页"].entries()) {
      const id = "D.05.88.00" + (i + 1);
      const before = await detail(page, id);
      expect(before.entity.payload.unit).toBe(unit);
      expect(before.entity.payload.grain).toBe("每笔归档记录");
      expect(before.entity.payload.source_refs[0].quote).toContain(
        "单位：" + unit,
      );
      const row = before.entity.payload.source_refs[0];
      for (const key of ["definition", "unit", "grain"]) {
        const field = before.gates.fields.find((item) => item.key === key);
        expect(field.source).toBe("system");
        expect(field.anchor_id).toBe(row.anchor_id);
        expect(field.source_quote).toContain(field.value);
      }
      expect(before.gates.checks.find((c) => c.code === "QG1").status).toBe(
        "pass",
      );
      expect(before.gates.checks.find((c) => c.code === "QG2").status).toBe(
        "pass",
      );
      await legacyAction(
        page,
        id,
        "提交审核",
        "请独立核对原始工作表。",
        "review",
      );
      const final = await legacyReview(reviewer.page, id);
      result.push({
        id: final.id,
        status: final.status,
        unit: final.payload.unit,
        source: final.payload.source_refs[0].anchor_id,
      });
    }
    receipt("workbook", { material: summary.material_id, rows: result });
  } finally {
    await builder.context.close();
    await reviewer.context.close();
  }
});

test("R4-A.2 AC12 original authority creation reaches independent publication", async ({
  browser,
}) => {
  const builder = await login(browser, "modeler"),
    reviewer = await login(browser, "reviewer");
  try {
    const page = builder.page;
    await selectSavedViewMode(page, "professional");
    await page.goto("/authority");
    await button(page.locator("header.page-head"), "新建权责规范").click();
    const form = page.getByRole("dialog", { name: "新建权责规范" });
    await form.getByRole("tab", { name: "岗位权责规范" }).click();
    const title = unique("资料归档岗位权责");
    await form.getByLabel("规范名称", { exact: true }).fill(title);
    const orgs = form.getByRole("combobox", { name: /主要关联机构/ });
    await orgs.selectOption({ index: 1 });
    const creating = postResponse(page, "/authority/assets");
    await button(form, "建立编辑稿").click();
    const created = await creating;
    expect(created.status()).toBe(200);
    const id = (await created.json()).entity.id;
    await expect(page).toHaveURL((url) => url.pathname === "/authority/" + id);
    for (const [section, content] of [
      ["主要职责", "逐项登记移交的合同资料并核对归档记录。"],
      ["行权范围与权限边界", "仅可确认交接资料，不代替业务审批。"],
      ["行权程序与控制要求", "核对经办人与交接登记，对差异逐项复核。"],
      ["责任落实与履职记录", "按归档凭证保留移交日期、经办人和原件目录。"],
    ]) {
      await page.getByLabel(section + "内容", { exact: true }).fill(content);
    }
    await page.getByRole("tab", { name: "适用范围与关联" }).click();
    for (const [field, value] of [
      ["规范目的", "保证资料移交可核查"],
      ["制度与授权依据", "内部档案岗位授权书"],
      ["适用事项", "合同资料交接与归档"],
      ["触发条件", "经办人提交合同资料时"],
      ["例外情况", "有争议的资料单独登记"],
      ["生效日期", "2026-01-01"],
    ]) {
      await page.getByLabel(field, { exact: true }).fill(value);
    }
    const saving = page.waitForResponse(
      (r) =>
        r.request().method() === "PUT" &&
        new URL(r.url()).pathname.endsWith("/authority/assets/" + id),
    );
    await button(page, "保存编辑稿").first().click();
    const updated = await saving;
    expect(updated.status(), await updated.text()).toBe(200);
    const gates = await repairAndSubmit(page, id);
    const source = gates.checks.find((check) => check.code === "QG1");
    expect(source.status).toBe("fail");
    const review = reviewer.page;
    await review.goto("/govern/reviews");
    await review.locator('[data-review-item-id="' + id + '"]').click();
    const current = review.locator('[data-review-id="' + id + '"]');
    await current
      .getByLabel(source.label + " · " + c.r4_gate_waiver, { exact: true })
      .fill("已独立查阅岗位授权书签收记录，同意本次办理。");
    await button(review, a.catalog_2_5).click();
    const modal = review.getByRole("dialog", { name: a.catalog_2_5 });
    const deciding = postResponse(review, ":decide");
    await button(modal, a.catalog_2_5).click();
    const decided = await deciding;
    expect(decided.status(), await decided.text()).toBe(200);
    const final = (await detail(review, id)).entity;
    expect([final.id, final.title, final.status]).toEqual([
      id,
      title,
      "published",
    ]);
    receipt("authority", {
      id,
      title,
      status: final.status,
      orgs: final.payload.authority_spec.org_ids,
      waived: source.code,
    });
  } finally {
    await builder.context.close();
    await reviewer.context.close();
  }
});

test("R4-A.2 AC12 pre-upgrade approved asset publishes from the original asset detail", async ({
  browser,
}) => {
  const reviewer = await login(browser, "superadmin");
  try {
    const id = "R4A2-OLD-APPROVED";
    const before = await detail(reviewer.page, id);
    expect(before.entity.status).toBe("approved");
    const published = await legacyAction(
      reviewer.page,
      id,
      "发布资产",
      "核对升级前独立审核结果及原文后发布。",
      "published",
    );
    expect(published.version).toBe(before.entity.version + 1);
    receipt("upgraded", {
      id,
      before: before.entity.status,
      after: published.status,
      version: published.version,
    });
  } finally {
    await reviewer.context.close();
  }
});
