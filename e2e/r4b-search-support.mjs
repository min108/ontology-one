import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { expect } from "./runtime.mjs";
import { accounts, evidence } from "./fixture.mjs";
import {
  a,
  button,
  c,
  confirmAction,
  loginCredentials,
  postResponse,
  read,
} from "./r3-support.mjs";
import {
  acceptItem,
  confirmOmissions,
  readProposal,
  startRegulation,
  submitReady,
} from "./r4a1-changeset-support.mjs";
import { assertUnwaivedChecks } from "./r4-assertion-contracts.mjs";
import {
  alias,
  assertRuleEvidence,
  original,
} from "./r4b-search-contracts.mjs";

export function fixture(action) {
  return JSON.parse(
    execFileSync(
      process.env.E2E_PYTHON,
      ["e2e/r4b_search_fixture.py", action],
      { encoding: "utf8" },
    ),
  );
}

export async function person(browser, name, role) {
  const context = await browser.newContext({
    baseURL: process.env.E2E_BASE_URL,
    viewport: { width: 1366, height: 768 },
    locale: "zh-CN",
    reducedMotion: "reduce",
  });
  const page = await context.newPage();
  await loginCredentials(page, name, accounts.modeler.password);
  const { user, workspace } = await read(page, "/api/auth/session");
  expect(user.role).toBe(role);
  expect(workspace.tenant_id).toBe(
    name.startsWith("b2.search.") ? "sub-01" : "dept-01",
  );
  return { context, page, user, workspace };
}

export async function post(page, path, body) {
  const actual = await page.evaluate(
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
      return { status: response.status, data: await response.json() };
    },
    { path, body },
  );
  expect(
    actual.status,
    path + " " + JSON.stringify(actual.data.error ?? actual.data.detail ?? ""),
  ).toBe(200);
  return actual.data;
}
export async function capability(page, id, body) {
  return post(page, "/api/v2/capabilities/" + id + ":invoke", body);
}
export async function elementAction(page, name, entity, extras) {
  return (
    await capability(page, "element." + name, {
      entity_id: entity.id,
      version: entity.version,
      ...extras,
    })
  ).data;
}

export async function publish(reviewer, builder, id) {
  await reviewer.goto("/govern/reviews");
  await reviewer.locator('[data-review-item-id="' + id + '"]').click();
  await button(reviewer, a.catalog_2_5).click();
  await expect(
    reviewer
      .getByRole("dialog", { name: a.catalog_2_5 })
      .locator('[data-governance-field="valid_from"]'),
  ).toContainText("2026-01-01");
  const deciding = postResponse(reviewer, "/" + id + ":decide");
  await confirmAction(reviewer, a.catalog_2_5);
  expect((await deciding).ok()).toBe(true);
  const detail = (await read(builder, "/api/v2/elements/" + id)).data;
  expect(detail.entity.status).toBe("published");
  assertUnwaivedChecks(detail.gates);
  return detail.entity;
}

export async function publishOriginal(builder, reviewer, receipt) {
  const { proposal, text } = await startRegulation(builder, "regulation-plain");
  receipt.source_sha256 = createHash("sha256").update(text).digest("hex");
  expect(receipt.source_sha256).toBe(original.sha256);
  expect(proposal.extraction_summary.fully_local).toBe(false);
  expect(proposal.extraction_summary.accepted_candidates).toBeGreaterThan(0);
  receipt.material_id = proposal.source_material_id;
  receipt.extraction = proposal.extraction_summary;
  const material = "/api/materials/" + receipt.material_id;
  const source = await read(builder, material + "/source");
  expect(source.text).toBe(text);
  expect(source.sha256).toBe(original.sha256);
  const chunks = (await read(builder, material + "/chunks")).items;
  expect(chunks.length).toBeGreaterThan(0);
  const confirmed = await confirmOmissions(builder, proposal.id);
  expect(confirmed.document_facts.valid_from.value).toBe("2026-01-01");
  const names = ["S1", "S2", "S5", "S6", "archive"];
  const chosen = {};
  for (const key of names) {
    const candidate = confirmed.items.find(
      (item) =>
        item.content_origin === "source" &&
        item.after_data.kind === "rule" &&
        item.evidence.some(
          ({ quote }) => text.includes(quote) && quote.includes(original[key]),
        ),
    );
    expect(candidate, "an extracted rule citing " + original[key]).toBeTruthy();
    chosen[key] = candidate.id;
    await acceptItem(builder, candidate);
  }
  expect(new Set(Object.values(chosen)).size).toBe(names.length);
  const submitted = await submitReady(builder);
  const saved = await readProposal(builder, proposal.id);
  const rules = {};
  receipt.citations = {};
  for (const key of names) {
    const item = saved.items.find((row) => row.id === chosen[key]);
    expect(item.applied_status, "submitted source clause " + key).toBe(
      "review",
    );
    expect(submitted.submitted_entity_ids).toContain(item.applied_entity_id);
    const entity = await publish(reviewer, builder, item.applied_entity_id);
    expect(entity.title).toBe(item.after_data.title);
    const ref = assertRuleEvidence(
      entity,
      original[key],
      receipt.material_id,
      text,
    );
    receipt.citations[key] = {
      entity_id: entity.id,
      chunk_id: ref.chunk_id,
      anchor_id: ref.anchor_id,
      quote: ref.quote,
    };
    rules[key] = entity;
  }
  receipt.published_rule_ids = names.map((key) => rules[key].id);
  receipt.s1_rule_id = rules.S1.id;
  return {
    rules,
    chunks,
    text,
    localIds: new Set([
      receipt.material_id,
      ...chunks.map((chunk) => chunk.id),
      ...saved.items.map((item) => item.applied_entity_id).filter(Boolean),
    ]),
  };
}

