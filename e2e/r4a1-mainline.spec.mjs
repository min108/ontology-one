import { writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { test, expect } from "./runtime.mjs";
import {
  a,
  button,
  c,
  confirmAction,
  login,
  postResponse,
  read,
} from "./r3-support.mjs";
import { assertUnwaivedChecks } from "./r4-assertion-contracts.mjs";
import {
  acceptItem,
  confirmOmissions,
  readProposal,
  setFileDate,
  startRegulation,
  submitReady,
} from "./r4a1-changeset-support.mjs";

const live = process.env.E2E_R3_LIVE === "1";
const material = process.env.E2E_R4A1_MATERIAL || "regulation-plain";
const deadline =
  material === "regulation-history" ? "15个工作日" : "10个工作日";
const compact = (text) => text.replace(/\s+/gu, "");

async function checkExports(page, browser, citation, quote) {
  const checks = {};
  for (const [label, extension] of [
    ["下载 Word", "docx"],
    ["下载网页", "html"],
  ]) {
    const waiting = page.waitForEvent("download");
    await button(page, label).first().click();
    const download = await waiting;
    expect(await download.failure()).toBeNull();
    expect(download.suggestedFilename().endsWith("." + extension)).toBe(true);
    const chunks = [];
    for await (const chunk of await download.createReadStream())
      chunks.push(chunk);
    const bytes = Buffer.concat(chunks);
    if (extension === "docx") {
      const inspection = spawnSync(
        process.env.E2E_PYTHON || "python",
        [
          "e2e/inspect_report_export.py",
          deadline,
          citation,
          createHash("sha256").update(compact(quote)).digest("hex"),
        ],
        { input: bytes },
      );
      expect(inspection.status, "the actual Word file can be parsed").toBe(0);
      checks[extension] = JSON.parse(inspection.stdout.toString());
    } else {
      const offline = await browser.newContext({ offline: true });
      try {
        const html = await offline.newPage();
        await html.setContent(bytes.toString());
        const body = await html
          .locator("body > p, body > table")
          .allTextContents();
        const linked = await html
          .locator("body > p > a")
          .evaluateAll(
            (nodes, key) =>
              nodes.some((node) => node.getAttribute("href") === "#" + key),
            citation,
          );
        const source = await html.evaluate(
          (key) =>
            document.getElementById(key)?.querySelector("blockquote")
              ?.textContent || "",
          citation,
        );
        checks[extension] = {
          body_deadline: body.some((line) => compact(line).includes(deadline)),
          citation_link: linked,
          source_quote: compact(source) === compact(quote),
        };
        await expect(
          html.locator("script[src],link[href^=http],img[src^=http]"),
        ).toHaveCount(0);
      } finally {
        await offline.close();
      }
    }
    expect(checks[extension]).toEqual({
      body_deadline: true,
      citation_link: true,
      source_quote: true,
    });
    await download.delete();
  }
  return checks;
}

test("R4-A.1 AB15: ordinary policy through independent publication and verified report exports", async ({
  browser,
}) => {
  const started = Date.now();
  const receipt = {
    provider: live ? "real_deepseek" : "synthetic_messages_real_sdk",
    channel: "browser_ui",
    material: material + ".txt",
    success: false,
    steps: [],
    extraction_summary: {},
    gate_repairs: [],
    publication_checks: [],
    automatic_revision: false,
    first_numeric_blocked: false,
  };
  let stage,
    last = started;
  const record = () => {
    receipt.steps.push({
      action: stage,
      seconds: (Date.now() - last) / 1000,
      passed: true,
    });
    last = Date.now();
  };
  const builder = await login(browser, "modeler"),
    reviewer = await login(browser, "reviewer");
  const page = builder.page,
    reports = [];
  page.on("request", (request) => {
    if (
      /\/sessions\/[^/]+\/turns$/.test(new URL(request.url()).pathname) &&
      request.method() === "POST" &&
      request.postDataJSON()?.revision_of
    )
      receipt.automatic_revision = true;
  });
  page.on("response", (response) => {
    if (
      /\/runs\/[^/]+\/report$/.test(new URL(response.url()).pathname) &&
      response.request().method() === "POST"
    )
      reports.push(response);
  });
  try {
    stage = "上传原制度并完成提炼";
    const { proposal, text } = await startRegulation(page, material);
    receipt.extraction_summary = proposal.extraction_summary;
    expect(proposal.extraction_summary.fully_local).toBe(false);
    expect(proposal.extraction_summary.accepted_candidates).toBeGreaterThan(0);
    const rule = proposal.items.find(
      (item) =>
        item.content_origin === "source" &&
        item.after_data.kind === "rule" &&
        item.evidence.some(
          ({ quote }) =>
            text.includes(quote) && compact(quote).includes(deadline),
        ) &&
        compact(item.after_data.definition || "").includes(deadline),
    );
    expect(
      rule,
      "the extracted rule retains its own deadline and verbatim evidence",
    ).toBeTruthy();
    record();

    stage = "核对文件事实并提交可送审条目";
    const date = material === "regulation-plain" ? "2026-01-01" : "2026-11-18";
    if (material === "regulation-plain") {
      expect(proposal.document_facts.valid_from.value).toBe(date);
      expect(text).toContain(proposal.document_facts.valid_from.quote);
      expect(proposal.document_facts.valid_from.quote).toContain(
        "本办法自2026年1月1日起施行",
      );
    } else {
      expect(proposal.document_facts.valid_from.value).toBe("");
      for (const item of proposal.items.filter(
        (row) => row.after_data.kind === "rule",
      ))
        expect(item.after_data.valid_from || "").toBe("");
      await setFileDate(page, date);
      receipt.gate_repairs.push({
        action: "核对人填写一次文件日期",
        field: "valid_from",
        value: date,
        basis: "演练给定的发布或印发日",
      });
    }
    const confirmed = await confirmOmissions(page, proposal.id);
    receipt.document_date = confirmed.document_facts.valid_from;
    for (const field of ["exceptions", "conditions"]) {
      const items = confirmed.items.filter(
        (row) =>
          row.after_data.field_provenance?.[field]?.origin ===
            "bulk_confirmation" &&
          !proposal.items.find((prior) => prior.id === row.id)?.after_data[
            field
          ],
      );
      if (items.length)
        receipt.gate_repairs.push({
          action: "核对人批量确认",
          field,
          count: items.length,
          items: items.map((item) => item.id),
        });
    }
    const bulk = button(page, "全部采纳高把握");
    if (await bulk.isEnabled()) {
      const decided = postResponse(page, "/changeset.decide_items:invoke");
      const applied = postResponse(page, "/changeset.apply:invoke");
      await bulk.click();
      expect((await decided).ok()).toBe(true);
      expect((await applied).ok()).toBe(true);
    }
    const checked = (await readProposal(page, proposal.id)).items.find(
      (item) => item.id === rule.id,
    );
    if (!checked.applied_entity_id) await acceptItem(page, checked);
    const submitted = await submitReady(page);
    receipt.submitted_count = submitted.submitted_count;
    receipt.remaining_count = submitted.remaining_count;
    receipt.pending_count = submitted.pending_count;
    const queued = (await readProposal(page, proposal.id)).items.find(
      (item) => item.id === rule.id,
    );
    expect(queued.applied_status).toBe("review");
    const id = queued.applied_entity_id;
    expect(submitted.submitted_entity_ids).toContain(id);
    record();

    stage = "独立审核并发布";
    await reviewer.page.goto("/govern/reviews");
    await reviewer.page.locator('[data-review-item-id="' + id + '"]').click();
    await button(reviewer.page, a.catalog_2_5).click();
    const modal = reviewer.page.getByRole("dialog", { name: a.catalog_2_5 });
    await expect(
      modal.locator('[data-governance-field="valid_from"]'),
    ).toContainText(date);
    const deciding = postResponse(reviewer.page, "/" + id + ":decide");
    await confirmAction(reviewer.page, a.catalog_2_5);
    expect((await deciding).ok()).toBe(true);
    const detail = (await read(page, "/api/v2/elements/" + id)).data;
    expect(detail.entity.status).toBe("published");
    expect(
      detail.gates.fields.find((field) => field.key === "valid_from")?.value,
    ).toBe(date);
    receipt.publication_checks = detail.gates.checks.map(
      ({ code, status, reason }) => ({ code, status, reason }),
    );
    assertUnwaivedChecks(detail.gates);
    record();

    stage = "就已发布的条款提问";
    await page.goto("/ontology/elements/" + id);
    await button(page, c.r3_main_ask).click();
    await expect(page.locator(".agent-scope-picker input:checked")).toHaveCount(
      1,
    );
    await button(page, "收起参考范围").click();
    await page
      .getByLabel("说说你希望完成的任务", { exact: true })
      .fill(
        "请根据已选制度，用一段话说明本条规定的办理时限、需要做什么，并列出原文依据。保留时限数字和单位。",
      );
    const turning = postResponse(page, "/turns");
    await button(page, "发送").click();
    const turn = await turning;
    expect(turn.ok()).toBe(true);
    expect(turn.request().postDataJSON()).toMatchObject({
      scope_mode: "selected",
      entity_ids: [id],
    });
    await expect(button(page, "生成报告").last()).toBeVisible({
      timeout: 180_000,
    });
    record();

    stage = "生成报告、定位引文并导出 Word 和网页";
    await button(page, "生成报告").last().click();
    await expect
      .poll(() => reports.at(-1)?.status() === 200 || reports.length >= 2, {
        timeout: 180_000,
      })
      .toBe(true);
    if (reports[0].status() === 422) {
      const first = await reports[0].json();
      receipt.first_numeric_blocked =
        (first.error?.details || first.detail)?.code === "NUMERIC_UNVERIFIED";
    }
    expect(reports.at(-1).status()).toBe(200);
    await expect(page).toHaveURL(/\/apply\/artifacts\//);
    const artifact = (await reports.at(-1).json()).data;
    expect(artifact.source_kind).toBe(live ? "analyst" : "mock");
    expect(compact(JSON.stringify(artifact.spec.blocks))).toContain(deadline);
    await page.locator("summary").filter({ hasText: c.r3_report_body }).click();
    const citations = Object.values(artifact.spec.citations);
    const index = citations.findIndex(
      ({ quote }) =>
        quote && text.includes(quote) && compact(quote).includes(deadline),
    );
    expect(index).toBeGreaterThanOrEqual(0);
    const ref = page
      .locator("sup button:visible,sup a:visible")
      .filter({ hasText: new RegExp("^" + (index + 1) + "$") })
      .first();
    await ref.click();
    const evidence = page.getByRole("dialog");
    await expect(evidence.locator("mark")).toHaveText(citations[index].quote);
    await evidence.getByRole("button", { name: /关闭|Close/ }).click();
    receipt.report_checks = await checkExports(
      page,
      browser,
      Object.keys(artifact.spec.citations)[index],
      citations[index].quote,
    );
    record();
    receipt.success = true;
  } catch (error) {
    receipt.failure = {
      stage,
      type: error?.name || "Error",
      line: String(error?.stack || "").match(
        /r4a1-mainline\.spec\.mjs:(\d+)/,
      )?.[1],
    };
    throw error;
  } finally {
    receipt.total_seconds = (Date.now() - started) / 1000;
    writeFileSync(
      resolve(process.env.E2E_EVIDENCE_DIR, "a1-mainline-1.json"),
      JSON.stringify(receipt, null, 2) + "\n",
    );
    await Promise.allSettled([
      builder.context.close(),
      reviewer.context.close(),
    ]);
  }
});
