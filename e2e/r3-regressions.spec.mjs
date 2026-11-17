import { resolve } from "node:path";
import { test, expect } from "./runtime.mjs";
import { fixture } from "./fixture.mjs";
import {
  c,
  login,
  read,
  button,
  confirmAction,
  unique,
} from "./r3-support.mjs";
import {
  assertDatasetCardsLayout,
  assertNoZeroHomeMetrics,
  assertRestrictionLabels,
  homeMetricSnapshot,
  restrictionSnapshot,
} from "./r3-assertion-contracts.mjs";
import { selectSavedViewMode } from "./select-saved-view-mode.mjs";

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
      if (!response.ok)
        throw new Error(`Fixture write HTTP ${response.status}`);
      return response.json();
    },
    { path, body },
  );
}

async function pendingEntity(page, title, source) {
  const quote = source.text.match(/附件信息[^。]+。/)[0];
  const chunks = await read(
    page,
    "/api/materials/" + fixture.objects.material + "/chunks",
  );
  const chunk = chunks.items.find((item) => item.content.includes(quote));
  const anchored = await read(
    page,
    "/api/materials/" +
      fixture.objects.material +
      "/source?" +
      new URLSearchParams({ chunk_id: chunk.id, quote }),
  );
  expect(anchored.focus.quote).toBe(quote);
  const entity = await write(page, "/api/entities", {
    kind: "knowledge",
    title,
    summary: "确认框重排核验",
    status: "draft",
    payload: {
      content: quote,
      definition: "确认框锁定原项的独立核验知识",
      conditions: ["需要独立核验时"],
      boundary: "仅用于确认框重排验收",
      source_refs: [anchored.focus],
    },
  });
  return write(page, "/api/entities/" + entity.id + "/action", {
    action: "submit",
    reason: "提交独立核验",
    expected_version: entity.version,
  });
}

for (const [decision, action, status] of [
  ["return", "退回", "returned"],
  ["approve_publish", "通过并发布", "published"],
])
  test(`R3.1 review confirmation ${decision}: real focus/refetch cannot replace the locked object`, async ({
    browser,
  }) => {
    const builder = await login(browser, "modeler"),
      reviewer = await login(browser, "reviewer");
    try {
      const source = await read(
        builder.page,
        "/api/materials/" + fixture.objects.material + "/source",
      );
      const original = await pendingEntity(
        builder.page,
        unique("锁定原项"),
        source,
      );
      await reviewer.page.goto("/govern/reviews");
      // Do not click a list item: this reproduces the implicit-first-item defect.
      await expect(reviewer.page.locator("[data-review-id]")).toHaveAttribute(
        "data-review-id",
        original.id,
      );
      await button(reviewer.page, action).click();
      const modal = reviewer.page.getByRole("dialog", {
        name: action,
        exact: true,
      });
      await expect(modal).toBeVisible();
      if (decision === "return")
        await modal
          .getByLabel("退回理由", { exact: true })
          .fill("锁定原项需要补充依据。");
      const newer = await pendingEntity(
        builder.page,
        unique("刷新后新项"),
        source,
      );
      const beforeNewer = await read(builder.page, "/api/entities/" + newer.id);
      const refreshing = reviewer.page.waitForResponse(
        (response) =>
          new URL(response.url()).pathname === "/api/v2/reviews" &&
          response.request().method() === "GET",
      );
      await reviewer.page.evaluate(() => {
        window.dispatchEvent(new Event("blur"));
        window.dispatchEvent(new Event("focus"));
        window.dispatchEvent(new Event("visibilitychange"));
      });
      const refreshed = (await (await refreshing).json()).data.items;
      expect(refreshed[0].entity.id).toBe(newer.id);
      await expect(
        reviewer.page.locator('[aria-label="选择一项待审内容"] button').first(),
      ).toContainText(newer.title);
      await expect(
        modal.locator("[data-testid=review-confirmation-object]"),
      ).toHaveAttribute("data-review-id", original.id);
      await reviewer.page.screenshot({
        path: resolve(
          process.env.E2E_EVIDENCE_DIR,
          `r31-review-confirmation-${decision}-1366.png`,
        ),
        animations: "disabled",
      });
      const saved = reviewer.page.waitForResponse(
        (response) =>
          /\/api\/v2\/reviews\/[^/]+:decide$/.test(
            new URL(response.url()).pathname,
          ) && response.request().method() === "POST",
      );
      await confirmAction(reviewer.page, action);
      expect((await saved).ok()).toBe(true);
      const afterOriginal = await read(
        builder.page,
        "/api/entities/" + original.id,
      );
      const afterNewer = await read(builder.page, "/api/entities/" + newer.id);
      expect({
        originalStatus: afterOriginal.status,
        originalVersion: afterOriginal.version,
        newerStatus: afterNewer.status,
        newerVersion: afterNewer.version,
      }).toEqual({
        originalStatus: status,
        originalVersion: original.version + (decision === "return" ? 1 : 2),
        newerStatus: beforeNewer.status,
        newerVersion: beforeNewer.version,
      });
      expect(afterNewer).toEqual(beforeNewer);
    } finally {
      await Promise.allSettled([
        builder.context.close(),
        reviewer.context.close(),
      ]);
    }
  });

