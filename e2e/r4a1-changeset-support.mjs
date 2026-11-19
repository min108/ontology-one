import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect } from "./runtime.mjs";
import { button, c, postResponse, read, confirmAction } from "./r3-support.mjs";
export const selected = (page) => page.locator("[data-selected-item-id]");
export async function pickItem(page, item) {
  await page.locator(`button[data-item-id="${item.id}"]`).click();
  await expect(selected(page)).toHaveAttribute(
    "data-selected-item-id",
    item.id,
  );
}
export async function readProposal(page, id) {
  return (await read(page, "/api/v2/changesets/" + encodeURIComponent(id)))
    .data;
}
export async function startRegulation(
  page,
  name,
  { filenamePrefix = "", stopAtIntake = false } = {},
) {
  const text = readFileSync(
    resolve(
      process.env.E2E_APPLICATION_ROOT || ".",
      "docs/blueprint/rounds/r4a1-materials",
      name + ".txt",
    ),
    "utf8",
  );
  return startRegulationText(page, name, text, {
    filenamePrefix,
    stopAtIntake,
  });
}
export async function startRegulationText(
  page,
  name,
  text,
  { filenamePrefix = "", stopAtIntake = false } = {},
) {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "工作台", exact: true }),
  ).toBeVisible();
  await expect(button(page, "选择文件")).toBeVisible();
  const data = await page.evaluateHandle(
    ({ text, name, filenamePrefix }) => {
      const data = new DataTransfer();
      data.items.add(
        new File([text], `${filenamePrefix}${name}-${Date.now()}.txt`, {
          type: "text/plain",
        }),
      );
      return data;
    },
    { text, name, filenamePrefix },
  );
  await page.dispatchEvent("body", "dragenter", { dataTransfer: data });
  await page.dispatchEvent("body", "drop", { dataTransfer: data });
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  const starting = postResponse(page, "/recipe.start:invoke");
  await button(dialog, "开始整理").click();
  const startedResponse = await starting;
  expect(startedResponse.ok()).toBe(true);
  const startedRecipe = (await startedResponse.json()).data;
  await expect(button(page, "核对建设方案")).toBeVisible({ timeout: 180_000 });
  if (stopAtIntake) {
    const recipe = (await read(page, "/api/v2/recipes/" + startedRecipe.id))
      .data;
    return {
      proposal: await readProposal(page, recipe.changeset_id),
      text,
      recipe,
    };
  }
  await button(page, "核对建设方案").click();
  await expect(page).toHaveURL(/\/build\/changesets\/CS-[^/]+$/);
  await expect(page.locator("[data-document-facts]")).toBeVisible();
  const id = decodeURIComponent(new URL(page.url()).pathname.split("/").at(-1));
  const proposal = await readProposal(page, id);
  expect(proposal.items.length).toBeGreaterThan(0);
  return { proposal, text };
}
export async function openOmissionList(page, name) {
  await expect(selected(page)).toBeVisible();
  const group = page.locator(`[data-omission-group="${name}"]`);
  if (
    (await group.count()) &&
    !(await group.locator("details").evaluate((node) => node.open))
  )
    await group.locator("summary").click();
  return group;
}
export async function confirmOmissions(page, id) {
  for (const [name, selector] of [
    ["exceptions", "[data-confirm-no-exceptions]"],
    ["conditions", "[data-confirm-unconditional]"],
  ]) {
    const group = await openOmissionList(page, name);
    const control = group.locator(selector);
    if (await control.count()) {
      for (const box of await group.getByRole("checkbox").all())
        await box.check();
      const confirmed = postResponse(page, "/changeset.confirm_facts:invoke");
      await control.click();
      expect((await confirmed).ok()).toBe(true);
      await expect(control).toHaveCount(0);
    }
  }
  return readProposal(page, id);
}
export async function acceptItem(page, item) {
  await pickItem(page, item);
  const decided = postResponse(page, "/changeset.decide_items:invoke");
  const applied = postResponse(page, "/changeset.apply:invoke");
  await button(selected(page), "采纳").click();
  expect((await decided).ok()).toBe(true);
  expect((await applied).ok()).toBe(true);
  await expect(
    selected(page).getByRole("link", { name: c.r2_view_draft, exact: true }),
  ).toBeVisible();
}
export async function submitReady(page) {
  const submitting = postResponse(page, "/changeset.apply_and_submit:invoke");
  await button(page, "采纳并提交审核").click();
  await confirmAction(page, "采纳并提交审核");
  const response = await submitting;
  expect(response.ok()).toBe(true);
  return (await response.json()).data;
}
export async function setFileDate(page, date) {
  const input = page.getByLabel(c.r4a1_file_date, { exact: true });
  await input.fill(date);
  await input.press("Enter");
  const confirmed = postResponse(page, "/changeset.confirm_facts:invoke");
  await button(page, c.r4a1_file_date_apply).click();
  const response = await confirmed;
  expect(response.ok()).toBe(true);
  await expect(input).toHaveValue(date);
  return (await response.json()).data;
}
