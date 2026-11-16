import { createRequire } from "node:module";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { chromium, test, expect } from "./runtime.mjs";
import { accounts, authDirectory, evidence } from "./fixture.mjs";
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
  const png = await page.screenshot({
    path: resolve(directory, "actual.png"),
    animations: "disabled",
    caret: "hide",
    scale: "css",
    mask: [
      page.locator(
        '[data-visual-dynamic="timestamp"], [data-visual-dynamic="request-id"]',
      ),
    ],
    maskColor: "#ffffff",
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
    screenshotScale: "css",
    fonts,
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
  expect.soft(png).toMatchSnapshot(`${layout.name}--${name}.png`, {
    maxDiffPixels: 0,
    threshold: 0,
  });
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
        // Fresh UI login keeps earlier scope-switch tests from mutating this session.
        // Cookies remain in the private profile; never copy a real user's profile.
        const page = launched.context.pages()[0];
        const problems = [];
        page.on("pageerror", (error) => problems.push(error.message));
        page.on("console", (message) => {
          if (message.type() === "error") problems.push(message.text());
        });
        await page.goto("/");
        await page
          .getByLabel("账号", { exact: true })
          .fill(accounts.superadmin.username);
        await page
          .getByLabel("密码", { exact: true })
          .fill(accounts.superadmin.password);
        await page.getByRole("button", { name: /登录/ }).click();
        await expect(
          page.getByRole("navigation", { name: "工作区导航", exact: true }),
        ).toBeVisible();
        await page
          .getByRole("button", { name: "个人账号", exact: true })
          .click();
        const radio = page.getByRole("radio", {
          name: mode === "simple" ? "简洁视图" : "专业视图",
          exact: true,
        });
        await radio.check();
        await expect(radio).toBeChecked();
        await expect
          .poll(() =>
            page.evaluate(async () => {
              const response = await fetch("/api/v2/prefs");
              if (!response.ok) throw new Error("View preference read failed");
              return (await response.json()).preferences.view_mode;
            }),
          )
          .toBe(mode === "professional" ? "pro" : "simple");
        await page.keyboard.press("Escape");
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