async function checkRestrictionAndCounterexample(page, label) {
  const tag = page.locator("[data-topbar] .ant-tag").filter({ hasText: label });
  await expect(tag).toBeVisible();
  assertRestrictionLabels(await page.evaluate(restrictionSnapshot), [label]);
  await tag.evaluate((node) => {
    node.style.visibility = "hidden";
  });
  const hidden = await page.evaluate(restrictionSnapshot);
  expect(() => assertRestrictionLabels(hidden, [label])).toThrow(/U3/);
  await tag.evaluate((node) => {
    node.style.visibility = "";
  });
}

test("R3.1 A1 U3: real metric nodes and always-visible restrictions with DOM counterexamples", async ({
  browser,
}) => {
  const reviewer = await login(browser, "reviewer"),
    admin = await login(browser, "superadmin");
  try {
    await checkRestrictionAndCounterexample(reviewer.page, c.r3_ui_reviewonly);
    await admin.page.getByRole("button", { name: /^机构与身份：/ }).click();
    await admin.page
      .getByRole("combobox", { name: "机构", exact: true })
      .click();
    await admin.page
      .getByRole("option", { name: "办公厅", exact: true })
      .click();
    await expect(admin.page.locator("[data-home-workbench]")).toHaveAttribute(
      "aria-busy",
      "false",
    );
    await checkRestrictionAndCounterexample(admin.page, c.r3_ui_cross_org);
    await selectSavedViewMode(admin.page, "simple");
    await expect(admin.page.locator("[data-home-count]").first()).toBeVisible();
    const snapshot = await admin.page.evaluate(homeMetricSnapshot);
    expect(snapshot.metrics.length).toBeGreaterThan(0);
    assertNoZeroHomeMetrics(snapshot);
    // Clone an actual product metric, so an unmatched selector cannot pass this probe.
    await admin.page
      .locator("[data-home-count]")
      .first()
      .evaluate((node) => {
        const zero = node.cloneNode(true);
        zero.setAttribute("data-home-count", "0");
        zero.setAttribute("data-e2e-zero-counterexample", "");
        zero.textContent = "0";
        node.after(zero);
      });
    const withZero = await admin.page.evaluate(homeMetricSnapshot);
    expect(() => assertNoZeroHomeMetrics(withZero)).toThrow(/A1/);
    await admin.page
      .locator("[data-e2e-zero-counterexample]")
      .evaluate((node) => node.remove());

    // No persisted readonly session is currently exposed by session_payload.
    // This display-only response fixture tests U3; it makes no authorization claim.
    await reviewer.page.route("**/api/auth/session", async (route) => {
      const response = await route.fetch();
      const session = await response.json();
      await route.fulfill({
        response,
        json: {
          ...session,
          workspace: { ...session.workspace, readonly: true },
        },
      });
    });
    await reviewer.page.reload();
    await checkRestrictionAndCounterexample(reviewer.page, c.r3_ui_readonly);
  } finally {
    await Promise.allSettled([reviewer.context.close(), admin.context.close()]);
  }
});

