import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { test, expect } from "./runtime.mjs";
import { accounts } from "./fixture.mjs";

const output = process.env.E2E_EVIDENCE_DIR;
const live = process.env.E2E_R2_LIVE === "1";
const documentText =
  "第一条 办理付款前应核对合同约定与验收资料；验收通过后30日内完成付款申请。\n第二条 累计付款不得超过合同金额；有异议的款项应记录原因并暂停支付。\n第三条 经办人员应保存验收记录，审核人员复核付款依据。";

async function login(browser, role) {
  const context = await browser.newContext({
    viewport: { width: 1366, height: 768 },
    acceptDownloads: true,
  });
  const page = await context.newPage();
  await page.goto(process.env.E2E_BASE_URL);
  await page.getByLabel("账号", { exact: true }).fill(accounts[role].username);
  await page.getByLabel("密码", { exact: true }).fill(accounts[role].password);
  await page.getByRole("button", { name: /登录/ }).click();
  await expect(page.locator("main").first()).toBeVisible();
  return { context, page };
}
async function screenshot(page, name) {
  if (!live)
    await page.screenshot({
      path: resolve(output, name + ".png"),
      animations: "disabled",
    });
}
const response = (page, suffix) =>
  page.waitForResponse(
    (r) =>
      new URL(r.url()).pathname.endsWith(suffix) &&
      r.request().method() === "POST",
  );