export async function registerAlias(builder, reviewer, rule) {
  await builder.goto("/ontology/elements/" + rule.id);
  await expect(
    builder.getByRole("heading", { name: rule.title, exact: true }),
  ).toBeVisible();
  await button(builder, c.r4b_alias_register).click();
  const dialog = builder.getByRole("dialog", { name: c.r4b_alias_register });
  await dialog.getByRole("textbox", { name: c.r4b_alias_new }).fill(alias);
  await builder.screenshot({
    animations: "disabled",
    path: resolve(evidence, "r4b-alias-entry-1366x768.png"),
  });
  const revising = postResponse(builder, "/element.start_revision:invoke");
  await button(dialog, a.catalog_2_8).click();
  const revision = (await (await revising).json()).data.entity;
  expect(revision).toMatchObject({
    id: rule.id,
    status: "draft",
    title: rule.title,
  });
  expect(revision.payload.aliases ?? []).not.toContain(alias);
  const saving = postResponse(builder, "/element.update_draft:invoke");
  await button(dialog, a.catalog_2_0).click();
  const saved = await saving;
  expect(saved.request().postDataJSON()).toEqual({
    entity_id: rule.id,
    version: revision.version,
    reason: c.r4b_alias_register,
    add_alias: alias,
  });
  const change = (await saved.json()).data.entity;
  expect(change).toMatchObject({
    id: rule.id,
    status: "draft",
    title: rule.title,
  });
  expect(change.payload.aliases).toContain(alias);
  expect(change.payload.source_refs).toEqual(rule.payload.source_refs);
  await expect(dialog).toHaveCount(0);
  const submitting = postResponse(builder, "/element.submit_draft:invoke");
  await button(
    builder.locator("#element-repair"),
    c.r4a1_legacy_submit,
  ).click();
  const submitted = (await (await submitting).json()).data;
  expect(submitted.entity.status).toBe("review");
  const updated = await publish(reviewer, builder, rule.id);
  expect(updated.version).toBeGreaterThan(rule.version);
  expect(updated.title).toBe(rule.title);
  expect(updated.payload.aliases).toContain(alias);
  await builder.goto("/ontology/elements/" + rule.id);
  await expect(
    builder.getByText(c.r4b_aliases + " · " + alias, { exact: true }),
  ).toBeVisible();
  await builder.screenshot({
    animations: "disabled",
    path: resolve(evidence, "r4b-alias-published-1366x768.png"),
  });
  return updated;
}

export async function topSearch(page, q) {
  // A new document also clears the UI query cache when observing an index mutation.
  await page.goto("/");
  await button(page, c.r3_ui_search).click();
  const dialog = page.getByRole("dialog", { name: a.search });
  const response = page.waitForResponse(
    (r) =>
      new URL(r.url()).pathname ===
        "/api/v2/capabilities/semantic.search:invoke" &&
      r.request().method() === "POST" &&
      r.request().postDataJSON()?.q === q,
  );
  await dialog.getByRole("combobox").fill(q);
  const result = await response;
  expect(result.ok()).toBe(true);
  const first = await result.json();
  const options = dialog.getByRole("option");
  await expect(options).toHaveCount(first.items.length);
  await expect(dialog.getByRole("alert")).toHaveCount(0);
  for (const [index, hit] of first.items.entries())
    await expect(options.nth(index).locator("strong")).toHaveText(hit.title);
  const hits = [...first.items];
  for (let p = first.page + 1; hits.length < first.total; p++) {
    const next = await capability(page, "semantic.search", {
      q,
      page: p,
      page_size: first.page_size,
    });
    expect(next.items.length).toBeGreaterThan(0);
    hits.push(...next.items);
  }
  expect(hits.length).toBe(first.total);
  return { first, hits, dialog, options };
}

