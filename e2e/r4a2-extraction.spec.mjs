import { createHash } from "node:crypto";
import { writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { test, expect } from "./runtime.mjs";
import { button, c, login, postResponse, read } from "./r3-support.mjs";
import {
  acceptItem,
  confirmOmissions,
  readProposal,
  startRegulation,
  submitReady,
} from "./r4a1-changeset-support.mjs";

test("R4-A.2 AC7 single invalid model quote exposes its original paragraph and can reach review", async ({
  browser,
}) => {
  const builder = await login(browser, "modeler");
  const { page } = builder;
  try {
    const { proposal, text } = await startRegulation(page, "regulation-plain", {
      filenamePrefix: "R4A2-invalid-one-",
    });
    const article = text
      .split(/\r?\n/)
      .find((line) => line.startsWith("第三条 "));
    const ref = proposal.unformed_sources.find(
      (source) => source.quote.trim() === article,
    );
    expect(ref).toBeTruthy();
    expect(ref.material_id).toBe(proposal.source_material_id);
    expect(text.slice(ref.start, ref.end)).toBe(ref.quote);
    expect(proposal.extraction_summary.discarded_candidates).toBe(1);
    expect(proposal.extraction_summary.retried_batches).toBe(0);
    expect(proposal.extraction_summary.fully_local).toBe(false);
    expect(
      proposal.items.some((row) =>
        row.evidence.some((evidence) => evidence.quote.trim() === article),
      ),
    ).toBe(false);
    const section = page.locator("[data-unformed-sources]");
    await expect(
      section.getByText(c.r4a2_unformed_sources, { exact: true }),
    ).toBeVisible();
    const row = section.locator('[data-source-anchor="' + ref.anchor_id + '"]');
    await expect(row.locator("blockquote")).toHaveText(article);
    const material = await read(
      page,
      "/api/materials/" + proposal.source_material_id,
    );
    expect(material.coverage[ref.chunk_id].status).toBe("needs_review");
    await page.screenshot({
      path: resolve(
        process.env.E2E_EVIDENCE_DIR,
        "r4a2-changeset-unformed.png",
      ),
    });
    const retaining = postResponse(page, "/changeset.retain_source:invoke");
    await button(row, c.r4a2_keep_source).click();
    expect((await retaining).ok()).toBe(true);
    await expect(row).toHaveCount(0);
    await page.reload();
    await expect(page.locator("[data-document-facts]")).toBeVisible();
    const fresh = await readProposal(page, proposal.id);
    expect(fresh.items).toHaveLength(proposal.items.length + 1);
    const kept = fresh.items.find((item) =>
      item.evidence.some((e) => e.anchor_id === ref.anchor_id),
    );
    expect(kept).toMatchObject({
      decision: "pending",
      tier: "doubt",
      content_origin: "local",
      ready_for_bulk_accept: false,
      after_data: { content: ref.quote },
    });
    expect(kept.evidence).toEqual([ref]);
    await confirmOmissions(page, proposal.id);
    await acceptItem(page, kept);
    const applied = (await readProposal(page, proposal.id)).items.find(
      (item) => item.id === kept.id,
    );
    expect(applied.applied_status).toBe("draft");
    const result = await submitReady(page);
    expect(result.submitted_entity_ids).toContain(applied.applied_entity_id);
    const detail = (
      await read(page, "/api/v2/elements/" + applied.applied_entity_id)
    ).data;
    expect(detail.entity.status).toBe("review");
    expect(detail.entity.payload.content).toBe(ref.quote);
    expect(detail.entity.payload.source_refs[0]).toMatchObject({
      anchor_id: ref.anchor_id,
      quote: ref.quote,
      source_version: ref.source_version,
      material_id: ref.material_id,
      start: ref.start,
      end: ref.end,
    });
    writeFileSync(
      resolve(process.env.E2E_EVIDENCE_DIR, "r4a2-extraction.json"),
      JSON.stringify(
        {
          action: "AC7",
          channel: "browser_ui",
          provider: "synthetic_messages_real_sdk",
          source_sha256: createHash("sha256").update(text).digest("hex"),
          changeset_id: proposal.id,
          discarded: 1,
          retained_item_id: kept.id,
          submitted_entity_id: applied.applied_entity_id,
          persisted_status: detail.entity.status,
        },
        null,
        2,
      ) + "\n",
    );
  } finally {
    await builder.context.close();
  }
});
