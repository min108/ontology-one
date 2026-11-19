import { test, expect } from "./runtime.mjs";
import {
  login,
  button,
  c,
  a,
  read,
  postResponse,
  confirmAction,
} from "./r3-support.mjs";
import {
  startRegulation,
  acceptItem,
  readProposal,
  pickItem,
  selected,
  submitReady,
  confirmOmissions,
} from "./r4a1-changeset-support.mjs";

async function savedItem(page, proposal, item) {
  return (await readProposal(page, proposal.id)).items.find(
    (row) => row.id === item.id,
  );
}

test("R4-A.2 AC3: packaged skills reject editing, reclassification and discard in the changeset", async ({
  browser,
}) => {
  const owner = await login(browser, "modeler");
  const { page } = owner;
  try {
    const { proposal } = await startRegulation(page, "regulation-plain");
    const candidate = proposal.items.find(
      (item) => item.after_data.kind === "skill",
    );
    expect(candidate).toBeTruthy();
    await acceptItem(page, candidate);
    const adopted = await savedItem(page, proposal, candidate);
    const id = adopted.applied_entity_id;
    await page
      .getByRole("link", { name: adopted.handoff.label, exact: true })
      .click();
    const conversion = page.getByRole("dialog", { name: "继续建设已有技能" });
    await conversion
      .getByLabel("技能建设需求", { exact: true })
      .fill("整理合同付款核对步骤和办理材料。");
    const creating = postResponse(page, "/skills");
    await button(conversion, "开始建设技能").click();
    expect((await creating).status()).toBe(200);
    await expect(page).toHaveURL(
      (url) => url.pathname === "/skill-development/" + id,
    );
    const packageBefore = await read(page, "/api/skills/" + id);
    const versions = await read(page, `/api/entities/${id}/versions`);
    expect(packageBefore.entity.payload.managed_skill).toBe(true);
    expect(packageBefore.package_revision).toBeGreaterThan(0);
    await page.goto("/build/changesets/" + proposal.id);
    const before = await savedItem(page, proposal, candidate);
    expect(before.handoff.href).toBe("/skill-development/" + id);
    await pickItem(page, candidate);
    for (const operation of ["edit", "reclassify", "discard"]) {
      let dialog;
      if (operation === "discard") {
        await button(selected(page), a.catalog_1_2).click();
        dialog = page.getByRole("dialog", { name: "放弃已形成的草稿？" });
        await expect(dialog).toBeVisible();
      } else {
        await button(selected(page), a.catalog_1_1).click();
        dialog = page.getByRole("dialog", { name: a.catalog_1_1 });
        if (operation === "edit") {
          await dialog
            .locator("label")
            .filter({ hasText: c.r2_content })
            .locator("textarea")
            .fill("尝试从核对页变更已建包的技能正文。");
        } else {
          await dialog.getByLabel(c.r2_kind, { exact: true }).click();
          await page
            .locator(".ant-select-item-option-content")
            .getByText("规则", { exact: true })
            .click();
          await dialog
            .getByLabel(c.r4a1_legacy_subject, { exact: true })
            .click();
          await page
            .locator(".ant-select-item-option-content")
            .getByText(proposal.applicability_options[0].title, { exact: true })
            .first()
            .click();
        }
      }
      const deciding = postResponse(page, "/changeset.decide_items:invoke");
      await button(
        dialog,
        operation === "discard" ? "确认放弃" : a.r2_save,
      ).click();
      const response = await deciding;
      expect(response.status()).toBe(409);
      const { error } = await response.json();
      expect(error.message).toContain("技能工作台");
      expect(error.next_actions).toEqual([
        { label: "前往技能工作台", href: before.handoff.href },
      ]);
      await expect(dialog.getByRole("alert")).toContainText("技能工作台");
      await page.goto("/build/changesets/" + proposal.id);
      await pickItem(page, candidate);
      const current = await read(page, "/api/skills/" + id);
      for (const key of [
        "entity",
        "package_revision",
        "digest",
        "files",
        "manifest",
        "gate_history",
        "history",
      ])
        expect(current[key]).toEqual(packageBefore[key]);
      expect(await read(page, `/api/entities/${id}/versions`)).toEqual(
        versions,
      );
      expect(await savedItem(page, proposal, candidate)).toEqual(before);
    }
  } finally {
    await owner.context.close();
  }
});

