import { resolve } from "node:path";
import { test, expect } from "./runtime.mjs";
import { fixture } from "./fixture.mjs";
import {
  c,
  a,
  login,
  read,
  button,
  postResponse,
  confirmAction,
  unique,
} from "./r3-support.mjs";
import { selectSavedViewMode } from "./select-saved-view-mode.mjs";
import { assertNoLeaks, checkStructure } from "./r3-structure.mjs";
import {
  assertGateProjection,
  assertSavedRuleCases,
  assertRuleCaseResults,
  assertWaiverActivity,
  assertSubmissionBlocked,
  assertSavedSemanticGroup,
} from "./r4-assertion-contracts.mjs";

async function write(page, path, body) {
  return page.evaluate(
    async ({ path, body }) => {
      const session = await (await fetch("/api/auth/session")).json();
      const response = await fetch(path, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Workspace-Id": session.workspace.tenant_id,
          "X-CSRF-Token": session.csrf_token,
        },
        body: JSON.stringify(body),
      });
      return { status: response.status, body: await response.json() };
    },
    { path, body },
  );
}
async function invoke(page, capability, body) {
  const response = await write(
    page,
    `/api/v2/capabilities/${capability}:invoke`,
    body,
  );
  expect(response.status, JSON.stringify(response.body)).toBe(200);
  return response.body.data;
}
async function projection(page, gates) {
  assertGateProjection(
    gates,
    await page
      .locator("[data-gate-checklist] [data-gate-code]")
      .evaluateAll((nodes) =>
        nodes.map((node) => ({
          code: node.dataset.gateCode,
          status: node.dataset.gateStatus,
          text: node.textContent,
        })),
      ),
  );
}

test("R4 AA3 capabilities create seven kinds, persist drafts and expose derived relations in business language", async ({
  browser,
}) => {
  const owner = await login(browser, "modeler"),
    page = owner.page;
  try {
    const create = (kind, title, payload) =>
      invoke(page, "element.create_draft", {
        kind,
        title: unique(title),
        payload,
      });
    const data = await create("data", "付款金额", {
      definition: "本次申请支付的人民币金额",
      unit: "元",
      grain: "每笔付款",
    });
    const object = await create("object_type", "付款申请", {
      schema: "object_type/1",
      definition: "待办理的付款请求",
      identity: { properties: ["amount"] },
      properties: [
        {
          key: "amount",
          name: "付款金额",
          datatype: "decimal",
          data_id: data.entity.id,
        },
      ],
    });
    const link = await create("link_type", "关联付款", {
      schema: "link_type/1",
      definition: "同一事项的前后付款关联",
      source_type: object.entity.id,
      target_type: object.entity.id,
      verb: "关联",
      cardinality: "many_to_many",
    });
    const metric = await create("metric", "申请付款口径", {
      schema: "metric/1",
      definition: "按每笔付款申请记录金额",
      object_type: object.entity.id,
      unit: "元",
      grain: "每笔付款",
      formula: { oxl: "amount" },
      test_cases: [
        { name: "普通付款", input: { amount: 20 }, expected: 20 },
        { name: "零额记录", input: { amount: 0 }, expected: 0 },
      ],
    });
    const term = await create("term", "申请款项", {
      schema: "term/1",
      definition: "申请办理的付款事项",
      refers_to: [object.entity.id],
    });
    const rule = await create("rule", "付款申请检查", {
      definition: "付款金额不得为负",
      scope: "付款请求",
      conditions: ["有申请记录"],
      exceptions: "无",
      judgement: {
        applies_to: { object_type: object.entity.id },
        condition: { oxl: "true" },
        requirement: { oxl: "amount >= 0" },
        exceptions: [],
        severity: "must",
        test_cases: [
          { name: "正额通过", input: { amount: 20 }, expected: "pass" },
          { name: "负额不通过", input: { amount: -1 }, expected: "violate" },
        ],
      },
    });
    const process = await create("process", "付款办理", {
      schema: "process/1",
      definition: "提交申请后核验并保存付款记录",
      trigger: "收到付款申请",
      end_result: "形成核验记录",
      steps: [
        {
          key: "check",
          name: "核验申请",
          actors: [{ org_id: "dept-01" }],
          basis: [{ rule_id: rule.entity.id }],
          inputs: [{ data_id: data.entity.id }],
          outputs: [{ object_type: object.entity.id }],
          next: ["save"],
          confirmed: true,
        },
        {
          key: "save",
          name: "保存记录",
          actors: [{ org_id: "dept-01" }],
          basis: [{ rule_id: rule.entity.id }],
          outputs: [{ object_type: object.entity.id }],
          next: [],
          confirmed: true,
        },
      ],
    });
    const created = [data, object, link, metric, term, rule, process];
    const persisted = [];
    for (const item of created)
      persisted.push(
        (await read(page, "/api/v2/elements/" + item.entity.id)).data.entity,
      );
    assertSavedSemanticGroup(persisted);
    for (const item of [object, link, metric, rule, process]) {
      const detail = (await read(page, "/api/v2/elements/" + item.entity.id))
        .data;
      expect(detail.card.relations.length, item.entity.kind).toBeGreaterThan(0);
    }
    await page.goto("/ontology/elements/" + process.entity.id);
    await selectSavedViewMode(page, "simple");
    await page.getByText(c.r2_card_relations, { exact: true }).click();
    const relations = page.getByRole("region", {
      name: c.r2_card_relations,
      exact: true,
    });
    await expect(relations).toContainText(rule.entity.title);
    await expect(relations).toContainText(data.entity.title);
    await expect(relations).toContainText(object.entity.title);
    await assertNoLeaks(page, "AA3 derived relations");
    await page.goto("/ontology/elements/" + rule.entity.id);
    const detail = (await read(page, "/api/v2/elements/" + rule.entity.id))
      .data;
    expect(detail.entity.payload.judgement.requirement.sentence).toMatch(
      /[\u4e00-\u9fff]/,
    );
    await expect(
      page.getByRole("article", { name: rule.entity.title }),
    ).toContainText(detail.entity.payload.judgement.requirement.sentence);
    await selectSavedViewMode(page, "professional");
    const running = postResponse(page, "/rule.test_cases:invoke");
    await button(page, c.r4_rule_run).click();
    assertRuleCaseResults((await (await running).json()).data);
    await expect(
      page.getByText(c.r4_rule_outcome_notice, { exact: true }),
    ).toBeVisible();
    await assertNoLeaks(page, "AA3 rule outcome", { professional: true });
  } finally {
    await owner.context.close();
  }
});

