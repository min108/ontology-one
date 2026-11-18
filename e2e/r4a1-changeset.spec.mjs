import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { test, expect } from "./runtime.mjs";
import { login, button, c, a, postResponse, read } from "./r3-support.mjs";
import {
  startRegulation,
  confirmOmissions,
  setFileDate,
  readProposal,
  pickItem,
  selected,
  acceptItem,
  submitReady,
} from "./r4a1-changeset-support.mjs";
import {
  assertDocumentDate,
  assertPartialSubmission,
  assertBatchExcluded,
} from "./r4a1-changeset-contracts.mjs";
const output = process.env.E2E_EVIDENCE_DIR;
async function evidence(page, name, data) {
  mkdirSync(output, { recursive: true });
  await page.screenshot({
    path: resolve(output, name + ".png"),
    fullPage: false,
  });
  writeFileSync(resolve(output, name + ".json"), JSON.stringify(data, null, 2));
}
async function editKind(page, item, kind, fields = {}) {
  await pickItem(page, item);
  await button(selected(page), a.catalog_1_1).click();
  const modal = page.getByRole("dialog");
  await modal.getByLabel(c.r2_kind, { exact: true }).click();
  await page
    .locator(".ant-select-item-option")
    .filter({
      hasText: kind === "data" ? "数据" : kind === "skill" ? "技能" : "规则",
    })
    .click();
  for (const [label, value] of Object.entries(fields))
    await modal.getByLabel(label, { exact: true }).fill(value);
  const decided = postResponse(page, "/changeset.decide_items:invoke");
  const applied = postResponse(page, "/changeset.apply:invoke");
  await button(modal, a.r2_save).click();
  expect((await decided).ok()).toBe(true);
  expect((await applied).ok()).toBe(true);
  await expect(modal).not.toBeVisible();
}

