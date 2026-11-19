import { expect, test } from "./runtime.mjs";
import { a, button, c, postResponse, read } from "./r3-support.mjs";
import { capability, person, post, publish } from "./r4b-search-support.mjs";
import { selectSavedViewMode } from "./select-saved-view-mode.mjs";
import { verifyPublishedPassage } from "./r4b-published-passage.mjs";

async function uploadEvidence(page, name, text) {
  const session = await read(page, "/api/auth/session");
  const upload = await page.request.post("/api/materials/upload", {
    headers: {
      "X-Workspace-Id": session.workspace.tenant_id,
      "X-CSRF-Token": session.csrf_token,
    },
    multipart: {
      file: { name, mimeType: "text/plain", buffer: Buffer.from(text) },
    },
  });
  expect(upload.ok()).toBe(true);
  const base = "/api/materials/" + (await upload.json()).id;
  const preview = await post(page, base + "/segment-preview", {
    mode: "default",
  });
  await post(page, base + "/segment-confirm", { preview_id: preview.id });
  const [chunk] = (await read(page, base + "/chunks")).items;
  const { focus } = await read(page, base + "/source?chunk_id=" + chunk.id);
  expect(focus.quote).toBe(text);
  return focus;
}

test("R4-B unified element edits preserve an archived original throughout independent review", async ({
  browser,
}) => {
  const builder = await person(browser, "fixture.builder", "modeler");
  const reviewer = await person(browser, "fixture.reviewer", "reviewer");
  const page = builder.page;
  try {
    const first = await uploadEvidence(
      page,
      "交接登记.txt",
      "交接清册记录资料的签收情况。",
    );
    const historical = await uploadEvidence(
      page,
      "历史交接凭证.txt",
      "接件人保留原交接凭据以备查阅。",
    );
    const refs = [first, historical];
    const signoff = (
      await capability(page, "element.create_draft", {
        kind: "object_type",
        title: "档案签收凭据",
        payload: {
          schema: "object_type/1",
          properties: [
            {
              key: "received",
              name: "已签收",
              datatype: "boolean",
              note: "经办人已确认交接",
            },
          ],
        },
      })
    ).data.entity;
    const rule = (
      await capability(page, "element.create_draft", {
        kind: "rule",
        title: "交接凭据核对办法",
        meta: { valid_from: "2026-01-01" },
        payload: {
          definition: "用签收凭据核对交接清册。",
          scope: "资料交接",
          conditions: ["签收完成"],
          exceptions: "无例外",
          source_refs: refs,
          judgement: {
            applies_to: { object_type: signoff.id },
            condition: { oxl: "received == true" },
            requirement: { oxl: "true" },
            original_evidence: { source_refs: refs },
            test_cases: [
              {
                name: "凭据已签收",
                input: { received: true },
                expected: "pass",
              },
              {
                name: "凭据尚未签收",
                input: { received: false },
                expected: "not_applicable",
              },
            ],
          },
        },
      })
    ).data.entity;
    const path = "/api/entities/" + rule.id;
    const route = "/ontology/elements/" + rule.id;
    const stored = () => read(page, path);
    async function preserved(status) {
      const entity = await stored();
      expect(entity.status).toBe(status);
      expect(entity.payload.source_refs).toEqual(refs);
      expect(entity.payload.judgement.original_evidence.source_refs).toEqual(
        refs,
      );
      return entity;
    }
    async function saveForm(control) {
      const response = postResponse(page, "/element.update_draft:invoke");
      await control.click();
      const saved = await response;
      expect(saved.status(), await saved.text()).toBe(200);
      const body = saved.request().postDataJSON();
      expect(body).not.toHaveProperty("payload");
      await preserved("draft");
      return body;
    }
    await capability(page, "element.submit_draft", {
      entity_id: rule.id,
      version: rule.version,
      reason: "复核交接规定",
    });
    await publish(reviewer.page, page, rule.id);
    expect(
      (
        await post(
          reviewer.page,
          "/api/entities/" + historical.material_id + "/action",
          {
            action: "archive",
            reason: "原交接凭证停止使用",
          },
        )
      ).status,
    ).toBe("archived");
    const projected = (await read(page, "/api/v2/elements/" + rule.id)).data;
    expect(
      projected.entity.payload.source_refs.map((ref) => ref.anchor_id),
    ).toEqual([first.anchor_id]);
    await page.goto(route);
    await selectSavedViewMode(page, "professional");
    await button(page, c.r4b_alias_register).click();
    const dialog = page.getByRole("dialog", { name: c.r4b_alias_register });
    const alias = "签收凭据核对";
    await dialog.getByRole("textbox", { name: c.r4b_alias_new }).fill(alias);
    const revising = postResponse(page, "/element.start_revision:invoke");
    await button(dialog, a.catalog_2_8).click();
    expect((await revising).ok()).toBe(true);
    const added = await saveForm(button(dialog, a.catalog_2_0));
    expect(added.add_alias).toBe(alias);
    expect(added).not.toHaveProperty("payload_fields");
    await expect(dialog).toHaveCount(0);

    await page.goto(route);
    const repair = page.locator("#element-repair");
    const label = projected.gates.fields.find(
      (field) => field.key === "definition",
    ).label;
    const definition = "逐笔核对签收凭据与交接清册。";
    await repair.getByRole("textbox", { name: label }).fill(definition);
    const repaired = await saveForm(button(repair, c.r4a1_legacy_save));
    expect(repaired.payload_fields).toEqual({ definition });
    expect((await stored()).payload.definition).toBe(definition);

    await page.goto(route);
    await page.locator("[data-technical-details] > details > summary").click();
    const ruleEditor = page.locator("[data-rule-editor]");
    const field = ruleEditor.getByRole("textbox", {
      name: c.r4_rule_judgement,
    });
    const judgement = JSON.parse(await field.inputValue());
    expect(
      judgement.original_evidence.source_refs.map((ref) => ref.anchor_id),
    ).toEqual([first.anchor_id]);
    judgement.test_cases[0].name = "有签收记录";
    await field.fill(JSON.stringify(judgement));
    const edited = await saveForm(button(ruleEditor, c.r4_rule_save));
    expect(edited.payload_fields).toEqual({ judgement });
    expect((await stored()).payload.judgement.test_cases[0].name).toBe(
      "有签收记录",
    );

    await page.goto(route);
    const submit = postResponse(page, "/element.submit_draft:invoke");
    await button(page.locator("#element-repair"), c.r4a1_legacy_submit).click();
    expect((await submit).ok()).toBe(true);
    await preserved("review");
    await publish(reviewer.page, page, rule.id);
    expect((await preserved("published")).payload.aliases).toContain(alias);
    await verifyPublishedPassage(browser, page, reviewer.page, first);
  } finally {
    await builder.context.close();
    await reviewer.context.close();
  }
});
