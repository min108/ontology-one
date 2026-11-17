import { createRequire } from "node:module";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { chromium, test, expect } from "./runtime.mjs";
import { accounts, authDirectory, evidence } from "./fixture.mjs";
import { openAuthenticatedVisualPage } from "./visual-auth.mjs";
import { selectSavedViewMode } from "./select-saved-view-mode.mjs";
import { compareVisualFiles } from "./r3-visual-diagnostics.mjs";
import {
  captureNativeViewport,
  nativeScreenshotMethod,
} from "./native-screenshot.mjs";
import {
  launchNativeContext,
  measureNativePage,
  calibratedWindow,
  assertNativeMetrics,
  zoomPreferences,
} from "./native-zoom.mjs";
import {
  visualLayouts,
  visualModes,
  visualPages,
  assertVisualInventory,
  seriousViolations,
  visualAuditHistory,
} from "./visual-contract.mjs";
import {
  assertCanonicalCapture,
  validateVisualPolicy,
  visualResultLabel,
} from "./visual-policy.mjs";

const require = createRequire(
  resolve(process.env.E2E_DEPENDENCY_ROOT || "frontend", "package.json"),
);
const { default: AxeBuilder } = require("@axe-core/playwright");
assertVisualInventory();
const visualPolicy = JSON.parse(
  readFileSync(new URL("./visual-policy.json", import.meta.url), "utf8"),
);
validateVisualPolicy(visualPolicy);

async function selectHistoricalAudit(page, mode) {
  const expected = visualAuditHistory;
  await page
    .getByRole("combobox", { name: "操作类型", exact: true })
    .selectOption(expected.action);
  await page
    .getByRole("combobox", { name: "所属机构", exact: true })
    .selectOption(expected.tenant_id);
  await page
    .getByRole("combobox", { name: "操作结果", exact: true })
    .selectOption(expected.outcome);
  const pending = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return (
      url.pathname === "/api/v2/audit" &&
      url.searchParams.get("action") === expected.action &&
      url.searchParams.get("tenant_id") === expected.tenant_id &&
      url.searchParams.get("outcome") === expected.outcome
    );
  });
  await page.getByRole("button", { name: "筛选记录", exact: true }).click();
  const response = await pending;
  expect(response.ok()).toBe(true);
  const result = await response.json();
  expect(result.total).toBe(1);
  expect(result.items).toHaveLength(1);
  expect(String(result.items[0].id)).toBe(expected.id);
  expect(result.items[0]).toMatchObject({
    action: expected.action,
    resource: expected.resource,
    tenant_id: expected.tenant_id,
    outcome: expected.outcome,
    actor_username: expected.actor_username,
    request_id: expected.request_id,
  });
  const row = page.locator(`tr[data-row-key="${expected.id}"]`);
  await expect(row).toHaveCount(1);
  await expect(row).toContainText(expected.actor_username);
  await row.getByRole("button").click();
  const drawer = page.getByRole("dialog");
  await expect(drawer).toBeVisible();
  if (mode === "professional") {
    await drawer.locator("summary").filter({ hasText: "技术详情" }).click();
    await expect(drawer.locator("details")).toHaveAttribute("open", "");
    await expect(drawer).toContainText(expected.action);
    await expect(drawer).toContainText(expected.tenant_id);
    await expect(drawer).toContainText(expected.resource);
    await expect(drawer.locator("pre")).toContainText('"entity_count": 20');
    await expect(drawer.locator("pre")).toContainText('"synthetic": true');
    await drawer.locator("pre").scrollIntoViewIfNeeded();
    await expect(drawer.locator("pre")).toBeInViewport({ ratio: 1 });
  } else {
    await expect(drawer.locator("[data-technical-details],pre")).toHaveCount(0);
    await expect(drawer).not.toContainText(expected.resource);
  }
}

async function openNative(layout, use) {
  const options = {
    ...use.launchOptions,
    locale: "zh-CN",
    timezoneId: "Asia/Shanghai",
    reducedMotion: "reduce",
    colorScheme: "light",
    baseURL: use.baseURL,
  };
  const calibration = await launchNativeContext(
    chromium,
    options,
    { width: layout.width, height: layout.height },
    1,
    authDirectory,
  );
  let measured;
  try {
    const page = calibration.context.pages()[0];
    await page.goto("/");
    measured = await measureNativePage(page);
  } finally {
    await calibration.close();
  }
  const window = calibratedWindow(measured, layout.width, layout.height);
  const launched = await launchNativeContext(
    chromium,
    options,
    window,
    layout.zoom,
    authDirectory,
  );
  if (visualPolicy.mode === "capture") {
    try {
      assertCanonicalCapture({
        githubActions: process.env.GITHUB_ACTIONS,
        browserVersion: launched.context.browser()?.version(),
        executablePath: options.executablePath,
      });
    } catch (error) {
      await launched.close();
      throw error;
    }
  }
  return { ...launched, calibration: measured, window };
}