const application = "适用条件或例外暂缺，请独立核对本项。";
const denial = "请依据实际办理范围补齐可以查明的条件和例外。";

async function applyForConditions(page, proposal, item) {
  await pickItem(page, item);
  await selected(page)
    .locator("summary")
    .filter({ hasText: c.r4a1_gate_application })
    .click();
  await selected(page)
    .getByLabel("条件完整 · " + c.r4a1_gate_application, { exact: true })
    .fill(application);
  const saving = postResponse(page, "/changeset.apply:invoke");
  await button(selected(page), c.r4a1_gate_application_save).click();
  expect((await saving).status()).toBe(200);
  const saved = await savedItem(page, proposal, item);
  expect(saved.after_data.waiver_applications).toEqual({ QG3: application });
  expect(
    saved.gates.checks.find((check) => check.code === "QG3"),
  ).toMatchObject({ status: "fail", application_reason: application });
  return saved;
}

async function fillConditions(page, proposal, item) {
  await pickItem(page, item);
  await button(selected(page), a.catalog_1_1).click();
  const dialog = page.getByRole("dialog", { name: a.catalog_1_1 });
  for (const [key, label, value] of [
    ["conditions", c.r4_rule_conditions, "经办人员按本条办理时"],
    ["exceptions", c.r4_rule_exceptions, "经核对本条没有其他例外"],
    ["scope", c.r4_rule_scope, "合同付款办理人员"],
  ]) {
    if (!item.after_data[key]?.length)
      await dialog.getByLabel(label, { exact: true }).fill(value);
  }
  const saved = postResponse(page, "/changeset.decide_items:invoke");
  await button(dialog, a.r2_save).click();
  expect((await saved).status()).toBe(200);
  await expect(dialog).not.toBeVisible();
  const current = await savedItem(page, proposal, item);
  expect(current.after_data.waiver_applications).toEqual({});
  expect(
    current.gates.checks.find((check) => check.code === "QG3"),
  ).toMatchObject({
    status: "pass",
    application_reason: "",
    denial_reason: "",
  });
  expect(current.can_submit).toBe(true);
}

async function activityPanel(page, id) {
  await page.goto("/ontology/elements/" + id);
  const history = page.locator("details").filter({
    has: page.locator("summary strong").filter({ hasText: c.r2_card_activity }),
  });
  await history.locator("summary").click();
  return history;
}

async function openReview(page, id) {
  await page.goto("/govern/reviews");
  await page.locator(`[data-review-item-id="${id}"]`).click();
}

