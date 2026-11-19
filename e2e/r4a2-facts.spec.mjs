import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { test, expect } from "./runtime.mjs";
import { a, button, c, login, postResponse, read } from "./r3-support.mjs";
import { checkStructure } from "./r3-structure.mjs";
import { selectSavedViewMode } from "./select-saved-view-mode.mjs";
import {
  acceptItem,
  confirmOmissions,
  openOmissionList,
  pickItem,
  readProposal,
  selected,
  startRegulation,
  startRegulationText,
  submitReady,
} from "./r4a1-changeset-support.mjs";

const cases = [
  {
    name: "历史实施句",
    dates: ["2018-05-01", "2026-01-01"],
    text: "第一条 本制度于2018年5月1日实施，至今已修订三次。\n第五条 本制度自2026年1月1日起施行。",
  },
  {
    name: "同句记载修订史",
    dates: ["2015-07-01", "2026-01-01"],
    text: "第一条 本办法自2015年7月1日起施行，2020年第一次修订。\n第五条 本次修订版自2026年1月1日起施行。",
  },
  {
    name: "同一句含两次施行",
    dates: ["2018-05-01", "2026-01-01"],
    text: "第五条 本制度于2018年5月1日实施，至今已修订三次，自2026年1月1日起施行。",
  },
  {
    name: "附件引述旧办法",
    dates: ["2015-07-01"],
    text: "附件一 原办法原文摘录\n第二十条 本办法自2015年7月1日起施行。",
  },
];
const sharedRules =
  "内部记录管理办法\n第二条 本办法适用于本部及各分公司的财务资料。\n第三条 经办人员应按事项分别登记支出明细。\n第四条 会计人员应保留完整的原始凭证。\n";