test("R4-A.1 AB1: current document facts and explicit omission confirmation preserve source provenance", async ({
  browser,
}) => {
  const { page, context } = await login(browser, "modeler");
  try {
    const { proposal, text } = await startRegulation(page, "regulation-plain");
    assertDocumentDate(proposal, "2026-01-01");
    expect(text).toContain(proposal.document_facts.valid_from.quote);
    expect(proposal.document_facts.valid_from.quote).toContain("2026年1月1日");
    expect(text).toContain(proposal.document_facts.scope.quote);
    expect(proposal.document_facts.scope.value).toBeTruthy();
    const rules = proposal.items.filter(
      (item) => item.after_data.kind === "rule",
    );
    const missing = rules.filter((item) => !item.after_data.exceptions);
    expect(missing.length).toBeGreaterThan(0);
    expect(await page.locator("[data-confirm-no-exceptions]").count()).toBe(1);
    const confirmed = await confirmOmissions(page, proposal.id);
    for (const item of missing) {
      const saved = confirmed.items.find((row) => row.id === item.id);
      expect(saved.after_data.exceptions).toBeTruthy();
      expect(saved.after_data.field_provenance.exceptions.origin).toBe(
        "bulk_confirmation",
      );
    }
    const ready = confirmed.items.find(
      (item) => item.after_data.kind === "rule" && item.gates?.passed,
    );
    expect(ready).toBeTruthy();
    await pickItem(page, ready);
    await expect(
      selected(page).locator("[data-governance-facts]"),
    ).toBeVisible();
    await acceptItem(page, ready);
    const saved = (await readProposal(page, proposal.id)).items.find(
      (item) => item.id === ready.id,
    );
    const entity = (
      await read(page, "/api/v2/elements/" + saved.applied_entity_id)
    ).data;
    expect(entity.meta.valid_from).toBe("2026-01-01");
    expect(entity.gates.passed).toBe(true);
    await evidence(page, "r4a1-ab1-facts", {
      changeset_id: proposal.id,
      rule_id: saved.applied_entity_id,
      missing_count: missing.length,
      passed: true,
    });
  } finally {
    await context.close();
  }
});
for (const [name, forbidden] of [
  ["regulation-repeal", "2019-03-01"],
  ["regulation-history", "2018-05-01"],
]) {
  test(`R4-A.1 ${name === "regulation-repeal" ? "AB2" : "AB3"}: ${name} cannot borrow the old date and accepts one file-level date`, async ({
    browser,
  }) => {
    const { page, context } = await login(browser, "modeler");
    try {
      const { proposal } = await startRegulation(page, name);
      assertDocumentDate(proposal, "");
      expect(proposal.document_facts.valid_from.value).not.toBe(forbidden);
      await expect(
        page.getByLabel(c.r4a1_file_date, { exact: true }),
      ).toHaveValue("");
      expect(proposal.document_facts.scope.value).toBeTruthy();
      await setFileDate(page, "2026-02-01");
      const saved = await readProposal(page, proposal.id);
      assertDocumentDate(saved, "2026-02-01");
      expect(saved.document_facts.valid_from.origin).toBe("manual");
      for (const item of saved.items.filter(
        (row) => row.after_data.kind === "rule",
      ))
        expect(item.after_data.field_provenance.valid_from.origin).toBe(
          "manual",
        );
      await evidence(page, "r4a1-" + name, {
        changeset_id: proposal.id,
        forbidden_date: forbidden,
        date: saved.document_facts.valid_from.value,
        passed: true,
      });
    } finally {
      await context.close();
    }
  });
}
test("R4-A.1 AB4–AB6: bulk excludes incomplete items; adopted types and discard work; eligible drafts submit independently", async ({
  browser,
}) => {
  const { page, context } = await login(browser, "modeler");
  try {
    const { proposal: initial } = await startRegulation(
      page,
      "regulation-plain",
    );
    const incomplete = initial.items.filter(
      (item) => !item.ready_for_bulk_accept,
    );
    expect(incomplete.length).toBeGreaterThan(0);
    for (const item of incomplete) expect(item.tier).not.toBe("sure");
    const bulk = button(page, a.catalog_1_4);
    if (await bulk.count()) {
      const decided = postResponse(page, "/changeset.decide_items:invoke"),
        applied = postResponse(page, "/changeset.apply:invoke");
      await bulk.click();
      expect((await decided).request().postDataJSON().only_ready).toBe(
        true,
      );
      expect((await applied).ok()).toBe(true);
    }
    assertBatchExcluded(initial, await readProposal(page, initial.id));
    let proposal = await confirmOmissions(page, initial.id);
    const candidates = proposal.items.filter(
      (item) =>
        item.after_data.kind === "rule" &&
        item.gates?.passed &&
        !item.applied_entity_id,
    );
    expect(candidates.length).toBeGreaterThanOrEqual(3);
    const [blocked, discarded, ready] = candidates;
    await acceptItem(page, blocked);
    await editKind(page, blocked, "data", {
      [c.r2_card_field_unit]: "",
      [c.r4_rule_grain]: "",
    });
    await acceptItem(page, discarded);
    await editKind(page, discarded, "skill");
    proposal = await readProposal(page, initial.id);
    const skill = proposal.items.find((item) => item.id === discarded.id);
    expect(skill.after_data.kind).toBe("skill");
    expect(skill.handoff?.href).toBeTruthy();
    await expect(
      page.getByRole("link", { name: skill.handoff.label, exact: true }),
    ).toHaveAttribute("href", skill.handoff.href);
    const rejecting = postResponse(page, "/changeset.decide_items:invoke");
    await button(selected(page), a.catalog_1_2).click();
    expect((await rejecting).ok()).toBe(true);
    const archived = (
      await read(page, "/api/v2/elements/" + skill.applied_entity_id)
    ).data;
    expect(archived.status).toBe("archived");
    await acceptItem(page, ready);
    proposal = await readProposal(page, initial.id);
    const blockedSaved = proposal.items.find((item) => item.id === blocked.id);
    expect(blockedSaved.can_submit).toBe(false);
    expect(
      blockedSaved.gates.checks.find((check) => check.code === "QG2").status,
    ).toBe("fail");
    const eligible = proposal.items.filter(
      (item) =>
        ["accepted", "edited"].includes(item.decision) && item.can_submit,
    );
    const remaining = proposal.items.filter(
      (item) =>
        ["accepted", "edited"].includes(item.decision) && !item.can_submit,
    );
    expect(eligible.length).toBeGreaterThan(0);
    expect(remaining.map((item) => item.id)).toContain(blocked.id);
    const submitted = await submitReady(page);
    assertPartialSubmission(
      submitted,
      eligible.map((item) => item.applied_entity_id),
      remaining.map((item) => item.id),
    );
    const after = await readProposal(page, initial.id);
    for (const item of eligible)
      expect(after.items.find((row) => row.id === item.id).applied_status).toBe(
        "review",
      );
    expect(
      after.items.find((item) => item.id === blocked.id).applied_status,
    ).toBe("draft");
    await expect(page.getByRole("status")).toContainText(
      c.r4a1_partial_submitted
        .replace("{submitted}", String(eligible.length))
        .replace("{remaining}", String(remaining.length)),
    );
    await evidence(page, "r4a1-ab4-ab6-partial", {
      changeset_id: initial.id,
      submitted,
      discarded_id: discarded.id,
      passed: true,
    });
  } finally {
    await context.close();
  }
});