test("R3.1 Authority navigation: cancel retains edits and leaving confirms only once", async ({
  browser,
}) => {
  const admin = await login(browser, "admin");
  try {
    const page = admin.page;
    await page.goto("/authority");
    await page
      .locator("[data-legacy-page] header.page-head")
      .getByRole("button", { name: "新建权责规范", exact: true })
      .click();
    const modal = page.getByRole("dialog", {
      name: "新建权责规范",
      exact: true,
    });
    const savedTitle = unique("导航确认草稿");
    await modal.getByLabel("规范名称", { exact: true }).fill(savedTitle);
    await button(modal, "建立编辑稿").click();
    await expect(page).toHaveURL((url) =>
      /^\/authority\/[^/?#]+$/.test(url.pathname),
    );
    const title = page.getByLabel("规范名称", { exact: true });
    await title.fill("取消离开后必须保留的内容");
    await page.waitForLoadState("networkidle");
    const originalUrl = page.url();
    let confirmations = 0,
      accept = false;
    page.on("dialog", async (dialog) => {
      confirmations++;
      expect(dialog.type()).toBe("confirm");
      expect(dialog.message()).toContain("尚未保存");
      if (accept) await dialog.accept();
      else await dialog.dismiss();
    });
    const destination = page.locator('[data-primary-link][href="/"]');
    await destination.click();
    expect(confirmations).toBe(1);
    expect(page.url()).toBe(originalUrl);
    await expect(title).toHaveValue("取消离开后必须保留的内容");
    await page.screenshot({
      path: resolve(
        process.env.E2E_EVIDENCE_DIR,
        "r31-authority-draft-1366.png",
      ),
      animations: "disabled",
    });
    accept = true;
    confirmations = 0;
    await destination.click();
    await expect(page).toHaveURL((url) => url.pathname === "/");
    expect(confirmations).toBe(1);
    const id = new URL(originalUrl).pathname.split("/").at(-1);
    expect((await read(page, "/api/entities/" + id)).title).toBe(savedTitle);
  } finally {
    await admin.context.close();
  }
});

test("R3.1 dataset entry cards: fresh direct visit loads grid and flex styles", async ({
  browser,
}) => {
  const member = await login(browser, "modeler");
  try {
    const page = member.page;
    // This fresh context never visits /data-sources, whose lazy CSS used to mask the defect.
    await page.goto("/datasets");
    await expect(
      page.locator(".src-mode-options .src-mode-option"),
    ).toHaveCount(2);
    await page.waitForLoadState("networkidle");
    const snapshot = () =>
      page.locator(".src-mode-options").evaluate((container) => ({
        display: getComputedStyle(container).display,
        cards: [...container.querySelectorAll(".src-mode-option")].map(
          (card) => {
            const box = card.getBoundingClientRect();
            const bounds = [...card.querySelectorAll("strong,span")].flatMap(
              (text) => {
                const range = document.createRange();
                range.selectNodeContents(text);
                return [...range.getClientRects()];
              },
            );
            return {
              display: getComputedStyle(card).display,
              width: box.width,
              height: box.height,
              overflow:
                card.scrollWidth > card.clientWidth + 1 ||
                card.scrollHeight > card.clientHeight + 1 ||
                bounds.some(
                  (text) =>
                    text.left < box.left - 1 ||
                    text.right > box.right + 1 ||
                    text.top < box.top - 1 ||
                    text.bottom > box.bottom + 1,
                ),
            };
          },
        ),
      }));
    assertDatasetCardsLayout(await snapshot());
    await page.locator(".src-mode-options").evaluate((node) => {
      node.style.display = "block";
    });
    const missingStyle = await snapshot();
    expect(() => assertDatasetCardsLayout(missingStyle)).toThrow(/grid/);
    await page.locator(".src-mode-options").evaluate((node) => {
      node.style.display = "";
    });
    assertDatasetCardsLayout(await snapshot());
    await page.screenshot({
      path: resolve(
        process.env.E2E_EVIDENCE_DIR,
        "r31-datasets-direct-1366.png",
      ),
      animations: "disabled",
    });
  } finally {
    await member.context.close();
  }
});