async function screenshot(page, name) {
  const dir = process.env.E2E_EVIDENCE_DIR;
  mkdirSync(dir, { recursive: true });
  await page.setViewportSize({ width: 1366, height: 768 });
  await page.screenshot({ path: resolve(dir, name + ".png") });
}
for (const sample of cases) {
  test(`R4-A.2 AC5: ${sample.name} stays unselected until a builder chooses the exact effective sentence`, async ({
    browser,
  }) => {
    const { page, context } = await login(browser, "modeler");
    try {
      const text = sharedRules + sample.text;
      const { proposal } = await startRegulationText(page, sample.name, text);
      const facts = page.locator("[data-document-facts]");
      const input = page.getByLabel(c.r4a1_file_date, { exact: true });
      const initial = proposal.document_facts;
      expect(initial.valid_from.value).toBe("");
      const candidates = initial.valid_from_candidates;
      expect(candidates.map((item) => item.value)).toEqual(sample.dates);
      const rules = proposal.items.filter(
        (item) => item.after_data.kind === "rule",
      );
      expect(rules.length).toBeGreaterThanOrEqual(2);
      for (const item of rules)
        expect(item.after_data.valid_from || "").toBe("");
      await expect(input).toHaveValue("");
      await expect(facts.getByRole("radio")).toHaveCount(candidates.length);
      for (const fact of candidates) {
        expect(Array.from(text).slice(fact.start, fact.end).join("")).toBe(
          fact.quote,
        );
        expect(fact.material_id).toBe(proposal.source_material_id);
        expect(fact.source_version && fact.anchor_id).toBeTruthy();
        const radio = facts.getByRole("radio", {
          name: fact.quote,
          exact: true,
        });
        await expect(radio).not.toBeChecked();
        await expect(radio).toBeVisible();
      }
      if (sample === cases[0]) {
        await screenshot(page, "r4a2-changeset-date-candidates");
        await checkStructure(page, "AC5 effective-date choices");
      }
      const chosen = candidates.at(-1);
      await facts
        .getByRole("radio", { name: chosen.quote, exact: true })
        .check();
      await expect(input).toHaveValue(chosen.value);
      const saved = postResponse(page, "/changeset.confirm_facts:invoke");
      await button(facts, c.r4a1_file_date_apply).click();
      const response = await saved;
      expect(response.status(), await response.text()).toBe(200);
      await page.reload();
      await expect(input).toHaveValue(chosen.value);
      const confirmed = await readProposal(page, proposal.id);
      const source = confirmed.document_facts.valid_from;
      expect(source).toMatchObject({
        value: chosen.value,
        origin: "manual",
        method: "selection",
        quote: chosen.quote,
        anchor_id: chosen.anchor_id,
        source_version: chosen.source_version,
      });
      expect(source.actor_id && source.confirmed_by).toBeTruthy();
      await expect(facts.locator("[data-document-date-origin]")).toHaveText(
        `${c.r4a2_origin_selection} · ${source.confirmed_by}`,
      );
      await expect(
        facts.getByRole("radio", { name: chosen.quote, exact: true }),
      ).toBeChecked();
      await expect(
        facts.getByRole("link", { name: chosen.quote, exact: true }),
      ).toHaveAttribute(
        "href",
        `/materials/${chosen.material_id}?anchor=${chosen.anchor_id}&source_version=${chosen.source_version}`,
      );
      for (const previous of rules) {
        const rule = confirmed.items.find((item) => item.id === previous.id);
        expect(rule.after_data.valid_from).toBe(chosen.value);
        expect(rule.after_data.field_provenance.valid_from).toMatchObject({
          origin: "manual",
          method: "selection",
          actor_id: source.actor_id,
          quote: chosen.quote,
          anchor_id: chosen.anchor_id,
        });
        expect(
          rule.gates.fields.find((field) => field.key === "valid_from"),
        ).toMatchObject({
          source: "editor",
          method: "selection",
          confirmed_by: source.confirmed_by,
          source_quote: chosen.quote,
        });
      }
    } finally {
      await context.close();
    }
  });
}
test("R4-A.2 AC6: misleading words stay out of exceptions and both workspaces identify system-derived facts", async ({
  browser,
}) => {
  const owner = await login(browser, "modeler"),
    reviewer = await login(browser, "reviewer");
  const page = owner.page;
  try {
    const negatives = [
      "应删除外部共享文件",
      "移除过期账号并对外部系统同步",
      "非但要核对金额",
    ];
    const positive = "如遇系统故障，经办人应报告；紧急救助事项除外。";
    const text = [
      "共享文件管理办法",
      "第一条 本办法适用于本部及各分公司的共享文件。",
      "第二条 交接完成后应删除外部共享文件。",
      "第三条 管理员应移除过期账号并对外部系统同步。",
      "第四条 经办人员非但要核对金额，还应核对收款账户。",
      "第五条 " + positive,
      "第六条 本办法自2026年1月1日起施行。",
    ].join("\n");
    const { proposal } = await startRegulationText(
      page,
      "共享文件管理办法",
      text,
    );
    const byQuote = (snippet) =>
      proposal.items.find((item) =>
        item.evidence.some((source) => source.quote.includes(snippet)),
      );
    for (const clue of negatives) {
      const item = byQuote(clue);
      expect(item).toBeTruthy();
      expect(item.after_data.exceptions).toBe("");
      expect(
        item.gates.fields.find((field) => field.key === "exceptions").source,
      ).toBe("missing");
    }
    const rule = byQuote(positive);
    expect(rule.after_data.conditions.length).toBeGreaterThan(0);
    expect(rule.after_data.exceptions).toContain("紧急救助事项除外");
    const ownQuote = rule.evidence[0].quote;
    const inspected = ["conditions", "exceptions"];
    for (const key of inspected) {
      const provenance = rule.after_data.field_provenance[key];
      expect(provenance.origin).toBe("system");
      expect(provenance.quote).toBeTruthy();
      expect(ownQuote).toContain(provenance.quote);
    }
    await pickItem(page, rule);
    await selected(page)
      .locator("summary")
      .filter({ hasText: c.r4a1_legacy_facts })
      .click();
    for (const key of inspected) {
      const field = selected(page).locator(`[data-governance-field="${key}"]`);
      await expect(field.locator('[data-fact-origin="system"]')).toHaveText(
        c.r4a2_origin_system,
      );
      await expect(field.locator("blockquote")).toHaveText(
        rule.after_data.field_provenance[key].quote,
      );
    }
    await confirmOmissions(page, proposal.id);
    await acceptItem(page, rule);
    const receipt = await submitReady(page);
    expect(receipt.submitted_count).toBe(1);
    const [id] = receipt.submitted_entity_ids;
    await reviewer.page.goto("/govern/reviews");
    await reviewer.page.locator(`[data-review-item-id="${id}"]`).click();
    const reviewRow = reviewer.page.locator(`[data-review-id="${id}"]`);
    for (const key of inspected) {
      const field = reviewRow.locator(`[data-governance-field="${key}"]`);
      await expect(field.locator('[data-fact-origin="system"]')).toBeVisible();
      await expect(field.locator("[data-fact-origin]")).toHaveText(
        c.r4a2_origin_system,
      );
      await expect(field.locator("blockquote")).toHaveText(
        rule.after_data.field_provenance[key].quote,
      );
    }
    const persisted = (await read(reviewer.page, "/api/v2/elements/" + id))
      .data;
    expect(persisted.entity.status).toBe("review");
    for (const key of inspected)
      expect(
        persisted.gates.fields.find((field) => field.key === key),
      ).toMatchObject({
        source: "system",
        source_quote: rule.after_data.field_provenance[key].quote,
      });
    await checkStructure(reviewer.page, "AC6 review fact origins");
    await reviewRow
      .locator('[data-governance-field="conditions"]')
      .scrollIntoViewIfNeeded();
    await screenshot(reviewer.page, "r4a2-review-fact-origins");
  } finally {
    await owner.context.close();
    await reviewer.context.close();
  }
});
test("R4-A.2 AC9: each omission quotes its own source and requires a deliberate selection", async ({
  browser,
}) => {
  const { page, context } = await login(browser, "modeler");
  try {
    const { proposal } = await startRegulation(page, "regulation-plain");
    await checkStructure(page, "AC9 changeset with omissions to verify");
    const group = await openOmissionList(page, "exceptions");
    await expect(group.locator("summary")).toContainText(/请核对.*例外/);
    await expect(group.locator("summary")).not.toContainText("原文未写");
    const rows = await group.locator("[data-omission-item]").all();
    expect(rows.length).toBeGreaterThan(1);
    for (const row of rows) {
      await expect(row.getByRole("checkbox")).not.toBeChecked();
      const id = await row.getAttribute("data-omission-item");
      const item = proposal.items.find((item) => item.id === id);
      for (const source of item.evidence)
        await expect(
          row.locator("blockquote").filter({ hasText: source.quote }),
        ).toBeVisible();
    }
    const submit = group.locator("[data-confirm-no-exceptions]");
    await expect(submit).toBeDisabled();
    await screenshot(page, "r4a2-changeset-confirm-omissions");
    const chosen = await rows[1].getAttribute("data-omission-item");
    await rows[1].getByRole("checkbox").check();
    const saving = postResponse(page, "/changeset.confirm_facts:invoke");
    await submit.click();
    const response = await saving;
    expect(response.status(), await response.text()).toBe(200);
    const after = await readProposal(page, proposal.id);
    for (const initial of proposal.items) {
      const saved = after.items.find((item) => item.id === initial.id);
      if (saved.id === chosen) {
        expect(saved.after_data.exceptions).toBe("无例外");
        expect(saved.after_data.field_provenance.exceptions.origin).toBe(
          "bulk_confirmation",
        );
        expect(
          saved.after_data.field_provenance.exceptions.confirmed_by,
        ).toBeTruthy();
      } else
        expect(saved.after_data.exceptions).toBe(initial.after_data.exceptions);
    }
  } finally {
    await context.close();
  }
});
test("R4-A.2 AC10: cancelling keeps a draft and confirming archives it without offering more edits", async ({
  browser,
}) => {
  const { page, context } = await login(browser, "modeler");
  try {
    const { proposal } = await startRegulation(page, "regulation-plain");
    const rule = proposal.items.find(
      (item) =>
        item.after_data.kind === "rule" && item.validation.level === "ok",
    );
    await acceptItem(page, rule);
    const adopted = (await readProposal(page, proposal.id)).items.find(
      (item) => item.id === rule.id,
    );
    const entity = async () =>
      (await read(page, "/api/v2/elements/" + adopted.applied_entity_id)).data
        .entity;
    const original = await entity();
    expect(original.status).toBe("draft");
    const discard = button(selected(page), a.catalog_1_2);
    await discard.click();
    const dialog = page.getByRole("dialog", { name: c.r4a2_discard_title });
    await expect(dialog).toContainText(rule.after_data.title);
    await expect(dialog).toContainText("将放弃已形成的草稿");
    await button(dialog, a.r2_cancel).click();
    await expect(dialog).not.toBeVisible();
    expect(await entity()).toMatchObject({
      status: "draft",
      version: original.version,
    });
    await discard.click();
    const saving = postResponse(page, "/changeset.decide_items:invoke");
    await button(dialog, c.r4a2_discard_confirm).click();
    const response = await saving;
    expect(response.status(), await response.text()).toBe(200);
    expect(await entity()).toMatchObject({
      status: "archived",
      id: original.id,
    });
    await page.reload();
    await pickItem(page, adopted);
    await expect(button(selected(page), a.catalog_1_1)).toHaveCount(0);
    await expect(button(selected(page), a.catalog_1_2)).toHaveCount(0);
    await expect(button(selected(page), a.catalog_1_0)).toHaveCount(0);
    await expect(
      selected(page).getByRole("link", { name: c.r4a2_view_archived }),
    ).toHaveAttribute("href", new RegExp(encodeURIComponent(original.id)));
    const item = (await readProposal(page, proposal.id)).items.find(
      (item) => item.id === rule.id,
    );
    expect(item).toMatchObject({
      decision: "rejected",
      applied_status: "archived",
      can_submit: false,
    });
  } finally {
    await context.close();
  }
});

