import { writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect } from "./runtime.mjs";
import { evidence } from "./fixture.mjs";
import { a, button, read } from "./r3-support.mjs";
import { fixture, person, post, topSearch } from "./r4b-search-support.mjs";

export async function uploadConfirmedMaterial(page, name, text) {
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
  const { id } = await upload.json();
  const base = "/api/materials/" + encodeURIComponent(id);
  const preview = await post(page, base + "/segment-preview", {
    mode: "default",
  });
  await post(page, base + "/segment-confirm", { preview_id: preview.id });
  return { id, base };
}

async function publishMaterial(builder, reviewer, id, reason) {
  for (const [actor, action, expected] of [
    [builder, "submit", "review"],
    [reviewer, "approve", "approved"],
    [reviewer, "publish", "published"],
  ]) {
    const entity = await post(actor, "/api/entities/" + id + "/action", {
      action,
      reason,
    });
    expect(entity.status).toBe(expected);
  }
}

async function openMaterialHit(page, query, id, type) {
  const result = await topSearch(page, query);
  const index = result.first.items.findIndex(
    (hit) =>
      hit.doc_type === type &&
      (type === "element" ? hit.id : hit.entity_id) === id,
  );
  expect(index, query).toBeGreaterThanOrEqual(0);
  const hit = result.first.items[index];
  await result.options.nth(index).click();
  await expect(page).toHaveURL(
    (url) =>
      url.pathname === "/materials/" + encodeURIComponent(id) &&
      url.searchParams.get("chunk") === (hit.chunk_id || null),
  );
  return hit;
}

async function readLargeMaterial(page, builder, reviewer) {
  const marker = "当日核对绿色封签";
  const text =
    "第一条 经办人应" +
    marker +
    "。\n\n第二条 凭证需要盖章确认。\n\n" +
    "办理时应逐笔核对该凭证的财务签收记录。".repeat(5000);
  expect(Buffer.byteLength(text)).toBeGreaterThan(256 * 1024);
  const { id, base } = await uploadConfirmedMaterial(
    builder,
    "长册实际点击.txt",
    text,
  );
  const original = await read(builder, base + "/source");
  expect(original.text).toBe(text);
  await publishMaterial(builder, reviewer, id, marker);
  const detailPath = "/api/v2/elements/" + encodeURIComponent(id);
  const detailRequests = [];
  const observe = (request) => {
    if (new URL(request.url()).pathname === detailPath)
      detailRequests.push(request.method());
  };
  page.on("request", observe);
  try {
    const hit = await openMaterialHit(page, id, id, "element");
    await expect(
      page.getByRole("heading", { name: hit.title, exact: true }),
    ).toBeVisible();
    const body = page.locator("[data-source-viewer] blockquote");
    const chars = Array.from(original.text);
    const first = chars.slice(0, 4096).join("");
    const second = chars.slice(4096, 8192).join("");
    expect(first).not.toBe(second);
    await expect.poll(() => body.textContent()).toBe(first);
    await page.screenshot({
      path: resolve(evidence, "r4b-published-large-1366x768.png"),
    });
    await button(page, a.identity_next).click();
    await expect.poll(() => body.textContent()).toBe(second);
    await button(page, a.identity_previous).click();
    await expect.poll(() => body.textContent()).toBe(first);

    const passage = await openMaterialHit(page, marker, id, "chunk");
    const { focus } = await read(
      builder,
      base + "/source?" + new URLSearchParams({ chunk_id: passage.chunk_id }),
    );
    expect(focus.quote).toContain(marker);
    await expect(page.locator("[data-source-viewer] mark")).toHaveText(
      focus.quote,
    );
    expect(detailRequests).toEqual([]);
    return {
      material_id: id,
      original_sha256: original.sha256,
      utf8_bytes: Buffer.byteLength(text),
      codepoints: chars.length,
      checked_page_offsets: [0, 4096, 0],
      selected_chunk: passage.chunk_id,
      selected_quote: focus.quote,
      full_detail_requests: detailRequests.length,
    };
  } finally {
    page.off("request", observe);
  }
}

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
    expect(
      (await page.request.get("/api/v2" + path + "/source")).status(),
    ).toBe(404);
    await publishMaterial(builder, reviewer, source.material_id, source.quote);
    expect((await page.request.get("/api" + path)).status()).toBe(403);
    const whole = await openMaterialHit(
      page,
      source.material_id,
      source.material_id,
      "element",
    );
    await expect(
      page.getByRole("heading", { name: whole.title, exact: true }),
    ).toBeVisible();
    await expect(page.locator("[data-source-viewer] blockquote")).toHaveText(
      source.quote,
    );

    await openMaterialHit(page, source.quote, source.material_id, "chunk");
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
    const large = await readLargeMaterial(page, builder, reviewer);
    writeFileSync(
      resolve(evidence, "r4b-material-reader.json"),
      JSON.stringify(
        {
          role: reader.user.role,
          small: {
            material_id: source.material_id,
            whole_text: source.quote,
            anchor: source.anchor_id,
            source_version: source.source_version,
          },
          large,
        },
        null,
        2,
      ) + "\n",
    );
  } finally {
    await reader.context.close();
  }
}
