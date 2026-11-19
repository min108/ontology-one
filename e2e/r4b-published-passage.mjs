import { resolve } from "node:path";
import { expect } from "./runtime.mjs";
import { evidence } from "./fixture.mjs";
import { fixture, person, post, topSearch } from "./r4b-search-support.mjs";

export async function verifyPublishedPassage(
  browser,
  builder,
  reviewer,
  source,
) {
  expect(fixture("department_viewer").tenant_id).toBe("dept-01");
  const reader = await person(browser, "b2.passage.viewer", "viewer");
  const { page } = reader;
  try {
    const path = "/materials/" + encodeURIComponent(source.material_id);
    const passage =
      "/api/v2/elements/" + encodeURIComponent(source.material_id) + "/passage";
    const location = {
      params: {
        anchor_id: source.anchor_id,
        source_version: source.source_version,
      },
    };
    expect((await page.request.get(passage, location)).status()).toBe(404);
    for (const [actor, action] of [
      [builder, "submit"],
      [reviewer, "approve"],
      [reviewer, "publish"],
    ]) {
      await post(actor, "/api/entities/" + source.material_id + "/action", {
        action,
        reason: source.quote,
      });
    }
    expect((await page.request.get("/api" + path)).status()).toBe(403);
    const whole = await topSearch(page, source.material_id);
    const document = whole.first.items.findIndex(
      (item) => item.doc_type === "element" && item.id === source.material_id,
    );
    expect(document).toBeGreaterThanOrEqual(0);
    await whole.options.nth(document).click();
    await expect(page).toHaveURL((url) => url.pathname === path);
    await expect(
      page.getByRole("heading", {
        name: whole.first.items[document].title,
        exact: true,
      }),
    ).toBeVisible();

    const match = await topSearch(page, source.quote);
    const chunk = match.first.items.findIndex(
      (item) =>
        item.doc_type === "chunk" && item.entity_id === source.material_id,
    );
    expect(chunk).toBeGreaterThanOrEqual(0);
    await match.options.nth(chunk).click();
    await expect(page).toHaveURL(
      (url) =>
        url.pathname === path &&
        url.searchParams.get("chunk") === match.first.items[chunk].chunk_id,
    );
    await expect(page.locator("[data-source-viewer] mark")).toHaveText(
      source.quote,
    );
    await page.screenshot({
      path: resolve(evidence, "r4b-published-passage-1366x768.png"),
    });
    await page.goto(
      path +
        "?" +
        new URLSearchParams({
          anchor: source.anchor_id,
          source_version: source.source_version,
        }),
    );
    await expect(page.locator("[data-source-viewer] mark")).toHaveText(
      source.quote,
    );
  } finally {
    await reader.context.close();
  }
}