test("R4-A.2 AC4: repair a requested check before submission and after an independent denial, then publish both", async ({
  browser,
}) => {
  const owner = await login(browser, "modeler");
  const reviewer = await login(browser, "reviewer");
  const page = owner.page;
  try {
    const { proposal } = await startRegulation(page, "regulation-plain");
    const candidates = proposal.items
      .filter(
        (item) =>
          item.after_data.kind === "rule" &&
          item.after_data.valid_from &&
          item.gates.checks
            .filter((c) => c.status === "fail")
            .map((c) => c.code)
            .join() === "QG3",
      )
      .slice(0, 2);
    expect(candidates).toHaveLength(2);
    const first = await applyForConditions(page, proposal, candidates[0]);
    const second = await applyForConditions(page, proposal, candidates[1]);
    await fillConditions(page, proposal, first);
    expect((await submitReady(page)).submitted_entity_ids.sort()).toEqual(
      [first.applied_entity_id, second.applied_entity_id].sort(),
    );
    await openReview(reviewer.page, second.applied_entity_id);
    await button(reviewer.page, a.catalog_2_6).click();
    const returning = reviewer.page.getByRole("dialog", {
      name: a.catalog_2_6,
    });
    await returning
      .getByLabel("条件完整 · " + c.r4a1_legacy_denial, { exact: true })
      .fill(denial);
    await returning
      .getByLabel(c.r3_main_return_reason, { exact: true })
      .fill(denial);
    const deciding = postResponse(
      reviewer.page,
      "/" + second.applied_entity_id + ":decide",
    );
    await confirmAction(reviewer.page, a.catalog_2_6);
    expect((await deciding).status()).toBe(200);
    await page.reload();
    const returned = await savedItem(page, proposal, second);
    expect(returned.applied_status).toBe("returned");
    expect(
      returned.gates.checks.find((check) => check.code === "QG3").denial_reason,
    ).toBe(denial);
    expect(returned.can_submit).toBe(false);
    await fillConditions(page, proposal, returned);
    expect((await submitReady(page)).submitted_entity_ids).toEqual([
      second.applied_entity_id,
    ]);
    for (const item of [first, second]) {
      const id = item.applied_entity_id;
      await openReview(reviewer.page, id);
      await button(reviewer.page, a.catalog_2_5).click();
      const publishing = postResponse(reviewer.page, "/" + id + ":decide");
      await confirmAction(reviewer.page, a.catalog_2_5);
      expect((await publishing).status()).toBe(200);
      const { entity, gates } = (await read(page, "/api/v2/elements/" + id))
        .data;
      expect(entity.status).toBe("published");
      expect(gates.checks.every((check) => check.status === "pass")).toBe(true);
      const history = entity.activities;
      expect(
        history.filter(
          (event) =>
            event.action === "申请发布检查豁免" &&
            event.detail.includes(application),
        ),
      ).toHaveLength(1);
      expect(
        history.filter(
          (event) =>
            event.action === "撤回发布检查豁免申请" &&
            event.detail.includes("条件完整") &&
            event.detail.includes("已通过"),
        ),
      ).toHaveLength(1);
      expect(
        history.filter(
          (event) =>
            event.action === "拒绝发布检查豁免" &&
            event.detail.includes(denial),
        ),
      ).toHaveLength(item.id === second.id ? 1 : 0);
      const timeline = await activityPanel(page, id);
      await expect(timeline).toContainText(application);
      await expect(timeline).toContainText("检查已通过，自动撤回申请");
      if (id === second.applied_entity_id)
        await expect(timeline).toContainText(denial);
    }
  } finally {
    await Promise.all([owner.context.close(), reviewer.context.close()]);
  }
});

test("R4-A.2 AC8: a submitted rule with no application has a submission activity and no waiver activity", async ({
  browser,
}) => {
  const owner = await login(browser, "modeler");
  const { page } = owner;
  try {
    const { proposal } = await startRegulation(page, "regulation-plain");
    const confirmed = await confirmOmissions(page, proposal.id);
    const item = confirmed.items.find(
      (row) => row.after_data.kind === "rule" && row.gates.passed,
    );
    expect(item).toBeTruthy();
    await acceptItem(page, item);
    const saved = await savedItem(page, proposal, item);
    expect(saved.after_data.waiver_applications).toEqual({});
    expect((await submitReady(page)).submitted_entity_ids).toEqual([
      saved.applied_entity_id,
    ]);
    const entity = await read(page, "/api/entities/" + saved.applied_entity_id);
    expect(entity.status).toBe("review");
    expect(entity.activities.some((event) => event.action === "提交审核")).toBe(
      true,
    );
    expect(
      entity.activities.filter((event) =>
        (event.action + event.detail).includes("豁免"),
      ),
    ).toEqual([]);
    const timeline = await activityPanel(page, saved.applied_entity_id);
    await expect(timeline).toContainText("提交审核");
    await expect(timeline).not.toContainText("豁免");
  } finally {
    await owner.context.close();
  }
});
