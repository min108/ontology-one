import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { test, expect } from "./runtime.mjs";
import { c, login, read, postResponse, confirmAction } from "./r3-support.mjs";
import { checkStructure, assertNoLeaks } from "./r3-structure.mjs";
import {
  selectSavedViewMode,
  readSavedViewMode,
  openViewModeMenu,
  closeViewModeMenu,
} from "./select-saved-view-mode.mjs";

const output = process.env.E2E_EVIDENCE_DIR;
const live = (process.env.E2E_R3_LIVE || process.env.E2E_R2_LIVE) === "1";
const documentText =
  "第一条 办理付款前应核对合同约定与验收资料；验收通过后30日内完成付款申请。\n第二条 累计付款不得超过合同金额；有异议的款项应记录原因并暂停支付。\n第三条 经办人员应保存验收记录，审核人员复核付款依据。";
const button = (scope, name) =>
  scope.getByRole("button", { name, exact: true });
const selected = (page) => page.locator("[data-selected-item-id]");
async function pick(page, item) {
  await page.locator(`button[data-item-id="${item.id}"]`).click();
  await expect(selected(page)).toHaveAttribute(
    "data-selected-item-id",
    item.id,
  );
}
async function decide(page, item, name) {
  await pick(page, item);
  const decided = postResponse(page, "/changeset.decide_items:invoke");
  const applied =
    name === "采纳" ? postResponse(page, "/changeset.apply:invoke") : null;
  await button(selected(page), name).click();
  const result = (await (await decided).json()).data;
  if (applied) expect((await applied).ok()).toBe(true);
  return result;
}
async function submit(page) {
  const pending = postResponse(page, "/changeset.apply_and_submit:invoke");
  await button(page, "采纳并提交审核").click();
  await confirmAction(page, "采纳并提交审核");
  return (await (await pending).json()).data;
}
async function selectReview(page, title) {
  await page
    .locator('[aria-label="选择一项待审内容"] button')
    .filter({ hasText: title })
    .click();
}

