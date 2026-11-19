import { writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { test, expect } from "./runtime.mjs";
import { accounts, evidence } from "./fixture.mjs";
import { c, read, terms, userRow } from "./r3-support.mjs";
import { verifyMaterialPickers } from "./r4b-material-picker.mjs";
import { selectSavedViewMode } from "./select-saved-view-mode.mjs";
import {
  assertAbsent,
  assertChunkLimit,
  assertLegacy,
  assertRankings,
  assertRebuilt,
  assertRuleEvidence,
  orderedDocuments,
  original,
  queries,
} from "./r4b-search-contracts.mjs";
import {
  capability,
  elementAction,
  fixture,
  person,
  post,
  publishOriginal,
  registerAlias,
  reopenAfterArchive,
  screenshotComparison,
  topSearch,
} from "./r4b-search-support.mjs";

async function searchMainline(page, sample, visit) {
  const results = {};
  for (const [name, q] of Object.entries(sample)) {
    const view = await topSearch(page, q);
    results[name] = view.hits;
    if (visit) await visit(name, view);
  }
  return results;
}

async function legacySearch(page, q) {
  for (const [path, pageSize] of [
    ["/api/search", 25],
    ["/api/entities", 30],
  ]) {
    const old = await read(page, path + "?q=" + encodeURIComponent(q));
    const samePage = await capability(page, "semantic.search", {
      q,
      page_size: pageSize,
    });
    assertLegacy(old, samePage);
    expect(old.items.some((item) => item.payload.chunk_id)).toBe(true);
  }
  const arguments_ = { q, page: 1, page_size: 25 };
  const tool = await post(page, "/api/integrations/tools/call", {
    name: "entities.search",
    arguments: arguments_,
  });
  expect(Object.keys(tool).sort()).toEqual(["items", "page", "total"]);
  const same = await capability(page, "semantic.search", arguments_);
  assertLegacy({ ...tool, page_size: arguments_.page_size }, same);
  const oldRows = await read(page, "/api/search?q=" + encodeURIComponent(q));
  expect(tool.items).toEqual(oldRows.items);
  await page.goto("/maintenance");
  await page
    .getByRole("combobox", { name: "审核状态筛选" })
    .selectOption("published");
  const request = page.waitForResponse((r) => {
    const url = new URL(r.url());
    return (
      url.pathname === "/api/entities" &&
      url.searchParams.get("q") === q &&
      url.searchParams.get("asset_only") === "true" &&
      url.searchParams.get("status") === "published"
    );
  });
  await page.getByRole("textbox", { name: "搜索维护内容" }).fill(q);
  const response = await request;
  expect(response.ok()).toBe(true);
  const old = await response.json();
  const canonical = await capability(page, "semantic.search", {
    q,
    status: "published",
    pillar: "asset",
    page_size: 20,
  });
  assertLegacy(old, canonical);
  expect(old.items.length).toBeGreaterThan(0);
  const links = page.locator("table tbody a.biz-table-title");
  await expect(links).toHaveCount(old.items.length);
  for (const [index, item] of old.items.entries()) {
    await expect(links.nth(index)).toHaveText(item.title);
    await expect(links.nth(index)).toHaveAttribute(
      "href",
      "/assets/" + item.id,
    );
  }
  return {
    endpoints: [
      "/api/search",
      "/api/entities",
      "/api/integrations/tools/call (entities.search)",
    ],
    page: "/maintenance",
    visible_ids: old.items.map((item) => item.id),
  };
}

async function lifecycle(builder, viewer, reviewer, rule, receipt) {
  // Separate application records exercise update/discard; extracted rules keep their actual source titles.
  const title = "纸质凭据移交登记";
  const renamed = "借阅清册归还情况";
  const draft = (
    await capability(builder, "element.create_draft", {
      kind: "data",
      title,
      summary: "记录每次移交。",
      payload: { definition: "移交份数", unit: "份", grain: "每次移交" },
    })
  ).data.entity;
  const view = await topSearch(builder, title.slice(0, 4));
  const index = view.first.items.findIndex(
    (item) => item.id === draft.id && item.status === "draft",
  );
  expect(index).toBeGreaterThanOrEqual(0);
  await expect(view.options.nth(index)).toContainText(
    terms.statuses.element.draft,
  );
  const limited = await topSearch(viewer, title.slice(0, 4));
  assertAbsent(
    limited.hits,
    new Set([draft.id]),
    "a read-only real session must not see the draft",
  );
  await expect(limited.dialog.getByText(title, { exact: true })).toHaveCount(0);
  const updated = (
    await elementAction(builder, "update_draft", draft, {
      title: renamed,
      reason: "修正登记名称",
    })
  ).entity;
  assertAbsent(
    (await topSearch(builder, title.slice(0, 4))).hits,
    new Set([draft.id]),
    "old title remained after save",
  );
  const refreshed = await topSearch(builder, renamed.slice(0, 4));
  expect(refreshed.hits.find((hit) => hit.id === draft.id)?.title).toBe(
    renamed,
  );
  const discarded = (
    await elementAction(builder, "discard_draft", updated, {
      reason: "此次临时登记终止",
    })
  ).entity;
  expect(discarded.status).toBe("archived");
  assertAbsent(
    (await topSearch(builder, renamed.slice(0, 4))).hits,
    new Set([draft.id]),
    "discarded draft remained searchable",
  );
  expect((await topSearch(builder, rule.id)).hits[0]?.id).toBe(rule.id);
  const archived = await post(
    reviewer,
    "/api/entities/" + rule.id + "/action",
    {
      action: "archive",
      expected_version: rule.version,
      reason: "独立归档演练，保留历史出处",
    },
  );
  expect(archived.status).toBe("archived");
  await reopenAfterArchive(builder, rule.id, rule.id);
  assertAbsent(
    (await topSearch(builder, rule.id)).hits,
    new Set([rule.id]),
    "archived published rule remained searchable",
  );
  receipt.lifecycle = {
    draft_id: draft.id,
    edited_version: updated.version,
    discarded_status: discarded.status,
    archived_id: rule.id,
    archived_status: archived.status,
  };
  receipt.passed.push("S7");
}

test("R4-B BA7–BA10 S1–S12 search the reviewed original regulation, all entry points and rebuilt index", async ({
  browser,
}) => {
  const receipt = {
    success: false,
    passed: [],
    tenant_id: "sub-01",
    model_channel: "synthetic_messages_real_sdk",
    source: "regulation-plain.txt",
  };
  const sessions = [];
  const record = () =>
    writeFileSync(
      resolve(evidence, "r4b-search.json"),
      JSON.stringify(receipt, null, 2) + "\n",
    );
  let stage = "provision original-fixture accounts";
  try {
    expect(fixture("accounts").tenant_id).toBe(receipt.tenant_id);
    for (const [name, role] of [
      ["b2.search.builder", "modeler"],
      ["b2.search.reviewer", "reviewer"],
      ["b2.search.viewer", "viewer"],
      ["b2.search.admin", "admin"],
      [accounts.modeler.username, "modeler"],
    ])
      sessions.push(await person(browser, name, role));
    const [builder, reviewer, viewer, admin, foreign] = sessions;
    expect(builder.user.id).not.toBe(reviewer.user.id);
    receipt.builder_id = builder.user.id;
    receipt.reviewer_id = reviewer.user.id;
    stage =
      "upload, segment, confirm and independently publish five sourced rules";
    const { rules, chunks, text, localIds } = await publishOriginal(
      builder.page,
      reviewer.page,
      receipt,
    );
    stage =
      "S5 register alias and independently republish unchanged official title";
    rules.S5 = await registerAlias(builder.page, reviewer.page, rules.S5);
    assertRuleEvidence(rules.S5, original.S5, receipt.material_id, text);
    receipt.alias = {
      id: rules.S5.id,
      official_title: rules.S5.title,
      revision: rules.S5.version,
    };
    stage = "S7 and immediate update, discard and archive";
    await lifecycle(
      builder.page,
      viewer.page,
      reviewer.page,
      rules.archive,
      receipt,
    );
    const sample = queries(rules);
    stage = "S1–S6 top search, detail navigation and original source";
    const before = await searchMainline(
      builder.page,
      sample,
      async (name, view) => {
        if (name === "S1") {
          const index = view.first.items.findIndex(
            (row) => row.id === rules.S1.id,
          );
          expect(index).toBeGreaterThanOrEqual(0);
          expect(index).toBeLessThan(5);
          const option = view.options.nth(index);
          const hit = view.first.items[index];
          expect(hit.summary).toMatch(/[\u3400-\u9fff]/u);
          expect(hit.summary).not.toBe(hit.title);
          await expect(option).toContainText(terms.kinds.rule);
          await expect(option).toContainText(terms.statuses.element.published);
          await expect(option).toContainText(hit.summary);
          await screenshotComparison(browser, builder.page);
          await option.click();
          await expect(builder.page).toHaveURL(
            new RegExp("/ontology/elements/" + rules.S1.id + "$"),
          );
          await expect(
            builder.page.getByRole("heading", {
              name: rules.S1.title,
              exact: true,
            }),
          ).toBeVisible();
        }
        if (name === "S6") {
          const index = view.first.items.findIndex(
            (row) =>
              row.doc_type === "chunk" &&
              row.entity_id === receipt.material_id &&
              chunks.some(
                (chunk) =>
                  chunk.id === row.chunk_id &&
                  chunk.content.includes(original.S6),
              ),
          );
          expect(index).toBeGreaterThanOrEqual(0);
          const hit = view.first.items[index];
          await view.options.nth(index).click();
          await expect(builder.page).toHaveURL(
            (url) =>
              url.pathname === "/materials/" + receipt.material_id &&
              url.searchParams.get("chunk") === hit.chunk_id,
          );
          const source = await read(
            builder.page,
            "/api/materials/" +
              receipt.material_id +
              "/source?chunk_id=" +
              encodeURIComponent(hit.chunk_id),
          );
          expect(source.focus.quote).toContain(original.S6);
          const highlighted = builder.page.locator("#material-anchor-focus");
          await expect(highlighted).toBeVisible();
          await expect(highlighted).toContainText(original.S6);
        }
      },
    );
    assertRankings(before, rules, receipt.material_id, chunks);
    receipt.passed.push("S1", "S2", "S3", "S4", "S5", "S6");
    receipt.queries = sample;
    receipt.before_rebuild = orderedDocuments(before);
    const general = (await topSearch(builder.page, "付款")).hits;
    assertChunkLimit(general);
    const matchingChunks = chunks.filter((chunk) =>
      chunk.content.includes("付款"),
    );
    expect(matchingChunks.length).toBeGreaterThan(2);
    const rankedChunks = general.filter(
      (hit) =>
        hit.doc_type === "chunk" && hit.entity_id === receipt.material_id,
    );
    expect(rankedChunks).toHaveLength(2);
    receipt.chunks = {
      confirmed: chunks.length,
      matching_broader_query: matchingChunks.length,
      returned_for_broader_query: rankedChunks.length,
    };
    stage =
      "S8, S9 and S10 insufficient evidence, one character and tenant isolation";
    const empty = await topSearch(builder.page, "区块链结算");
    expect(empty.hits).toEqual([]);
    await expect(empty.dialog).toContainText(c.global_search_insufficient);
    receipt.passed.push("S8");
    const short = await topSearch(builder.page, "款");
    if (short.hits.length)
      expect(short.hits.every((item) => item.title.startsWith("款"))).toBe(
        true,
      );
    else await expect(short.dialog).toContainText(c.global_search_short);
    receipt.passed.push("S9");
    expect(foreign.workspace.tenant_id).not.toBe(builder.workspace.tenant_id);
    const other = await topSearch(foreign.page, sample.S1);
    assertAbsent(
      other.hits,
      localIds,
      "the other institution must not obtain a source or rule from the tested institution",
    );
    receipt.passed.push("S10");
    stage =
      "S11 old field and original HTTP adapters preserve results and order";
    receipt.legacy = await legacySearch(builder.page, sample.S2);
    receipt.passed.push("S11");
    stage =
      "S12 clear only derived index, prove absence, rebuild via production admin and repeat S1–S6";
    receipt.cleared = fixture("clear");
    expect(receipt.cleared.before).toBeGreaterThan(0);
    expect(receipt.cleared.after).toBe(0);
    for (const [name, q] of Object.entries(sample)) {
      const result = await topSearch(builder.page, q);
      expect(
        result.hits,
        name + " was still returned with the index absent",
      ).toEqual([]);
    }
    const job = await post(
      admin.page,
      "/api/v2/system/search-index:rebuild",
      {},
    );
    expect(job.job_id).toBeTruthy();
    let completed;
    await expect
      .poll(
        async () => {
          completed = await read(admin.page, "/api/jobs/" + job.job_id);
          if (["failed", "cancelled"].includes(completed.status))
            throw Error("Rebuild ended " + completed.status);
          return completed.status;
        },
        { timeout: 60_000, intervals: [100, 250, 500] },
      )
      .toBe("completed");
    receipt.rebuild_job = { id: job.job_id, status: completed.status };
    const rebuilt = await searchMainline(builder.page, sample);
    assertRankings(rebuilt, rules, receipt.material_id, chunks);
    assertRebuilt(before, rebuilt);
    receipt.after_rebuild = orderedDocuments(rebuilt);
    receipt.passed.push("S12");
    stage = "register the original material through two retained pickers";
    receipt.material_pickers = await verifyMaterialPickers(
      builder.page,
      receipt.material_id,
    );
    stage =
      "administrator views the existing read-only account in the real institution directory";
    await admin.page.goto("/admin/users");
    await selectSavedViewMode(admin.page, "professional");
    const viewerRow = userRow(admin.page, "b2.search.viewer");
    await expect(viewerRow).toBeVisible();
    await expect(
      viewerRow.getByText(terms.roles.viewer, { exact: true }),
    ).toBeVisible();
    await admin.page.screenshot({
      path: resolve(evidence, "r4b-users-1366x768.png"),
    });
    receipt.admin_users_screen = {
      view: "professional",
      account: "b2.search.viewer",
      role_label: terms.roles.viewer,
      viewport: admin.page.viewportSize(),
    };
    receipt.passed.sort((a, b) => Number(a.slice(1)) - Number(b.slice(1)));
    receipt.success = true;
  } catch (error) {
    receipt.failure_stage = stage;
    throw error;
  } finally {
    record();
    for (const session of sessions) await session.context.close();
  }
});
