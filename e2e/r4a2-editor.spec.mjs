import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
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
import {
  acceptItem,
  confirmOmissions,
  pickItem,
  readProposal,
  selected,
  startRegulation,
} from "./r4a1-changeset-support.mjs";

const detail = async (page, id) =>
  (await read(page, "/api/v2/elements/" + id)).data;
const entity = (page, id) => read(page, "/api/entities/" + id);
const history = (page, id) => read(page, "/api/entities/" + id + "/versions");

async function request(page, path, body, method = "POST") {
  return page.evaluate(
    async ({ path, body, method }) => {
      const session = await (await fetch("/api/auth/session")).json();
      const response = await fetch(path, {
        method,
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": session.csrf_token,
          "X-Workspace-Id": session.workspace.tenant_id,
        },
        body: JSON.stringify(body),
      });
      return { status: response.status, data: await response.json() };
    },
    { path, body, method },
  );
}

async function setupWrite(page, path, body, method) {
  const result = await request(page, path, body, method);
  expect(result.status, JSON.stringify(result.data)).toBe(200);
  return result.data;
}

async function oldAction(page, id, action, target, expected = 200) {
  await page.goto("/assets/" + id);
  await button(page, action === "归档资产" ? "归档" : action).click();
  const modal = page.getByRole("dialog", { name: action, exact: true });
  await modal
    .getByLabel("办理意见", { exact: true })
    .fill("核对当前档案与本次修订。");
  const posted = postResponse(page, "/action");
  await button(modal, "确认" + action).click();
  const response = await posted;
  expect(response.status(), await response.text()).toBe(expected);
  if (expected === 403) {
    await expect(modal.getByRole("alert")).toContainText("其他审核人员");
    await button(modal, "取消").click();
  }
  const saved = await entity(page, id);
  expect(saved.status).toBe(target);
  return saved;
}

async function blockedFromReview(page, id) {
  const before = await entity(page, id);
  await oldAction(page, id, "审核通过", "review", 403);
  await page.goto("/govern/reviews");
  await page.locator(`[data-review-item-id="${id}"]`).click();
  await expect(button(page, a.catalog_2_5)).toBeDisabled();
  await expect(
    page
      .getByText("本次修订的编辑人和提交人需由其他审核人员办理。", {
        exact: true,
      })
      .first(),
  ).toBeVisible();
  expect((await entity(page, id)).version).toBe(before.version);
}

async function submitAndReview(builder, author, id) {
  await oldAction(builder, id, "提交审核", "review");
  await blockedFromReview(author, id);
}

