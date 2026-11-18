import { createHash } from "node:crypto";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { test, expect } from "./runtime.mjs";
import { login, button, c } from "./r3-support.mjs";
import { startRegulation } from "./r4a1-changeset-support.mjs";

test("R4-A.1 synthetic provider rejection: actual completed fallback stays explicit and never high confidence", async ({
  browser,
}) => {
  const { page, context } = await login(browser, "modeler");
  try {
    const { proposal, recipe, text } = await startRegulation(
      page,
      "regulation-plain",
      { filenamePrefix: "R4A1-unusable-", stopAtIntake: true },
    );
    expect(recipe.status).toBe("completed");
    expect(recipe.used_model).toBe(0);
    expect(proposal.source_text.trim()).toBe(text.trim());
    expect(proposal.extraction_summary.fully_local).toBe(true);
    expect(proposal.extraction_summary.accepted_candidates).toBe(0);
    expect(proposal.extraction_summary.retried_batches).toBeGreaterThan(0);
    expect(proposal.extraction_summary.failed_batches).toBeGreaterThan(0);
    expect(proposal.items.length).toBeGreaterThan(0);
    expect(
      proposal.items.every(
        (item) =>
          item.content_origin === "local" &&
          item.tier !== "sure" &&
          !item.ready_for_bulk_accept,
      ),
    ).toBe(true);
    await expect(
      page.getByText(c.r4a1_extraction_local, { exact: true }),
    ).toBeVisible();
    await expect(page.locator("[data-extraction-summary]")).toHaveAttribute(
      "data-fully-local",
      "true",
    );
    const output = process.env.E2E_EVIDENCE_DIR;
    mkdirSync(output, { recursive: true });
    await page.screenshot({
      path: resolve(output, "r4a1-intake-synthetic-fallback.png"),
    });
    await button(page, c.r3_main_intake_view).click();
    await expect(
      page.getByText(c.r4a1_extraction_local, { exact: true }),
    ).toBeVisible();
    await expect(button(page, "全部采纳高把握")).toHaveCount(0);
    await page.screenshot({
      path: resolve(output, "r4a1-changeset-synthetic-fallback.png"),
    });
    writeFileSync(
      resolve(output, "r4a1-synthetic-fallback.json"),
      JSON.stringify(
        {
          channel: "synthetic_provider_real_sdk_browser",
          not_live_acceptance: true,
          fixture:
            "filename-triggered unusable quote; original source unchanged",
          source_sha256: createHash("sha256").update(text).digest("hex"),
          changeset_id: proposal.id,
          summary: proposal.extraction_summary,
          local_candidate_count: proposal.items.length,
          passed: true,
        },
        null,
        2,
      ),
    );
  } finally {
    await context.close();
  }
});