async function recordPage(page, name, layout, launched, testInfo) {
  await page.waitForLoadState("networkidle");
  await page.evaluate(() => document.fonts.ready);
  const fonts = await page.evaluate(async () => {
    await document.fonts.load("16px 'Noto Sans SC'", "知元账号机构");
    return [...document.fonts]
      .filter((font) => font.family.replaceAll(/['"]/g, "") === "Noto Sans SC")
      .map((font) => ({
        family: font.family,
        status: font.status,
        weight: font.weight,
      }));
  });
  expect(
    fonts.some((font) => font.status === "loaded"),
    "Actual local Noto Sans SC font face loaded",
  ).toBe(true);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1,
    ),
    "No horizontal document overflow",
  ).toBe(true);
  const directory = resolve(evidence, "visual", layout.name, name);
  mkdirSync(directory, { recursive: true });
  const axe = await new AxeBuilder({ page }).analyze();
  const axeReport = {
    engine: axe.testEngine,
    urlPath: new URL(page.url()).pathname,
    violations: axe.violations.map(({ id, impact, description, nodes }) => ({
      id,
      impact,
      description,
      nodes: nodes.map(({ target, failureSummary }) => ({
        target,
        failureSummary,
      })),
    })),
    incomplete: axe.incomplete.map(({ id, impact }) => ({ id, impact })),
  };
  writeFileSync(
    resolve(directory, "axe.json"),
    JSON.stringify(axeReport, null, 2) + "\n",
  );
  const png = await captureNativeViewport(page, {
    path: resolve(directory, "actual.png"),
    maskKinds: name.endsWith("--audit") ? [] : ["timestamp", "request-id"],
  });
  const dimensions = {
    width: png.readUInt32BE(16),
    height: png.readUInt32BE(20),
  };
  const metrics = await measureNativePage(page);
  const record = {
    sourceCommit: process.env.E2E_SOURCE_COMMIT,
    browser: launched.context.browser()?.version(),
    resultKind: visualResultLabel(visualPolicy),
    policy: visualPolicy,
    preferences: zoomPreferences(layout.zoom),
    calibration: launched.calibration,
    window: launched.window,
    expected: layout,
    metrics,
    png: dimensions,
    screenshotScale: "physical",
    screenshotMethod: nativeScreenshotMethod,
    dynamicMasks: name.endsWith("--audit") ? [] : ["timestamp", "request-id"],
    fonts,
    ...(name.endsWith("--audit")
      ? {
          auditState: visualAuditHistory,
          auditDetailsOpen: name.startsWith("professional--"),
          auditDrawerOpen: true,
        }
      : {}),
    view: name.split("--")[0],
    authenticated: !name.startsWith("login--"),
  };
  writeFileSync(
    resolve(directory, "metrics.json"),
    JSON.stringify(record, null, 2) + "\n",
  );
  if (visualPolicy.mode === "capture") {
    const baselines = resolve(
      process.env.E2E_VISUAL_BASELINE_DIR || "e2e/visual-baselines",
    );
    mkdirSync(baselines, { recursive: true });
    writeFileSync(
      resolve(baselines, `${layout.name}--${name}.json`),
      JSON.stringify(record, null, 2) + "\n",
    );
  }
  assertNativeMetrics(metrics, layout, dimensions);
  await testInfo.attach(`${layout.name}-${name}`, {
    path: resolve(directory, "actual.png"),
    contentType: "image/png",
  });
  expect
    .soft(seriousViolations(axe), "No serious or critical axe violations")
    .toEqual([]);
  const baseline = resolve(
    process.env.E2E_VISUAL_BASELINE_DIR || "e2e/visual-baselines",
    `${layout.name}--${name}.png`,
  );
  if (visualPolicy.mode === "capture") writeFileSync(baseline, png);
  else
    writeFileSync(
      resolve(directory, "comparison.json"),
      JSON.stringify(compareVisualFiles(png, baseline), null, 2) + "\n",
    );
}

for (const layout of visualLayouts) {
  test(`${layout.name}: unauthenticated login visual and accessibility`, async ({}, testInfo) => {
    const launched = await openNative(layout, testInfo.project.use);
    try {
      const page = launched.context.pages()[0];
      await page.goto("/");
      await expect(
        page.getByRole("heading", { name: "账号登录", exact: true }),
      ).toBeVisible();
      await recordPage(
        page,
        "login--unauthenticated",
        layout,
        launched,
        testInfo,
      );
    } finally {
      await launched.close();
    }
  });
  for (const mode of visualModes) {
    test(`${layout.name}: ${mode} all replacement pages visual and accessibility`, async ({}, testInfo) => {
      test.setTimeout(180_000);
      const launched = await openNative(layout, testInfo.project.use);
      try {
        // The real fresh context cookie jar is authenticated before first navigation.
        // Login UI behavior remains covered by identity acceptance and login visuals.
        const page = launched.context.pages()[0];
        const problems = await openAuthenticatedVisualPage(
          launched.context,
          page,
          accounts.superadmin,
        );
        await expect(
          page.getByRole("navigation", { name: "工作区导航", exact: true }),
        ).toBeVisible();
        await page.waitForLoadState("networkidle");
        await selectSavedViewMode(page, mode);
        for (const item of visualPages) {
          await page.goto(item.path);
          if (item.heading)
            await expect(
              page.getByRole("heading", { name: item.heading, exact: true }),
            ).toBeVisible();
          if (item.ready) await expect(page.locator(item.ready)).toBeVisible();
          if (item.readyButton)
            await expect(
              page.getByRole("button", { name: item.readyButton, exact: true }),
            ).toBeEnabled();
          if (item.legacy)
            await expect(page.locator("[data-legacy-page]")).toBeVisible();
          if (item.name === "audit") await selectHistoricalAudit(page, mode);
          await recordPage(
            page,
            `${mode}--${item.name}`,
            layout,
            launched,
            testInfo,
          );
        }
        expect(
          problems,
          "No uncaught or console errors in visual acceptance",
        ).toEqual([]);
      } finally {
        await launched.close();
      }
    });
  }
}
