import { test, expect } from "./runtime.mjs";
import { leakedText, visibleLeakText } from "./r3-structure.mjs";

test("R3 leakage: Markdown inline nodes preserve whole visible words", async ({
  page,
}) => {
  // Actual DOM produced by source_**chunk**_ids and pre**published**.
  await page.setContent("<p>source_<strong>chunk</strong>_ids</p>");
  expect(leakedText(await visibleLeakText(page))).toEqual([
    { kind: "internal field", value: "source_chunk_ids" },
  ]);
  await page.setContent("<p>pre<strong>published</strong></p>");
  expect(leakedText(await visibleLeakText(page))).toEqual([]);
  await page.setContent("<p><strong>published</strong></p>");
  expect(leakedText(await visibleLeakText(page))).toEqual([
    { kind: "raw state", value: "published" },
  ]);
});

test("R3 leakage: block and explicit line breaks retain word boundaries", async ({
  page,
}) => {
  for (const html of [
    "<p>pre</p><p><strong>published</strong></p>",
    "<p>pre<br><strong>published</strong></p>",
  ]) {
    await page.setContent(html);
    expect(leakedText(await visibleLeakText(page))).toEqual([
      { kind: "raw state", value: "published" },
    ]);
  }
});

test("R3 leakage: hidden and closed content stays excluded", async ({
  page,
}) => {
  await page.setContent(`
    <details><summary>摘要</summary>source_chunk_ids</details>
    <div aria-hidden="true"><strong>published</strong></div>
    <div style="display:none">pending</div>
    <div style="visibility:hidden">returned</div>
  `);
  expect(leakedText(await visibleLeakText(page))).toEqual([]);
  await page.locator("summary").click();
  expect(leakedText(await visibleLeakText(page))).toEqual([
    { kind: "internal field", value: "source_chunk_ids" },
  ]);
});

test("R3 leakage: professional allowance is limited to technical details", async ({
  page,
}) => {
  await page.setContent(`
    <div data-technical-details><details open><summary>技术详情</summary>
      <p>source_chunk_ids</p></details></div>
    <p>pre<strong>published</strong></p>
  `);
  expect(
    leakedText(await visibleLeakText(page, { professional: true })),
  ).toEqual([]);
  expect(leakedText(await visibleLeakText(page))).toEqual([
    { kind: "internal field", value: "source_chunk_ids" },
  ]);
  await page.locator("body").evaluate((body) => {
    body.insertAdjacentHTML("beforeend", "<p><strong>published</strong></p>");
  });
  expect(
    leakedText(await visibleLeakText(page, { professional: true })),
  ).toEqual([{ kind: "raw state", value: "published" }]);
});