test("R2: hand in, check, independently publish, ask, export", async ({
  browser,
}) => {
  mkdirSync(output, { recursive: true });
  const times = [],
    start = Date.now();
  let checkpoint = start;
  const record = (step) => {
    times.push({
      step,
      seconds: Math.round((Date.now() - checkpoint) / 100) / 10,
      passed: true,
    });
    checkpoint = Date.now();
  };
  const builder = await login(browser, "modeler"),
    reviewer = await login(browser, "reviewer");
  const page = builder.page;
  try {
    await page.goto(process.env.E2E_BASE_URL + "/");
    await expect(
      page.getByRole("heading", { name: "工作台", exact: true }),
    ).toBeVisible();
    const dropped = await page.evaluateHandle((text) => {
      const data = new DataTransfer();
      data.items.add(
        new File([text], "【演示】付款核对制度.txt", { type: "text/plain" }),
      );
      return data;
    }, documentText);
    await page.dispatchEvent("body", "dragenter", { dataTransfer: dropped });
    await page.dispatchEvent("body", "drop", { dataTransfer: dropped });
    await expect(page.getByRole("dialog")).toBeVisible();
    await screenshot(page, "home-intake");
    const started = response(page, "/recipe.start:invoke");
    await page.getByRole("button", { name: "开始整理", exact: true }).click();
    const recipe = (await (await started).json()).data;
    expect(Boolean(recipe?.id)).toBe(true);
    await expect(page).toHaveURL(/\/build\/intake/);
    await screenshot(page, "intake");
    await expect(
      page.getByRole("link", { name: "核对建设方案", exact: true }),
    ).toBeVisible({ timeout: 120_000 });
    record("交进来");

    const proposalResponse = page.waitForResponse((r) =>
      /\/api\/v2\/changesets\/CS-/.test(new URL(r.url()).pathname),
    );
    await page.getByRole("link", { name: "核对建设方案", exact: true }).click();
    const proposal = (await (await proposalResponse).json()).data;
    expect(proposal.items.length > 0).toBe(true);
    expect(proposal.items.some((item) => item.content_origin !== "local")).toBe(
      true,
    );
    expect(proposal.items.every((item) => item.evidence.length > 0)).toBe(true);
    await page
      .getByRole("button", { name: "查看原文", exact: true })
      .first()
      .click();
    await expect(page.locator("mark")).toBeVisible();
    await screenshot(page, "changeset");
    const editedItem = proposal.items.find((item) =>
      item.evidence.some((ref) => ref.quote.includes("30日")),
    );
    expect(Boolean(editedItem)).toBe(true);
    const rejectedItem = proposal.items.find(
      (item) => item.id !== editedItem.id,
    );
    expect(Boolean(rejectedItem)).toBe(true);
    const rejected = response(page, "/changeset.decide_items:invoke");
    await page
      .locator(`[data-item-id="${rejectedItem.id}"]`)
      .getByRole("button", { name: "不要", exact: true })
      .click();
    const rejection = (await (await rejected).json()).data;
    expect(
      rejection.items.find((item) => item.id === rejectedItem.id).decision,
    ).toBe("rejected");
    const singleAccepted = response(page, "/changeset.decide_items:invoke");
    const singleApplied = response(page, "/changeset.apply:invoke");
    await page
      .locator(`[data-item-id="${rejectedItem.id}"]`)
      .getByRole("button", { name: "采纳", exact: true })
      .click();
    expect(
      (await (await singleAccepted).json()).data.items.find(
        (item) => item.id === rejectedItem.id,
      ).decision,
    ).toBe("accepted");
    const singleAppliedResponse = await singleApplied;
    expect(singleAppliedResponse.ok()).toBe(true);
    const previousEntityIds = (await singleAppliedResponse.json()).data
      .entity_ids;
    const editedCard = page.locator(`[data-item-id="${editedItem.id}"]`);
    await editedCard
      .getByRole("button", { name: "改一改", exact: true })
      .click();
    const modal = page.getByRole("dialog", { name: "改一改", exact: true });
    const revisedTitle = editedItem.after_data.title + "（核对稿）";
    const revisedContent =
      editedItem.evidence.find((ref) => ref.quote.includes("30日")).quote +
      "\n办理前请核对适用条件。";
    await modal.getByLabel("名称", { exact: true }).fill("");
    const invalidEdit = response(page, "/changeset.decide_items:invoke");
    await modal
      .getByRole("button", { name: "保存并采纳", exact: true })
      .click();
    expect((await invalidEdit).status()).toBe(422);
    await expect(modal.getByRole("alert")).toBeVisible();
    await modal.getByLabel("名称", { exact: true }).fill(revisedTitle);
    await modal
      .getByRole("textbox", { name: "正文与适用条件", exact: true })
      .fill(revisedContent);
    const edited = response(page, "/changeset.decide_items:invoke");
    const adopted = response(page, "/changeset.apply:invoke");
    await modal
      .getByRole("button", { name: "保存并采纳", exact: true })
      .click();
    const editResult = (await (await edited).json()).data;
    expect(
      editResult.items.find((item) => item.id === editedItem.id).after_data
        .content === revisedContent,
    ).toBe(true);
    const newEntityIds = (await (await adopted).json()).data.entity_ids.filter(
      (id) => !previousEntityIds.includes(id),
    );
    expect(newEntityIds.length).toBe(1);
    const editedId = newEntityIds[0];
    await expect(modal).not.toBeVisible();
    const sure = editResult.items.filter(
      (item) =>
        item.id !== editedItem.id &&
        item.tier === "sure" &&
        item.decision === "pending" &&
        item.validation.level !== "error",
    );
    expect(sure.length > 0).toBe(true);
    const bulk = response(page, "/changeset.decide_items:invoke");
    const bulkApplied = response(page, "/changeset.apply:invoke");
    await page
      .getByRole("button", { name: "全部采纳高把握", exact: true })
      .click();
    const bulkResult = (await (await bulk).json()).data;
    expect(
      sure.every(
        (item) =>
          bulkResult.items.find((x) => x.id === item.id).decision ===
          "accepted",
      ),
    ).toBe(true);
    expect((await bulkApplied).ok()).toBe(true);
    const submitted = response(page, "/changeset.apply_and_submit:invoke");
    const submitButton = page.getByRole("button", {
      name: "采纳并提交审核",
      exact: true,
    });
    await submitButton.click();
    const applied = (await (await submitted).json()).data;
    expect(applied.entity_ids.length).toBe(sure.length + 2);
    expect(applied.entity_ids.includes(editedId)).toBe(true);
    await expect(submitButton).toBeDisabled();
    const persisted = await page.evaluate(
      async (id) => (await fetch("/api/entities/" + id)).json(),
      editedId,
    );
    expect(persisted.status).toBe("review");
    expect(
      persisted.payload.content === revisedContent ||
        persisted.payload.rule_text === revisedContent,
    ).toBe(true);
    record("看一眼");

    const inboxResponse = reviewer.page.waitForResponse(
      (r) => new URL(r.url()).pathname === "/api/v2/reviews",
    );
    await reviewer.page.goto(process.env.E2E_BASE_URL + "/govern/reviews");
    const inbox = (await (await inboxResponse).json()).data;
    const selected = inbox.items.find((item) => item.entity.id === editedId);
    expect(Boolean(selected?.can_decide)).toBe(true);
    await reviewer.page
      .locator('[aria-label="选择一项待审内容"] button')
      .filter({ hasText: selected.entity.title })
      .click();
    await screenshot(reviewer.page, "reviews");
    await reviewer.page
      .getByLabel("退回理由", { exact: true })
      .fill("请再次确认适用条件。");
    const returned = response(
      reviewer.page,
      "/reviews/" + editedId + ":decide",
    );
    await reviewer.page
      .getByRole("button", { name: "退回", exact: true })
      .click();
    expect((await (await returned).json()).data.entity.status).toBe("returned");
    await page.reload();
    const resubmitted = response(page, "/changeset.apply_and_submit:invoke");
    await page
      .getByRole("button", { name: "采纳并提交审核", exact: true })
      .click();
    expect((await resubmitted).ok()).toBe(true);
    await reviewer.page.reload();
    await reviewer.page
      .locator('[aria-label="选择一项待审内容"] button')
      .filter({ hasText: revisedTitle })
      .click();

    const published = response(
      reviewer.page,
      "/reviews/" + editedId + ":decide",
    );
    await reviewer.page
      .getByRole("button", { name: "通过并发布", exact: true })
      .click();
    expect((await (await published).json()).data.entity.status).toBe(
      "published",
    );
    record("点通过");
    await page.goto(
      process.env.E2E_BASE_URL +
        "/ontology/catalog?kind=rule&q=" +
        encodeURIComponent(selected.entity.title),
    );
    await expect(
      page.getByRole("heading", { name: "本体目录", exact: true }),
    ).toBeVisible();
    const cardLink = page.getByRole("link", {
      name: selected.entity.title,
      exact: true,
    });
    await expect(cardLink).toBeVisible();
    await screenshot(page, "catalog");
    await cardLink.click();
    await expect(
      page.getByRole("heading", { name: "关键要点", exact: true }),
    ).toBeVisible();
    await expect(page.locator("blockquote").first()).toContainText("付款");
    await screenshot(page, "element");
    for (const name of ["关系", "版本", "动态", "结构化信息"]) {
      await page.getByRole("tab", { name, exact: true }).click();
      await expect(page.getByRole("tabpanel")).toBeVisible();
    }

    await page.goto(process.env.E2E_BASE_URL + "/applications");
    await page.getByRole("button", { name: "参考范围", exact: true }).click();
    await page.locator(".agent-auto input").uncheck();
    const assets = page
      .locator(".agent-scope-picker")
      .filter({ has: page.getByRole("heading", { name: "本体成果" }) });
    await assets
      .locator("label")
      .filter({ hasText: selected.entity.title })
      .locator("input")
      .check();
    await page
      .getByRole("button", { name: "收起参考范围", exact: true })
      .click();
    await page
      .getByLabel("说说你希望完成的任务", { exact: true })
      .fill(
        "请阅读所选制度，说明付款前要核对什么，以及验收通过后的付款申请时限。用一段说明和一个两列表格回答，保留准确数字并注明原文依据。",
      );
    const turn = page.waitForResponse((r) =>
      /\/api\/agent\/sessions\/[^/]+\/turns$/.test(new URL(r.url()).pathname),
    );
    await page.getByRole("button", { name: "发送", exact: true }).click();
    expect((await turn).ok()).toBe(true);
    await expect(
      page.getByRole("button", { name: "生成报告", exact: true }).last(),
    ).toBeVisible({ timeout: 120_000 });
    record("问一个问题");

    const created = page.waitForResponse((r) =>
      /\/api\/agent\/sessions\/[^/]+\/runs\/[^/]+\/report$/.test(
        new URL(r.url()).pathname,
      ),
    );
    await page
      .getByRole("button", { name: "生成报告", exact: true })
      .last()
      .click();
    let createdResponse = await created;
    let revisionUsed = false;
    if (!createdResponse.ok()) {
      const detail = await createdResponse.json();
      const code = detail.error?.details?.code || detail.detail?.code;
      expect(["NUMERIC_UNVERIFIED", "CITATION_UNVERIFIED"].includes(code)).toBe(
        true,
      );
      const repaired = page.waitForResponse((r) =>
        /\/api\/agent\/sessions\/[^/]+\/runs\/[^/]+\/report$/.test(
          new URL(r.url()).pathname,
        ),
      );
      await page
        .getByRole("button", {
          name: "请助手按核对结果修订后重新生成",
          exact: true,
        })
        .last()
        .click();
      createdResponse = await repaired;
      revisionUsed = true;
    }
    expect(createdResponse.status()).toBe(200);
    const artifact = (await createdResponse.json()).data;
    expect(
      artifact.spec.blocks.some((block) =>
        JSON.stringify(block).includes("30"),
      ),
    ).toBe(true);
    expect(artifact.source_kind).toBe(live ? "analyst" : "mock");
    await expect(page).toHaveURL(new RegExp("/apply/artifacts/" + artifact.id));
    await expect(
      page.getByRole("heading", { name: "依据清单", exact: true }),
    ).toBeVisible();
    await screenshot(page, "report");
    for (const [label, format] of [
      ["下载 Word", "docx"],
      ["下载网页", "html"],
    ]) {
      const downloading = page.waitForEvent("download");
      await page
        .getByRole("button", { name: label, exact: true })
        .first()
        .click();
      const download = await downloading;
      expect(download.suggestedFilename().endsWith("." + format)).toBe(true);
      const parts = [];
      for await (const part of await download.createReadStream())
        parts.push(part);
      const bytes = Buffer.concat(parts);
      expect(
        format === "docx"
          ? bytes.subarray(0, 2).toString() === "PK"
          : bytes.toString().includes("Content-Security-Policy"),
      ).toBe(true);
      if (format === "docx") {
        const inspected = spawnSync(
          process.env.E2E_PYTHON || "python",
          [
            "-c",
            "import sys,io,json;from docx import Document;d=Document(io.BytesIO(sys.stdin.buffer.read()));print(json.dumps({'title':bool(d.paragraphs[0].text.strip()),'tables':len(d.tables),'number':any('30' in c.text for t in d.tables for r in t.rows for c in r.cells)}))",
          ],
          { input: bytes, encoding: "utf8" },
        );
        expect(inspected.status).toBe(0);
        const document = JSON.parse(inspected.stdout);
        expect(document.title && document.tables > 0 && document.number).toBe(
          true,
        );
      }
      await download.delete();
    }
    const again = response(page, "/artifacts/" + artifact.id + ":render");
    await page.getByRole("button", { name: "再次生成", exact: true }).click();
    expect((await (await again).json()).data.current_version).toBe(2);
    await page.getByRole("link", { name: "返回报告列表", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "我的报告", exact: true }),
    ).toBeVisible();
    await screenshot(page, "reports");
    record("出一份报告");
    writeFileSync(
      resolve(output, "mainline.json"),
      JSON.stringify(
        {
          provider: live ? "real_deepseek" : "synthetic_messages_real_sdk",
          steps: times,
          revision_used: revisionUsed,
          actions: {
            edited: true,
            rejected: true,
            accepted: true,
            bulk_accepted: true,
            returned: true,
            published: true,
            word_opened: true,
          },
          total_seconds: Math.round((Date.now() - start) / 1000),
          screenshots: live ? 0 : 8,
        },
        null,
        2,
      ) + "\n",
    );
  } finally {
    await Promise.allSettled([
      builder.context.close(),
      reviewer.context.close(),
    ]);
  }
});