test("R4 AA4 rule: save two cases in professional view, run and show actual condition gate", async ({
  browser,
}) => {
  const owner = await login(browser, "modeler"),
    page = owner.page;
  try {
    const object = await invoke(page, "element.create_draft", {
      kind: "object_type",
      title: unique("付款对象"),
      payload: {
        schema: "object_type/1",
        definition: "每笔付款请求",
        properties: [
          {
            key: "amount",
            name: "付款金额",
            datatype: "decimal",
            note: "付款申请的人民币金额",
          },
          {
            key: "approved",
            name: "审批通过",
            datatype: "boolean",
            note: "审批记录是否通过",
          },
        ],
      },
    });
    const judgement = {
      applies_to: { object_type: object.entity.id },
      condition: { oxl: "amount > 0" },
      requirement: { oxl: "approved == true" },
      exceptions: [],
      severity: "must",
      test_cases: [],
    };
    const created = await invoke(page, "element.create_draft", {
      kind: "rule",
      title: unique("付款审批核验"),
      payload: {
        definition: "付款须经过审批",
        scope: "合同付款",
        conditions: ["有付款申请"],
        exceptions: "无",
        judgement,
      },
      meta: { valid_from: "2026-01-01" },
    });
    await page.goto("/ontology/elements/" + created.entity.id);
    await selectSavedViewMode(page, "professional");
    await page.locator("[data-technical-details] > details > summary").click();
    const cases = [
      {
        name: "符合付款条件",
        input: { amount: 20, approved: true },
        expected: "pass",
      },
      {
        name: "未发生付款",
        input: { amount: 0, approved: false },
        expected: "not_applicable",
      },
    ];
    const persisted = (
      await read(page, "/api/v2/elements/" + created.entity.id)
    ).data;
    await page
      .getByRole("textbox", { name: c.r4_rule_judgement, exact: true })
      .fill(
        JSON.stringify(
          { ...persisted.entity.payload.judgement, test_cases: cases },
          null,
          2,
        ),
      );
    const saving = postResponse(page, "/element.update_draft:invoke");
    await button(page, c.r4_rule_save).click();
    expect((await saving).ok()).toBe(true);
    const saved = (await read(page, "/api/v2/elements/" + created.entity.id))
      .data;
    assertSavedRuleCases(saved.entity, cases);
    const running = postResponse(page, "/rule.test_cases:invoke");
    await button(page, c.r4_rule_run).click();
    assertRuleCaseResults((await (await running).json()).data);
    await expect(page.locator("[data-rule-test-result]")).toHaveAttribute(
      "data-passed",
      "true",
    );
    await expect(page.locator("[data-rule-test-case]")).toHaveCount(2);
    await expect(page.locator("[data-gate-code=QG3]")).toHaveAttribute(
      "data-gate-status",
      "pass",
    );
    await selectSavedViewMode(page, "simple");
    await assertNoLeaks(page, "AA4 simple Chinese rule");
    await expect(page.locator("[data-page-content]")).not.toContainText(
      "amount > 0",
    );
    await projection(
      page,
      (await read(page, "/api/v2/elements/" + created.entity.id)).data.gates,
    );
    await page.screenshot({
      path: resolve(process.env.E2E_EVIDENCE_DIR, "r4-rule-element.png"),
    });
  } finally {
    await owner.context.close();
  }
});

