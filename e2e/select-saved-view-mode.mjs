import { expect } from "./runtime.mjs";

const names = { simple: "简洁视图", pro: "专业视图" };
const account = (page) => page.getByRole("button", { name: /^个人账号：/ });
const radio = (page, mode) =>
  page.getByRole("radio", { name: names[mode], exact: true });
const menu = (page) =>
  page.getByRole("menu", { includeHidden: true }).filter({
    has: page.getByRole("radio", {
      name: names.simple,
      exact: true,
      includeHidden: true,
    }),
  });

export async function closeViewModeMenu(page) {
  // rc-dropdown handles Escape on window; await the real menu's closing motion.
  await page.keyboard.press("Escape");
  await expect(menu(page)).toBeHidden();
}

export async function openViewModeMenu(page) {
  // Never infer open state from an element still visible during closing motion.
  await closeViewModeMenu(page);
  await account(page).click();
  await expect(menu(page)).toBeVisible();
  await expect(radio(page, "simple").locator("..")).toBeVisible();
}

export async function readSavedViewMode(page) {
  return page.evaluate(async () => {
    const response = await fetch("/api/v2/prefs");
    if (!response.ok) throw new Error(`Preference GET HTTP ${response.status}`);
    return (await response.json()).preferences.view_mode ?? null;
  });
}

export async function selectSavedViewMode(page, requested) {
  const target = requested === "professional" ? "pro" : requested;
  if (!["simple", "pro"].includes(target)) throw new Error("Unknown view mode");
  await openViewModeMenu(page);
  const sequence = (await radio(page, target).isChecked())
    ? [target === "simple" ? "pro" : "simple", target]
    : [target];
  await closeViewModeMenu(page);
  for (const mode of sequence) {
    await openViewModeMenu(page);
    await expect(radio(page, mode)).not.toBeChecked();
    const label = radio(page, mode).locator("..");
    await expect(label).toHaveJSProperty("tagName", "LABEL");
    await expect(label).toBeVisible();
    // Native radio tab order includes the checked input. Segmented's keydown
    // handler selects its adjacent option; no pointer coordinate or auto-scroll
    // click can race the popup's subsequent realignment.
    const current = radio(page, mode === "simple" ? "pro" : "simple");
    await expect(current).toBeChecked();
    await expect(current).toBeEnabled();
    await expect(current).toHaveJSProperty("tabIndex", 0);
    const [response] = await Promise.all([
      page.waitForResponse(
        (response) =>
          new URL(response.url()).pathname === "/api/v2/prefs/view_mode" &&
          response.request().method() === "PUT" &&
          response.request().postDataJSON()?.value === mode,
      ),
      current.press(mode === "simple" ? "ArrowLeft" : "ArrowRight"),
    ]);
    expect(response.ok()).toBe(true);
    await expect.poll(() => readSavedViewMode(page)).toBe(mode);
    // Keyboard selection need not dismiss Dropdown. Explicit Escape owns that
    // lifecycle, then a fresh open verifies the persisted target state.
    await closeViewModeMenu(page);
    await openViewModeMenu(page);
    await expect(radio(page, mode)).toBeChecked();
    await closeViewModeMenu(page);
  }
}
