import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { test, expect } from "./runtime.mjs";
import { login, read, c, a, button } from "./r3-support.mjs";
import { selectSavedViewMode } from "./select-saved-view-mode.mjs";
import { assertNoLeaks } from "./r3-structure.mjs";
import {
  assertLegacyCard,
  assertLegacyHighlight,
} from "./r4-legacy-contracts.mjs";

test("R4 AA1-AA2: preserve original rows and open 200 old-format cards with resolved source highlights", async ({
  browser,
}) => {
  const folder = process.env.E2E_EVIDENCE_DIR;
  const fixture = JSON.parse(
    readFileSync(resolve(folder, "r4-legacy.json"), "utf8"),
  );
  expect(fixture.migration_verified).toBe(true);
  expect(fixture.repeat_migration_unchanged).toBe(true);
  expect(fixture.row_hash_counterexample_rejected).toBe(true);
  expect(fixture.items).toHaveLength(200);
  expect(
    fixture.items.filter((item) => item.origin === "original_v070"),
  ).toHaveLength(fixture.browser_original_count);
  expect(fixture.browser_original_count + fixture.browser_synthetic_count).toBe(
    200,
  );
  const owner = await login(browser, "modeler"),
    page = owner.page;
  const results = [];
  let savedScreenshot = false;
  let cardCounterexample = false,
    sourceCounterexample = false;
  try {
    expect((await read(page, "/api/auth/session")).workspace.tenant_id).toBe(
      fixture.browser_tenant,
    );
    await selectSavedViewMode(page, "simple");
    for (const item of fixture.items) {
      await test.step(`${item.origin}: ${item.id}`, async () => {
        const response = page.waitForResponse(
          (value) =>
            new URL(value.url()).pathname ===
            `/api/v2/elements/${encodeURIComponent(item.id)}`,
        );
        await page.goto("/ontology/elements/" + encodeURIComponent(item.id));
        const loaded = await response;
        expect(loaded.status(), item.id).toBe(200);
        const detail = (await loaded.json()).data;
        const article = page.getByRole("article", {
          name: item.title,
          exact: true,
        });
        await expect(article).toBeVisible();
        const sentence = article.locator(":scope > p").first();
        await expect(sentence).toHaveText(detail.card.one_liner);
        const box = await sentence.boundingBox();
        assertLegacyCard(item, detail, {
          title: await page.locator("[data-page-header] h1").innerText(),
          one_liner: await sentence.textContent(),
          visible: !!box && box.y >= 0 && box.y + box.height <= 768,
        });
        await assertNoLeaks(page, `AA2 ${item.origin} ${item.id}`);
        if (!cardCounterexample) {
          await sentence.evaluate((node) => {
            node.textContent = "";
          });
          const erased = await sentence.textContent();
          expect(() =>
            assertLegacyCard(item, detail, {
              title: item.title,
              one_liner: erased,
              visible: true,
            }),
          ).toThrow();
          await sentence.evaluate((node, text) => {
            node.textContent = text;
          }, detail.card.one_liner);
          cardCounterexample = true;
        }
        if (item.source) {
          const sourceResponse = page.waitForResponse(
            (value) =>
              new URL(value.url()).pathname ===
              `/api/materials/${item.source.material_id}/source`,
          );
          await button(article, c.r3_main_view_original).first().click();
          const original = await sourceResponse;
          expect(original.status()).toBe(200);
          const source = await original.json();
          const drawer = page.getByRole("dialog");
          await expect(drawer.locator("[data-source-viewer] mark")).toHaveText(
            item.source.quote,
          );
          await expect(drawer).toBeInViewport({ ratio: 0.99 });
          await expect(
            drawer.locator("[data-source-viewer] mark"),
          ).toBeInViewport({ ratio: 0.99 });
          assertLegacyHighlight(item.source, source, {
            mark: await drawer
              .locator("[data-source-viewer] mark")
              .textContent(),
            unmatched:
              (await drawer
                .getByText(c.r3_ui_quote_unmatched, { exact: true })
                .count()) > 0,
          });
          if (!savedScreenshot) {
            await page.screenshot({
              path: resolve(folder, "r4-legacy-source.png"),
              animations: "disabled",
            });
            savedScreenshot = true;
          }
          await assertNoLeaks(page, `AA2 original source ${item.id}`);
          if (!sourceCounterexample) {
            await drawer
              .locator("[data-source-viewer] mark")
              .evaluate((node) =>
                node.replaceWith(document.createTextNode(node.textContent)),
              );
            expect(
              await drawer.locator("[data-source-viewer] mark").count(),
            ).toBe(0);
            expect(() =>
              assertLegacyHighlight(item.source, source, {
                mark: "",
                unmatched: false,
              }),
            ).toThrow();
            sourceCounterexample = true;
          }
          await button(drawer, a.r3_ui_close).click();
          await expect(drawer).not.toBeVisible();
        }
        results.push({
          id: item.id,
          origin: item.origin,
          card: "passed",
          source_highlight: item.source ? "passed" : "not_applicable",
          status_preserved: detail.entity.status,
        });
      });
    }
    expect(results).toHaveLength(200);
    expect(
      results.filter((item) => item.source_highlight === "passed"),
    ).toHaveLength(fixture.sourced_synthetic_count);
  } finally {
    writeFileSync(
      resolve(folder, "r4-legacy-browser.json"),
      JSON.stringify(
        {
          channel: "browser_ui",
          model: "none_deterministic_cards",
          original_records: fixture.browser_original_count,
          synthetic_legacy_copies: fixture.browser_synthetic_count,
          expected: 200,
          completed: results.length,
          card_sentence_counterexample_rejected: cardCounterexample,
          source_highlight_counterexample_rejected: sourceCounterexample,
          status: results.length === 200 ? "passed" : "failed",
          results,
        },
        null,
        2,
      ) + "\n",
    );
    await owner.context.close();
  }
});