test("R4 AA5-AA6 recipe: missing prerequisites block submit, repair applied candidate and submit", async ({
  browser,
}) => {
  const owner = await login(browser, "modeler"),
    page = owner.page;
  try {
    await selectSavedViewMode(page, "simple");
    const dropped = await page.evaluateHandle(() => {
      const transfer = new DataTransfer();
      transfer.items.add(
        new File(
          [
            "第一条 验收通过后30日内完成付款申请。\n第二条 付款前核对合同约定与验收资料。\n第三条 经办人员保存验收记录。",
          ],
          "缺字段的核对制度.txt",
          { type: "text/plain" },
        ),
      );
      return transfer;
    });
    await page.dispatchEvent("body", "dragenter", { dataTransfer: dropped });
    await page.dispatchEvent("body", "drop", { dataTransfer: dropped });
    await button(page.getByRole("dialog"), "开始整理").click();
    await expect(button(page, "核对建设方案")).toBeVisible({ timeout: 180000 });
    const getting = page.waitForResponse((response) =>
      /\/api\/v2\/changesets\/CS-/.test(new URL(response.url()).pathname),
    );
    await button(page, "核对建设方案").click();
    const proposal = (await (await getting).json()).data;
    const target = proposal.items.find(
      (item) => item.after_data.kind === "rule",
    );
    expect(target).toBeTruthy();
    for (const item of proposal.items) {
      await page.locator(`button[data-item-id="${item.id}"]`).click();
      await projection(page, item.gates);
      if (item.id !== target.id) {
        const deciding = postResponse(page, "/changeset.decide_items:invoke");
        await button(page.locator("[data-selected-item-id]"), "不要").click();
        expect((await deciding).ok()).toBe(true);
      }
    }
    await page.locator(`button[data-item-id="${target.id}"]`).click();
    const adopted = postResponse(page, "/changeset.apply:invoke");
    await button(page.locator("[data-selected-item-id]"), "采纳").click();
    expect((await adopted).ok()).toBe(true);
    await expect(button(page, a.catalog_1_6)).toBeDisabled();
    const prerequisites = (
      await read(page, "/api/v2/changesets/" + proposal.id)
    ).data;
    expect(prerequisites.submit_blockers.length).toBeGreaterThan(0);
    assertSubmissionBlocked({
      disabled: await button(page, a.catalog_1_6).isDisabled(),
      reason: await page.locator("[data-page-header]").innerText(),
    });
    await expect(page.locator("[data-page-header]")).toContainText(
      prerequisites.submit_blockers[0],
    );
    await button(page.locator("[data-selected-item-id]"), "改一改").click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await dialog.screenshot({
      path: resolve(process.env.E2E_EVIDENCE_DIR, "r4-repair-dialog.png"),
    });
    await dialog
      .getByLabel(c.r4_rule_definition, { exact: true })
      .fill("付款申请的时间要求");
    await dialog
      .getByLabel(c.r4_rule_scope, { exact: true })
      .fill("本机构合同付款");
    await dialog
      .getByLabel(c.r4_rule_conditions, { exact: true })
      .fill("验收完成");
    await dialog.getByLabel(c.r4_rule_exceptions, { exact: true }).fill("无");
    await dialog
      .getByLabel(c.r4_rule_valid_from, { exact: true })
      .fill("2026-01-01");
    await dialog
      .getByLabel(c.r4_rule_valid_from, { exact: true })
      .press("Enter");
    const repaired = postResponse(page, "/changeset.apply:invoke");
    await button(dialog, a.r2_save).click();
    expect((await repaired).ok()).toBe(true);
    await expect(dialog).not.toBeVisible();
    await expect(button(page, a.catalog_1_6)).toBeEnabled();
    const ready = (await read(page, "/api/v2/changesets/" + proposal.id)).data;
    expect(ready.items.find((item) => item.id === target.id).gates.passed).toBe(
      true,
    );
    await page.screenshot({
      path: resolve(process.env.E2E_EVIDENCE_DIR, "r4-changeset-repaired.png"),
    });
    const submitting = postResponse(page, "/changeset.apply_and_submit:invoke");
    await button(page, a.catalog_1_6).click();
    await confirmAction(page, a.catalog_1_6);
    expect((await submitting).ok()).toBe(true);
    const entityId = ready.items.find(
      (item) => item.id === target.id,
    ).applied_entity_id;
    expect(
      (await read(page, "/api/v2/elements/" + entityId)).data.entity.status,
    ).toBe("review");
  } finally {
    await owner.context.close();
  }
});