test("R4-A.2 AC1 adopted rule retains authors through discard, archive, restore and kind changes", async ({
  browser,
}) => {
  const author = await login(browser, "admin"),
    builder = await login(browser, "modeler"),
    reviewer = await login(browser, "reviewer");
  try {
    const page = author.page;
    const { proposal } = await startRegulation(page, "regulation-plain");
    const ready = await confirmOmissions(page, proposal.id);
    const items = ready.items.filter(
      (item) => item.after_data.kind === "rule" && item.gates.passed,
    );
    expect(items.length).toBeGreaterThan(1);
    await acceptItem(page, items[0]);
    const adopted = (await readProposal(page, proposal.id)).items.find(
      (item) => item.id === items[0].id,
    );
    const id = adopted.applied_entity_id,
      original = await entity(page, id);
    const discarded = postResponse(page, "/changeset.decide_items:invoke");
    await button(selected(page), a.catalog_1_2).click();
    // A.2 asks to confirm abandonment of an already materialized draft.
    const confirmation = page.getByRole("dialog");
    const needsConfirmation = await Promise.race([
      confirmation.waitFor({ state: "visible" }).then(() => true),
      discarded.then(() => false),
    ]);
    if (needsConfirmation)
      await confirmation
        .getByRole("button", { name: /确认|放弃/ })
        .last()
        .click();
    expect((await discarded).status()).toBe(200);
    expect((await entity(page, id)).status).toBe("archived");
    await oldAction(builder.page, id, "发起修订", "draft");
    await submitAndReview(builder.page, page, id);
    await oldAction(page, id, "归档资产", "archived");
    await oldAction(builder.page, id, "发起修订", "draft");
    await submitAndReview(builder.page, page, id);

    await page.goto("/assets/" + id);
    await button(page, "修订历史").click();
    const old = page
      .locator(".asset-history-entry")
      .filter({ has: page.getByText("版本 1", { exact: true }) })
      .last();
    await button(old, "还原为新草稿").click();
    const restore = page.getByRole("dialog", { name: "还原版本 V1" });
    await restore.getByLabel("还原原因").fill("按照最初内容复核原文。");
    const restored = postResponse(page, "/restore");
    await button(restore, "确认还原为草稿").click();
    expect((await restored).status()).toBe(200);
    expect((await entity(page, id)).payload.r2_review.revision_id).toBe(
      original.payload.r2_review.revision_id,
    );
    await submitAndReview(builder.page, page, id);
    await oldAction(reviewer.page, id, "审核通过", "approved");
    await oldAction(reviewer.page, id, "发布资产", "published");

    await page.goto("/build/changesets/" + proposal.id);
    const change = items[1];
    await acceptItem(page, change);
    const source = (await readProposal(page, proposal.id)).items.find(
      (item) => item.id === change.id,
    );
    const started = await entity(page, source.applied_entity_id);
    await builder.page.goto("/build/changesets/" + proposal.id);
    await pickItem(builder.page, source);
    await button(selected(builder.page), a.catalog_1_1).click();
    const form = builder.page.getByRole("dialog");
    await form.getByRole("combobox", { name: c.r2_kind }).click();
    await builder.page
      .locator(".ant-select-item-option")
      .filter({ hasText: "知识" })
      .click();
    const saving = postResponse(builder.page, "/changeset.decide_items:invoke");
    await button(form, a.r2_save).click();
    const changed = await saving;
    expect(changed.status(), await changed.text()).toBe(200);
    const converted = await detail(builder.page, started.id);
    expect(converted.entity.kind).toBe("knowledge");
    expect(converted.entity.payload.r2_review.revision_id).toBe(
      started.payload.r2_review.revision_id,
    );
    await completeAndSubmit(builder.page, started.id);
    await blockedFromReview(page, started.id);
    await publishInCenter(reviewer.page, started.id);
  } finally {
    await Promise.all([
      author.context.close(),
      builder.context.close(),
      reviewer.context.close(),
    ]);
  }
});

async function completeAndSubmit(page, id) {
  const before = await detail(page, id);
  await page.goto("/ontology/elements/" + id + "?repair=1");
  const panel = page.getByRole("region", { name: c.r4a1_legacy_repair_title });
  const definition = before.gates.fields.find((f) => f.key === "definition");
  if (definition && !definition.value)
    await panel
      .getByLabel(definition.label, { exact: true })
      .fill("核对纸质凭证的登记方法");
  for (const field of before.gates.fields) {
    if (!field.value && ["unit", "grain"].includes(field.key))
      await panel
        .getByLabel(field.label, { exact: true })
        .fill(field.key === "unit" ? "份" : "每份登记");
  }
  for (const check of before.gates.checks.filter(
    (check) => check.status === "fail" && check.can_waive,
  )) {
    await panel
      .getByLabel(check.label + " · " + c.r4a1_legacy_application, {
        exact: true,
      })
      .fill("请独立核对纸质原件和经办登记。");
  }
  const save = postResponse(page, "/element.update_draft:invoke");
  await button(panel, c.r4a1_legacy_save).click();
  const saved = await save;
  expect(saved.status(), await saved.text()).toBe(200);
  const submit = postResponse(page, "/element.submit_draft:invoke");
  await button(panel, c.r4a1_legacy_submit).click();
  const response = await submit;
  expect(response.status(), await response.text()).toBe(200);
  expect((await entity(page, id)).status).toBe("review");
}