test("R4-A.2 experience 11: changing a judgement in the changeset regenerates the saved Chinese sentence", async ({
  browser,
}) => {
  const owner = await login(browser, "modeler");
  const page = owner.page;
  try {
    const { proposal } = await startRegulation(page, "regulation-plain");
    const rule = proposal.items.find(
      (item) =>
        item.after_data.kind === "rule" && item.validation.level === "ok",
    );
    await pickItem(page, rule);
    await selectSavedViewMode(page, "professional");
    async function saveJudgement(oxl) {
      await button(selected(page), a.catalog_1_1).click();
      const dialog = page.getByRole("dialog", { name: a.catalog_1_1 });
      const fold = dialog.locator("[data-technical-details] details");
      if (!(await fold.evaluate((node) => node.open)))
        await fold.locator("summary").click();
      const input = dialog.getByLabel(c.r4_rule_judgement, { exact: true });
      const current = JSON.parse(
        (await input.inputValue()) ||
          '{"condition":{"oxl":"true"},"exceptions":[]}',
      );
      current.requirement = { ...current.requirement, oxl };
      await input.fill(JSON.stringify(current));
      const applied = postResponse(page, "/changeset.apply:invoke");
      await button(dialog, a.r2_save).click();
      const response = await applied;
      expect(response.status(), await response.text()).toBe(200);
      await expect(dialog).not.toBeVisible();
      return (await readProposal(page, proposal.id)).items.find(
        (item) => item.id === rule.id,
      );
    }
    const first = await saveJudgement("2 > 1");
    expect(first.after_data.judgement.requirement.sentence).toContain("大于");
    const changed = await saveJudgement("2 < 1");
    expect(changed.after_data.judgement.requirement.sentence).toContain("小于");
    expect(changed.after_data.judgement.requirement.sentence).not.toBe(
      first.after_data.judgement.requirement.sentence,
    );
    const persisted = (
      await read(page, "/api/v2/elements/" + first.applied_entity_id)
    ).data.entity;
    expect(persisted.payload.judgement.requirement).toMatchObject({
      oxl: "2 < 1",
      sentence: changed.after_data.judgement.requirement.sentence,
    });
  } finally {
    await owner.context.close();
  }
});