// Serial mode stops after any failure: three reported successes are consecutive.
test.describe.configure({ mode: "serial" });
for (
  let iteration = 1;
  iteration <= Number(process.env.E2E_R3_REPETITIONS || 1);
  iteration++
) {
  test(`R3 A2–A13 UI mainline ${iteration}: hand in, check, independently publish, ask, export`, async ({
    browser,
  }) => {
    mkdirSync(output, { recursive: true });
    const times = [],
      reportChecks = [],
      reportReads = [],
      reportResponses = [],
      started = Date.now();
    let checkpoint = started,
      firstNumericBlocked = false,
      revisionUsed = false,
      success = false;
    const record = (action) => {
      times.push({
        action,
        seconds: Math.round((Date.now() - checkpoint) / 100) / 10,
        passed: true,
      });
      checkpoint = Date.now();
    };
    const builder = await login(browser, "modeler"),
      reviewer = await login(browser, "reviewer");
    const page = builder.page;
    page.on("request", (request) => {
      if (
        /\/api\/agent\/sessions\/[^/]+\/turns$/.test(
          new URL(request.url()).pathname,
        ) &&
        request.method() === "POST" &&
        request.postDataJSON()?.revision_of
      )
        revisionUsed = true;
    });
    page.on("response", (response) => {
      if (
        !/\/api\/agent\/sessions\/[^/]+\/runs\/[^/]+\/report$/.test(
          new URL(response.url()).pathname,
        ) ||
        response.request().method() !== "POST"
      )
        return;
      reportResponses.push(response);
      if (response.status() === 422)
        reportReads.push(
          (async () => {
            const error = await response.json(),
              detail = error.error?.details || error.detail || {};
            reportChecks.push({
              code: detail.code,
              ...(!live ? { issues: detail.issues } : {}),
            });
            if (reportResponses[0] === response)
              firstNumericBlocked = detail.code === "NUMERIC_UNVERIFIED";
          })(),
        );
    });
    try {
      await selectSavedViewMode(page, "simple");
      await expect(
        page.getByRole("heading", { name: "工作台", exact: true }),
      ).toBeVisible();
      await checkStructure(page, `home-${iteration}`);
      const dropped = await page.evaluateHandle((text) => {
        const data = new DataTransfer();
        data.items.add(
          new File([text], `【演示】付款核对制度-${Date.now()}.txt`, {
            type: "text/plain",
          }),
        );
        return data;
      }, documentText);
      await page.dispatchEvent("body", "dragenter", { dataTransfer: dropped });
      await page.dispatchEvent("body", "drop", { dataTransfer: dropped });
      const intake = page.getByRole("dialog");
      await expect(intake).toBeVisible();
      await expect(
        intake.locator("input[required],textarea[required],select[required]"),
      ).toHaveCount(0);
      const starting = postResponse(page, "/recipe.start:invoke");
      await button(intake, "开始整理").click();
      const recipe = (await (await starting).json()).data;
      expect(Boolean(recipe.id)).toBe(true);
      await expect(page).toHaveURL(/\/build\/intake/);
      await page.locator(".harness-activity summary").click();
      await button(page, "刷新后台任务").click();
      const trayItem = page
        .locator(".harness-activity-item")
        .filter({ hasText: "制度整理" })
        .first();
      await expect(trayItem).toBeVisible();
      await expect(trayItem).toContainText(/整理|提炼|完成|排队|处理/);
      await page.locator(".harness-activity summary").click();
      await expect(
        page.getByRole("button", { name: "核对建设方案", exact: true }),
      ).toBeVisible({ timeout: 180_000 });
      await expect(page.locator("[data-page-header]")).toContainText(
        /整理出\s*\d+\s*条/,
      );
      await checkStructure(page, `intake-${iteration}`);
      record("A2 拖入、零必填整理、完成条数");

      const getting = page.waitForResponse((r) =>
        /\/api\/v2\/changesets\/CS-/.test(new URL(r.url()).pathname),
      );
      await page
        .getByRole("button", { name: "核对建设方案", exact: true })
        .click();
      const proposal = (await (await getting).json()).data;
      const changesetUrl = page.url();
      expect(proposal.items.length).toBeGreaterThan(1);
      expect(
        proposal.items.some((item) => item.content_origin !== "local"),
      ).toBe(true);
      expect(proposal.items.every((item) => item.evidence.length > 0)).toBe(
        true,
      );
      await expect(button(page, "采纳并提交审核")).toBeDisabled();
      await expect(page.locator("[data-page-header]")).toContainText(
        /还有\s*\d+\s*条没处理/,
      );
      await checkStructure(page, `changeset-${iteration}`);
      const editedItem = proposal.items.find((item) =>
        item.evidence.some((ref) => ref.quote.includes("30日")),
      );
      expect(Boolean(editedItem)).toBe(true);
      const rejectedItem = proposal.items.find(
        (item) => item.id !== editedItem.id,
      );
      await pick(page, rejectedItem);
      const scroll = await page
        .locator("[data-split-list]")
        .evaluate((el) => el.scrollTop);
      await button(selected(page), "查看原文").click();
      const drawer = page.getByRole("dialog");
      await expect(drawer.locator("mark")).toHaveText(
        rejectedItem.evidence[0].quote,
      );
      await assertNoLeaks(page, "A3 source drawer open");
      expect(page.url()).toBe(changesetUrl);
      await drawer.getByRole("button", { name: /关闭|Close/ }).click();
      await expect(drawer).not.toBeVisible();
      await expect(selected(page)).toHaveAttribute(
        "data-selected-item-id",
        rejectedItem.id,
      );
      expect(
        await page.locator("[data-split-list]").evaluate((el) => el.scrollTop),
      ).toBe(scroll);
      record("A3 原文逐字高亮、选择与滚动保留");
      const rejection = await decide(page, rejectedItem, "不要");
      expect(
        rejection.items.find((item) => item.id === rejectedItem.id).decision,
      ).toBe("rejected");
      expect(
        (await read(page, "/api/v2/changesets/" + proposal.id)).data.items.find(
          (item) => item.id === rejectedItem.id,
        ).decision,
      ).toBe("rejected");
      const acceptance = await decide(page, rejectedItem, "采纳");
      expect(
        acceptance.items.find((item) => item.id === rejectedItem.id).decision,
      ).toBe("accepted");
      const beforeEdit = (
        await read(page, "/api/v2/changesets/" + proposal.id)
      ).data.items
        .filter((item) => item.applied_entity_id)
        .map((item) => item.applied_entity_id);
      await pick(page, editedItem);
      await button(selected(page), "改一改").click();
      const modal = page.getByRole("dialog", { name: "改一改", exact: true });
      const revisedTitle =
        editedItem.after_data.title + `（核对稿${iteration}）`;
      const revisedContent =
        editedItem.evidence.find((ref) => ref.quote.includes("30日")).quote +
        "\n办理前请核对适用条件。";
      await modal.getByLabel("名称", { exact: true }).fill("");
      await expect(button(modal, "保存并采纳")).toBeDisabled();
      await modal.getByLabel("名称", { exact: true }).fill(revisedTitle);
      // Preserve R2's real 422 + retained dialog + successful retry assertion.
      // Empty/oversize input is now prevented by controls. Inject one stale-client
      // extra field into the real request to preserve the server 422 recovery case.
      await modal
        .getByRole("textbox", { name: "正文与适用条件", exact: true })
        .fill(revisedContent);
      await page.route(
        "**/changeset.decide_items:invoke",
        async (route) => {
          const body = route.request().postDataJSON();
          await route.continue({
            postData: JSON.stringify({
              ...body,
              edit: { ...body.edit, unsupported: true },
            }),
          });
        },
        { times: 1 },
      );
      const invalid = postResponse(page, "/changeset.decide_items:invoke");
      await button(modal, "保存并采纳").click();
      expect((await invalid).status()).toBe(422);
      await expect(modal.getByRole("alert")).toBeVisible();
      await modal
        .getByRole("textbox", { name: "正文与适用条件", exact: true })
        .fill(revisedContent);
      const editing = postResponse(page, "/changeset.decide_items:invoke"),
        applying = postResponse(page, "/changeset.apply:invoke");
      await button(modal, "保存并采纳").click();
      const editResult = (await (await editing).json()).data;
      expect(
        editResult.items.find((item) => item.id === editedItem.id).after_data
          .content,
      ).toBe(revisedContent);
      const newIds = (await (await applying).json()).data.entity_ids.filter(
        (id) => !beforeEdit.includes(id),
      );
      expect(newIds).toHaveLength(1);
      const editedId = newIds[0];
      await expect(modal).not.toBeVisible();
      const saved = await read(page, "/api/entities/" + editedId);
      expect(saved.status).toBe("draft");
      expect(saved.payload.content || saved.payload.rule_text).toBe(
        revisedContent,
      );
      expect(saved.payload.r2_review.last_editor_id).toBe(
        (await read(page, "/api/auth/session")).user.id,
      );
      record("A4 修改正文确已保存、最后编辑人");
      await page.getByRole("link", { name: "查看草稿", exact: true }).click();
      await expect(page).toHaveURL(
        new RegExp("/ontology/elements/" + editedId),
      );
      await expect(page.locator("[data-page-header]")).toContainText("草稿");
      await expect(page.locator("[data-legacy-page]")).toHaveCount(0);
      await checkStructure(page, `draft-${iteration}`);
      await page.goto(changesetUrl);
      await expect(page.locator("button[data-item-id]").first()).toBeVisible();
      record("A7 新要素页查看草稿");
      const sure = editResult.items.filter(
        (item) =>
          item.id !== editedItem.id &&
          item.tier === "sure" &&
          item.decision === "pending" &&
          item.validation.level !== "error",
      );
      expect(sure.length).toBeGreaterThan(0);
      const bulk = postResponse(page, "/changeset.decide_items:invoke"),
        bulkApplied = postResponse(page, "/changeset.apply:invoke");
      await button(page, "全部采纳高把握").click();
      const bulkResult = (await (await bulk).json()).data;
      expect(
        sure.every(
          (item) =>
            bulkResult.items.find((x) => x.id === item.id).decision ===
            "accepted",
        ),
      ).toBe(true);
      expect((await bulkApplied).ok()).toBe(true);
      for (const item of bulkResult.items.filter(
        (item) => item.decision === "pending",
      )) {
        const result = await decide(page, item, "采纳");
        expect(result.items.find((x) => x.id === item.id).decision).toBe(
          "accepted",
        );
      }
      record("A5 不要、单条采纳、全部高把握采纳");
      await expect(button(page, "采纳并提交审核")).toBeEnabled();
      const applied = await submit(page);
      expect(applied.entity_ids).toHaveLength(proposal.items.length);
      expect(applied.entity_ids).toContain(editedId);
      await expect(button(page, "已提交审核")).toBeDisabled();
      await expect(page.locator("[aria-current=step]")).toContainText("点通过");
      const persisted = await read(page, "/api/entities/" + editedId);
      expect(persisted.status).toBe("review");
      expect(persisted.payload.content || persisted.payload.rule_text).toBe(
        revisedContent,
      );
      record("A6 所有条目处理、确认提交、待审核");

      await selectSavedViewMode(reviewer.page, "simple");
      await reviewer.page.goto("/govern/reviews");
      await selectReview(reviewer.page, revisedTitle);
      expect(
        (await read(reviewer.page, "/api/v2/reviews")).data.items.find(
          (item) => item.entity.id === editedId,
        ).can_decide,
      ).toBe(true);
      await checkStructure(reviewer.page, `reviews-${iteration}`);
      await button(reviewer.page, "退回").click();
      const returnModal = reviewer.page.getByRole("dialog", {
        name: "退回",
        exact: true,
      });
      await expect(button(returnModal, "退回")).toBeDisabled();
      await returnModal
        .getByLabel("退回理由", { exact: true })
        .fill("请再次确认适用条件。");
      const returned = postResponse(
        reviewer.page,
        "/reviews/" + editedId + ":decide",
      );
      await confirmAction(reviewer.page, "退回");
      expect((await (await returned).json()).data.entity.status).toBe(
        "returned",
      );
      await expect(reviewer.page.getByRole("status")).toContainText(
        "请再次确认适用条件。",
      );
      expect((await read(page, "/api/entities/" + editedId)).status).toBe(
        "returned",
      );
      await page.reload();
      await submit(page);
      expect((await read(page, "/api/entities/" + editedId)).status).toBe(
        "review",
      );
      await reviewer.page.reload();
      await selectReview(reviewer.page, revisedTitle);
      record("A8 填理由退回、原方案重提");
      const published = postResponse(
        reviewer.page,
        "/reviews/" + editedId + ":decide",
      );
      await button(reviewer.page, "通过并发布").click();
      await confirmAction(reviewer.page, "通过并发布");
      expect((await (await published).json()).data.entity.status).toBe(
        "published",
      );
      expect((await read(page, "/api/entities/" + editedId)).status).toBe(
        "published",
      );
      record("A9 独立审核并发布");
      await page.goto("/ontology/catalog");
      await page
        .getByLabel(c.r2_catalog_search, { exact: true })
        .fill(revisedTitle);
      await page
        .getByLabel(c.r2_catalog_search, { exact: true })
        .press("Enter");
      await expect(
        page.getByRole("link", { name: revisedTitle, exact: true }),
      ).toBeVisible();
      await checkStructure(page, `catalog-${iteration}`);
      await page.getByRole("link", { name: revisedTitle, exact: true }).click();
      await expect(
        page.getByRole("heading", { name: "关键要点", exact: true }),
      ).toBeVisible();
      await expect(page.locator("blockquote").first()).toContainText("付款");
      await checkStructure(page, `element-${iteration}`);
      for (const name of ["关系", "版本", "动态"]) {
        const fold = page.locator("details").filter({
          has: page.locator("summary strong").filter({ hasText: name }),
        });
        await fold.locator("summary").click();
        await expect(fold).toHaveAttribute("open", "");
        await assertNoLeaks(page, "A13 open fold " + name);
        await fold.locator("summary").click();
      }
      await expect(page.locator("[data-technical-details]")).toHaveCount(0);
      await selectSavedViewMode(page, "pro");
      for (const name of ["结构化信息", "五项检查", "技术详情"])
        await expect(
          page.locator("summary").filter({ hasText: name }),
        ).toBeVisible();
      await assertNoLeaks(page, "A13 professional outside technical", {
        professional: true,
      });
      await page.reload();
      expect(await readSavedViewMode(page)).toBe("pro");
      await openViewModeMenu(page);
      await expect(
        page.getByRole("radio", { name: "专业视图", exact: true }),
      ).toBeChecked();
      await closeViewModeMenu(page);
      await expect(
        page.locator("summary").filter({ hasText: "结构化信息" }),
      ).toBeVisible();
      await selectSavedViewMode(page, "simple");
      await assertNoLeaks(page, "A13 element");
      record("A13 搜索、打开要素、视图差异与刷新保持");

      await page.goto("/applications");
      await button(page, "参考范围").click();
      await page.locator(".agent-auto input").uncheck();
      const assets = page
        .locator(".agent-scope-picker")
        .filter({ has: page.getByRole("heading", { name: "本体成果" }) });
      await assets
        .locator("label")
        .filter({ hasText: revisedTitle })
        .locator("input")
        .check();
      await button(page, "收起参考范围").click();
      await page
        .getByLabel("说说你希望完成的任务", { exact: true })
        .fill(
          "请阅读所选制度，说明付款前要核对什么，以及验收通过后的付款申请时限。用一段说明和一个两列表格回答，保留准确数字并注明原文依据。",
        );
      const turn = page.waitForResponse((r) =>
        /\/api\/agent\/sessions\/[^/]+\/turns$/.test(new URL(r.url()).pathname),
      );
      await button(page, "发送").click();
      expect((await turn).ok()).toBe(true);
      await expect(button(page, "生成报告").last()).toBeVisible({
        timeout: 180_000,
      });
      const citations = page.locator(
        `a[href="/assets/${editedId}"],a[href="/ontology/elements/${editedId}"]`,
      );
      await expect(citations.first()).toBeVisible();
      const [sourcePage] = await Promise.all([
        builder.context.waitForEvent("page"),
        citations.first().click({ modifiers: ["Control"] }),
      ]);
      await expect(
        sourcePage
          .getByRole("heading", { name: revisedTitle, exact: true })
          .first(),
      ).toBeVisible();
      await sourcePage.close();
      await assertNoLeaks(page, "A10 application answer and citations");
      record("A10 提问、回答出处可打开要素");
      await button(page, "生成报告").last().click();
      // R3's automatic correction must navigate with no second user click.
      await expect(page).toHaveURL(/\/apply\/artifacts\//, {
        timeout: 180_000,
      });
      await Promise.all(reportReads);
      expect(reportResponses.length).toBeGreaterThanOrEqual(1);
      expect(reportResponses.length).toBeLessThanOrEqual(2);
      if (!live && process.env.E2E_R3_LIVE !== undefined) {
        expect(firstNumericBlocked).toBe(true);
        expect(revisionUsed).toBe(true);
      }
      const created = reportResponses.at(-1);
      expect(created.status()).toBe(200);
      const artifact = (await created.json()).data;
      expect(
        artifact.spec.blocks.some((block) =>
          JSON.stringify(block).includes("30"),
        ),
      ).toBe(true);
      expect(artifact.source_kind).toBe(live ? "analyst" : "mock");
      if (revisionUsed)
        await expect(
          page.getByText("已按核对结果修订 1 次", { exact: true }),
        ).toBeVisible();
      if (revisionUsed) {
        const compare = page.locator("details").filter({
          has: page.locator("summary").filter({ hasText: "查看修订前后" }),
        });
        await compare.locator("summary").click();
        await expect(compare).toHaveAttribute("open", "");
        await expect(compare).toContainText("修订前");
        await expect(compare).toContainText("修订后");
        if (!live && process.env.E2E_R3_LIVE !== undefined) {
          await expect(compare).toContainText("31日");
          await expect(compare).toContainText("30日");
        }
        await assertNoLeaks(page, "A11 revision comparison open");
        expect(artifact.spec.revision.before.length).toBeGreaterThan(0);
        expect(artifact.spec.revision.after.length).toBeGreaterThan(0);
        await compare.locator("summary").click();
      }
      await checkStructure(page, `report-${iteration}`);
      const superscript = page.locator("sup button,sup a").first();
      await expect(superscript).toBeVisible();
      await superscript.click();
      const evidence = page.getByRole("dialog");
      await expect(evidence).toContainText("30日");
      await expect(evidence.locator("mark")).toBeVisible();
      await assertNoLeaks(page, "A12 report evidence drawer open");
      await evidence.getByRole("button", { name: /关闭|Close/ }).click();
      record("A12 报告引用上标原文抽屉");
      for (const [label, format] of [
        ["下载 Word", "docx"],
        ["下载网页", "html"],
      ]) {
        const downloading = page.waitForEvent("download");
        await button(page, label).first().click();
        const download = await downloading;
        expect(download.suggestedFilename().endsWith("." + format)).toBe(true);
        const parts = [];
        for await (const part of await download.createReadStream())
          parts.push(part);
        const bytes = Buffer.concat(parts);
        if (format === "docx") {
          expect(bytes.subarray(0, 2).toString()).toBe("PK");
          const inspected = spawnSync(
            process.env.E2E_PYTHON || "python",
            [
              "-c",
              "import sys,io,json;from docx import Document;d=Document(io.BytesIO(sys.stdin.buffer.read()));print(json.dumps({'title':bool(d.paragraphs[0].text.strip()),'tables':len(d.tables),'number':any('30' in c.text for t in d.tables for r in t.rows for c in r.cells)}))",
            ],
            { input: bytes, encoding: "utf8" },
          );
          expect(inspected.status).toBe(0);
          const doc = JSON.parse(inspected.stdout);
          expect(doc.title && doc.tables > 0 && doc.number).toBe(true);
        } else {
          expect(bytes.toString()).toContain("Content-Security-Policy");
          const offline = await browser.newContext({ offline: true });
          try {
            const html = await offline.newPage();
            await html.setContent(bytes.toString());
            await expect(html.locator("h1")).not.toBeEmpty();
            await expect(html.locator("table").first()).toContainText("30");
            await expect(
              html.locator("script[src],link[href^=http],img[src^=http]"),
            ).toHaveCount(0);
          } finally {
            await offline.close();
          }
        }
        await download.delete();
      }
      const again = postResponse(page, "/artifacts/" + artifact.id + ":render");
      await page
        .locator("summary")
        .filter({ hasText: c.r2_report_versions })
        .click();
      await button(page, "再次生成").click();
      expect((await (await again).json()).data.current_version).toBe(2);
      await page
        .getByRole("link", { name: "返回报告列表", exact: true })
        .click();
      await expect(
        page.getByRole("heading", { name: "我的报告", exact: true }),
      ).toBeVisible();
      await checkStructure(page, `reports-${iteration}`);
      await page.locator(`a[href="/apply/artifacts/${artifact.id}"]`).click();
      await expect(page).toHaveURL(
        new RegExp("/apply/artifacts/" + artifact.id),
      );
      const reopened = page.waitForEvent("download");
      await button(page, "下载 Word").first().click();
      const reopenedFile = await reopened;
      expect(await reopenedFile.failure()).toBeNull();
      const reopenedParts = [];
      for await (const part of await reopenedFile.createReadStream())
        reopenedParts.push(part);
      const reopenedInspection = spawnSync(
        process.env.E2E_PYTHON || "python",
        [
          "-c",
          "import sys,io;from docx import Document;d=Document(io.BytesIO(sys.stdin.buffer.read()));assert d.paragraphs[0].text.strip();assert any('30' in c.text for t in d.tables for r in t.rows for c in r.cells)",
        ],
        { input: Buffer.concat(reopenedParts) },
      );
      expect(reopenedInspection.status).toBe(0);
      await reopenedFile.delete();
      record("A11 自动修订、Word内容、离线网页、版本再生成");
      success = true;
    } finally {
      await Promise.allSettled(reportReads);
      writeFileSync(
        resolve(output, `mainline-${iteration}.json`),
        JSON.stringify(
          {
            provider: live ? "real_deepseek" : "synthetic_messages_real_sdk",
            channel: "browser_ui",
            iteration,
            success: success && test.info().errors.length === 0,
            steps: times,
            first_numeric_blocked: firstNumericBlocked,
            automatic_revision: revisionUsed,
            report_checks: reportChecks,
            total_seconds: Math.round((Date.now() - started) / 1000),
          },
          null,
          2,
        ) + "\n",
      );
      await Promise.allSettled([
        builder.context.close(),
        reviewer.context.close(),
      ]);
    }
  });
}