async function publishInCenter(page, id, action = a.catalog_2_5) {
  await page.goto("/govern/reviews");
  await page.locator(`[data-review-item-id="${id}"]`).click();
  const region = page.locator(`[data-review-id="${id}"]`);
  const current = await detail(page, id);
  for (const check of current.gates.checks.filter(
    (check) => check.status === "fail",
  )) {
    expect(check.can_waive).toBe(true);
    await region
      .getByLabel(check.label + " · " + c.r4_gate_waiver, { exact: true })
      .fill("已独立核对原件、登记记录并确认适用边界。");
  }
  if (action === a.catalog_2_4) {
    await page.screenshot({
      path: resolve(process.env.E2E_EVIDENCE_DIR, "r4a2-approved-review.png"),
      animations: "disabled",
    });
  }
  await button(page, action).click();
  const dialog = page.getByRole("dialog", { name: action });
  const saving = postResponse(page, "/" + id + ":decide");
  await button(dialog, action).click();
  const result = await saving;
  expect(result.status(), await result.text()).toBe(200);
  return entity(page, id);
}

async function punctuate(page, id) {
  await page.goto("/assets/" + id);
  await button(page, "编辑内容").click();
  const modal = page.getByRole("dialog"),
    summary = modal.getByLabel("摘要", { exact: true });
  await summary.fill((await summary.inputValue()) + "。");
  await modal.getByLabel("修改说明", { exact: true }).fill("补齐句末标点。");
  const patch = page.waitForResponse(
    (r) =>
      r.request().method() === "PATCH" &&
      r.url().endsWith("/api/entities/" + id),
  );
  await button(modal, "保存修改").click();
  expect((await patch).status()).toBe(200);
}

async function verifyImporters(author, builder, reviewer, id) {
  await punctuate(builder, id);
  await completeAndSubmit(builder, id);
  await blockedFromReview(author, id);
  const pending = await entity(author, id);
  const blocked = await request(
    author,
    "/api/v2/capabilities/quality.waive_gates:invoke",
    {
      entity_id: id,
      version: pending.version,
      reason: "核对申请",
      waivers: { QG1: "核对原件" },
    },
  );
  expect(blocked.status).toBe(403);
  expect(blocked.data.error.code).toBe("SEPARATION_OF_DUTIES");
  expect((await entity(author, id)).version).toBe(pending.version);
  expect((await publishInCenter(reviewer, id)).status).toBe("published");
}