export async function verifyMatchedWords(page, aliasRuleId) {
  const prefix = await topSearch(page, "质量保证");
  const aliasIndex = prefix.first.items.findIndex(
    (row) => row.id === aliasRuleId,
  );
  expect(aliasIndex).toBeGreaterThanOrEqual(0);
  expect(prefix.first.items[aliasIndex].match_terms).toContain("质量保证");
  const aliasRow = prefix.options.nth(aliasIndex);
  await expect(aliasRow).toContainText(alias);
  expect(await aliasRow.locator("mark").allTextContents()).toContain(
    "质量保证",
  );

  const question = await topSearch(page, "合同如何付款？");
  const words = ["合同", "付款"];
  const index = question.first.items.findIndex((hit) =>
    words.every((word) => hit.match_terms?.includes(word)),
  );
  expect(index).toBeGreaterThanOrEqual(0);
  const marks = await question.options
    .nth(index)
    .locator("mark")
    .allTextContents();
  for (const word of words) expect(marks).toContain(word);
  return { question: "合同如何付款？", words, alias_prefix: "质量保证" };
}

export async function reopenAfterArchive(page, q, cachedId) {
  // Keep this tab and its live QueryClient. Only delay forwarding the second request.
  await page.getByRole("combobox").press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  const url = page.url();
  let release, started;
  const held = new Promise((resolve) => {
    release = resolve;
  });
  const intercepted = new Promise((resolve) => {
    started = resolve;
  });
  const target = (url) =>
    url.pathname === "/api/v2/capabilities/semantic.search:invoke";
  await page.route(
    target,
    async (route) => {
      started();
      await held;
      await route.continue();
    },
    { times: 1 },
  );
  try {
    await button(page, c.r3_ui_search).click();
    const input = page.getByRole("combobox");
    await input.fill(q);
    await intercepted;
    await expect(page.getByRole("option")).toHaveCount(0);
    await input.press("Enter");
    await expect(input).toBeVisible();
    expect(page.url()).toBe(url);
    await page.screenshot({
      animations: "disabled",
      path: resolve(evidence, "r4b-search-refresh-1366x768.png"),
    });
    const response = page.waitForResponse(
      (r) => target(new URL(r.url())) && r.request().postDataJSON()?.q === q,
    );
    release();
    const actual = await response;
    expect(actual.ok()).toBe(true);
    const result = await actual.json();
    expect(result.items.some((row) => row.id === cachedId)).toBe(false);
    await expect(page.getByRole("dialog").getByRole("status")).toHaveText(
      c.global_search_insufficient,
    );
  } finally {
    release();
    await page.unroute(target);
  }
}

export async function screenshotComparison(browser, page) {
  const actual = await page.screenshot({
    path: resolve(evidence, "r4b-search-1366x768.png"),
  });
  const reference = await browser.newContext({
    viewport: { width: 1366, height: 768 },
    deviceScaleFactor: 1,
    reducedMotion: "reduce",
  });
  try {
    // The separate reference context renders the checked-in design and fonts.
    // The product context above uses only the real running application.
    const files = new Set([
      "docs/design/benchmarks/01-workbench.html",
      "docs/design/tokens-v2.css",
      "docs/design/benchmarks/assets/base.css",
      "docs/design/benchmarks/assets/base.js",
      "frontend/public/fonts/cn-sans-400.woff2",
      "frontend/public/fonts/cn-sans-700.woff2",
    ]);
    await reference.route("**/*", (route) => {
      const file = new URL(route.request().url()).pathname.slice(1);
      return files.has(file)
        ? route.fulfill({ path: resolve(file) })
        : route.abort();
    });
    const baseline = await reference.newPage();
    await baseline.goto(
      "http://127.0.0.1/docs/design/benchmarks/01-workbench.html",
    );
    await baseline.evaluate(() => document.fonts.ready);
    const design = await baseline.screenshot({
      path: resolve(evidence, "r4b-search-baseline-1366x768.png"),
    });
    const comparison = await reference.newPage();
    await comparison.setViewportSize({ width: 2732, height: 800 });
    await comparison.setContent(
      "<style>body{margin:0;font:16px sans-serif;display:flex}figure{margin:0}figcaption{height:32px;line-height:32px;padding-left:12px}img{display:block;width:1366px;height:768px}</style>" +
        '<figure><figcaption>01 工作台设计基准 · 1366×768</figcaption><img src="data:image/png;base64,' +
        design.toString("base64") +
        '"></figure>' +
        '<figure><figcaption>顶部检索 · 1366×768</figcaption><img src="data:image/png;base64,' +
        actual.toString("base64") +
        '"></figure>',
    );
    await comparison.screenshot({
      path: resolve(evidence, "r4b-search-comparison.png"),
    });
  } finally {
    await reference.close();
  }
}