test("R4 AA7 legacy pending review: source and condition waivers publish and retain both reasons", async ({
  browser,
}) => {
  const owner = await login(browser, "reviewer"),
    page = owner.page;
  try {
    await page.goto("/govern/reviews");
    await page
      .locator('button[data-review-item-id="R4-LEGACY-WAIVABLE"]')
      .click();
    const current = (await read(page, "/api/v2/reviews")).data.items.find(
      (row) => row.entity.id === "R4-LEGACY-WAIVABLE",
    );
    await projection(page, current.gates);
    await expect(button(page, a.catalog_2_5)).toBeDisabled();
    const sourceReason = "已核对保管中的纸质原件",
      conditionReason = "本次明确按试行场景执行";
    await page
      .getByLabel("出处可定位 · " + c.r4_gate_waiver)
      .fill(sourceReason);
    await expect(button(page, a.catalog_2_5)).toBeDisabled();
    await page
      .getByLabel("条件完整 · " + c.r4_gate_waiver)
      .fill(conditionReason);
    await expect(button(page, a.catalog_2_5)).toBeEnabled();
    await page.screenshot({
      path: resolve(process.env.E2E_EVIDENCE_DIR, "r4-review-waiver.png"),
    });
    const deciding = postResponse(page, "/R4-LEGACY-WAIVABLE:decide");
    await button(page, a.catalog_2_5).click();
    await confirmAction(page, a.catalog_2_5);
    expect((await deciding).ok()).toBe(true);
    const entity = (await read(page, "/api/v2/elements/R4-LEGACY-WAIVABLE"))
      .data.entity;
    assertWaiverActivity(entity, sourceReason);
    assertWaiverActivity(entity, conditionReason);
    await page.goto("/ontology/elements/" + entity.id);
    await page.getByText(c.r2_card_activity, { exact: true }).click();
    await expect(page.locator("[data-page-content]")).toContainText(
      sourceReason,
    );
    await expect(page.locator("[data-page-content]")).toContainText(
      conditionReason,
    );
    await assertNoLeaks(page, "AA7 waiver history");
  } finally {
    await owner.context.close();
  }
});

test("R4 AA8 missing relation cannot be waived from review or direct decision", async ({
  browser,
}) => {
  const owner = await login(browser, "reviewer"),
    page = owner.page;
  try {
    await page.goto("/govern/reviews");
    await page
      .locator('button[data-review-item-id="R4-LEGACY-RELATION-MISSING"]')
      .click();
    await expect(button(page, a.catalog_2_5)).toBeDisabled();
    await expect(page.locator("[data-gate-code=QG4]")).toContainText(
      c.r4_gate_cannot_waive,
    );
    await expect(page.locator("[data-gate-code=QG4] textarea")).toHaveCount(0);
    const row = (await read(page, "/api/v2/reviews")).data.items.find(
      (row) => row.entity.id === "R4-LEGACY-RELATION-MISSING",
    );
    for (const code of ["QG2", "QG4", "QG5"]) {
      const response = await write(
        page,
        "/api/v2/reviews/" + row.entity.id + ":decide",
        {
          decision: "approve_publish",
          version: row.entity.version,
          waivers: { [code]: "不能越过此项检查" },
        },
      );
      expect(response.status).toBe(422);
    }
    expect(
      (await read(page, "/api/v2/elements/" + row.entity.id)).data.entity
        .status,
    ).toBe("review");
  } finally {
    await owner.context.close();
  }
});

test("R4 AA9 grandfathered published element retains status and exposes actual checks", async ({
  browser,
}) => {
  const owner = await login(browser, "modeler"),
    page = owner.page;
  try {
    const detail = (
      await read(page, "/api/v2/elements/" + fixture.objects.asset)
    ).data;
    expect(detail.entity.status).toBe("published");
    await page.goto("/ontology/elements/" + detail.entity.id);
    await selectSavedViewMode(page, "simple");
    await projection(page, detail.gates);
    await expect(page.locator("[data-page-header]")).toContainText("已发布");
    await checkStructure(page, "r4-grandfathered-element");
    expect(
      (await read(page, "/api/v2/elements/" + detail.entity.id)).data.entity
        .status,
    ).toBe("published");
  } finally {
    await owner.context.close();
  }
});