test("R4-A.2 AC2 Excel rewrites an existing item and an experience revision updates the same knowledge", async ({
  browser,
}) => {
  const author = await login(browser, "admin"),
    builder = await login(browser, "modeler"),
    reviewer = await login(browser, "reviewer");
  try {
    const id = "D.05.89.001",
      title = unique("移交登记册数");
    await setupWrite(builder.page, "/api/entities", {
      id,
      kind: "data",
      title,
      payload: {
        definition: "纸质移交登记册的原始数量",
        unit: "册",
        grain: "每次移交",
      },
    });
    await author.page.goto("/assets?kind=data");
    const downloading = author.page.waitForEvent("download");
    await author.page.getByRole("link", { name: "下载标准模板" }).click();
    const template = resolve(
        process.env.E2E_EVIDENCE_DIR,
        "editor-template.xlsx",
      ),
      output = resolve(process.env.E2E_EVIDENCE_DIR, "editor-records.xlsx");
    await (await downloading).saveAs(template);
    execFileSync(process.env.E2E_PYTHON, [
      "-c",
      [
        "from openpyxl import load_workbook",
        "import sys",
        "book = load_workbook(sys.argv[1])",
        "sheet = book['资产导入']",
        "values = {'数据项编码': sys.argv[3], '二级分类':'档案分类','三级分类':'纸质档案','数据项名称':sys.argv[4], '业务含义':'按纸质登记簿计算移交册数','数据形态':'结构化','资产归属':'经营管理','敏感级别':'内部','单位':'册','粒度':'每次移交'}",
        "for cell in sheet[1]:",
        "    if cell.value in values: sheet.cell(2,cell.column,values[cell.value])",
        "book.save(sys.argv[2])",
        "book.close()",
      ].join("\n"),
      template,
      output,
      id,
      title,
    ]);
    const posting = postResponse(author.page, "/asset-import/data");
    await author.page
      .getByLabel("选择数据资产台账", { exact: true })
      .setInputFiles(output);
    const imported = await posting;
    expect(imported.status(), await imported.text()).toBe(200);
    expect((await imported.json()).updated).toBe(1);
    expect((await entity(author.page, id)).payload.definition).toBe(
      "按纸质登记簿计算移交册数",
    );
    expect(
      (await history(author.page, id)).items.some(
        (r) => r.snapshot.payload.definition === "纸质移交登记册的原始数量",
      ),
    ).toBe(true);
    await verifyImporters(author.page, builder.page, reviewer.page, id);

    const narrative =
      "移交纸质文件时，我先把登记簿与实际卷宗逐项比对，再请接收人员签收。";
    const experience = await setupWrite(
      builder.page,
      "/api/experience/sessions",
      { title: unique("纸质档案交接"), context: narrative, mode: "structured" },
    );
    const recordId = experience.entity.id;
    const original = await setupWrite(
      builder.page,
      "/api/experience/" + recordId + "/adopt",
      {
        title: unique("纸质档案移交方法"),
        summary: "登记后逐项比对卷宗",
        content: narrative,
        boundary: "仅适用于纸质卷宗。",
        questions: "",
      },
    );
    const knowledgeId = original.entity.id;
    const before = await read(author.page, "/api/experience/" + recordId);
    const candidate = before.entity.payload.knowledge_candidates.find(
      (item) => item.adopted_entity_id === knowledgeId,
    );
    await author.page.goto(
      "/experience?record=" + recordId + "&view=ideas&state=adopted",
    );
    await button(author.page, "核对与编辑").click();
    const record = author.page.getByLabel("章节正文", { exact: true });
    const revised = (await record.inputValue()) + "接收人员当面确认双方签字。";
    await record.fill(revised);
    const updating = postResponse(
      author.page,
      "/candidates/" + candidate.id + "/review",
    );
    await button(author.page, "确认并修订关联知识").click();
    const updated = await updating;
    expect(updated.status(), await updated.text()).toBe(200);
    expect((await updated.json()).entity_id).toBe(knowledgeId);
    const changed = await entity(author.page, knowledgeId);
    expect(changed.payload.content).toContain("接收人员当面确认双方签字");
    expect(changed.version).toBe(original.entity.version + 1);
    await verifyImporters(
      author.page,
      builder.page,
      reviewer.page,
      knowledgeId,
    );
  } finally {
    await Promise.all([
      author.context.close(),
      builder.context.close(),
      reviewer.context.close(),
    ]);
  }
});

test("R4-A.2 AC11 old malformed rules can revise, archive and return without changing expression or evidence", async ({
  browser,
}) => {
  const owner = await login(browser, "superadmin");
  try {
    for (const [suffix, action, target] of [
      ["REVISE", "发起修订", "draft"],
      ["ARCHIVE", "归档资产", "archived"],
      ["RETURN", "退回修改", "returned"],
    ]) {
      const id = "R4A2-OLD-RULE-" + suffix;
      const before = await entity(owner.page, id);
      expect(before.payload.judgement.applies_to).toBeNull();
      const after = await oldAction(owner.page, id, action, target);
      expect(after.payload.judgement).toEqual(before.payload.judgement);
      expect(after.version).toBe(before.version + 1);
      const checks = (await detail(owner.page, id)).gates.checks;
      expect(checks.find((item) => item.code === "QG3").status).toBe("fail");
    }
    const id = "R4A2-OLD-APPROVED-PAPER";
    const before = await entity(owner.page, id);
    expect(before.status).toBe("approved");
    const after = await publishInCenter(owner.page, id, a.catalog_2_4);
    expect(after.status).toBe("published");
    expect(after.version).toBe(before.version + 1);
    expect(after.activities.filter((row) => row.action === "审核通过")).toEqual(
      before.activities.filter((row) => row.action === "审核通过"),
    );
    expect(after.activities.some((row) => row.action === "发布检查豁免")).toBe(
      true,
    );
  } finally {
    await owner.context.close();
  }
});
